# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
"""Own only an initially disabled, empty native rectangle channel.

Use the native visibility setter, never MMIO. Native single/batch visibility
writes relinquish ownership. Releasing never hides active native rectangles.
"""
from ...assembly import constant as c, invoke as n

SIZES=dict(aspect_border_lease_get=96,aspect_border_lease_empty=192,
           aspect_border_lease_control=96,aspect_border_lease_take=320,
           aspect_border_lease_release=256,aspect_border_visibility=256,
           aspect_border_visibility_original=64,aspect_border_visibility_batch=320,
           aspect_border_visibility_batch_original=64)


def source_codes(f,cache_offset,lease_offset,geometry_table):
    call=lambda key:n(f[key])
    code={}
    code['aspect_border_lease_get']=f'''push {{r4,lr}};{call('aspect_get')}cmp r0,#0;beq done;
        {c('r1',lease_offset)}add r0,r1;done:pop {{r4,pc}};'''
    code['aspect_border_lease_empty']=f'''push {{r4,lr}};
        movs r0,#3;movs r1,#1;movs r2,#0;{n(0x104b5e30)}adds r0,#0x30;movs r1,#42;
        scan:ldr r2,[r0];cmp r2,#0;bne busy;adds r0,#20;subs r1,#1;bne scan;
        movs r0,#1;pop {{r4,pc}};busy:movs r0,#0;pop {{r4,pc}};'''
    # Its ROM range is the sole ownership exemption in the visibility watcher.
    code['aspect_border_lease_control']=f'''push {{r4,lr}};mov r2,r0;movs r0,#1;movs r1,#3;
        {n(0x104b6b0c)}pop {{r4,pc}};'''
    code['aspect_border_lease_take']=f'''push {{r4-r6,lr}};
        {call('aspect_border_lease_get')}cmp r0,#0;beq bad;mov r4,r0;
        {call('aspect_border_lease_empty')}cmp r0,#1;bne bad;
        movs r0,#1;str r0,[r4];dmb ish;{call('aspect_border_lease_control')}
        movs r0,#1;movs r1,#3;{n(0x10454b60)}cmp r0,#1;bne take_failed;
        {call('aspect_get')}
        {c('r1',cache_offset)}movs r2,#0;str r2,[r0,r1];dmb ish;
        movs r0,#1;pop {{r4-r6,pc}};
        take_failed:movs r0,#0;str r0,[r4];
        bad:movs r0,#0;pop {{r4-r6,pc}};'''
    code['aspect_border_lease_release']=f'''push {{r4-r6,lr}};
        {call('aspect_border_lease_get')}cmp r0,#0;beq done;ldr r1,[r0];cmp r1,#1;bne done;
        movs r1,#0;str r1,[r0];dmb ish;
        {call('aspect_get')}{c('r1',cache_offset)}movs r2,#0;str r2,[r0,r1];
        {call('aspect_border_lease_empty')}cmp r0,#1;bne done;
        movs r0,#0;{call('aspect_border_lease_control')}
        {call('aspect_get')}movs r1,#0;str r1,[r0,#12];
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    code['aspect_border_visibility_original']=f'''push.w {{r4-r8,lr}};cmp r0,#3;mov r4,r2;
        {c('r12',0x104b6b15)}bx r12;'''
    # A native caller takes ownership, even if writing the same enable value.
    # This is deliberately conservative: OFF must not disable a native owner.
    code['aspect_border_visibility']=f'''push.w {{r4-r8,lr}};mov r4,r0;mov r5,r1;mov r7,lr;
        {call('aspect_border_visibility_original')}mov r8,r0;
        tst r4,#1;beq done;cmp r5,#3;bne done;
        {c('r0',f['aspect_border_lease_control'])}cmp r7,r0;blo external;
        adds r0,#96;cmp r7,r0;blo done;
        external:{call('aspect_border_lease_get')}cmp r0,#0;beq done;movs r1,#0;str r1,[r0];
        {call('aspect_get')}{c('r1',cache_offset)}movs r2,#0;str r2,[r0,r1];dmb ish;
        done:mov r0,r8;pop.w {{r4-r8,pc}};'''
    code['aspect_border_visibility_batch_original']=f'''push.w {{r4-r8,lr}};movs r6,r2;mov r5,r1;
        {c('r12',0x104b6be5)}bx r12;'''
    code['aspect_border_visibility_batch']=f'''push.w {{r4-r8,lr}};mov r4,r0;mov r5,r1;mov r6,r2;
        {call('aspect_border_visibility_batch_original')}mov r8,r0;
        tst r4,#1;beq done;cmp r6,#1;blo done;cmp r6,#64;bhi external;
        scan:ldr r0,[r5];cmp r0,#3;beq external;adds r5,#8;subs r6,#1;bne scan;b done;
        external:{call('aspect_border_lease_get')}cmp r0,#0;beq done;movs r1,#0;str r1,[r0];
        {call('aspect_get')}{c('r1',cache_offset)}movs r2,#0;str r2,[r0,r1];dmb ish;
        done:mov r0,r8;pop.w {{r4-r8,pc}};'''
    code['aspect_border_publish']=f'''push {{r4-r7,lr}};subw sp,sp,#0x47c;str r0,[sp,#0x478];
        {call('aspect_get')}cmp r0,#0;beq done;mov r4,r0;ldr r7,[r0];subs r1,r7,#1;cmp r1,#5;bhi cleanup;
        movw r0,#0x501;{n(0x10465984)}cmp r0,#2;beq active;
        cleanup:ldr r0,[r4,#12];cmp r0,#1;beq release;
        {call('aspect_border_lease_get')}cmp r0,#0;beq done;ldr r0,[r0];cmp r0,#1;bne done;
        release:{call('aspect_border_lease_release')}movs r7,#0;
        active:{n(0x1044fcf6)}cmp r0,#0;bne not_visible;
        {n(0x104be4a0)}cmp r0,#1;bne not_visible;
        mov r0,sp;{n(0x104b9348)}ldr r0,[sp];tst r0,#1;beq not_visible;
        cmp r7,#0;beq existing_channel;cmp r7,#6;bhi done;
        addw r0,sp,#0x468;add r1,sp,#8;{c('r2',geometry_table)}add r2,r2,r7,lsl #4;mov r3,r7;
        {call('aspect_border_project')}cmp r0,#0;beq not_visible;
        movs r0,#1;movs r1,#3;{n(0x10454b60)}cmp r0,#1;beq signature;
        need_channel:{call('aspect_border_lease_take')}cmp r0,#1;bne done;b signature;
        existing_channel:movs r0,#1;movs r1,#3;{n(0x10454b60)}cmp r0,#1;beq signature;
        movs r0,#0;str r0,[r4,#12];b done;
        signature:{c('r6',cache_offset)}add r6,r4;adds r5,r7,#1;
        ldr r0,[sp,#0x478];cmp r0,#1;beq changed;
        ldr r0,[r6];cmp r0,r5;bne changed;
        ldr r0,[sp,#8];ldr r1,[r6,#4];cmp r0,r1;bne changed;
        ldr r0,[sp,#12];ldr r1,[r6,#8];cmp r0,r1;beq done;
        changed:str r5,[r6];ldr r0,[sp,#8];str r0,[r6,#4];ldr r0,[sp,#12];str r0,[r6,#8];dmb ish;
        {c('r0',0x10453ac3)}movs r1,#8;movs r2,#1;{n(0x10453fac)}b done;
        not_visible:{call('aspect_border_lease_release')}
        done:addw sp,sp,#0x47c;pop {{r4-r7,pc}};'''
    return code
