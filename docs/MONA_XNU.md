# Reference XNU on the mona development machine

The custom `mona` machine and reference XNU can share the verified Moto GICv3
addresses, 19.2 MHz timer, virtual timer INTID 27 and CPU affinities. This
integration retains SWEET's platform/IOCPU personalities and iPhone X identity.
It is an incomplete board experiment, not a completed Moto XNU driver port.

The machine defaults to eight modeled CPUs and 8 GiB of contiguous synthetic
RAM starting at `0x80000000`. The default ADT exposes only the conservative
reference managed range `0xb0000000` + `0x4cf00000` (about 1.20 GiB), with a
separate 1 MiB panic-log reservation immediately above it. Unexposed machine
RAM remains available for later integration work. The actual handset RAM banks
and carve-outs are not established by either arrangement.

`--highmem` exposes the reference upper-bank layout instead: managed base
`0x100000000`, size `0xfcb00000`, plus its 1 MiB panic-log reservation. It still
uses the emulator's synthetic contiguous bank and is not a measured Moto map.

`--full-memory` keeps the reference load base `0xb0000000` and extends managed
RAM to `0x27ff00000` (size `0x1cff00000`, about 7.25 GiB), leaving the top
1 MiB for panic logs. It requires the machine's 8 GiB setting. This enables
experiments with large RAM-disk roots while preserving the same explicitly
synthetic map. It is mutually exclusive with `--highmem`.

The reference console remains a byte array at `0x9c000000`, 1080 x 2400 pixels.
Its iOS-facing canvas is the reference 1125 x 2436. Neither is the Moto's
1220 x 2712 panel. The ADT retains nominal reference CPU/bus/memory clock
properties for XNU compatibility; these do not measure Moto clock behavior or
instruction timing. The architectural timer frequency is changed to the
firmware-derived 19.2 MHz.

## Prepare a minimal root and payload

Build the custom emulator first with `python3 tools/build-mona-qemu.py`; see
the machine documentation for dependencies. Obtain a matching SWEET development
kernel separately. The following uses the repository's own tiny static PID 1,
served as `/sbin/launchd` by mockfs, and needs no Apple userland download.

```sh
mkdir -p research/mona-xnu
clang -target arm64-apple-ios16.0 -c tools/sweet-init.S -o research/mona-xnu/init.o
python3 tools/mkinit.py research/mona-xnu/init.o research/mona-xnu/init.img
python3 tools/mk-mona-adt.py research/mona-xnu/mona.adt --cpus 8

# Set this to the matching, unstripped reference kernel you built/downloaded.
MONA_KERNEL=/absolute/path/to/kernel.development.sweet
python3 tools/mkpayload.py "$MONA_KERNEL" research/mona-xnu/mona.adt research/mona-xnu/payload.bin \
  --ramdisk research/mona-xnu/init.img \
  --cmdline 'debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0 sweettouch=0 sweetkeys=0 sweetusb=0 sweetlog=0 wdt=-1 wdt_test=no_user_checkin'
python3 tools/qemu-mona.py research/mona-xnu/payload.bin research/mona-xnu/capture \
  --kernel "$MONA_KERNEL" --secs 150 --fail-on-panic
```

The eight CPU nodes describe the modeled machine. `cpus=1` deliberately starts
reference kernel bring-up on one CPU; additional CPUs require an independent
XNU SMP test. The ADT's `--cpus` and runner's `--cpus` must agree. A kernel
`cpus=N` boot argument may select a smaller initial prefix.

`sweettouch=0 sweetkeys=0 sweetusb=0` are mandatory in the payload: the reference
drivers otherwise probe Redmi-specific MMIO. The tree omits UFS because its
Qualcomm host is not modeled. `sweetlog=0` disables the reference persistent
UFS log path. Loading a RAM disk exercises a different storage path from a
Moto UFS controller.

## Large roots and iOS userland experiments

For a separately prepared, local iOS root image, generate the tree with
`--full-memory --product-adt /absolute/path/to/d22ap.adt`. The second option
imports non-placeholder `/product` properties from a local iPhone X device
tree for the reference userland's MobileGestalt lookups. It does not copy the
Apple source artifact into the repository. Kernel/platform identity and the
framebuffer remain the reference implementation described above.

`tools/mk-mona-payload.py` can package the kernel, this ADT and a large local
RAM disk without constructing the entire payload in Python memory. The runner
maps only the payload metadata, checks the kernel, hashes/copies files in
bounded chunks and writes sparse loader images of at most 512 MiB each. These
images are mapped at successive physical addresses, preserving the original
payload/boot-argument layout while avoiding host raw-loader limits for a
single multi-GiB file. The payload mapping is closed before QEMU starts; all
temporary loader chunks are removed after capture.

The existence of this preparation path does not establish a graphical iOS
boot on `mona`. Results require their own captured process, framebuffer and
interaction evidence. The minimal PID 1 test and the Apple graphical userland
exercise different paths.

## Capture and interpretation

The runner uses the custom binary at
`research/qemu-build-mona/qemu-system-aarch64`; `--qemu` overrides it. It rejects
stock QEMU without `mona`, generic/phone ADTs, mismatched CPU counts, wrong
GIC/timer properties, unsafe payload ranges and enabled reference input/USB
drivers. With `--kernel`, it verifies the initialized kernel bytes against the
payload and uses the matching font for framebuffer decoding. `llvm-nm` must be
on PATH or available at its standard Homebrew LLVM location.

The XNU entry stub is loaded at `0x80200000`, above QEMU's DTB reservation at
the start of RAM. The runner directly places XNU, its ADT, RAM disk and boot
arguments in memory; it does not execute the Moto bootloader or secure firmware.
There is no implicit virtual network interface.

Each capture writes `fb.png`, `regs.txt`, `qemu.log`, `qemu.stderr`,
`command.json` and `result.json`; decoded captures also include `fb.txt`.
`result.json` records the machine, synthetic RAM, reference-kernel status,
checksums and hardware gaps. QMP operations and binary/tool queries have bounded
timeouts. The temporary RAM loader images are removed after capture.

`no_panic_observed` describes a single circular framebuffer snapshot. It does
not establish successful boot. For the minimal root, separate evidence is the
PID 1 message followed by at least two `SWEET init: still alive` messages;
the runner reports `reference_pid1_liveness_observed` only when those appear
together in the captured text. Their absence can also mean the circular
console has scrolled past the evidence. This field never establishes a full
Moto boot or iOS graphical userspace.

With `--fail-on-panic`, visible panic returns 2 and blank/incomplete decoding
returns 3. Startup/capture errors return 1. Exit 0 is a completed capture,
not a general boot-success assertion. Unimplemented UFS, panel, touch, power,
radios and security/firmware devices remain visible fidelity gaps.

## Observed minimal-root results

On 2026-10-29, 150-second runs with kernel `cpus=1` and `cpus=8` both reached
the static test PID 1 and retained 23 `still alive` messages. Both snapshots
decoded with zero unknown glyphs and contained no visible panic marker.
The [validation record](../qemu/validation/core-and-xnu.json) preserves the
inputs and observations. The kernel was the previously downloaded upstream
[build from fdad0de](https://github.com/dipa-studio/ios-sweet/actions/runs/39158169212),
SHA-256 `d9cb893bd2baf841e94cd2001f0ec70c0fea42f045f87f14ae807e4ebc50e1e5`.
It was not rebuilt or changed for these experiments.

## Local iOS root experiment

This experiment uses a separately prepared, locally retained iPhone X iOS
16.7.16 root image and its product ADT. Neither is distributed here. The
known local root's SHA-256 is
`c240e0d0f8ac450ea71d7679f9f2f92ff1d95c66396600a1453388cc3899383b`.
An arbitrary unprepared IPSW image is not interchangeable with this root.

The synthetic `--full-memory` mode retains load base `0xb0000000` and exposes
RAM through `0x27ff00000`, with the last MiB reserved for panic logs. This gives
the large RAM disk and userspace space to operate inside the board's 8 GiB
development bank; it does not identify the handset's RAM carve-outs.

```sh
MONA_KERNEL=/absolute/path/to/kernel.development.sweet
MONA_IOS_ROOT=/absolute/path/to/prepared-ios-root.img
MONA_PRODUCT_ADT=/absolute/path/to/d22ap.adt

python3 tools/mk-mona-adt.py research/mona-xnu/mona-gui.adt \
  --cpus 8 --full-memory --product-adt "$MONA_PRODUCT_ADT"
python3 tools/mk-mona-payload.py "$MONA_KERNEL" research/mona-xnu/mona-gui.adt \
  research/mona-xnu/payload-gui.bin --ramdisk "$MONA_IOS_ROOT" \
  --cmdline 'debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress rd=md0 unentitled_ios_sim_launch=1 ignition_level=1 launchd_unsecure_cache=1 interrupt-masked-debug-mode=1 rootrw=1 sweetfb_noconsole=1 sweetnull=backboardd,SpringBoard,guest-sampler,Setup,budd sweettouch=0 sweetkeys=0 sweetusb=0 sweetlog=0'
python3 tools/qemu-mona.py research/mona-xnu/payload-gui.bin research/mona-xnu/gui \
  --kernel "$MONA_KERNEL" --secs 240 --hmp 'info registers -a'
```

The streaming packer writes the same payload format as `mkpayload.py` with
bounded RAM usage and atomic replacement. The runner maps only payload
metadata, streams hashes and copies, and emits sparse loader files of at most
512 MiB to avoid host raw-loader size limits. The multi-GiB mapping is closed
before launching QEMU, and temporary loader files are removed after capture.

The 240-second GUI trial reached the multilingual welcome screen. Its early
retained log confirmed all eight XNU CPUs initialized and verified SGI1;
SpringBoard and backboardd executed, and the framebuffer swap count reached
4,918. No crash or kernel-panic marker appeared in the retained log samples.
The intermediate capture shows the Turkish welcome message; the final frame
was captured during the text animation's blank phase. This uses
software rendering and the reference RAM framebuffer. No hardware touch or
handset peripheral model is established by this result. Graphical frames are
expected to produce `incomplete_decode` in the console-font decoder; inspect
the PNG and retained kernel log rather than interpreting that status as a GUI
failure or success.

The [GUI validation record](../qemu/validation/ios-gui.json) includes the
reference inputs, capture hashes, eight-core startup lines and limitations.
