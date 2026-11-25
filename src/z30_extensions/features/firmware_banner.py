# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Project name and copyright on Setup -> Firmware version (resident EG).

Z30 C 1.20, found by disassembly and checked in QEMU (docs/card-loader.md):
  0x104D05FA  page builder (sb = page 0xC1839680). Each firmware component
              that is present (C, LF, ...) gets a stock widget pair from the
              id table 0xC94/0xC95, 0xC96/0xC97 ... 0xC9C/0xC9D (at most 5)
              and two text slots, numbered in the same order (pair i uses
              slots 2i and 2i+1); the layout tables then place the pairs.
  0x104D075C  layout entry of that (non-0x111) path, r4 = component count.
              The pairs below r4 are placed from rows y 76..304; with 3 or
              fewer components the lowest row ends at y 256 and the Done row
              starts at y 322 (with a card and package, "Update" and "Delete
              update file" follow Done, so nothing may go below it).
The hook sets the last pair (0xC9C/0xC9D, slots 8/9: used only by a fifth
component) to two lines between the version rows and Done, before the stock
layout runs, and only when the count is 3 or less. Stock calls used:
  0x10454210(id, [sb+0xC]) -> widget, 0x10454EFE(text, slot, style) copies
  <= 50 bytes, 0x104546C6(widget, slot) binds, 0x104546E2(widget, 0, 1, 0,
  [sp]=0) as for the stock value text, 0x104546B0(widget, 1) shows;
  rect (x0, y0, x1, y1) at widget +0x10 and +0x98.
Style 47: font 0x16, size 24, centred; ASCII only (the CJK fonts lack some
ASCII, the text avoids those characters).
"""
import struct

from ..assembly import assemble, constant as c, invoke as n, jump

SITE = 0x104D075C
EXPECTED = bytes.fromhex('062c' '58db' '254e' '4ff00005')  # cmp r4,#6; blt; ldr r6,=; mov.w r5,#0
SMALL = 0x104D0812          # blt target: 5 or fewer components
LARGE = 0x104D0766          # after mov.w r5,#0 (bne uses the cmp r4,#6 flags)
LARGE_TABLE = 0x107533CC    # r6 on the LARGE path
WIDGET, SET_TEXT, BIND, ALIGN, SHOW = 0x10454210, 0x10454EFE, 0x104546C6, 0x104546E2, 0x104546B0
STYLE = 47
MAX_COMPONENTS = 3
LINES = (
    (0xC9C, 8, b'Wonder Lantern Z30', (72, 258, 712, 288)),
    (0xC9D, 9, b'(c) 2026 CaCl2 (GitHub: RealCaCl2)', (72, 288, 712, 318)),
)
UNSUPPORTED = set(b'$&\',<=@QY\\^_`jqw{|}~?')   # missing from the stock CJK fonts' ASCII
SIZES = dict(hook=64, show=160)


def add(plan):
    if plan.image.read(SITE, len(EXPECTED)) != EXPECTED:
        raise ValueError('Unexpected stock bytes at the firmware page layout entry')
    table = b''
    for wid, slot, text, rect in LINES:
        if len(text) >= 50 or UNSUPPORTED & set(text):
            raise ValueError(f'Firmware page text cannot be shown: {text!r}')
        table += struct.pack('<HHI4H', wid, slot, plan.alloc(text + b'\0'), *rect)
    table = plan.alloc(table)
    f = {name: plan.alloc(bytes(size)) for name, size in SIZES.items()}
    plan.put(f['show'], assemble(f'''
        push {{r4-r7,lr}}; sub sp,#12; mov r7,r0; {c('r6', table)} movs r5,#{len(LINES)};
        next: ldrh r0,[r6]; ldr r1,[r7,#0xc]; {n(WIDGET)} movs r4,r0; beq skip;
        ldr r0,[r6,#4]; ldrh r1,[r6,#2]; movs r2,#{STYLE}; {n(SET_TEXT)}
        mov r0,r4; ldrh r1,[r6,#2]; {n(BIND)}
        movs r0,#0; str r0,[sp]; mov r0,r4; movs r1,#0; movs r2,#1; movs r3,#0; {n(ALIGN)}
        ldr r0,[r6,#8]; ldr r1,[r6,#12]; str r0,[r4,#0x10]; str r1,[r4,#0x14];
        str r0,[r4,#0x98]; str r1,[r4,#0x9c];
        mov r0,r4; movs r1,#1; {n(SHOW)}
        skip: adds r6,#16; subs r5,#1; bne next;
        add sp,#12; pop {{r4-r7,pc}};
    ''', f['show'], SIZES['show']))
    plan.put(f['hook'], assemble(f'''
        cmp r4,#{MAX_COMPONENTS}; bhi layout; mov r0,r9; {n(f['show'])}
        layout: cmp r4,#6; blt small;
        {c('r6', LARGE_TABLE)} mov.w r5,#0; cmp r4,#6; ldr.w pc,={LARGE | 1};
        small: ldr.w pc,={SMALL | 1};
    ''', f['hook'], SIZES['hook']))
    plan.edit(SITE, jump(SITE, f['hook'], len(EXPECTED)), 'firmware page banner', expected=EXPECTED)
    plan.features['firmware_banner'] = dict(site=hex(SITE), style=STYLE, max_components=MAX_COMPONENTS,
                                            lines=[t.decode() for _, _, t, _ in LINES],
                                            functions={k: hex(v) for k, v in f.items()})
    return f
