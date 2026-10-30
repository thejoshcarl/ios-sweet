/* Runtime diagnostics for implemented QEMU behavior, not physical Moto validation. */
#include <stdint.h>

#define UART 0x09000000UL /* Explicitly synthetic PL011 debug console. */
#define GICD 0x17200000UL
#define GICR 0x17260000UL
#define TIMER_HZ 19200000UL
#define CORE_COUNT 8
#define VTIMER_INTID 27

extern void _secondary_start(void);
static volatile uint64_t secondary_seen[CORE_COUNT];
static volatile uint64_t secondary_midr[CORE_COUNT];
static volatile uint64_t timer_interrupts;
static volatile uint64_t last_interrupt;
static volatile uint64_t ram_pattern[512];

static inline void write32(uintptr_t address, uint32_t value)
{
    *(volatile uint32_t *)address = value;
}

static inline uint32_t read32(uintptr_t address)
{
    return *(volatile uint32_t *)address;
}

static inline uint64_t counter(void)
{
    uint64_t value;
    __asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(value));
    return value;
}

static void putc(char ch)
{
    while (read32(UART + 0x18) & (1U << 5)) {
        __asm__ volatile("yield");
    }
    write32(UART, (uint32_t)ch);
}

static void puts(const char *text)
{
    while (*text) {
        putc(*text++);
    }
}

static void hex(uint64_t value)
{
    static const char digits[] = "0123456789abcdef";
    puts("0x");
    for (int shift = 60; shift >= 0; shift -= 4) {
        putc(digits[(value >> shift) & 15]);
    }
}

static __attribute__((noreturn)) void halt(void)
{
    __asm__ volatile("msr daifset, #0xf");
    for (;;) {
        __asm__ volatile("wfe");
    }
}

static __attribute__((noreturn)) void fail(const char *reason)
{
    puts("MONA_PROBE FAIL ");
    puts(reason);
    putc('\n');
    halt();
}

static uint64_t smc(uint64_t function, uint64_t arg1, uint64_t arg2,
                    uint64_t arg3)
{
    register uint64_t x0 __asm__("x0") = function;
    register uint64_t x1 __asm__("x1") = arg1;
    register uint64_t x2 __asm__("x2") = arg2;
    register uint64_t x3 __asm__("x3") = arg3;
    __asm__ volatile("smc #0" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)
                     : : "x4", "x5", "x6", "x7", "x8", "x9", "x10",
                         "x11", "x12", "x13", "x14", "x15", "x16",
                         "x17", "memory");
    return x0;
}

void probe_secondary(uint64_t context)
{
    uint64_t mpidr, midr;
    __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
    __asm__ volatile("mrs %0, midr_el1" : "=r"(midr));
    if (context == 0 || context >= CORE_COUNT ||
        (mpidr & 0xffffffU) != (context << 8)) {
        halt();
    }
    secondary_midr[context] = midr;
    __asm__ volatile("dsb sy" : : : "memory");
    secondary_seen[context] = mpidr & 0xffffffU;
    __asm__ volatile("dsb sy; sev" : : : "memory");
    halt();
}

void probe_exception(uint64_t esr, uint64_t elr)
{
    puts("MONA_PROBE EXCEPTION esr=");
    hex(esr);
    puts(" elr=");
    hex(elr);
    putc('\n');
    fail("unexpected exception");
}

void probe_irq_handler(void)
{
    uint64_t id;
    __asm__ volatile("mrs %0, ICC_IAR1_EL1" : "=r"(id));
    last_interrupt = id;
    if (id == VTIMER_INTID) {
        __asm__ volatile("msr cntv_ctl_el0, xzr; isb" : : : "memory");
        timer_interrupts++;
    }
    if (id < 1020) {
        __asm__ volatile("msr ICC_EOIR1_EL1, %0; isb" : : "r"(id) : "memory");
    }
}

static void test_ram(void)
{
    /* Host harness fixes -m 8G. Touch both ends and above 4 GiB together to
     * detect an absent/truncated high bank or accidental address aliasing.
     * Restore these words because the lowest one may contain the guest DTB.
     */
    static const uintptr_t addresses[] = {
        0x80000000UL, 0x180000000UL, 0x27ffffff8UL,
    };
    static const uint64_t patterns[] = {
        0x13579bdf2468ace0UL, 0xfedcba9876543210UL, 0x0f1e2d3c4b5a6978UL,
    };
    uint64_t saved[3];
    unsigned bank_failure = 0;
    for (unsigned i = 0; i < 3; i++) {
        saved[i] = *(volatile uint64_t *)addresses[i];
    }
    for (unsigned i = 0; i < 3; i++) {
        *(volatile uint64_t *)addresses[i] = patterns[i];
    }
    __asm__ volatile("dsb sy" : : : "memory");
    for (unsigned i = 0; i < 3; i++) {
        if (*(volatile uint64_t *)addresses[i] != patterns[i]) {
            bank_failure = 1;
        }
    }
    for (unsigned i = 0; i < 3; i++) {
        *(volatile uint64_t *)addresses[i] = saved[i];
    }
    __asm__ volatile("dsb sy" : : : "memory");
    if (bank_failure) {
        fail("8GiB RAM boundary/alias test failed");
    }
    puts("MONA_PROBE ram_boundaries=pass size_gib=8 high_word=0x27ffffff8\n");

    for (unsigned i = 0; i < 512; i++) {
        ram_pattern[i] = 0x5aa55aa5a55aa55aUL ^ ((uint64_t)i << 32) ^ i;
    }
    __asm__ volatile("dsb sy" : : : "memory");
    for (unsigned i = 0; i < 512; i++) {
        if (ram_pattern[i] !=
            (0x5aa55aa5a55aa55aUL ^ ((uint64_t)i << 32) ^ i)) {
            fail("RAM pattern mismatch");
        }
    }
    puts("MONA_PROBE ram_pattern=pass bytes=4096\n");
}

static void test_secondaries(void)
{
    uint64_t version = smc(0x84000000U, 0, 0, 0);
    puts("MONA_PROBE psci_version=");
    hex(version);
    putc('\n');
    if ((int64_t)version < 0 || version < 2) {
        fail("PSCI unavailable");
    }
    for (unsigned cpu = 1; cpu < CORE_COUNT; cpu++) {
        uint64_t result = smc(0xc4000003U, (uint64_t)cpu << 8,
                              (uintptr_t)_secondary_start, cpu);
        if (result != 0) {
            puts("MONA_PROBE cpu_on_error cpu=");
            hex(cpu);
            puts(" result=");
            hex(result);
            putc('\n');
            fail("PSCI CPU_ON rejected");
        }
    }
    uint64_t start = counter();
    for (;;) {
        unsigned seen = 1;
        for (unsigned cpu = 1; cpu < CORE_COUNT; cpu++) {
            if (secondary_seen[cpu] == ((uint64_t)cpu << 8)) {
                seen++;
            }
        }
        if (seen == CORE_COUNT) {
            break;
        }
        if (counter() - start > TIMER_HZ * 2) {
            fail("secondary CPU handshake timed out");
        }
        __asm__ volatile("yield");
    }
    for (unsigned cpu = 1; cpu < CORE_COUNT; cpu++) {
        puts("MONA_PROBE secondary_affinity=");
        hex(secondary_seen[cpu]);
        puts(" midr=");
        hex(secondary_midr[cpu]);
        putc('\n');
        /* A78AE is an explicit development surrogate, not a renamed A78. */
        unsigned part = (secondary_midr[cpu] >> 4) & 0xfff;
        if (part != (cpu < 4 ? 0xd05U : 0xd42U)) {
            fail("secondary CPU MIDR part mismatch");
        }
    }
    puts("MONA_PROBE psci_cpu_on=pass online=8 models=4xa55+4xa78ae\n");
}

static void test_timer_irq(void)
{
    uint64_t one = 1, pmr = 0xff;
    __asm__ volatile("msr ICC_SRE_EL1, %0; isb" : : "r"(one) : "memory");
    write32(GICD + 0x000, 0x13); /* ARE + Group1 (and Group0 where present). */
    uint64_t start = counter();
    while (read32(GICD) & (1U << 31)) {
        if (counter() - start > TIMER_HZ) {
            fail("GIC distributor RWP timed out");
        }
    }
    write32(GICR + 0x14, read32(GICR + 0x14) & ~(1U << 1));
    start = counter();
    while (read32(GICR + 0x14) & (1U << 2)) {
        if (counter() - start > TIMER_HZ) {
            fail("GIC redistributor wake timed out");
        }
    }
    uintptr_t sgi = GICR + 0x10000;
    write32(sgi + 0x180, 0xffffffffU); /* Disable SGIs/PPIs during setup. */
    write32(sgi + 0x80, 0xffffffffU);  /* Group1. */
    *(volatile uint8_t *)(sgi + 0x400 + VTIMER_INTID) = 0x80;
    write32(sgi + 0x100, 1U << VTIMER_INTID);
    __asm__ volatile("dsb sy; msr ICC_PMR_EL1, %0; msr ICC_IGRPEN1_EL1, %1; isb"
                     : : "r"(pmr), "r"(one) : "memory");
    uint64_t ticks = TIMER_HZ / 100;
    __asm__ volatile("msr cntv_tval_el0, %0; msr cntv_ctl_el0, %1; isb; "
                     "msr daifclr, #2" : : "r"(ticks), "r"(one) : "memory");
    start = counter();
    while (!timer_interrupts) {
        if (counter() - start > TIMER_HZ * 2) {
            puts("MONA_PROBE last_interrupt=");
            hex(last_interrupt);
            putc('\n');
            fail("virtual timer IRQ timed out");
        }
        __asm__ volatile("yield");
    }
    __asm__ volatile("msr daifset, #2" : : : "memory");
    if (last_interrupt != VTIMER_INTID || timer_interrupts != 1) {
        fail("wrong interrupt delivered");
    }
    puts("MONA_PROBE gicv3_virtual_timer=pass intid=27\n");
}

void probe_main(void)
{
    uint64_t current_el, mpidr, frequency, midr;
    puts("MONA_PROBE START scope=qemu-core-model\n");
    __asm__ volatile("mrs %0, CurrentEL" : "=r"(current_el));
    __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(frequency));
    __asm__ volatile("mrs %0, midr_el1" : "=r"(midr));
    puts("MONA_PROBE current_el="); hex(current_el >> 2);
    puts(" mpidr="); hex(mpidr);
    puts(" midr="); hex(midr);
    puts(" cntfrq="); hex(frequency); putc('\n');
    if (current_el != 4 || (mpidr & 0xffffffU) != 0) {
        fail("boot CPU EL or affinity mismatch");
    }
    if (frequency != TIMER_HZ) {
        fail("timer frequency mismatch");
    }
    if (((midr >> 4) & 0xfffU) != 0xd05) {
        fail("boot CPU MIDR part mismatch");
    }
    test_ram();
    test_secondaries();
    test_timer_irq();
    puts("MONA_PROBE PASS scope=qemu-core-model cpus=8 timer_irq=27\n");
    halt();
}
