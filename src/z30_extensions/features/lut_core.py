# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""Thumb-2 LUT core for the stock index-6 effect slot, assembled via Keystone.

One assembly unit: the gate stub (patched over the stock index-6 processing
entry 0x1025FB8E), lut_core and lut_lookup. Flagged jobs run
lut_core(params): it reads the effect input and output image descriptors
through the params object, invalidates the input planes through the stock
cache service, converts every pixel of format 1 (4:2:2 interleaved chroma)
YCbCr -> RGB, runs the packed trilinear LUT, converts back, averages the
chroma pair, cleans the output planes and returns 1/0 exactly like the stock
processing function.

Register plan inside lut_core: r4 params, r5/r6 input row Y/C, r7 LUT block
(read by lut_lookup), r8 x cursor, r9 pairs left, r10/r11 output row Y/C.
Everything else lives in the stack frame. The integer contract lives in
z30_extensions.lut (*_int functions); tools/verify_lut_core_native.py proves
per-pixel parity in Unicorn before any firmware is produced.
"""
import struct

from ..assembly import assemble, invoke

LUT_FLAG = 0x574C5554          # 'WLUT'
BLOCK_HEADER = 8               # flag word + reserved word before the payload
STOCK_PROCESSING = 0x1025FB8E  # stock index-6 processing entry
DISPLACED_CALL = 0x1025FB00   # stock validator bl displaced by the gate window
CACHE_INVALIDATE = 0x10204B48  # stock d-cache invalidate by region
CACHE_CLEAN = 0x10204B0C       # stock d-cache clean by region

CORE_FRAME = 0x70
S_YIN, S_CIN, S_YOUT, S_COUT = 0x00, 0x04, 0x08, 0x0C
S_YSIN, S_CSIN, S_YSOUT, S_CSOUT = 0x10, 0x12, 0x14, 0x16
S_W, S_H, S_X0, S_Y0 = 0x18, 0x1A, 0x1C, 0x1E
S_Y, S_YEND = 0x30, 0x34
S_CBSUM, S_CRSUM, S_TAIL = 0x38, 0x3C, 0x40
S_CB, S_CR = 0x44, 0x48
S_CRR, S_CBG, S_CBB, S_CRG = 0x4C, 0x50, 0x54, 0x58
S_RY, S_RCB, S_RCR = 0x5C, 0x60, 0x64

# SIMD frame: the scalar slots above plus per-frame LUT constants, the
# per-pixel axis results and the nine reverse-conversion coefficients.
CORE_FRAME_SIMD = 0xE0
F_TBASE, F_SY6, F_SZ6, F_S1 = 0x68, 0x6C, 0x70, 0x74
F_MINR, F_MING, F_MINB = 0x78, 0x7C, 0x80
F_MAXR, F_MAXG, F_MAXB = 0x84, 0x88, 0x8C
F_SPANR, F_SPANG, F_SPANB = 0x90, 0x94, 0x98
F_BXR, F_BYG, F_BZB = 0x9C, 0xA0, 0xA4
F_YCR, F_YCG, F_YCB = 0xA8, 0xAC, 0xB0
F_CBCR, F_CBCG, F_CBCCB = 0xB4, 0xB8, 0xBC
F_CRCR, F_CRCG, F_CRCB = 0xC0, 0xC4, 0xC8
F_RGBG, F_RGBB = 0xCC, 0xD0

LOOKUP_FRAME = 0x40
L_BASE_R, L_BASE_G, L_BASE_B = 0, 4, 8
L_FR_R_LO, L_FR_R_HI = 12, 16
L_FR_G_LO, L_FR_G_HI = 20, 24
L_FR_B_LO, L_FR_B_HI = 28, 32
L_STRIDE_Y, L_STRIDE_Z, L_BASEOFF = 36, 40, 44
L_ACC, L_VAL_R, L_VAL_G, L_VAL_B = 48, 52, 56, 60

FWD_Y_SCALE = 1193
FWD_CR_R, FWD_CB_G, FWD_CR_G, FWD_CB_B = 359, 88, 183, 454
REV = dict(y=(4208, 8262, 1604, 16, 235), cb=(-2428, -4768, 7196, 16, 240),
           cr=(7196, -6026, -1170, 16, 240))


def build_block(binary, flag=LUT_FLAG):
    """Flag word + reserved word + packed WL3L payload."""
    return struct.pack('<II', flag, 0) + bytes(binary)


def _call(reg, address):
    return f'movw {reg}, #{address & 0xFFFF}; movt {reg}, #{address >> 16}; blx {reg};'


def _clamp(reg, lo, hi, tag):
    return (f'cmp {reg}, #{lo}; bge {tag}_lo; movs {reg}, #{lo}; b {tag}_done;'
            f'{tag}_lo: cmp {reg}, #{hi}; ble {tag}_done; movs {reg}, #{hi}; {tag}_done:')


def _axis(value_slot, min_off, max_off, base_slot, flo_slot, fhi_slot, tag):
    """Map one 8-bit sample (frame slot) onto LUT node position.

    r0/r1/r3/r12 scratch; r7 holds the block pointer. Bit-for-bit match with
    lut.trilinear_int: coord = sample*257 clamped into [min,max]; t =
    (coord-min)<<16 / span; node = t*(size-1)>>16 clamped to size-2; both
    weights clamped into u16 so every corner product stays inside 32 bits.
    Stores node and 65536-frac / frac.
    """
    return (f'ldr r0, [sp, #{value_slot}];'
            f'ldrh r3, [r7, #{min_off}];'
            f'ldrh r12, [r7, #{max_off}];'
            f'movw r1, #257; mul r0, r0, r1;'
            f'cmp r0, r3; bhs {tag}_hi;'
            f'mov r0, r3; b {tag}_clamped;'
            f'{tag}_hi: cmp r0, r12; blo {tag}_clamped;'
            f'mov r0, r12;'
            f'{tag}_clamped: subs r0, r0, r3;'
            f'subs r1, r12, r3;'
            f'lsls r0, r0, #16;'
            f'udiv r0, r0, r1;'
            f'ldrh r1, [r7, #14]; subs r1, #1;'
            f'mul r0, r0, r1;'
            f'ubfx r2, r0, #16, #16;'
            f'subs r1, #1;'
            f'cmp r2, r1; bls {tag}_base_ok;'
            f'mov r2, r1; movw r0, #65535; b {tag}_weight;'
            f'{tag}_base_ok: ubfx r0, r0, #0, #16;'
            f'{tag}_weight: str r2, [sp, #{base_slot}];'
            f'cmp r0, #0; bne {tag}_wlo;'
            f'movw r1, #65535; b {tag}_store;'
            f'{tag}_wlo: mov r1, #1; lsls r1, r1, #16; subs r1, r1, r0;'
            f'{tag}_store: str r1, [sp, #{flo_slot}];'
            f'str r0, [sp, #{fhi_slot}];')


def _corner(index, channel_offset):
    """One trilinear corner accumulation for the channel at channel_offset."""
    dx, dy, dz = index & 1, (index >> 1) & 1, (index >> 2) & 1
    address = ''
    if dx:
        address += 'adds r0, #6;'
    if dy:
        address += f'ldr r1, [sp, #{L_STRIDE_Y}]; adds r0, r0, r1;'
    if dz:
        address += f'ldr r1, [sp, #{L_STRIDE_Z}]; adds r0, r0, r1;'
    if channel_offset:
        address += f'adds r0, #{channel_offset};'
    return (f'ldr r3, [sp, #{L_FR_R_LO + 4 * dx}];'
            f'ldr r12, [sp, #{L_FR_G_LO + 4 * dy}];'
            f'mul r3, r3, r12; lsrs r3, r3, #16;'
            f'ldr r12, [sp, #{L_FR_B_LO + 4 * dz}];'
            f'mul r3, r3, r12; lsrs r3, r3, #16;'
            f'ldr r0, [sp, #{L_BASEOFF}];'
            f'{address}'
            f'ldrh r1, [r4, r0];'
            f'mul r1, r1, r3; lsrs r1, r1, #16;'
            f'ldr r0, [sp, #{L_ACC}]; adds r0, r0, r1; str r0, [sp, #{L_ACC}];')


def _channel(channel_offset, result_slot):
    body = f'movs r0, #0; str r0, [sp, #{L_ACC}];'
    body += ''.join(_corner(index, channel_offset) for index in range(8))
    body += f'ldr r0, [sp, #{L_ACC}]; lsrs r0, r0, #8; str r0, [sp, #{result_slot}];'
    return body


def _lut_lookup():
    """lut_lookup: inputs stashed at L_VAL_R/G/B; r7 = block.

    Returns r0/r1/r2 = 8-bit RGB out. Preserves r4-r11; clobbers r0-r3, r12.
    """
    return (f'lut_lookup: push {{r4-r6, lr}}; sub sp, #{LOOKUP_FRAME};'
            f'str r0, [sp, #{L_VAL_R}]; str r1, [sp, #{L_VAL_G}]; str r2, [sp, #{L_VAL_B}];'
            + _axis(L_VAL_R, 18, 24, L_BASE_R, L_FR_R_LO, L_FR_R_HI, 'axr')
            + _axis(L_VAL_G, 20, 26, L_BASE_G, L_FR_G_LO, L_FR_G_HI, 'axg')
            + _axis(L_VAL_B, 22, 28, L_BASE_B, L_FR_B_LO, L_FR_B_HI, 'axb')
            + f'ldrh r2, [r7, #14];'
            f'lsls r3, r2, #2; lsls r1, r2, #1; adds r3, r3, r1;'
            f'str r3, [sp, #{L_STRIDE_Y}];'
            f'mul r12, r2, r3; str r12, [sp, #{L_STRIDE_Z}];'
            f'ldr r0, [sp, #{L_BASE_R}];'
            f'lsls r0, r0, #2; ldr r1, [sp, #{L_BASE_R}]; adds r0, r0, r1; adds r0, r0, r1;'
            f'ldr r1, [sp, #{L_BASE_G}]; mla r0, r1, r3, r0;'
            f'ldr r1, [sp, #{L_BASE_B}]; mla r0, r1, r12, r0;'
            f'str r0, [sp, #{L_BASEOFF}];'
            f'adds r4, r7, #30;'
            + _channel(0, L_VAL_R)
            + _channel(2, L_VAL_G)
            + _channel(4, L_VAL_B)
            + f'ldr r0, [sp, #{L_VAL_R}]; ldr r1, [sp, #{L_VAL_G}]; ldr r2, [sp, #{L_VAL_B}];'
            f'add sp, #{LOOKUP_FRAME}; pop {{r4-r6, pc}};')


def _reverse_channel(coeffs, offset, lo, hi, slot, tag):
    """One limited-range output channel into a frame slot (lut.rgb_to_ycbcr_int).

    Inputs r0/r1/r2 = 8-bit R/G/B survive all three calls; scratch r3/r12.
    """
    packed = [c & 0xFFFFFFFF for c in coeffs]
    return (f'movw r3, #{packed[0] & 0xFFFF}; movt r3, #{packed[0] >> 16};'
            f'mul r3, r0, r3;'
            f'movw r12, #{packed[1] & 0xFFFF}; movt r12, #{packed[1] >> 16};'
            f'mla r3, r1, r12, r3;'
            f'movw r12, #{packed[2] & 0xFFFF}; movt r12, #{packed[2] >> 16};'
            f'mla r3, r2, r12, r3;'
            f'adds r3, #8192; asrs r3, r3, #14; adds r3, #{offset};'
            + _clamp('r3', lo, hi, tag)
            + f'str r3, [sp, #{slot}];')


def _rev_frame(coef_slot, offset, lo, hi, slot, tag):
    """SIMD-mode reverse channel; the three Q2.14 coefficients live in the frame."""
    return (f'ldr r3, [sp, #{coef_slot}];'
            f'mul r3, r0, r3;'
            f'ldr r12, [sp, #{coef_slot + 4}];'
            f'mla r3, r1, r12, r3;'
            f'ldr r12, [sp, #{coef_slot + 8}];'
            f'mla r3, r2, r12, r3;'
            f'adds r3, #8192; asrs r3, r3, #14; adds r3, #{offset};'
            + _clamp('r3', lo, hi, tag)
            + f'str r3, [sp, #{slot}];')


def _simd_axis(sample_reg, min_slot, max_slot, span_slot, base_slot, tag):
    """One scalar axis mapping for the SIMD helper; r0 = working, r1/r2/r3/r12 scratch.

    Matches lut.trilinear_int axis(): coord = clamp(sample*257, min, max);
    t = (coord-min)<<16/span (udiv); pos = t*(size-1)>>16; base = min(pos,
    size-2); frac clamped so both weights stay inside u16 (see lut.py).
    Leaves frac in r0 and 65536-frac in r1.
    """
    return (f'mul r0, {sample_reg}, r4;'
            f'ldr r1, [sp, #{max_slot}]; cmp r0, r1; bls {tag}_hi;'
            f'mov r0, r1; {tag}_hi:'
            f'ldr r1, [sp, #{min_slot}]; cmp r0, r1; bhs {tag}_lo;'
            f'mov r0, r1; {tag}_lo:'
            f'subs r0, r0, r1;'
            f'ldr r1, [sp, #{span_slot}]; lsls r0, r0, #16; udiv r0, r0, r1;'
            f'mul r1, r0, r12;'
            f'ubfx r2, r1, #16, #16;'
            f'subs r3, r12, #1;'
            f'cmp r2, r3; bls {tag}_pos_ok;'
            f'mov r2, r3; movw r0, #65535; b {tag}_have;'
            f'{tag}_pos_ok: ubfx r0, r1, #0, #16;'
            f'{tag}_have: str r2, [sp, #{base_slot}];'
            f'cmp r0, #0; bne {tag}_wlo;'
            f'movw r1, #65535; b {tag}_done;'
            f'{tag}_wlo: mov r1, #1; lsls r1, r1, #16; subs r1, r1, r0;'
            f'{tag}_done:')


def _pixel_simd(tag):
    """lut_pix: r0/r1/r2 = 8-bit R/G/B in, same out after the packed trilinear LUT.

    Leaf that runs at the caller's sp and uses the shared SIMD frame slots;
    clobbers r0-r3, r7, r12 and d0-d15. r4 must hold 257. The trilinear part
    is NEON: VLD3 lane gathers pull each corner's RGB triplet, VMULL.U16
    applies the u16 corner weights and VPADD reduces the eight products,
    matching lut.trilinear_int term for term.
    """
    def channel(dz0, dz1, out, step):
        # vmull+shr per dz group gives each corner's (value*weight)>>16; the
        # pairwise add and scalar sum then total all eight truncated terms.
        return (f'vmull.u16 q0, {dz0}, d14; vshr.u32 q0, q0, #16;'
                f'vmull.u16 q1, {dz1}, d15; vshr.u32 q1, q1, #16;'
                f'vadd.i32 q0, q0, q1;'
                f'vpadd.u32 d2, d0, d1;'
                f'vmov.32 {out}, d2[0]; vmov.32 r3, d2[1]; add {out}, {out}, r3;'
                f'lsrs {out}, {out}, #8; cmp {out}, #255; bls {tag}{step}_u8;'
                f'movs {out}, #255; {tag}{step}_u8:')
    return (f'lut_pix: '
            # The green/blue samples live in frame slots: each axis rebuilds
            # its working registers, so r1/r2 would not survive the red axis.
            f'str r1, [sp, #{F_RGBG}]; str r2, [sp, #{F_RGBB}];'
            f'ldr r12, [sp, #{F_S1}];'
            # Red axis: d0 = {wlo, frac, wlo, frac}.
            + _simd_axis('r0', F_MINR, F_MAXR, F_SPANR, F_BXR, tag + 'r')
            + f'vdup.16 d0, r1; vmov.16 d0[1], r0; vmov.16 d0[3], r0;'
            # Green axis: d1 = {wlo, wlo, frac, frac}.
            + f'ldr r1, [sp, #{F_RGBG}];'
            + _simd_axis('r1', F_MING, F_MAXG, F_SPANG, F_BYG, tag + 'g')
            + f'vdup.16 d1, r1; vmov.16 d1[2], r0; vmov.16 d1[3], r0;'
            # Blue axis keeps frac in r0 and 65536-frac in r1 for the weights.
            + f'ldr r2, [sp, #{F_RGBB}];'
            + _simd_axis('r2', F_MINB, F_MAXB, F_SPANB, F_BZB, tag + 'b')
            + f'vmull.u16 q2, d0, d1; vshr.u32 q2, q2, #16; vmovn.u32 d2, q2;'
            f'vdup.16 d3, r1; vmull.u16 q2, d2, d3; vshr.u32 q2, q2, #16; vmovn.u32 d14, q2;'
            f'vdup.16 d3, r0; vmull.u16 q2, d2, d3; vshr.u32 q2, q2, #16; vmovn.u32 d15, q2;'
            # Eight corner addresses share one base plus the two row offsets.
            f'ldr r0, [sp, #{F_BXR}]; ldr r1, [sp, #{F_BYG}]; ldr r2, [sp, #{F_BZB}];'
            f'mov r3, #6;'
            f'ldr r7, [sp, #{F_TBASE}];'
            f'mla r7, r0, r3, r7;'
            f'ldr r0, [sp, #{F_SY6}]; mla r7, r1, r0, r7;'
            f'ldr r1, [sp, #{F_SZ6}]; mla r7, r2, r1, r7;'
            f'add r12, r7, r0;'
            f'add r2, r7, r1;'
            f'vld3.16 {{d4[0], d5[0], d6[0]}}, [r7];'
            f'add r3, r7, #6; vld3.16 {{d4[1], d5[1], d6[1]}}, [r3];'
            f'vld3.16 {{d4[2], d5[2], d6[2]}}, [r12];'
            f'add r3, r12, #6; vld3.16 {{d4[3], d5[3], d6[3]}}, [r3];'
            f'vld3.16 {{d7[0], d8[0], d9[0]}}, [r2];'
            f'add r3, r2, #6; vld3.16 {{d7[1], d8[1], d9[1]}}, [r3];'
            f'add r3, r2, r0; vld3.16 {{d7[2], d8[2], d9[2]}}, [r3];'
            f'add r3, r3, #6; vld3.16 {{d7[3], d8[3], d9[3]}}, [r3];'
            # Red corners d4/d7, green d5/d8, blue d6/d9 (VLD3 triplets).
            + channel('d4', 'd7', 'r0', 'r')
            + channel('d5', 'd8', 'r1', 'g')
            + channel('d6', 'd9', 'r2', 'b')
            + f'bx lr;')


def _forward(tag):
    """Integer limited-range YCbCr -> RGB for one pixel (lut.ycbcr_to_rgb_int).

    r0 holds the 8-bit Y sample on entry; chroma products live in the core
    frame; returns r0/r1/r2 = 8-bit R/G/B. r3 is scratch.
    """
    return (f'subs r0, #16;'
            f'movw r12, #{FWD_Y_SCALE}; mul r0, r0, r12; asrs r0, r0, #10;'
            f'mov r3, r0;'
            f'ldr r0, [sp, #{S_CRR}]; adds r0, r0, r3;'
            + _clamp('r0', 0, 255, tag + 'r')
            + f'ldr r1, [sp, #{S_CBG}]; subs r1, r3, r1;'
            f'ldr r12, [sp, #{S_CRG}]; subs r1, r1, r12;'
            + _clamp('r1', 0, 255, tag + 'g')
            + f'ldr r2, [sp, #{S_CBB}]; adds r2, r2, r3;'
            + _clamp('r2', 0, 255, tag + 'b'))


def _pixel_pass(p, tag, simd=False):
    """Forward -> LUT -> reverse for pixel x+p; stores Y and accumulates chroma."""
    head = f'mov r12, r8;' if p == 0 else f'movs r12, #{p}; adds r12, r8, r12;'
    if simd:
        lut_call = 'bl lut_pix;'
        revs = (_rev_frame(F_YCR, 16, 16, 235, S_RY, tag + 'yy')
                + _rev_frame(F_CBCR, 128, 16, 240, S_RCB, tag + 'cb')
                + _rev_frame(F_CRCR, 128, 16, 240, S_RCR, tag + 'cr'))
    else:
        lut_call = 'bl lut_lookup;'
        revs = (_reverse_channel(REV['y'], 16, 16, 235, S_RY, tag + 'yy')
                + _reverse_channel(REV['cb'], 128, 16, 240, S_RCB, tag + 'cb')
                + _reverse_channel(REV['cr'], 128, 16, 240, S_RCR, tag + 'cr'))
    return (head
            + f'ldrb r0, [r5, r12];'
            + _forward(tag)
            + lut_call
            # r12 is clobbered by the reverse channels (coefficients); the
            # pixel offset must be recomputed after them, before the store.
            + revs
            + head
            + f'ldrb r3, [sp, #{S_RY}]; strb r3, [r10, r12];'
            f'ldr r0, [sp, #{S_RCB}]; ldr r12, [sp, #{S_CBSUM}]; adds r12, r12, r0; str r12, [sp, #{S_CBSUM}];'
            f'ldr r0, [sp, #{S_RCR}]; ldr r12, [sp, #{S_CRSUM}]; adds r12, r12, r0; str r12, [sp, #{S_CRSUM}];')


def _chroma_prologue():
    """Load the Cb/Cr pair at [r6, r8] and compute the four frame products."""
    return (f'adds r12, r8, #1;'
            f'ldrb r0, [r6, r8]; ldrb r1, [r6, r12];'
            f'str r0, [sp, #{S_CB}]; str r1, [sp, #{S_CR}];'
            f'subs r0, #128; subs r1, #128;'
            f'movw r12, #{FWD_CR_R}; mul r2, r1, r12; adds r2, #128; asrs r2, r2, #8; str r2, [sp, #{S_CRR}];'
            f'movw r12, #{FWD_CB_G}; mul r2, r0, r12; adds r2, #128; asrs r2, r2, #8; str r2, [sp, #{S_CBG}];'
            f'movw r12, #{FWD_CB_B}; mul r2, r0, r12; adds r2, #128; asrs r2, r2, #8; str r2, [sp, #{S_CBB}];'
            f'movw r12, #{FWD_CR_G}; mul r2, r1, r12; adds r2, #128; asrs r2, r2, #8; str r2, [sp, #{S_CRG}];'
            f'movs r0, #0; str r0, [sp, #{S_CBSUM}]; str r0, [sp, #{S_CRSUM}];')


def _cache_call(address, base_slot, stride_slot):
    return (f'ldr r0, [sp, #{base_slot}]; ldrh r1, [sp, #{stride_slot}];'
            f'ldrh r2, [sp, #{S_H}]; mul r1, r1, r2;'
            + _call('r12', address))


def _gate_source(block_address, processing, displaced_call, session_lock=False):
    # The stock window covers cmp/push/beq/mov plus the first half of the
    # validator bl, so the stock path replicates all of it (r12 clobber is
    # safe: the worker does not hold r12 across the processing call). The
    # append area sits far outside Thumb BL range, so the replicated call
    # goes through r12.
    acquire = release = extra = ''
    if session_lock:
        acquire = (
            'cmp r4, #0; beq gate_null;'
            f'movw r1, #{(block_address + 24) & 65535}; movt r1, #{(block_address + 24) >> 16};'
            'movs r12, #8; gate_lock_loop: ldrex r2, [r1]; cmp r2, #0; bne gate_busy;'
            'movs r2, #1; strex r3, r2, [r1]; cmp r3, #0; beq gate_locked;'
            'subs r12, #1; bne gate_lock_loop; b gate_busy; gate_locked: dmb sy;')
        release = (
            f'movw r1, #{(block_address + 24) & 65535}; movt r1, #{(block_address + 24) >> 16};'
            'dmb sy; movs r2, #0; str r2, [r1];')
        extra = ('gate_busy: clrex; movs r0, #0; str r0, [r4, #0x14]; pop {r4, pc};'
                 'gate_null: movs r0, #0; pop {r4, pc};')
    return (f'push {{r4, lr}}; mov r4, r0;'
            f'movw r1, #{block_address & 0xFFFF}; movt r1, #{block_address >> 16};'
            f'ldr r1, [r1]; cmp r1, #0; beq gate_stock;'
            + acquire +
            f'mov r0, r4; bl lut_core; str r0, [r4, #0x14];'
            + release + 'pop {r4, pc};'
            f'gate_stock: cmp r0, #0; beq gate_pass; mov r0, r4;'
            f'movw r12, #{displaced_call & 0xFFFF}; movt r12, #{displaced_call >> 16};'
            f'blx r12;'
            f'ldr.w pc, ={processing + 12 | 1};'
            f'gate_pass: pop {{r4, pc}};' + extra)


def _getter_gate_source(getter, processing, displaced_call):
    """Resolve a RAM session without allocating or doing file I/O in the worker."""
    return (
        'push {r4, lr}; mov r4, r0;' + invoke(getter) +
        'cmp r0, #0; beq getter_stock; mov r1, r0; ldr r2, [r1];'
        'cmp r2, #0; beq getter_stock; cmp r4, #0; beq getter_null;'
        'sub sp, #8; str r1, [sp]; adds r1, #24; movs r12, #8;'
        'getter_lock: ldrex r2, [r1]; cmp r2, #0; bne getter_busy;'
        'movs r2, #1; strex r3, r2, [r1]; cmp r3, #0; beq getter_locked;'
        'subs r12, #1; bne getter_lock; b getter_busy;'
        'getter_locked: dmb sy; ldr r1, [sp]; mov r0, r4; bl lut_core;'
        'str r0, [r4, #0x14]; ldr r1, [sp]; movs r2, #0; dmb sy; str r2, [r1, #24];'
        'add sp, #8; pop {r4, pc};'
        'getter_busy: clrex; movs r0, #0; str r0, [r4, #0x14]; add sp, #8; pop {r4, pc};'
        'getter_stock: mov r0, r4; cmp r0, #0; beq getter_null;'
        + invoke(displaced_call) + f'ldr.w pc, ={processing + 12 | 1};'
        'getter_null: movs r0, #0; pop {r4, pc};')


def _cell_gate_source(cell, processing, displaced_call):
    """Resolve a RAM session through one owned pointer word, not the page state.

    The menu stores the live session pointer into a fixed word when it commits a
    table and clears that word when it releases one, so the gate keeps working in
    contexts where the settings page is closed and its state anchor is gone.
    """
    return (
        'push {r4, lr}; mov r4, r0;'
        f'movw r1, #{cell & 0xFFFF}; movt r1, #{cell >> 16}; ldr r1, [r1];'
        'cmp r1, #0; beq cell_stock; ldr r2, [r1]; cmp r2, #0; beq cell_stock;'
        'cmp r4, #0; beq cell_null;'
        'sub sp, #8; str r1, [sp]; adds r1, #24; movs r12, #8;'
        'cell_lock: ldrex r2, [r1]; cmp r2, #0; bne cell_busy;'
        'movs r2, #1; strex r3, r2, [r1]; cmp r3, #0; beq cell_locked;'
        'subs r12, #1; bne cell_lock; b cell_busy;'
        'cell_locked: dmb sy; ldr r1, [sp]; mov r0, r4; bl lut_core;'
        'str r0, [r4, #0x14]; ldr r1, [sp]; movs r2, #0; dmb sy; str r2, [r1, #24];'
        'add sp, #8; pop {r4, pc};'
        'cell_busy: clrex; movs r0, #0; str r0, [r4, #0x14]; add sp, #8; pop {r4, pc};'
        'cell_stock: mov r0, r4; cmp r0, #0; beq cell_null;'
        + invoke(displaced_call) + f'ldr.w pc, ={processing + 12 | 1};'
        'cell_null: movs r0, #0; pop {r4, pc};')


def build_lut_core(block_address, gate_address, processing=STOCK_PROCESSING,
                   displaced_call=DISPLACED_CALL,
                   cache_invalidate=CACHE_INVALIDATE, cache_clean=CACHE_CLEAN,
                   active_payload=False, session_lock=False, block_getter=None,
                   cell=None, simd=False):
    """One assembly unit: gate + lut_core + lut_lookup, assembled at gate_address.

    Returns code bytes; the feature patches the stock processing entry with a
    jump to gate_address, and the block data must land at block_address.
    active_payload=True reads an absolute WL3L pointer from block+4, suitable
    for lut_sdload's RAM control. Default retains the embedded block+8 ABI.
    Caller must serialize loading against processing and hold the payload.
    session_lock=True shares lut_session's +24 lock for the full core call;
    a busy gate returns 0 and never accesses table/pixels or clears the owner.
    block_getter optionally resolves a pinned RAM session at runtime without
    allocation/I/O; the gate passes that same session to the private core in r1.
    cell resolves the same session from one owned pointer word instead, which
    keeps the gate independent of the settings page staying open.
    simd=True swaps lut_lookup for the lut_pix helper, whose gather/weight/
    accumulate inner section runs on NEON; the pixel contract is unchanged.
    """
    if session_lock and not active_payload:
        raise ValueError('Session locking requires the active-payload ABI')
    if block_getter is not None and not (active_payload and session_lock):
        raise ValueError('A dynamic session getter requires active payload and session locking')
    if cell is not None and block_getter is not None:
        raise ValueError('Resolve the session through either a cell or a getter, not both')
    if cell is not None and not (active_payload and session_lock):
        raise ValueError('A pointer cell requires active payload and session locking')
    if cell is not None:
        gate = _cell_gate_source(cell, processing, displaced_call)
    elif block_getter is not None:
        gate = _getter_gate_source(block_getter, processing, displaced_call)
    else:
        gate = _gate_source(block_address, processing, displaced_call, session_lock)
    dynamic = block_getter is not None or cell is not None
    frame = CORE_FRAME_SIMD if simd else CORE_FRAME
    simd_prologue = ''
    if simd:
        packed = [REV['y'][0], REV['y'][1], REV['y'][2],
                  REV['cb'][0], REV['cb'][1], REV['cb'][2],
                  REV['cr'][0], REV['cr'][1], REV['cr'][2]]
        simd_prologue = (
            # Table start, row offsets, size-1, domain bounds and spans.
            f'ldrh r0, [r7, #14];'
            f'adds r1, r7, #30; str r1, [sp, #{F_TBASE}];'
            f'mov r1, #6; mul r1, r1, r0; str r1, [sp, #{F_SY6}];'
            f'mul r1, r1, r0; str r1, [sp, #{F_SZ6}];'
            f'subs r1, r0, #1; str r1, [sp, #{F_S1}];'
            f'ldrh r1, [r7, #18]; str r1, [sp, #{F_MINR}];'
            f'ldrh r1, [r7, #20]; str r1, [sp, #{F_MING}];'
            f'ldrh r1, [r7, #22]; str r1, [sp, #{F_MINB}];'
            f'ldrh r1, [r7, #24]; str r1, [sp, #{F_MAXR}];'
            f'ldrh r1, [r7, #26]; str r1, [sp, #{F_MAXG}];'
            f'ldrh r1, [r7, #28]; str r1, [sp, #{F_MAXB}];'
            f'ldr r1, [sp, #{F_MAXR}]; ldr r2, [sp, #{F_MINR}]; subs r1, r1, r2; str r1, [sp, #{F_SPANR}];'
            f'ldr r1, [sp, #{F_MAXG}]; ldr r2, [sp, #{F_MING}]; subs r1, r1, r2; str r1, [sp, #{F_SPANG}];'
            f'ldr r1, [sp, #{F_MAXB}]; ldr r2, [sp, #{F_MINB}]; subs r1, r1, r2; str r1, [sp, #{F_SPANB}];'
            # Reverse-conversion coefficients stay in the frame (one LDR per use).
            + ''.join(f'movw r1, #{c & 0xFFFF}; movt r1, #{(c & 0xFFFFFFFF) >> 16};'
                      f'str r1, [sp, #{F_YCR + i * 4}];'
                      for i, c in enumerate(packed))
            + f'mov r4, #257;')
    source = (gate
              + 'lut_core: '
              + f'push {{r4-r11, lr}}; sub sp, #{frame};'
              + ('str r1, [sp, #32];' if dynamic else '')
              + f'mov r4, r0; cmp r4, #0; beq core_fail;'
              f'ldr r5, [r4, #4]; ldr r6, [r4, #0x10]; cmp r5, #0; beq core_fail; cmp r6, #0; beq core_fail;'
              f'ldrb r0, [r5]; cmp r0, #1; bne core_fail;'
              f'ldrh r0, [r5, #0xA]; ldrh r1, [r6, #0xA]; cmp r0, r1; bne core_fail;'
              f'ldrh r1, [r5, #0xC]; ldrh r2, [r6, #0xC]; cmp r1, r2; bne core_fail;'
              f'cmp r0, #0; beq core_fail; cmp r1, #0; beq core_fail;'
              f'strh r0, [sp, #{S_W}]; strh r1, [sp, #{S_H}];'
              f'ldrh r0, [r5, #2]; strh r0, [sp, #{S_YSIN}];'
              f'ldrh r0, [r5, #4]; strh r0, [sp, #{S_CSIN}];'
              f'ldrh r0, [r6, #2]; strh r0, [sp, #{S_YSOUT}];'
              f'ldrh r0, [r6, #4]; strh r0, [sp, #{S_CSOUT}];'
              f'ldrh r0, [r5, #0xE]; strh r0, [sp, #{S_X0}];'
              f'ldrh r0, [r5, #0x10]; strh r0, [sp, #{S_Y0}];'
              f'ldr r0, [r5, #0x14]; str r0, [sp, #{S_YIN}];'
              f'ldr r0, [r5, #0x18]; str r0, [sp, #{S_CIN}];'
              f'ldr r0, [r6, #0x14]; str r0, [sp, #{S_YOUT}];'
              f'ldr r0, [r6, #0x18]; str r0, [sp, #{S_COUT}];'
              + ('ldr r7, [sp, #32];' if dynamic else
                 f'movw r7, #{block_address & 0xFFFF}; movt r7, #{block_address >> 16};')
              + ('ldr r7, [r7, #4]; cmp r7, #0; beq core_fail; subs r7, #8;'
                 if active_payload else '')
              # x0 must be even for interleaved chroma pairs
              + f'ldrh r0, [sp, #{S_X0}]; lsrs r0, r0, #1; lsls r0, r0, #1;'
              f'ldrh r1, [sp, #{S_X0}]; cmp r0, r1; bne core_fail;'
              f'ldrh r0, [sp, #{S_W}]; lsrs r0, r0, #1; lsls r0, r0, #1;'
              f'ldrh r1, [sp, #{S_W}]; subs r0, r1, r0; str r0, [sp, #{S_TAIL}];'
              + simd_prologue
              + _cache_call(cache_invalidate, S_YIN, S_YSIN)
              + _cache_call(cache_invalidate, S_CIN, S_CSIN)
              + f'ldrh r0, [sp, #{S_Y0}]; str r0, [sp, #{S_Y}];'
              f'ldrh r1, [sp, #{S_H}]; adds r0, r0, r1; str r0, [sp, #{S_YEND}];'
              f'row_loop: ldr r0, [sp, #{S_Y}]; ldr r1, [sp, #{S_YEND}]; cmp r0, r1; bhs rows_done;'
              f'ldr r2, [sp, #{S_YIN}]; ldrh r3, [sp, #{S_YSIN}]; mla r5, r3, r0, r2;'
              f'ldr r2, [sp, #{S_CIN}]; ldrh r3, [sp, #{S_CSIN}]; mla r6, r3, r0, r2;'
              f'ldr r2, [sp, #{S_YOUT}]; ldrh r3, [sp, #{S_YSOUT}]; mla r10, r3, r0, r2;'
              f'ldr r2, [sp, #{S_COUT}]; ldrh r3, [sp, #{S_CSOUT}]; mla r11, r3, r0, r2;'
              f'ldrh r8, [sp, #{S_X0}];'
              f'ldrh r9, [sp, #{S_W}]; lsrs r9, r9, #1;'
              f'pair_loop: cmp r9, #0; beq pair_done; subs r9, #1;'
              + _chroma_prologue()
              + _pixel_pass(0, 'p0', simd)
              + _pixel_pass(1, 'p1', simd)
              + f'ldr r0, [sp, #{S_CBSUM}]; lsrs r0, r0, #1; strb r0, [r11, r8];'
              f'ldr r0, [sp, #{S_CRSUM}]; lsrs r0, r0, #1;'
              f'adds r12, r8, #1; strb r0, [r11, r12];'
              f'adds r8, #2; b pair_loop;'
              f'pair_done: ldr r0, [sp, #{S_TAIL}]; cmp r0, #0; beq row_next;'
              + _chroma_prologue()
              + _pixel_pass(0, 'pt', simd)
              + f'ldrb r0, [sp, #{S_RCB}]; strb r0, [r11, r8];'
              f'ldrb r0, [sp, #{S_RCR}]; adds r12, r8, #1; strb r0, [r11, r12];'
              f'row_next: ldr r0, [sp, #{S_Y}]; adds r0, #1; str r0, [sp, #{S_Y}]; b row_loop;'
              f'rows_done:'
              + _cache_call(cache_clean, S_YOUT, S_YSOUT)
              + _cache_call(cache_clean, S_COUT, S_CSOUT)
              + f'movs r0, #1; b core_done;'
              f'core_fail: movs r0, #0;'
              f'core_done: add sp, #{frame}; pop {{r4-r11, pc}};'
              + ('' if simd else _lut_lookup())
              + (_pixel_simd('t') if simd else ''))
    return assemble(source, gate_address, 0x8000)
