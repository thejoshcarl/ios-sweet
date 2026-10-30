#!/usr/bin/env python3
"""Write a SWEETXNU v1 payload without loading a large root image into RAM.

The kernel and ADT are small in-memory inputs. The optional RAM disk is copied
in chunks of at most 16 MiB. Output is replaced atomically only after every
input has been copied and the finished file flushed successfully.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile

import mkpayload

CHUNK_BYTES = 16 * 1024 * 1024


def align(value, boundary):
    return (value + boundary - 1) & ~(boundary - 1)


def reject_input_output_alias(output, inputs):
    for path in inputs:
        if output.resolve() == path.resolve():
            raise ValueError(f'output must differ from input: {path}')
        if output.exists() and path.exists() and output.samefile(path):
            raise ValueError(f'output is a hard link to input: {path}')


def build(kernel_path, adt_path, output_path, cmdline, ramdisk_path=None):
    kernel_path = Path(kernel_path)
    adt_path = Path(adt_path)
    output_path = Path(output_path)
    ramdisk_path = Path(ramdisk_path) if ramdisk_path is not None else None
    inputs = [kernel_path, adt_path] + ([ramdisk_path] if ramdisk_path else [])
    reject_input_output_alias(output_path, inputs)

    kernel = bytearray(kernel_path.read_bytes())
    macho = mkpayload.parse_macho(kernel)
    fixes = mkpayload.apply_chained_fixups(kernel, macho)
    segments = [segment for segment in macho['segs'] if segment['vmsize']]
    if not segments or macho['entry'] is None:
        raise ValueError('kernel needs memory segments and an entry point')
    text = macho['segs'][0]
    if text['name'] != '__TEXT' or text['fileoff'] != 0:
        raise ValueError('kernel must start with a __TEXT segment at file offset 0')
    virt_base = text['vmaddr'] & ~(0x2000000 - 1)
    kernel_end = align(max(s['vmaddr'] + s['vmsize'] for s in segments),
                       mkpayload.PAGE)
    pieces = []
    for segment in segments:
        offset, size = segment['fileoff'], segment['filesize']
        if (size > segment['vmsize'] or offset + size > len(kernel)
                or segment['vmaddr'] < virt_base):
            raise ValueError(f'invalid kernel segment: {segment["name"]}')
        pieces.append((segment['vmaddr'], segment['vmsize'], size, offset))

    adt_blob = adt_path.read_bytes()
    phys_base, mem_size = mkpayload.adt_dram(adt_blob)
    ramdisk = None
    root_size = None
    if ramdisk_path is not None:
        root_size = ramdisk_path.stat().st_size
        if not root_size:
            raise ValueError('RAM disk image is empty')
        rounded_size = align(root_size, mkpayload.PAGE)
        root_va = kernel_end
        root_pa = root_va - virt_base + phys_base
        kernel_end += rounded_size
        pieces.append((root_va, rounded_size, root_size, None))
        adt_blob = mkpayload.add_ramdisk_to_adt(adt_blob, root_pa, rounded_size)
        ramdisk = (hex(root_pa), hex(rounded_size))

    # The loader puts ADT and one boot-argument page immediately after the
    # kernel/RAM disk. Check their complete footprint before copying gigabytes.
    footprint = align(kernel_end - virt_base + len(adt_blob), mkpayload.PAGE)
    footprint += mkpayload.PAGE
    if footprint > mem_size:
        raise ValueError(
            f'kernel, RAM disk, ADT and boot arguments require {footprint} bytes; '
            f'ADT manages only {mem_size} bytes (use an appropriate high-memory ADT)')
    if not virt_base <= macho['entry'] < kernel_end:
        raise ValueError('kernel entry point lies outside the managed image')

    header_size = mkpayload.SEG_TABLE + 32 * len(pieces)
    cursor = header_size
    table = []
    for vmaddr, vmsize, filesize, source_offset in pieces:
        table.append((vmaddr, vmsize, cursor, filesize))
        cursor = align(cursor + filesize, 16)
    adt_offset = cursor
    total_size = align(adt_offset + len(adt_blob), 16)
    header = bytearray(header_size)
    struct.pack_into('<8sII', header, 0, b'SWEETXNU', 1, len(pieces))
    struct.pack_into('<7Q', header, 0x10, virt_base, macho['entry'],
                     adt_offset, len(adt_blob), kernel_end, phys_base, mem_size)
    encoded_command = cmdline.encode()[:mkpayload.BOOT_LINE_LENGTH - 1]
    header[mkpayload.CMDLINE:mkpayload.CMDLINE + len(encoded_command)] = encoded_command
    for index, record in enumerate(table):
        struct.pack_into('<4Q', header, mkpayload.SEG_TABLE + 32 * index, *record)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    digest = hashlib.sha256()
    try:
        with tempfile.NamedTemporaryFile(
                mode='wb', prefix=f'.{output_path.name}.', suffix='.tmp',
                dir=output_path.parent, delete=False) as output:
            temp_path = Path(output.name)

            def write(data):
                output.write(data)
                digest.update(data)

            write(header)
            for index, (_, _, filesize, source_offset) in enumerate(pieces):
                if output.tell() != table[index][2]:
                    raise ValueError('internal segment layout mismatch')
                if source_offset is not None:
                    view = memoryview(kernel)[source_offset:source_offset + filesize]
                    for chunk_offset in range(0, filesize, CHUNK_BYTES):
                        write(view[chunk_offset:chunk_offset + CHUNK_BYTES])
                else:
                    with ramdisk_path.open('rb') as root:
                        before = os.fstat(root.fileno())
                        if before.st_size != root_size:
                            raise ValueError('RAM disk size changed before copying')
                        remaining = filesize
                        while remaining:
                            data = root.read(min(remaining, CHUNK_BYTES))
                            if not data:
                                raise ValueError('RAM disk ended during copying')
                            write(data)
                            remaining -= len(data)
                        after = os.fstat(root.fileno())
                        if (root.read(1) or after.st_size != before.st_size
                                or after.st_mtime_ns != before.st_mtime_ns):
                            raise ValueError('RAM disk changed during copying')
                write(b'\0' * (-output.tell() & 15))
            if output.tell() != adt_offset:
                raise ValueError('internal ADT layout mismatch')
            write(adt_blob)
            write(b'\0' * (-output.tell() & 15))
            if output.tell() != total_size:
                raise ValueError('internal payload size mismatch')
            output.flush()
            os.fsync(output.fileno())
        # Recheck aliases before replacing an existing destination.
        reject_input_output_alias(output_path, inputs)
        os.replace(temp_path, output_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return {
        'format': 'SWEETXNU v1', 'output': str(output_path),
        'cputype': hex(macho['cputype']), 'cpusub': macho['cpusub'],
        'fixups': fixes, 'virt_base': hex(virt_base), 'entry': hex(macho['entry']),
        'kernel_end': hex(kernel_end), 'ramdisk': ramdisk, 'size': total_size,
        'sha256': digest.hexdigest(), 'segment_count': len(pieces),
        'managed_ram_bytes': mem_size, 'occupied_ram_bytes': footprint,
        'stream_chunk_bytes': CHUNK_BYTES,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('adt', type=Path)
    parser.add_argument('out', type=Path)
    parser.add_argument('--cmdline', default='debug=0xc serial=3 cpus=1 keepsyms=1')
    parser.add_argument('--ramdisk', type=Path, help='root image; copied in bounded chunks')
    args = parser.parse_args()
    try:
        result = build(args.kernel, args.adt, args.out, args.cmdline, args.ramdisk)
    except (OSError, ValueError, AssertionError, StopIteration, struct.error) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
