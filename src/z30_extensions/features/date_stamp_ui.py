# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Date stamp mode page: OFF, date, or date and time, remembered on the SD card."""
import struct
from ..assembly import constant as c, invoke as n, assemble

PAGE_INDEX = 964
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = 126, 127, 128, 131, 134
MAGIC = 0x31544457  # WDT1
SIZES = dict(stamp_get=96, stamp_labels=320, stamp_open=160,
             stamp_action=256, stamp_save=640, stamp_restore=640, stamp_close=128)


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in
               dict(title='Date stamp', off='OFF', date='Date', both='Date and time', empty='', mark='*').items()}
    labels = plan.alloc(struct.pack('<3I', strings['off'], strings['date'], strings['both']))
    path = plan.alloc(b'A:\\WL-STAMP.CFG\0')
    call = lambda name: n(f[name])
    from .sound_settings import settings_code
    persistence = settings_code(f, path, state_get='stamp_get', close='stamp_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=0, selected_max=2, load_sound=False)
    codes = {new: persistence[old] for old, new in (
        ('save_settings', 'stamp_save'), ('restore_settings', 'stamp_restore'),
        ('settings_close', 'stamp_close'))}
    codes['stamp_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    codes['stamp_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('stamp_get')}movs r4,#0;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#2;it hi;movhi r4,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#3;bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['stamp_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('stamp_get')}ldr r4,[r0];cmp r4,#2;it hi;movhi r4,#0;
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        STAMP_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['stamp_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#2;bhi done;
        {call('stamp_get')}cmp r0,#0;beq done;str r5,[r0];dmb ish;
        {call('stamp_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:movs r0,#2;movw r1,#955;{n(0x1045f732)}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset+16, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack('<3I', *identifiers))
    extension['codes']['stamp_open'] = extension['codes']['stamp_open'].replace('STAMP_IDS;', c('r1',ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['date_stamp_control'] = dict(
        default=0, page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        state_offset=extension['state_offset'], settings_file='WL-STAMP.CFG',
        modes=['OFF', 'Date', 'Date and time'],
        restore_policy='First custom-menu entry or first shutter event after power-on; OFF before that',
        functions={k: hex(f[k]|1) for k in SIZES}, physical_verified=False)
