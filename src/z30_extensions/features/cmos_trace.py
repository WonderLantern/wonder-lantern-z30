# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""CMOS research page: the read-only drive-data dump plus a mode-change log.

An observer at the entry of the stock drive selector 0x10139F44(command,
request) records each command-4 call (the main drive-mode write) into a ring in
the page state: sequence, command, the first 16 request bytes (byte 0 is the
drive type) and the first 40 bytes of the upper parameter block 0xC239BCFC
(p0/p1/p2 and p[0x27]). Registers, flags and the stock instruction stream are
unchanged; nothing is recorded until the Extensions page has been opened once
(the state is allocated then). A second menu row saves the ring to
A:\\WL-MODE.BIN through the same cache-cleaned writer as the dump.

WL-MODE.BIN: 32-byte header (magic WLM1, version, next sequence, record count,
record size, request address, parameter address, zero), then RECORDS records
of 64 bytes: +0 sequence (1-based, 0 = empty), +4 command, +8 request[0:16],
+24 parameters[0:40].
"""
import struct
from ..assembly import constant as c, invoke as n, jump
from .menu_slots import page_slots
from . import cmos_dump as cd

PAGE_INDEX = cd.PAGE_INDEX
ROWS = 2
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('video', ROWS)
SELECTOR = 0x10139F44
SELECTOR_PROLOGUE = bytes.fromhex('2de9f84f05460c46')  # push.w {r3-r11,lr}; mov r5,r0; mov r4,r1
REQUEST = 0xC18E4EBC
PARAMETERS = 0xC239BCFC
COMMAND = 4
LOG_MAGIC = 0x314D4C57  # WLM1
LOG_VERSION = 1
RECORDS, RECORD_BYTES = 128, 64
LOG_INDEX = cd.STATE_BYTES          # u32 next sequence, ldrex/strex
LOG_BASE = cd.STATE_BYTES + 64
STATE_BYTES = LOG_BASE + RECORDS * RECORD_BYTES
LOG_HEADER = 32
LOG_PATH = 'A:\\WL-MODE.BIN'
STATUS = cd.STATUS + ('Saved WL-MODE.BIN',)
SIZES = dict(cd.SIZES, bitrate_labels=320, bitrate_action=192, cmos_hook=224, cmos_log_export=640)
assert LOG_HEADER + RECORDS * RECORD_BYTES <= cd.CHUNK and RECORDS & (RECORDS - 1) == 0


def prepare(plan, f, resources, allocation_size):
    page = cd.prepare(plan, f, resources, allocation_size, sizes=SIZES,
                      state_bytes=STATE_BYTES, statuses=STATUS)
    codes, strings, statuses = page['codes'], page['strings'], page['statuses']
    state_offset = page['state_offset']
    log_row = resources.alloc('Save mode log')
    log_path = plan.alloc(LOG_PATH.encode() + b'\0')
    call = lambda name: n(f[name])
    codes['bitrate_labels'] = f'''push {{r4,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['action'])}movs r1,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',log_row)}movs r1,#{LABEL_SLOT + 1};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}movs r1,#{MARK_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}movs r1,#{MARK_SLOT + 1};movs r2,#0;{call('text')}
        {call('bitrate_get')}movs r4,#0;cmp r0,#0;beq put;
        ldr r4,[r0,#{cd.STATUS_OFF}];cmp r4,#{len(STATUS)-1};it hi;movhi r4,#0;
        put:{c('r0',statuses)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4,pc}};'''
    codes['bitrate_action'] = f'''push {{r4,r5,lr}};sub sp,#4;mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r0;cmp r5,#1;bhi done;
        {call('bitrate_get')}cmp r0,#0;beq done;
        cmp r5,#0;bne log;{call('cmos_export')}b refresh;
        log:{call('cmos_log_export')}
        refresh:{call('bitrate_labels')}mov r0,r4;{call('page_return')}
        done:movs r0,#0;add sp,#4;pop {{r4,r5,pc}};'''
    # Observer: runs before the displaced prologue, preserves r0-r3/r12/lr and
    # APSR (the stock prologue does not read flags, but nothing is assumed).
    codes['cmos_hook'] = f'''push {{r0-r3,r12,lr}};mrs r12,apsr;push {{r4,r5,r12}};
        cmp r0,#{COMMAND};bne resume;
        {call('get')}cmp r0,#0;beq resume;{c('r1',state_offset)}add r4,r0,r1;
        {c('r1',LOG_INDEX)}add r2,r4,r1;
    claim:ldrex r3,[r2];adds r5,r3,#1;strex r1,r5,[r2];cmp r1,#0;bne claim;
        and r3,r3,#{RECORDS-1};{c('r1',LOG_BASE)}add r1,r4,r1;add r1,r1,r3,lsl #6;
        movs r0,#0;str r0,[r1];dmb ish;
        ldr r0,[sp,#12];str r0,[r1,#4];
        ldr r2,[sp,#16];tst r2,#3;bne params;ldm r2,{{r0,r3,r4,r12}};add r2,r1,#8;stm r2,{{r0,r3,r4,r12}};
    params:
        {c('r2',PARAMETERS)}movs r3,#0;
    copy:ldr r0,[r2,r3];add r4,r1,#24;str r0,[r4,r3];adds r3,#4;cmp r3,#40;blo copy;
        dmb ish;str r5,[r1];
    resume:pop {{r4,r5,r12}};msr apsr_nzcvq,r12;pop {{r0-r3,r12,lr}};
        push.w {{r3-r11,lr}};mov r5,r0;mov r4,r1;
        {c('r12',SELECTOR + 8 + 1)}bx r12;'''
    # r4 state, r5 bounce buffer.
    codes['cmos_log_export'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldr r5,[r4,#{cd.BUFFER_OFF}];cmp r5,#0;bne have_buffer;
        {c('r0',cd.CHUNK)}movs r1,#0;{n(0x10211F4C)}cmp r0,#0;beq no_memory;
        str r0,[r4,#{cd.BUFFER_OFF}];mov r5,r0;
    have_buffer:
        mov r0,r5;movs r1,#{LOG_HEADER};{n(0x10199C40)}
        {c('r0',LOG_MAGIC)}str r0,[r5];movs r0,#{LOG_VERSION};str r0,[r5,#4];
        {c('r1',LOG_INDEX)}ldr r0,[r4,r1];str r0,[r5,#8];movs r0,#{RECORDS};str r0,[r5,#12];
        movs r0,#{RECORD_BYTES};str r0,[r5,#16];{c('r0',REQUEST)}str r0,[r5,#20];
        {c('r0',PARAMETERS)}str r0,[r5,#24];
        add r0,r5,#{LOG_HEADER};{c('r1',LOG_BASE)}add r1,r4,r1;{c('r2',RECORDS*RECORD_BYTES)}
        {n(0x10199CF0, thumb=False)}
        mov r0,r4;{call('cmos_close')}cmp r0,#1;bne open_failed;
        add r0,r4,#{cd.FILE_OFF};movs r1,#48;{n(0x10199C40)}
        mvn r0,#0;str r0,[r4,#{cd.FD_OFF}];
        add r0,r4,#{cd.FILE_OFF};{c('r1',log_path)}{n(0x1033829E)}
        add r0,r4,#{cd.FILE_OFF};movw r1,#0x301;movs r2,#0;{n(0x103382A2)}mov r6,r0;
        ldr r0,[r4,#{cd.FD_OFF}];cmp r0,#0;blt not_open;movs r0,#1;str r0,[r4,#{cd.OPEN_OFF}];
    not_open:
        {c('r0',cd.SUCCESS)}cmp r6,r0;bne open_failed;
        ldr r0,[r4,#{cd.FD_OFF}];cmp r0,#0;blt open_failed;
        mov r0,r4;mov r1,r5;{c('r2',LOG_HEADER + RECORDS*RECORD_BYTES)}{call('cmos_write')}
        cmp r0,#1;bne write_failed;
        mov r0,r4;{call('cmos_close')}cmp r0,#1;bne close_failed;
        movs r0,#{len(STATUS)-1};b status;
    no_memory:movs r0,#3;b status;
    open_failed:mov r0,r4;{call('cmos_close')}movs r0,#4;b status;
    write_failed:mov r0,r4;{call('cmos_close')}movs r0,#5;b status;
    close_failed:movs r0,#6;
    status:str r0,[r4,#{cd.STATUS_OFF}];pop {{r4-r6,pc}};'''
    actual = plan.image.read(SELECTOR, 8)
    if actual != SELECTOR_PROLOGUE:
        raise ValueError(f'Unexpected drive selector prologue: {actual.hex()}')
    plan.edit(SELECTOR, jump(SELECTOR, f['cmos_hook']), 'Observe CMOS drive-mode selector', SELECTOR_PROLOGUE)
    page.update(log_path=log_path)
    return page


def finish(plan, f, extension, identifiers):
    cd.finish(plan, f, extension, identifiers, sizes=SIZES)
    feature = plan.features.pop('cmos_dump')
    feature.update(rows=ROWS, hooks_outside_menu=True,
                   observer=dict(site=hex(SELECTOR), displaced=SELECTOR_PROLOGUE.hex(), command=COMMAND,
                                 request=hex(REQUEST), parameters=hex(PARAMETERS),
                                 records=RECORDS, record_bytes=RECORD_BYTES,
                                 log_index_offset=LOG_INDEX, log_base_offset=LOG_BASE,
                                 behaviour='records only; stock arguments, registers and flow unchanged'),
                   log_file=LOG_PATH, log_magic=hex(LOG_MAGIC), log_header_bytes=LOG_HEADER,
                   writes=['SD file A:\\WL-CMOS.BIN', 'SD file A:\\WL-MODE.BIN'])
    plan.features['cmos_trace'] = feature
