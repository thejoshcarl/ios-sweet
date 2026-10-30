#!/usr/bin/env python3
"""Build the experimental mona machine against a pinned QEMU source tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PIN = json.loads((ROOT / 'qemu/source.json').read_text())
KCONFIG = '''
config MONA
    bool
    depends on TCG && AARCH64
    select ARM_GIC
    # QEMU 11.1.2 Arm CPU code references GICv5 helpers even on GICv3 boards.
    select ARM_GICV5
    select PL011
    select DEVICE_TREE
    select GENERIC_LOADER
'''
MESON = "arm_common_ss.add(when: 'CONFIG_MONA', if_true: files('mona.c'))\n"


def run(command, **kwargs):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def write_changed(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != content:
        path.write_text(content)


def overlay_section(path, body):
    begin, end = '# BEGIN IOS_SWEET_MONA\n', '# END IOS_SWEET_MONA\n'
    content = path.read_text()
    if begin in content:
        before, rest = content.split(begin, 1)
        _, after = rest.split(end, 1)
        content = before + after
    write_changed(path, content.rstrip() + '\n\n' + begin + body + end)


def check_source(source):
    """Allow our managed overlay, but refuse unrelated upstream source edits."""
    managed = {'hw/arm/Kconfig', 'hw/arm/meson.build'}
    changed = set(subprocess.check_output(
        ['git', '-C', str(source), 'diff', 'HEAD', '--name-only'], text=True).splitlines())
    unexpected = changed - managed
    if unexpected:
        raise ValueError('unexpected QEMU source edits: ' + ', '.join(sorted(unexpected)))
    for name in managed:
        content = (source / name).read_text()
        begin, end = '# BEGIN IOS_SWEET_MONA\n', '# END IOS_SWEET_MONA\n'
        if begin in content:
            before, rest = content.split(begin, 1)
            _, after = rest.split(end, 1)
            content = before + after
        upstream = subprocess.check_output(['git', '-C', str(source), 'show', 'HEAD:' + name], text=True)
        if content.rstrip() != upstream.rstrip():
            raise ValueError(f'unexpected QEMU edits outside managed section in {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'research/qemu')
    parser.add_argument('--build-dir', type=Path, default=ROOT / 'research/qemu-build-mona')
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 2, 8))
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    source, build = args.source.resolve(), args.build_dir.resolve()
    if source == build:
        parser.error('--source and --build-dir must differ')
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        run(['git', 'clone', '--depth', '1', '--branch', PIN['tag'], PIN['repository'], source])
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != PIN['commit']:
        parser.error(f'QEMU source must be {PIN["commit"]}; found {revision}')
    try:
        check_source(source)
    except ValueError as error:
        parser.error(str(error))
    machine = ROOT / 'qemu/hw/arm/mona.c'
    if not machine.is_file():
        parser.error(f'missing machine implementation: {machine}')
    write_changed(source / 'hw/arm/mona.c', machine.read_text())
    overlay_section(source / 'hw/arm/Kconfig', KCONFIG)
    overlay_section(source / 'hw/arm/meson.build', MESON)
    write_changed(source / 'configs/devices/aarch64-softmmu/mona.mak', 'CONFIG_MONA=y\n')
    if args.prepare_only:
        print(f'Prepared {source} at {revision}')
        return 0
    for dependency in ('ninja', 'pkg-config'):
        if not shutil.which(dependency):
            parser.error(f'{dependency} is required; see docs/MONA_QEMU.md')
    options = [
        '--target-list=aarch64-softmmu', '--with-devices-aarch64=mona',
        '--without-default-devices', '--enable-tcg', '--disable-hvf',
        '--disable-docs', '--disable-tools', '--disable-user', '--disable-guest-agent',
        '--disable-gtk', '--disable-sdl', '--disable-cocoa', '--disable-vnc',
        '--disable-curses', '--disable-opengl', '--disable-slirp', '--disable-rust',
        '--disable-plugins', '--disable-werror',
    ]
    build.mkdir(parents=True, exist_ok=True)
    configuration = {'source': str(source), 'revision': revision, 'options': options}
    stamp = build / 'mona-config.json'
    if not (build / 'build.ninja').exists() or not stamp.exists() or json.loads(stamp.read_text()) != configuration:
        run([source / 'configure', *options], cwd=build)
        stamp.write_text(json.dumps(configuration, indent=2) + '\n')
    run(['ninja', '-j', args.jobs, 'qemu-system-aarch64'], cwd=build)
    binary = build / 'qemu-system-aarch64'
    run([binary, '-machine', 'help'])
    evidence = {
        **configuration,
        'machine_source_sha256': hashlib.sha256(machine.read_bytes()).hexdigest(),
        'build_overlay_sha256': hashlib.sha256((KCONFIG + MESON + 'CONFIG_MONA=y\n').encode()).hexdigest(),
        'binary': str(binary),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'status': 'built; hardware and boot coverage require separate tests',
    }
    (build / 'mona-build.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(f'Mona QEMU: {binary}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
