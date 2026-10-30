#!/usr/bin/env python3
"""Generate a mona-development ADT for the existing SWEET reference kernel.

This changes the board's GIC/timer/CPU contract, not the XNU driver port.
The iPhone/SWEET platform and RAM framebuffer identity remains for matching.
"""
import argparse
import json
from pathlib import Path
import struct
import sys

import adt
import mkadt

PROFILE = Path(__file__).resolve().parents[1] / 'boards/mona.json'


def replace(node, name, value):
    node.props = [p for p in node.props if p[0] != name] + [(name, 0, value)]


def child(node, name):
    return next(c for c in node.children if c.name == name)


def build(cpus=8, highmem=False, seed=None, full_memory=False, product_adt=None):
    if not 1 <= cpus <= 8:
        raise ValueError('cpus must be between 1 and 8')
    if highmem and full_memory:
        raise ValueError('highmem and full_memory are mutually exclusive')
    if product_adt is not None and not Path(product_adt).is_file():
        raise ValueError(f'product ADT does not exist: {product_adt}')
    profile = json.loads(PROFILE.read_text())
    root = mkadt.build(seed=seed, qemu=False, highmem=highmem)
    root.children = [c for c in root.children if c.name != 'sweet-ufs']
    replace(root, 'mona-development', mkadt.u32(1))
    replace(root, 'mona-reference-xnu', mkadt.u32(1))
    replace(root, 'mona-synthetic-ram', mkadt.u32(1))
    replace(root, 'compatible', b'D22AP\0iPhone10,3\0xiaomi,sweet\0moto,mona\0qcom,sm6475\0AppleARM\0')
    chosen = child(root, 'chosen')
    if full_memory:
        # The board exposes a synthetic 8-GiB bank ending at 0x280000000.
        # Keep the reference load base, and reserve its top MiB for panic logs.
        panic_base = 0x280000000 - mkadt.PANIC_LOG_SIZE
        replace(chosen, 'dram-base', mkadt.u64(mkadt.PHYS_BASE))
        replace(chosen, 'dram-size', mkadt.u64(panic_base - mkadt.PHYS_BASE))
        replace(child(root, 'pram'), 'reg', mkadt.u64(panic_base, mkadt.PANIC_LOG_SIZE))
        replace(root, 'mona-full-memory', mkadt.u32(1))
    if product_adt is not None:
        properties = mkadt.d22ap_product(str(product_adt))
        product = next((c for c in root.children if c.name == 'product'), None)
        if product is None:
            # Synthetic unique phandle; the device's identifying placeholders
            # were excluded by d22ap_product and are never imported here.
            product = mkadt.node('product', [('AAPL,phandle', mkadt.u32(0x4d4f4e41))])
            root.children.append(product)
        for name, value in properties:
            replace(product, name, value)
    for name in ('firmware-version', 'system-firmware-version'):
        replace(chosen, name, mkadt.cstr('mona-qemu-reference'))
    cpus_node = child(root, 'cpus')
    cpus_node.children = cpus_node.children[:cpus]
    frequency = profile['timer']['frequency_hz']
    for index, cpu in enumerate(cpus_node.children):
        replace(cpu, 'reg', mkadt.u32(index << 8))
        # qcom,kryo470 is required by the unmodified SWEET IOCPU personality.
        replace(cpu, 'compatible', b'qcom,kryo470\0qcom,kryo\0ARM,v8\0')
        replace(cpu, 'mona-observed-core', mkadt.cstr('Cortex-A55' if index < 4 else 'Cortex-A78'))
        for name in ('timebase-frequency', 'peripheral-frequency', 'fixed-frequency'):
            replace(cpu, name, mkadt.u32(frequency))
    gic = child(child(root, 'arm-io'), 'gic')
    hw = profile['gic']
    replace(gic, 'reg', mkadt.u64(int(hw['distributor_base'], 16) - mkadt.SOC_BASE,
                                int(hw['distributor_size'], 16),
                                int(hw['redistributor_base'], 16) - mkadt.SOC_BASE,
                                int(hw['redistributor_size'], 16)))
    replace(gic, 'virtual-timer-intid', mkadt.u32(profile['timer']['interrupt_ids']['virtual']))
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--cpus', type=int, choices=range(1, 9), default=8)
    memory = parser.add_mutually_exclusive_group()
    memory.add_argument('--highmem', action='store_true', help='use the reference 4-GiB-and-up managed bank; still synthetic')
    memory.add_argument('--full-memory', action='store_true', help='use synthetic RAM from the reference load base to the top panic-log reservation')
    parser.add_argument('--product-adt', type=Path, help='local iPhone ADT providing non-placeholder /product properties; never copied into git')
    parser.add_argument('--dump', action='store_true')
    args = parser.parse_args()
    try:
        root = build(args.cpus, args.highmem, full_memory=args.full_memory, product_adt=args.product_adt)
    except (OSError, ValueError, struct.error) as error:
        parser.error(str(error))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(adt.serialize(root))
    if args.dump:
        adt.dump(root)
    chosen = child(root, 'chosen')
    base, size = (struct.unpack('<Q', chosen.prop(p))[0] for p in ('dram-base', 'dram-size'))
    print(f'{args.out}: {args.cpus} CPU nodes; synthetic managed RAM {base:#x}+{size:#x}', file=sys.stderr)
    print('Reference SWEET/iPhone identity and RAM framebuffer retained; no Moto peripheral port.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
