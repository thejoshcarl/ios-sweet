# Mona hardware contract

The machine-readable [board profile](../boards/mona.json) separates the Moto
hardware evidence from the development choices used to build an incomplete
QEMU model. It describes the XT2517V Moto G Stylus (2025), `mona`, Qualcomm
SM6475/`parrot`. It is not a firmware image or a handset flashing configuration.

The user authorized the closest feasible implementation on 2026-10-29. Partial
models are useful development milestones; they do not establish identical
phone hardware or a working Moto iOS boot.

## Evidence and its limits

Live read-only fastboot observations on **2026-10-30** confirmed the handset's
XT2517V / `mona` identity and reported `hwrev: PVT`. PVT is a bootloader label,
not a unique board identifier or a complete specification of every component
revision. The current Android 16 build is `W1VAS36.62-22-16-1-3-7`.
The sanitized [live observation report](../qemu/validation/mona-live-2026-10-30.json)
records the public hardware facts without serial numbers, IMEIs or raw dumps.

| Current live fact | Reported value | Interpretation |
| --- | --- | --- |
| Bootloader | `MBM-3.0-mona-b11af6458aa6-260827-VVA3S5V-W1-ST15.1.3-0c763` | Current reported version; no matching current firmware package was located |
| TrustZone | `MBM-3.0-mona-f8a54d93f-260827` | Reported component version; no secure-world behavior was tested |
| Security / carrier | Secure production state, `oem_locked`, CID 51, TracFone | Bootloader reports; no unlock or firmware write was performed |
| Installed RAM | 8 GB SK hynix LPDDR4X | Capacity/type/vendor report; physical banks and carve-outs remain unknown |
| Installed UFS | 128 GB SK hynix `H9QT0G6CN6X146`, firmware `A003`, WB value 2048 | Component model information, not a serial number or a UFS driver test |
| Primary panel | `tm_nt37706_667_1220p_dsc_vdo_120hz_v1` | Bootloader-selected Tianma NT37706 panel identifier; framebuffer address, stride and format remain unknown |

This unit's reported **128 GB** storage is the target fact. Motorola's 256 GB
US retail listing describes another product configuration and must not be used
as a substitute for the live handset's report. The exact Goodix touch chip is
still unresolved; panel selection does not establish touch-controller identity.

The earlier evidence combines saved read-only observations from this XT2517V,
analysis of device trees from its matching Android 16 firmware build
`W1VAS36.62-22-16-1-3-6`, and Motorola's public Android 15 device-tree snapshot
at commit `9454192995fbc9443f5ad9c0563bf1386949c5eb`. The selected interrupt,
timer, CPU-affinity and PSCI properties agree between these firmware sources.
These remain older firmware configuration facts, not measurements of a
running custom kernel or a newly verified device tree for the current `-7`
build. The address-map rows below retain that earlier provenance.
The saved firmware analysis checked package consistency and correspondence to
the observed build; it did not independently authenticate Motorola's signature
chain.

| Item | Hardware evidence | Source and limit |
| --- | --- | --- |
| CPUs | Four Cortex-A55 r2p0 and four Cortex-A78 r1p2 | Saved implementer/part/variant/revision observations; not re-read during this implementation |
| CPU affinities | `0x000`, `0x100`, ... `0x700` | Matching firmware DTBs and Motorola `parrot.dtsi` |
| GIC distributor | `0x17200000`, size `0x10000` | Firmware description; runtime MMIO untested |
| GIC redistributors | `0x17260000`, size `0x100000`, stride `0x20000` | Firmware description; eight redistributor frames |
| Generic timer | 19,200,000 Hz; virtual timer INTID 27 | DT frequency and PPI 11 + 16; runtime CNTFRQ not captured |
| Other timer INTIDs | Secure physical 29, nonsecure physical 30, EL2 physical 28 | Derived from the Arm timer binding's tuple order |
| PSCI | `arm,psci-1.0`, SMC conduit | Firmware contract; secure monitor internals are not specified |
| UFS | Controller at `0x01d84000` | Firmware and saved Android boot-device property; PHY/clock/DMA behavior remains unresolved |
| Display | 1220 x 2712, up to 120 Hz | Saved Android mode information and current primary-panel selection; framebuffer handoff remains unresolved |
| Touch | Goodix driver on SPI controller `0xa8c000`; ten slots | Saved live observations; exact controller chip unresolved |

Primary source references: [Motorola mona board overlay](https://github.com/MotorolaMobilityLLC/kernel-devicetree/blob/9454192995fbc9443f5ad9c0563bf1386949c5eb/qcom/parrot-mona-evb-overlay.dts),
[parrot SoC description](https://github.com/MotorolaMobilityLLC/kernel-devicetree/blob/9454192995fbc9443f5ad9c0563bf1386949c5eb/qcom/parrot.dtsi),
and [mona touchscreen overlay](https://github.com/MotorolaMobilityLLC/kernel-devicetree/blob/9454192995fbc9443f5ad9c0563bf1386949c5eb/qcom/parrot-mona-touchscreen-overlay.dtsi).
The public overlay describes a GT9916P and selects between panel candidates;
the current bootloader resolves the selected panel name, while the overlay
alone still does not prove the exact touch-controller variant in this handset.

## Explicit development choices

The initial development RAM bank starts at `0x80000000` and defaults to 8 GiB.
It is a configurable synthetic map. The real usable banks, reserved regions,
Qualcomm protection carve-outs and bootloader load addresses are unknown.
Motorola's static `/memory/reg` contains four zero cells that firmware must
populate; treating those zeros as a complete physical memory map would be
incorrect. A contiguous 8 GiB bank is an emulator development choice.

The QEMU CPU classes must be distinguished from the observed handset cores.
In particular, substituting `cortex-a78ae` or `cortex-a76` for the missing
`cortex-a78` class changes guest-visible CPU features and implementation
behavior. The model must disclose whichever substitute it uses. A changed
name or CPU ID alone cannot establish A78 equivalence.

PSCI calls may initially be handled by QEMU's architectural PSCI implementation.
That can exercise CPU power-on and affinity handling without executing the
phone's secure monitor firmware. Such a test does not validate Qualcomm SCM
services or the original boot chain.

## Remaining device work

The board contract does not supply Qualcomm peripheral implementations. UFS
requires its host integration, PHY, clocks, resets, DMA and storage semantics.
Display requires SDE/DSI/DSC and panel behavior. Touch requires GENI SPI,
Goodix protocol, GPIOs and power. PMIC buttons, RPMh/SPMI, clocks and memory
protection are separate models. Reusing an existing QEMU controller is useful
only where its actual behavior matches the Moto hardware contract.

Adreno GPU, DSP, cellular, Wi-Fi/Bluetooth, audio, cameras/ISP, sensors, NFC,
fingerprint, security services and original firmware dependencies remain part
of the full phone inventory. Missing implementations must remain visible in
test results. CPU/timer/interrupt success is a foundation for later bring-up,
not proof of peripheral support, iOS userland boot or handset compatibility.

No device dumps, serial numbers, calibration data or proprietary Apple/phone
binaries are included in this contract. Unknown fields are `null`, rather than
invented addresses or values copied from the Redmi reference.
