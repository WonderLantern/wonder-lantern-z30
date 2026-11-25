# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Gyro e-VR page: Standard / Enhanced / Native crop / OFF, remembered on the SD card.

Row numbers equal the saved value, and row 0 (Standard) is what a fresh state
holds, so a camera without WL-GYRO.CFG keeps the built-in behaviour. OFF stops
every gyro read and leaves the stock e-VR alone. Standard (RS) (rolling-shutter
correction) was removed on 2026-11-14 and the rows moved up, so the record magic
moved to WYG3: a WYG1 / WYG2 file of an older order is ignored and the page
starts at Standard.

The built-in stabilizer asks for the choice through gyro_choice (via the private
get_property key of evr_builtin), from the IMU task. gyro_choice first runs the
shared ensure(), so the saved choice is restored without opening the menu.
"""
import struct
from ...assembly import constant as c, invoke as n, assemble
from ..menu.ui import list_top
from ..menu.slots import page_slots

PAGE_INDEX = 967   # 965 portrait (7ba48a9), 966 NEF compression (d3f6a0c)
LABELS = ('Standard', 'Enhanced', 'Native crop', 'OFF')
STANDARD, ENHANCED, NATIVE, OFF = range(4)
ROWS = len(LABELS)
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('gyro', ROWS)
MAGIC = 0x33475957  # WYG3 (WYG1 / WYG2 = older row orders, ignored)
SIZES = dict(gyro_get=96, gyro_choice=96, gyro_labels=384, gyro_open=160,
             gyro_action=256, gyro_save=640, gyro_restore=640, gyro_close=128)


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in dict(title='Gyro e-VR', empty='', mark='*').items()}
    labels = plan.alloc(struct.pack(f'<{ROWS}I', *(resources.alloc(s) for s in LABELS)))
    path = plan.alloc(b'A:\\WL-GYRO.CFG\0')
    call = lambda name: n(f[name])
    from ..sound.settings import settings_code
    persistence = settings_code(f, path, state_get='gyro_get', close='gyro_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=0, selected_max=ROWS - 1, load_sound=False)
    codes = {new: persistence[old] for old, new in (
        ('save_settings', 'gyro_save'), ('restore_settings', 'gyro_restore'),
        ('settings_close', 'gyro_close'))}
    codes['gyro_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    # Restores the saved choice on first use (any task); Standard if no state.
    codes['gyro_choice'] = f'''push {{r4,lr}};{call('ensure')}
        {call('gyro_get')}movs r4,#{STANDARD};cmp r0,#0;beq done;
        ldr r4,[r0];cmp r4,#{ROWS - 1};it hi;movhi r4,#{STANDARD};
        done:mov r0,r4;pop {{r4,pc}};'''
    codes['gyro_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('gyro_get')}movs r4,#0;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#{ROWS - 1};it hi;movhi r4,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{ROWS};bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['gyro_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;{list_top(page_offset)}
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        GYRO_IDS;ldr r1,[r1];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # The stabilizer polls the choice from the IMU task, so a stored word is all
    # it needs; no native property changes here.
    codes['gyro_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#{ROWS - 1};bhi done;
        {call('gyro_get')}cmp r0,#0;beq done;str r5,[r0];dmb ish;
        {call('gyro_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:mov r0,r4;{call('page_return')}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    plan.gyro_choice = f['gyro_choice']
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + 16, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack(f'<{ROWS}I', *identifiers))
    extension['codes']['gyro_open'] = extension['codes']['gyro_open'].replace('GYRO_IDS;', c('r1', ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['gyro_evr_control'] = dict(
        default=LABELS[STANDARD], rows=list(LABELS), page_index=PAGE_INDEX,
        page_offset=extension['page_offset'], state_offset=extension['state_offset'],
        settings_file='WL-GYRO.CFG', choice=hex(f['gyro_choice'] | 1),
        restore_policy='First ensure(): custom menu, photo trigger, or the stabilizer asking from the IMU task',
        functions={k: hex(f[k] | 1) for k in SIZES}, physical_verified=False)
