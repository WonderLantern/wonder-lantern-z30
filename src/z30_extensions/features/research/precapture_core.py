"""Experimental freestanding Thumb JPEG history core; no camera hooks.

All entry points require one serialized owner task and stable readable input.
The caller owns a word-aligned 64-byte context and three independent fixed-size
slots. Addresses are caller-provided, never inferred from stock pool sizes.
Times are exposure-start milliseconds modulo 2**32, less than 2**31 apart.
JPEG markers are a framing guard, not a decoder or a metadata contract.
This module is deliberately absent from presets and the module ABI.
"""
from ...assembly import assemble, constant

MAGIC = 0x504A5031
CONTEXT_SIZE = 64
SLOTS = 3
ENTRY_SIZE = 0x400
ENTRIES = ('init', 'arm', 'offer', 'freeze', 'peek', 'ack', 'cancel', 'discard')
OK, BAD_STATE, BAD_FRAME, TOO_LARGE, STALE, EMPTY, BAD_ARGUMENT, WRITE_FAILED = range(8)


def build_core(base):
    """Return code and entry addresses, without allocating or patching firmware.

    init(ctx, arena, bytes_per_slot, window_ms)
    arm(ctx); offer(ctx, {source, length, exposure_ms})
    freeze(ctx, full_press_ms); peek(ctx, {source, length, exposure_ms} out)
    ack(ctx, writer_status): only status zero consumes the oldest frozen frame
    cancel(ctx): rejects frozen batches; discard(ctx): explicit batch abandonment

    Slot capacity is 4..16 MiB and window is 1..60000 ms. Initialization
    validates integer range and context/arena overlap, not physical RAM safety.
    peek's pointer remains owned by the core until ack/discard; no offer is
    accepted while frozen. Output/context/arena aliasing is forbidden.
    """
    if base & 3 or not 0 <= base <= 0xFFFFFFFF - ENTRY_SIZE * len(ENTRIES):
        raise ValueError('Core needs a word-aligned, non-wrapping code range')
    guard = constant('r12', MAGIC) + '''
        ldr r3, [r0]; cmp r3, r12; bne bad_argument;
    '''
    finish = '''
    success: movs r0, #0; bx lr;
    bad_state: movs r0, #1; bx lr;
    bad_argument: movs r0, #6; bx lr;
    '''
    bodies = {}
    bodies['init'] = '''
        push {r4-r7, lr};
        tst r0, #3; bne init_bad;
        tst r1, #3; bne init_bad;
        cmp r2, #4; blo init_bad;
        cmp r2, #0x1000000; bhi init_bad;
        cmp r3, #1; blo init_bad;
        movw r4, #60000; cmp r3, r4; bhi init_bad;
        adds r4, r0, #64; bcs init_bad;
        add r5, r2, r2, lsl #1;
        adds r5, r1, r5; bcs init_bad;
        cmp r0, r5; bhs init_separate;
        cmp r1, r4; blo init_bad;
    init_separate:
        movs r4, #0; movs r5, #0;
    init_zero:
        str r4, [r0, r5]; adds r5, #4; cmp r5, #64; blo init_zero;
        str r1, [r0, #8]; str r2, [r0, #12]; str r3, [r0, #16];
    ''' + constant('r4', MAGIC) + '''
        str r4, [r0]; movs r0, #0; pop {r4-r7, pc};
    init_bad: movs r0, #6; pop {r4-r7, pc};
    '''
    bodies['arm'] = guard + '''
        ldr r2, [r0, #4]; cmp r2, #0; bne bad_state;
        movs r2, #1; str r2, [r0, #4]; b success;
    ''' + finish
    bodies['offer'] = guard + '''
        ldr r2, [r0, #4]; cmp r2, #1; bne bad_state;
        push {r4-r11, lr};
        mov r4, r0;
        ldr r5, [r1]; ldr r6, [r1, #4]; ldr r7, [r1, #8];
        cmp r6, #4; blo offer_frame;
        ldr r2, [r4, #12]; cmp r6, r2; bhi offer_large;
        adds r3, r5, r6; bcs offer_frame;
        ldr r1, [r4, #8]; add r2, r2, r2, lsl #1; add r2, r1;
        cmp r5, r2; bhs source_separate;
        cmp r3, r1; bhi offer_frame;
    source_separate:
        add r2, r4, #64; cmp r5, r2; bhs source_context_separate;
        cmp r3, r4; bhi offer_frame;
    source_context_separate:
        ldrb r1, [r5]; cmp r1, #255; bne offer_frame;
        ldrb r1, [r5, #1]; cmp r1, #216; bne offer_frame;
        ldrb r1, [r3, #-2]; cmp r1, #255; bne offer_frame;
        ldrb r1, [r3, #-1]; cmp r1, #217; bne offer_frame;
        ldr r1, [r4, #32]; cbz r1, offer_prune;
        ldr r1, [r4, #28]; subs r1, r7, r1;
        beq offer_stale; bmi offer_stale;
    offer_prune:
        ldr r8, [r4, #20]; ldr r9, [r4, #24];
    prune_loop:
        cmp r9, #0; beq offer_slot;
        add r10, r4, #40; add r10, r10, r8, lsl #3;
        ldr r1, [r10, #4]; sub r1, r7, r1;
        ldr r2, [r4, #16]; cmp r1, r2; bhi evict;
        cmp r9, #3; blo offer_slot;
    evict:
        adds r8, #1; cmp r8, #3; bne evict_no_wrap; movs r8, #0;
    evict_no_wrap:
        subs r9, #1; b prune_loop;
    offer_slot:
        add r10, r8, r9; cmp r10, #3; blo slot_no_wrap; sub r10, #3;
    slot_no_wrap:
        ldr r1, [r4, #12]; mul r1, r10, r1;
        ldr r2, [r4, #8]; add r1, r2;
        mov r2, r6;
    copy_loop:
        ldrb r3, [r5], #1; strb r3, [r1], #1;
        subs r2, #1; bne copy_loop;
        add r11, r4, #40; add r11, r11, r10, lsl #3;
        str r6, [r11]; str r7, [r11, #4];
        adds r9, #1; str r8, [r4, #20]; str r9, [r4, #24];
        str r7, [r4, #28]; movs r1, #1; str r1, [r4, #32];
        movs r0, #0; b offer_return;
    offer_frame: movs r0, #2; b offer_return;
    offer_large: movs r0, #3; b offer_return;
    offer_stale: movs r0, #4;
    offer_return: pop {r4-r11, pc};
    ''' + finish
    bodies['freeze'] = guard + '''
        ldr r2, [r0, #4]; cmp r2, #1; bne bad_state;
        push {r4-r7, lr};
        ldr r4, [r0, #20]; ldr r5, [r0, #24];
    trim_future:
        cbz r5, freeze_done;
        add r6, r4, r5; subs r6, #1;
        cmp r6, #3; it hs; subhs r6, #3;
        add r7, r0, #40; add r7, r7, r6, lsl #3;
        ldr r2, [r7, #4]; subs r2, r1, r2; bpl trim_old;
        subs r5, #1; b trim_future;
    trim_old:
        cbz r5, freeze_done;
        add r7, r0, #40; add r7, r7, r4, lsl #3;
        ldr r2, [r7, #4]; sub r2, r1, r2;
        ldr r3, [r0, #16]; cmp r2, r3; bls freeze_done;
        adds r4, #1; cmp r4, #3; it eq; moveq r4, #0;
        subs r5, #1; b trim_old;
    freeze_done:
        str r4, [r0, #20]; str r5, [r0, #24]; str r1, [r0, #36];
        movs r2, #2; str r2, [r0, #4];
        movs r0, #0; pop {r4-r7, pc};
    ''' + finish
    bodies['peek'] = guard + '''
        ldr r2, [r0, #4]; cmp r2, #2; bne bad_state;
        ldr r2, [r0, #24]; cbz r2, peek_empty;
        push {r4, lr};
        ldr r2, [r0, #20]; ldr r3, [r0, #12]; mul r3, r2, r3;
        ldr r4, [r0, #8]; add r3, r4; str r3, [r1];
        add r4, r0, #40; add r4, r4, r2, lsl #3;
        ldr r2, [r4]; str r2, [r1, #4];
        ldr r2, [r4, #4]; str r2, [r1, #8];
        movs r0, #0; pop {r4, pc};
    peek_empty: movs r0, #5; bx lr;
    ''' + finish
    bodies['ack'] = guard + '''
        ldr r2, [r0, #4]; cmp r2, #2; bne bad_state;
        cmp r1, #0; bne ack_failed;
        ldr r2, [r0, #24]; cbz r2, ack_empty;
        subs r2, #1; str r2, [r0, #24];
        ldr r3, [r0, #20]; adds r3, #1;
        cmp r3, #3; it eq; moveq r3, #0; str r3, [r0, #20];
        cmp r2, #0; bne success;
        str r2, [r0, #4]; str r2, [r0, #32]; b success;
    ack_empty: movs r0, #5; bx lr;
    ack_failed: movs r0, #7; bx lr;
    ''' + finish
    clear = '''
        movs r2, #0; str r2, [r0, #4]; str r2, [r0, #20];
        str r2, [r0, #24]; str r2, [r0, #32]; b success;
    ''' + finish
    bodies['cancel'] = guard + '''
        ldr r2, [r0, #4]; cmp r2, #2; beq bad_state;
    ''' + clear
    bodies['discard'] = guard + clear
    code = bytearray()
    entries = {}
    for name in ENTRIES:
        address = base + len(code)
        native = assemble(bodies[name], address, ENTRY_SIZE)
        entries[name] = address
        code.extend(native + bytes(ENTRY_SIZE - len(native)))
    return bytes(code), entries
