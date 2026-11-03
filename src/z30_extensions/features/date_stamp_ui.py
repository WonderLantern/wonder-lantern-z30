# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Date stamp page: pick a format, cycle the color, or toggle the glow; all remembered on the SD card.

Row numbers equal the saved mode values, so records written before the extra
formats existed still restore the same choice. Color and glow share the style word.
"""
import struct
from ..assembly import constant as c, invoke as n, assemble
from ..date_stamp import COLORS, FORMATS, GLOW_FLAG, STYLE_MAX
from .date_stamp import COLOR_FIELD
from .menu_slots import page_slots

PAGE_INDEX = 964
FORMAT_LABELS = ('OFF', 'Y M D', 'Y M D H:M', 'M D Y', 'D M Y', 'D H:M')
COLOR_LABELS = ('Orange', 'Red', 'Yellow', 'White')
COLOR_ROW = len(FORMAT_LABELS)
GLOW_ROW = COLOR_ROW + 1
ROWS = GLOW_ROW + 1
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('stamp', ROWS)
MAGIC = 0x31544457  # WDT1
SIZES = dict(stamp_get=96, stamp_labels=448, stamp_open=160,
             stamp_action=384, stamp_save=640, stamp_restore=640, stamp_close=128)
if len(FORMAT_LABELS) != len(FORMATS) + 1 or len(COLOR_LABELS) != len(COLORS):
    raise ValueError('Date stamp labels must match its styles')


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in dict(title='Date stamp', color='Color', glow='Glow',
                                                      on='ON', off='OFF', empty='', mark='*').items()}
    labels = plan.alloc(struct.pack(f'<{ROWS}I', *(resources.alloc(s) for s in FORMAT_LABELS),
                                    strings['color'], strings['glow']))
    colors = plan.alloc(struct.pack(f'<{len(COLOR_LABELS)}I', *(resources.alloc(s) for s in COLOR_LABELS)))
    path = plan.alloc(b'A:\\WL-STAMP.CFG\0')
    call = lambda name: n(f[name])
    from .sound_settings import settings_code
    persistence = settings_code(f, path, state_get='stamp_get', close='stamp_close',
        magic=MAGIC, enabled_offset=COLOR_FIELD, selected_offset=0, fd_offset=4,
        enabled_max=STYLE_MAX, selected_max=COLOR_ROW - 1, load_sound=False, restore_enabled=True)
    codes = {new: persistence[old] for old, new in (
        ('save_settings', 'stamp_save'), ('restore_settings', 'stamp_restore'),
        ('settings_close', 'stamp_close'))}
    codes['stamp_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    codes['stamp_labels'] = f'''push {{r4-r7,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('stamp_get')}movs r4,#0;movs r6,#0;cmp r0,#0;beq ready;
        ldr r4,[r0];cmp r4,#{COLOR_ROW - 1};it hi;movhi r4,#0;
        ldr r6,[r0,#{COLOR_FIELD}];cmp r6,#{STYLE_MAX};it hi;movhi r6,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        cmp r5,#{COLOR_ROW};bne glow_mark;and r7,r6,#{GLOW_FLAG - 1};{c('r0',colors)}ldr r0,[r0,r7,lsl #2];b put;
        glow_mark:cmp r5,#{GLOW_ROW};bne format_mark;
        tst r6,#{GLOW_FLAG};{c('r0',strings['on'])}bne put;{c('r0',strings['off'])}b put;
        format_mark:{c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{ROWS};bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r7,pc}};'''
    codes['stamp_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('stamp_get')}ldr r4,[r0];cmp r4,#{COLOR_ROW - 1};it hi;movhi r4,#0;
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        STAMP_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # A format choice returns to the parent; the color and glow rows stay here so
    # the user can step through them. The list keeps showing the old value until
    # its widgets are rebuilt, so the page is reopened with focus back on that row.
    codes['stamp_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#{GLOW_ROW};bhi done;
        {call('stamp_get')}cmp r0,#0;beq done;
        cmp r5,#{COLOR_ROW};bhs style;str r5,[r0];b store;
        style:ldr r1,[r0,#{COLOR_FIELD}];cmp r1,#{STYLE_MAX};it hi;movhi r1,#0;
        cmp r5,#{GLOW_ROW};beq toggle_glow;
        and r2,r1,#{GLOW_FLAG};adds r1,#1;and r1,r1,#{GLOW_FLAG - 1};orr r1,r1,r2;b put_style;
        toggle_glow:eor r1,r1,#{GLOW_FLAG};
        put_style:str r1,[r0,#{COLOR_FIELD}];
        store:dmb ish;{call('stamp_save')}cmp r0,#1;beq saved;
        mov r0,r4;{call('show_error')}b done;
        saved:cmp r5,#{COLOR_ROW};bhs redraw;
        mov r0,r4;{call('page_return')}b done;
        redraw:{call('stamp_labels')}movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        mov r0,r4;STAMP_IDS;ldr r1,[r1,r5,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset+16, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack(f'<{ROWS}I', *identifiers))
    for name in ('stamp_open', 'stamp_action'):
        extension['codes'][name] = extension['codes'][name].replace('STAMP_IDS;', c('r1',ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['date_stamp_control'] = dict(
        default=0, page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        state_offset=extension['state_offset'], color_offset=extension['state_offset'] + COLOR_FIELD,
        settings_file='WL-STAMP.CFG', modes=list(FORMAT_LABELS), colors=list(COLOR_LABELS),
        color_row=COLOR_ROW, glow_row=GLOW_ROW, glow_flag=GLOW_FLAG,
        restore_policy='First direct-row open, custom-menu entry or shutter event after power-on; OFF before that',
        functions={k: hex(f[k]|1) for k in SIZES}, physical_verified=False)
