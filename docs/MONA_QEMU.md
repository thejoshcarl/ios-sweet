# Building and testing the experimental mona machine

This fork includes a custom QEMU machine named `mona`. It implements a tested
CPU/memory/interrupt foundation for Moto G Stylus (2025) research. It is not a
complete phone emulator. See [the hardware contract](MONA_HARDWARE.md) for
provenance and unresolved hardware details.

## Build

Requirements: Git, Python 3, a C compiler, Ninja, Meson, pkg-config, GLib
development files and QEMU's build dependencies. The diagnostic also needs
Clang with AArch64 assembly support and `ld.lld`. On macOS, the relevant
Homebrew packages include `glib`, `meson`, `ninja`, `pkgconf`, `llvm` and `lld`.
The QEMU configure step may fetch its pinned Python/subproject dependencies.

From this repository:

```sh
python3 tools/build-mona-qemu.py
python3 tools/test-mona-qemu.py
```

The build helper checks out the version in `qemu/source.json`, installs the
tracked `qemu/hw/arm/mona.c` into that local source tree, and adds the Kconfig
and Meson entries. It builds only the required AArch64 machine dependencies.
It refuses a source tree at a different upstream revision.

Default paths (all generated files are ignored by Git):

| Path | Purpose |
| --- | --- |
| `research/qemu` | Pinned QEMU source and local overlay |
| `research/qemu-build-mona/qemu-system-aarch64` | Custom emulator |
| `research/qemu-build-mona/mona-build.json` | Source and binary hashes |
| `research/mona-test` | Compiled probe, UART transcript and `result.json` |

The system-installed QEMU is not replaced. The custom binary advertises
`mona` in `-machine help`. A full QEMU GitHub fork is unnecessary to reproduce
this source overlay; it is maintained in this repository.

## Implemented behavior and diagnostic

The probe is compiled from `tests/mona/` and loaded as an ELF at EL1. It checks:

- EL1 boot and the CPU's architectural identification registers.
- Distinct restored RAM words at the bottom of the bank, above 4 GiB and at
  the top of the configured 8 GiB bank, plus a 4 KiB pattern.
- SMC PSCI calls starting all seven secondary CPUs, with the expected
  `0x100` through `0x700` affinities and four A55/four A78AE model identities.
- `CNTFRQ_EL0 = 19200000` and an actual virtual-timer interrupt delivered as
  INTID 27 through the GIC at `0x17200000`/`0x17260000`.

The host harness requires an explicit guest PASS marker, fails on an exception,
failure marker, early emulator exit or timeout, and terminates the guest after
capturing evidence. It does not infer success from an absence of panics.

The first runtime diagnostic passed on 2026-10-29 using QEMU v11.1.2 at
`4fc49f46dc95d4a27de2509e7fceb2931e91faeb`. These are tests of implemented
emulator behavior, not measurements of the physical handset.

The generated hardware tree can also be inspected:

```sh
research/qemu-build-mona/qemu-system-aarch64 \
  -machine mona,dumpdtb=research/mona-test/machine.dtb \
  -nodefaults -display none -serial null
dtc -I dtb -O dts research/mona-test/machine.dtb
```

Its root identifies `qemu,mona-development` and includes explicit approximation
markers. The tree declares the Moto interrupt/timer/affinity values alongside
the development substitutes.

## Current fidelity limits

| Area | Implemented state |
| --- | --- |
| Little CPUs | Four QEMU Cortex-A55s; CPU0 revision matches saved r2p0 identity |
| Big CPUs | Four Cortex-A78AE substitutes, not an identical Cortex-A78 implementation |
| RAM | Configurable contiguous development bank at `0x80000000`, default 8 GiB; handset banks/carve-outs unresolved |
| GIC and timer | Moto firmware addresses, affinities and described interrupt wiring; generic QEMU GICv3/Arm timer behavior |
| Timer scale | CNTFRQ reports 19.2 MHz; QEMU's integer 52 ns tick scale is approximately 0.16% fast relative to virtual time |
| Firmware | Direct ELF/loader entry at EL1 and QEMU SMC PSCI; Qualcomm secure monitor, EL2 and EL3 absent |
| Console | Synthetic PL011 at `0x09000000`, SPI 1 (INTID 33); not the phone's GENI UART |
| Peripherals | Qualcomm UFS integration, display/panel, SPI touch, PMIC/power, GPU, radios and other phone devices are not modeled |

Missing peripherals are not represented by fake success-returning controllers.
Their MMIO remains unimplemented. The first intended OS experiment is a
reference SWEET XNU kernel with a RAM-disk root on the modeled core platform.
That result must be reported separately from a Moto driver port or complete
iOS phone boot.

The C machine implementation is GPL-2.0-or-later, matching its QEMU integration.
QEMU remains under its upstream licensing terms.

## XNU and graphical userspace

The reference XNU kernel reached the test PID 1 and produced 23 liveness
messages in each of the single-core and eight-core captures. The separate
iOS-root experiment also reached a rendered multilingual welcome screen and
recorded `8/8 CPUs initialized, SGI1 verified` in the kernel log. These use
the reference SWEET framebuffer and drivers, not a complete Moto platform port.

See [XNU preparation and capture](MONA_XNU.md) for the minimal root and GUI
commands, and [recorded validation](../qemu/validation/core-and-xnu.json) for
the core/minimal-root results and checksums. The separate
[GUI validation record](../qemu/validation/ios-gui.json) captures the four-minute
run, eight-core XNU startup, screenshot hashes and 4,918 framebuffer swaps.
