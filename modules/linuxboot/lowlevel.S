/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Wonder Lantern Z30 contributors
 *
 * Low-level pieces of the Linux handoff (linuxboot.c). ARM state for the parts
 * that run with the MMU off or in an exception context.
 *
 * No L2 on this core (camera benchmark: CLIDR has L1 only), so cleaning L1 by
 * set/way pushes every dirty line to DRAM.
 */
    .syntax unified
    .arch armv7-a
    .text

/* Clean and invalidate the whole L1 data cache by set/way (ARM ARM B2.2.7).
 * Clobbers r0-r7. Interrupts must be off. */
    .macro L1_CLEAN_INV
    mov r0, #0
    mcr p15, 2, r0, c0, c0, 0          /* CSSELR = L1 data */
    isb
    mrc p15, 1, r1, c0, c0, 0          /* CCSIDR */
    and r2, r1, #7
    add r2, r2, #4                     /* set shift = log2(line bytes) */
    ubfx r3, r1, #3, #10               /* way = ways - 1, counts down */
    clz r4, r3                         /* way shift */
    ubfx r5, r1, #13, #15              /* sets - 1 */
10: mov r6, r5                         /* set = sets - 1, counts down */
11: lsl r7, r3, r4
    orr r7, r7, r6, lsl r2
    mcr p15, 0, r7, c7, c14, 2         /* DCCISW */
    subs r6, r6, #1
    bge 11b
    subs r3, r3, #1
    bge 10b
    dsb sy
    .endm

    .arm
/* void lb_clean_dcache(void): clean+invalidate L1 D, from Thumb C. */
    .global lb_clean_dcache
    .type lb_clean_dcache, %function
    .p2align 2
lb_clean_dcache:
    push {r4-r7, lr}
    L1_CLEAN_INV
    pop {r4-r7, pc}

/* void lb_jump(u32 entry, u32 dtb): never returns.
 * Interrupts off; clean+invalidate L1 D; turn the MMU, D-cache and I-cache
 * off (this code runs identity mapped); invalidate I-cache, branch predictor
 * and TLB; enter the kernel in ARM state with r0 = 0, r1 = ~0 (DT boot), r2 = dtb. */
    .global lb_jump
    .type lb_jump, %function
    .p2align 5
lb_jump:
    cpsid if
    mov r8, r0
    mov r9, r1
    L1_CLEAN_INV
    mrc p15, 0, r0, c1, c0, 0
    bic r0, r0, #0x1                   /* M */
    bic r0, r0, #0x4                   /* C */
    bic r0, r0, #0x1000                /* I */
    bic r0, r0, #0x800                 /* Z (branch prediction) */
    dsb sy
    mcr p15, 0, r0, c1, c0, 0
    isb
    mov r0, #0
    mcr p15, 0, r0, c7, c5, 0          /* ICIALLU */
    mcr p15, 0, r0, c7, c5, 6          /* BPIALL */
    mcr p15, 0, r0, c8, c7, 0          /* TLBIALL */
    dsb sy
    isb
    mov r0, #0
    mvn r1, #0
    mov r2, r9
    bx r8                              /* even address: ARM state, as Linux expects */

/* Core 1 parking loop (lb_park_loop(u32 *state)): state[0] = 1 once parked,
 * then wait in WFE for state[1] (a release address, for a later SMP bring-up).
 * Interrupts are off; the cache is cleaned first so nothing dirty of this
 * core can land on Linux memory later. */
    .global lb_park_loop
    .type lb_park_loop, %function
    .p2align 2
lb_park_loop:
    cpsid if
    mov r8, r0
    /* The stock dispatcher acknowledged SGI 1 (from core 0: IAR = 1) and never
     * gets to end it. End it here, or this CPU interface stays at the SGI's
     * running priority and a later Linux would get no interrupts on this core. */
    ldr r1, [r8, #12]                  /* GICC base */
    cmp r1, #0
    movne r0, #1
    strne r0, [r1, #0x10]              /* GICC_EOIR */
    dsb sy
    L1_CLEAN_INV
    /* The state words have a cache line of their own (lb_park_state). Linux writes
     * the release address with an uncached store, so this core must neither keep
     * a dirty copy of the line (its eviction would overwrite the address) nor read
     * a stale clean one (it would wait forever): clean+invalidate after writing,
     * invalidate before every read. The camera has caches; QEMU does not, which
     * is why the first version only worked there (2026-11-26). */
    mov r0, #1
    str r0, [r8]
    dsb sy
    mcr p15, 0, r8, c7, c14, 1         /* DCCIMVAC: "parked" to DRAM, no copy left */
    dsb sy
    sev
1:  wfe
    mcr p15, 0, r8, c7, c6, 1          /* DCIMVAC: the next read comes from DRAM */
    dsb sy
    ldr r9, [r8, #4]
    cmp r9, #0
    beq 1b
    mov r0, #2                         /* left the loop: z30-smp.c waits for this */
    str r0, [r8]
    mov r0, #0
    str r0, [r8, #4]
    dsb sy
    mcr p15, 0, r8, c7, c14, 1         /* DCCIMVAC */
    dsb sy
    /* Released: enter at r9 like a cold secondary (Linux secondary_startup_arm):
     * MMU, D-cache and I-cache off, TLB / I-cache / predictor clean, ARM state. */
    L1_CLEAN_INV
    mrc p15, 0, r0, c1, c0, 0
    bic r0, r0, #0x1
    bic r0, r0, #0x4
    bic r0, r0, #0x1000
    bic r0, r0, #0x800
    dsb sy
    mcr p15, 0, r0, c1, c0, 0
    isb
    mov r0, #0
    mcr p15, 0, r0, c7, c5, 0          /* ICIALLU */
    mcr p15, 0, r0, c7, c5, 6          /* BPIALL */
    mcr p15, 0, r0, c8, c7, 0          /* TLBIALL */
    dsb sy
    isb
    bx r9

/* SGI 1 entry installed in the stock interrupt table (slot 0xC2A1C7F0) while
 * core 1 is being parked. The stock entry 0x100527A8 is an ARM routine with its
 * own register and mode conventions, so this one touches nothing unless it
 * parks: only core 1, only while lb_park_state[2] (armed) is set, goes to
 * lb_park_loop and never returns; everything else continues in the stock entry
 * with every register as it came. */
    .global lb_park_state
    .global lb_sgi1
    .type lb_sgi1, %function
    .p2align 6                         /* a 64-byte cache line of its own */
lb_park_state:
    .word 0, 0, 0, 0                   /* parked (1, then 2 once released), release address, armed, GICC base */
    .space 48
lb_sgi1:
    push {r0, r1}
    mrc p15, 0, r0, c0, c0, 5
    and r0, r0, #3
    cmp r0, #1
    bne 9f
    adr r1, lb_park_state
    ldr r0, [r1, #8]
    cmp r0, #0
    beq 9f
    mov r0, r1
    b lb_park_loop
9:  pop {r0, r1}
    ldr pc, 8f
8:  .word 0x100527a8
