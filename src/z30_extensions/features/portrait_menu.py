# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""OFF/ON page for the portrait live-view layout; the drawing hooks are in portrait_ui.py."""
import struct
from ..assembly import constant as c, invoke as n, assemble
from .menu_slots import page_slots

PAGE_INDEX = 965
# Research builds turn this on: two readout lines below OFF/ON, and opening the
# page writes the state block (switch, readout, draw log) to WL-VUI.DBG.
DIAGNOSTICS = False
ROWS = 4 if DIAGNOSTICS else 2  # OFF, ON (and two readout lines)
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('portrait', ROWS)


def set_diagnostics(on):
    """Research builds: show the readout rows and export WL-VUI.DBG; call before building."""
    global DIAGNOSTICS, ROWS, TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT
    DIAGNOSTICS, ROWS = bool(on), 4 if on else 2
    TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('portrait', ROWS)
MAGIC = 0x31564C57  # WLV1
SETTINGS_FILE = 'WL-VUI.CFG'
# The hooks keep their runtime fields after the persisted ones (+0 choice, +4 fd, +8 enabled).
RUNTIME_OFFSET = 16
READOUT = 36            # first readout word; portrait_ui.R_KINDS
TEXT_A, TEXT_B = 56, 84  # two 28-byte ASCII lines
# Draw log while upright: header (magic, next index, frozen, capacity) then
# 16-byte records; see portrait_ui portrait_log and tools/read_portrait_log.py.
LOG, LOG_MAGIC, LOG_CAPACITY, LOG_STRIDE = 112, 0x314C5657, 256, 16  # WVL1
# Redraw-request trace: header (magic, next index) then 16-byte records
# (caller, predicate, what, screens) of every 0x10453FAC call while the switch is on.
REQ, REQ_MAGIC, REQ_CAPACITY, REQ_STRIDE = LOG + 16 + LOG_CAPACITY * LOG_STRIDE, 0x31525657, 64, 16  # WVR1
# UI event trace: header (magic, next index) then 16-byte records
# (entry | pose << 8, caller, r0, r1) from the four UI dispatch wrappers at 10310BF8.
EVT, EVT_MAGIC, EVT_CAPACITY, EVT_STRIDE = REQ + 16 + REQ_CAPACITY * REQ_STRIDE, 0x31455657, 128, 16  # WVE1
# Redraw traversal trace: header (magic, next index) then 16-byte records
# (container object, callback, caller, view kind | pose << 8) from 0x102BD3BA.
TRV, TRV_MAGIC, TRV_CAPACITY, TRV_STRIDE = EVT + 16 + EVT_CAPACITY * EVT_STRIDE, 0x31545657, 64, 16  # WVT1
# Live-view widgets the stock traversal actually drew: header (count) then pointers.
WID, WID_CAPACITY = TRV + 16 + TRV_CAPACITY * TRV_STRIDE, 256
WID_STRIDE = 8  # widget, last-draw stamp; header +0 count, +4 stamp counter
STATE_SIZE = WID + 16 + WID_CAPACITY * WID_STRIDE
LOG_FILE = 'WL-VUI.DBG'
SIZES = dict(portrait_get=96, portrait_labels=768, portrait_open=192,
             portrait_action=256, portrait_save=640, portrait_restore=640, portrait_close=128,
             portrait_hex=64, portrait_export=384)


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in
               dict(title='Portrait UI', off='OFF', on='ON', empty='', mark='*').items()}
    labels = plan.alloc(struct.pack('<2I', strings['off'], strings['on']))
    path = plan.alloc(b'A:\\' + SETTINGS_FILE.encode() + b'\0')
    log_path = plan.alloc(b'A:\\' + LOG_FILE.encode() + b'\0')
    call = lambda name: n(f[name])
    from .sound_settings import settings_code
    persistence = settings_code(f, path, state_get='portrait_get', close='portrait_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=0, selected_max=1, load_sound=False)
    codes = {new: persistence[old] for old, new in (
        ('save_settings', 'portrait_save'), ('restore_settings', 'portrait_restore'),
        ('settings_close', 'portrait_close'))}
    codes['portrait_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    # r0 -> buffer, r1 value, r2 digits; returns the end of the digits.
    codes['portrait_hex'] = '''push {r4,lr};add r0,r0,r2;mov r3,r0;
        loop:and r4,r1,#15;cmp r4,#10;ite lo;addlo r4,#48;addhs r4,#55;strb r4,[r3,#-1]!;
        lsr r1,r1,#4;subs r2,#1;bne loop;pop {r4,pc};'''
    def put(char):
        return f'movs r1,#{ord(char)};strb r1,[r0],#1;'
    def hexfield(char, offset, digits):
        return f"{put(char)}ldr r1,[r4,#{offset}];movs r2,#{digits};{call('portrait_hex')}"
    # K view kinds, T targets; P phases, B overlay words, C full-layer copy targets,
    # R refresh passes, F forced repaints. Offsets follow portrait_ui.R_*.
    line_a = hexfield('K', 36, 8) + put(' ') + hexfield('T', 40, 8)
    line_b = (hexfield('P', 44, 2) + hexfield('B', 48, 1) + hexfield('C', 52, 8)
              + hexfield('R', 28, 4) + hexfield('F', 32, 2))
    readout = f'''{call('portrait_get')}cmp r0,#0;beq readout_done;mov r4,r0;
        add r0,r4,#{TEXT_A};{line_a}movs r1,#0;strb r1,[r0];
        add r0,r4,#{TEXT_A};movs r1,#{LABEL_SLOT + 2};movs r2,#0;{call('text_ascii')}
        add r0,r4,#{TEXT_B};{line_b}movs r1,#0;strb r1,[r0];
        add r0,r4,#{TEXT_B};movs r1,#{LABEL_SLOT + 3};movs r2,#0;{call('text_ascii')}
        readout_done:
        {c('r0',strings['empty'])}movs r1,#{MARK_SLOT + 2};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}movs r1,#{MARK_SLOT + 3};movs r2,#0;{call('text')}'''
    codes['portrait_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('portrait_get')}movs r4,#0;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#1;it hi;movhi r4,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#2;bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        {{readout}}
        movs r0,#0;pop {{r4-r6,pc}};'''.replace('{readout}', readout if DIAGNOSTICS else '')
    # Opening the page writes the whole block (switch, readout, log) to the card.
    # Recording pauses meanwhile; a failed write changes nothing else.
    codes['portrait_export'] = f'''push {{r4-r6,lr}};sub sp,#64;{call('portrait_get')}cmp r0,#0;beq done;mov r4,r0;
        movs r0,#1;str r0,[r4,#{LOG + 8}];dmb ish;
        mov r0,sp;movs r1,#48;{n(0x10199c40)}
        mvn r0,#0;str r0,[sp,#8];mov r0,sp;{c('r1',log_path)}{n(0x1033829e)}
        mov r0,sp;{c('r1',0x301)}movs r2,#0;{n(0x103382a2)}
        {c('r1',0x04370080)}cmp r0,r1;bne thaw;
        movs r0,#0;str r0,[sp,#48];ldr r0,[sp,#8];mov r1,r4;{c('r2',STATE_SIZE)}add r3,sp,#48;{n(0x102c8a6a)}
        mov r0,sp;{n(0x1033832e)}
        thaw:movs r0,#0;dmb ish;str r0,[r4,#{LOG + 8}];
        done:add sp,#64;pop {{r4-r6,pc}};'''
    codes['portrait_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;{call('portrait_export') if DIAGNOSTICS else ''}
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('portrait_get')}ldr r4,[r0];cmp r4,#1;it hi;movhi r4,#0;
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        PORTRAIT_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # Only the choice word changes here. The drawing hooks notice the change on
    # their next call and clear the old layout once, back in live view.
    codes['portrait_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#1;bhi done;
        {call('portrait_get')}cmp r0,#0;beq done;str r5,[r0];
        movs r1,#0;movs r2,#{RUNTIME_OFFSET};wipe:str r1,[r0,r2];adds r2,#4;cmp r2,#{TEXT_A};blo wipe;
        str r1,[r0,#{LOG + 4}];movw r2,#{REQ + 4};str r1,[r0,r2];movw r2,#{EVT + 4};str r1,[r0,r2];movw r2,#{TRV + 4};str r1,[r0,r2];dmb ish;
        {call('portrait_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:mov r0,r4;{call('page_return')}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + STATE_SIZE, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack(f'<{len(identifiers)}I', *identifiers))
    extension['codes']['portrait_open'] = extension['codes']['portrait_open'].replace('PORTRAIT_IDS;', c('r1', ids))
    if not DIAGNOSTICS:
        del extension['codes']['portrait_export']
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['portrait_ui_control'] = dict(
        default=0, page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        state_offset=extension['state_offset'], runtime_offset=RUNTIME_OFFSET,
        settings_file=SETTINGS_FILE, diagnostics=DIAGNOSTICS, log_file=LOG_FILE if DIAGNOSTICS else None, log_offset=LOG, log_capacity=LOG_CAPACITY,
        log_stride=LOG_STRIDE, state_size=STATE_SIZE, request_offset=REQ, request_capacity=REQ_CAPACITY, event_offset=EVT, event_capacity=EVT_CAPACITY, traversal_offset=TRV, traversal_capacity=TRV_CAPACITY, widget_offset=WID, widget_capacity=WID_CAPACITY,
        restore_policy='First custom-menu initialization; OFF before initialization',
        functions={k: hex(f[k] | 1) for k in SIZES}, physical_verified=False)
