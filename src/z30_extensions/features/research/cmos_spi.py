# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""CMOS research page with a full SPI write log, on top of cmos_trace.

Every CMOS register write funnels through the stock serial writer
0x1019D868(buffer, length) (callers: the sequence writer 0x101401F6 and the
clock switch 0x1019D8E2). Once armed from the menu, an observer at its entry
appends one 32-byte entry per packet: entry number (1-based, written last),
segment number (incremented by each Arm), packet length and the first 24
packet bytes. The log is a ring: entry n lives in slot (n - 1) mod CAPACITY,
so a host polling over USB (ptp_read) can follow it without a limit.
A packet is skipped when it equals either of the last two packets logged for
the same register (64 hashed slots), so steady per-frame writes, including the
alternating exposure pair, stop filling the ring; Arm clears this history.
The stock arguments, registers and instruction stream are unchanged.

Rows: Save CMOS data, Save mode log, Arm SPI log, Save SPI log.
WL-SPI.BIN: 32-byte header (magic WLS1, version, entries claimed, capacity,
entry size, current segment, zero, zero), then CAPACITY entries.
"""
import struct
from ...assembly import constant as c, invoke as n, jump
from ..menu.slots import page_slots
from . import cmos_dump as cd
from . import cmos_trace as ct

PAGE_INDEX = cd.PAGE_INDEX
ROWS = 4
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('video', ROWS)
WRITER = 0x1019D868
WRITER_PROLOGUE = bytes.fromhex('2de9f04387b00746')  # push.w {r4-r9,lr}; sub sp,#0x1c; mov r7,r0
CAPACITY, ENTRY_BYTES, ENTRY_DATA = 4096, 32, 24
LOG_BYTES = CAPACITY * ENTRY_BYTES
SPI_MAGIC = 0x31534C57  # WLS1
SPI_VERSION = 3  # v2: ring log; v3: per-register de-duplication
SPI_HEADER = 32
SPI_PATH = 'A:\\WL-SPI.BIN'
SPI_BUFFER = ct.STATE_BYTES        # pointer to the pinned log buffer, 0 until first Arm
SPI_ARMED = SPI_BUFFER + 4         # 1 once armed
SPI_SEGMENT = SPI_BUFFER + 8       # increments on each Arm
SPI_INDEX = SPI_BUFFER + 12        # entries claimed so far, ldrex/strex; slot = index mod CAPACITY
DEDUPE = SPI_BUFFER + 16           # 64 slots x 64 bytes: last two keys (tag + 24 data bytes) per register hash
DEDUPE_SLOTS, DEDUPE_SLOT_BYTES, KEY_BYTES = 64, 64, 28
STATE_BYTES = DEDUPE + DEDUPE_SLOTS * DEDUPE_SLOT_BYTES
STATUS = ct.STATUS + ('SPI log armed', 'Saved WL-SPI.BIN', 'SPI log not armed')
ARMED_STATUS, SAVED_STATUS, NOT_ARMED_STATUS = len(ct.STATUS), len(ct.STATUS) + 1, len(ct.STATUS) + 2
SIZES = dict(ct.SIZES, bitrate_labels=448, bitrate_action=256, spi_hook=640, spi_arm=320, spi_export=768)
assert LOG_BYTES % cd.CHUNK == 0 and SPI_HEADER <= cd.CHUNK and CAPACITY & (CAPACITY - 1) == 0


def prepare(plan, f, resources, allocation_size, window=None, sizes=SIZES):
    """window: optional dict(preset_offset, buffer_offset, table) for cmos_window's 4K rewrite."""
    page = ct.prepare(plan, f, resources, allocation_size)
    # ct.prepare allocated with its own sizes and state; re-allocate ours on top.
    for name in ('bitrate_labels', 'bitrate_action', 'spi_hook', 'spi_arm', 'spi_export'):
        f[name] = plan.alloc(bytes(sizes[name]))
    codes, strings = page['codes'], page['strings']
    state_offset = page['state_offset']
    page['allocation_size'] = state_offset + STATE_BYTES
    statuses = plan.alloc(struct.pack(f'<{len(STATUS)}I', *(resources.alloc(s) for s in STATUS)))
    rows = [strings['action'], resources.alloc('Save mode log'), resources.alloc('Arm SPI log'),
            resources.alloc('Save SPI log')]
    row_table = plan.alloc(struct.pack('<4I', *rows))
    spi_path = plan.alloc(SPI_PATH.encode() + b'\0')
    call = lambda name: n(f[name])
    codes['bitrate_labels'] = f'''push {{r4,r5,lr}};sub sp,#4;
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        movs r5,#0;
    rows:{c('r0',row_table)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{ROWS};blo rows;
        {call('bitrate_get')}movs r4,#0;cmp r0,#0;beq put;
        ldr r4,[r0,#{cd.STATUS_OFF}];cmp r4,#{len(STATUS)-1};it hi;movhi r4,#0;
        put:{c('r0',statuses)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;add sp,#4;pop {{r4,r5,pc}};'''
    codes['bitrate_action'] = f'''push {{r4,r5,lr}};sub sp,#4;mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r0;cmp r5,#{ROWS-1};bhi done;
        {call('bitrate_get')}cmp r0,#0;beq done;
        cmp r5,#0;bne row1;{call('cmos_export')}b refresh;
        row1:cmp r5,#1;bne row2;{call('cmos_log_export')}b refresh;
        row2:cmp r5,#2;bne row3;{call('spi_arm')}b refresh;
        row3:{call('spi_export')}
        refresh:{call('bitrate_labels')}mov r0,r4;{call('page_return')}
        done:movs r0,#0;add sp,#4;pop {{r4,r5,pc}};'''
    # Menu task: allocate once (pinned to power off), zero, bump the segment, arm.
    codes['spi_arm'] = f'''push {{r4,r5,lr}};sub sp,#4;mov r4,r0;
        {c('r1',SPI_BUFFER)}ldr r5,[r4,r1];cmp r5,#0;bne ready;
        {c('r0',LOG_BYTES)}movs r1,#0;{n(0x10211F4C)}cmp r0,#0;beq no_memory;mov r5,r0;
        {c('r1',LOG_BYTES)}{n(0x10199C40)}
        {c('r1',SPI_INDEX)}movs r0,#0;str r0,[r4,r1];
        dmb ish;{c('r1',SPI_BUFFER)}str r5,[r4,r1];
    ready:
        {c('r0',DEDUPE)}add r0,r4,r0;{c('r1',DEDUPE_SLOTS*DEDUPE_SLOT_BYTES)}{n(0x10199C40)}
        {c('r1',SPI_SEGMENT)}ldr r0,[r4,r1];adds r0,#1;str r0,[r4,r1];
        dmb ish;{c('r1',SPI_ARMED)}movs r0,#1;str r0,[r4,r1];
        movs r0,#{ARMED_STATUS};b status;
    no_memory:movs r0,#3;
    status:str r0,[r4,#{cd.STATUS_OFF}];add sp,#4;pop {{r4,r5,pc}};'''
    # Observer at the CMOS serial writer; [sp+16]/[sp+20] are the stock r0/r1.
    codes['spi_hook'] = f'''push {{r0-r3,r12,lr}};mrs r12,apsr;push {{r4-r6,r12}};
        {call('get')}cmp r0,#0;beq resume;{c('r1',state_offset)}add r4,r0,r1;
        WINDOW_REWRITE
        {c('r1',SPI_ARMED)}ldr r0,[r4,r1];cmp r0,#1;bne resume;
        {c('r1',SPI_BUFFER)}ldr r6,[r4,r1];cmp r6,#0;beq resume;dmb ish;
        sub sp,#{KEY_BYTES};movs r0,#0;movs r3,#0;
    zero:str r0,[sp,r3];adds r3,#4;cmp r3,#{KEY_BYTES};blo zero;
        ldr r2,[sp,#{KEY_BYTES+16}];ldr r1,[sp,#{KEY_BYTES+20}];
        mov r5,r1;cmp r5,#{ENTRY_DATA};it hi;movhi r5,#{ENTRY_DATA};movs r3,#0;
    key:cmp r3,r5;bhs keyed;ldrb r0,[r2,r3];add r12,sp,#4;strb r0,[r12,r3];adds r3,#1;b key;
    keyed:movs r0,#0;cmp r1,#3;blo untagged;ldrb r0,[r2,#1];ldrb r3,[r2,#2];orr r0,r3,r0,lsl #8;
    untagged:orr r3,r0,r1,lsl #16;str r3,[sp];
        eor r3,r0,r0,lsr #6;and r3,r3,#{DEDUPE_SLOTS-1};
        {c('r12',DEDUPE)}add r12,r4,r12;add r12,r12,r3,lsl #6;
        movs r3,#0;
    first:ldr r0,[sp,r3];ldr r2,[r12,r3];cmp r0,r2;bne second_start;adds r3,#4;cmp r3,#{KEY_BYTES};blo first;b skip;
    second_start:movs r3,#0;
    second:ldr r0,[sp,r3];add r2,r12,#32;ldr r2,[r2,r3];cmp r0,r2;bne remember;adds r3,#4;cmp r3,#{KEY_BYTES};blo second;b skip;
    remember:movs r3,#0;
    shift:ldr r0,[r12,r3];add r2,r12,#32;str r0,[r2,r3];ldr r0,[sp,r3];str r0,[r12,r3];adds r3,#4;cmp r3,#{KEY_BYTES};blo shift;
        add sp,#{KEY_BYTES};b log;
    skip:add sp,#{KEY_BYTES};b resume;
    log:{c('r1',SPI_INDEX)}add r2,r4,r1;
    claim:ldrex r3,[r2];adds r5,r3,#1;strex r1,r5,[r2];cmp r1,#0;bne claim;
        ubfx r3,r3,#0,#{CAPACITY.bit_length()-1};add r6,r6,r3,lsl #5;movs r0,#0;str r0,[r6];dmb ish;
        {c('r1',SPI_SEGMENT)}ldr r0,[r4,r1];ldr r1,[sp,#20];
        uxth r0,r0;orr r0,r0,r1,lsl #16;str r0,[r6,#4];
        ldr r2,[sp,#16];cmp r1,#{ENTRY_DATA};it hi;movhi r1,#{ENTRY_DATA};movs r3,#0;
    copy:cmp r3,r1;bhs copied;ldrb r0,[r2,r3];add r12,r6,#8;strb r0,[r12,r3];adds r3,#1;b copy;
    copied:dmb ish;str r5,[r6];
    resume:pop {{r4-r6,r12}};msr apsr_nzcvq,r12;pop {{r0-r3,r12,lr}};
        push.w {{r4-r9,lr}};sub sp,#0x1c;mov r7,r0;
        {c('r12',WRITER + 8 + 1)}bx r12;'''
    # Menu task: header in the bounce buffer, then the log in CHUNK pieces.
    codes['spi_export'] = f'''push {{r4-r8,lr}};mov r4,r0;
        {c('r1',SPI_BUFFER)}ldr r7,[r4,r1];cmp r7,#0;beq not_armed;
        ldr r5,[r4,#{cd.BUFFER_OFF}];cmp r5,#0;bne have_buffer;
        {c('r0',cd.CHUNK)}movs r1,#0;{n(0x10211F4C)}cmp r0,#0;beq no_memory;
        str r0,[r4,#{cd.BUFFER_OFF}];mov r5,r0;
    have_buffer:
        mov r0,r5;movs r1,#{SPI_HEADER};{n(0x10199C40)}
        {c('r0',SPI_MAGIC)}str r0,[r5];movs r0,#{SPI_VERSION};str r0,[r5,#4];
        {c('r1',SPI_INDEX)}ldr r0,[r4,r1];str r0,[r5,#8];{c('r0',CAPACITY)}str r0,[r5,#12];
        movs r0,#{ENTRY_BYTES};str r0,[r5,#16];{c('r1',SPI_SEGMENT)}ldr r0,[r4,r1];str r0,[r5,#20];
        mov r0,r4;{call('cmos_close')}cmp r0,#1;bne open_failed;
        add r0,r4,#{cd.FILE_OFF};movs r1,#48;{n(0x10199C40)}
        mvn r0,#0;str r0,[r4,#{cd.FD_OFF}];
        add r0,r4,#{cd.FILE_OFF};{c('r1',spi_path)}{n(0x1033829E)}
        add r0,r4,#{cd.FILE_OFF};movw r1,#0x301;movs r2,#0;{n(0x103382A2)}mov r6,r0;
        ldr r0,[r4,#{cd.FD_OFF}];cmp r0,#0;blt not_open;movs r0,#1;str r0,[r4,#{cd.OPEN_OFF}];
    not_open:
        {c('r0',cd.SUCCESS)}cmp r6,r0;bne open_failed;
        ldr r0,[r4,#{cd.FD_OFF}];cmp r0,#0;blt open_failed;
        mov r0,r4;mov r1,r5;movs r2,#{SPI_HEADER};{call('cmos_write')}cmp r0,#1;bne write_failed;
        movs r6,#0;{c('r8',LOG_BYTES)}
    chunk:
        mov r0,r5;add r1,r7,r6;{c('r2',cd.CHUNK)}{n(0x10199CF0, thumb=False)}
        mov r0,r4;mov r1,r5;{c('r2',cd.CHUNK)}{call('cmos_write')}cmp r0,#1;bne write_failed;
        {c('r0',cd.CHUNK)}add r6,r0;cmp r6,r8;blo chunk;
        mov r0,r4;{call('cmos_close')}cmp r0,#1;bne close_failed;
        movs r0,#{SAVED_STATUS};b status;
    not_armed:movs r0,#{NOT_ARMED_STATUS};b status;
    no_memory:movs r0,#3;b status;
    open_failed:mov r0,r4;{call('cmos_close')}movs r0,#4;b status;
    write_failed:mov r0,r4;{call('cmos_close')}movs r0,#5;b status;
    close_failed:movs r0,#6;
    status:str r0,[r4,#{cd.STATUS_OFF}];pop {{r4-r8,pc}};'''
    rewrite = ''
    if window:
        stock = window['stock']
        checks = ''.join(f'ldrb r3,[r2,#{i}];cmp r3,#{v};bne nowin;' for i, v in enumerate(window['signature']))
        checks += ''.join(f'ldrb r3,[r2,#{13 + i}];cmp r3,#{v};bne nowin;' for i, v in enumerate(stock))
        rewrite = f'''{c('r1',window['preset_offset'])}ldr r0,[r4,r1];cmp r0,#0;beq nowin;cmp r0,#{window['presets']};bhi nowin;
        ldr r1,[sp,#20];cmp r1,#24;bne nowin;ldr r2,[sp,#16];{checks}
        {c('r1',window['buffer_offset'])}add r1,r4,r1;movs r3,#0;
    wcopy:ldrb r12,[r2,r3];strb r12,[r1,r3];adds r3,#1;cmp r3,#24;blo wcopy;
        {c('r2',window['table'])}subs r0,#1;ldr r3,[r2,r0,lsl #2];
        strb r3,[r1,#13];lsrs r3,r3,#8;strb r3,[r1,#14];lsrs r3,r3,#8;strb r3,[r1,#15];lsrs r3,r3,#8;strb r3,[r1,#16];
        str r1,[sp,#16];
    nowin:'''
    codes['spi_hook'] = codes['spi_hook'].replace('WINDOW_REWRITE', rewrite)
    actual = plan.image.read(WRITER, 8)
    if actual != WRITER_PROLOGUE:
        raise ValueError(f'Unexpected CMOS serial writer prologue: {actual.hex()}')
    plan.edit(WRITER, jump(WRITER, f['spi_hook']), 'Observe CMOS serial writes', WRITER_PROLOGUE)
    return page


def finish(plan, f, extension, identifiers, sizes=SIZES):
    cd.finish(plan, f, extension, identifiers, sizes=sizes)
    feature = plan.features.pop('cmos_dump')
    feature.update(rows=ROWS, hooks_outside_menu=True,
                   observer=dict(site=hex(ct.SELECTOR), displaced=ct.SELECTOR_PROLOGUE.hex(), command=ct.COMMAND,
                                 request=hex(ct.REQUEST), parameters=hex(ct.PARAMETERS),
                                 records=ct.RECORDS, record_bytes=ct.RECORD_BYTES,
                                 log_index_offset=ct.LOG_INDEX, log_base_offset=ct.LOG_BASE,
                                 behaviour='records only; stock arguments, registers and flow unchanged'),
                   log_file=ct.LOG_PATH, log_magic=hex(ct.LOG_MAGIC), log_header_bytes=ct.LOG_HEADER,
                   spi_observer=dict(site=hex(WRITER), displaced=WRITER_PROLOGUE.hex(), capacity=CAPACITY,
                                     entry_bytes=ENTRY_BYTES, entry_data=ENTRY_DATA,
                                     buffer_offset=SPI_BUFFER, armed_offset=SPI_ARMED,
                                     segment_offset=SPI_SEGMENT, index_offset=SPI_INDEX,
                                     buffer=f'{LOG_BYTES} bytes from 0x10211F4C(size, pool=0) on first Arm, pinned',
                                     behaviour='records only after Arm; skips repeats of the last two packets per register; ring of CAPACITY entries; stock flow unchanged'),
                   spi_file=SPI_PATH, spi_magic=hex(SPI_MAGIC), spi_header_bytes=SPI_HEADER,
                   status_codes=list(STATUS),
                   writes=['SD file A:\\WL-CMOS.BIN', 'SD file A:\\WL-MODE.BIN', 'SD file A:\\WL-SPI.BIN'])
    plan.features['cmos_spi'] = feature
