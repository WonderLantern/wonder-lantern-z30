"""Persistent buffer requests applied through original socket IPC at PTP init.
No early-loader I/O, cross-CPU shared state or live instruction writes.
"""
import struct
from ..assembly import constant as c, invoke as n, assemble, jump
from .menu_slots import page_slots
from .sound_settings import settings_code

PAGE_INDEX = 960
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('video', 4)
MAGIC = 0x31464C57
NAMES = ('64 KiB (stock)', '128 KiB', '256 KiB', '512 KiB')
SIZES = dict(bitrate_get=96, bitrate_labels=384, bitrate_open=192,
             bitrate_action=256, bitrate_save=640, bitrate_restore=640,
             bitrate_close=128, wireless_apply=384, wireless_send=192)
SOURCE, DESTINATION, LENGTH = 0x11C9E69C, 0xC1000000, 0x204A00
BUFFER_SITE = 0x11CB7FBE
BLOCK_OFFSET = (BUFFER_SITE-SOURCE) & ~31


def prepare(plan, f, resources, allocation_size):
    for name,size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size+44
    strings = {key:resources.alloc(text) for key,text in
               dict(title='Wi-Fi buffer',empty='',mark='*').items()}
    labels = plan.alloc(struct.pack('<4I',*(resources.alloc(text) for text in NAMES)))
    path = plan.alloc(b'A:\\WL-WIFI.CFG\0')
    call = lambda name:n(f[name])
    persistence = settings_code(f,path,state_get='bitrate_get',close='bitrate_close',
                               magic=MAGIC,enabled_offset=8,selected_offset=0,fd_offset=4,
                               enabled_max=0,selected_max=3,load_sound=False)
    codes = {new:persistence[old] for old,new in (
        ('save_settings','bitrate_save'),('restore_settings','bitrate_restore'),
        ('settings_close','bitrate_close'))}
    codes['bitrate_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    codes['bitrate_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('bitrate_get')}movs r4,#1;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#3;it hi;movhi r4,#1;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#4;bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['bitrate_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045F732)}
        {call('bitrate_get')}ldr r4,[r0];cmp r4,#3;it hi;movhi r4,#1;
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        WIRELESS_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045D54C)}
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['bitrate_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#3;bhi done;
        {call('bitrate_get')}cmp r0,#0;beq done;str r5,[r0];dmb ish;
        {call('bitrate_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        root:movs r0,#2;movw r1,#955;{n(0x1045F732)}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    codes['wireless_apply'] = f'''push {{r4-r8,lr}};sub sp,#40;
        {call('ensure')}cmp r0,#0;beq applied;
        {call('bitrate_get')}cmp r0,#0;beq applied;
        ldr r1,[r0];cmp r1,#3;it hi;movhi r1,#1;
        movs r0,#1;adds r1,#16;lsl r0,r0,r1;str r0,[sp,#4];movs r4,#1;
        next_socket:movs r0,#16;str r0,[sp,#8];mov r0,r4;
        add r1,sp,#16;add r2,sp,#8;{n(0x104834FA)}cmp r0,#0;bne advance;
        ldrb r0,[sp,#17];cmp r0,#2;bne advance;
        ldrh r0,[sp,#18];movw r1,#0x7c3d;cmp r0,r1;bne advance;
        movs r0,#4;str r0,[sp];mov r0,r4;movw r1,#0xffff;
        movw r2,#0x1001;add r3,sp,#4;{n(0x104834B8)}
        advance:adds r4,#1;cmp r4,#32;bne next_socket;
        applied:add sp,#40;pop {{r4-r8,pc}};'''
    codes['wireless_send'] = f'''push {{r0-r3,r12,lr}};
        cmp r1,#8;blo original;cmp r0,#0;beq original;
        ldr r3,[r0,#4];cmp r3,#2;beq resize;cmp r3,#4;bne original;
        resize:{call('wireless_apply')}
        original:pop {{r0-r3,r12,lr}};
        push.w {{r4-r8,lr}};movs r5,r2;mov r7,r0;
        {c('r0',int.from_bytes(plan.image.read(0x1031eddc,4),'little'))}mov r8,r1;
        {c('r12',0x1031eabd)}bx r12;'''
    expected=plan.image.read(0x1031eab0,12)
    assert expected.hex()=='2de9f04115000746c8488846'
    plan.edit(0x1031eab0,jump(0x1031eab0,f['wireless_send'],12),
              'Apply saved send buffer through native socket IPC on PTP init acknowledgement',expected)
    return dict(page_offset=page_offset,state_offset=state_offset,
                allocation_size=state_offset+16,default=1,codes=codes)


def finish(plan,f,extension,identifiers):
    ids=plan.alloc(struct.pack('<4I',*identifiers))
    extension['codes']['bitrate_open']=extension['codes']['bitrate_open'].replace('WIRELESS_IDS;',c('r1',ids))
    for name,source in extension['codes'].items():
        plan.put(f[name],assemble(source,f[name],SIZES[name]))
    plan.features['wireless_buffer'] = dict(names=list(NAMES),sizes=[65536,131072,262144,524288],
        default=1,page_index=PAGE_INDEX,page_offset=extension['page_offset'],
        state_offset=extension['state_offset'],settings_file='WL-WIFI.CFG',
        functions={k:hex(f[k]|1) for k in SIZES},controls=list(identifiers),
        scope='Apply through native socket IPC on PTP initialization acknowledgement; reconnect required',
        hardware_verified=False)
