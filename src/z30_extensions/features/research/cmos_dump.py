# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Menu-only, read-only export of the CMOS driver's drive data to SD.

The CMOS serial driver keeps a pointer to its drive data (data area 0x4C,
loaded from NOR bank 26 at boot) in its context at 0xC18E4FEC + 0x14. One menu
action copies that RAM through a pinned 32 KiB bounce buffer, cleans its
data-cache lines (the SD path reads it by DMA) and writes it into
A:\\WL-CMOS.BIN. Nothing is written to the sensor, the driver, NOR or any
setting; no hook is placed in capture or sensor code paths.

File layout (little endian):
  0x000  header, 64 bytes: magic WLC1, version, blob pointer, blob length,
         word sum of the blob, context address, area descriptor address,
         bank descriptor address, area +8 address word, reserved zeros
  0x040  driver context, 256 bytes from 0xC18E4FEC
  0x140  data-area descriptor 0x4C, 28 bytes + 4 zero bytes
  0x160  NOR bank descriptor 26, 44 bytes + 4 zero bytes
  0x190  drive data, BLOB_BYTES bytes
"""
import struct
from ...assembly import assemble, constant as c, invoke as n
from ..menu.slots import page_slots
from ..menu.layout import SUCCESS

PAGE_INDEX = 960
ROWS = 1
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('video', ROWS)
MAGIC = 0x31434C57  # WLC1
VERSION = 2  # v1 wrote stale DRAM: the bounce buffer was not cleaned before SD DMA
CACHE_CLEAN = 0x10204B0C  # stock d-cache clean to PoC by region, then DSB SY
CTX = 0xC18E4FEC
CTX_BYTES = 256
BLOB_FIELD = 0x14
BLOB_BYTES = 0x168000
AREA = 0xC1880D2C + 0x4C * 28
BANK = 0xC1880888 + 26 * 44
RAM_LOW, RAM_HIGH = 0x80000000, 0xE0000000
CHUNK = 0x8000
HEADER_BYTES = 0x190
PATH = 'A:\\WL-CMOS.BIN'
STATUS = ('Not saved', 'Saved WL-CMOS.BIN', 'No CMOS data', 'No memory',
          'Open failed', 'Write failed', 'Close failed')
# State words: status, bounce buffer, open flag, count-out, file object (48 bytes).
# The framework stores -1 at the page's fd_offset on first entry; FD_OFF is
# reported as that offset so the bounce-buffer word stays zero until allocated.
STATUS_OFF, BUFFER_OFF, OPEN_OFF, COUNT_OFF, FILE_OFF = 0, 4, 8, 12, 16
FD_OFF = FILE_OFF + 8
assert FD_OFF not in (STATUS_OFF, BUFFER_OFF, OPEN_OFF, COUNT_OFF)
STATE_BYTES = FILE_OFF + 48
SIZES = dict(bitrate_get=96, bitrate_labels=256, bitrate_open=160, bitrate_action=160,
             bitrate_restore=32, cmos_write=224, cmos_close=160, cmos_export=1024)


def prepare(plan, f, resources, allocation_size, *, sizes=SIZES, state_bytes=STATE_BYTES,
            statuses=STATUS):
    """Shared by cmos_trace, which passes a larger state and status list."""
    STATUS = statuses
    for name, size in sizes.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in
               dict(title='CMOS data dump', action='Save CMOS data', empty='').items()}
    statuses = plan.alloc(struct.pack(f'<{len(STATUS)}I', *(resources.alloc(s) for s in STATUS)))
    path = plan.alloc(PATH.encode() + b'\0')
    call = lambda name: n(f[name])
    codes = {}
    codes['bitrate_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    codes['bitrate_restore'] = 'movs r0,#0;bx lr;'
    codes['bitrate_labels'] = f'''push {{r4,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['action'])}movs r1,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}movs r1,#{MARK_SLOT};movs r2,#0;{call('text')}
        {call('bitrate_get')}movs r4,#0;cmp r0,#0;beq put;
        ldr r4,[r0,#{STATUS_OFF}];cmp r4,#{len(STATUS)-1};it hi;movhi r4,#0;
        put:{c('r0',statuses)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4,pc}};'''
    codes['bitrate_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045F732)}
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        CMOS_IDS;ldr r1,[r1];{n(0x1045D54C)}
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['bitrate_action'] = f'''push {{r4,lr}};mov r4,r0;
        ldrh r0,[r4,#22];ldrh r1,[r4,#24];add r0,r1;cmp r0,#0;bne done;
        {call('bitrate_get')}cmp r0,#0;beq done;{call('cmos_export')}
        {call('bitrate_labels')}mov r0,r4;{call('page_return')}
        done:movs r0,#0;pop {{r4,pc}};'''
    # write(state, source, length): whole length or failure; checks count-out.
    # The SD path reads the buffer by DMA, so CPU-written lines are cleaned first.
    codes['cmos_write'] = f'''push {{r4-r7,lr}};mov r4,r0;mov r5,r1;mov r6,r2;
        mov r0,r5;mov r1,r6;{n(CACHE_CLEAN)}
        movs r0,#0;str r0,[r4,#{COUNT_OFF}];
        ldr r0,[r4,#{FD_OFF}];mov r1,r5;mov r2,r6;add r3,r4,#{COUNT_OFF};{n(0x102C8A6A)}
        cmp r0,#0;bne failed;ldr r0,[r4,#{COUNT_OFF}];cmp r0,r6;bne failed;
        movs r0,#1;pop {{r4-r7,pc}};
        failed:movs r0,#0;pop {{r4-r7,pc}};'''
    codes['cmos_close'] = f'''push {{r4,lr}};mov r4,r0;
        ldr r0,[r4,#{OPEN_OFF}];cmp r0,#0;beq good;
        add r0,r4,#{FILE_OFF};{n(0x1033832E)}{c('r1',SUCCESS)}cmp r0,r1;bne bad;
        good:movs r0,#0;str r0,[r4,#{OPEN_OFF}];mvn r0,#0;str r0,[r4,#{FD_OFF}];
        movs r0,#1;pop {{r4,pc}};
        bad:movs r0,#0;str r0,[r4,#{OPEN_OFF}];pop {{r4,pc}};'''
    # r4 state, r5 blob, r6 bounce buffer, r7 sum then offset, r8 blob length.
    codes['cmos_export'] = f'''push {{r4-r8,lr}};mov r4,r0;
        {c('r0',CTX + BLOB_FIELD)}ldr r5,[r0];
        tst r5,#3;bne no_data;{c('r0',RAM_LOW)}cmp r5,r0;blo no_data;
        {c('r0',RAM_HIGH - BLOB_BYTES)}cmp r5,r0;bhi no_data;
        ldr r6,[r4,#{BUFFER_OFF}];cmp r6,#0;bne have_buffer;
        {c('r0',CHUNK)}movs r1,#0;{n(0x10211F4C)}cmp r0,#0;beq no_memory;
        str r0,[r4,#{BUFFER_OFF}];mov r6,r0;
    have_buffer:
        movs r7,#0;mov r0,r5;{c('r1',BLOB_BYTES)}add r1,r5;
    sum_loop:ldr r2,[r0],#4;add r7,r2;cmp r0,r1;blo sum_loop;
        mov r0,r6;{c('r1',HEADER_BYTES)}{n(0x10199C40)}
        {c('r0',MAGIC)}str r0,[r6];movs r0,#{VERSION};str r0,[r6,#4];str r5,[r6,#8];
        {c('r0',BLOB_BYTES)}str r0,[r6,#12];str r7,[r6,#16];
        {c('r0',CTX)}str r0,[r6,#20];{c('r0',AREA)}str r0,[r6,#24];
        {c('r0',BANK)}str r0,[r6,#28];{c('r0',AREA+8)}ldr r0,[r0];str r0,[r6,#32];
        add r0,r6,#0x40;{c('r1',CTX)}movs r2,#{CTX_BYTES};{n(0x10199CF0, thumb=False)}
        add r0,r6,#0x140;{c('r1',AREA)}movs r2,#28;{n(0x10199CF0, thumb=False)}
        add r0,r6,#0x160;{c('r1',BANK)}movs r2,#44;{n(0x10199CF0, thumb=False)}
        mov r0,r4;{call('cmos_close')}cmp r0,#1;bne open_failed;
        add r0,r4,#{FILE_OFF};movs r1,#48;{n(0x10199C40)}
        mvn r0,#0;str r0,[r4,#{FD_OFF}];
        add r0,r4,#{FILE_OFF};{c('r1',path)}{n(0x1033829E)}
        add r0,r4,#{FILE_OFF};movw r1,#0x301;movs r2,#0;{n(0x103382A2)}mov r7,r0;
        ldr r0,[r4,#{FD_OFF}];cmp r0,#0;blt not_open;movs r0,#1;str r0,[r4,#{OPEN_OFF}];
    not_open:
        {c('r0',SUCCESS)}cmp r7,r0;bne open_failed;
        ldr r0,[r4,#{FD_OFF}];cmp r0,#0;blt open_failed;
        mov r0,r4;mov r1,r6;movw r2,#{HEADER_BYTES};{call('cmos_write')}cmp r0,#1;bne write_failed;
        movs r7,#0;{c('r8',BLOB_BYTES)}
    chunk_loop:
        mov r0,r6;add r1,r5,r7;{c('r2',CHUNK)}{n(0x10199CF0, thumb=False)}
        mov r0,r4;mov r1,r6;{c('r2',CHUNK)}{call('cmos_write')}cmp r0,#1;bne write_failed;
        {c('r0',CHUNK)}add r7,r0;cmp r7,r8;blo chunk_loop;
        mov r0,r4;{call('cmos_close')}cmp r0,#1;bne close_failed;
        movs r0,#1;b status;
    no_data:movs r0,#2;b status;
    no_memory:movs r0,#3;b status;
    open_failed:mov r0,r4;{call('cmos_close')}movs r0,#4;b status;
    write_failed:mov r0,r4;{call('cmos_close')}movs r0,#5;b status;
    close_failed:movs r0,#6;
    status:str r0,[r4,#{STATUS_OFF}];pop {{r4-r8,pc}};'''
    assert BLOB_BYTES % CHUNK == 0 and HEADER_BYTES <= CHUNK
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + state_bytes, default=0, fd_offset=FD_OFF, codes=codes,
                strings=strings, statuses=statuses, path=path)


def finish(plan, f, extension, identifiers, sizes=SIZES):
    ids = plan.alloc(struct.pack(f'<{len(identifiers)}I', *identifiers))
    extension['codes']['bitrate_open'] = extension['codes']['bitrate_open'].replace('CMOS_IDS;', c('r1', ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], sizes[name]))
    plan.features['cmos_dump'] = dict(
        version=VERSION, page_index=PAGE_INDEX, rows=ROWS,
        functions={k: hex(f[k] | 1) for k in sizes},
        page_offset=extension['page_offset'], state_offset=extension['state_offset'],
        file=PATH, magic=hex(MAGIC), header_bytes=HEADER_BYTES, blob_bytes=hex(BLOB_BYTES),
        context=hex(CTX), blob_field=hex(BLOB_FIELD), area_descriptor=hex(AREA),
        bank_descriptor=hex(BANK), bounce_buffer=f'{CHUNK} bytes from 0x10211F4C(size, pool=0), pinned',
        cache_clean_before_each_write=hex(CACHE_CLEAN),
        pointer_guard=f'aligned, {RAM_LOW:#x} <= p <= {RAM_HIGH - BLOB_BYTES:#x}',
        writes=['SD file A:\\WL-CMOS.BIN only'], sensor_writes=False, nor_writes=False,
        setting_writes=False, hooks_outside_menu=False, camera_verified=False)
