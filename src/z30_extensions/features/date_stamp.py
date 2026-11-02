# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""Film-style date stamp drawn on the planes just before the stock JPEG encoder.

The gate replaces the entry of the stock encode gateway 0x10096EE6. Calls from the
step family that owns a capture context (r4) stamp the descriptor in [r0] when the
menu mode is on; every other caller and mode continues into the stock function
with registers intact. The date is the shot time the EXIF writer uses, at
capture info +0x2624 (u16 year, u8 month, day, hour, minute, second).

Register plan in stamp_draw: r4 descriptor, r5 date, r6 mode then glyph count,
r8 font unit, r9 font table, r11 frame. The pixel contract is
z30_extensions.date_stamp.stamp_frame.
"""
import struct

from .. import date_stamp as host
from ..assembly import assemble, constant as c, invoke as n, jump

ENCODE_GATEWAY = 0x10096EE6
# push.w {r4-r8,lr}; movs r6,#1; mov r5,r0; movs r1,#0
GATE = (ENCODE_GATEWAY, bytes.fromhex('2de9f04101260546 0021'),
        'Date stamp gate at the stock JPEG encode gateway')
RESUME = ENCODE_GATEWAY + 10
# Main, second-main and derived image encodes of the capture-context step family.
CALL_SITES = (0x100A6198, 0x100A6304, 0x100A6540, 0x100A66AE, 0x100A684A)
INFO_FIELD = 0x3A4
DATE_OFFSET = 0x2624
CACHE_INVALIDATE = 0x10204B48
CACHE_CLEAN = 0x10204B0C

F_YB, F_CB, F_YS, F_CS, F_FMT, F_X0, F_Y0, F_TW, F_TH = 0, 4, 8, 12, 16, 20, 24, 28, 32
F_TEXT = 36     # 15 glyph codes fit below the frame end
FRAME = 56


def _thumb_bl(site, target):
    offset = target - (site + 4)
    s = (offset >> 24) & 1
    j1 = (~((offset >> 23) ^ s)) & 1
    j2 = (~((offset >> 22) ^ s)) & 1
    return struct.pack('<2H', 0xF000 | (s << 10) | ((offset >> 12) & 0x3FF),
                       0xD000 | (j1 << 13) | (j2 << 11) | ((offset >> 1) & 0x7FF))


def _digits(source):
    """Two more glyph codes at the r7 cursor; the value comes from source."""
    return source + 'bl stamp_two;'


def _plain(glyph):
    return f'movs r0,#{glyph};strb r0,[r7],#1;'


def _cache_rows():
    """Run the maintenance function in r0 over each stamped row of both planes.

    Row by row keeps every call inside the stock line-maintenance branch instead
    of its whole-cache service, and leaves the rest of the frame alone.
    """
    return (f'stamp_rows:push {{r4-r7,lr}};mov r4,r0;'
            f'ldr r5,[r11,#{F_Y0}];ldr r6,[r11,#{F_TH}];adds r6,r6,r5;'
            f'rows_y:cmp r5,r6;bhs rows_y_done;'
            f'ldr r0,[r11,#{F_YB}];ldr r1,[r11,#{F_YS}];mla r0,r1,r5,r0;'
            f'ldr r1,[r11,#{F_X0}];adds r0,r0,r1;ldr r1,[r11,#{F_TW}];blx r4;'
            f'adds r5,#1;b rows_y;'
            f'rows_y_done:ldr r5,[r11,#{F_Y0}];ldr r0,[r11,#{F_FMT}];cmp r0,#2;bne rows_c;'
            f'lsrs r5,r5,#1;adds r6,#1;lsrs r6,r6,#1;'
            f'rows_c:cmp r5,r6;bhs rows_done;'
            f'ldr r0,[r11,#{F_CB}];ldr r1,[r11,#{F_CS}];mla r0,r1,r5,r0;'
            f'ldr r1,[r11,#{F_X0}];adds r0,r0,r1;ldr r1,[r11,#{F_TW}];adds r1,#1;bic r1,r1,#1;blx r4;'
            f'adds r5,#1;b rows_c;'
            f'rows_done:pop {{r4-r7,pc}};')


def _rect():
    """Fill r0=x, r1=y, r2=w, r3=h on both planes; matches host._fill."""
    return (f'stamp_rect:push {{r4-r10,lr}};mov r4,r0;mov r5,r1;mov r6,r2;mov r7,r3;'
            f'ldr r8,[r11,#{F_YS}];ldr r9,[r11,#{F_YB}];mla r9,r8,r5,r9;add r9,r9,r4;'
            f'mov r10,r7;movs r1,#{host.STAMP_Y};'
            f'luma_row:movs r0,#0;'
            f'luma_col:strb r1,[r9,r0];adds r0,#1;cmp r0,r6;blo luma_col;'
            f'add r9,r9,r8;subs r10,#1;bne luma_row;'
            # r6 becomes the chroma byte span of one row; r4 its even start.
            f'adds r6,r6,r4;adds r6,#1;bic r6,r6,#1;bic r4,r4,#1;subs r6,r6,r4;'
            f'ldr r8,[r11,#{F_CS}];ldr r9,[r11,#{F_CB}];ldr r10,[r11,#{F_FMT}];'
            f'chroma_row:mov r0,r5;cmp r10,#2;bne chroma_addr;lsrs r0,r0,#1;'
            f'chroma_addr:mla r2,r0,r8,r9;adds r2,r2,r4;movs r0,#0;'
            f'chroma_col:movs r1,#{host.STAMP_CB};strb r1,[r2,r0];adds r0,#1;'
            f'movs r1,#{host.STAMP_CR};strb r1,[r2,r0];adds r0,#1;cmp r0,r6;blo chroma_col;'
            f'adds r5,#1;subs r7,#1;bne chroma_row;'
            f'pop {{r4-r10,pc}};')


def _draw(table):
    return (
        f'stamp_draw:push {{r4-r11,lr}};sub sp,#{FRAME + 4};mov r11,sp;'
        f'mov r4,r1;mov r6,r2;movw r1,#{DATE_OFFSET};adds r5,r0,r1;'
        # Descriptor: only the formats the stock encoder accepts, full planes.
        f'ldrb r0,[r4];cmp r0,#1;beq format_ok;cmp r0,#2;bne stamp_out;'
        f'format_ok:str r0,[r11,#{F_FMT}];'
        f'ldrh r0,[r4,#0xA];cmp r0,#0;beq stamp_out;ldrh r1,[r4,#0xC];cmp r1,#0;beq stamp_out;'
        f'ldrh r2,[r4,#2];cmp r2,r0;blo stamp_out;str r2,[r11,#{F_YS}];'
        f'ldrh r2,[r4,#4];cmp r2,r0;blo stamp_out;str r2,[r11,#{F_CS}];'
        f'ldr r2,[r4,#0x14];cmp r2,#0;beq stamp_out;str r2,[r11,#{F_YB}];'
        f'ldr r2,[r4,#0x18];cmp r2,#0;beq stamp_out;str r2,[r11,#{F_CB}];'
        # An unset or corrupt shot time leaves the photo untouched.
        f'ldrh r0,[r5];subw r0,r0,#2000;cmp r0,#99;bhi stamp_out;'
        f'ldrb r0,[r5,#2];subs r0,#1;cmp r0,#11;bhi stamp_out;'
        f'ldrb r0,[r5,#3];subs r0,#1;cmp r0,#30;bhi stamp_out;'
        f'ldrb r0,[r5,#4];cmp r0,#23;bhi stamp_out;'
        f'ldrb r0,[r5,#5];cmp r0,#59;bhi stamp_out;'
        f'add r7,r11,#{F_TEXT};'
        + _plain(host.APOSTROPHE)
        + _digits('ldrh r0,[r5];subw r0,r0,#2000;')
        + _plain(host.SPACE) + _digits('ldrb r0,[r5,#2];')
        + _plain(host.SPACE) + _digits('ldrb r0,[r5,#3];')
        + 'cmp r6,#2;bne text_done;'
        + _plain(host.SPACE) + _digits('ldrb r0,[r5,#4];')
        + _plain(host.COLON) + _digits('ldrb r0,[r5,#5];')
        + f'text_done:add r6,r11,#{F_TEXT};subs r6,r7,r6;'
        # Geometry, signed so a stamp that cannot fit is skipped.
        + c('r9', table)
        + f'ldrh r0,[r4,#0xA];movw r1,#{host.UNIT_DIVISOR};udiv r8,r0,r1;cmp r8,#0;bne unit_ok;movs r0,#1;mov r8,r0;'
        f'unit_ok:add r2,r11,#{F_TEXT};add r3,r9,#{host.TABLE_ADVANCES};movs r0,#0;movs r1,#0;'
        f'width_loop:ldrb r10,[r2,r1];ldrb r10,[r3,r10];add r0,r0,r10;adds r1,#1;cmp r1,r6;blo width_loop;'
        f'subs r0,#{host.TRAILING_GAP};mul r0,r0,r8;str r0,[r11,#{F_TW}];'
        f'ldrh r1,[r4,#0xA];movs r2,#{host.MARGIN_X_DIVISOR};udiv r2,r1,r2;subs r1,r1,r2;subs r1,r1,r0;'
        f'blt stamp_out;bic r1,r1,#1;str r1,[r11,#{F_X0}];'
        f'movs r0,#{host.GLYPH_HEIGHT};mul r0,r0,r8;str r0,[r11,#{F_TH}];'
        f'ldrh r1,[r4,#0xC];movs r2,#{host.MARGIN_Y_DIVISOR};udiv r2,r1,r2;subs r1,r1,r2;subs r1,r1,r0;'
        f'blt stamp_out;bic r1,r1,#1;str r1,[r11,#{F_Y0}];'
        # Hardware wrote these planes, so drop stale lines before the CPU dirties any.
        + c('r0', CACHE_INVALIDATE | 1) + 'bl stamp_rows;'
        f'ldr r5,[r11,#{F_X0}];movs r4,#0;'
        f'glyph_loop:cmp r4,r6;bhs glyphs_done;'
        f'add r0,r11,#{F_TEXT};ldrb r7,[r0,r4];'
        f'lsls r0,r7,#1;ldrh r10,[r9,r0];'
        f'mov r3,r9;add r3,r3,#{host.TABLE_PRIMITIVES};'
        f'prim_loop:cmp r10,#0;beq prim_done;tst r10,#1;beq prim_next;'
        f'push {{r3}};ldrb r0,[r3];ldrb r1,[r3,#1];ldrb r2,[r3,#2];ldrb r3,[r3,#3];'
        f'mul r0,r0,r8;mul r1,r1,r8;mul r2,r2,r8;mul r3,r3,r8;'
        f'adds r0,r0,r5;ldr r12,[r11,#{F_Y0}];add r1,r1,r12;bl stamp_rect;pop {{r3}};'
        f'prim_next:adds r3,#4;lsr r10,r10,#1;b prim_loop;'
        f'prim_done:add r0,r9,#{host.TABLE_ADVANCES};ldrb r0,[r0,r7];mul r0,r0,r8;adds r5,r5,r0;'
        f'adds r4,#1;b glyph_loop;'
        f'glyphs_done:' + c('r0', CACHE_CLEAN | 1) + 'bl stamp_rows;'
        f'stamp_out:add sp,#{FRAME + 4};pop {{r4-r11,pc}};'
        f'stamp_two:movs r1,#10;udiv r2,r0,r1;mls r0,r2,r1,r0;strb r2,[r7],#1;strb r0,[r7],#1;bx lr;'
        + _cache_rows() + _rect())


def _gate(get, state_offset):
    sites = ''.join(c('r1', site + 5) + 'cmp r5,r1;beq gate_hit;' for site in CALL_SITES)
    return (
        f'push {{r0-r3,r5,lr}};mov r5,lr;{sites}b gate_out;'
        f'gate_hit:{n(get)}cmp r0,#0;beq gate_out;'
        + c('r1', state_offset)
        + f'ldr r2,[r0,r1];subs r1,r2,#1;cmp r1,#1;bhi gate_out;'
        f'ldr r0,[sp];ldr r1,[r0];cmp r1,#0;beq gate_out;'
        f'ldr.w r0,[r4,#{INFO_FIELD}];cmp r0,#0;beq gate_out;bl stamp_draw;'
        f'gate_out:pop {{r0-r3,r5,lr}};push.w {{r4-r8,lr}};movs r6,#1;mov r5,r0;movs r1,#0;'
        f'.balign 4;ldr.w pc,[pc,#0];.word {RESUME | 1};')


def build_core(address, table, get, state_offset):
    """Gate plus drawing routines, assembled as one unit at address."""
    return assemble(_gate(get, state_offset) + _draw(table), address, 2048)


def add(plan):
    for site in CALL_SITES:
        if plan.image.read(site, 4) != _thumb_bl(site, ENCODE_GATEWAY):
            raise ValueError(f'Unexpected stock encode call at {site:#x}')
    control = plan.features['date_stamp_control']
    get = int(plan.features['shutter_sound']['functions']['get'], 16)
    table = plan.alloc(host.font_table())
    entry = plan.address
    code = build_core(entry, table, get, control['state_offset'])
    plan.alloc(code)
    address, expected, name = GATE
    plan.edit(address, jump(address, entry, len(expected)), name, expected)
    plan.features['date_stamp'] = dict(
        gate=hex(address), gate_entry=hex(entry | 1), resume=hex(RESUME | 1), code_bytes=len(code),
        font_table=hex(table), call_sites=[hex(site) for site in CALL_SITES],
        date_source=f'capture info +{DATE_OFFSET:#x}, validated before drawing',
        color_rgb=list(host.STAMP_RGB), color_ycbcr=[host.STAMP_Y, host.STAMP_CB, host.STAMP_CR],
        pixel_contract='Opaque Y/C writes; Cb-first interleaving pending on-device check',
        native_parity='tools/verify_date_stamp.py', camera_verified=False,
        limitations=['JPEG only: NEF raw data is never altered.',
                     'Stamped into pixels and cannot be removed from the JPEG afterwards.',
                     'Retouch copies pass through the same step family and are stamped while the mode is on.',
                     'The other stock encode callers (0x1009xxxx family) pass through unstamped.'])
