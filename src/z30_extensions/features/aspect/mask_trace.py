# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
"""Opt-in mask-context trace; no file I/O from the graphics callback.

This records defined scalar fields used by native getters, not arbitrary RAM
or image contents. Field names describe reads, not an inferred app identity.
The existing mask try-lock serializes writers. Menu export freezes recording
and skips export if a writer still owns that lock; it never waits for the GPU.
"""
import struct

from ...assembly import constant as c, invoke as n
from .mask import HISTORY_OFFSET, STATE_SIZE, STATE_LOCK, VIEW_KIND, PHOTO_BACKEND, SNAPSHOT, SOURCE_WINDOW, IMAGE_SLOT, OVERLAY_BUSY, UI_SERVICE_PHASE
from ..menu.layout import SUCCESS

MAGIC, VERSION = 0x31444D57, 1  # WMD1
CAPACITY, STRIDE, HEADER = 64, 128, 24
TRACE_BYTES = HEADER + CAPACITY * STRIDE
TRACE_OFFSET = HISTORY_OFFSET + STATE_SIZE

# Sources can be traced to the stated original getters. Unknown semantics are
# deliberately left as raw UI property names instead of "playback active".
FIELDS = (
    ('view_kind', VIEW_KIND, 1),
    ('photo_backend', PHOTO_BACKEND, 4),
    ('viewport_ready', SNAPSHOT, 4),
    ('ui_property_2d', OVERLAY_BUSY[0], 4),
    ('ui_property_2a', OVERLAY_BUSY[1], 4),
    ('native_display_busy', 0xC1821E6C, 4),  # 1044fcf6
    ('ui_property_7e', 0xC2016A3C, 4),       # 1044dd08(7e)
    ('ui_service_phase', UI_SERVICE_PHASE, 1),  # 10457db2
    ('capture_service_phase', 0xC23A5165, 1),  # 102224f6
    ('display_sources', 0xC18200DC, 4),
    ('ui_property_0', 0xC2016860, 1),        # 1044dd08(0)
    ('ui_property_1', 0xC201685F, 1),        # 1044dd08(1)
    ('ui_property_2', 0xC2016861, 1),        # 1044dd08(2)
    ('disp_page', 0xC2016A68 + 0x196, 1),
    ('ui_snapshot_19e', 0xC2016A68 + 0x19E, 1),
    ('image_surface', IMAGE_SLOT, 4),
    ('viewport_xy', SNAPSHOT + 8, 4),
    ('viewport_end', SNAPSHOT + 12, 4),
    ('source_xy', SOURCE_WINDOW, 4),
    ('source_end', SOURCE_WINDOW + 4, 4),
)
assert 12 + 4 * len(FIELDS) <= STRIDE - 4


def decode(data):
    if len(data) != TRACE_BYTES:
        raise ValueError('Incorrect WMD1 trace length')
    magic, version, counter, capacity, stride, freeze = struct.unpack_from('<6I', data)
    if (magic, version, capacity, stride) != (MAGIC, VERSION, CAPACITY, STRIDE):
        raise ValueError('Unknown mask trace format')
    records = []
    for slot in range(CAPACITY):
        offset = HEADER + slot * STRIDE
        seq, profile, allowed, reserved = struct.unpack_from('<4I', data, offset)
        if not seq or seq > counter or seq <= counter - CAPACITY:
            continue
        if (seq - 1) % CAPACITY != slot or profile > 6 or allowed > 1 or reserved:
            raise ValueError('Invalid mask trace record')
        values = struct.unpack_from('<' + 'I' * len(FIELDS), data, offset + 16)
        records.append(dict(sequence=seq, profile=profile, mask_predicate=allowed,
                            fields=dict(zip((v[0] for v in FIELDS), values))))
    records.sort(key=lambda row: row['sequence'])
    if len(records) != min(counter, CAPACITY):
        raise ValueError('Incomplete mask trace records')
    return dict(format='WMD1', version=version, counter=counter, capacity=capacity,
                frozen=bool(freeze), records=records,
                field_sources={name:dict(address=hex(address), width=width)
                               for name,address,width in FIELDS},
                scope='Observed native scalar reads; field combinations are not proven app identities.')


def install(plan, f, codes, sizes):
    for name,size in (('aspect_mask_trace_sample', 1024), ('aspect_mask_trace_save', 768)):
        sizes[name] = size
        f[name] = plan.alloc(bytes(size))
    call = lambda key: n(f[key])
    path = plan.alloc(b'A:\\WL-ASPECT.DBG\0')
    fields = ''.join(c('r1',address) +
                     ('ldrb r0,[r1];' if width == 1 else
                      'ldrh r0,[r1];ldrh r2,[r1,#2];orr r0,r0,r2,lsl #16;' if address % 4 else
                      'ldr r0,[r1];') + f'str r0,[sp,#{12+index*4}];'
                     for index,(_,address,width) in enumerate(FIELDS))
    # r0 is the existing predicate result. This executes only while the mask
    # ownership lock is held. Header counter is committed after the record.
    codes['aspect_mask_trace_sample'] = f'''push {{r4-r11,lr}};sub sp,#124;
        mov r6,r0;{call('aspect_get')}cmp r0,#0;beq done;mov r8,r0;
        {c('r1',TRACE_OFFSET)}add r4,r0,r1;ldr r0,[r4,#20];cmp r0,#0;bne done;
        ldr r0,[r8];cmp r0,#6;bhi done;str r0,[sp];str r6,[sp,#4];
        movs r0,#0;add r1,sp,#8;movs r2,#29;
        clear:str r0,[r1],#4;subs r2,#1;bne clear;
        {fields}
        ldr r0,[r4];{c('r1',MAGIC)}cmp r0,r1;beq header_ok;
        cmp r0,#0;bne done;str r1,[r4];movs r0,#{VERSION};str r0,[r4,#4];
        movs r0,#{CAPACITY};str r0,[r4,#12];movs r0,#{STRIDE};str r0,[r4,#16];
        header_ok:ldr r7,[r4,#8];cmp r7,#0;beq record;
        subs r0,r7,#1;and r0,r0,#{CAPACITY-1};lsls r0,r0,#7;
        add r1,r4,#{HEADER+4};add r1,r0;mov r0,sp;movs r2,#31;
        compare:ldr r9,[r0],#4;ldr r10,[r1],#4;cmp r9,r10;bne record;
        subs r2,#1;bne compare;b done;
        record:adds r6,r7,#1;beq done;and r0,r7,#{CAPACITY-1};lsls r0,r0,#7;
        add r5,r4,#{HEADER};add r5,r0;movs r0,#0;str r0,[r5];
        mov r0,sp;add r1,r5,#4;movs r2,#31;
        copy:ldr r3,[r0],#4;str r3,[r1],#4;subs r2,#1;bne copy;
        dmb ish;str r6,[r5];dmb ish;str r6,[r4,#8];
        done:add sp,#124;pop {{r4-r11,pc}};'''
    # Same already-used file service contract as the capture trace. Export
    # runs from the menu task; even busy/error paths release the freeze flag.
    codes['aspect_mask_trace_save'] = f'''push {{r4-r8,lr}};sub sp,#64;
        {call('aspect_get')}cmp r0,#0;beq done;mov r5,r0;
        {c('r1',TRACE_OFFSET)}add r4,r0,r1;
        ldr r0,[r4];{c('r1',MAGIC)}cmp r0,r1;bne done;
        movw r0,#0x188c;{n(0x101993de)}cmp r0,#0;bne done;
        movs r0,#1;str r0,[r4,#20];dmb ish;
        ldr r0,[r5,#{HISTORY_OFFSET+STATE_LOCK}];cmp r0,#0;bne unfreeze;
        mov r0,sp;movs r1,#48;{n(0x10199c40)}
        mvn r0,#0;str r0,[sp,#8];mov r0,sp;{c('r1',path)}{n(0x1033829e)}
        mov r0,sp;{c('r1',0x301)}movs r2,#0;{n(0x103382a2)}
        {c('r1',SUCCESS)}cmp r0,r1;bne unfreeze;
        movs r0,#0;str r0,[sp,#48];ldr r0,[sp,#8];mov r1,r4;
        {c('r2',TRACE_BYTES)}add r3,sp,#48;{n(0x102c8a6a)}mov r6,r0;
        mov r0,sp;{n(0x1033832e)}
        {c('r1',SUCCESS)}cmp r0,r1;bne unfreeze;
        cmp r6,#0;bne unfreeze;ldr r0,[sp,#48];{c('r1',TRACE_BYTES)}cmp r0,r1;
        unfreeze:movs r0,#0;dmb ish;str r0,[r4,#20];
        done:movs r0,#0;add sp,#64;pop {{r4-r8,pc}};'''
    needle=call('aspect_mask_photo_context')
    assert codes['aspect_mask_submit'].count(needle) == 2
    codes['aspect_mask_submit']=codes['aspect_mask_submit'].replace(needle,
        needle+'push {r0-r3,r12,lr};'+call('aspect_mask_trace_sample')+
        'pop {r0-r3,r12,lr};',1)
    # The early lifecycle path intentionally runs before descriptor filtering.
    # Keep those observations, too, under the same nonblocking writer lock.
    needle='retire_owned:dmb ish;'
    assert codes['aspect_mask_retire'].count(needle)==1
    codes['aspect_mask_retire']=codes['aspect_mask_retire'].replace(needle,
        needle+'movs r0,#0;'+call('aspect_mask_trace_sample'),1)
    needle='cmp r0,#0;beq done;'
    assert needle in codes['aspect_open']
    codes['aspect_open']=codes['aspect_open'].replace(needle,
        needle+call('aspect_mask_trace_save'),1)
    return dict(format='WMD1',version=VERSION,file='WL-ASPECT.DBG',bytes=TRACE_BYTES,
                magic=hex(MAGIC),capacity=CAPACITY,stride=STRIDE,offset=TRACE_OFFSET,
                capture_file_io=False,graphics_file_io=False,change_only=True,
                diagnostic_only=True,changes_mask_eligibility=False,
                dump='Open photo-aspect submenu after reproducing playback; busy writer skips export',
                fields=[dict(name=name,address=hex(address),width=width)
                        for name,address,width in FIELDS])
