# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""RAM ownership and serialization around the offline SD loader component.

The caller supplies a writable, initialized 48-byte session and a valid path.
Entry +0 loads/reloads; entry +4 resets to the embedded fallback. These are
component entry points, not installed into a firmware menu/task. Heap and file
services still require a real-device context and memory-availability test.
"""
import struct
from ...assembly import assemble, invoke
from . import core as lut_core, sdload as lut_sdload

ALLOCATE, RELEASE = 0x10211F4C, 0x10211F5E
SESSION_BYTES = 48
OWNED, PENDING_INPUT, LOCK, FALLBACK, RETIRED, FALLBACK_LENGTH, COMMITTED = 16, 20, 24, 28, 32, 36, 40
PENDING_FD = 44


def build_session(fallback, length, flag=lut_core.LUT_FLAG):
    if fallback & 1 or fallback <= 0 or length < 22 or fallback + length > 0x100000000:
        raise ValueError('Invalid fallback payload range')
    return struct.pack('<12I', flag, fallback, 0, length, 0, 0, 0,
                       fallback, 0, length, 0, 0xFFFFFFFF)


def session_source(read_capacity=64 * 1024, max_read_capacity=lut_sdload.READ_BUF_SIZE):
    """load(session, path), reset(session); both preserve AAPCS saved registers.

    Status 0..4 follows sd_load, 5 allocation failure, 6 busy, 7 retained
    resource without a new commit, 8 committed successfully but cleanup is
    pending. +40 distinguishes a commit from failure. Failed frees retain
    ownership in +20/+32; a failed close retains its fd in +44 (-1 means none).
    Pending resources block more allocation until released. A lock at
    +24 must also guard the *entire* pixel-processing call.
    Input starts small and doubles to max_read_capacity on capacity error,
    closing the file and freeing the previous input before retrying.
    """
    if not 22 <= read_capacity <= max_read_capacity <= lut_sdload.READ_BUF_SIZE:
        raise ValueError('Read capacity outside the supported bound')
    return (
        'b.w session_load; b.w session_reset;'
        'session_load: push {r4-r11, lr}; sub sp, #44;'
        'mov r4, r0; mov r5, r1; bl session_acquire; cmp r0, #0; beq session_busy;'
        'movs r0, #0; str r0, [r4, #40]; movs r6, #0; movs r7, #0;'
        'bl session_drain; cmp r0, #0; bne session_pending;'
        f'ldr r10, ={read_capacity}; mov r0, r10; movs r1, #0;' + invoke(ALLOCATE) +
        'mov r6, r0; cmp r6, #0; beq session_no_memory;'
        f'ldr r0, ={lut_sdload.SD_SLOT_SIZE}; movs r1, #0;' + invoke(ALLOCATE) +
        'mov r7, r0; cmp r7, #0; beq session_no_memory;'
        'session_read_again: str r6, [sp];'
        'str r10, [sp, #4]; str r7, [sp, #8];'
        f'ldr r0, ={lut_sdload.SD_SLOT_SIZE}; str r0, [sp, #12]; str r5, [sp, #16];'
        'ldr r9, [r4, #16]; mov r0, r4; mov r1, sp; bl sd_load; mov r8, r0; str r1, [r4, #44];'
        f'cmp r8, #4; bne session_loaded; ldr r0, ={max_read_capacity}; cmp r10, r0; bhs session_loaded;'
        # Grow only after closing the file, releasing the previous input first.
        'mov r0, r6;' + invoke(RELEASE) +
        'cmp r0, #0; beq session_grow; str r6, [r4, #20]; movs r6, #0;'
        'movs r8, #7; b session_free_candidate;'
        'session_grow: movs r6, #0; lsls r10, r10, #1;'
        f'ldr r0, ={max_read_capacity}; cmp r10, r0; bls session_grow_allocate; mov r10, r0;'
        'session_grow_allocate: mov r0, r10; movs r1, #0;' + invoke(ALLOCATE) +
        'mov r6, r0; cmp r6, #0; beq session_no_memory; b session_read_again;'
        'session_loaded: '
        'cmp r8, #0; bne session_free_candidate;'
        'str r7, [r4, #16]; movs r0, #1; str r0, [r4, #40];'
        'mov r7, r9;'
        'session_free_candidate: cmp r7, #0; beq session_free_input;'
        'mov r0, r7;' + invoke(RELEASE) +
        'cmp r0, #0; beq session_free_input; str r7, [r4, #32]; bl session_cleanup_status;'
        'session_free_input: cmp r6, #0; beq session_finish;'
        'mov r0, r6;' + invoke(RELEASE) +
        'cmp r0, #0; beq session_finish; str r6, [r4, #20]; bl session_cleanup_status;'
        'b session_finish;'
        'session_no_memory: movs r8, #5; b session_free_candidate;'
        'session_pending: movs r8, #7; b session_finish;'
        'session_reset: push {r4-r11, lr}; sub sp, #44; mov r4, r0;'
        'bl session_acquire; cmp r0, #0; beq session_busy;'
        'movs r0, #0; str r0, [r4, #40]; bl session_drain; cmp r0, #0; bne session_pending;'
        'ldr r7, [r4, #16]; ldr r0, [r4, #28]; ldr r1, [r4, #36];'
        'dmb sy; str r0, [r4, #4]; str r1, [r4, #12];'
        'movs r0, #0; str r0, [r4, #16]; movs r0, #1; str r0, [r4, #40];'
        'movs r6, #0; movs r8, #0; b session_free_candidate;'
        'session_finish: str r8, [r4, #8]; dmb sy; movs r1, #0; str r1, [r4, #24];'
        'mov r0, r8; add sp, #44; pop {r4-r11, pc};'
        # Busy is observational: never overwrite the owner's status or lock.
        'session_busy: movs r0, #6; add sp, #44; pop {r4-r11, pc};'
        'session_cleanup_status: ldr r0, [r4, #40]; movs r8, #7;'
        'cmp r0, #0; beq session_cleanup_done; movs r8, #8;'
        'session_cleanup_done: bx lr;'
        'session_acquire: add r0, r4, #24; movs r12, #8;'
        'session_lock_loop: ldrex r1, [r0]; cmp r1, #0; bne session_lock_fail;'
        'movs r2, #1; strex r3, r2, [r0]; cmp r3, #0; beq session_lock_ok;'
        'subs r12, #1; bne session_lock_loop;'
        'session_lock_fail: clrex; movs r0, #0; bx lr;'
        'session_lock_ok: dmb sy; movs r0, #1; bx lr;'
        'session_drain: push {r5-r7, lr}; ldr r0, [r4, #44]; cmp r0, #0; blt session_drain_memory;'
        + invoke(lut_sdload.FS_CLOSE) +
        'cmp r0, #0; bne session_drain_bad; mov.w r0, #-1; str r0, [r4, #44];'
        'session_drain_memory: movs r5, #20;'
        'session_drain_loop: ldr r6, [r4, r5]; cmp r6, #0; beq session_drain_next;'
        'mov r0, r6;' + invoke(RELEASE) +
        'cmp r0, #0; bne session_drain_bad; str r0, [r4, r5];'
        'session_drain_next: cmp r5, #32; beq session_drain_done; movs r5, #32; b session_drain_loop;'
        'session_drain_done: movs r0, #0; pop {r5-r7, pc};'
        'session_drain_bad: movs r0, #7; pop {r5-r7, pc};'
        + lut_sdload.sd_load_source())


def build_manager(address, read_capacity=64 * 1024, max_read_capacity=lut_sdload.READ_BUF_SIZE):
    return assemble(session_source(read_capacity, max_read_capacity), address, 0x4000)
