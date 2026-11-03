# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Native Z30 sound routines and menu support."""
from .sound_layout import *
from ..assembly import constant, invoke

def ui_code(f, strings, fixed, file_names, errors, file_ids, switch_ids, anchor_table, template, child_templates, pages=(), sidebar_parent=None, group=None):
    code = {}
    allocation_size = NEW_SIZE
    page_copy = page_restore = page_labels = getter_check = getter_result = route_extra = route_action = action_extra = ''
    # One registered page per feature; `pages` keeps the order they were declared in.
    for page in pages:
        key, fn = page['key'], page['functions']
        allocation_size = page['allocation_size']
        initial = f"movs r0,#{page['default']};str r0,[r5];" if 'default' in page else ''
        page_copy += f"""{constant('r5',page['page_offset'])}add r5,r4,r5;mov r0,r5;
            {constant('r1',page['template'])}movs r2,#44;{invoke(0x10199bdc,thumb=False)}
            add r0,r4,#{PAGE_OFFSET};str r0,[r5,#4];
            {constant('r5',page['state_offset'])}add r5,r4,r5;{initial}mvn r0,#0;str r0,[r5,#{page['fd_offset']}];"""
        page_restore += ''.join(invoke(f[name]) for name in page['restore'])
        page_labels += invoke(f[fn+'_labels'])
        getter_check += f"{constant('r12',page['index'])}cmp r1,r12;beq {key}_page;"
        getter_result += f"""{key}_page:push {{r1-r3,lr}};{invoke(f['get'])}cmp r0,#0;beq ret;
            {constant('r2',page['page_offset'])}b add_page;"""
        route_extra += f"beq owned;{constant('r2',page['page_offset'])}add r2,r0,r2;cmp r4,r2;"
        route_action = f"""ldrh r2,[r4,#2];{constant('r12',page['index'])}cmp r2,r12;bne not_{key};
            {invoke(f[fn+'_action'])}b action_done;not_{key}:""" + route_action
        # A native shooting-menu row may have pointed this page's parent at the tab container.
        parent = (f"{constant('r0',page['page_offset'])}add r0,r4,r0;add r1,r4,#{PAGE_OFFSET};str r1,[r0,#4];"
                  if page.get('direct') else '')
        action_extra = f"cmp r5,#{page['root_row']};bne not_{key}_root;{parent}{invoke(f[fn+'_open'])}b done;not_{key}_root:" + action_extra
    c = constant
    def call(name): return invoke(f[name])
    native = lambda address: invoke(address, thumb=address != 0x10199bdc)
    sound_page = SOUND_GROUP_INDEX if group else PAGE_INDEX
    sound_offset = group['page_offset'] if group else PAGE_OFFSET
    sound_parent = f"{c('r0',sound_offset)}add r0,r4,r0;"
    group_copy = group_labels = ''
    if group:
        allocation_size = group['allocation_size']
        group_copy = f"""{c('r5',group['page_offset'])}add r5,r4,r5;mov r0,r5;
            {c('r1',group['template'])}movs r2,#44;{native(0x10199bdc)}
            add r0,r4,#{PAGE_OFFSET};str r0,[r5,#4];"""
        group_labels = f"{c('r0',strings['sound_title'])}movs r1,#{SOUND_GROUP_TITLE_SLOT};movs r2,#0;{call('text')}"
        getter_check += f"{c('r12',SOUND_GROUP_INDEX)}cmp r1,r12;beq sound_group_page;"
        getter_result += f"""sound_group_page:push {{r1-r3,lr}};{call('get')}cmp r0,#0;beq ret;
            {c('r2',group['page_offset'])}b add_page;"""
        route_extra += f"beq owned;{c('r2',group['page_offset'])}add r2,r0,r2;cmp r4,r2;"
        action_extra = f"""{c('r0',PAGE_INDEX)}cmp r6,r0;bne sound_controls;
            {action_extra}
            cmp r5,#{group['order'].index('sound')};bne done;
            movs r0,#0;str r0,[r4,#{RESULT}];
            {c('r1',group['page_offset'])}add r1,r4,r1;strh r0,[r1,#22];strh r0,[r1,#24];
            movs r0,#2;{c('r1',SOUND_GROUP_INDEX)}{native(0x1045f732)}b done;
            sound_controls:"""
    sidebar_refresh = ''
    if sidebar_parent:
        sidebar_refresh = f"""{c('r0',sidebar_parent)}ldr r0,[r0,#12];cmp r0,#0;beq done;
            mov r8,r0;movs r1,#1;{native(0x104546b0)}mov r0,r8;{native(0x10454226)}"""
    code['ensure']=f"""
            push {{r4-r6,lr}}; {call('get')} cmp r0,#0; bne done;
            {c('r0',STATE_ANCHOR+20)}movs r1,#0;movs r2,#1;{call('cas')}cmp r0,#1;bne done;
            {call('get')}cmp r0,#0;bne unlock;
            {c('r0',allocation_size+31)} movs r1,#0; {native(0x10211f4c)} cmp r0,#0; beq unlock;
            mov r6,r0; adds r0,#31; bic r4,r0,#31;
            mov r0,r4; {c('r1',allocation_size)} {native(0x10199c40)}
            mov r0,r4; {c('r1',anchor_table)} movs r2,#32; {native(0x10199bdc)}
            add r0,r4,#{PAGE_OFFSET}; {c('r1',template)} movs r2,#44; {native(0x10199bdc)}
            {c('r5',FILES_OFFSET)} add r5,r4,r5; mov r0,r5;
            {c('r1',child_templates[FILES_INDEX])} movs r2,#44; {native(0x10199bdc)} {sound_parent}str r0,[r5,#4];
            {c('r5',SWITCH_OFFSET)} add r5,r4,r5; mov r0,r5;
            {c('r1',child_templates[SWITCH_INDEX])} movs r2,#44; {native(0x10199bdc)} {sound_parent}str r0,[r5,#4];
            {c('r5',POPUP_OFFSET)} add r5,r4,r5; mov r0,r5;
            {c('r1',child_templates[POPUP_INDEX])} movs r2,#44; {native(0x10199bdc)}
            add r0,r4,#{PAGE_OFFSET};str r0,[r5,#4];
            {page_copy} {group_copy} {c('r0',MAGIC)} str r0,[r4,#56];str r6,[r4,#60];mvn r0,#0;str r0,[r4,#112];str r0,[r4,#{SETTINGS_FD}];
            dmb ish;{c('r0',STATE_ANCHOR)} str r4,[r0,#16];{call('restore_settings')}{page_restore}mov r0,r4;
            unlock:{c('r1',STATE_ANCHOR+20)}movs r2,#0;dmb ish;str r2,[r1];
            done: pop {{r4-r6,pc}};
        """

    code['labels']=f"""
            push {{r4-r8,lr}};{c('r4',fixed)} ldr r0,[r4],#4;
            movs r1,#{ENTRY_SLOT};movs r2,#0;{call('text_entry')} movs r5,#{TITLE_SLOT};movs r6,#14;
            fixed_loop: ldr r0,[r4],#4;mov r1,r5;movs r2,#0;{call('text')}
            adds r5,#1;subs r6,#1;bne fixed_loop;
            {call('get')} mov r4,r0;{c('r0',strings['off'])} cmp r4,#0;beq enabled;
            ldr r1,[r4,#{ENABLED}];cmp r1,#1;bne enabled;{c('r0',strings['on'])}
            enabled: movs r1,#{ENABLED_VALUE_SLOT};movs r2,#0;{call('text')}
            {c('r0',strings['none'])} cmp r4,#0;beq selected;
            ldr r1,[r4,#{LENGTH}];cmp r1,#0;beq selected;
            ldr r1,[r4,#{SELECTED}];cmp r1,#4;bhi selected;
            {c('r0',file_names)} ldr r0,[r0,r1,lsl #2];
            selected: movs r1,#{FILE_VALUE_SLOT};movs r2,#0;{call('text')}
            {c('r0',strings['empty'])} movs r1,#{EMPTY_SLOT};movs r2,#0;{call('text')}
            {c('r0',strings['current'])} movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
            movs r5,#0;
            switch_loop: {c('r0',strings['empty'])} cmp r4,#0;beq switch_put;
            ldr r1,[r4,#{ENABLED}];cmp r1,r5;bne switch_put;{c('r0',strings['current'])}
            switch_put: add r1,r5,#{SWITCH_MARK_SLOT};movs r2,#0;{call('text')}
            adds r5,#1;cmp r5,#2;bne switch_loop;
            movs r5,#0;
            files_loop: {c('r0',strings['empty'])} cmp r4,#0;beq file_put;
            ldr r1,[r4,#{LENGTH}];cmp r1,#0;beq file_put;
            ldr r1,[r4,#{SELECTED}];cmp r1,r5;bne file_put;{c('r0',strings['current'])}
            file_put: add r1,r5,#{FILE_MARK_SLOT};movs r2,#0;{call('text')}
            adds r5,#1;cmp r5,#5;bne files_loop;
            {c('r0',strings['empty'])}cmp r4,#0;beq popup_text;
            ldr r1,[r4,#{RESULT}];cmp r1,#8;bhi popup_text;
            {c('r0',errors)}ldr r0,[r0,r1,lsl #2];
            popup_text:movs r1,#{POPUP_BODY_SLOT};movs r2,#0;{call('text')}
            {c('r0',strings['ok'])}movs r1,#{POPUP_OK_SLOT};movs r2,#0;{call('text')}
            done:{page_labels}{group_labels} movs r0,#0;pop {{r4-r8,pc}};
        """

    # The stock layout constructs a static text widget (kind 1). Nikon's
    # binder clears through kind 0 before changing to dynamic ASCII kind 2
    # or UTF-16 kind 3, and preserves the kind on repeated refreshes.
    code['render']=f"""
            push {{r4-r8,lr}};mov r4,r0;{call('labels')}
            ldr r5,[r4,#36];cmp r5,#0;beq done;ldr r6,[r4,#40];movs r7,#0;
            next_row:ldrh r0,[r6,#20];cmp r7,r0;bhs header;
            mov r0,r5;movs r1,#1;mov r2,r7;{native(0x1045745e)} mov r8,r0;
            ldr r0,[r6,#12];movs r1,#28;mla r0,r7,r1,r0;ldr r1,[r0];mov r0,r8;{call('text_bind')}
            mov r0,r5;mov r1,r7;movs r2,#0;{native(0x104574a8)} mov r8,r0;
            ldr r0,[r6,#12];movs r1,#28;mla r0,r7,r1,r0;ldr r1,[r0,#20];mov r0,r8;{call('text_bind')}
            adds r7,#1;b next_row;
            header:ldr r1,[r4,#12];{c('r0',0xa18)}{native(0x10454210)}
            cmp r0,#0;beq done;mov r8,r0;
            mov r0,r8;ldr r1,[r6,#8];ldr r1,[r1];{call('text_bind')}
            ldrh r0,[r4,#2];{c('r1',POPUP_INDEX)}cmp r0,r1;bne sidebar;
            ldr r1,[r4,#12];{c('r0',0xa19)}{native(0x10454210)}cmp r0,#0;beq sidebar;
            movs r1,#{POPUP_BODY_SLOT};{call('text_bind')}
            sidebar:
            {sidebar_refresh}
            done:movs r0,#0;pop {{r4-r8,pc}};
        """

    code['action']=f"""
            push {{r4-r8,lr}};mov r8,r0;{call('get')}mov r4,r0;cmp r4,#0;beq done;
            ldrh r5,[r8,#22];ldrh r0,[r8,#24];add r5,r5,r0;ldrh r6,[r8,#2];
            {c('r0',POPUP_INDEX)}cmp r6,r0;bne not_popup;
            movs r0,#0;str r0,[r4,#{RESULT}];ldr r1,[r8,#4];ldrh r1,[r1,#2];
            movs r0,#2;{native(0x1045f732)}b done;not_popup:
            {c('r0',FILES_INDEX)}cmp r6,r0;beq file_apply;
            {c('r0',SWITCH_INDEX)}cmp r6,r0;beq switch_apply;
            {action_extra} cmp r5,#0;beq open_switch;cmp r5,#1;beq open_files;
            cmp r5,#2;beq preview;cmp r5,#3;bne redraw;
            ldr r0,[r4,#{LENGTH}];cmp r0,#0;beq reload;
            reload:ldr r0,[r4,#{SELECTED}];{call('load')}b redraw;
            open_switch:movs r0,#0;str r0,[r4,#{RESULT}];movs r0,#2;{c('r1',SWITCH_INDEX)}{native(0x1045f732)}
            {c('r0',SWITCH_OFFSET)}add r0,r4,r0;ldr r1,[r4,#{ENABLED}];{c('r2',switch_ids)}ldr r1,[r2,r1,lsl #2];{native(0x1045d54c)}b done;
            open_files:movs r0,#0;str r0,[r4,#{RESULT}];movs r0,#2;{c('r1',FILES_INDEX)}{native(0x1045f732)}
            {c('r0',FILES_OFFSET)}add r0,r4,r0;ldr r1,[r4,#{SELECTED}];{c('r2',file_ids)}ldr r1,[r2,r1,lsl #2];{native(0x1045d54c)}b done;
            file_apply:cmp r5,#4;bhi redraw;mov r0,r5;{call('load')}cmp r0,#1;bne redraw;
            {call('save_settings')}b return_root;
            switch_apply:cmp r5,#1;bhi redraw;cmp r5,#0;beq disable;
            ldr r0,[r4,#{LENGTH}];cmp r0,#0;bne enabled;
            ldr r0,[r4,#{SELECTED}];{call('load')}cmp r0,#1;bne redraw;
            enabled:movs r0,#1;str r0,[r4,#{ENABLED}];b switched;
            disable:movs r0,#0;str r0,[r4,#{ENABLED}];str r0,[r4,#{PENDING}];
            switched:movs r0,#0;str r0,[r4,#{RESULT}];{call('save_settings')}
            return_root:movs r0,#2;{c('r1',sound_page)}{native(0x1045f732)}
            ldr r0,[r4,#{RESULT}];cmp r0,#0;beq draw;
            {sound_parent}{call('show_error')}b done;
            preview:ldr r0,[r4,#{LENGTH}];cmp r0,#0;beq no_file;
            ldr r0,[r4,#{BUSY}];cmp r0,#0;bne busy;
            {c('r0',0x188c)}{native(0x101993de)}cmp r0,#0;bne busy;
            {c('r0',0xc1885a50)}ldrb r0,[r0];cmp r0,#0;bne busy;
            add r0,r4,#{BUSY};movs r1,#0;movs r2,#2;{call('cas')}cmp r0,#1;bne busy;
            mov r0,r4;{call('start')}b redraw;
            no_file:movs r0,#1;str r0,[r4,#{RESULT}];b redraw;
            busy:movs r0,#5;str r0,[r4,#{RESULT}];
            redraw:ldr r0,[r4,#{RESULT}];cmp r0,#0;beq render_page;
            mov r0,r8;{call('show_error')}b done;
            render_page:mov r0,r8;{call('render')}
            draw:{c('r0',0x10453ac3)}movs r1,#3;movs r2,#7;{native(0x10453fac)}
            done:movs r0,#0;pop {{r4-r8,pc}};
        """

    code['getter']=f"""
            cmp r0,#2;bne original;{c('r12',POPUP_INDEX)}cmp r1,r12;beq popup_page;
            {getter_check} {c('r12',PAGE_INDEX)}cmp r1,r12;blo original;
            {c('r12',SWITCH_INDEX)}cmp r1,r12;bhi original;
            push {{r1-r3,lr}};{call('get')}cmp r0,#0;beq ret;
            ldr r1,[sp];{c('r2',PAGE_INDEX)}cmp r1,r2;beq root;adds r2,#1;cmp r1,r2;beq files;
            {c('r2',SWITCH_OFFSET)}b add_page;
            files:{c('r2',FILES_OFFSET)}b add_page;
            root:movs r2,#{PAGE_OFFSET};add_page:add r0,r0,r2;
            ret:pop {{r1-r3,pc}};
            {getter_result} original:cmp r0,#7;mov r2,r0;mov.w r0,#0;bhs outside;{c('r0',0xc2006594)}
            .balign 4;ldr.w pc,[pc,#0];.word 0x1045720b;outside:bx lr;
            popup_page:push {{r1-r3,lr}};{call('get')}cmp r0,#0;beq ret;
            {c('r2',POPUP_OFFSET)}b add_page;
        """

    code['route']=f"""
            push {{r0-r4,lr}};mov r4,r0;{call('get')}cmp r0,#0;beq original;
            {c('r2',POPUP_OFFSET)}add r2,r0,r2;cmp r4,r2;beq owned;
            add r2,r0,#{PAGE_OFFSET};cmp r4,r2;beq owned;
            {c('r2',FILES_OFFSET)}add r2,r0,r2;cmp r4,r2;beq owned;
            {c('r2',SWITCH_OFFSET)}add r2,r0,r2;cmp r4,r2;{route_extra} bne original;
            owned:ldr r3,[sp,#4];{c('r2',0x256)}cmp r3,r2;beq activate;
            {c('r2',0x25b)}cmp r3,r2;beq activate;
            {c('r2',0x26f)}cmp r3,r2;beq activate;
            {c('r2',0x27e)}cmp r3,r2;bne original;
            activate:mov r0,r4;mov r1,r3;{route_action} {call('action')} action_done:
            add sp,#4;pop {{r1-r4,lr}};{c('r0',0x3bb)}bx lr;
            original:pop {{r0-r4,lr}};push {{r4-r10,lr}};movs r5,r0;mov r6,r1;
            .balign 4;ldr.w pc,[pc,#0];.word 0x103ce80d;
        """
    code['show_error']=f"""
        push {{r4,lr}};mov r4,r0;{call('get')}cmp r0,#0;beq done;
        {c('r1',POPUP_OFFSET)}add r1,r0,r1;str r4,[r1,#4];
        {call('labels')}movs r0,#2;{c('r1',POPUP_INDEX)}{native(0x1045f732)}
        {call('get')}{c('r1',POPUP_OFFSET)}add r0,r0,r1;
        {c('r1',POPUP_ID)}{native(0x1045d54c)}
        {c('r0',0x10453ac3)}movs r1,#3;movs r2,#7;{native(0x10453fac)}
        done:movs r0,#0;pop {{r4,pc}};
    """
    # A direct row's page answers to the tab container, but going there would leave
    # focus on the tab bar. Ask the stock history for the page below, as back (0x3BC) does.
    code['page_return']=f"""
        push {{r4,lr}};ldr r1,[r0,#4];{c('r2',sidebar_parent or 0)}cmp r1,r2;beq previous;
        ldrh r1,[r1,#2];b go;
        previous:movw r0,#0x3bc;{native(0x1045f74c)}mov r1,r0;
        go:movs r0,#2;{native(0x1045f732)}pop {{r4,pc}};
    """
    code['dismiss_error']=f"""
        push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        movs r1,#0;str r1,[r0,#{RESULT}];
        done:movs r0,#0;pop {{r4,pc}};
    """
    return code
