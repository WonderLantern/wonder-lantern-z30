# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
r"""Handheld page: a Game Boy emulator inside the card file (branch experimental).

A showcase of how far a card file stretches: the whole emulator (Peanut-GB plus
the camera glue, modules/handheld/handheld.c) is one C payload in WL.PATCH, with
no change to the resident EG. Rows:

  0 Play      load A:\Z30\GAME.GB (and GAME.SAV) once, then run it full screen;
              without GAME.GB the built-in demo runs
  1 Status    payload status (errors, or fps and CPU load of the last run)
  2 Keys      key diagnostics of the last run (raw / event codes, release source)
  3 Save      write the cart RAM to A:\Z30\GAME.SAV

The game paints through the last row's composite widget (stock painter
0x104B3855, as in the Doom port), drawn after the other rows.

While the game runs it owns the page: the multi selector is the D-pad, OK is A,
touch zones left and right of the picture give SELECT / B and START / A / B.
MENU (or anything that leaves the page) pauses; Play resumes.
"""
import struct
from ...assembly import constant as c, invoke as n, assemble
from ..menu.ui import list_top
from ..menu.slots import BLOCKS
from ..menu.layout import EMPTY_SLOT
from . import handheld_payload as payload

PAGE_INDEX = 976   # 975 film grain
ROWS = 4
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT = BLOCKS['handheld'][0], BLOCKS['handheld'][0] + 1, BLOCKS['handheld'][0] + 2
STATUS_SLOT, KEYS_SLOT = BLOCKS['handheld_values'][0], BLOCKS['handheld_values'][0] + 1
MARK_SLOT = CURRENT_SLOT = STATUS_SLOT
# Per row: the value column (None: label only).
VALUE_SLOTS = (None, STATUS_SLOT, KEYS_SLOT, None)
TITLE = ('Handheld', ('掌机', '掌機'))
LABELS = (('Play GAME.GB', ('开始游戏', '開始遊戲')),
          ('Status', ('状态', '狀態')),
          ('Keys', ('按键', '按鍵')),
          ('Save game', ('保存进度', '儲存進度')))
SIZES = dict(handheld_labels=256, handheld_open=128, handheld_action=128)
CMD_STATUS, CMD_ACTION, CMD_KEYS = 0, 1, 2


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    f['handheld_payload'] = plan.alloc(payload.CODE)
    page_offset, state_offset = allocation_size, allocation_size + 44
    title = resources.alloc(*TITLE)
    labels = [resources.alloc(*label) for label in LABELS]
    call = lambda name: n(f[name])
    run = lambda cmd: f"movs r0,#{cmd};movs r1,#0;movs r2,#0;{call('handheld_payload')}"
    codes = {}
    codes['handheld_labels'] = f'''push {{r4,lr}};
        {c('r0',title)}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',title)}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        ''' + ''.join(f"{c('r0',label)}movs r1,#{LABEL_SLOT + i};movs r2,#0;{call('text')}"
                      for i, label in enumerate(labels)) + f'''
        {run(CMD_STATUS)}movs r1,#{STATUS_SLOT};movs r2,#0;{call('text_ascii')}
        {run(CMD_KEYS)}movs r1,#{KEYS_SLOT};movs r2,#0;{call('text_ascii')}
        movs r0,#0;pop {{r4,pc}};'''
    codes['handheld_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;{list_top(page_offset)}
        {call('handheld_labels')}movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # r0 = the page object; the payload takes over the page when Play starts the game.
    codes['handheld_action'] = f'''push {{r4,lr}};mov r4,r0;
        ldrh r2,[r4,#22];ldrh r1,[r4,#24];adds r2,r2,r1;
        movs r0,#{CMD_ACTION};mov r1,r4;{call('handheld_payload')}
        mov r0,r4;{call('render')}
        {c('r0',0x10453ac3)}movs r1,#3;movs r2,#7;{n(0x10453fac)}
        movs r0,#0;pop {{r4,pc}};'''
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + 16, codes=codes)


def finish(plan, f, extension, identifiers):
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['handheld'] = dict(
        page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        payload=hex(f['handheld_payload'] | 1), payload_bytes=payload.SIZE, payload_sha256=payload.SHA256,
        rom='Z30/GAME.GB', save='Z30/GAME.SAV', rows=[label for label, _ in LABELS],
        controls=list(identifiers), functions={k: hex(f[k] | 1) for k in SIZES},
        physical_verified=False)
