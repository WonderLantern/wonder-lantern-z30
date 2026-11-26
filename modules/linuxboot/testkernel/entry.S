/* SPDX-License-Identifier: MIT
 * Test kernel entry: entered like an ARM Linux zImage (ARM state, SVC, MMU and
 * caches off, interrupts off) with r0 = 0, r1 = ~0, r2 = DTB. Position independent. */
    .syntax unified
    .arch armv7-a
    .arm
    .section .text.entry, "ax"
    .global _start
_start:
    cpsid if
    adr r4, _start
    ldr r5, 1f
    add sp, r4, r5                     /* stack at the end of the image (.bss) */
    mov r0, r2
    blx tk_main
2:  wfi
    b 2b
1:  .word tk_stack_top - _start
