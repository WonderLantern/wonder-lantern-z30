# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Independent photo eligibility switch; native movie settings remain shared."""
import struct
from ..assembly import constant as c, invoke as n, assemble
from .menu_slots import page_slots

PAGE_INDEX = 963
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('zebra', 2)
MAGIC = 0x315A4C57  # WLZ1
SIZES = dict(zebra_get=96, zebra_labels=320, zebra_open=160,
             zebra_action=384, zebra_save=640, zebra_restore=640, zebra_close=128)


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in
               dict(title='Photo zebra', off='OFF', on='ON', empty='', mark='*').items()}
    labels = plan.alloc(struct.pack('<2I', strings['off'], strings['on']))
    path = plan.alloc(b'A:\\WL-ZEBRA.CFG\0')
    call = lambda name: n(f[name])
    from .sound_settings import settings_code
    persistence = settings_code(f, path, state_get='zebra_get', close='zebra_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=0, selected_max=1, load_sound=False)
    codes = {new: persistence[old] for old, new in (
        ('save_settings', 'zebra_save'), ('restore_settings', 'zebra_restore'),
        ('settings_close', 'zebra_close'))}
    codes['zebra_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    codes['zebra_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('zebra_get')}movs r4,#0;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#1;it hi;movhi r4,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#2;bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['zebra_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('zebra_get')}ldr r4,[r0];cmp r4,#1;it hi;movhi r4,#0;
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        ZEBRA_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # Notify the original pattern dependency while retaining the user's final
    # native pattern. This schedules native recalculation on menu return.
    codes['zebra_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#1;bhi done;
        {call('zebra_get')}cmp r0,#0;beq done;str r5,[r0];dmb ish;
        movw r0,#0x1182;{n(0x10465984)}mov r6,r0;cmp r6,#2;bhi save;
        movw r0,#0x1182;movs r1,#0;cmp r6,#0;it eq;moveq r1,#1;{n(0x10465990)}
        movw r0,#0x1182;mov r1,r6;{n(0x10465990)}
        save:{call('zebra_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:mov r0,r4;{call('page_return')}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset+16, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack('<2I', *identifiers))
    extension['codes']['zebra_open'] = extension['codes']['zebra_open'].replace('ZEBRA_IDS;', c('r1',ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['photo_zebra_control'] = dict(
        default=0, page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        state_offset=extension['state_offset'], settings_file='WL-ZEBRA.CFG',
        restore_policy='First custom-menu initialization; OFF before initialization',
        functions={k: hex(f[k]|1) for k in SIZES}, physical_verified=False)
