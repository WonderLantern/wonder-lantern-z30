# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Auto tracking box switch (Enable / Disable), a row in the photo shooting menu,
saved to WL-AARM.CFG. Row 0 (Enable) is what a fresh state holds, so a camera
without the file has the feature on. Derived from af_frame_colour_ui.py (same
persistence and page code, no table hook). The built-in auto_arm module asks
autoarm_choice through its get_property shim (auto_arm.py)."""
import struct
from ..assembly import constant as c, invoke as n, assemble
from .menu_slots import page_slots

PAGE_INDEX = 970   # 965 portrait, 966 NEF, 967 gyro e-VR, 968 3D tracking, 969 AF colour
ENABLE, DISABLE = 0, 1
LABELS = ('Enable', 'Disable')
ROWS = len(LABELS)
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('autoarm', ROWS)
MAGIC = 0x31414157  # WAA1
TITLE = 'Auto tracking box'
SIZES = dict(autoarm_get=96, autoarm_choice=96, autoarm_labels=384, autoarm_open=160,
             autoarm_action=320, autoarm_save=640, autoarm_restore=640, autoarm_close=128)


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in dict(title=TITLE, empty='', mark='*').items()}
    labels = plan.alloc(struct.pack(f'<{ROWS}I', *(resources.alloc(s) for s in LABELS)))
    path = plan.alloc(b'A:\\WL-AARM.CFG\0')
    call = lambda name: n(f[name])
    from .sound_settings import settings_code
    persistence = settings_code(f, path, state_get='autoarm_get', close='autoarm_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=0, selected_max=ROWS - 1, load_sound=False)
    codes = {new: persistence[old] for old, new in (
        ('save_settings', 'autoarm_save'), ('restore_settings', 'autoarm_restore'),
        ('settings_close', 'autoarm_close'))}
    codes['autoarm_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    # Restores the saved choice on first use from any task; Enable without state.
    codes['autoarm_choice'] = f'''push {{r4,lr}};{call('ensure')}
        {call('autoarm_get')}movs r4,#{ENABLE};cmp r0,#0;beq done;
        ldr r4,[r0];cmp r4,#{ROWS - 1};it hi;movhi r4,#{ENABLE};
        done:mov r0,r4;pop {{r4,pc}};'''
    codes['autoarm_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('autoarm_get')}movs r4,#0;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#{ROWS - 1};it hi;movhi r4,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{ROWS};bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['autoarm_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('autoarm_get')}ldr r4,[r0];cmp r4,#{ROWS - 1};it hi;movhi r4,#0;
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        AARM_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['autoarm_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#{ROWS - 1};bhi done;
        {call('autoarm_get')}cmp r0,#0;beq done;str r5,[r0];dmb ish;
        {call('autoarm_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:mov r0,r4;{call('page_return')}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    plan.autoarm_choice = f['autoarm_choice']
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + 16, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack(f'<{ROWS}I', *identifiers))
    extension['codes']['autoarm_open'] = extension['codes']['autoarm_open'].replace('AARM_IDS;', c('r1', ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['auto_arm_control'] = dict(
        default='enable', page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        state_offset=extension['state_offset'], settings_file='WL-AARM.CFG',
        selections={'0': 'enable (tracking box up by default)', '1': 'disable (stock)'},
        choice=hex(f['autoarm_choice'] | 1),
        restore_policy='Restored on first use from any task (autoarm_choice runs ensure); Enable without state',
        functions={k: hex(f[k] | 1) for k in SIZES})
