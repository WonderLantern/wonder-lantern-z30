# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""Film-style date stamp drawn on the planes just before the stock JPEG encoder.

The gate replaces the entry of the stock encode gateway 0x10096EE6. Calls from the
step family that owns a capture context (r4) stamp the descriptor in [r0] when the
menu mode is on; every other caller and mode continues into the stock function
with registers intact. The date is the shot time the EXIF writer uses, at
capture info +0x2624 (u16 year, u8 month, day, hour, minute, second).

Register plan in stamp_draw: r4 descriptor, r5 date then grid cursor, r6 mode then
glyph count, r8 scale, r9 style table, r11 frame. The pixel contract is
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
# Capture orientation byte; the stock EXIF Orientation writer 0x102D48E6 reads it
# from the same info block, and 0x101D5F74 latches it with the shot time.
ORIENTATION_OFFSET = 0x2615
COLOR_FIELD = 8     # menu state: +0 mode, +4 settings handle, +8 style (color | glow flag)
CACHE_INVALIDATE = 0x10204B48
CACHE_CLEAN = 0x10204B0C

F_YB, F_CB, F_YS, F_CS, F_FMT, F_X0, F_Y0, F_TW, F_TH = 0, 4, 8, 12, 16, 20, 24, 28, 32
F_TEXT = 36     # up to 15 glyph codes
F_LUMA, F_CHROMA_B, F_CHROMA_R = 52, 53, 54
F_END = 56      # end of the primitive runs
F_OX, F_OY, F_GROW = 60, 64, 68     # this pass: run origin, and growth added to each size
F_PASS, F_COLOR = 72, 76            # pass index, the color's row of pass triples
F_BORDER = 80                       # glow border in grid units, 0 without the glow
F_DW, F_DH, F_ORIENT = 88, 92, 96   # upright text box, and the capture orientation
FRAME = 104


def _thumb_bl(site, target):
    offset = target - (site + 4)
    s = (offset >> 24) & 1
    j1 = (~((offset >> 23) ^ s)) & 1
    j2 = (~((offset >> 22) ^ s)) & 1
    return struct.pack('<2H', 0xF000 | (s << 10) | ((offset >> 12) & 0x3FF),
                       0xD000 | (j1 << 13) | (j2 << 11) | ((offset >> 1) & 0x7FF))


def _text():
    """Expand the mode's format tokens (r3) into glyph codes at the r7 cursor."""
    return (f'text_loop:ldrb r0,[r3],#1;cmp r0,#{host.TOKEN_END};beq text_done;'
            f'cmp r0,#{host.TOKEN_SPACE};bne not_space;movs r0,#{host.SPACE};strb r0,[r7],#1;b text_loop;'
            f'not_space:cmp r0,#{host.TOKEN_COLON};bne not_colon;movs r0,#{host.COLON};strb r0,[r7],#1;b text_loop;'
            f'not_colon:cmp r0,#{host.YEAR};bne field;movs r0,#{host.APOSTROPHE};strb r0,[r7],#1;'
            f'ldrh r0,[r5];subw r0,r0,#2000;bl stamp_two;b text_loop;'
            f'field:ldrb r0,[r5,r0];bl stamp_two;b text_loop;'
            f'text_done:')


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
            f'mov r10,r7;ldrb r1,[r11,#{F_LUMA}];'
            f'luma_row:movs r0,#0;'
            f'luma_col:strb r1,[r9,r0];adds r0,#1;cmp r0,r6;blo luma_col;'
            f'add r9,r9,r8;subs r10,#1;bne luma_row;'
            # r6 becomes the chroma byte span of one row; r4 its even start.
            f'adds r6,r6,r4;adds r6,#1;bic r6,r6,#1;bic r4,r4,#1;subs r6,r6,r4;'
            f'ldr r8,[r11,#{F_CS}];ldr r9,[r11,#{F_CB}];ldr r10,[r11,#{F_FMT}];'
            f'chroma_row:mov r0,r5;cmp r10,#2;bne chroma_addr;lsrs r0,r0,#1;'
            f'chroma_addr:mla r2,r0,r8,r9;adds r2,r2,r4;movs r0,#0;'
            f'chroma_col:ldrb r1,[r11,#{F_CHROMA_B}];strb r1,[r2,r0];adds r0,#1;'
            f'ldrb r1,[r11,#{F_CHROMA_R}];strb r1,[r2,r0];adds r0,#1;cmp r0,r6;blo chroma_col;'
            f'adds r5,#1;subs r7,#1;bne chroma_row;'
            f'pop {{r4-r10,pc}};')


def _place():
    """Map r0=u, r1=v, r2=w, r3=h in the upright text box to stored pixels; matches host place()."""
    return (f'stamp_place:push {{r4,lr}};ldr r12,[r11,#{F_ORIENT}];'
            f'cmp r12,#{host.ORIENT_CW};beq place_cw;cmp r12,#{host.ORIENT_CCW};beq place_ccw;'
            f'ldr r12,[r11,#{F_X0}];add r0,r0,r12;ldr r12,[r11,#{F_Y0}];add r1,r1,r12;pop {{r4,pc}};'
            # Clockwise view: x = x0 + v, y = y0 + box width - u - w.
            f'place_cw:ldr r4,[r11,#{F_DW}];subs r4,r4,r0;subs r4,r4,r2;'
            f'ldr r12,[r11,#{F_X0}];add r0,r1,r12;ldr r12,[r11,#{F_Y0}];add r1,r4,r12;b place_swap;'
            # Counter-clockwise view: x = x0 + box height - v - h, y = y0 + u.
            f'place_ccw:ldr r4,[r11,#{F_DH}];subs r4,r4,r1;subs r4,r4,r3;'
            f'ldr r12,[r11,#{F_Y0}];add r1,r0,r12;ldr r12,[r11,#{F_X0}];add r0,r4,r12;'
            f'place_swap:mov r4,r2;mov r2,r3;mov r3,r4;pop {{r4,pc}};')


def _lighten():
    """Glow r0=x, r1=y, r2=w, r3=h: raise darker luma, recolor its pair; matches host._lighten."""
    return (f'stamp_lighten:push {{r4-r10,lr}};mov r4,r0;mov r5,r1;mov r6,r2;mov r7,r3;'
            f'ldr r8,[r11,#{F_YS}];ldr r9,[r11,#{F_YB}];mla r9,r8,r5,r9;add r9,r9,r4;'
            f'ldr r10,[r11,#{F_FMT}];'
            f'light_row:mov r0,r5;cmp r10,#2;bne light_addr;lsrs r0,r0,#1;'
            f'light_addr:ldr r1,[r11,#{F_CS}];ldr r2,[r11,#{F_CB}];mla r2,r0,r1,r2;movs r0,#0;'
            f'light_col:ldrb r1,[r9,r0];ldrb r3,[r11,#{F_LUMA}];cmp r1,r3;bhs light_next;'
            f'strb r3,[r9,r0];adds r1,r0,r4;bic r1,r1,#1;add r1,r1,r2;'
            f'ldrb r3,[r11,#{F_CHROMA_B}];strb r3,[r1];ldrb r3,[r11,#{F_CHROMA_R}];strb r3,[r1,#1];'
            f'light_next:adds r0,#1;cmp r0,r6;blo light_col;'
            f'add r9,r9,r8;adds r5,#1;subs r7,#1;bne light_row;'
            f'pop {{r4-r10,pc}};')


def _draw(table):
    return (
        f'stamp_draw:push {{r4-r11,lr}};sub sp,#{FRAME + 4};mov r11,sp;'
        f'mov r4,r1;mov r6,r2;movw r1,#{DATE_OFFSET};adds r5,r0,r1;'
        + c('r9', table)
        # r3 = saved style: range-checked, then the glow flag picks the first pass and
        # the border, and the color index selects a table row.
        + f'cmp r3,#{host.STYLE_MAX};bhi stamp_out;'
        f'movs r0,#{len(host.PASS_GROWTH) - 1};movs r1,#0;tst r3,#{host.GLOW_FLAG};beq style_set;'
        f'movs r0,#0;movs r1,#{host.PASS_GROWTH[0]};'
        f'style_set:str r0,[r11,#{F_PASS}];str r1,[r11,#{F_BORDER}];and r3,r3,#{host.GLOW_FLAG - 1};'
        f'movs r0,#{host.COLOR_BYTES};mul r3,r3,r0;add r3,r3,r9;'
        f'add r3,r3,#{host.TABLE_COLORS};str r3,[r11,#{F_COLOR}];'
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
        # Orientation as the EXIF writer reads it; only its two portrait codes turn the stamp.
        f'ldrb r0,[r5,#-{DATE_OFFSET - ORIENTATION_OFFSET}];cmp r0,#{host.ORIENT_CW};it hi;movhi r0,#0;'
        f'str r0,[r11,#{F_ORIENT}];'
        f'add r7,r11,#{F_TEXT};subs r3,r6,#1;movs r0,#{host.FORMAT_BYTES};mul r3,r3,r0;add r3,r3,r9;'
        f'add r3,r3,#{host.TABLE_FORMATS};'
        + _text()
        + f'add r6,r11,#{F_TEXT};subs r6,r7,r6;'
        # Geometry, signed so a stamp that cannot fit is skipped.
        + f'ldrh r8,[r4,#0xA];movw r0,#{host.MIN_SCALE};cmp r8,r0;it lo;movlo r8,r0;'
        f'add r2,r11,#{F_TEXT};add r3,r9,#{host.TABLE_ADVANCES};movs r0,#0;movs r1,#0;'
        f'width_loop:ldrb r10,[r2,r1];ldrb r10,[r3,r10];add r0,r0,r10;adds r1,#1;cmp r1,r6;blo width_loop;'
        f'subs r0,#{host.TRAILING_GAP};mul r0,r0,r8;movw r1,#{host.SCALE_DIVISOR};udiv r0,r0,r1;'
        # With the glow, the box keeps a border for its outermost pass on every side.
        f'ldr r2,[r11,#{F_BORDER}];mul r2,r2,r8;udiv r2,r2,r1;add r0,r0,r2,lsl #1;str r0,[r11,#{F_DW}];'
        f'movs r0,#{host.GLYPH_HEIGHT};mul r0,r0,r8;movw r1,#{host.SCALE_DIVISOR};udiv r0,r0,r1;'
        f'ldr r2,[r11,#{F_BORDER}];mul r2,r2,r8;udiv r2,r2,r1;add r0,r0,r2,lsl #1;str r0,[r11,#{F_DH}];'
        # The stored box turns a quarter for portrait; each stored edge keeps its margin.
        f'ldr r0,[r11,#{F_DW}];ldr r1,[r11,#{F_DH}];ldr r2,[r11,#{F_ORIENT}];cmp r2,#0;beq box_set;'
        f'mov r12,r0;mov r0,r1;mov r1,r12;'
        f'box_set:str r0,[r11,#{F_TW}];str r1,[r11,#{F_TH}];'
        f'ldrh r3,[r4,#0xA];movs r12,#{host.MARGIN_X_DIVISOR};udiv r12,r3,r12;'
        f'cmp r2,#{host.ORIENT_CCW};beq x_set;subs r12,r3,r12;subs r12,r12,r0;'
        f'x_set:cmp r12,#0;blt stamp_out;add r2,r12,r0;cmp r2,r3;bgt stamp_out;'
        f'bic r12,r12,#1;str r12,[r11,#{F_X0}];'
        f'ldr r2,[r11,#{F_ORIENT}];ldrh r3,[r4,#0xC];movs r12,#{host.MARGIN_Y_DIVISOR};udiv r12,r3,r12;'
        f'cmp r2,#{host.ORIENT_CW};beq y_set;subs r12,r3,r12;subs r12,r12,r1;'
        f'y_set:cmp r12,#0;blt stamp_out;add r2,r12,r1;cmp r2,r3;bgt stamp_out;'
        f'bic r12,r12,#1;str r12,[r11,#{F_Y0}];'
        # Hardware wrote these planes, so drop stale lines before the CPU dirties any.
        + c('r0', CACHE_INVALIDATE | 1) + 'bl stamp_rows;'
        + c('r0', host.TABLE_PRIMITIVES + host.PRIMITIVE_BYTES * len(host.PRIMITIVES))
        + f'add r0,r0,r9;str r0,[r11,#{F_END}];'
        # Each pass: its color triple, then its growth in pixels inside the border.
        f'pass_loop:ldr r0,[r11,#{F_PASS}];cmp r0,#{len(host.PASS_GROWTH)};bhs passes_done;'
        f'ldr r1,[r11,#{F_COLOR}];add r2,r0,r0,lsl #1;add r1,r1,r2;'
        f'ldrb r2,[r1];strb r2,[r11,#{F_LUMA}];ldrb r2,[r1,#1];strb r2,[r11,#{F_CHROMA_B}];'
        f'ldrb r2,[r1,#2];strb r2,[r11,#{F_CHROMA_R}];'
        f'add r1,r9,#{host.TABLE_GROWTH};ldrb r1,[r1,r0];mul r1,r1,r8;movw r3,#{host.SCALE_DIVISOR};udiv r1,r1,r3;'
        f'ldr r2,[r11,#{F_BORDER}];mul r2,r2,r8;udiv r2,r2,r3;subs r2,r2,r1;'
        f'str r2,[r11,#{F_OX}];str r2,[r11,#{F_OY}];lsls r1,r1,#1;str r1,[r11,#{F_GROW}];'
        f'movs r5,#0;movs r4,#0;'
        f'glyph_loop:cmp r4,r6;bhs glyphs_done;'
        f'add r0,r11,#{F_TEXT};ldrb r7,[r0,r4];'
        f'lsls r0,r7,#1;ldrh r10,[r9,r0];'
        f'mov r3,r9;add r3,r3,#{host.TABLE_PRIMITIVES};'
        # Each run is (segment bit, x, y, w, h) in grid units; its edges scale to pixels.
        f'prim_loop:ldr r0,[r11,#{F_END}];cmp r3,r0;bhs prim_done;'
        f'ldrb r0,[r3];lsr r0,r10,r0;tst r0,#1;beq prim_next;push {{r3}};'
        f'ldrb r0,[r3,#1];adds r0,r0,r5;ldrb r1,[r3,#3];adds r1,r1,r0;mul r0,r0,r8;mul r1,r1,r8;'
        f'movw r12,#{host.SCALE_DIVISOR};udiv r0,r0,r12;udiv r1,r1,r12;subs r2,r1,r0;'
        f'ldr r1,[r11,#{F_OX}];adds r0,r0,r1;'
        f'ldrb r1,[r3,#2];ldrb r3,[r3,#4];adds r3,r3,r1;mul r1,r1,r8;mul r3,r3,r8;'
        f'udiv r1,r1,r12;udiv r3,r3,r12;subs r3,r3,r1;ldr r12,[r11,#{F_OY}];add r1,r1,r12;'
        f'cmp r2,#0;beq prim_skip;cmp r3,#0;beq prim_skip;'
        f'ldr r12,[r11,#{F_GROW}];add r2,r2,r12;add r3,r3,r12;bl stamp_place;'
        f'ldr r12,[r11,#{F_PASS}];cmp r12,#{len(host.PASS_GROWTH) - 1};beq prim_opaque;'
        f'bl stamp_lighten;b prim_skip;prim_opaque:bl stamp_rect;'
        f'prim_skip:pop {{r3}};'
        f'prim_next:adds r3,#{host.PRIMITIVE_BYTES};b prim_loop;'
        f'prim_done:add r0,r9,#{host.TABLE_ADVANCES};ldrb r0,[r0,r7];adds r5,r5,r0;'
        f'adds r4,#1;b glyph_loop;'
        f'glyphs_done:ldr r0,[r11,#{F_PASS}];adds r0,#1;str r0,[r11,#{F_PASS}];b pass_loop;'
        f'passes_done:' + c('r0', CACHE_CLEAN | 1) + 'bl stamp_rows;'
        f'stamp_out:add sp,#{FRAME + 4};pop {{r4-r11,pc}};'
        f'stamp_two:movs r1,#10;udiv r2,r0,r1;mls r0,r2,r1,r0;strb r2,[r7],#1;strb r0,[r7],#1;bx lr;'
        + _cache_rows() + _rect() + _lighten() + _place())


def _gate(get, state_offset):
    sites = ''.join(c('r1', site + 5) + 'cmp r5,r1;beq gate_hit;' for site in CALL_SITES)
    return (
        f'push {{r0-r3,r5,lr}};mov r5,lr;{sites}b gate_out;'
        f'gate_hit:{n(get)}cmp r0,#0;beq gate_out;'
        + c('r1', state_offset)
        + f'ldr r2,[r0,r1];adds r1,#{COLOR_FIELD};ldr r3,[r0,r1];subs r1,r2,#1;cmp r1,#{len(host.FORMATS) - 1};bhi gate_out;'
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
        colors_rgb=[list(rgb) for rgb in host.COLORS_RGB], colors_ycbcr=[list(color) for color in host.COLORS],
        pixel_contract='Opaque Y/C writes; Cb-first interleaving pending on-device check',
        native_parity='tools/verify_date_stamp.py', camera_verified=False,
        limitations=['JPEG only: NEF raw data is never altered.',
                     'Stamped into pixels and cannot be removed from the JPEG afterwards.',
                     'Retouch copies pass through the same step family and are stamped while the mode is on.',
                     'The other stock encode callers (0x1009xxxx family) pass through unstamped.'])
