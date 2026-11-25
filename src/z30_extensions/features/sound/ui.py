# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""The shutter sound's own menu pages, as fragments for the menu framework (menu_ui)."""
from dataclasses import dataclass

from ..menu.layout import *
from ...assembly import constant, invoke
from ..menu.ui import list_top


@dataclass
class SoundFragments:
    ensure_pages: str      # copy the file and switch page objects
    ensure_restore: str    # restore WL-SOUND.CFG once the state exists
    labels: str            # entry, title and every sound label; leaves the state in r4
    action_pages: str      # route the file and switch pages to their handlers
    action_controls: str   # sound rows, file / switch choices, preview and reload
    getter_pages: str      # map the root, file and switch page indexes to their objects
    route_pages: str       # the file and switch objects are owned too


def fragments(f, strings, fixed, file_names, file_ids, switch_ids, child_templates, group=None):
    c = constant
    def call(name): return invoke(f[name])
    native = lambda address: invoke(address, thumb=address != 0x10199bdc)
    sound_page = SOUND_GROUP_INDEX if group else PAGE_INDEX
    sound_offset = group['page_offset'] if group else PAGE_OFFSET
    sound_parent = f"{c('r0',sound_offset)}add r0,r4,r0;"
    child = child_templates
    ensure_pages = f"""
            {c('r5',FILES_OFFSET)} add r5,r4,r5; mov r0,r5;
            {c('r1',child[FILES_INDEX])} movs r2,#44; {native(0x10199bdc)} {sound_parent}str r0,[r5,#4];
            {c('r5',SWITCH_OFFSET)} add r5,r4,r5; mov r0,r5;
            {c('r1',child[SWITCH_INDEX])} movs r2,#44; {native(0x10199bdc)} {sound_parent}str r0,[r5,#4];"""
    labels = f"""{c('r4',fixed)} ldr r0,[r4],#4;
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
            adds r5,#1;cmp r5,#5;bne files_loop;"""
    action_pages = f"""{c('r0',FILES_INDEX)}cmp r6,r0;beq file_apply;
            {c('r0',SWITCH_INDEX)}cmp r6,r0;beq switch_apply;"""
    action_controls = f"""cmp r5,#0;beq open_switch;cmp r5,#1;beq open_files;
            cmp r5,#2;beq preview;cmp r5,#3;bne redraw;
            ldr r0,[r4,#{LENGTH}];cmp r0,#0;beq reload;
            reload:ldr r0,[r4,#{SELECTED}];{call('load')}b redraw;
            open_switch:movs r0,#0;str r0,[r4,#{RESULT}];{list_top(SWITCH_OFFSET, 'r4')}movs r0,#2;{c('r1',SWITCH_INDEX)}{native(0x1045f732)}
            {c('r0',SWITCH_OFFSET)}add r0,r4,r0;{c('r2',switch_ids)}ldr r1,[r2];{native(0x1045d54c)}b done;
            open_files:movs r0,#0;str r0,[r4,#{RESULT}];{list_top(FILES_OFFSET, 'r4')}movs r0,#2;{c('r1',FILES_INDEX)}{native(0x1045f732)}
            {c('r0',FILES_OFFSET)}add r0,r4,r0;{c('r2',file_ids)}ldr r1,[r2];{native(0x1045d54c)}b done;
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
            busy:movs r0,#5;str r0,[r4,#{RESULT}];"""
    getter_pages = f"""{c('r12',PAGE_INDEX)}cmp r1,r12;blo original;
            {c('r12',SWITCH_INDEX)}cmp r1,r12;bhi original;
            push {{r1-r3,lr}};{call('get')}cmp r0,#0;beq ret;
            ldr r1,[sp];{c('r2',PAGE_INDEX)}cmp r1,r2;beq root;adds r2,#1;cmp r1,r2;beq files;
            {c('r2',SWITCH_OFFSET)}b add_page;
            files:{c('r2',FILES_OFFSET)}b add_page;
            root:movs r2,#{PAGE_OFFSET};add_page:add r0,r0,r2;
            ret:pop {{r1-r3,pc}};"""
    route_pages = f"""beq owned;
            {c('r2',FILES_OFFSET)}add r2,r0,r2;cmp r4,r2;beq owned;
            {c('r2',SWITCH_OFFSET)}add r2,r0,r2;cmp r4,r2;"""
    return SoundFragments(ensure_pages, call('restore_settings'), labels, action_pages, action_controls,
                          getter_pages, route_pages)
