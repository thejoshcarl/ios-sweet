#!/usr/bin/env python3
"""Compile and execute hardware diagnostics on the experimental mona machine."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command):
    print('+ ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, default=ROOT / 'research/qemu-build-mona/qemu-system-aarch64')
    parser.add_argument('--outdir', type=Path, default=ROOT / 'research/mona-test')
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and positive')
    qemu, output = args.qemu.resolve(), args.outdir.resolve()
    if not qemu.is_file():
        parser.error('custom QEMU missing; run python3 tools/build-mona-qemu.py')
    clang, linker = shutil.which('clang'), shutil.which('ld.lld')
    if not clang or not linker:
        parser.error('clang and ld.lld are required (macOS: brew install llvm lld)')
    output.mkdir(parents=True, exist_ok=True)
    sources = ROOT / 'tests/mona'
    flags = ['-target', 'aarch64-none-elf', '-O2', '-ffreestanding', '-fno-builtin',
             '-fno-stack-protector', '-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror']
    for filename in ('probe.S', 'probe.c'):
        run([clang, *flags, '-c', sources / filename, '-o', output / (filename + '.o')])
    elf = output / 'probe.elf'
    run([linker, '-T', sources / 'link.ld', output / 'probe.S.o', output / 'probe.c.o', '-o', elf])
    serial = output / 'serial.txt'
    serial.write_text('')
    command = [str(qemu), '-machine', 'mona', '-accel', 'tcg', '-smp', '8', '-m', '8G',
               '-nodefaults', '-display', 'none', '-monitor', 'none',
               '-serial', f'file:{serial}', '-kernel', str(elf), '-no-reboot']
    status, reason = 'failed', 'guest did not report success'
    start = time.monotonic()
    stderr_path = output / 'qemu-stderr.txt'
    with stderr_path.open('w') as stderr:
        print('+ ' + ' '.join(command), flush=True)
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
        try:
            while time.monotonic() - start < args.timeout:
                transcript = serial.read_text(errors='replace')
                if 'MONA_PROBE FAIL' in transcript or 'MONA_PROBE EXCEPTION' in transcript:
                    reason = 'guest diagnostic failed'
                    break
                if 'MONA_PROBE PASS scope=qemu-core-model cpus=8 timer_irq=27' in transcript:
                    status, reason = 'passed', 'all guest checks completed'
                    break
                if process.poll() is not None:
                    reason = f'QEMU exited before success ({process.returncode})'
                    break
                time.sleep(0.05)
            else:
                reason = 'guest diagnostic timed out'
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    transcript = serial.read_text(errors='replace')
    print(transcript, end='')
    print(stderr_path.read_text(), file=sys.stderr, end='')
    result = {
        'status': status, 'reason': reason,
        'scope': 'implemented QEMU CPU/RAM/GIC/timer/PSCI behavior; not physical handset validation',
        'duration_seconds': round(time.monotonic() - start, 3),
        'command': command,
        'qemu_version': subprocess.check_output([str(qemu), '--version'], text=True, timeout=10).splitlines()[0],
        'qemu_sha256': digest(qemu), 'guest_sha256': digest(elf),
        'inputs_sha256': {name: digest(sources / name) for name in ('probe.S', 'probe.c', 'link.ld')},
        'serial_sha256': digest(serial),
        'transcript': transcript,
        'ios_boot_tested': False,
        'phone_accessed': False,
    }
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f'{status}: {reason}; evidence: {output / "result.json"}')
    return 0 if status == 'passed' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
