/* Bare-metal tests for the experimental QEMU mona board, not handset firmware. */
.section .text.boot, "ax"
.global _start
.global _secondary_start
.type _start, %function
_start:
    msr daifset, #0xf
    adrp x0, __bss_start
    add x0, x0, :lo12:__bss_start
    adrp x1, __bss_end
    add x1, x1, :lo12:__bss_end
1:  cmp x0, x1
    b.hs 2f
    str xzr, [x0], #8
    b 1b
2:  adrp x0, probe_stacks
    add x0, x0, :lo12:probe_stacks
    add x0, x0, #0x4000
    mov sp, x0
    adrp x0, probe_vectors
    add x0, x0, :lo12:probe_vectors
    msr vbar_el1, x0
    isb
    bl probe_main
3:  wfe
    b 3b
.size _start, . - _start

.type _secondary_start, %function
_secondary_start:
    msr daifset, #0xf
    mov x19, x0
    mrs x1, mpidr_el1
    ubfx x1, x1, #8, #8
    cmp x1, #8
    b.hs 3b
    adrp x2, probe_stacks
    add x2, x2, :lo12:probe_stacks
    add x1, x1, #1
    add x2, x2, x1, lsl #14
    mov sp, x2
    adrp x1, probe_vectors
    add x1, x1, :lo12:probe_vectors
    msr vbar_el1, x1
    isb
    mov x0, x19
    bl probe_secondary
    b 3b
.size _secondary_start, . - _secondary_start

/* Each architectural vector slot is 128 bytes. */
.balign 2048
probe_vectors:
.macro VECTOR target
    b \target
    .balign 128
.endm
    VECTOR probe_sync
    VECTOR probe_irq
    VECTOR probe_sync
    VECTOR probe_sync
    VECTOR probe_sync
    VECTOR probe_irq
    VECTOR probe_sync
    VECTOR probe_sync
    VECTOR probe_sync
    VECTOR probe_irq
    VECTOR probe_sync
    VECTOR probe_sync
    VECTOR probe_sync
    VECTOR probe_irq
    VECTOR probe_sync
    VECTOR probe_sync

probe_irq:
    sub sp, sp, #256
    stp x0, x1, [sp, #0]
    stp x2, x3, [sp, #16]
    stp x4, x5, [sp, #32]
    stp x6, x7, [sp, #48]
    stp x8, x9, [sp, #64]
    stp x10, x11, [sp, #80]
    stp x12, x13, [sp, #96]
    stp x14, x15, [sp, #112]
    stp x16, x17, [sp, #128]
    stp x18, x19, [sp, #144]
    stp x20, x21, [sp, #160]
    stp x22, x23, [sp, #176]
    stp x24, x25, [sp, #192]
    stp x26, x27, [sp, #208]
    stp x28, x29, [sp, #224]
    str x30, [sp, #240]
    bl probe_irq_handler
    ldp x0, x1, [sp, #0]
    ldp x2, x3, [sp, #16]
    ldp x4, x5, [sp, #32]
    ldp x6, x7, [sp, #48]
    ldp x8, x9, [sp, #64]
    ldp x10, x11, [sp, #80]
    ldp x12, x13, [sp, #96]
    ldp x14, x15, [sp, #112]
    ldp x16, x17, [sp, #128]
    ldp x18, x19, [sp, #144]
    ldp x20, x21, [sp, #160]
    ldp x22, x23, [sp, #176]
    ldp x24, x25, [sp, #192]
    ldp x26, x27, [sp, #208]
    ldp x28, x29, [sp, #224]
    ldr x30, [sp, #240]
    add sp, sp, #256
    eret

probe_sync:
    msr daifset, #0xf
    mrs x0, esr_el1
    mrs x1, elr_el1
    bl probe_exception
    b 3b

.section .bss, "aw", %nobits
.balign 16
probe_stacks:
    .skip 8 * 0x4000
