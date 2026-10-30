/*
 * Motorola mona / SM6475 development machine.
 *
 * Copyright (c) 2026 Joshua Srery
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * This is an incomplete hardware model, not an emulation of the whole phone.
 * Moto-derived: CPU affinities, GICv3 addresses, 19.2 MHz timer, SMC PSCI.
 * Development substitutes: A78AE for A78, contiguous RAM, PL011 console,
 * and QEMU PSCI instead of Qualcomm firmware. Unimplemented MMIO is unmapped.
 *
 * Source: MotorolaMobilityLLC/kernel-devicetree at
 * 9454192995fbc9443f5ad9c0563bf1386949c5eb, qcom/parrot.dtsi.
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/module.h"
#include "qemu/units.h"
#include "qobject/qlist.h"
#include "hw/arm/boot.h"
#include "hw/arm/machines-qom.h"
#include "hw/char/pl011.h"
#include "hw/core/boards.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/sysbus.h"
#include "hw/intc/arm_gicv3_common.h"
#include "system/address-spaces.h"
#include "system/device_tree.h"
#include "system/system.h"
#include "system/tcg.h"
#include "target/arm/cpu.h"
#include "target/arm/gtimer.h"

#define TYPE_MONA_MACHINE MACHINE_TYPE_NAME("mona")
OBJECT_DECLARE_SIMPLE_TYPE(MonaMachineState, MONA_MACHINE)

#define MONA_MAX_CPUS 8
#define MONA_NUM_IRQS 256
#define MONA_GICD_BASE 0x17200000ULL
#define MONA_GICR_BASE 0x17260000ULL
#define MONA_GICR_STRIDE 0x20000ULL
#define MONA_TIMER_HZ 19200000
/* Development memory arrangement; handset banks/carve-outs are unresolved. */
#define MONA_RAM_BASE 0x80000000ULL
/* Development console: NOT the handset's Qualcomm GENI UART. */
#define MONA_UART_BASE 0x09000000ULL
#define MONA_UART_SPI 1

struct MonaMachineState {
    MachineState parent_obj;
    struct arm_boot_info bootinfo;
    DeviceState *gic;
    int fdt_size;
};

static void *mona_get_dtb(const struct arm_boot_info *info, int *size)
{
    MonaMachineState *s = container_of(info, MonaMachineState, bootinfo);
    MachineState *ms = MACHINE(s);

    *size = s->fdt_size;
    return g_memdup2(ms->fdt, *size);
}

static void mona_create_fdt(MonaMachineState *s)
{
    MachineState *ms = MACHINE(s);
    void *fdt = create_device_tree(&s->fdt_size);
    uint32_t gic_phandle;
    uint32_t clock_phandle;
    const char *gic_path = "/interrupt-controller@17200000";
    const char *uart_path = "/pl011@9000000";
    const char uart_compatible[] = "arm,pl011\0arm,primecell";
    const char clock_names[] = "uartclk\0apb_pclk";

    if (!fdt) {
        error_report("mona: unable to create device tree");
        exit(EXIT_FAILURE);
    }
    ms->fdt = fdt;
    qemu_fdt_setprop_string(fdt, "/", "model",
                           "Moto mona development model (incomplete)");
    qemu_fdt_setprop_string(fdt, "/", "compatible", "qemu,mona-development");
    qemu_fdt_setprop_cell(fdt, "/", "#address-cells", 2);
    qemu_fdt_setprop_cell(fdt, "/", "#size-cells", 2);
    qemu_fdt_setprop(fdt, "/", "qemu,incomplete-hardware-model", NULL, 0);
    qemu_fdt_setprop_string(fdt, "/", "qemu,approximations",
                           "A78AE-for-A78; contiguous-RAM; PL011; emulated-PSCI");

    qemu_fdt_add_subnode(fdt, "/chosen");
    qemu_fdt_setprop_string(fdt, "/chosen", "stdout-path", "/pl011@9000000");
    qemu_fdt_add_subnode(fdt, "/memory@80000000");
    qemu_fdt_setprop_string(fdt, "/memory@80000000", "device_type", "memory");
    qemu_fdt_setprop_sized_cells(fdt, "/memory@80000000", "reg",
                               2, MONA_RAM_BASE, 2, ms->ram_size);

    qemu_fdt_add_subnode(fdt, "/cpus");
    qemu_fdt_setprop_cell(fdt, "/cpus", "#address-cells", 2);
    qemu_fdt_setprop_cell(fdt, "/cpus", "#size-cells", 0);
    for (int i = ms->smp.cpus - 1; i >= 0; i--) {
        g_autofree char *path = g_strdup_printf("/cpus/cpu@%x", i << 8);
        ARMCPU *cpu = ARM_CPU(qemu_get_cpu(i));

        qemu_fdt_add_subnode(fdt, path);
        qemu_fdt_setprop_string(fdt, path, "device_type", "cpu");
        qemu_fdt_setprop_string(fdt, path, "compatible", cpu->dtb_compatible);
        qemu_fdt_setprop_u64(fdt, path, "reg", i << 8);
        qemu_fdt_setprop_string(fdt, path, "enable-method", "psci");
        if (i >= 4) {
            qemu_fdt_setprop_string(fdt, path, "qemu,substitute-for",
                                   "arm,cortex-a78");
        }
    }

    qemu_fdt_add_subnode(fdt, "/psci");
    qemu_fdt_setprop_string(fdt, "/psci", "compatible", "arm,psci-1.0");
    qemu_fdt_setprop_string(fdt, "/psci", "method", "smc");

    gic_phandle = qemu_fdt_alloc_phandle(fdt);
    qemu_fdt_setprop_cell(fdt, "/", "interrupt-parent", gic_phandle);
    qemu_fdt_add_subnode(fdt, gic_path);
    qemu_fdt_setprop_string(fdt, gic_path, "compatible", "arm,gic-v3");
    qemu_fdt_setprop_cell(fdt, gic_path, "#interrupt-cells", 3);
    qemu_fdt_setprop_cell(fdt, gic_path, "#redistributor-regions", 1);
    qemu_fdt_setprop(fdt, gic_path, "interrupt-controller", NULL, 0);
    qemu_fdt_setprop_cells(fdt, gic_path, "interrupts", 1, 9, 8);
    qemu_fdt_setprop_cell(fdt, gic_path, "phandle", gic_phandle);
    qemu_fdt_setprop_sized_cells(fdt, gic_path, "reg",
                               2, MONA_GICD_BASE, 2, 0x10000,
                               2, MONA_GICR_BASE,
                               2, MONA_GICR_STRIDE * ms->smp.cpus);
    qemu_fdt_setprop_u64(fdt, gic_path, "redistributor-stride",
                        MONA_GICR_STRIDE);

    qemu_fdt_add_subnode(fdt, "/timer");
    qemu_fdt_setprop_string(fdt, "/timer", "compatible", "arm,armv8-timer");
    qemu_fdt_setprop_cell(fdt, "/timer", "clock-frequency", MONA_TIMER_HZ);
    qemu_fdt_setprop_cells(fdt, "/timer", "interrupts",
                           1, 13, 0xff08, 1, 14, 0xff08,
                           1, 11, 0xff08, 1, 12, 0xff08);
    qemu_fdt_setprop(fdt, "/timer", "always-on", NULL, 0);

    qemu_fdt_add_subnode(fdt, "/pmu");
    qemu_fdt_setprop_string(fdt, "/pmu", "compatible", "arm,armv8-pmuv3");
    qemu_fdt_setprop_cells(fdt, "/pmu", "interrupts", 1, 7, 8);

    clock_phandle = qemu_fdt_alloc_phandle(fdt);
    qemu_fdt_add_subnode(fdt, "/apb-pclk");
    qemu_fdt_setprop_string(fdt, "/apb-pclk", "compatible", "fixed-clock");
    qemu_fdt_setprop_cell(fdt, "/apb-pclk", "#clock-cells", 0);
    qemu_fdt_setprop_cell(fdt, "/apb-pclk", "clock-frequency", 24000000);
    qemu_fdt_setprop_cell(fdt, "/apb-pclk", "phandle", clock_phandle);
    qemu_fdt_add_subnode(fdt, uart_path);
    qemu_fdt_setprop(fdt, uart_path, "compatible",
                     uart_compatible, sizeof(uart_compatible));
    qemu_fdt_setprop_sized_cells(fdt, uart_path, "reg",
                               2, MONA_UART_BASE, 2, 0x1000);
    qemu_fdt_setprop_cells(fdt, uart_path, "interrupts", 0, MONA_UART_SPI, 4);
    qemu_fdt_setprop_cells(fdt, uart_path, "clocks",
                           clock_phandle, clock_phandle);
    qemu_fdt_setprop(fdt, uart_path, "clock-names",
                     clock_names, sizeof(clock_names));
    qemu_fdt_setprop(fdt, uart_path, "qemu,development-console", NULL, 0);
}

static void mona_create_gic(MonaMachineState *s)
{
    MachineState *ms = MACHINE(s);
    QList *redist_count = qlist_new();
    SysBusDevice *bus;

    s->gic = qdev_new(gicv3_class_name());
    qdev_prop_set_uint32(s->gic, "revision", 3);
    qdev_prop_set_uint32(s->gic, "num-cpu", ms->smp.cpus);
    qdev_prop_set_uint32(s->gic, "num-irq", MONA_NUM_IRQS + 32);
    /* No secure firmware is modeled; expose the nonsecure GIC view. */
    qdev_prop_set_bit(s->gic, "has-security-extensions", false);
    qlist_append_int(redist_count, ms->smp.cpus);
    qdev_prop_set_array(s->gic, "redist-region-count", redist_count);
    bus = SYS_BUS_DEVICE(s->gic);
    sysbus_realize_and_unref(bus, &error_fatal);
    sysbus_mmio_map(bus, 0, MONA_GICD_BASE);
    sysbus_mmio_map(bus, 1, MONA_GICR_BASE);

    for (int i = 0; i < ms->smp.cpus; i++) {
        DeviceState *cpu = DEVICE(qemu_get_cpu(i));
        int ppi_base = MONA_NUM_IRQS + i * GIC_INTERNAL;
        const struct {
            int output;
            int intid;
        } timer_irq[] = {
            { GTIMER_PHYS, 30 },
            { GTIMER_VIRT, 27 },
            { GTIMER_HYP, 28 },
            { GTIMER_SEC, 29 },
        };

        /* Other EL2 timer outputs are unused: EL2 is not modeled here. */
        for (int t = 0; t < ARRAY_SIZE(timer_irq); t++) {
            int input = ppi_base + timer_irq[t].intid;

            qdev_connect_gpio_out(cpu, timer_irq[t].output,
                                 qdev_get_gpio_in(s->gic, input));
        }
        qdev_connect_gpio_out_named(cpu, "gicv3-maintenance-interrupt", 0,
                                   qdev_get_gpio_in(s->gic, ppi_base + 25));
        qdev_connect_gpio_out_named(cpu, "pmu-interrupt", 0,
                                   qdev_get_gpio_in(s->gic, ppi_base + 23));
        sysbus_connect_irq(bus, i, qdev_get_gpio_in(cpu, ARM_CPU_IRQ));
        sysbus_connect_irq(bus, i + ms->smp.cpus,
                           qdev_get_gpio_in(cpu, ARM_CPU_FIQ));
        sysbus_connect_irq(bus, i + 2 * ms->smp.cpus,
                           qdev_get_gpio_in(cpu, ARM_CPU_VIRQ));
        sysbus_connect_irq(bus, i + 3 * ms->smp.cpus,
                           qdev_get_gpio_in(cpu, ARM_CPU_VFIQ));
    }
}

static void mona_init(MachineState *ms)
{
    MonaMachineState *s = MONA_MACHINE(ms);

    if (!tcg_enabled()) {
        error_report("mona: this model requires the TCG accelerator");
        exit(EXIT_FAILURE);
    }
    if (ms->ram_size < 64 * MiB || ms->ram_size > 8 * GiB) {
        error_report("mona: development RAM must be between 64 MiB and 8 GiB");
        exit(EXIT_FAILURE);
    }
    if (strcmp(ms->cpu_type, ARM_CPU_TYPE_NAME("cortex-a55"))) {
        error_report("mona: fixed CPUs (A55 0-3, A78AE substitutes 4-7); "
                     "omit -cpu");
        exit(EXIT_FAILURE);
    }
    for (int i = 0; i < ms->smp.cpus; i++) {
        const char *type = i < 4 ? ARM_CPU_TYPE_NAME("cortex-a55") :
                                  ARM_CPU_TYPE_NAME("cortex-a78ae");
        Object *obj = object_new(type);
        CPUState *cs = CPU(obj);

        cs->cpu_index = i;
        object_property_set_int(obj, "mp-affinity", i << 8, &error_fatal);
        object_property_set_int(obj, "cntfrq", MONA_TIMER_HZ, &error_fatal);
        object_property_set_bool(obj, "has_el3", false, &error_fatal);
        object_property_set_bool(obj, "has_el2", false, &error_fatal);
        object_property_set_int(obj, "psci-conduit", QEMU_PSCI_CONDUIT_SMC,
                                &error_fatal);
        object_property_set_bool(obj, "start-powered-off", i != 0,
                                 &error_fatal);
        object_property_set_link(obj, "memory", OBJECT(get_system_memory()),
                                 &error_fatal);
        qdev_realize(DEVICE(obj), NULL, &error_fatal);
        object_unref(obj);
    }
    memory_region_add_subregion(get_system_memory(), MONA_RAM_BASE, ms->ram);
    mona_create_gic(s);
    pl011_create(MONA_UART_BASE, qdev_get_gpio_in(s->gic, MONA_UART_SPI),
                 serial_hd(0));
    mona_create_fdt(s);

    s->bootinfo.ram_size = ms->ram_size;
    s->bootinfo.loader_start = MONA_RAM_BASE;
    s->bootinfo.board_id = -1;
    s->bootinfo.psci_conduit = QEMU_PSCI_CONDUIT_SMC;
    s->bootinfo.get_dtb = mona_get_dtb;
    arm_load_kernel(ARM_CPU(first_cpu), ms, &s->bootinfo);
}

static void mona_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);

    mc->desc = "Moto mona development model "
               "(incomplete; A78AE/RAM/UART substitutes)";
    mc->init = mona_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a55");
    mc->default_cpus = MONA_MAX_CPUS;
    mc->max_cpus = MONA_MAX_CPUS;
    mc->default_ram_size = 8 * GiB;
    mc->default_ram_id = "mona.ram";
    mc->minimum_page_bits = 12;
    mc->no_cdrom = true;
    mc->block_default_type = IF_NONE;
}

static const TypeInfo mona_type_info = {
    .name = TYPE_MONA_MACHINE,
    .parent = TYPE_MACHINE,
    .instance_size = sizeof(MonaMachineState),
    .class_init = mona_class_init,
    .interfaces = aarch64_machine_interfaces,
};

static void mona_register_types(void)
{
    type_register_static(&mona_type_info);
}
type_init(mona_register_types)
