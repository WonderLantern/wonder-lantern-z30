"""Opt-in single-JPEG ownership experiment at stock save boundaries.

Default off. No synthetic shutter input, mode writes, capture suppression,
producer pointer retention, file I/O in capture callbacks, or module ABI change.
The original save BL and subsequent original pool return remain in place.
"""
import struct
from ..assembly import assemble, constant as c, invoke as n, jump
from .menu_slots import page_slots
from .sound_layout import SUCCESS
from . import precapture_capture as capture

PAGE_INDEX = 960
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('video', 3)
MAGIC = 0x31504A57  # WJP1
MAX_FRAME = 8 * 1024 * 1024
FILLER, MAX_PADDING = 0xC2897B20, 512
RECORDS, RECORD_COUNT, RECORD_SIZE = 112, 64, 24
SEGMENTS = RECORDS + RECORD_COUNT * RECORD_SIZE
SEGMENT_BYTES = 6 * 16
assert SEGMENTS + SEGMENT_BYTES == capture.TASK
STATE_BYTES = capture.STATE_BYTES
PROFILES = ('Off', 'Half press one e-JPG', 'Export copy and log')
STATUS = ('Off', 'Waiting for JPG', 'Copy ready', 'Saved to SD', 'No memory',
          'Too large', 'Unsupported segments', 'Invalid JPG', 'Export failed', 'Busy', 'Log saved',
          'Enable silent photo', 'Worker unavailable', 'Capture refused', 'Capture pending')
SIZES = dict(bitrate_get=96, bitrate_labels=384, bitrate_open=192,
             bitrate_action=256, bitrate_restore=96, pre_arm=640, pre_copy=1536,
             pre_log=256, pre_hook_1=192, pre_hook_0=192, pre_input=256, pre_keys=256,
             pre_export=1280, pre_write=256, pre_close=192)
SIZES.update(capture.SIZES)


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in
               dict(title='Half press JPG trial', empty='', mark='*').items()}
    labels = plan.alloc(struct.pack('<3I', *(resources.alloc(s) for s in PROFILES)))
    statuses = plan.alloc(struct.pack(f'<{len(STATUS)}I', *(resources.alloc(s) for s in STATUS)))
    jpg_path = plan.alloc(b'A:\\WL-PRE.JPG\0')
    log_path = plan.alloc(b'A:\\WL-PRE.DBG\0')
    call = lambda name: n(f[name])
    codes = {}
    codes['bitrate_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    codes['bitrate_restore'] = f'''push {{r4,lr}};{call('bitrate_get')}cmp r0,#0;beq done;
        {c('r1',MAGIC)}str r1,[r0,#4];done:pop {{r4,pc}};'''
    codes['bitrate_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        movs r5,#0;
    labels_loop:
        {c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#3;blo labels_loop;
        {call('bitrate_get')}movs r4,#0;cmp r0,#0;beq put_status;
        ldr r4,[r0,#36];cmp r4,#{len(STATUS)-1};it hi;movhi r4,#8;
    put_status:
        {c('r0',statuses)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['bitrate_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;movw r1,#{PAGE_INDEX};{n(0x1045F732)}
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        PRE_IDS;ldr r1,[r1];{n(0x1045D54C)}
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['bitrate_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r0;cmp r5,#2;bhi done;
        {call('bitrate_get')}cmp r0,#0;beq done;mov r4,r0;
        cmp r5,#2;beq export;
        mov r1,r5;{call('pre_arm')}b refresh;
        export:{call('pre_export')}
        refresh:{call('bitrate_labels')}movs r0,#2;movw r1,#955;{n(0x1045F732)}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    # State: selected/magic/enabled/armed/copy-lock/buffer/length/mode/object/
    # status/save-selector/copies/log-sequence/reserved/open/log-lock; then a
    # persistent 48-byte file object and 64 records (six scalar words each).
    codes['pre_arm'] = f'''push {{r4-r6,lr}};mov r4,r0;mov r5,r1;
        add r2,r4,#16;ldrex r0,[r2];cmp r0,#0;bne busy;
        movs r0,#1;strex r1,r0,[r2];cmp r1,#0;bne busy;
        cmp r5,#0;bne arm;
        str r5,[r4,#8];str r5,[r4,#12];str r5,[r4];str r5,[r4,#36];
        str r5,[r4,#{capture.TICKET}];b unlock;
    arm:
        ldr r0,[r4,#{capture.PHASE}];cmp r0,#1;blo idle;cmp r0,#3;bhi idle;
        movs r0,#9;str r0,[r4,#36];b unlock;
    idle:
        ldr r0,[r4,#20];cmp r0,#0;bne allocated;
        {c('r0',MAX_FRAME)}movs r1,#0;{n(0x10211F4C)}cmp r0,#0;beq no_memory;
        str r0,[r4,#20];
    allocated:
        {c('r0',SEGMENTS)}add r0,r4;movs r1,#{SEGMENT_BYTES};{n(0x10199C40)}
        movs r0,#0;str r0,[r4,#24];str r0,[r4,#8];
        movs r0,#1;str r0,[r4];str r0,[r4,#12];str r0,[r4,#36];
        dmb ish;str r0,[r4,#8];
        movs r0,#0;str r0,[r4,#{capture.TICKET}];str r0,[r4,#{capture.PHASE}];
        mov r0,r4;{call('pre_task')}cmp r0,#1;bne unlock;
        movs r0,#1;dmb ish;str r0,[r4,#{capture.TICKET}];b unlock;
    no_memory:movs r0,#4;str r0,[r4,#36];
    unlock:movs r0,#0;dmb ish;str r0,[r4,#16];pop {{r4-r6,pc}};
    busy:clrex;movs r0,#0;pop {{r4-r6,pc}};'''
    # Snapshot the descriptor subset through ORIGINAL extraction/order code.
    # Match stock output: (logical_length - padding) producer bytes, followed
    # by padding bytes from the stock 512-byte filler buffer. Never extend a
    # read into producer RAM by the logical length or modify the filler buffer.
    # Source object and data are used only synchronously before the stock BL.
    codes['pre_copy'] = f'''push {{r4-r11,lr}};sub sp,#108;
        mov r4,r0;mov r5,r1;mov r6,r2;
        ldr r0,[r4,#8];cmp r0,#1;bne done;
        ldr r0,[r4,#12];cmp r0,#1;bne done;
        add r3,r4,#16;ldrex r0,[r3];cmp r0,#0;bne locked;
        movs r0,#1;strex r1,r0,[r3];cmp r1,#0;bne locked;
        ldr r0,[r4,#8];cmp r0,#1;bne unlock;
        ldr r0,[r4,#12];cmp r0,#1;bne unlock;
        movs r0,#0;str r0,[r4,#12];str r0,[r4,#24];
        str r5,[r4,#28];str r6,[r4,#32];
        cmp r5,#3;bhi unsupported;
        ldr r7,[r4,#20];cmp r7,#0;beq memory;
        ldr r0,[r6,#28];cmp r0,#0;bne large;
        ldr r0,[r6,#24];cmp r0,#4;blo invalid;
        str r0,[r4,#52];{c('r1',MAX_FRAME)}cmp r0,r1;bhi large;str r0,[sp,#96];
        mov r0,r5;{n(0x102B6FBE)}cmp r0,#0;beq unsupported;
        cmp r0,#6;bhi unsupported;mov r8,r0;movs r9,#0;movs r10,#0;
    extract_loop:
        mov r0,r5;mov r1,r9;{n(0x102B68E0)}mov r2,r0;
        {c('r1',SEGMENTS)}add r1,r4;add r1,r1,r9,lsl #4;str r0,[r1];
        movs r0,#12;mul r0,r9,r0;add r11,sp,r0;
        mov r0,r5;mov r1,r6;mov r3,r11;{n(0x102B688E)}str r0,[sp,#100];
        {c('r3',SEGMENTS)}add r3,r4;add r3,r3,r9,lsl #4;
        ldm r11,{{r0,r1,r2}};str r0,[r3,#4];str r1,[r3,#8];str r2,[r3,#12];
        ldr r0,[sp,#100];
        cmp r0,#1;beq next_segment;cmp r0,#0;bne unsupported;
        ldr r0,[r11,#8];cmp r0,#{MAX_PADDING};bhi unsupported;
        ldr r1,[r11,#4];cmp r0,r1;bhi unsupported;
        ldr r0,[r11];cmp r0,#0;beq unsupported;
        ldr r1,[r11,#4];cmp r1,#0;beq unsupported;
        ldr r2,[r11,#8];subs r2,r1,r2;adds r2,r0,r2;bcs unsupported;
        {c('r3',MAX_FRAME)}add r3,r7;cmp r0,r3;bhs no_alias;
        cmp r2,r7;bhi unsupported;
    no_alias:
        adds r10,r1;bcs large;ldr r0,[sp,#96];cmp r10,r0;bhi unsupported;
    next_segment:
        adds r9,#1;cmp r9,r8;blo extract_loop;
        ldr r0,[sp,#96];cmp r0,r10;bne unsupported;
        movs r9,#0;mov r11,r7;
    segments_loop:
        movs r0,#12;mul r0,r9,r0;add r0,sp,r0;
        ldr r1,[r0];ldr r2,[r0,#4];ldr r3,[r0,#8];
        sub r2,r3;str r2,[sp,#76];str r3,[sp,#80];
        cmp r2,#0;beq copy_padding;
        mov r0,r11;{n(0x10199CF0,thumb=False)}
        ldr r2,[sp,#76];add r11,r2;
    copy_padding:
        ldr r2,[sp,#80];cmp r2,#0;beq copy_next;
        mov r0,r11;{c('r1',FILLER)}{n(0x10199CF0,thumb=False)}
        ldr r2,[sp,#80];add r11,r2;
    copy_next:
        adds r9,#1;cmp r9,r8;blo segments_loop;
        ldrb r0,[r7];cmp r0,#255;bne invalid;
        ldrb r0,[r7,#1];cmp r0,#216;bne invalid;
        ldrb r0,[r11,#-2];cmp r0,#255;bne invalid;
        ldrb r0,[r11,#-1];cmp r0,#217;bne invalid;
        str r10,[r4,#24];ldr r0,[r4,#44];adds r0,#1;str r0,[r4,#44];
        movs r0,#2;b status;
    memory:movs r0,#4;b status;
    large:movs r0,#5;b status;
    unsupported:movs r0,#6;b status;
    invalid:movs r0,#7;
    status:str r0,[r4,#36];
    unlock:movs r0,#0;dmb ish;str r0,[r4,#16];b done;
    locked:clrex;
    done:movs r0,#0;add sp,#108;pop {{r4-r11,pc}};'''
    codes['pre_log'] = f'''push {{r4-r7,lr}};sub sp,#4;
        mov r4,r0;ldr r5,[r4,#8];cmp r5,#1;bne done;
        add r5,r4,#60;ldrex r6,[r5];cmp r6,#0;bne locked;
        movs r6,#1;strex r7,r6,[r5];cmp r7,#0;bne locked;
        ldr r6,[r4,#48];and r7,r6,#63;movs r0,#{RECORD_SIZE};mul r7,r0;
        add r7,r4;adds r7,#{RECORDS};adds r6,#1;
        str r1,[r7,#4];str r2,[r7,#8];str r3,[r7,#12];
        ldr r0,[r4,#36];str r0,[r7,#16];ldr r0,[r4,#24];str r0,[r7,#20];
        dmb ish;str r6,[r7];str r6,[r4,#48];movs r0,#0;dmb ish;str r0,[r5];b done;
    locked:clrex;
    done:add sp,#4;pop {{r4-r7,pc}};'''
    for selector, site in ((1, 0x1009F9B8), (0, 0x1009FC28)):
        name = f'pre_hook_{selector}'
        expected = bytes.fromhex(('0120' if selector else '0020') + '394605f13002')
        codes[name] = f'''push {{r0-r3,r12,lr}};
            {call('bitrate_get')}cmp r0,#0;beq resume;
            mov r1,r7;add r2,r5,#0x30;{call('pre_copy')}
            {call('bitrate_get')}cmp r0,#0;beq resume;
            movs r1,#{selector+1};mov r2,r7;add r3,r5,#0x30;{call('pre_log')}
        resume:pop {{r0-r3,r12,lr}};
            movs r0,#{selector};mov r1,r7;add.w r2,r5,#0x30;
            {c('r12',site+9)}bx r12;'''
        plan.edit(site, jump(site, f[name]), f'JPG probe before stock save selector {selector}', expected)
    # Record release-controller messages without changing their payload/order.
    site = 0x100636F0
    codes['pre_input'] = f'''push {{r0-r3,r12,lr}};
        {call('bitrate_get')}cmp r0,#0;beq resume;
        movs r1,#3;ldr r2,[sp];ldr r3,[sp,#4];{call('pre_log')}
        resume:pop {{r0-r3,r12,lr}};
        {c('r3',0x04260012)}subs r2,r0,r3;cmp r0,r3;bne unequal;
        {c('r12',0x10063763)}bx r12;
        unequal:{c('r12',site+9)}bx r12;'''
    plan.edit(site, jump(site, f['pre_input']), 'Observe release-controller message IDs',
              bytes.fromhex('484bc21a984234d0'))
    # The physical SW0 constructor, before any UI/remote key rewriting:
    # current/previous/XOR/rising/falling at offsets 0/4/8/12/16.
    # Preserve the actual stock constructor results, APSR and all registers.
    # This observer neither injects keys nor starts/stops a capture.
    codes['pre_keys'] = f'''push {{r4,r5,r12,lr}};{n(0x101DD502)}
        push {{r0-r3,r12,lr}};mrs r4,apsr;
        ldr r2,[r0];ldr r3,[r0,#4];eor r1,r2,r3;
        tst r1,#0xc0000000;beq keys_done;
        {call('bitrate_get')}cmp r0,#0;beq keys_done;
        ldr r1,[sp];ldr r2,[r1];ldr r3,[r1,#4];
        and r2,r2,#0xc0000000;and r3,r3,#0xc0000000;
        movs r1,#4;{call('pre_log')}
        {call('bitrate_get')}cmp r0,#0;beq keys_done;
        ldr r1,[sp];ldr r2,[r1,#4];ldr r1,[r1];{call('pre_wake')}
    keys_done:msr apsr_nzcvq,r4;pop {{r0-r3,r12,lr}};
        pop {{r4,r5,r12,lr}};
        {c('lr',0x101E3397)}ldr r2,[r5,#4];sub.w r0,r4,#0x174;'''
    site = 0x101E3392
    plan.edit(site, jump(site, f['pre_keys'], length=10), 'Observe physical SW0 release-button edges',
              bytes.fromhex('faf7b6f86a68a4f5ba70'))
    # Menu-only file writer. Check both actual count and the return code.
    codes['pre_write'] = f'''push {{r4-r8,lr}};sub sp,#8;
        mov r4,r0;mov r5,r1;mov r6,r2;
    loop:cmp r6,#0;beq success;
        {c('r7',0x100000)}cmp r6,r7;it lo;movlo r7,r6;
        movs r0,#0;str r0,[sp];mov r0,r4;mov r1,r5;mov r2,r7;mov r3,sp;{n(0x102C8A6A)}
        cmp r0,#0;bne failed;ldr r0,[sp];cmp r0,r7;bne failed;
        add r5,r7;sub r6,r7;b loop;
    success:movs r0,#1;b done;
    failed:movs r0,#0;
    done:add sp,#8;pop {{r4-r8,pc}};'''
    codes['pre_close'] = f'''push {{r4,lr}};mov r4,r0;
        ldr r0,[r4,#56];cmp r0,#0;beq success;
        add r0,r4,#64;{n(0x1033832E)}{c('r1',SUCCESS)}cmp r0,r1;bne failure;
        movs r0,#0;str r0,[r4,#56];mvn r0,#0;str r0,[r4,#72];
    success:movs r0,#1;pop {{r4,pc}};
    failure:movs r0,#0;pop {{r4,pc}};'''
    codes['pre_export'] = f'''push {{r4-r8,lr}};mov r4,r0;
        {c('r0',0x188C)}{n(0x101993DE)}cmp r0,#0;bne done;
        mov r0,r4;{call('pre_inspect')}
        add r2,r4,#16;ldrex r0,[r2];cmp r0,#0;bne locked;
        movs r0,#1;strex r1,r0,[r2];cmp r1,#0;bne locked;
        add r2,r4,#60;ldrex r0,[r2];cmp r0,#0;bne log_locked;
        movs r0,#1;strex r1,r0,[r2];cmp r1,#0;bne log_locked;
        movs r0,#0;str r0,[r4,#8];str r0,[r4,#12];str r0,[r4];
        mov r0,r4;{call('pre_close')}cmp r0,#1;bne failed;
        movs r5,#0;
    next_file:
        cmp r5,#0;bne select_log;
        ldr r0,[r4,#24];cmp r0,#0;beq advance;{c('r6',jpg_path)}b open;
    select_log:{c('r6',log_path)}
    open:
        add r0,r4,#64;movs r1,#48;{n(0x10199C40)}
        mvn r0,#0;str r0,[r4,#72];add r0,r4,#64;mov r1,r6;{n(0x1033829E)}
        add r0,r4,#64;movw r1,#0x301;movs r2,#0;{n(0x103382A2)}
        mov r6,r0;ldr r0,[r4,#72];cmp r0,#0;blt opened;
        movs r0,#1;str r0,[r4,#56];
    opened:{c('r0',SUCCESS)}cmp r6,r0;bne failed;
        ldr r0,[r4,#72];cmp r0,#0;blt failed;
        cmp r5,#0;bne write_log;
        ldr r0,[r4,#72];ldr r1,[r4,#20];ldr r2,[r4,#24];{call('pre_write')}
        mov r7,r0;b close;
    write_log:
        ldr r0,[r4,#72];mov r1,r4;movs r2,#64;{call('pre_write')}
        mov r7,r0;cmp r7,#1;bne close;
        ldr r0,[r4,#72];add r1,r4,#{RECORDS};movw r2,#{RECORD_COUNT*RECORD_SIZE};{call('pre_write')}mov r7,r0;
        cmp r7,#1;bne close;
        ldr r0,[r4,#72];{c('r1',SEGMENTS)}add r1,r4;movs r2,#{SEGMENT_BYTES};{call('pre_write')}mov r7,r0;
        cmp r7,#1;bne close;
        ldr r0,[r4,#72];add r1,r4,#{capture.TASK};movs r2,#{capture.STATE_BYTES-capture.TASK};{call('pre_write')}mov r7,r0;
    close:mov r0,r4;{call('pre_close')}cmp r0,#1;bne failed;
        cmp r7,#1;bne failed;
    advance:adds r5,#1;cmp r5,#2;blo next_file;
        ldr r0,[r4,#24];cmp r0,#0;bne saved_jpg;movs r0,#10;b saved_status;
    saved_jpg:movs r0,#3;
    saved_status:
        str r0,[r4,#36];b unlock;
    failed:mov r0,r4;{call('pre_close')}movs r0,#8;str r0,[r4,#36];
    unlock:movs r0,#0;dmb ish;str r0,[r4,#60];str r0,[r4,#16];b done;
    log_locked:clrex;movs r0,#0;str r0,[r4,#16];b done;
    locked:clrex;
    done:movs r0,#0;pop {{r4-r8,pc}};'''
    codes.update(capture.codes(f))
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset+STATE_BYTES, default=0, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack('<3I', *identifiers))
    extension['codes']['bitrate_open'] = extension['codes']['bitrate_open'].replace('PRE_IDS;', c('r1', ids))
    for name, source in extension['codes'].items():
        raw = assemble(source, f[name], SIZES[name])
        if name == 'pre_keys':
            tail = f[name] + len(raw)
            raw += jump(tail, 0x101E339C, length=8 if tail % 4 == 0 else 10)
            if len(raw) > SIZES[name]:
                raise ValueError('Physical key observer exceeds its slot')
        plan.put(f[name], raw)
    plan.features['jpg_capture_probe'] = dict(
        version=9, page_index=PAGE_INDEX, default_enabled=False,
        page_offset=extension['page_offset'], state_offset=extension['state_offset'],
        allocation_size=extension['allocation_size'], state_bytes=STATE_BYTES,
        functions={k: hex(f[k] | 1) for k in SIZES}, controls=list(identifiers),
        max_frame_bytes=MAX_FRAME, buffer_allocator='0x10211F4C(size, pool=0)',
        buffer_lifetime='One allocation per boot, pinned until power off; rearm reuses it',
        save_hooks=['0x1009F9B8', '0x1009FC28'], input_observer='0x100636F0',
        physical_key_observer='0x101E3392', physical_sw0='0xC1F87A74',
        physical_key_masks=dict(half='0x80000000', full='0x40000000'),
        stock_save_and_pool_release_preserved=True, capture_file_io=False,
        files=['WL-PRE.JPG', 'WL-PRE.DBG'], record_count=RECORD_COUNT,
        segment_records=6, segment_fields=['kind', 'address', 'logical_length', 'padding'],
        padding_source=hex(FILLER), max_padding_bytes=MAX_PADDING,
        half_press_capture=True, autonomous_capture_limit_per_arm=1,
        requires_existing_silent_photography=True, setting_writes=False,
        shutter_mode_switching=False, continuous_precapture=False,
        native_capture_request='0x1020A630, 20-byte request, word +0x10 = 1',
        native_worker=dict(task_offset=capture.TASK, config_offset=capture.CONFIG,
                           request_offset=capture.REQUEST, phase_offset=capture.PHASE,
                           ready_offset=capture.READY, ticket_offset=capture.TICKET,
                           result_offset=capture.RESULT, core_field=1, priority=32, stack_bytes=16384,
                           lifetime='Pinned until shutdown, one create attempt per boot'),
        camera_verified=False,
        limitations=['One half-press capture request per arm, only with native silent photography already on.',
                     'Export uses a sidecar JPG, not a new Nikon DCF/replay record.',
                     'Padding above 512 bytes, invalid lengths and non-JPG objects are refused.',
                     'Native callback 0x46 is not claimed as proof of all pool returns.',
                     'No continuous pre-capture, save suppression or electronic/mechanical switching.'])
