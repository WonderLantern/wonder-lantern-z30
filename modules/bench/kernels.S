/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Wonder Lantern Z30 contributors
 *
 * Benchmark kernels (Thumb-2, AAPCS). bench.c times each call with the PMU
 * cycle counter, interrupts off. Every loop does a fixed amount of work per
 * iteration so cycles per operation fall out directly.
 *
 * The VFP / NEON kernels may only run between fpu_open and fpu_close: stock
 * firmware turns CP10/CP11 off (0x100356E4), so bench.c enables them for one
 * interrupt-free chunk and puts every register back afterwards.
 */
    .syntax unified
    .thumb
    .arch armv7-a
    .fpu neon-vfpv4
    .text

#define R4(...) __VA_ARGS__; __VA_ARGS__; __VA_ARGS__; __VA_ARGS__
#define R16(...) R4(__VA_ARGS__); R4(__VA_ARGS__); R4(__VA_ARGS__); R4(__VA_ARGS__)
#define R64(...) R16(__VA_ARGS__); R16(__VA_ARGS__); R16(__VA_ARGS__); R16(__VA_ARGS__)

    .macro FUNC name
    .global \name
    .type \name, %function
    .thumb_func
    .p2align 4
\name:
    .endm

/* k_dep_add(n): n x 128 dependent adds (latency). */
FUNC k_dep_add
    movs r1, #0
1:  R64(add r1, r1, #1)
    R64(add r1, r1, #1)
    subs r0, r0, #1
    bne 1b
    mov r0, r1
    bx lr

/* k_ind_add(n): n x 128 adds in four independent chains (throughput). */
FUNC k_ind_add
    movs r1, #0
    movs r2, #0
    movs r3, #0
    mov r12, #0
1:  R16(add r1, r1, #1; add r2, r2, #1; add r3, r3, #1; add r12, r12, #1)
    R16(add r1, r1, #1; add r2, r2, #1; add r3, r3, #1; add r12, r12, #1)
    subs r0, r0, #1
    bne 1b
    add r0, r1, r2
    bx lr

/* k_dep_mul(n): n x 64 dependent multiplies. */
FUNC k_dep_mul
    movs r1, #3
    movs r2, #5
1:  R64(mul r1, r1, r2)
    subs r0, r0, #1
    bne 1b
    mov r0, r1
    bx lr

/* k_ind_mul(n): n x 64 multiplies in four chains. */
FUNC k_ind_mul
    push {r4, lr}
    movs r1, #3
    movs r2, #5
    movs r3, #7
    movs r4, #9
    mov r12, #11
1:  R16(mul r1, r1, r12; mul r2, r2, r12; mul r3, r3, r12; mul r4, r4, r12)
    subs r0, r0, #1
    bne 1b
    add r0, r1, r2
    pop {r4, pc}

/* k_chase(p, n): n x 16 dependent loads p = *(p + 4); returns p. */
FUNC k_chase
1:  R16(ldr r0, [r0, #4])
    subs r1, r1, #1
    bne 1b
    bx lr

/* k_read(p, bytes): load bytes (a multiple of 128) with ldm. */
FUNC k_read
    push {r4-r7, lr}
1:  ldmia r0!, {r2-r7, r12, lr}
    ldmia r0!, {r2-r7, r12, lr}
    ldmia r0!, {r2-r7, r12, lr}
    ldmia r0!, {r2-r7, r12, lr}
    subs r1, r1, #128
    bne 1b
    pop {r4-r7, pc}

/* k_write(p, bytes): store bytes (a multiple of 128) with stm. */
FUNC k_write
    push {r4-r7, lr}
    movs r2, #0
    movs r3, #0
    movs r4, #0
    movs r5, #0
    movs r6, #0
    movs r7, #0
    mov r12, #0
    mov lr, #0
1:  stmia r0!, {r2-r7, r12, lr}
    stmia r0!, {r2-r7, r12, lr}
    stmia r0!, {r2-r7, r12, lr}
    stmia r0!, {r2-r7, r12, lr}
    subs r1, r1, #128
    bne 1b
    pop {r4-r7, pc}

/* k_copy(dst, src, bytes): copy bytes (a multiple of 64) with ldm / stm. */
FUNC k_copy
    push {r4-r8, lr}
1:  ldmia r1!, {r3-r8, r12, lr}
    stmia r0!, {r3-r8, r12, lr}
    ldmia r1!, {r3-r8, r12, lr}
    stmia r0!, {r3-r8, r12, lr}
    subs r2, r2, #64
    bne 1b
    pop {r4-r8, pc}

/* ---- FPU / NEON ------------------------------------------------------- */

/* fpu_open(save): r0 -> struct fpu_save {cpacr, fpexc, fpscr, d32, ok, pad[3], d[32]}
 * (8-byte aligned). Enables CP10/CP11 full access and FPEXC.EN, saves FPSCR and
 * every D register. Returns 1, or 0 with nothing changed when CPACR refuses
 * (non-secure access denied, or no FPU). Interrupts must be off. */
FUNC fpu_open
    mrc p15, #0, r1, c1, c0, #2
    str r1, [r0]
    orr r2, r1, #0x00f00000
    bic r2, r2, #0xc0000000          /* clear ASEDIS and D32DIS */
    mcr p15, #0, r2, c1, c0, #2
    isb sy
    mrc p15, #0, r3, c1, c0, #2
    and r3, r3, #0x00f00000
    cmp r3, #0x00f00000
    bne 9f
    vmrs r2, fpexc
    str r2, [r0, #4]
    orr r2, r2, #0x40000000
    vmsr fpexc, r2
    isb sy
    vmrs r2, fpscr
    str r2, [r0, #8]
    add r3, r0, #32
    vstmia r3!, {d0-d15}
    ldr r2, [r0, #12]
    cbz r2, 2f
    vstmia r3!, {d16-d31}
2:  movs r2, #1
    str r2, [r0, #16]
    movs r0, #1
    bx lr
9:  mcr p15, #0, r1, c1, c0, #2      /* put CPACR back */
    isb sy
    movs r2, #0
    str r2, [r0, #16]
    movs r0, #0
    bx lr

/* fpu_close(save): restore the D registers, FPSCR, FPEXC and CPACR. */
FUNC fpu_close
    ldr r1, [r0, #16]
    cbz r1, 9f
    add r3, r0, #32
    vldmia r3!, {d0-d15}
    ldr r2, [r0, #12]
    cbz r2, 2f
    vldmia r3!, {d16-d31}
2:  ldr r2, [r0, #8]
    vmsr fpscr, r2
    ldr r2, [r0, #4]
    vmsr fpexc, r2
    ldr r2, [r0]
    mcr p15, #0, r2, c1, c0, #2
    isb sy
    movs r1, #0
    str r1, [r0, #16]
9:  bx lr

/* fpu_ids(out): out[0] FPSID, out[1] MVFR0, out[2] MVFR1 (only between open and close). */
FUNC fpu_ids
    vmrs r1, fpsid
    str r1, [r0]
    vmrs r1, mvfr0
    str r1, [r0, #4]
    vmrs r1, mvfr1
    str r1, [r0, #8]
    bx lr

/* k_vfp32(n): n x 32 vmla.f32 in 8 chains = 64 flops per iteration. */
FUNC k_vfp32
    vmov.f32 s16, #1.0
    vmov.f32 s17, #0.5
    movs r1, #0
    vmov s0, r1
    vmov.f32 s1, s0
    vmov.f32 s2, s0
    vmov.f32 s3, s0
    vmov.f32 s4, s0
    vmov.f32 s5, s0
    vmov.f32 s6, s0
    vmov.f32 s7, s0
1:  R4(vmla.f32 s0, s16, s17; vmla.f32 s1, s16, s17; vmla.f32 s2, s16, s17; vmla.f32 s3, s16, s17; \
       vmla.f32 s4, s16, s17; vmla.f32 s5, s16, s17; vmla.f32 s6, s16, s17; vmla.f32 s7, s16, s17)
    subs r0, r0, #1
    bne 1b
    vmov r0, s0
    bx lr

/* k_vfp64(n): n x 32 vmla.f64 in 8 chains = 64 flops per iteration. */
FUNC k_vfp64
    vmov.f64 d8, #1.0
    vmov.f64 d9, #0.5
    movs r1, #0
    vmov d0, r1, r1
    vmov.f64 d1, d0
    vmov.f64 d2, d0
    vmov.f64 d3, d0
    vmov.f64 d4, d0
    vmov.f64 d5, d0
    vmov.f64 d6, d0
    vmov.f64 d7, d0
1:  R4(vmla.f64 d0, d8, d9; vmla.f64 d1, d8, d9; vmla.f64 d2, d8, d9; vmla.f64 d3, d8, d9; \
       vmla.f64 d4, d8, d9; vmla.f64 d5, d8, d9; vmla.f64 d6, d8, d9; vmla.f64 d7, d8, d9)
    subs r0, r0, #1
    bne 1b
    vmov r0, r1, d0
    bx lr

/* k_neon32(n): n x 32 vmla.f32 on q registers, 8 chains = 256 flops per iteration. */
FUNC k_neon32
    vmov.f32 q8, #1.0
    vmov.f32 q9, #0.5
    vmov.i32 q0, #0
    vmov.i32 q1, #0
    vmov.i32 q2, #0
    vmov.i32 q3, #0
    vmov.i32 q4, #0
    vmov.i32 q5, #0
    vmov.i32 q6, #0
    vmov.i32 q7, #0
1:  R4(vmla.f32 q0, q8, q9; vmla.f32 q1, q8, q9; vmla.f32 q2, q8, q9; vmla.f32 q3, q8, q9; \
       vmla.f32 q4, q8, q9; vmla.f32 q5, q8, q9; vmla.f32 q6, q8, q9; vmla.f32 q7, q8, q9)
    subs r0, r0, #1
    bne 1b
    vmov r0, s0
    bx lr

/* k_neonfma(n): as k_neon32 with fused vfma.f32 (VFPv4 / SIMDv2 only). */
FUNC k_neonfma
    vmov.f32 q8, #1.0
    vmov.f32 q9, #0.5
    vmov.i32 q0, #0
    vmov.i32 q1, #0
    vmov.i32 q2, #0
    vmov.i32 q3, #0
    vmov.i32 q4, #0
    vmov.i32 q5, #0
    vmov.i32 q6, #0
    vmov.i32 q7, #0
1:  R4(vfma.f32 q0, q8, q9; vfma.f32 q1, q8, q9; vfma.f32 q2, q8, q9; vfma.f32 q3, q8, q9; \
       vfma.f32 q4, q8, q9; vfma.f32 q5, q8, q9; vfma.f32 q6, q8, q9; vfma.f32 q7, q8, q9)
    subs r0, r0, #1
    bne 1b
    vmov r0, s0
    bx lr

/* k_neon16(n): n x 32 vmla.i16 on q registers = 32 x 16 = 512 integer ops per iteration. */
FUNC k_neon16
    vmov.i16 q8, #1
    vmov.i16 q9, #3
    vmov.i32 q0, #0
    vmov.i32 q1, #0
    vmov.i32 q2, #0
    vmov.i32 q3, #0
    vmov.i32 q4, #0
    vmov.i32 q5, #0
    vmov.i32 q6, #0
    vmov.i32 q7, #0
1:  R4(vmla.i16 q0, q8, q9; vmla.i16 q1, q8, q9; vmla.i16 q2, q8, q9; vmla.i16 q3, q8, q9; \
       vmla.i16 q4, q8, q9; vmla.i16 q5, q8, q9; vmla.i16 q6, q8, q9; vmla.i16 q7, q8, q9)
    subs r0, r0, #1
    bne 1b
    vmov r0, s0
    bx lr

/* k_nread(p, bytes): NEON loads, 128 bytes per iteration (p 16-byte aligned). */
FUNC k_nread
1:  vld1.64 {d0-d3}, [r0:128]!
    vld1.64 {d4-d7}, [r0:128]!
    vld1.64 {d16-d19}, [r0:128]!
    vld1.64 {d20-d23}, [r0:128]!
    subs r1, r1, #128
    bne 1b
    bx lr

/* k_nwrite(p, bytes): NEON stores, 128 bytes per iteration. */
FUNC k_nwrite
    vmov.i32 q0, #0
    vmov.i32 q1, #0
1:  vst1.64 {d0-d3}, [r0:128]!
    vst1.64 {d0-d3}, [r0:128]!
    vst1.64 {d0-d3}, [r0:128]!
    vst1.64 {d0-d3}, [r0:128]!
    subs r1, r1, #128
    bne 1b
    bx lr

/* k_ncopy(dst, src, bytes): NEON copy, 64 bytes per iteration. */
FUNC k_ncopy
1:  vld1.64 {d0-d3}, [r1:128]!
    vld1.64 {d4-d7}, [r1:128]!
    vst1.64 {d0-d3}, [r0:128]!
    vst1.64 {d4-d7}, [r0:128]!
    subs r2, r2, #64
    bne 1b
    bx lr

/* ---- security state probe ------------------------------------------- */
/* ARM mode: exceptions are taken in ARM state (SCTLR.TE = 0, checked by bench.c).
 *
 * sec_probe(vbar, out): with IRQ and FIQ off, point VBAR at a temporary table
 * whose Undefined Instruction entry is sec_undef, then
 *   out[0] = 1 if UDF was caught (self-test of the catcher),
 *   out[1] = SCR, out[2] = 1 if reading SCR was undefined (non-secure state),
 * and put VBAR back. Nothing else in the table is expected to fire. */
    .arm
    .global sec_probe
    .type sec_probe, %function
    .p2align 2
sec_probe:
    push {r4-r6, lr}
    mov r5, r1
    mrs r4, cpsr
    cpsid if
    mrc p15, #0, r6, c12, c0, #0       /* old VBAR */
    mcr p15, #0, r0, c12, c0, #0
    isb sy
    mov r12, #0
    udf #0                              /* must be caught */
    str r12, [r5]
    mov r12, #0
    mov r0, #0
    mrc p15, #0, r0, c1, c1, #0         /* SCR: undefined in the non-secure world */
    str r0, [r5, #4]
    str r12, [r5, #8]
    mcr p15, #0, r6, c12, c0, #0
    isb sy
    msr cpsr_c, r4
    pop {r4-r6, pc}

/* Undefined Instruction handler (Undefined mode): flag in r12 (not banked) and
 * return to the instruction after the faulting ARM instruction (LR = it + 4). */
    .global sec_undef
    .type sec_undef, %function
sec_undef:
    mov r12, #1
    movs pc, lr

/* Any other exception during the probe: stop here (none is expected). */
    .global sec_hang
    .type sec_hang, %function
sec_hang:
    b sec_hang
    .thumb
