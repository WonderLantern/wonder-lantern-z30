# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""CMOS research page with a 4K readout-window experiment, on top of cmos_spi.

When a preset is selected, the CMOS serial-write observer recognises the 4K
main drive-mode packet (`81 00 00`, 24 bytes, type bytes 0C 21, stock start
row 308 and line count 3174 at registers 0x000A/0x000C), copies it to a page
buffer, replaces those two little-endian fields and lets the stock writer send
the copy. Every other packet, the drive data in RAM and NOR are unchanged.
Sensor registers are volatile: power-cycling restores stock behaviour, and
selecting Stock stops the rewrite at the next drive-mode change.

Rows: Save CMOS data, Arm SPI log, Window test (cycles the presets). The
status column shows the active preset; logs are read over USB (ptp_read).
"""
import struct
from ...assembly import constant as c, invoke as n
from ..menu.slots import page_slots
from . import cmos_dump as cd
from . import cmos_spi as cs

PAGE_INDEX = cd.PAGE_INDEX
ROWS = 3
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('video', ROWS)
SIGNATURE = (0x81, 0x00, 0x00, 0x0C, 0x21)
STOCK_START, STOCK_LINES = 308, 3174
PRESETS = (('Window: stock', None), ('Window: +64 lines', (276, 3238)), ('Window: 3:2 test', (106, 3396)))
WINDOW_PRESET = cs.STATE_BYTES
WINDOW_BUFFER = cs.STATE_BYTES + 4
# Per-mode capture records (ROM): the lookups 0x100F6630 (0x178-byte records, table 0x115A81E4,
# u16 height at +0) and 0x100F665A (0x94-byte records, table 0x115A8274, u16 height at +0x16)
# feed the capture/ISP window registers (0x20069D00 ...). 0x100F6CE0 reads the first table too.
# With a preset active the 4K records (capture codes 4 and 8) are served from a RAM copy whose
# height is the preset line count; with Stock the ROM record pointer is returned unchanged.
LOOKUP_SPLIT = 0x100F65FC
LOOKUPS = dict(
    a=dict(site=0x100F6630, prologue=bytes.fromhex('10b50446fff7e2ff'), table=0x115A81E4, size=0x178, height=0,
           records=(0x115A67E0, 0x115A6C48)),
    b=dict(site=0x100F665A, prologue=bytes.fromhex('10b50446fff7cdfff449'), table=0x115A8274, size=0x94, height=0x16,
           records=(0x115A7B80, 0x115A7D3C), lv_output=0x6C))
# Record b is the front-end resize: +0x16 input lines, +0x1A output width, +0x6C output lines.
# The 4K live-view record (code 4) decimates 4:1 with a fixed phase pattern (5600x3174 -> 1400x794),
# the same pattern photo live view uses for 3728 -> 932. With a preset its output is preset/4 lines
# (even), so the live view carries the whole readout instead of its top 3174 lines.
LV_RECORD = 0x115A7B80
# Live-view frame size: 0x1020C010(aspect) returns &table[0x115A4CC0 + aspect*4] (u16 width, height;
# 1400x932 for 3:2, 1400x794 for 16:9). Its callers derive the live-view buffer pitch and size.
# With a preset the 16:9 entry is served as 1400 x (preset/4, even); every other entry unchanged.
LV_SIZE_GETTER = 0x1020C010
LV_SIZE_PROLOGUE = bytes.fromhex('7f49403101eb8000')
LV_SIZE_TABLE, LV_SIZE_ENTRY = 0x115A4CC0, 0x115A4CC8
# Buffer planner 0x10143CD2(plan, settings) fills {type u8, ..., pitch u16 @2, width @4, height @6,
# width @8, height @0xA}. In 4K movie mode it hard-codes live view 1400x794 (0x10143DD0/DD4) and the
# recording intermediate 4000x2268. With a preset a planned 1400x794 becomes 1400 x (preset/4, even);
# pitch and every other plan are unchanged.
# Display crop table (static RAM, base 0xC18DCEA0, 0x20-byte entries {src w, src h, x, y, crop w, crop h,
# crop w, crop h} as u32): the two 4K live-view entries are source 1400x794 cropped to 139x x 782.
# Window test rewrites their heights per preset (source preset/4, crop source-12) and only over a value
# it recognises; Stock restores 794/782.
DISPLAY_FIELDS = tuple((entry + off, values) for entry in (0xC18DCF00, 0xC18DCF20)
                       for off, values in ((4, (794, 808, 848)), (0x14, (782, 796, 836)), (0x1C, (782, 796, 836))))
PLANNER = 0x10143CD2
PLANNER_PROLOGUE = bytes.fromhex('2de9f05f044602220326')
LOOKUP_XY = 0x100F6CE0
LOOKUP_XY_PROLOGUE = bytes.fromhex('70b514460546fff7')
WINDOW_RECORDS = WINDOW_BUFFER + 24 + 4
RECORD_OFFSETS = dict(a=WINDOW_RECORDS, b=WINDOW_RECORDS + 2 * LOOKUPS['a']['size'])
# Capture crop window: 0x100CB682(rec) is the only caller path of the writer 0x1011FD80, which
# programs 0x2006BA00..0C from rec+0x1E/+0x20/+0x22/+0x24 (x, y, width, height; stock 0, 0,
# 5600, 3174 in 4K). With a preset active a 5600x3174 rec is passed as a copy whose height is
# the preset line count; the firmware's own struct is never written.
CROP_WRAPPER = 0x100CB682
CROP_WRAPPER_BYTES = bytes.fromhex('01467948006854f07abb')
CROP_WRITER = 0x1011FD80
CROP_CONTEXT = 0xC180913C
CROP_BYTES = 0x28
CROP_COPY = WINDOW_RECORDS + 2 * (LOOKUPS['a']['size'] + LOOKUPS['b']['size'])
STATE_BYTES = CROP_COPY + CROP_BYTES
STATUS = cs.STATUS + tuple(label for label, _ in PRESETS)
WINDOW_STATUS = len(cs.STATUS)
SIZES = dict(cs.SIZES, bitrate_labels=448, bitrate_action=256, spi_hook=1024, window_cycle=1024,
             size_hook=192, size_original=32, record_fix_a=192, record_fix_b=192, lookup_a=96, lookup_b=96,
             lookup_xy=64, crop_hook=160, lv_size=96, plan_hook=128, plan_original=24)
# Stock 0x10132128(request, size_out, offset_out) returns the sensor raw size per capture
# code (5600x3174 for the 4K codes 4 and 8). With a preset active the wrapper reports
# the preset's line count for exactly that size, so capture is configured for the taller
# sensor window. Every other size is returned unchanged.
RAW_SIZE = 0x10132128
RAW_SIZE_PROLOGUE = bytes.fromhex('f0b5002414805480')  # push {r4-r7,lr}; movs r4,#0; strh r4,[r2]; strh r4,[r2,#2]
RAW_WIDTH = 5600
# Drive data (RAM copy at [CTX+0x14]) holds a 0x80-byte geometry record per drive type at
# 0x5000 + type*0x80; +0x40..+0x46 are the capture window x, y, width, height. Window test
# writes the 4K record height (type 6) in RAM only, and only over a value it recognises.
GEOMETRY_HEIGHT = 0x5000 + 6 * 0x80 + 0x46
# Two further 4K line counts in drive data: the 0xA0-byte SIF record (0x8E0 + 6*0xA0,
# fields 5656, 5600, 56, 186, 3174 ...) and the size table at 0xF01AA (5600 at +0xC, 3174 at +0x10).
HEIGHT_OFFSETS = (GEOMETRY_HEIGHT, 0x8E0 + 6 * 0xA0 + 0x58, 0xF01B6)


def prepare(plan, f, resources, allocation_size):
    table = plan.alloc(b''.join(struct.pack('<2H', start, lines) for _, value in PRESETS[1:]
                                for start, lines in [value]))
    stock = struct.pack('<2H', STOCK_START, STOCK_LINES)
    page = cs.prepare(plan, f, resources, allocation_size, sizes=SIZES,
                      window=dict(preset_offset=WINDOW_PRESET, buffer_offset=WINDOW_BUFFER, table=table,
                                  presets=len(PRESETS) - 1, signature=SIGNATURE, stock=stock))
    for name in ('window_cycle', 'size_hook', 'size_original'):
        f[name] = plan.alloc(bytes(SIZES[name]))
    for name in ('bitrate_labels', 'bitrate_action'):
        f[name] = plan.alloc(bytes(SIZES[name]))
    codes, strings = page['codes'], page['strings']
    page['allocation_size'] = page['state_offset'] + STATE_BYTES
    statuses = plan.alloc(struct.pack(f'<{len(STATUS)}I', *(resources.alloc(s) for s in STATUS)))
    rows = plan.alloc(struct.pack('<3I', strings['action'], resources.alloc('Arm SPI log'),
                                  resources.alloc('Window test')))
    call = lambda name: n(f[name])
    codes['bitrate_labels'] = f'''push {{r4,r5,lr}};sub sp,#4;
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        movs r5,#0;
    rows:{c('r0',rows)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{ROWS};blo rows;
        {call('bitrate_get')}movs r4,#{WINDOW_STATUS};cmp r0,#0;beq put;
        ldr r4,[r0,#{cd.STATUS_OFF}];cmp r4,#{len(STATUS)-1};it hi;movhi r4,#0;
        put:{c('r0',statuses)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;add sp,#4;pop {{r4,r5,pc}};'''
    codes['bitrate_action'] = f'''push {{r4,r5,lr}};sub sp,#4;mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r0;cmp r5,#{ROWS-1};bhi done;
        {call('bitrate_get')}cmp r0,#0;beq done;
        cmp r5,#0;bne row1;{call('cmos_export')}b refresh;
        row1:cmp r5,#1;bne row2;{call('spi_arm')}b refresh;
        row2:{call('window_cycle')}
        refresh:{call('bitrate_labels')}mov r0,r4;{call('page_return')}
        done:movs r0,#0;add sp,#4;pop {{r4,r5,pc}};'''
    # Menu task: next preset (wraps to Stock); the status column names it.
    heights = [STOCK_LINES] + [value[1] for _, value in PRESETS[1:]]
    known = ''.join(f'movw r1,#{h};cmp r3,r1;beq known;' for h in heights)
    display_tables = [plan.alloc(struct.pack('<3H', *values) + bytes(2)) for _, values in DISPLAY_FIELDS]
    display_block = ''.join(
        f"{c('r2',address)}ldrh r3,[r2];"
        + ''.join(f"movw r1,#{v};cmp r3,r1;beq dm{i};" for v in values)
        + f"b dn{i};dm{i}:{c('r1',display_tables[i])}ldrh r1,[r1,r0,lsl #1];strh r1,[r2];dn{i}:"
        for i, (address, values) in enumerate(DISPLAY_FIELDS))
    codes['window_cycle'] = f'''push {{r4,lr}};mov r4,r0;
        {c('r1',WINDOW_PRESET)}ldr r0,[r4,r1];adds r0,#1;cmp r0,#{len(PRESETS)-1};it hi;movhi r0,#0;
        dmb ish;str r0,[r4,r1];adds r0,#{WINDOW_STATUS};str r0,[r4,#{cd.STATUS_OFF}];
        {c('r1',WINDOW_PRESET)}ldr r0,[r4,r1];
        {display_block}
        {c('r2',cd.CTX + cd.BLOB_FIELD)}ldr r2,[r2];tst r2,#3;bne done;
        {c('r1',cd.RAM_LOW)}cmp r2,r1;blo done;{c('r1',cd.RAM_HIGH - cd.BLOB_BYTES)}cmp r2,r1;bhi done;
        mov r12,r2;
        {c('r1',WINDOW_PRESET)}ldr r0,[r4,r1];movw r3,#{STOCK_LINES};cmp r0,#0;beq chosen;
        {c('r1',table)}subs r0,#1;ldr r3,[r1,r0,lsl #2];lsrs r3,r3,#16;
    chosen:mov r4,r3;
        {''.join(f"{c('r2',off)}add r2,r12;ldrh r3,[r2];{known.replace('known', f'k{i}')}b s{i};k{i}:strh r4,[r2];s{i}:" for i, off in enumerate(HEIGHT_OFFSETS))}
        dmb ish;
    done:pop {{r4,pc}};'''
    codes['size_original'] = f'''push {{r4-r7,lr}};movs r4,#0;strh r4,[r2];strh r4,[r2,#2];
        {c('r12', RAW_SIZE + 8 + 1)}bx r12;'''
    codes['size_hook'] = f'''push {{r4,r5,lr}};sub sp,#4;mov r4,r1;{call('size_original')}str r0,[sp];
        ldrh r0,[r4];movw r1,#{RAW_WIDTH};cmp r0,r1;bne done;
        ldrh r0,[r4,#2];movw r1,#{STOCK_LINES};cmp r0,r1;bne done;
        {call('get')}cmp r0,#0;beq done;{c('r1',page['state_offset'] + WINDOW_PRESET)}ldr r0,[r0,r1];
        cmp r0,#0;beq done;cmp r0,#{len(PRESETS)-1};bhi done;
        {c('r1',table)}subs r0,#1;ldr r1,[r1,r0,lsl #2];lsrs r1,r1,#16;strh r1,[r4,#2];
        done:ldr r0,[sp];add sp,#4;pop {{r4,r5,pc}};'''
    for name in ('record_fix_a', 'record_fix_b', 'lookup_a', 'lookup_b', 'lookup_xy', 'crop_hook', 'lv_size', 'plan_hook', 'plan_original'):
        f[name] = plan.alloc(bytes(SIZES[name]))
    def lv_patch(spec):
        if 'lv_output' not in spec:
            return ''
        return f'''{c('r1',LV_RECORD)}cmp r4,r1;bne lvskip;lsrs r3,r6,#2;bic r3,r3,#1;strh r3,[r7,#{spec['lv_output']}];lvskip:'''
    for key, spec in LOOKUPS.items():
        first, second = spec['records']
        word, shift = spec['height'] & ~3, (spec['height'] & 2) * 8
        # r0 = record pointer or 0. Returns the pointer, or the preset copy for a 4K record.
        codes[f'record_fix_{key}'] = f'''push {{r4-r7,lr}};mov r4,r0;cmp r4,#0;beq done;
            movs r7,#0;{c('r1',first)}cmp r4,r1;beq found;movs r7,#{spec['size']};{c('r1',second)}cmp r4,r1;bne done;
        found:{call('get')}cmp r0,#0;beq done;{c('r1',page['state_offset'] + WINDOW_PRESET)}ldr r2,[r0,r1];
            cmp r2,#0;beq done;cmp r2,#{len(PRESETS)-1};bhi done;
            {c('r1',table)}subs r2,#1;ldr r6,[r1,r2,lsl #2];lsrs r6,r6,#16;
            {c('r1',page['state_offset'] + RECORD_OFFSETS[key])}add r0,r1;add r7,r0;
            movs r5,#0;
        copy:ldr r3,[r4,r5];cmp r5,#{word};bne store;bfi r3,r6,#{shift},#16;
        store:str r3,[r7,r5];adds r5,#4;cmp r5,#{spec['size']};blo copy;
            {lv_patch(spec)}dmb ish;mov r4,r7;
        done:mov r0,r4;pop {{r4-r7,pc}};'''
        # Same contract as the stock lookup (r0 = capture code, r1 = variant); record via the fix.
        codes[f'lookup_{key}'] = f'''push {{r4,lr}};mov r4,r0;{n(LOOKUP_SPLIT)}
            {c('r1',spec['table'])}add r1,r1,r4,lsl #3;ldr r2,[r1,#4];cmp r2,r0;ble none;
            movw r2,#{spec['size']};muls r0,r2,r0;ldr r1,[r1];add r0,r1;{call(f'record_fix_{key}')}pop {{r4,pc}};
        none:movs r0,#0;pop {{r4,pc}};'''
    codes['lookup_xy'] = f'''push {{r4,lr}};mov r4,r2;{call('lookup_a')}cmp r0,#0;beq done;
        ldrh r1,[r0,#6];strh r1,[r4];ldrh r0,[r0,#2];strh r0,[r4,#2];movs r0,#1;
        done:pop {{r4,pc}};'''
    codes['crop_hook'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r1,[r4,#0x22];movw r2,#{RAW_WIDTH};cmp r1,r2;bne go;
        ldrh r1,[r4,#0x24];movw r2,#{STOCK_LINES};cmp r1,r2;bne go;
        {call('get')}cmp r0,#0;beq go;{c('r1',page['state_offset'] + WINDOW_PRESET)}ldr r2,[r0,r1];
        cmp r2,#0;beq go;cmp r2,#{len(PRESETS)-1};bhi go;
        {c('r1',table)}subs r2,#1;ldr r6,[r1,r2,lsl #2];lsrs r6,r6,#16;
        {c('r1',page['state_offset'] + CROP_COPY)}add r5,r0,r1;movs r1,#0;
    copy:ldr r2,[r4,r1];str r2,[r5,r1];adds r1,#4;cmp r1,#{CROP_BYTES};blo copy;
        strh r6,[r5,#0x24];dmb ish;mov r4,r5;
    go:mov r1,r4;{c('r0',CROP_CONTEXT)}ldr r0,[r0];{c('r12',CROP_WRITER | 1)}pop {{r4-r6,lr}};bx r12;'''
    lv_sizes = plan.alloc(b''.join(struct.pack('<2H', 1400, (v[1] // 4) & ~1) for _, v in PRESETS[1:]))
    codes['lv_size'] = f'''push {{r2-r4,lr}};{c('r4',LV_SIZE_TABLE)}add r4,r4,r0,lsl #2;
        {c('r1',LV_SIZE_ENTRY)}cmp r4,r1;bne done;
        {call('get')}cmp r0,#0;beq done;{c('r1',page['state_offset'] + WINDOW_PRESET)}ldr r2,[r0,r1];
        cmp r2,#0;beq done;cmp r2,#{len(PRESETS)-1};bhi done;
        subs r2,#1;{c('r4',lv_sizes)}add r4,r4,r2,lsl #2;
    done:mov r0,r4;pop {{r2-r4,pc}};'''
    codes['plan_original'] = f'''push.w {{r4-r12,lr}};mov r4,r0;movs r2,#2;movs r6,#3;
        {c('r12', PLANNER + len(PLANNER_PROLOGUE) + 1)}bx r12;'''
    codes['plan_hook'] = f'''push {{r4,r5,r6,lr}};mov r4,r0;{call('plan_original')}mov r5,r0;
        ldrh r1,[r4,#4];movw r2,#1400;cmp r1,r2;bne done;ldrh r1,[r4,#6];movw r2,#794;cmp r1,r2;bne done;
        {call('get')}cmp r0,#0;beq done;{c('r1',page['state_offset'] + WINDOW_PRESET)}ldr r2,[r0,r1];
        cmp r2,#0;beq done;cmp r2,#{len(PRESETS)-1};bhi done;
        {c('r1',table)}subs r2,#1;ldr r6,[r1,r2,lsl #2];lsrs r6,r6,#19;lsls r6,r6,#1;
        strh r6,[r4,#6];ldrh r1,[r4,#0xa];movw r2,#794;cmp r1,r2;bne done;strh r6,[r4,#0xa];
    done:mov r0,r5;pop {{r4,r5,r6,pc}};'''
    from ...assembly import jump as _jump
    actual = plan.image.read(PLANNER, len(PLANNER_PROLOGUE))
    if actual != PLANNER_PROLOGUE:
        raise ValueError(f'Unexpected buffer planner prologue: {actual.hex()}')
    plan.edit(PLANNER, _jump(PLANNER, f['plan_hook'], len(PLANNER_PROLOGUE)), 'Preset-height 4K live-view plan',
              PLANNER_PROLOGUE)
    actual = plan.image.read(LV_SIZE_GETTER, len(LV_SIZE_PROLOGUE))
    if actual != LV_SIZE_PROLOGUE:
        raise ValueError(f'Unexpected live-view size getter: {actual.hex()}')
    plan.edit(LV_SIZE_GETTER, _jump(LV_SIZE_GETTER, f['lv_size']), 'Preset-height 16:9 live-view frame size',
              LV_SIZE_PROLOGUE)
    actual = plan.image.read(CROP_WRAPPER, len(CROP_WRAPPER_BYTES))
    if actual != CROP_WRAPPER_BYTES:
        raise ValueError(f'Unexpected crop writer wrapper: {actual.hex()}')
    plan.edit(CROP_WRAPPER, _jump(CROP_WRAPPER, f['crop_hook'], len(CROP_WRAPPER_BYTES)),
              'Pass a preset-height copy of the 4K crop window', CROP_WRAPPER_BYTES)
    for key, spec in LOOKUPS.items():
        actual = plan.image.read(spec['site'], len(spec['prologue']))
        if actual != spec['prologue']:
            raise ValueError(f"Unexpected capture-record lookup prologue at {spec['site']:#x}: {actual.hex()}")
        plan.edit(spec['site'], _jump(spec['site'], f[f'lookup_{key}'], len(spec['prologue'])),
                  'Serve preset-height 4K capture record', spec['prologue'])
    actual = plan.image.read(LOOKUP_XY, 8)
    if actual != LOOKUP_XY_PROLOGUE:
        raise ValueError(f'Unexpected capture-record offset lookup prologue: {actual.hex()}')
    plan.edit(LOOKUP_XY, _jump(LOOKUP_XY, f['lookup_xy']), 'Capture-record offsets via the preset lookup',
              LOOKUP_XY_PROLOGUE)
    actual = plan.image.read(RAW_SIZE, 8)
    if actual != RAW_SIZE_PROLOGUE:
        raise ValueError(f'Unexpected sensor raw-size getter prologue: {actual.hex()}')
    from ...assembly import jump
    plan.edit(RAW_SIZE, jump(RAW_SIZE, f['size_hook']), 'Report preset line count as sensor raw height',
              RAW_SIZE_PROLOGUE)
    return page


def finish(plan, f, extension, identifiers):
    cs.finish(plan, f, extension, identifiers, sizes=SIZES)
    feature = plan.features.pop('cmos_spi')
    feature.update(rows=ROWS, window=dict(
        signature=bytes(SIGNATURE).hex(), registers=dict(start='0x000A', lines='0x000C'),
        stock=dict(start=STOCK_START, lines=STOCK_LINES),
        presets=[dict(label=label, start=v[0], lines=v[1]) if v else dict(label=label) for label, v in PRESETS],
        preset_offset=WINDOW_PRESET, buffer_offset=WINDOW_BUFFER, status_base=WINDOW_STATUS,
        behaviour='4K main drive-mode packet copied and rewritten in RAM; drive data and NOR unchanged',
        height_offsets=[hex(o) for o in HEIGHT_OFFSETS],
        capture_records={k: dict(site=hex(v['site']), table=hex(v['table']), size=hex(v['size']),
                                 height=hex(v['height']), records=[hex(r) for r in v['records']],
                                 state_offset=RECORD_OFFSETS[k]) for k, v in LOOKUPS.items()},
        crop=dict(wrapper=hex(CROP_WRAPPER), writer=hex(CROP_WRITER), fields='+0x22 width, +0x24 height',
                  state_offset=CROP_COPY),
        raw_size_getter=dict(site=hex(RAW_SIZE), displaced=RAW_SIZE_PROLOGUE.hex(),
                             rule=f'{RAW_WIDTH}x{STOCK_LINES} reported as {RAW_WIDTH}x<preset lines> while a preset is active')))
    feature['status_codes'] = list(STATUS)
    plan.features['cmos_window'] = feature
