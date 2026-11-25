# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""AF frame colour switch (Enable / Disable), a row in the photo shooting menu,
saved to WL-AFCOL.CFG. Row 0 (Enable) is what a fresh state holds, so a camera
without the file keeps the new colours (v51; v38-v50 had no switch).

The stock style table 0x107277B0 stays untouched; the edited copy (af_frame_colour.EDITS)
lives in the owned tail. The stock lookup 0x1039C930 loads the table address with
`ldr r1,[pc,#0x248]` at 0x1039CAEA and indexes it with the next two instructions;
those 10 bytes jump to afcol_table, which asks afcol_choice (ensure + saved value),
picks the stock or the edited table, runs the two displaced adds, repeats the
displaced `cmp.w ip,#5` from 0x1039CAE4 (its flags are used at 0x1039CB04 and the
call clobbers them) and returns to 0x1039CAF4.
"""
import struct
from ...assembly import constant as c, invoke as n, assemble, jump
from ..menu.ui import list_top
from ..menu.slots import page_slots
from . import af_frame_colour as fc

PAGE_INDEX = 969   # 965 portrait, 966 NEF compression, 967 gyro e-VR, 968 3D tracking
ENABLE, DISABLE = 0, 1
LABELS = ('Enable', 'Disable')
ROWS = len(LABELS)
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('afcol', ROWS)
MAGIC = 0x31434657  # WFC1
TITLE = 'AF frame colour'
SIZES = dict(afcol_get=96, afcol_choice=96, afcol_labels=384, afcol_open=160,
             afcol_action=320, afcol_save=640, afcol_restore=640, afcol_close=128, afcol_table=96)
LOOKUP_SITE = 0x1039CAEA
LOOKUP_EXPECTED = bytes.fromhex('92 49 02 eb 42 03 03 eb c2 02')   # ldr r1,[pc,#0x248]; add.w r3,r2,r2,lsl #1; add.w r2,r3,r2,lsl #3
# the literal that ldr reads (thumb ldr literal: Align(pc + 4, 4) + imm8 * 4) must be the stock table
LOOKUP_LITERAL = ((LOOKUP_SITE + 4) & ~3) + LOOKUP_EXPECTED[0] * 4   # 0x1039CD34
LOOKUP_RETURN = 0x1039CAF4
TABLE_BYTES = 29 * fc.COLUMNS * 2


def edited_table(stock):
    table = bytearray(stock)
    for row, column, before, after in fc.EDITS:
        at = (row * fc.COLUMNS + column) * 2
        if struct.unpack_from('<H', table, at)[0] != before:
            raise ValueError(f'Unexpected AF frame style at row {row} column {column}')
        struct.pack_into('<H', table, at, after)
    return bytes(table)


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    if plan.image.read(LOOKUP_SITE, len(LOOKUP_EXPECTED)) != LOOKUP_EXPECTED:
        raise ValueError('Unexpected AF frame style lookup')
    if struct.unpack('<I', plan.image.read(LOOKUP_LITERAL, 4))[0] != fc.TABLE:
        raise ValueError(f'AF frame style lookup does not load {fc.TABLE:#x}')
    table = plan.alloc(edited_table(plan.image.read(fc.TABLE, TABLE_BYTES)))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in dict(title=TITLE, empty='', mark='*').items()}
    labels = plan.alloc(struct.pack(f'<{ROWS}I', *(resources.alloc(s) for s in LABELS)))
    path = plan.alloc(b'A:\\WL-AFCOL.CFG\0')
    call = lambda name: n(f[name])
    from ..sound.settings import settings_code
    persistence = settings_code(f, path, state_get='afcol_get', close='afcol_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=0, selected_max=ROWS - 1, load_sound=False)
    codes = {new: persistence[old] for old, new in (
        ('save_settings', 'afcol_save'), ('restore_settings', 'afcol_restore'),
        ('settings_close', 'afcol_close'))}
    codes['afcol_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    # Restores the saved choice on first use from any task; Enable without state.
    codes['afcol_choice'] = f'''push {{r4,lr}};{call('ensure')}
        {call('afcol_get')}movs r4,#{ENABLE};cmp r0,#0;beq done;
        ldr r4,[r0];cmp r4,#{ROWS - 1};it hi;movhi r4,#{ENABLE};
        done:mov r0,r4;pop {{r4,pc}};'''
    codes['afcol_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('afcol_get')}movs r4,#0;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#{ROWS - 1};it hi;movhi r4,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{ROWS};bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['afcol_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;{list_top(page_offset)}
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        AFCOL_IDS;ldr r1,[r1];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['afcol_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#{ROWS - 1};bhi done;
        {call('afcol_get')}cmp r0,#0;beq done;str r5,[r0];dmb ish;
        {call('afcol_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:mov r0,r4;{call('page_return')}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    # table lookup: r0 output struct, r2 state code, ip column must survive
    codes['afcol_table'] = f'''push {{r0,r2,r12,lr}};{call('afcol_choice')}cmp r0,#{ENABLE};
        {c('r1',fc.TABLE)}bne stock;{c('r1',table)}stock:pop {{r0,r2,r12,lr}};
        add.w r3,r2,r2,lsl #1;add.w r2,r3,r2,lsl #3;cmp.w r12,#5;
        ldr.w pc,={LOOKUP_RETURN | 1};'''
    plan.edit(LOOKUP_SITE, jump(LOOKUP_SITE, f['afcol_table'], len(LOOKUP_EXPECTED)),
              'AF frame colour: pick the stock or the edited style table', LOOKUP_EXPECTED)
    plan.afcol_choice = f['afcol_choice']
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + 16, codes=codes, table=table)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack(f'<{ROWS}I', *identifiers))
    extension['codes']['afcol_open'] = extension['codes']['afcol_open'].replace('AFCOL_IDS;', c('r1', ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['af_frame_colour_control'] = dict(
        default='enable', page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        state_offset=extension['state_offset'], settings_file='WL-AFCOL.CFG',
        selections={'0': 'enable (EXPEED 7 style colours)', '1': 'disable (stock)'},
        table=hex(extension['table']), lookup=hex(LOOKUP_SITE), choice=hex(f['afcol_choice'] | 1),
        restore_policy='Restored on first use from any task (afcol_choice runs ensure); Enable without state',
        functions={k: hex(f[k] | 1) for k in SIZES})
