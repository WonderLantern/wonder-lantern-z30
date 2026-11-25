# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Show the focal length for every CPU lens in photo live view.

The idea follows the Z 5 extension (WonderLantern/wonder-lantern-z5,
docs/PORTRAIT_UI.md 12.4): the stock code already tracks the focal length for
every lens, but only shows it when the widget's "show" byte is 1, which is the
case for power-zoom lenses only.

Z30 C 1.20, found by disassembly and read over USB on the camera:
  0x1032AC3E  focal-length updater: focal = [0xC2016A68 + 0x37C], unit 0.1 mm
              (camera: 160 at 16 mm, 500 at 50 mm on a plain zoom lens);
              formats "%dmm" / "%d.%dmm" and sets widget 0x3B's text.
  0x10326238  gate(widget): 1 when any entry of the widget's descriptor has
              byte [1] == 1; descriptor = table[widget], table 0x1073F830,
              entry array at descriptor + 0x30 (Z 5: +0x2C), count at +0x2C.
The patch writes that byte (entry 0) just before the gate runs, so the stock
updater goes on to draw the text. Nothing else is changed. Always on in this
first build; the menu switch comes later.
"""
from ...assembly import assemble, constant as c, invoke as n, jump

SITE = 0x1032AC58
EXPECTED = bytes.fromhex('3b20' 'fbf7edfa' '0028')  # movs r0,#0x3b; bl 0x10326238; cmp r0,#0
RESUME = 0x1032AC60                                   # beq (flags from the replayed cmp)
GATE = 0x10326238
WIDGET = 0x3B
TABLE = 0x1073F830
FOCAL_VALUE = 0xC2016DE4
RAM_LO, RAM_HI = 0xC1800000, 0xC3000000


def code(entry):
    return assemble(f'''
        push {{r1-r3}};
        {c('r0', TABLE + WIDGET * 4)}ldr r0,[r0];
        {c('r1', RAM_LO)}cmp r0,r1;blo skip;{c('r1', RAM_HI)}cmp r0,r1;bhs skip;
        ldr r0,[r0,#0x30];
        {c('r1', RAM_LO)}cmp r0,r1;blo skip;{c('r1', RAM_HI)}cmp r0,r1;bhs skip;
        movs r1,#1;strb r1,[r0,#1];
        skip:
        pop {{r1-r3}};
        movs r0,#{WIDGET};
        {n(GATE)}
        cmp r0,#0;
        ldr.w pc,={RESUME | 1};
    ''', entry, 96)


def add(plan):
    entry = plan.address
    body = code(entry)
    plan.alloc(body)
    plan.edit(SITE, jump(SITE, entry, len(EXPECTED)), 'Show focal length for every CPU lens', EXPECTED)
    plan.features['focal_length'] = dict(
        site=hex(SITE), entry=hex(entry | 1), size=len(body), widget=hex(WIDGET),
        descriptor_table=hex(TABLE), value_address=hex(FOCAL_VALUE), unit='0.1 mm',
        behaviour='sets entry 0 show byte of widget 0x3B before the stock gate; always on',
        menu_switch=False, camera_verified=False)
