# Moto G Stylus (2025): exact QEMU target assessment

**Implementation update:** The user subsequently authorized the closest
feasible model. An incomplete custom `mona` machine now passes core-platform
diagnostics. See [the current implementation](MONA_QEMU.md). The assessment
below records the initial exact-hardware requirement and remaining gaps.

Assessment date: 2026-10-29. Upstream baseline:
`dipa-studio/ios-sweet@45263bee51df4a0f55cdf9c649af886399488ddb`.

## Status

The GitHub fork exists at <https://github.com/thejoshcarl/ios-sweet>.
**An exact Moto G Stylus (2025) machine is not implemented, and no Moto boot
has been demonstrated.** The requested hardware equivalence cannot be achieved
by changing the existing runner's CPU, memory, storage or display arguments.
The user explicitly requires exact phone hardware; a generic `virt` boot does
not satisfy this requirement.

The installed QEMU 11.1.2 has no Motorola, Snapdragon 6 Gen 3 or SM6475 machine
in its `-machine help` inventory. Its CPU inventory includes `cortex-a55` and
`cortex-a78ae`, but no `cortex-a78`, Kryo or Snapdragon CPU model. A78AE is not
an identical model of an A78. These observations concern this QEMU installation,
not proof that no third-party implementation could ever be developed.

GitHub repository searches for `QEMU SM6475`, `QEMU SM6450`, `QEMU mona`
and `QEMU parrot Qualcomm`, plus code search for `SM6475 qemu`, did not locate
an exact board implementation in this review. Qualcomm-hosted virtualization
and partial device emulation projects are not evidence of a complete mona model.

QEMU's [Arm system documentation](https://www.qemu.org/docs/master/system/target-arm.html)
explains why selecting a compatible instruction set does not select the right
board. The [`virt` documentation](https://www.qemu.org/docs/master/system/arm/virt.html)
explicitly describes a virtual platform that corresponds to no real hardware.

## Verified product specifications

These describe Motorola's US product listing, not a complete register-level
board specification. An exact target must also select a particular SKU, board
revision and firmware release.

| Component | Motorola product specification |
| --- | --- |
| SoC | Qualcomm Snapdragon 6 Gen 3 |
| RAM | 8 GB LPDDR4X |
| Storage | 256 GB uMCP; microSD expansion up to 1 TB |
| Display | 6.7-inch pOLED, 1220 x 2712, adaptive refresh up to 120 Hz |
| Connectivity | 5G, Wi-Fi 6E, Bluetooth, NFC; USB 2.0 Type-C |

Sources: [Motorola specifications](https://www.motorola.com/us/en/p/phones/moto-g/g-stylus-gen-5/pmipmhl40me?pn=PB6V0015US),
[Motorola announcement](https://motorolanews.com/creativity-meets-durability-in-the-new-moto-g-stylus-2025/),
and [Qualcomm platform description](https://www.qualcomm.com/smartphones/products/6-series/snapdragon-6-gen-3-mobile-platform).
Qualcomm describes the platform's Kryo CPU, Adreno GPU, Hexagon processor,
Spectra ISP, modem and FastConnect subsystem. The product names alone do not
specify their MMIO behavior, firmware interfaces or board wiring.

## Why the current code is not a Moto model

| Existing implementation | Required Moto work |
| --- | --- |
| `tools/qemu-sweet.py` selects `virt,gic-version=3`, one CPU and synthetic RAM placement | A QEMU board with the verified Moto address map, CPU topology, interrupts and firmware handoff |
| `tools/mkadt.py --qemu` uses virt GIC addresses and a 62.5 MHz timer; it emits one CPU and omits UFS | Device-tree generation from the selected Moto hardware contract and the actual implemented models |
| The framebuffer is a byte array in RAM at `0x9c000000`, captured as a PNG | Display-controller, DSI/DSC, panel and touch models with the selected unit's behavior |
| The non-QEMU tree describes SWEET/SM7150; the shim has SWEET-specific register addresses | A Moto platform/shim with validated memory ranges, MMIO, interrupt routing and boot state |
| The XNU patches contain SWEET-specific UFS, SPMI, RPMh, GENI touch and USB assumptions | Matching Moto drivers and modeled controllers, clocks, resets, power domains and DMA |
| SMP uses SWEET-specific affinity checks and SMC-based PSCI calls | Correct CPU identities, affinities, reset state and secure monitor behavior for the Moto target |

The runner places the kernel and generated boot arguments directly in RAM;
it bypasses the phone's bootloader chain. A successful run of that path would
validate a subset of XNU behavior on `virt`, not Moto firmware or hardware.

Prior local research identifies an XT2517V (`mona`, SM6475/`parrot`) and includes
firmware-derived interrupt, timer and storage addresses. That work is a useful
starting point, but it leaves usable RAM ranges, active panel identity,
framebuffer handoff, exact touch controller and runtime register behavior
unresolved. No device dumps, identifiers or firmware binaries are included here.
Static device trees describe connections and configuration; they do not
implement the devices they name.

Motorola publishes a [mona board overlay](https://github.com/MotorolaMobilityLLC/kernel-devicetree/blob/9454192995fbc9443f5ad9c0563bf1386949c5eb/qcom/parrot-mona-evb-overlay.dts)
and the associated [parrot SoC description](https://github.com/MotorolaMobilityLLC/kernel-devicetree/blob/9454192995fbc9443f5ad9c0563bf1386949c5eb/qcom/parrot.dtsi).
This Android 15 source snapshot is a primary reference for the board wiring,
not confirmation of the saved XT2517V Android 16 firmware's exact configuration.
It supplies a starting point for model development, not a QEMU implementation.

## Work required before an exact boot claim

1. Fix the target SKU, board revision and firmware version. Resolve the live
   memory map and reserved regions, CPU feature registers, timer, active panel,
   touch controller and bootloader handoff. Record provenance for each value.
2. Implement a dedicated QEMU Moto/SM6475 board and its CPU/SoC behavior. Model
   interrupt routing, timer/PSCI, clocks/resets, power management, memory
   protection and DMA, then the boot-required storage, display and input paths.
   Inventory remaining peripherals explicitly; omitted devices preclude a
   claim of identical phone hardware.
3. Define and reproduce the chosen boot entry state. Direct XNU loading and
   execution of the original firmware chain are different validation scopes.
   The latter additionally requires models for the firmware's secure-world,
   boot/storage and other dependencies.
4. Port XNU's platform and hardware drivers to that verified board contract.
   Reusing a register address from SWEET does not establish driver equivalence.
5. Compare register behavior and device transactions against the selected
   handset. Validate exceptions, timers, interrupts and all eight CPUs before
   storage and userland. Preserve full boot logs and repeatable checks.

Boot-critical subset models can be intermediate milestones, but they must not
be described as an identical phone. No working launcher or boot image can be
provided until the required models and platform support exist.

## Checks performed in this fork

```sh
qemu-system-aarch64 --version
qemu-system-aarch64 -machine help
qemu-system-aarch64 -M virt -cpu help
python3 tools/qemu-sweet.py --check-env
```

The environment check passed for the existing generic `cortex-a76` runner.
It checks dependencies, not board compatibility or successful boot. No guest
was started for this assessment. No CI kernel build was dispatched, and no
phone was accessed or modified.
