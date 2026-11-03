# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Owned bitrate selector and versioned SD preferences for native movie records."""
import struct
from ..assembly import constant as c, invoke as n, assemble, jump
from .menu_slots import page_slots
from .sound_layout import RESULT

PAGE_INDEX = 960
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('video', 3)
MAGIC = 0x31424C57  # WLB1: magic, profile, reserved zero, complemented XOR.
PROFILES = ('Stock', 'Balanced', 'High quality')
BALANCED_MBPS = {
    '4K 30p HIGH': (168, 140), '4K 25p HIGH': (168, 140),
    '4K 24p HIGH': (168, 140),
    '1080 60p HIGH': (84, 70), '1080 50p HIGH': (84, 70),
    '1080 30p HIGH': (48, 40), '1080 25p HIGH': (48, 40),
    '1080 24p HIGH': (48, 40),
}
SIZES = dict(bitrate_get=96, bitrate_labels=320, bitrate_open=160,
             bitrate_action=256, bitrate_save=640, bitrate_restore=640,
             bitrate_close=128, bitrate_copy=320)


def prepare(plan, f, resources, allocation_size):
    from .bitrate_slowmo_uncap import MODE_TABLE, RECORD, RATES, RECORDS, BITRATES_MBPS, rates
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset = allocation_size
    state_offset = page_offset + 44
    strings = {k: resources.alloc(v) for k, v in
               dict(title='Video bitrate', stock=PROFILES[0], balanced=PROFILES[1],
                    high=PROFILES[2], empty='', mark='*').items()}
    text_table = plan.alloc(struct.pack('<3I', *(strings[k] for k in ('stock','balanced','high'))))
    tables = bytearray()
    for overrides in ({}, BALANCED_MBPS, BITRATES_MBPS):
        pairs = [plan.image.read(MODE_TABLE + i*RECORD + RATES, 8) for i in range(30)]
        for mode, pair in overrides.items():
            if not 0 < pair[1] <= pair[0] <= 400:
                raise ValueError('Bitrate profile exceeds encoder range')
            pairs[RECORDS[mode]] = rates(pair)
        tables.extend(b''.join(pairs))
    pairs_address = plan.alloc(tables)
    path = plan.alloc(b'A:\\WL-VIDEO.CFG\0')
    call = lambda name: n(f[name])
    # Reuse the established file-service contract, with independent FD/state
    # and an independent record, so changing audio cannot overwrite video.
    from .sound_settings import settings_code
    persistence = settings_code(f, path, state_get='bitrate_get', close='bitrate_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=0, selected_max=2, load_sound=False)
    codes = {new:persistence[old] for old,new in (
        ('save_settings','bitrate_save'),('restore_settings','bitrate_restore'),
        ('settings_close','bitrate_close'))}
    codes['bitrate_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    codes['bitrate_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('bitrate_get')}movs r4,#2;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#2;it hi;movhi r4,#2;
        ready:movs r5,#0;
        loop:{c('r0',text_table)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#3;bne loop;
        {c('r0',text_table)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['bitrate_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('bitrate_get')}ldr r4,[r0];cmp r4,#2;it hi;movhi r4,#2;
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        BITRATE_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # IDs are installed after the stock menu's occupied IDs have been scanned.
    codes['bitrate_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#2;bhi done;
        {call('bitrate_get')}cmp r0,#0;beq done;str r5,[r0];dmb ish;
        {call('bitrate_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:mov r0,r4;{call('page_return')}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    # Reproduce the native record getter, then replace only the two rate words.
    # Width, height, timing and GOP come from the native table unchanged.
    codes['bitrate_copy'] = f'''push {{r3-r7,lr}};mov r4,r1;mov r1,sp;{n(0x1035566c)}
        ldrb r5,[sp];movs r2,#28;{c('r1',MODE_TABLE)}mul r0,r5,r2;add r1,r0;
        mov r0,r4;{n(0x10199bdc,thumb=False)}
        {call('ensure')}movs r6,#2;cmp r0,#0;beq choose;
        {c('r1',state_offset)}ldr r6,[r0,r1];cmp r6,#2;it hi;movhi r6,#2;
        choose:cmp r5,#29;bhi done;movs r0,#240;mul r0,r6,r0;
        {c('r1',pairs_address)}add r1,r0;add r1,r1,r5,lsl #3;
        ldr r0,[r1];ldr r2,[r1,#4];str r0,[r4,#20];str r2,[r4,#24];
        done:mov r0,r4;pop {{r3-r7,pc}};'''
    expected = bytes.fromhex('38b50c466946fff787ff')
    actual = plan.image.read(0x10355754,10)
    # Exact digest binding is supplemented by a displaced-window guard.
    if actual != expected:
        raise ValueError(f'Unexpected movie record getter: {actual.hex()}')
    plan.edit(0x10355754,jump(0x10355754,f['bitrate_copy'],10),'Runtime bitrate record getter',expected)
    return dict(page_offset=page_offset,state_offset=state_offset,allocation_size=state_offset+16,
                strings=strings,codes=codes,table=pairs_address)


def finish(plan, f, extension, identifiers):
    ids=plan.alloc(struct.pack('<3I',*identifiers))
    extension['codes']['bitrate_open']=extension['codes']['bitrate_open'].replace('BITRATE_IDS;',c('r1',ids))
    for name,source in extension['codes'].items():
        plan.put(f[name],assemble(source,f[name],SIZES[name]))
    plan.features['bitrate_profiles']=dict(
        names=list(PROFILES),default=2,balanced_mbps=BALANCED_MBPS,
        functions={k:hex(f[k]|1) for k in SIZES},page_index=PAGE_INDEX,
        page_offset=extension['page_offset'],state_offset=extension['state_offset'],
        rates_table=hex(extension['table']),settings_file='WL-VIDEO.CFG',
        record_getter='0x10355754',scope='Selected profile is copied to subsequent native movie configurations; no active encoder writes',
        physical_verified=False)
