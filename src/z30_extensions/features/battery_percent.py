# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Battery icon with the charge in percent in photo live view.

The idea and the icon follow the Z 5 extension (WonderLantern/wonder-lantern-z5,
docs/PORTRAIT_UI.md 12.4). Z30 C 1.20, found by disassembly, a real draw log and
a USB readout on the camera:
  0xC236CE44  battery block (a 0x50-byte copy is what the unreachable "battery
              information" page of 0x104CE76E shows). Camera readout with
              three batteries: +0x18 stays 100, so it is NOT the live charge;
              +0x1C = 8484, 8259, 8152 (full, part-used, lower) follows the
              battery and is the pack voltage in mV.
  0x103BC4D0  `bl 0x10477C9C` in the stock image-draw wrapper (0x103BC43E):
              r0 -> the 0x5C-byte image descriptor on the stack, +0x14/+0x16
              width/height, +0x18/+0x1A x/y. All stock images pass here.
The draw log shows the battery as two image draws at (2, 396), 60 x 32, render
target 2 (screen layer), after two clears of the same rectangle.

The hook replaces that call. An image of 60 x 32 at x < 80, y 392..429 is not
drawn; instead the module clears the box and draws a frame, a translucent body
and the charge with 3 x 5 glyphs (white, red at 20 % and below) through the stock
rectangle and clear entries. Every other image goes to the stock entry unchanged,
as does the battery icon when the voltage is outside 5000..9500 mV. The percent
is an estimate: the pack voltage (+0x1C) goes through a piecewise-linear
open-circuit curve of a 2-cell Li-ion pack (CURVE below; typical cell curve,
offset so that the 8484 mV of a freshly charged pack reads 100). It sags under
load and the stock firmware has its own idea of the level, so the value is
approximate. Drawing is idempotent
(clear first), so the second stock call just repeats it. The charge is read at
each draw; the stock redraws the icon whenever the shooting page repaints.

Always on in this first build. In portrait the stock rectangle entry is the one
portrait-ui hooks; combining the two is not yet verified.
"""
import struct
from ..assembly import assemble, constant as c, invoke as n, jump

SITE = 0x103BC4D0
EXPECTED = bytes.fromhex('bbf0e4fb' '17b0' '00bd')  # bl IMAGE; add sp,#0x5c; pop {pc}
IMAGE = 0x10477C9C
RECT = 0x1047743C     # r0 -> x, y-up, w, h, rgba, 0, 0
CLEAR = 0x1047797E    # r0 -> x, y-up, w, h
VOLTAGE = 0xC236CE60  # battery block 0xC236CE44 + 0x1C, pack voltage in mV
VOLT_MIN, VOLT_MAX = 5000, 9500
# (mV, percent), descending; typical Li-ion cell curve x 2 + 80 mV.
CURVE = ((8480, 100), (8380, 95), (8300, 90), (8240, 85), (8120, 80), (8040, 75), (7980, 70),
         (7900, 65), (7820, 60), (7780, 55), (7760, 50), (7720, 45), (7680, 40), (7660, 35),
         (7620, 30), (7580, 25), (7540, 20), (7500, 15), (7460, 10), (7300, 5), (6620, 0))
WIDTH, HEIGHT = 60, 32
X_LIMIT, Y_FIRST, Y_SPAN = 80, 392, 38
WHITE, RED, BODY = 0xFFFFFFFF, 0xE62E2EFF, 0x80
LOW_PERCENT = 20
# 3 x 5 glyphs, row 0 in bits 14-12; digits 0-9, then '%' (as on the Z 5).
GLYPHS = (0x7B6F, 0x2C97, 0x73E7, 0x73CF, 0x5BC9, 0x79CF, 0x79EF, 0x7249, 0x7BEF, 0x7BCF, 0x52A5)


def runs():
    """Per 3-bit row pattern: up to two runs as bytes start1, len1, start2, len2 (bit 2 is the left pixel)."""
    table = []
    for bits in range(8):
        found, k = [], 0
        while k < 3:
            if bits & (4 >> k):
                length = 1
                while k + length < 3 and bits & (4 >> (k + length)):
                    length += 1
                found.append((k, length))
                k += length
            else:
                k += 1
        found += [(0, 0)] * (2 - len(found))
        table.append(struct.pack('4B', found[0][0], found[0][1], found[1][0], found[1][1]))
    return b''.join(table)


def code(entry, glyphs, run_table, curve, size=900):
    return assemble(f'''
        push {{r4-r11,lr}};
        sub sp,#12;
        mov r4,r0;
        ldrh r1,[r4,#0x14];cmp r1,#{WIDTH};bne stock;
        ldrh r1,[r4,#0x16];cmp r1,#{HEIGHT};bne stock;
        ldrsh r6,[r4,#0x18];ldrsh r7,[r4,#0x1a];
        cmp r6,#{X_LIMIT};bhs stock;
        sub r1,r7,#{Y_FIRST};cmp r1,#{Y_SPAN};bhs stock;
        bl estimate;cmp r5,#100;bhi stock;
        {c('r8', CLEAR | 1)}mov.w r10,#0;
        mov r0,r6;mov r1,r7;movs r2,#{WIDTH};movs r3,#{HEIGHT};bl rect;
        {c('r8', RECT | 1)}
        {c('r10', WHITE)}cmp r5,#{LOW_PERCENT};bhi colour_ok;{c('r10', RED)}
        colour_ok:
        mov r9,r10;
        mov.w r10,#{BODY};adds r0,r6,#2;adds r1,r7,#4;movs r2,#52;movs r3,#24;bl rect;
        mov r10,r9;
        mov r0,r6;adds r1,r7,#2;movs r2,#56;movs r3,#2;bl rect;
        mov r0,r6;adds r1,r7,#28;movs r2,#56;movs r3,#2;bl rect;
        mov r0,r6;adds r1,r7,#4;movs r2,#2;movs r3,#24;bl rect;
        adds r0,r6,#54;adds r1,r7,#4;movs r2,#2;movs r3,#24;bl rect;
        adds r0,r6,#56;adds r1,r7,#11;movs r2,#4;movs r3,#10;bl rect;
        cmp r5,#100;blo two;
        movs r0,#1;strb r0,[sp];movs r0,#0;strb r0,[sp,#1];strb r0,[sp,#2];movs r1,#3;b percent;
        two:
        movs r0,#10;udiv r1,r5,r0;mls r2,r1,r0,r5;movs r3,#0;
        cmp r1,#0;beq ones;strb r1,[sp];movs r3,#1;
        ones:
        add r0,sp,r3;strb r2,[r0];adds r3,#1;mov r1,r3;
        percent:
        add r0,sp,r1;movs r2,#10;strb r2,[r0];adds r1,#1;
        mov r9,r1;
        movs r0,#12;mul r0,r0,r1;subs r0,#3;lsrs r0,r0,#1;rsb r0,r0,#28;add r11,r6,r0;
        movs r4,#0;
        next_glyph:
        cmp r4,r9;bhs leave;
        add r1,sp,r4;ldrb r0,[r1];
        {c('r1', glyphs)}ldrh r0,[r1,r0,lsl #1];
        mov r1,r11;bl glyph;
        add.w r11,r11,#12;adds r4,#1;b next_glyph;
        leave:
        add sp,#12;pop {{r4-r11,lr}};add sp,#0x5c;pop {{pc}};
        stock:
        mov r0,r4;add sp,#12;pop {{r4-r11,lr}};
        {n(IMAGE)}
        add sp,#0x5c;pop {{pc}};

        estimate:
        {c('r0', VOLTAGE)}ldr r0,[r0];
        movw r1,#{VOLT_MIN};cmp r0,r1;blo bad;movw r1,#{VOLT_MAX};cmp r0,r1;bhi bad;
        {c('r1', curve)}ldrh r2,[r1];cmp r0,r2;bhs full;
        movs r3,#1;
        scan:
        add.w r12,r1,r3,lsl #2;ldrh r2,[r12];cmp r0,r2;bhs found;
        adds r3,#1;cmp r3,#{len(CURVE)};blo scan;
        movs r5,#0;bx lr;
        found:
        ldrh r5,[r12,#2];ldrh r3,[r12,#-4];ldrh r12,[r12,#-2];
        subs r0,r0,r2;sub.w r12,r12,r5;mul r0,r0,r12;subs r3,r3,r2;udiv r0,r0,r3;adds r5,r5,r0;bx lr;
        full:
        movs r5,#100;bx lr;
        bad:
        movs r5,#101;bx lr;

        rect:
        push {{lr}};sub sp,#28;
        str r0,[sp];
        mov.w r12,#480;sub.w r12,r12,r1;sub.w r12,r12,r3;str r12,[sp,#4];
        str r2,[sp,#8];str r3,[sp,#12];str r10,[sp,#16];
        movs r0,#0;str r0,[sp,#20];str r0,[sp,#24];
        mov r0,sp;blx r8;
        add sp,#28;pop {{pc}};

        glyph:
        push {{r4-r6,r11,lr}};sub sp,#4;
        mov r5,r0;mov r11,r1;movs r4,#0;
        row:
        rsb r0,r4,#4;add.w r0,r0,r0,lsl #1;lsr.w r0,r5,r0;and r0,r0,#7;
        {c('r1', run_table)}ldr r6,[r1,r0,lsl #2];
        ubfx r2,r6,#8,#8;cmp r2,#0;beq second;
        ubfx r0,r6,#0,#8;add.w r0,r0,r0,lsl #1;add r0,r11;
        add.w r1,r4,r4,lsl #1;add r1,r7;adds r1,#8;
        add.w r2,r2,r2,lsl #1;movs r3,#3;bl rect;
        second:
        ubfx r2,r6,#24,#8;cmp r2,#0;beq next_row;
        ubfx r0,r6,#16,#8;add.w r0,r0,r0,lsl #1;add r0,r11;
        add.w r1,r4,r4,lsl #1;add r1,r7;adds r1,#8;
        add.w r2,r2,r2,lsl #1;movs r3,#3;bl rect;
        next_row:
        adds r4,#1;cmp r4,#5;blo row;
        add sp,#4;pop {{r4-r6,r11,pc}};
    ''', entry, size)


def add(plan):
    glyphs = plan.alloc(struct.pack('<11H', *GLYPHS))
    run_table = plan.alloc(runs())
    curve = plan.alloc(b''.join(struct.pack('<2H', mv, pct) for mv, pct in CURVE))
    entry = plan.address
    body = code(entry, glyphs, run_table, curve)
    plan.alloc(body)
    plan.edit(SITE, jump(SITE, entry, len(EXPECTED)), 'Battery icon with percent', EXPECTED)
    plan.features['battery_percent'] = dict(
        site=hex(SITE), entry=hex(entry | 1), size=len(body), voltage_address=hex(VOLTAGE), voltage_range_mv=[VOLT_MIN, VOLT_MAX], curve=[list(p) for p in CURVE],
        percent_is_estimate=True,
        icon=dict(width=WIDTH, height=HEIGHT, x_limit=X_LIMIT, y_first=Y_FIRST, y_span=Y_SPAN),
        entries=dict(image=hex(IMAGE), rect=hex(RECT), clear=hex(CLEAR)),
        low_percent=LOW_PERCENT, behaviour='replaces the stock battery image draw with a voltage-based percent estimate; always on',
        menu_switch=False, camera_verified=False)
