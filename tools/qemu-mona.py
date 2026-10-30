#!/usr/bin/env python3
"""Capture reference XNU execution on the custom, incomplete mona machine.

Requires an ADT from mk-mona-adt.py. A framebuffer without a visible panic
does not establish boot success or Moto compatibility; result.json says so.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import mmap
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

import adt
import mkpayload

ROOT = Path(__file__).resolve().parents[1]
RAM_BASE = 0x80000000
STUB_PA = 0x80200000  # above QEMU's DTB reservation at the start of RAM
COPY_CHUNK = 16 << 20
LOADER_CHUNK = 512 << 20  # avoid host-specific raw-loader limits above 2 GiB
spec = importlib.util.spec_from_file_location('qemu_sweet', Path(__file__).with_name('qemu-sweet.py'))
sweet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweet)


def child(node, name):
    return next(c for c in node.children if c.name == name)


def validate(payload, cpus, ram_gib):
    """Reject wrong-board ADTs and out-of-range segment/header addresses."""
    if len(payload) < 0x2a8 or payload[:8] != b'SWEETXNU':
        raise ValueError('expected a SWEETXNU payload')
    version, count = struct.unpack_from('<II', payload, 8)
    if version != 1 or not count or 0x2a8 + count * 32 > len(payload):
        raise ValueError('invalid payload version or segment table')
    virt, entry, offset, size, end, phys, memory = struct.unpack_from('<7Q', payload, 0x10)
    if not size or offset + size > len(payload):
        raise ValueError('invalid payload ADT bounds')
    root, consumed = adt.parse(payload[offset:offset + size])
    if consumed != size or root.prop('mona-development') != struct.pack('<I', 1):
        raise ValueError('requires a mona-development ADT from mk-mona-adt.py')
    chosen = child(root, 'chosen')
    if (chosen.prop('dram-base'), chosen.prop('dram-size')) != (struct.pack('<Q', phys), struct.pack('<Q', memory)):
        raise ValueError('payload RAM and ADT RAM disagree')
    cpu_nodes = child(root, 'cpus').children
    if len(cpu_nodes) != cpus:
        raise ValueError(f'ADT CPU count {len(cpu_nodes)} differs from --cpus {cpus}')
    for index, cpu in enumerate(cpu_nodes):
        if cpu.prop('reg') != struct.pack('<I', index << 8) or cpu.prop('timebase-frequency') != struct.pack('<I', 19200000):
            raise ValueError('ADT CPU affinity/timer does not match mona')
    gic = child(child(root, 'arm-io'), 'gic')
    if gic.prop('reg') != struct.pack('<4Q', 0x16200000, 0x10000, 0x16260000, 0x100000) or gic.prop('virtual-timer-intid') != struct.pack('<I', 27):
        raise ValueError('ADT GIC/timer does not match mona')
    if any(c.name == 'sweet-ufs' for c in root.children):
        raise ValueError('UFS is not implemented by the mona machine')
    cmdline = payload[0x48:0x2a8].split(b'\0', 1)[0].decode('utf-8')
    tokens = cmdline.split()
    for key in ('sweettouch', 'sweetkeys', 'sweetusb'):
        if [t for t in tokens if t.startswith(key + '=')] != [key + '=0']:
            raise ValueError(f'payload must explicitly contain {key}=0; hardware is not implemented')
    selected = [t.split('=', 1)[1] for t in tokens if t.startswith('cpus=')]
    if len(selected) != 1 or not selected[0].isdigit() or not 1 <= int(selected[0]) <= cpus:
        raise ValueError('payload needs one cpus=N argument, within the ADT CPU count')
    ram_end = RAM_BASE + ram_gib * (1 << 30)
    if not RAM_BASE <= phys < phys + memory <= ram_end:
        raise ValueError('kernel-managed RAM is outside configured synthetic machine RAM')
    if not virt <= entry < end <= virt + memory:
        raise ValueError('kernel entry/end is outside managed RAM')
    for index in range(count):
        va, vm_size, file_offset, file_size = struct.unpack_from('<4Q', payload, 0x2a8 + 32 * index)
        if not virt <= va <= va + vm_size <= end or file_size > vm_size or file_offset + file_size > len(payload):
            raise ValueError(f'invalid payload segment {index}')
    top = ((end - virt + phys + size + sweet.PAGE - 1) & ~(sweet.PAGE - 1)) + sweet.PAGE
    if top > phys + memory:
        raise ValueError('kernel, ramdisk, ADT and boot arguments exceed managed RAM')
    if not RAM_BASE <= STUB_PA < STUB_PA + 32 <= phys or sweet.FB_BASE + sweet.FB_STRIDE * sweet.FB_H > ram_end:
        raise ValueError('stub/framebuffer layout does not fit configured RAM')
    mode = ('full-memory' if root.prop('mona-full-memory') == struct.pack('<I', 1)
            else 'highmem' if phys == 0x100000000 else 'conservative')
    return {'cmdline': cmdline, 'managed_ram_base': hex(phys),
            'managed_ram_bytes': memory, 'managed_ram_mode': mode}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data := stream.read(COPY_CHUNK):
            digest.update(data)
    return digest.hexdigest()


def write_sparse_images(payload, payload_path, out, chunk_size=LOADER_CHUNK):
    """Emit the legacy loader layout in sparse chunks without a RAM-size buffer.

    The mmap is used only for the small header/ADT. Segment data is copied
    through a bounded file buffer, keeping multi-GiB RAM disks out of RSS.
    """
    virt, entry, adt_offset, adt_size, kernel_end, phys, memory = struct.unpack_from('<7Q', payload, 0x10)
    adt_pa = kernel_end + phys - virt
    boot_args = (adt_pa + adt_size + sweet.PAGE - 1) & ~(sweet.PAGE - 1)
    top = boot_args + sweet.PAGE
    image_size = top - phys
    count = (image_size + chunk_size - 1) // chunk_size
    files = []
    try:
        for index in range(count):
            name = 'ram.bin' if count == 1 else f'ram-{index:03d}.bin'
            path = out / name
            size = min(chunk_size, image_size - index * chunk_size)
            with path.open('wb') as destination:
                destination.truncate(size)
            files.append((path, phys + index * chunk_size, size))

        def write_at(offset, data):
            while data:
                index, local = divmod(offset, chunk_size)
                amount = min(len(data), files[index][2] - local)
                with files[index][0].open('r+b') as destination:
                    destination.seek(local)
                    destination.write(data[:amount])
                data = data[amount:]
                offset += amount

        with Path(payload_path).open('rb') as source:
            for index in range(struct.unpack_from('<I', payload, 12)[0]):
                va, _, file_offset, file_size = struct.unpack_from('<4Q', payload, 0x2a8 + 32 * index)
                source.seek(file_offset)
                target = va - virt
                remaining = file_size
                while remaining:
                    data = source.read(min(COPY_CHUNK, remaining))
                    if not data:
                        raise ValueError('payload truncated during segment copy')
                    write_at(target, data)
                    target += len(data)
                    remaining -= len(data)
        write_at(adt_pa - phys, payload[adt_offset:adt_offset + adt_size])
        arguments = struct.pack('<HH4x4Q6QI4xQI', 2, 2, virt, phys, memory, top,
                                sweet.FB_BASE, 1, sweet.FB_STRIDE, sweet.FB_W,
                                sweet.FB_H, 32, 0, adt_pa - (phys - virt), adt_size)
        arguments += payload[0x48:0x2a8] + bytes(4) + struct.pack('<QQ', 0, memory)
        if len(arguments) != 736:
            raise ValueError('incorrect reference boot-argument layout')
        write_at(boot_args - phys, arguments)
        return files, boot_args, entry + phys - virt
    except BaseException:
        for path, _, _ in files:
            path.unlink(missing_ok=True)
        raise


def check_qemu(binary):
    lines = subprocess.check_output([binary, '--version'], text=True, timeout=10).splitlines()
    if not lines:
        raise ValueError('QEMU did not report its version')
    version = lines[0]
    machines = subprocess.check_output([binary, '-machine', 'help'], text=True, timeout=10)
    if 'mona' not in {line.split()[0] for line in machines.splitlines() if line.split()}:
        raise ValueError('QEMU binary has no mona machine; build it with tools/build-mona-qemu.py')
    return version


def qmp_capture(path, commands, framebuffer):
    """Bound each QMP operation so a failed monitor cannot hang a capture."""
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(10)
        connection.connect(path)
        with connection.makefile('rw') as stream:
            if not stream.readline():
                raise ValueError('QMP closed before greeting')

            def call(execute, arguments=None):
                stream.write(json.dumps({'execute': execute, 'arguments': arguments or {}}) + '\n')
                stream.flush()
                while True:
                    line = stream.readline()
                    if not line:
                        raise ValueError('QMP closed before replying')
                    reply = json.loads(line)
                    if 'error' in reply:
                        raise ValueError(f'QMP {execute}: {reply["error"]}')
                    if 'return' in reply:
                        return reply['return']

            call('qmp_capabilities')
            answers = [call('human-monitor-command', {'command-line': c}) for c in commands]
            call('pmemsave', {'val': sweet.FB_BASE, 'size': sweet.FB_STRIDE * sweet.FB_H,
                             'filename': str(framebuffer)})
            return answers


def check_matching_kernel(payload, kernel):
    """Match every initialized kernel segment after applying payload fixups."""
    image = bytearray(kernel.read_bytes())
    macho = mkpayload.parse_macho(image)
    mkpayload.apply_chained_fixups(image, macho)
    segments = [struct.unpack_from('<4Q', payload, 0x2a8 + 32 * n)
                for n in range(struct.unpack_from('<I', payload, 12)[0])]
    for segment in (s for s in macho['segs'] if s['vmsize']):
        matches = [(offset, size) for va, memory, offset, size in segments
                   if (va, memory) == (segment['vmaddr'], segment['vmsize'])]
        if len(matches) != 1:
            raise ValueError('--kernel does not match payload kernel segments')
        offset, size = matches[0]
        if size != segment['filesize'] or payload[offset:offset + size] != image[segment['fileoff']:segment['fileoff'] + size]:
            raise ValueError('--kernel does not match payload initialized kernel bytes')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload', type=Path, nargs='?')
    parser.add_argument('outdir', type=Path, nargs='?')
    parser.add_argument('--qemu', default=str(ROOT / 'research/qemu-build-mona/qemu-system-aarch64'))
    parser.add_argument('--kernel', type=Path, help='matching unstripped kernel for framebuffer font decoding')
    parser.add_argument('--cpus', type=int, choices=range(1, 9), default=8)
    parser.add_argument('--ram-gib', type=int, choices=range(1, 9), default=8)
    parser.add_argument('--secs', type=float, default=40)
    parser.add_argument('--hmp', action='append', default=[])
    parser.add_argument('--fail-on-panic', action='store_true')
    parser.add_argument('--check-env', action='store_true')
    args = parser.parse_args()
    try:
        if not math.isfinite(args.secs) or args.secs <= 0:
            parser.error('--secs must be finite and positive')
        args.qemu = str(Path(shutil.which(args.qemu) or args.qemu).resolve())
        version = check_qemu(args.qemu)
        if args.check_env:
            print(version + '\nCustom mona machine is available; this does not verify a kernel boot.')
            return 0
        if not args.payload or not args.outdir:
            parser.error('payload and outdir are required')
        if args.fail_on_panic and not args.kernel:
            parser.error('--fail-on-panic requires --kernel')
        out = args.outdir.resolve()
        out.mkdir(parents=True, exist_ok=True)
        generated = [out / name for name in (
            'fb.png', 'fb.txt', 'fb.raw', 'regs.txt', 'result.json', 'ram.bin',
            'stub.bin', 'serial.txt', 'qemu.log', 'qemu.stderr', 'command.json')]
        generated.extend(out.glob('ram-[0-9][0-9][0-9].bin'))
        inputs = [args.payload, Path(args.qemu)] + ([args.kernel] if args.kernel else [])
        for source in inputs:
            for target in generated:
                if (source.resolve() == target.resolve() or
                        source.exists() and target.exists() and source.samefile(target)):
                    raise ValueError(f'input aliases a generated output file: {source}')
        # A failed rerun must not leave an earlier screenshot or loader image.
        for filename in ('fb.png', 'fb.txt', 'fb.raw', 'regs.txt', 'result.json', 'ram.bin'):
            (out / filename).unlink(missing_ok=True)
        for path in out.glob('ram-[0-9][0-9][0-9].bin'):
            path.unlink()
        with args.payload.open('rb') as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as payload:
            layout = validate(payload, args.cpus, args.ram_gib)
            font = None
            if args.kernel:
                check_matching_kernel(payload, args.kernel)
                nm = shutil.which('llvm-nm') or next((str(p) for p in (Path('/opt/homebrew/opt/llvm/bin/llvm-nm'), Path('/usr/local/opt/llvm/bin/llvm-nm')) if p.is_file()), None)
                if not nm:
                    raise ValueError('llvm-nm is required for --kernel')
                symbols = subprocess.check_output([nm, '--defined-only', str(args.kernel)], text=True, timeout=10)
                fonts = [int(line.split()[0], 16) for line in symbols.splitlines() if line.split()[-1:] == ['_iso_font']]
                if len(fonts) != 1:
                    raise ValueError('kernel must contain exactly one _iso_font symbol')
                font = sweet.payload_data(payload, fonts[0], 256 * 16)
            payload_hash = sha256_file(args.payload)
            loader_images, boot_args, entry = write_sparse_images(payload, args.payload, out)
        # The multi-GiB payload mapping is closed before QEMU starts.
    except (OSError, ValueError, StopIteration, struct.error, UnicodeError, AssertionError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    (out / 'stub.bin').write_bytes(sweet.stub(boot_args, entry))
    result = {
        'schema_version': 1, 'machine': 'mona-development', 'qemu_machine': 'mona',
        'reference_xnu': True, 'full_moto_port': False, 'boot_verified': False,
        'reference_pid1_liveness_observed': False,
        'snapshot_only': True, 'qemu_version': version, 'cpus': args.cpus,
        'qemu_sha256': sha256_file(args.qemu),
        'synthetic_ram_base': hex(RAM_BASE), 'synthetic_ram_bytes': args.ram_gib * (1 << 30),
        'payload_sha256': payload_hash, **layout,
        'loader_image_count': len(loader_images), 'loader_chunk_limit_bytes': LOADER_CHUNK,
        'payload_preparation': 'read-only metadata mmap; streamed hashing/copy; sparse loader chunks',
        'warnings': ['CPU models include an approximation for the handset big cores.',
                     'RAM layout is synthetic; real handset carve-outs remain unknown.',
                     'SWEET/iPhone platform identity and 1080x2400 RAM framebuffer are retained.',
                     'Moto UFS, display, touch, PMIC, radios and secure firmware are not implemented.',
                     'No visible panic does not establish successful boot.']}
    if args.kernel:
        result['kernel_sha256'] = sha256_file(args.kernel)
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix='mona-', dir='/tmp') as temp:
            monitor = str(Path(temp) / 'qmp.sock')
            escaped = lambda p: str(p).replace(',', ',,')
            command = [args.qemu, '-M', 'mona', '-accel', 'tcg', '-smp', str(args.cpus),
                       '-m', f'{args.ram_gib}G', '-nodefaults', '-display', 'none', '-monitor', 'none',
                       '-serial', f'file:{escaped(out / "serial.txt")}', '-nic', 'none',
                       '-qmp', f'unix:{monitor},server=on,wait=off', '-no-reboot',
                       '-d', 'guest_errors,unimp', '-D', str(out / 'qemu.log')]
            for path, address, _ in loader_images:
                command += ['-device', f'loader,file={escaped(path)},addr={address:#x},force-raw=on']
            command += ['-device', f'loader,file={escaped(out / "stub.bin")},addr={STUB_PA:#x},force-raw=on',
                        '-device', f'loader,addr={STUB_PA:#x},cpu-num=0']
            result['command'] = command
            (out / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
            with (out / 'qemu.stderr').open('w') as stderr:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
                deadline = time.monotonic() + args.secs
                while process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                if process.poll() is None:
                    _, registers, *extra = qmp_capture(monitor, ['stop', 'info registers', *args.hmp], out / 'fb.raw')
                    (out / 'regs.txt').write_text(registers + ''.join(f'\n(qemu) {c}\n{r}' for c, r in zip(args.hmp, extra)))
                    raw = (out / 'fb.raw').read_bytes()
                    if len(raw) != sweet.FB_STRIDE * sweet.FB_H:
                        raise ValueError('incomplete framebuffer capture')
                    sweet.write_png(str(out / 'fb.png'), raw)
                    result['status'] = 'not_decoded'
                    if font is not None:
                        screen, unknown = sweet.decode_screen(raw, font)
                        (out / 'fb.txt').write_text(screen)
                        result.update(sweet.screen_result(screen, unknown))
                        result['reference_pid1_liveness_observed'] = (
                            'SWEET userspace: init running as PID 1' in screen
                            and screen.count('SWEET init: still alive') >= 2)
                    (out / 'fb.raw').unlink()
                else:
                    result.update(status='qemu_exited', qemu_returncode=process.returncode)
    except KeyboardInterrupt:
        result['status'] = 'interrupted'
    except (OSError, ValueError) as error:
        result.update(status='capture_error', error=str(error))
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        for path, _, _ in loader_images:
            path.unlink(missing_ok=True)
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if result['status'] == 'interrupted':
        return 130
    if result['status'] in ('capture_error', 'qemu_exited'):
        return 1
    if args.fail_on_panic and result['status'] == 'panic':
        return 2
    if args.fail_on_panic and result['status'] in ('blank_screen', 'incomplete_decode'):
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
