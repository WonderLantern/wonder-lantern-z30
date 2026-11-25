# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
"""Independent border prototype using free native rectangle records.

The stock 42-record producer runs first. Never replace an occupied record or
change its bank, enable property, AF selection, or grid preference. Native
repacking removes our additions without modifying the original backing bank.
"""
from ...assembly import constant as c, invoke as n
from .frame import projection_instructions

SIZES = dict(aspect_border_project=768, aspect_border_rect=256,
             aspect_border_apply=1024, aspect_border_pack=128,
             aspect_border_original=64, aspect_border_publish=512,
             aspect_border_monitor=96, aspect_border_monitor_original=64)

DIAGNOSTIC_NAMES = ('monitor', 'publish', 'inactive', 'grid_updater', 'display_busy',
                    'photo_not_ready', 'rectangle_channel_disabled', 'snapshot_not_ready',
                    'projection_rejected', 'unchanged_request', 'request_attempted',
                    'rectangle_producer', 'apply', 'apply_rejected',
                    'free_records_insufficient', 'apply_succeeded')
DIAGNOSTIC_MAGIC = 0x31444257  # WBD1, appended to event 5's existing payload.


def rectangles(bounds, viewport):
    """Eight-pixel yellow strips INSIDE visible, true photo edges only."""
    x0, y0, x1, y1 = bounds
    x0, y0, x1, y1 = (x0+1)&~1, (y0+1)&~1, x1&~1, y1&~1
    l, t, r, b = viewport
    result = []
    candidates = [(x0, max(y0, t), x0+8, min(y1, b)),
                  (x1-8, max(y0, t), x1, min(y1, b)),
                  (max(x0, l), y0, min(x1, r), y0+8),
                  (max(x0, l), y1-8, min(x1, r), y1)]
    for a, e, z, h in candidates:
        a, e, z, h = (a+1)&~1, (e+1)&~1, z&~1, h&~1
        if l <= a < z <= r and t <= e < h <= b and z-a >= 8 and h-e >= 8:
            result.append((a, e, z, h))
    return result


def source_codes(f, geometry_table, cache_offset, *, diagnostic=False, lease=False):
    call = lambda key: n(f[key])
    code = {}
    code['aspect_border_project'] = f'''push {{r4-r11,lr}};sub sp,#132;
        mov r4,r0;mov r5,r1;mov r6,r2;mov r7,r3;
        {projection_instructions()}
        axes:ldr r0,[sp,#16];ldr r1,[sp,#20];ldr r2,[sp,#24];ldr r3,[sp,#28];
        stm r4,{{r0-r3}};movs r0,#1;b done;
        bad:movs r0,#0;done:add sp,#132;pop {{r4-r11,pc}};'''
    # Native converter: direct RGB, x/y/end x/end y, independent even strokes,
    # enable word. Use separate strokes for the long sides and short caps.
    # v21 (8,8) joined the sides on camera but produced protruding end caps.
    # v22 long-side=6 split again on camera. Keep the v23 trial settings while
    # soft-mask research proceeds; its cap protrusions remain unresolved.
    code['aspect_border_rect'] = f'''push {{r4-r6,lr}};mov r4,r0;ldm r1,{{r0-r3}};
        adds r0,#1;bic r0,r0,#1;adds r1,#1;bic r1,r1,#1;
        bic r2,r2,#1;bic r3,r3,#1;subs r5,r2,r0;cmp r5,#8;blt bad;
        subs r5,r3,r1;cmp r5,#8;blt bad;
        movs r5,#0;str r5,[r4];movw r6,#65535;str r6,[r4,#4];strh r5,[r4,#8];
        strh r0,[r4,#10];strh r1,[r4,#12];strh r2,[r4,#14];strh r3,[r4,#16];
        subs r5,r2,r0;subs r6,r3,r1;cmp r5,r6;bhi horizontal_stroke;
        movs r5,#8;movs r6,#2;b strokes;
        horizontal_stroke:movs r5,#2;movs r6,#8;
        strokes:strh r5,[r4,#18];strh r6,[r4,#20];movs r5,#0;strh r5,[r4,#22];
        movs r0,#1;str r0,[r4,#24];pop {{r4-r6,pc}};
        bad:movs r0,#0;pop {{r4-r6,pc}};'''
    code['aspect_border_apply'] = f'''push {{r4-r11,lr}};subw sp,sp,#0x504;mov r4,r0;
        {call('aspect_get')}cmp r0,#0;beq bad;ldr r5,[r0];movs r1,#0;str r1,[r0,#12];
        subs r5,#1;cmp r5,#5;bhi bad;adds r5,#1;
        movw r0,#0x501;{n(0x10465984)}cmp r0,#2;bne bad;
        {n(0x1044fcf6)}cmp r0,#0;bne bad;
        movs r0,#0x7e;{n(0x1044dd08)}cmp r0,#0;bne bad;
        {n(0x104be4a0)}cmp r0,#1;bne bad;
        mov r0,sp;{n(0x104b9348)}ldr r0,[sp];tst r0,#1;beq bad;
        addw r0,sp,#0x468;add r1,sp,#8;
        {c('r2',geometry_table)}add r2,r2,r5,lsl #4;mov r3,r5;
        {call('aspect_border_project')}cmp r0,#0;beq bad;
        addw r9,sp,#0x468;ldm r9,{{r0-r3}};
        subs r2,r2,r0;cmp r2,#16;blt bad;subs r3,r3,r1;cmp r3,#16;blt bad;
        adds r6,r4,#4;movs r7,#42;movs r8,#0;addw r10,sp,#0x478;
        scan:ldr r0,[r6,#24];cmp r0,#0;bne next;
        str r6,[r10,r8,lsl #2];adds r8,#1;cmp r8,#4;beq ready;
        next:adds r6,#28;subs r7,#1;bne scan;b bad;
        ready:movs r6,#0;movs r8,#0;
        edge:ldm r9,{{r0-r3}};adds r0,#1;bic r0,r0,#1;adds r1,#1;bic r1,r1,#1;
        bic r2,r2,#1;bic r3,r3,#1;
        ldrsh.w r4,[sp,#8];ldrsh.w r5,[sp,#10];ldrsh.w r7,[sp,#12];ldrsh.w r11,[sp,#14];
        cmp r6,#2;bhs horizontal;
        cmp r1,r5;it lt;movlt r1,r5;cmp r3,r11;it gt;movgt r3,r11;
        cmp r6,#0;bne right;add r2,r0,#8;b check;
        right:sub r0,r2,#8;b check;
        horizontal:cmp r0,r4;it lt;movlt r0,r4;cmp r2,r7;it gt;movgt r2,r7;
        cmp r6,#2;bne bottom;add r3,r1,#8;b check;
        bottom:sub r1,r3,#8;
        check:cmp r0,r4;blt skip;cmp r1,r5;blt skip;cmp r2,r7;bgt skip;cmp r3,r11;bgt skip;
        addw r4,sp,#0x488;stm r4,{{r0-r3}};mov r1,r4;ldr r0,[r10,r6,lsl #2];
        {call('aspect_border_rect')}add r8,r0;
        skip:adds r6,#1;cmp r6,#4;blo edge;cmp r8,#0;beq bad;
        {call('aspect_get')}cmp r0,#0;beq result;movs r1,#1;str r1,[r0,#12];
        result:mov r0,r8;b done;
        bad:movs r0,#0;done:addw sp,sp,#0x504;pop {{r4-r11,pc}};'''
    code['aspect_border_original'] = f'''push.w {{r4-r8,lr}};mov r6,r2;mov r8,r0;
        {n(0x104b81be)}{c('r12',0x103c0575)}bx r12;'''
    code['aspect_border_pack'] = f'''push {{r4-r6,lr}};mov r4,r0;mov r5,r2;
        {call('aspect_border_original')}mov r6,r0;cmp r4,#1;bne done;
        mov r0,r5;{call('aspect_border_apply')}
        done:mov r0,r6;pop {{r4-r6,pc}};'''
    # Request the original rectangle producer, including OFF cleanup. Do not
    # enable a disabled component or call any display device function directly.
    code['aspect_border_publish'] = f'''push {{r4-r7,lr}};subw sp,sp,#0x47c;str r0,[sp,#0x478];
        {call('aspect_get')}cmp r0,#0;beq done;mov r4,r0;ldr r7,[r0];subs r1,r7,#1;cmp r1,#5;bhi cleanup;
        movw r0,#0x501;{n(0x10465984)}cmp r0,#2;beq active;
        cleanup:ldr r0,[r4,#12];cmp r0,#1;bne done;movs r7,#0;
        active:{n(0x1044fcf6)}cmp r0,#0;bne done;{n(0x104be4a0)}cmp r0,#1;bne done;
        movs r0,#1;movs r1,#3;{n(0x10454b60)}cmp r0,#1;bne done;
        mov r0,sp;{n(0x104b9348)}ldr r0,[sp];tst r0,#1;beq done;
        cmp r7,#0;beq signature;cmp r7,#6;bhi done;
        addw r0,sp,#0x468;add r1,sp,#8;{c('r2',geometry_table)}add r2,r2,r7,lsl #4;mov r3,r7;
        {call('aspect_border_project')}cmp r0,#0;beq done;
        signature:{c('r6',cache_offset)}add r6,r4;adds r5,r7,#1;
        ldr r0,[sp,#0x478];cmp r0,#1;beq changed;
        ldr r0,[r6];cmp r0,r5;bne changed;
        ldr r0,[sp,#8];ldr r1,[r6,#4];cmp r0,r1;bne changed;
        ldr r0,[sp,#12];ldr r1,[r6,#8];cmp r0,r1;beq done;
        changed:str r5,[r6];ldr r0,[sp,#8];str r0,[r6,#4];ldr r0,[sp,#12];str r0,[r6,#8];dmb ish;
        {c('r0',0x10453ac3)}movs r1,#8;movs r2,#1;{n(0x10453fac)}
        done:addw sp,sp,#0x47c;pop {{r4-r7,pc}};'''
    code['aspect_border_monitor_original'] = f'''push {{r4-r6,lr}};movw r0,#0x1a07;
        {n(0x10465984)}{c('r12',0x104be745)}bx r12;'''
    code['aspect_border_monitor'] = f'''push {{r4,lr}};
        {call('aspect_border_monitor_original')}mov r4,r0;
        {call('aspect_border_publish')}mov r0,r4;pop {{r4,pc}};'''
    if lease:
        from .border_lease import source_codes as lease_codes
        code.update(lease_codes(f,cache_offset,cache_offset+12+(64 if diagnostic else 0),geometry_table))
    if diagnostic:
        code.update(diagnostic_codes(f, cache_offset))
        # Preserve arguments, return values and volatile registers. These
        # insertions are at complete statements, never between CMP and branch.
        def note(index):
            return (f'push {{r0-r3,r12,lr}};movs r0,#{index};'+
                    call('aspect_border_note')+'pop {r0-r3,r12,lr};')
        code['aspect_border_monitor'] = code['aspect_border_monitor'].replace(
            call('aspect_border_monitor_original'),call('aspect_border_monitor_original')+note(0),1)
        code['aspect_border_pack'] = code['aspect_border_pack'].replace(
            "mov r0,r5;",note(11)+"mov r0,r5;",1)
        body=code['aspect_border_apply']
        body=body.replace('mov r4,r0;', 'mov r4,r0;'+note(12),1)
        body=body.replace('subs r7,#1;bne scan;b bad;', 'subs r7,#1;bne scan;'+note(14)+'b bad;',1)
        body=body.replace('result:mov r0,r8;', 'result:'+note(15)+'mov r0,r8;',1)
        body=body.replace('bad:movs r0,#0;', 'bad:'+note(13)+'movs r0,#0;',1)
        code['aspect_border_apply']=body
        if lease:
            body=code['aspect_border_lease_take']
            body=body.replace(call('aspect_border_lease_empty')+'cmp r0,#1;bne bad;',
                call('aspect_border_lease_empty')+'cmp r0,#1;beq empty;'+note(14)+'b bad;empty:',1)
            code['aspect_border_lease_take']=body
        body=code['aspect_border_publish']
        body=body.replace('str r0,[sp,#0x478];','str r0,[sp,#0x478];'+note(1),1)
        body=body.replace('cmp r0,#1;bne done;movs r7,#0;', 'cmp r0,#1;bne inactive;movs r7,#0;',1)
        body=body.replace(f"{n(0x1044fcf6)}cmp r0,#0;bne done;",f"{n(0x1044fcf6)}cmp r0,#0;bne display_busy;",1)
        body=body.replace(f"{n(0x104be4a0)}cmp r0,#1;bne done;",f"{n(0x104be4a0)}cmp r0,#1;bne photo_not_ready;",1)
        body=body.replace(f"{n(0x10454b60)}cmp r0,#1;bne done;",f"{n(0x10454b60)}cmp r0,#1;bne rectangle_channel_disabled;",1)
        body=body.replace('tst r0,#1;beq done;', 'tst r0,#1;beq snapshot_not_ready;',1)
        body=body.replace(call('aspect_border_project')+'cmp r0,#0;beq done;',
                          call('aspect_border_project')+'cmp r0,#0;beq projection_rejected;',1)
        body=body.replace('cmp r0,r1;beq done;', 'cmp r0,r1;beq unchanged_request;',1)
        if lease:
            body=body.replace(f"{n(0x1044fcf6)}cmp r0,#0;bne not_visible;",f"{n(0x1044fcf6)}cmp r0,#0;bne display_busy;",1)
            body=body.replace(f"{n(0x104be4a0)}cmp r0,#1;bne not_visible;",f"{n(0x104be4a0)}cmp r0,#1;bne photo_not_ready;",1)
            body=body.replace('tst r0,#1;beq not_visible;', 'tst r0,#1;beq snapshot_not_ready;',1)
            body=body.replace(call('aspect_border_project')+'cmp r0,#0;beq not_visible;',
                              call('aspect_border_project')+'cmp r0,#0;beq projection_rejected;',1)
            body=body.replace('need_channel:', 'need_channel:'+note(6),1)
        body=body.replace(c('r0',0x10453ac3),note(10)+c('r0',0x10453ac3),1)
        labels='b done;'+''.join(name+':'+note(index)+
            (call('aspect_border_lease_release') if lease and index in (4,5,6,7,8) else '')+'b done;' for name,index in (
            ('inactive',2),('display_busy',4),('photo_not_ready',5),('rectangle_channel_disabled',6),
            ('snapshot_not_ready',7),('projection_rejected',8),('unchanged_request',9)))
        body=body.replace('done:addw sp,sp,#0x47c;',labels+'done:addw sp,sp,#0x47c;',1)
        code['aspect_border_publish']=body
    return code


def diagnostic_codes(f, cache_offset):
    call=lambda key:n(f[key])
    code = {
        'aspect_border_note':f'''push {{r4,lr}};mov r4,r0;cmp r4,#15;bhi done;
            {call('aspect_get')}cmp r0,#0;beq done;{c('r1',cache_offset+12)}add r0,r1;
            ldr r1,[r0,r4,lsl #2];adds r1,#1;str r1,[r0,r4,lsl #2];
            done:pop {{r4,pc}};''',
        'aspect_border_snapshot':f'''push {{r4-r6,lr}};mov r4,r0;
            {c('r0',DIAGNOSTIC_MAGIC)}str r0,[r4,#64];movs r0,#1;str r0,[r4,#68];
            {call('aspect_get')}cmp r0,#0;beq native;
            {c('r1',cache_offset+12)}add r1,r0;add r0,r4,#72;movs r2,#16;
            copy:ldr r3,[r1],#4;str r3,[r0],#4;subs r2,#1;bne copy;
            native:movs r0,#1;movs r1,#3;{n(0x10454b60)}str r0,[r4,#136];
            movw r0,#0x1129;{n(0x10465984)}str r0,[r4,#140];pop {{r4-r6,pc}};'''
    }
    if 'aspect_border_lease_get' in f:
        code['aspect_border_snapshot']=code['aspect_border_snapshot'].replace('movs r0,#1;str r0,[r4,#68];','movs r0,#2;str r0,[r4,#68];',1)
        code['aspect_border_snapshot']=code['aspect_border_snapshot'].replace('str r0,[r4,#136];',
            'mov r5,r0;'+call('aspect_border_lease_get')+'cmp r0,#0;beq channel;ldr r0,[r0];cmp r0,#1;it eq;orreq r5,r5,#256;channel:str r5,[r4,#136];',1)
    return code
