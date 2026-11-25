# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""3D tracking switch (Disable / Enable), saved to WL-3DTRK.CFG; read by tracking3d.py.

The Enable row names the prerequisite (review of PR 36): the handover only works
with the AF-area mode Auto-area AF (people) or (animals) (tracking3d.py, revision 6)."""
import struct
from ..assembly import constant as c, invoke as n, assemble
from .menu_ui import list_top
from .menu_slots import page_slots

PAGE_INDEX = 968   # 965 portrait, 966 NEF compression, 967 gyro e-VR
OFF, ENABLE = 0, 1
LABELS = ('Disable', 'Enable (people/animals)')
ROWS = len(LABELS)
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('trk3d', ROWS)
from .tracking3d_boxes import SIZE as BOX_SIZE, HOOK_SIZE
MAGIC = 0x31544C57  # WLT1
TITLE = '3D tracking'
SIZES = dict(trk3d_get=96, trk3d_choice=96, trk3d_labels=384, trk3d_open=160,
             trk3d_action=320, trk3d_save=640, trk3d_restore=640, trk3d_close=128)


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    from . import tracking3d_boxes as tb
    f['trk3d_boxes'] = plan.alloc(bytes(tb.SIZE))
    f['trk3d_render'] = plan.alloc(bytes(tb.HOOK_SIZE))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in dict(title=TITLE, empty='', mark='*').items()}
    labels = plan.alloc(struct.pack(f'<{ROWS}I', *(resources.alloc(s) for s in LABELS)))
    path = plan.alloc(b'A:\\WL-3DTRK.CFG\0')
    call = lambda name: n(f[name])
    from .sound_settings import settings_code
    persistence = settings_code(f, path, state_get='trk3d_get', close='trk3d_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=0, selected_max=ROWS - 1, load_sound=False)
    codes = {new: persistence[old] for old, new in (
        ('save_settings', 'trk3d_save'), ('restore_settings', 'trk3d_restore'),
        ('settings_close', 'trk3d_close'))}
    codes['trk3d_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    # Restores the saved choice on first use from any task (as gyro_choice); OFF without state.
    codes['trk3d_choice'] = f'''push {{r4,lr}};{call('ensure')}
        {call('trk3d_get')}movs r4,#{OFF};cmp r0,#0;beq done;
        ldr r4,[r0];cmp r4,#{ROWS - 1};it hi;movhi r4,#{OFF};
        done:mov r0,r4;pop {{r4,pc}};'''
    codes['trk3d_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('trk3d_get')}movs r4,#0;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#{ROWS - 1};it hi;movhi r4,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{ROWS};bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['trk3d_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;{list_top(page_offset)}
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        TRK3D_IDS;ldr r1,[r1];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['trk3d_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#{ROWS - 1};bhi done;
        {call('trk3d_get')}cmp r0,#0;beq done;str r5,[r0];dmb ish;
        {call('trk3d_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:mov r0,r4;{call('page_return')}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    # subject boxes in the stock UI renderer, after its canvas clear (screen 1 only)
    from ..assembly import jump
    codes['trk3d_boxes'] = tb.source(f)
    codes['trk3d_render'] = tb.hook(f)
    plan.edit(tb.RENDER_SITE, jump(tb.RENDER_SITE, f['trk3d_render'], tb.RENDER_SIZE),
              '3D tracking: subject boxes in the UI renderer after the canvas clear', bytes.fromhex(tb.RENDER_EXPECTED))
    plan.trk3d_choice = f['trk3d_choice']
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + 16 + tb.STATE_BYTES, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack(f'<{ROWS}I', *identifiers))
    extension['codes']['trk3d_open'] = extension['codes']['trk3d_open'].replace('TRK3D_IDS;', c('r1', ids))
    for name, source in extension['codes'].items():
        size = SIZES.get(name) or {'trk3d_boxes': BOX_SIZE, 'trk3d_render': HOOK_SIZE}[name]
        plan.put(f[name], assemble(source, f[name], size))
    plan.features['tracking3d_control'] = dict(
        default=OFF, page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        state_offset=extension['state_offset'], settings_file='WL-3DTRK.CFG',
        selections={'0': 'disable (stock)', '1': 'enable'},
        boxes=hex(f['trk3d_boxes'] | 1), boxes_hook='UI renderer after the canvas clear (0x104785d8)',
        choice=hex(f['trk3d_choice'] | 1),
        restore_policy='Restored on first use from any task (trk3d_choice runs ensure); OFF without state',
        functions={k: hex(f[k] | 1) for k in SIZES})
