# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Opt-in capture-contract trace: bounded RAM records, deferred menu SD dump.

No file service is called from geometry/capture callbacks. Records contain
parameters and addresses, never image data or arbitrary memory contents.
"""
from ...assembly import constant as c, invoke as n, jump
from ..menu.layout import SUCCESS

MAGIC = 0x31444157  # WAD1
CAPACITY, STRIDE, HEADER = 96, 160, 24
PINNED = 77  # Seven profiles for events 2..12; remaining slots are event 1.
TRACE_BYTES = HEADER + CAPACITY * STRIDE


def install(plan, f, codes, sizes):
    for name, size in (('aspect_trace_event', 768), ('aspect_trace_config', 384),
                       ('aspect_trace_save', 768), ('aspect_trace_request', 256),
                       ('aspect_trace_parameters', 256), ('aspect_trace_parameters_original', 64),
                       ('aspect_trace_view_cache', 192), ('aspect_trace_view_menu', 256)):
        if name == 'aspect_trace_view_menu' and 'aspect_border_snapshot' in f:
            size = 512
        sizes[name] = size
        f[name] = plan.alloc(bytes(size))
    call = lambda name: n(f[name])
    path = plan.alloc(b'A:\\WL-ASPECT.DBG\0')
    codes['aspect_trace_event'] = f'''push {{r4-r11,lr}};sub sp,#4;
        mov r4,r0;mov r5,r1;mov r6,r2;mov r7,r3;
        cmp r6,#144;bhi done;{call('aspect_get')}cmp r0,#0;beq done;mov r8,r0;
        ldr r0,[r8,#36];cmp r0,#0;bne done;
        ldr r0,[r8];cmp r0,#6;bhi done;
        cmp r4,#1;bne validate;
        cmp r0,#2;bhi done;
        {c('r0',0x10132247)}cmp r7,r0;beq done;
        {c('r0',0x100aea9b)}cmp r7,r0;beq done;
        validate:cmp r4,#1;blo done;cmp r4,#12;bhi done;
        cmp r4,#6;blo record;cmp r6,#64;bne done;
        ldr r0,[r8];cmp r0,#2;bhi done;
        subs r0,r4,#2;movs r1,#7;mul r0,r0,r1;ldr r1,[r8];add r0,r1;
        movs r1,#{STRIDE};mul r0,r0,r1;add r1,r8,#40;add r1,r0;
        ldr r0,[r1];cmp r0,#0;beq record;
        cmp r4,#11;bhs done;
        add r3,r1,#16;mov r0,r5;movs r2,#4;
        compare_head:ldr r9,[r0],#4;ldr r10,[r3],#4;cmp r9,r10;bne record;
        subs r2,#1;bne compare_head;
        add r0,r5,#32;add r3,r1,#48;movs r2,#8;
        compare_tail:ldr r9,[r0],#4;ldr r10,[r3],#4;cmp r9,r10;bne record;
        subs r2,#1;bne compare_tail;b done;
        record:{c('r0',MAGIC)}str r0,[r8,#16];movs r0,#4;str r0,[r8,#20];
        movs r0,#{CAPACITY};str r0,[r8,#28];movs r0,#{STRIDE};str r0,[r8,#32];
        add r9,r8,#24;movs r2,#4;
        claim:ldrex r10,[r9];adds r1,r10,#1;strex r0,r1,[r9];cmp r0,#0;beq claimed;
        subs r2,#1;bne claim;b done;
        claimed:cmp r4,#2;blo ring_slot;
        subs r0,r4,#2;movs r1,#7;mul r0,r0,r1;ldr r1,[r8];add r0,r1;b slot;
        ring_slot:movs r1,#{CAPACITY-PINNED};udiv r0,r10,r1;mls r0,r0,r1,r10;adds r0,#{PINNED};
        slot:movs r1,#{STRIDE};mul r11,r0,r1;
        add r11,r8;adds r11,#40;mov r0,r11;movs r1,#40;movs r2,#0;
        clear:str r2,[r0],#4;subs r1,#1;bne clear;
        str r4,[r11,#4];ldr r0,[r8];str r0,[r11,#8];str r7,[r11,#12];
        add r0,r11,#16;mov r1,r5;mov r2,r6;cmp r2,#0;beq commit;
        copy:ldrb r3,[r1],#1;strb r3,[r0],#1;subs r2,#1;bne copy;
        commit:dmb ish;adds r10,#1;str r10,[r11];
        done:add sp,#4;pop {{r4-r11,pc}};'''
    codes['aspect_trace_config'] = f'''push {{r4-r8,lr}};sub sp,#144;
        mov r4,r0;mov r5,r1;mov r8,lr;mov r0,sp;movs r1,#144;{n(0x10199c40)}
        ldr r0,[r4];str r0,[sp];ldr r0,[r4,#0x98];str r0,[sp,#4];
        ldr.w r0,[r4,#0x104];str r0,[sp,#8];
        ldr.w r0,[r4,#0x208];str r0,[sp,#12];
        {c('r1',0x2658)}ldrb r0,[r4,r1];str r0,[sp,#16];
        ldr.w r0,[r4,#0x228];str r0,[sp,#20];
        ldr.w r0,[r4,#0x70];str r0,[sp,#24];
        {c('r1',0x2941)}ldrb r0,[r4,r1];str r0,[sp,#28];
        add r0,sp,#32;mov r1,r5;movs r2,#28;
        copy:ldr r3,[r1],#4;str r3,[r0],#4;subs r2,#1;bne copy;
        movs r0,#2;mov r1,sp;movs r2,#144;{c('r3',0x10142a22)}{call('aspect_trace_event')}
        add sp,#144;pop {{r4-r8,pc}};'''
    # Boundary 1: actual processing request. Only keep image-sized outputs;
    # smaller preview requests must not overwrite the last main-image record.
    codes['aspect_trace_request'] = f'''push {{r0-r3,r12,lr}};
        ldrh.w r1,[r0,#0x24];{c('r2',2000)}cmp r1,r2;blo resume;
        mov r1,r0;movs r0,#3;movs r2,#72;ldr r3,[sp,#20];{call('aspect_trace_event')}
        resume:pop {{r0-r3,r12,lr}};
        push {{r4-r11,lr}};mov r5,r0;movw r0,#0x4758;
        push {{r0,r1}};{c('r0',0x100ef7c3)}str r0,[sp,#4];pop {{r0,pc}};'''
    plan.edit(0x100ef7b8, jump(0x100ef7b8, f['aspect_trace_request'], 10),
              'Aspect diagnostic processing request boundary', bytes.fromhex('2de9f04f054644f25870'))
    # Boundary 2: run the complete original translator, then snapshot its
    # defined scalar parameter fields. This does not change the request.
    codes['aspect_trace_parameters_original'] = f'''push {{r4,r5,lr}};
        ldrb r3,[r0,#12];add.w r2,r0,#12;strb.w r3,[r1,#0x384];
        push {{r0,r1}};{c('r0',0x102507d7)}str r0,[sp,#4];pop {{r0,pc}};'''
    codes['aspect_trace_parameters'] = f'''push {{r4-r6,lr}};mov r4,r1;mov r6,lr;
        {call('aspect_trace_parameters_original')}mov r5,r0;
        ldr.w r0,[r4,#0x3a0];{c('r1',2000)}cmp r0,r1;blo done;
        movs r0,#4;add.w r1,r4,#0x384;movs r2,#144;mov r3,r6;{call('aspect_trace_event')}
        done:mov r0,r5;pop {{r4-r6,pc}};'''
    plan.edit(0x102507ca, jump(0x102507ca, f['aspect_trace_parameters'], 12),
              'Aspect diagnostic translated parameters boundary', bytes.fromhex('30b5037b00f10c0281f88433'))
    # Read only defined geometry fields. These are cached inputs, not proof
    # that the ISP/LCD applied them. No object-pointer range guessing is used.
    codes['aspect_trace_view_cache'] = f'''{c('r1',0xc237c380)}
        ldrh r2,[r1,#0x4e];strh r2,[r0,#36];ldrh r2,[r1,#0x50];strh r2,[r0,#38];
        ldrh r2,[r1,#0x52];strh r2,[r0,#40];ldrh r2,[r1,#0x54];strh r2,[r0,#42];
        ldrh r2,[r1,#0x56];strh r2,[r0,#44];ldrh r2,[r1,#0x58];strh r2,[r0,#46];
        ldr r2,[r1,#0x60];str r2,[r0,#48];ldr r2,[r1,#0x64];str r2,[r0,#52];
        ldr r2,[r1,#0x68];str r2,[r0,#56];
        ldrb r2,[r1,#0x5b];strb r2,[r0,#60];ldrb r2,[r1,#0x5c];strb r2,[r0,#61];
        movs r2,#0;strh r2,[r0,#62];bx lr;'''
    codes['aspect_trace_view_menu'] = f'''push {{r4,lr}};sub sp,#64;
        mov r0,sp;movs r1,#64;{n(0x10199c40)}
        {c('r0',0x501)}{n(0x10465984)}str r0,[sp];
        {c('r0',0x189e)}{n(0x10465984)}str r0,[sp,#4];
        mov r0,sp;{call('aspect_trace_view_cache')}
        movs r0,#5;mov r1,sp;movs r2,#64;movs r3,#0;{call('aspect_trace_event')}
        add sp,#64;pop {{r4,pc}};'''
    if 'aspect_border_snapshot' in f:
        codes['aspect_trace_view_menu'] = codes['aspect_trace_view_menu'].replace('#64', '#144')
        codes['aspect_trace_view_menu'] = codes['aspect_trace_view_menu'].replace(
            'movs r0,#5;', 'mov r0,sp;'+call('aspect_border_snapshot')+'movs r0,#5;',1)
    codes['aspect_output'] = f'''push {{r4-r8,lr}};sub sp,#64;
        mov r4,r0;mov r5,r1;mov r6,lr;{c('r2',0xc18c7dfc)}
        ldr r0,[r2,r4,lsl #2];add r0,r0,r5,lsl #2;mov r1,r6;{call('aspect_output_data')}mov r7,r0;
        str r4,[sp];str r5,[sp,#4];str r7,[sp,#8];ldr r0,[r7];str r0,[sp,#12];
        ldr r0,[sp,#64];str r0,[sp,#16];ldr r0,[sp,#68];str r0,[sp,#20];
        ldr r0,[sp,#72];str r0,[sp,#24];ldr r0,[sp,#76];str r0,[sp,#28];
        movs r8,#1;
        {c('r0',0x10197d9d)}cmp r6,r0;it eq;moveq r8,#6;
        {c('r0',0x1018ab41)}cmp r6,r0;it eq;moveq r8,#7;
        {c('r0',0x101985e5)}cmp r6,r0;it eq;moveq r8,#8;
        {c('r0',0x10132247)}cmp r6,r0;it eq;moveq r8,#9;
        {c('r0',0x100aea9b)}cmp r6,r0;it eq;moveq r8,#10;
        cmp r8,#1;beq generic;
        movs r0,#0;str r0,[sp,#32];
        cmp r8,#9;bne hot_other;
        ldr r1,[sp,#76];ldrh r0,[r1];strh r0,[sp,#32];ldrh r0,[r1,#2];strh r0,[sp,#34];b cache;
        hot_other:cmp r8,#10;bne cache;
        ldr r1,[sp,#64];ldrh r0,[r1,#0x52];strh r0,[sp,#32];ldrh r0,[r1,#0x54];strh r0,[sp,#34];
        cache:mov r0,sp;{call('aspect_trace_view_cache')}
        cmp r8,#9;blo latest;
        add r0,r8,#2;mov r1,sp;movs r2,#64;mov r3,r6;{call('aspect_trace_event')}
        latest:mov r0,r8;mov r1,sp;movs r2,#64;b emit;
        generic:movs r0,#1;mov r1,sp;movs r2,#32;
        emit:mov r3,r6;{call('aspect_trace_event')}
        mov r0,r7;add sp,#64;pop {{r4-r8,pc}};'''
    needle='ldrb r0,[r5];\n        push {r0,r1};'
    assert codes['aspect_narrow_source'].count(needle) == 1
    codes['aspect_narrow_source'] = codes['aspect_narrow_source'].replace(needle,
        "ldrb r0,[r5];push {r0-r3,r12,lr};mov r0,r5;mov r1,r4;"+
        call('aspect_trace_config')+"pop {r0-r3,r12,lr};push {r0,r1};")
    # Menu task only. The freeze flag drops trace events while the synchronous
    # file writer reads the ring. It never blocks capture or changes geometry.
    codes['aspect_trace_save'] = f'''push {{r4-r8,lr}};sub sp,#64;
        {call('aspect_get')}mov r4,r0;cmp r4,#0;beq done;
        {c('r0',0x188c)}{n(0x101993de)}cmp r0,#0;bne done;
        ldr r0,[r4,#16];{c('r1',MAGIC)}cmp r0,r1;bne done;
        movs r0,#1;str r0,[r4,#36];dmb ish;
        mov r0,sp;movs r1,#48;{n(0x10199c40)}
        mvn r0,#0;str r0,[sp,#8];mov r0,sp;{c('r1',path)}{n(0x1033829e)}
        mov r0,sp;{c('r1',0x301)}movs r2,#0;{n(0x103382a2)}
        {c('r1',SUCCESS)}cmp r0,r1;bne unfreeze;
        movs r0,#0;str r0,[sp,#48];ldr r0,[sp,#8];
        add r1,r4,#16;{c('r2',TRACE_BYTES)}add r3,sp,#48;{n(0x102c8a6a)}mov r5,r0;
        mov r0,sp;{n(0x1033832e)}
        {c('r1',SUCCESS)}cmp r0,r1;bne unfreeze;
        cmp r5,#0;bne unfreeze;ldr r0,[sp,#48];{c('r1',TRACE_BYTES)}cmp r0,r1;
        unfreeze:movs r0,#0;dmb ish;str r0,[r4,#36];
        done:movs r0,#0;add sp,#64;pop {{r4-r8,pc}};'''
    needle="cmp r0,#0;beq done;"
    assert needle in codes['aspect_open']
    codes['aspect_open'] = codes['aspect_open'].replace(needle,
        needle+call('aspect_trace_view_menu')+call('aspect_trace_save'),1)
    return {'version': 4, 'file': 'WL-ASPECT.DBG', 'bytes': TRACE_BYTES,
            'magic': hex(MAGIC), 'capacity': CAPACITY, 'stride': STRIDE,
            'dump': 'Open photo-aspect submenu after shooting; busy state skips dumping',
            'capture_file_io': False,
            'pinned_config_slots':7, 'pinned_request_slots':7, 'pinned_parameter_slots':7,
            'output_ring_capacity':CAPACITY-PINNED, 'minimum_processing_output_width':2000,
            'view_slots_per_event':7, 'first_hot_events':[11,12],
            'change_only_events':[6,7,8,9,10], 'view_cache_address':'0xc237c380',
            'processing_request_hook':'0x100ef7b8','processing_parameters_hook':'0x102507ca',
            'output_profiles':[0,1,2],
            'excluded_output_callers':['0x10132247','0x100aea9b'],
            'events': {'1': 'Output selector caller, area, size, pointer, WH and saved registers',
                       '2': 'Normal configuration, branch selectors and 112-byte geometry',
                       '3': '72-byte processing request at actual entry, width >= 2000',
                       '4': '144-byte translated parameter window at +0x384, output width >= 2000',
                       '5': 'Idle menu sample: user/derived area plus defined view-cache fields',
                       '6': 'Cached view query: output geometry and prior cache',
                       '7': 'Local view query: output geometry and current cache',
                       '8': 'Submission view query: output geometry and current cache',
                       '9': 'Latest 10132247 query plus caller input WH and view cache',
                       '10': 'Latest 100aea9b query plus caller parent WH and view cache',
                       '11': 'First 10132247 query for this profile in this RAM session',
                       '12': 'First 100aea9b query for this profile in this RAM session'}}
