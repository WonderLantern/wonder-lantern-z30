# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Native Z30 routines of the extension menu framework.

The framework owns the state block, the root page, the error popup and the
routing of every registered page. The shutter sound is an optional part: it
hands in the fragments for its own pages (`sound_ui.SoundFragments`); without
it the root page only lists the feature pages.
"""
from .layout import ROOT_BLANK_OBJECT, ENTRY_SLOT, TITLE_SLOT, EMPTY_SLOT, CURRENT_SLOT, POPUP_BODY_SLOT, POPUP_OK_SLOT, POPUP_INDEX, POPUP_ID, PAGE_INDEX, PAGE_OFFSET, SOUND_GROUP_INDEX, SOUND_GROUP_TITLE_SLOT, STATE_ANCHOR, MAGIC, RESULT, SETTINGS_FD
from ...assembly import constant, invoke


def list_top(page_offset, state='r0'):
    """Reset a page's list cursor (+22) and first visible row (+24) before it opens.

    A page object outlives its visits. The camera can draw a re-entered list
    from the top while its cursor stays on the old row, so the highlight and
    the selected row disagree; every extension page therefore opens at its
    first row (the root page's `open` does the same). Expects the state block
    in `state`; uses r1 and r2.
    """
    return (f"{constant('r1', page_offset)}add r1,{state},r1;movs r2,#0;"
            "strh r2,[r1,#22];strh r2,[r1,#24];")


def ui_code(f, strings, errors, anchor_table, template, child_templates, pages=(), sidebar_parent=None,
            group=None, sound=None, popup_offset=None, allocation_size=None, ensure_root=False):
    """Code of the framework functions.

    sound: sound_ui.SoundFragments or None. popup_offset / allocation_size:
    where the popup object sits and where the first page starts.
    """
    code = {}
    category_rows = {}
    def parent_address(offset):
        # r0 = state (r4) + offset; the root keeps its historical short form.
        return f"add r0,r4,#{offset};" if offset == PAGE_OFFSET else f"{constant('r0',offset)}add r0,r4,r0;"
    page_copy = page_restore = page_labels = getter_check = getter_result = route_extra = route_action = action_extra = ''
    # One registered page per feature; `pages` keeps the order they were declared in.
    for page in pages:
        key, fn = page['key'], page['functions']
        allocation_size = page['allocation_size']
        initial = f"movs r0,#{page['default']};str r0,[r5];" if 'default' in page else ''
        page_copy += f"""{constant('r5',page['page_offset'])}add r5,r4,r5;mov r0,r5;
            {constant('r1',page['template'])}movs r2,#44;{invoke(0x10199bdc,thumb=False)}
            {parent_address(page.get('parent_offset', PAGE_OFFSET))}str r0,[r5,#4];
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
        # Also when the status page opened it last: back must return to the root again.
        parent = (f"{constant('r0',page['page_offset'])}add r0,r4,r0;add r1,r4,#{PAGE_OFFSET};str r1,[r0,#4];"
                  if page.get('direct') or 'status_row' in page else '')
        if 'status_row' in page:
            status = group['status']
            category_rows.setdefault('status', []).append(
                f"""cmp r5,#{page['status_row']};bne not_{key}_status;
                {constant('r0',page['page_offset'])}add r0,r4,r0;{constant('r1',status['page_offset'])}add r1,r4,r1;str r1,[r0,#4];
                {invoke(f[fn+'_open'])}b done;not_{key}_status:""")
        if 'category' in page:
            # Opened from its category page: back and a confirmed choice return there,
            # also after a native shooting-menu row pointed the parent elsewhere.
            cat = page['category']
            category_rows.setdefault(cat['key'], []).append(
                f"""cmp r5,#{page['category_row']};bne not_{key}_row;
                {constant('r0',page['page_offset'])}add r0,r4,r0;{constant('r1',cat['page_offset'])}add r1,r4,r1;str r1,[r0,#4];
                {invoke(f[fn+'_open'])}b done;not_{key}_row:""")
        if 'root_row' in page:   # native-only pages (no custom root row) open from their stock menu row
            action_extra = f"cmp r5,#{page['root_row']};bne not_{key}_root;{parent}{invoke(f[fn+'_open'])}b done;not_{key}_root:" + action_extra
    c = constant
    def call(name): return invoke(f[name])
    native = lambda address: invoke(address, thumb=address != 0x10199bdc)
    category_copy = category_labels = category_actions = ''
    status = (group or {}).get('status')
    for cat in list((group or {}).get('categories', ())) + ([status] if status else []):
        ckey, offset, index = cat['key'], cat['page_offset'], cat['index']
        category_copy += f"""{c('r5',offset)}add r5,r4,r5;mov r0,r5;
            {c('r1',cat['template'])}movs r2,#44;{native(0x10199bdc)}
            add r0,r4,#{PAGE_OFFSET};str r0,[r5,#4];"""
        category_labels += f"{c('r0',cat['string'])}movs r1,#{cat['title_slot']};movs r2,#0;{call('text')}"
        getter_check += f"{c('r12',index)}cmp r1,r12;beq cat_{ckey}_page;"
        getter_result += f"""cat_{ckey}_page:push {{r1-r3,lr}};{call('get')}cmp r0,#0;beq ret;
            {c('r2',offset)}b add_page;"""
        route_extra += f"beq owned;{c('r2',offset)}add r2,r0,r2;cmp r4,r2;"
        # Root row: open the category at its first row with the focus in its list.
        action_extra = f"""cmp r5,#{cat['root_row']};bne not_cat_{ckey};{list_top(offset, 'r4')}
            movs r0,#2;{c('r1',index)}{native(0x1045f732)}
            {c('r0',offset)}add r0,r4,r0;{c('r1',cat['first_id_word'])}ldr r1,[r1];{native(0x1045d54c)}
            b done;not_cat_{ckey}:""" + action_extra
        category_actions += f"""{c('r0',index)}cmp r6,r0;bne not_cat_{ckey}_page;
            {''.join(category_rows.get(ckey, ()))}b done;not_cat_{ckey}_page:"""
    if status:
        s, card = status['strings'], status['card']
        def put(string, slot): return f"{c('r0',s[string])}movs r1,#{slot};movs r2,#0;{call('text')}"
        from .framework import STATUS_LABEL_SLOTS as L, STATUS_VALUE_SLOTS as V, STATUS_ROOT_VALUE_SLOT, CARD_LOADED
        from ..card.loader import CELL
        category_labels += ''.join(put(key, L[key]) for key in status['system'])
        if status.get('banner_debug'):
            # Diagnostic builds: the ready banner's readout instead of the build tag.
            category_labels += f"{invoke(status['banner_debug']['text'])}movs r1,#{V['version']};movs r2,#0;{call('text')}"
        else:
            category_labels += put('version_value', V['version'])
        category_labels += put('features_value', V['features'])
        # r6 counts problems: a card patch not cleanly loaded, a settings save that failed.
        card_value = (f"""{c('r0',CELL)}ldrh r1,[r0];movw r2,#{CARD_LOADED};cmp r1,r2;beq st_card_ok;
            adds r6,#1;{c('r0',s['check'])}b st_card_put;st_card_ok:{c('r0',s['loaded'])}st_card_put:"""
                      if card else c('r0', s['built_in']))
        category_labels += f"""{call('get')}mov r5,r0;movs r6,#0;
            {card_value}movs r1,#{V['card']};movs r2,#0;{call('text')}
            {c('r0',s['saved'])}cmp r5,#0;beq st_saved_put;{c('r1',status['flag_offset'])}ldr r1,[r5,r1];cmp r1,#0;beq st_saved_put;
            adds r6,#1;{c('r0',s['not_saved'])}st_saved_put:movs r1,#{V['settings']};movs r2,#0;{call('text')}
            {c('r0',s['normal'])}cmp r6,#0;beq st_root_put;{c('r0',s['check'])}
            st_root_put:movs r1,#{STATUS_ROOT_VALUE_SLOT};movs r2,#0;{call('text')}"""
    group_copy = group_labels = ''
    if group:
        allocation_size = group['allocation_size']
        if sound:
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
        else:
            action_extra = f"""{c('r0',PAGE_INDEX)}cmp r6,r0;bne done;
                {action_extra} b done;"""
    sidebar_refresh = ''
    if sidebar_parent:
        sidebar_refresh = f"""{c('r0',sidebar_parent)}ldr r0,[r0,#12];cmp r0,#0;beq done;
            mov r8,r0;movs r1,#1;{native(0x104546b0)}mov r0,r8;{native(0x10454226)}"""

    code['get']=f"""
            {c('r0',STATE_ANCHOR)} ldr r0,[r0,#16]; {c('r1',anchor_table)}
            cmp r0,r1; beq absent; cmp r0,#0; beq absent;
            ldr r1,[r0,#56]; {c('r2',MAGIC)} cmp r1,r2; bne absent; bx lr;
            absent: movs r0,#0; bx lr;
        """
    code['cas']="""
            movs r12,#8;
            retry: ldrex r3,[r0]; cmp r3,r1; bne mismatch;
            strex r3,r2,[r0]; cmp r3,#0; beq good;
            subs r12,r12,#1; bne retry;
            mismatch: clrex; movs r0,#0; bx lr;
            good: dmb ish; movs r0,#1; bx lr;
        """
    # The root page object persists, and its list cursor (+22) and first visible row
    # (+24) with it. The camera can draw a re-entered list from the top while the
    # cursor stays on the old row, so each entry starts both at the first row.
    code['open']=f"""
            push {{r4,lr}}; {call('ensure')} cmp r0,#0; beq done;
            movs r1,#0; strh r1,[r0,#{PAGE_OFFSET + 22}]; strh r1,[r0,#{PAGE_OFFSET + 24}]; {call('labels')}
            movs r0,#2; {c('r1',PAGE_INDEX)} {native(0x1045f732)}
            done: movs r0,#0; pop {{r4,pc}};
        """
    code['ensure']=f"""
            push {{r4-r6,lr}}; {call('get')} cmp r0,#0; bne done;
            {c('r0',STATE_ANCHOR+20)}movs r1,#0;movs r2,#1;{call('cas')}cmp r0,#1;bne done;
            {call('get')}cmp r0,#0;bne unlock;
            {c('r0',allocation_size+31)} movs r1,#0; {native(0x10211f4c)} cmp r0,#0; beq unlock;
            mov r6,r0; adds r0,#31; bic r4,r0,#31;
            mov r0,r4; {c('r1',allocation_size)} {native(0x10199c40)}
            mov r0,r4; {c('r1',anchor_table)} movs r2,#32; {native(0x10199bdc)}
            add r0,r4,#{PAGE_OFFSET}; {c('r1',template)} movs r2,#44; {native(0x10199bdc)}
            {sound.ensure_pages if sound else ''}
            {c('r5',popup_offset)} add r5,r4,r5; mov r0,r5;
            {c('r1',child_templates[POPUP_INDEX])} movs r2,#44; {native(0x10199bdc)}
            add r0,r4,#{PAGE_OFFSET};str r0,[r5,#4];
            {page_copy} {group_copy} {category_copy} {c('r0',MAGIC)} str r0,[r4,#56];str r6,[r4,#60];mvn r0,#0;str r0,[r4,#112];str r0,[r4,#{SETTINGS_FD}];
            dmb ish;{c('r0',STATE_ANCHOR)} str r4,[r0,#16];{sound.ensure_restore if sound else ''}{page_restore}mov r0,r4;
            unlock:{c('r1',STATE_ANCHOR+20)}movs r2,#0;dmb ish;str r2,[r1];
            done: pop {{r4-r6,pc}};
        """
    # Labels: the sound part (when present) starts with the entry and title from its
    # fixed table and leaves the state in r4 for the popup text.
    plain_labels = f"""{c('r0',strings['entry'])}movs r1,#{ENTRY_SLOT};movs r2,#0;{call('text_entry')}
            {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
            {call('get')} mov r4,r0;
            {c('r0',strings['empty'])} movs r1,#{EMPTY_SLOT};movs r2,#0;{call('text')}
            {c('r0',strings['current'])} movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}"""
    code['labels']=f"""
            push {{r4-r8,lr}};{sound.labels if sound else plain_labels}
            {c('r0',strings['empty'])}cmp r4,#0;beq popup_text;
            ldr r1,[r4,#{RESULT}];cmp r1,#8;bhi popup_text;
            {c('r0',errors)}ldr r0,[r0,r1,lsl #2];
            popup_text:movs r1,#{POPUP_BODY_SLOT};movs r2,#0;{call('text')}
            {c('r0',strings['ok'])}movs r1,#{POPUP_OK_SLOT};movs r2,#0;{call('text')}
            done:{page_labels}{group_labels}{category_labels} movs r0,#0;pop {{r4-r8,pc}};
        """

    # The stock layout constructs a static text widget (kind 1). Nikon's
    # binder clears through kind 0 before changing to dynamic ASCII kind 2
    # or UTF-16 kind 3, and preserves the kind on repeated refreshes.
    # Menu tab builds: the root's empty widget over the feature pages' back button.
    blank_bind = (f"ldr r1,[r4,#12];{c('r0',ROOT_BLANK_OBJECT)}{native(0x10454210)}cmp r0,#0;beq blank_done;"
                  f"movs r1,#{EMPTY_SLOT};{call('text_bind')}blank_done:") if ensure_root else ''
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
            sidebar:{blank_bind}
            {sidebar_refresh}
            done:movs r0,#0;pop {{r4-r8,pc}};
        """

    code['action']=f"""
            push {{r4-r8,lr}};mov r8,r0;{call('get')}mov r4,r0;cmp r4,#0;beq done;
            ldrh r5,[r8,#22];ldrh r0,[r8,#24];add r5,r5,r0;ldrh r6,[r8,#2];
            {c('r0',POPUP_INDEX)}cmp r6,r0;bne not_popup;
            movs r0,#0;str r0,[r4,#{RESULT}];ldr r1,[r8,#4];ldrh r1,[r1,#2];
            movs r0,#2;{native(0x1045f732)}b done;not_popup:
            {sound.action_pages if sound else ''}
            {category_actions} {action_extra} {sound.action_controls if sound else ''}
            redraw:ldr r0,[r4,#{RESULT}];cmp r0,#0;beq render_page;
            mov r0,r8;{call('show_error')}b done;
            render_page:mov r0,r8;{call('render')}
            draw:{c('r0',0x10453ac3)}movs r1,#3;movs r2,#7;{native(0x10453fac)}
            done:movs r0,#0;pop {{r4-r8,pc}};
        """

    root_only = f"""{c('r12',PAGE_INDEX)}cmp r1,r12;bne original;
            push {{r1-r3,lr}};{call('get')}cmp r0,#0;beq ret;
            root:movs r2,#{PAGE_OFFSET};add_page:add r0,r0,r2;
            ret:pop {{r1-r3,pc}};"""
    code['getter']=f"""
            cmp r0,#2;bne original;{c('r12',POPUP_INDEX)}cmp r1,r12;beq popup_page;
            {getter_check} {sound.getter_pages if sound else root_only}
            {getter_result} original:cmp r0,#7;mov r2,r0;mov.w r0,#0;bhs outside;{c('r0',0xc2006594)}
            .balign 4;ldr.w pc,[pc,#0];.word 0x1045720b;outside:bx lr;
            popup_page:push {{r1-r3,lr}};{call('get')}cmp r0,#0;beq ret;
            {c('r2',popup_offset)}b add_page;
        """

    if ensure_root:
        # The root page can be opened straight from a menu tab (menu_tab), before
        # anything made the state block: create it on the first lookup.
        lookup = f"push {{r1-r3,lr}};{call('get')}cmp r0,#0;beq ret;"
        assert lookup in code['getter']
        code['getter'] = code['getter'].replace(lookup, f"""push {{r1-r3,lr}};{call('get')}cmp r0,#0;bne have_state;
            {call('ensure')}cmp r0,#0;beq ret;have_state:""", 1)
    code['route']=f"""
            push {{r0-r4,lr}};mov r4,r0;{call('get')}cmp r0,#0;beq original;
            {c('r2',popup_offset)}add r2,r0,r2;cmp r4,r2;beq owned;
            add r2,r0,#{PAGE_OFFSET};cmp r4,r2;{sound.route_pages if sound else ''}{route_extra} bne original;
            owned:ldr r3,[sp,#4];{c('r2',0x256)}cmp r3,r2;beq activate;
            {c('r2',0x25b)}cmp r3,r2;beq activate;
            {c('r2',0x26f)}cmp r3,r2;beq activate;
            {c('r2',0x27e)}cmp r3,r2;bne original;
            activate:mov r0,r4;mov r1,r3;{route_action} {call('action')} action_done:
            add sp,#4;pop {{r1-r4,lr}};{c('r0',0x3bb)}bx lr;
            original:pop {{r0-r4,lr}};push {{r4-r10,lr}};movs r5,r0;mov r6,r1;
            .balign 4;ldr.w pc,[pc,#0];.word 0x103ce80d;
        """
    # A failed settings save (RESULT 8) also marks the status page until the next start.
    save_flag = (f"""ldr r1,[r0,#{RESULT}];cmp r1,#8;bne no_save_flag;
        {c('r1',status['flag_offset'])}movs r2,#1;str r2,[r0,r1];no_save_flag:""" if status else '')
    code['show_error']=f"""
        push {{r4,lr}};mov r4,r0;{call('get')}cmp r0,#0;beq done;{save_flag}
        {c('r1',popup_offset)}add r1,r0,r1;str r4,[r1,#4];
        {call('labels')}movs r0,#2;{c('r1',POPUP_INDEX)}{native(0x1045f732)}
        {call('get')}{c('r1',popup_offset)}add r0,r0,r1;
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
