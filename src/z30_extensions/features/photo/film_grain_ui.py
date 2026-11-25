# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Film grain page: grain style and strength, remembered on the SD card.

Rows: Style (OFF / FUJI400 / HP5) and Strength (1..6) step to the next value in place.

State after the page object (44 bytes), in words: the payload's ST_* layout
(modules/film_grain/film_grain.c): +0 style, +4 settings fd, +8 strength code
(0 = strength 3, then 4, 5, 6, 1, 2). The payload also keeps its last result
there (+12 result, +16 ms, +20 count, +24 PAR, +28 calls, +32 size, +36 site,
+40..63 ASCII text); the page does not show it (2026-11-25: no diagnostics in the
release menu), a memory read does. WL-FILM.CFG keeps style and strength code.
"""
import struct
from ...assembly import constant as c, invoke as n, assemble
from ..menu.ui import list_top
from ..menu.slots import page_slots
from ...host.film_grain import STRENGTH_ORDER, STRENGTHS, STYLES

PAGE_INDEX = 975   # 970-973 category pages, 974 status page
ROWS = 2
STYLE_ROW, STRENGTH_ROW = range(ROWS)
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('grain', ROWS)
MAGIC = 0x31464C57  # WLF1
STRENGTH_FIELD = 8
TEXT_FIELD = 40
STATE_BYTES = 64
SIZES = dict(grain_get=96, grain_labels=384, grain_open=160,
             grain_action=384, grain_save=640, grain_restore=640, grain_close=128)
# 胶/颗/粒 are not stock glyphs: card builds draw them from the resident common
# characters; a flash build with this page adds them to its fonts (menu_framework
# sets plan.feature_labels), builds without it keep their fonts unchanged.
TITLE = ('胶片颗粒', '膠片顆粒')


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    title = resources.alloc('Film grain', TITLE)
    strings = {k: resources.alloc(v) for k, v in dict(style='Style', strength='Strength').items()}
    labels = plan.alloc(struct.pack(f'<{ROWS}I', strings['style'], strings['strength']))
    styles = plan.alloc(struct.pack(f'<{len(STYLES)}I', *(resources.alloc(s) for s in STYLES)))
    strengths = plan.alloc(struct.pack(f'<{STRENGTHS}I', *(resources.alloc(str(s)) for s in STRENGTH_ORDER)))
    path = plan.alloc(b'A:\\WL-FILM.CFG\0')
    call = lambda name: n(f[name])
    from ..sound.settings import settings_code
    persistence = settings_code(f, path, state_get='grain_get', close='grain_close',
        magic=MAGIC, enabled_offset=STRENGTH_FIELD, selected_offset=0, fd_offset=4,
        enabled_max=STRENGTHS - 1, selected_max=len(STYLES) - 1, load_sound=False, restore_enabled=True)
    codes = {new: persistence[old] for old, new in (
        ('save_settings', 'grain_save'), ('restore_settings', 'grain_restore'),
        ('settings_close', 'grain_close'))}
    codes['grain_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    # r4 style, r6 strength code.
    codes['grain_labels'] = f'''push {{r4-r7,lr}};
        {c('r0',title)}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',title)}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('grain_get')}movs r4,#0;movs r6,#0;cmp r0,#0;beq ready;
        ldr r4,[r0];cmp r4,#{len(STYLES) - 1};it hi;movhi r4,#0;
        ldr r6,[r0,#{STRENGTH_FIELD}];cmp r6,#{STRENGTHS - 1};it hi;movhi r6,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        cmp r5,#{STYLE_ROW};bne strength_value;{c('r0',styles)}ldr r0,[r0,r4,lsl #2];b put;
        strength_value:{c('r0',strengths)}ldr r0,[r0,r6,lsl #2];
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{ROWS};bne loop;
        {c('r0',styles)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r7,pc}};'''
    codes['grain_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;{list_top(page_offset)}
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        GRAIN_IDS;ldr r1,[r1];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # Every row stays on the page: the list shows a new value only once its widgets
    # are rebuilt, so the page is reopened with focus back on the row (as the date stamp).
    codes['grain_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#{STRENGTH_ROW};bhi done;
        {call('grain_get')}cmp r0,#0;beq done;
        cmp r5,#{STYLE_ROW};bne strength;
        ldr r1,[r0];adds r1,#1;cmp r1,#{len(STYLES) - 1};it hi;movhi r1,#0;str r1,[r0];b store;
        strength:ldr r1,[r0,#{STRENGTH_FIELD}];adds r1,#1;cmp r1,#{STRENGTHS - 1};it hi;movhi r1,#0;
        str r1,[r0,#{STRENGTH_FIELD}];
        store:dmb ish;{call('grain_save')}cmp r0,#1;beq redraw;
        mov r0,r4;{call('show_error')}b done;
        redraw:{call('grain_labels')}movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        mov r0,r4;GRAIN_IDS;ldr r1,[r1,r5,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + STATE_BYTES, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack(f'<{ROWS}I', *identifiers))
    for name in ('grain_open', 'grain_action'):
        extension['codes'][name] = extension['codes'][name].replace('GRAIN_IDS;', c('r1', ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['film_grain_control'] = dict(
        default=0, page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        state_offset=extension['state_offset'], strength_offset=extension['state_offset'] + STRENGTH_FIELD,
        text_offset=extension['state_offset'] + TEXT_FIELD, settings_file='WL-FILM.CFG',
        styles=list(STYLES), strength_order=list(STRENGTH_ORDER),
        rows=['Style', 'Strength'],
        restore_policy='First extension-menu initialisation after power-on; OFF before that',
        functions={k: hex(f[k] | 1) for k in SIZES}, physical_verified=False)
