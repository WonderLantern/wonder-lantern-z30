# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Build owned sound menus and native SD-WAV audio hooks from stock templates."""
import itertools
import struct
from ..assembly import assemble, constant, invoke, jump
from .sound_layout import *
from .sound_audio import audio_code
from .sound_ui import ui_code

ROOT_ORDER = ('aspect', 'zebra', 'portrait', 'stamp', 'nef', 'bitrate', 'sound', 'modules')


def add(plan, modules=False, bitrate=False, aspect=False, zebra=False, stamp=False, portrait=False, nef=False, wireless=False, aspect_trace=False, aspect_border=False, aspect_border_diagnostic=False, aspect_border_lease=False, aspect_mask=False, aspect_mask_trial=False, precapture_probe=False, lut_sd=False, lut_cube_text=None, lut_size=17):
    if lut_sd and (wireless or bitrate or precapture_probe):
        raise ValueError('SD LUT research menu requires the independent experimental page slot')
    if precapture_probe and (wireless or bitrate):
        raise ValueError('JPG capture probe needs the independent experimental page slot')
    if wireless and bitrate:
        raise ValueError('Wireless research menu and bitrate menu currently share one owned page slot')
    bitrate = bitrate or wireless or precapture_probe or lut_sd
    im = plan.image
    if im.read(STATE_ANCHOR, 44) != bytes(44):
        raise ValueError('Sound state requires the unused stock page-zero slot')
    setup_header = list(im.words(SETUP_META_SOURCE, 7))
    if setup_header[5] & 65535 != 38:
        raise ValueError('Unexpected stock settings row count')
    # Copy a valid native callback table into the private state prefix. Page 0
    # remains absent from the flash layout; only its unused RAM pointer anchors
    # this allocation after opening sound. No Camera Status page is registered.
    anchor_table = im.words(0xC1832D50 + 16, 1)[0]
    sizes = {'get': 128, 'cas': 96, 'validate': 384, 'convert': 192,
             'load': 1024, 'start': 448, 'stop': 192, 'trigger': 384,
             'complete': 384, 'open': 192, 'config': 320, 'data': 320,
             'shot': 192, 'ensure': 512, 'labels': 1024, 'render': 512,
             'action': 768, 'row': 256, 'getter': 256, 'route': 320, 'setup_init': 128}
    sizes.update(ensure=768, action=1024, getter=384, show_error=192,
                 save_settings=640, restore_settings=640)
    sizes['settings_close'] = 128
    sizes['dismiss_error'] = 96
    sizes['page_return'] = 96
    if aspect:
        sizes.update(ensure=1024, getter=512, route=448, row=384)
    grouped = bool(modules or bitrate or aspect or zebra or stamp or portrait or nef)
    if grouped:
        sizes.update(ensure=1280, getter=640, route=640, action=1280)
    if stamp:
        sizes.update(ensure=1536, getter=768, route=768, action=1536)
    if nef:
        sizes.update(ensure=sizes['ensure'] + 128, getter=sizes['getter'] + 64,
                     route=sizes['route'] + 64, action=sizes['action'] + 128)
    # Research builds keep their single-purpose page inside the custom menu only.
    native_menus = () if wireless or lut_sd or precapture_probe else tuple(
        menu for menu in NATIVE_MENUS if any(dict(aspect=aspect, zebra=zebra, portrait=portrait, stamp=stamp, bitrate=bitrate)[key] for key in menu[3]))
    if native_menus:
        sizes.update(row=768)
        for name, _, _, keys in native_menus:
            sizes[f'select_{name}'] = 128
            sizes[f'init_{name}'] = 128
            # Builds without the portrait page keep their earlier layout byte for byte.
            sizes.update({f'direct_{key}': 96 for key in keys if key != 'portrait' or portrait})
    f = {name: plan.alloc(bytes(size)) for name, size in sizes.items()}
    from . import ui_text
    text_resources = ui_text.prepare(plan, f)
    tables = {i: plan.alloc(bytes(32)) for i in (PAGE_INDEX, FILES_INDEX, SWITCH_INDEX, POPUP_INDEX)}
    metas = {i: plan.alloc(bytes(28)) for i in tables}
    texts = dict(entry='Shutter sound', title='Shutter sound', files='Select sound', switch='Sound enabled',
                 enable='Enabled', file='Sound file', preview='Preview sound', reload='Reload sound',
                 off='OFF', on='ON', none='None', root='SHUTTER.WAV',
                 one='1.WAV', two='2.WAV', three='3.WAV', four='4.WAV', empty='', current='*',
                 missing='File not found', read='Read failed', invalid='Invalid WAV', close='Close failed',
                 busy='Sound busy', queue='Play failed', audio='Audio failed', ok='OK', save='Settings not saved')
    if grouped:
        texts['entry'] = texts['title'] = 'Extensions'
    strings = {key: text_resources.alloc(text) for key, text in texts.items()}
    if grouped:
        strings['sound_title'] = text_resources.alloc('Shutter sound')
    paths = [plan.alloc(path.encode() + b'\0') for path in ['A:\\SHUTTER.WAV'] + [f'A:\\SHUTTER\\{i}.WAV' for i in range(1, 5)]]
    path_table = plan.alloc(struct.pack('<5I', *paths))
    file_names = plan.alloc(struct.pack('<5I', *[strings[k] for k in ('root', 'one', 'two', 'three', 'four')]))
    fixed = plan.alloc(struct.pack('<15I', *[strings[k] for k in
        ('entry', 'title', 'enable', 'file', 'preview', 'reload', 'off', 'on', 'root', 'one', 'two', 'three', 'four', 'switch', 'files')]))
    errors = plan.alloc(struct.pack('<9I', *[strings[k] for k in ('empty', 'missing', 'read', 'invalid', 'close', 'busy', 'queue', 'audio', 'save')]))
    file_ids = plan.alloc(struct.pack('<5I', *FILE_IDS))
    switch_ids = plan.alloc(struct.pack('<2I', *SWITCH_IDS))
    # Settings is a child of the native tab container. Returning to the
    # settings object directly skips reconstruction of the shared sidebar.
    # Match native settings leaf pages by parenting to that container.
    setup_parent = im.words(SETUP_OBJECT + 4, 1)[0]
    template = plan.alloc(struct.pack('<11I', (PAGE_INDEX << 16) | 2, setup_parent, 0, 0,
                                    tables[PAGE_INDEX], 0, 0, 0, 0, 0, metas[PAGE_INDEX]))
    child_templates = {i: plan.alloc(struct.pack('<11I', (i << 16) | 2, 0, 0, 0, tables[i],
                                                0, 0, 0, 0, 0, metas[i])) for i in (FILES_INDEX, SWITCH_INDEX, POPUP_INDEX)}
    def declare(index):
        tables[index], metas[index] = plan.alloc(bytes(32)), plan.alloc(bytes(28))
        child_templates[index] = plan.alloc(struct.pack('<11I',
            (index << 16) | 2, 0, 0, 0, tables[index], 0, 0, 0, 0, 0, metas[index]))
    # The one place a feature page is registered. Each entry names its module,
    # how many rows it shows and how its state restores; the state block,
    # control IDs, root row and all native routing derive from this list.
    pages = []
    allocation_size = NEW_SIZE
    def register(key, module, rows, prefix=None, restore=(), value_slot=None, prepare=None, **options):
        nonlocal allocation_size
        declare(module.PAGE_INDEX)
        page = prepare() if prepare else module.prepare(plan, f, text_resources, allocation_size, **options)
        page.update(key=key, module=module, rows=rows, index=module.PAGE_INDEX, functions=prefix or key,
                    restore=restore, template=child_templates[module.PAGE_INDEX],
                    fd_offset=page.get('fd_offset', 4), root_slot=module.ROOT_SLOT,
                    value_slot=value_slot or module.CURRENT_SLOT)
        allocation_size = page['allocation_size']
        pages.append(page)
        return page
    if modules:
        from . import module_host as mh
        register('modules', mh, 4, prefix='module', value_slot=mh.STATUS_SLOT,
                 prepare=lambda: mh.prepare(plan, f, text_resources))
    if bitrate:
        if lut_sd:
            from . import lut_ui as bu
        elif precapture_probe:
            from . import precapture_probe as bu
        elif wireless:
            from . import wireless_ui as bu
        else:
            from . import bitrate_ui as bu
        video = register('bitrate', bu, 4 if wireless or lut_sd else 3, restore=('bitrate_restore',),
                         **(dict(cube_text=lut_cube_text, size=lut_size) if lut_sd else {}))
        video['default'] = video.get('default', 2)
    if aspect:
        from . import aspect_ui as au
        register('aspect', au, 7, restore=('aspect_restore', 'aspect_sync'), trace=aspect_trace,
                 border=aspect_border, border_diagnostic=aspect_border_diagnostic, border_lease=aspect_border_lease,
                 mask=aspect_mask, mask_trial=aspect_mask_trial)
    if zebra:
        from . import zebra_ui as zu
        register('zebra', zu, 2, restore=('zebra_restore',))
    if portrait:
        from . import portrait_menu as pm
        register('portrait', pm, pm.ROWS, restore=('portrait_restore',))
    if stamp:
        from . import date_stamp_ui as su
        register('stamp', su, su.ROWS, restore=('stamp_restore',))
    if nef:
        from . import nef_ui as nu
        register('nef', nu, 3, restore=('nef_restore',))
    by_key = {page['key']: page for page in pages}
    # Scan stock row IDs now: direct rows need theirs before the row hook assembles.
    used = set()
    for index in range(955):
        page = im.words(0xC1832D24 + index * 44, 11)
        if not any(page):
            continue
        meta = im.words(page[10], 7)
        for row in range(meta[5] & 65535):
            used.add(struct.unpack('<H', im.read(meta[3] + row * 28 + 24, 2))[0])
    # Direct rows take IDs from the top so page IDs do not depend on which menus exist.
    native_ids = (i for i in range(0x2FFF,0x2400,-1) if i not in used)
    native = []
    for name, page_object, stock_rows, keys in native_menus:
        present = tuple(key for key in keys if key in by_key)
        for key in present:
            by_key[key]['direct'] = True
        native.append(dict(name=name, object=page_object, first_row=stock_rows, keys=present,
                           select=f'select_{name}', ids=tuple(itertools.islice(native_ids, len(present)))))
    group = None
    if grouped:
        order = [key for key in ROOT_ORDER if key == 'sound' or key in by_key]
        declare(SOUND_GROUP_INDEX)
        group = dict(order=order, page_offset=allocation_size, allocation_size=allocation_size+44,
                     template=child_templates[SOUND_GROUP_INDEX],
                     labels={'sound': SOUND_GROUP_TITLE_SLOT}, values={'sound': ENABLED_VALUE_SLOT})
        for page in pages:
            page['root_row'] = order.index(page['key'])
            group['labels'][page['key']], group['values'][page['key']] = page['root_slot'], page['value_slot']
        if modules:
            plan.features['module_host']['root_row'] = by_key['modules']['root_row']
    codes = audio_code(f, path_table, anchor_table)
    from .sound_settings import settings_code
    config_path = plan.alloc(b'A:\\WL-SOUND.CFG\0')
    codes.update(settings_code(f, config_path))
    codes.update(ui_code(f, strings, fixed, file_names, errors, file_ids, switch_ids,
                         anchor_table, template, child_templates, pages, sidebar_parent=setup_parent,
                         group=group))
    c = constant
    call = lambda name: invoke(f[name])
    owned_pages = [PAGE_INDEX, FILES_INDEX, SWITCH_INDEX, POPUP_INDEX] + [page['index'] for page in pages]
    if grouped:
        owned_pages.append(SOUND_GROUP_INDEX)
    codes['row'] = ui_text.row_code(f, SETUP_OBJECT, ENTRY_ID, owned_pages, native)
    for menu in native:
        # Stock menu leaves parent to the tab container, whose current child is the
        # menu the row was opened from, so back and a confirmed choice both return there.
        for key in menu['keys']:
            page = by_key[key]
            codes[f'direct_{key}'] = f"""push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
                {c('r1',page['page_offset'])}add r0,r0,r1;{c('r1',setup_parent)}str r1,[r0,#4];
                {call(page['functions']+'_open')}done:movs r0,#0;pop {{r4,pc}};"""
        original = im.words(im.words(menu['object'] + 16, 1)[0], 1)[0]
        codes[f"init_{menu['name']}"] = f"""push {{r0-r4,lr}};{call(menu['select'])}{call('labels')}
            pop {{r0-r4,lr}};{c('r12',original|1)}bx r12;"""
    setup_controls = im.read(setup_header[3], 38 * 28)
    entry = bytearray(setup_controls[-28:])
    struct.pack_into('<I', entry, 0, ENTRY_SLOT)
    # Row bit 9 selects ASCII kind 2; bit 13 selects Unicode kind 3.
    struct.pack_into('<I', entry, 16, 0x3001)
    struct.pack_into('<I', entry, 24, ENTRY_ID)
    unicode_controls = plan.alloc(setup_controls + entry)
    struct.pack_into('<I', entry, 16, 0x1201)
    controls_address = plan.alloc(setup_controls + entry)
    ui_text.configure_entry(plan, f, controls_address, unicode_controls)

    original_setup = im.words(im.words(SETUP_OBJECT + 16, 1)[0], 1)[0]
    codes['setup_init'] = f"""
        push {{r4,lr}}; mov r4,r0; {call('entry_select')} {call('labels')} mov r0,r4;
        {invoke(original_setup)} pop {{r4,pc}};
    """
    code_sizes = {}
    for name, source in codes.items():
        raw = assemble(source, f[name], sizes[name])
        plan.put(f[name], raw)
        code_sizes[name] = len(raw)
    zero = plan.alloc(bytes.fromhex('00207047 01207047'))

    # Retain native navigation handlers. Never copy firmware-update/device
    # callbacks into owned sound pages for unrelated messages.
    source_events = []
    for n in range(200):
        row = im.words(0x1140ED20 + n * 16, 4)
        source_events.append(row)
        if row[0] == 0x31E:
            break
    else:
        raise ValueError('Missing native event-map terminator')
    navigation = {0x1045FA81, 0x1045FA83, 0x1045FAA9, 0x1045FA65,
                  0x1045FA71, 0x1045FA7D, 0x1045FAF7}
    def events(callback):
        words = []
        for event, fn, predicate, destination in source_events:
            if event in (0x256, 0x25B, 0x26F, 0x27E):
                fn, predicate, destination = callback | 1, 0, 0x3BB
            elif fn not in navigation:
                fn, predicate = 0, 0
                destination = 0x3BC if destination == 0x3BC else 0x3BB
            words.extend((event, fn, predicate, destination))
        return plan.alloc(struct.pack('<' + 'I' * len(words), *words))
    entry_events = events(f['open'])
    owned_events = events(f['action'])
    dispatch_address = plan.alloc(im.read(setup_header[4], 38 * 4) + struct.pack('<I', entry_events))
    plan.edit(SETUP_META_SOURCE + 12, struct.pack('<2I', controls_address, dispatch_address), 'Sound settings entry tables')
    plan.edit(SETUP_META_SOURCE + 20, struct.pack('<I', (setup_header[5] & 0xFFFF0000) | 39), 'Settings row count with sound only')
    plan.edit(im.words(SETUP_OBJECT + 16, 1)[0], struct.pack('<I', f['setup_init'] | 1), 'Seed sound entry before settings rows')
    for menu in native:
        source = im.rom(im.words(menu['object'] + 40, 1)[0])
        header = im.words(source, 7)
        count = header[5] & 65535
        if count != menu['first_row'] or im.words(menu['object'] + 4, 1)[0] != setup_parent:
            raise ValueError(f"Unexpected native {menu['name']} menu")
        stock_controls = im.read(header[3], count * 28)
        def direct_rows(flags):
            added = bytearray()
            for key, ident in zip(menu['keys'], menu['ids']):
                # The settings entry row has an empty value column; stock rows here carry real values.
                row = bytearray(entry)
                struct.pack_into('<I', row, 0, by_key[key]['root_slot'])
                struct.pack_into('<I', row, 16, flags)
                struct.pack_into('<I', row, 24, ident)
                added += row
            return bytes(added)
        unicode_table = plan.alloc(stock_controls + direct_rows(0x3001))
        ascii_table = plan.alloc(stock_controls + direct_rows(0x1201))
        ui_text.configure_entry(plan, f, ascii_table, unicode_table, menu['select'])
        row_events = [events(f[f'direct_{key}']) for key in menu['keys']]
        dispatch = plan.alloc(im.read(header[4], count * 4) + struct.pack(f'<{len(row_events)}I', *row_events))
        plan.edit(source + 12, struct.pack('<2I', ascii_table, dispatch), f"Native {menu['name']} menu direct rows")
        plan.edit(source + 20, struct.pack('<I', (header[5] & 0xFFFF0000) | (count + len(row_events))), f"Native {menu['name']} menu row count")
        plan.edit(im.words(menu['object'] + 16, 1)[0], struct.pack('<I', f[f"init_{menu['name']}"] | 1), f"Seed native {menu['name']} menu rows")

    # Stock leaf page 0x18f contains both a header and the native back bitmap.
    # Copy those records from the caller's stock, then use dynamic header text.
    leaf = im.words(0xC1832D24 + 0x18F * 44, 11)
    if not setup_parent or leaf[1] != setup_parent:
        raise ValueError('Unexpected native settings tab parent')
    leaf_header = list(im.words(leaf[10], 7))
    if leaf_header[0] != 0x1008:
        raise ValueError('Unexpected native leaf menu flags')
    base_layout = bytearray(im.read(leaf_header[2], 96))
    if im.words(leaf_header[2] + 32, 8)[1] != 4:
        raise ValueError('Native back icon record missing')
    if im.words(leaf_header[2] + 64, 8)[1] != 0:
        raise ValueError('Unexpected leaf layout terminator')
    # Layout kind 1 constructs a text widget. Kind 2 is not the dynamic
    # string selector; the render callback supplies that selector separately.
    struct.pack_into('<I', base_layout, 28, 0xA18 << 16)

    if set(ROOT_IDS + FILE_IDS + SWITCH_IDS + (ENTRY_ID, POPUP_ID)) & used:
        raise ValueError('Custom control identifiers conflict with stock')
    page_report = {}
    page_specs = [
        (PAGE_INDEX, 4, ROOT_LABEL_SLOT, ENABLED_VALUE_SLOT, TITLE_SLOT, ROOT_IDS),
        (FILES_INDEX, 5, FILE_LABEL_SLOT, FILE_MARK_SLOT, FILES_TITLE_SLOT, FILE_IDS),
        (SWITCH_INDEX, 2, SWITCH_LABEL_SLOT, SWITCH_MARK_SLOT, SWITCH_TITLE_SLOT, SWITCH_IDS),
        (POPUP_INDEX, 1, POPUP_OK_SLOT, EMPTY_SLOT, TITLE_SLOT, (POPUP_ID,)),
    ]
    free_ids = (i for i in range(0x2400,0x3000) if i not in used)
    def claim_ids(count):
        ids = tuple(itertools.islice(free_ids, count))
        if len(ids) != count:
            raise ValueError('No free control IDs for owned menu pages')
        return ids
    category_ids = {}
    for page in pages:
        ids = claim_ids(page['rows'] + 1)
        category_ids[page['key']] = ids[-1]
        module = page['module']
        marks = mh.EMPTY_SLOT if page['key'] == 'modules' else module.MARK_SLOT
        page_specs.append((page['index'], page['rows'], module.LABEL_SLOT, marks, module.TITLE_SLOT, ids[:-1]))
        if page['key'] == 'modules':
            plan.features['module_host']['controls'] = list(ids[:-1])
        else:
            module.finish(plan, f, page, ids[:-1])
    if grouped:
        category_ids['sound'] = claim_ids(1)[0]
        page_specs[0] = (PAGE_INDEX,len(group['order']),ROOT_LABEL_SLOT,ENABLED_VALUE_SLOT,TITLE_SLOT,
                         tuple(category_ids[key] for key in group['order']))
        page_specs.append((SOUND_GROUP_INDEX,4,ROOT_LABEL_SLOT,ENABLED_VALUE_SLOT,SOUND_GROUP_TITLE_SLOT,ROOT_IDS))
    for index, count, label, value, title, identifiers in page_specs:
        if count > MAX_ROWS:
            raise ValueError(f'Owned menu page {index} has {count} rows; only {MAX_ROWS} fit one screen')
        is_root = index == PAGE_INDEX
        is_sound_group = grouped and index == SOUND_GROUP_INDEX
        is_module = modules and index == mh.PAGE_INDEX
        controls = bytearray()
        for i in range(count):
            control = bytearray(entry)
            label_slot = group['labels'][group['order'][i]] if grouped and is_root else label+i
            struct.pack_into('<I', control, 0, label_slot)
            struct.pack_into('<I', control, 16, 0x1209)
            if grouped and is_root:
                value_slot = group['values'][group['order'][i]]
            elif is_module:
                value_slot = mh.STATUS_SLOT if i == 2 else mh.EMPTY_SLOT
            elif is_root or is_sound_group:
                value_slot = value+i if i<2 else EMPTY_SLOT
            else:
                value_slot = value+i
            struct.pack_into('<I', control, 20, value_slot)
            struct.pack_into('<I', control, 24, identifiers[i])
            controls.extend(control)
        control_address = plan.alloc(controls)
        dispatch = plan.alloc(struct.pack('<' + 'I' * count, *([owned_events] * count)))
        layout = bytearray(base_layout)
        struct.pack_into('<I', layout, 0, title)
        if index == POPUP_INDEX:
            # Preserve the normal header/back geometry; put messages in an
            # independent body widget, never in the shared title widget.
            body = bytearray(base_layout[:32])
            struct.pack_into('<I', body, 0, POPUP_BODY_SLOT)
            struct.pack_into('<4h', body, 16, 150, 150, 620, 260)
            struct.pack_into('<I', body, 28, 0xA19 << 16)
            layout = layout[:64] + body + bytes(32)
        layout_address = plan.alloc(layout)
        # Menu boxes, label boxes, value boxes. Root filenames get a 330 px
        # value column; choices use a compact '*' marker in a 126 px column.
        boxes, labels, values = [], [], []
        for row in range(count):
            y = ROW_TOP + row * ROW_PITCH
            boxes.extend((72, y, 702, y + 48))
            if is_root or is_sound_group:
                has_value = bool(grouped and is_root) or row < 2
                label_right, value_left = (350,366) if has_value else (696,696)
            elif is_module:
                has_value = row == 2
                label_right, value_left = (350,366) if has_value else (696,696)
            else:
                label_right, value_left = 550,570
            labels.extend((78, y + 4, label_right, y + 44))
            # Native row composition clips its label at the value box even
            # when that box contains an empty string. Give label-only rows
            # a zero-width value box at the right edge.
            values.extend((value_left, y, 696, y + 48))
        if index == POPUP_INDEX:
            boxes, labels, values = [250,280,500,332], [270,284,480,328], [480,280,480,332]
        rectangles = [plan.alloc(struct.pack('<' + 'h' * len(rect), *rect)) for rect in (boxes, labels, values)]
        geometry = plan.alloc(struct.pack('<4I', count, *rectangles))
        header = leaf_header.copy()
        # Bit 3 makes the leaf replace its parent's drawing. Keep the
        # ancestor tab container visible while retaining owned leaf controls.
        header[0] &= ~8
        header[1:5] = [geometry, layout_address, control_address, dispatch]
        # Low half: total rows. High half: rows per screen. The source leaf
        # only shows three; owned geometry fits all rows and must own capacity.
        header[5] = (count << 16) | count
        plan.put(metas[index], struct.pack('<7I', *header))
        plan.put(tables[index], struct.pack('<8I', f['render'] | 1, (f['dismiss_error'] if index == POPUP_INDEX else zero) | 1, 0x1045FDA1,
                                           (zero + 4) | 1, zero | 1, zero | 1, zero | 1, 0x1045FDA1))
        page_report[str(index)] = {'metadata': hex(metas[index]), 'functions': hex(tables[index]),
                             'count': count, 'controls': hex(control_address), 'layout': hex(layout_address),
                             'geometry': hex(geometry), 'label_rectangles': [labels[i:i+4] for i in range(0, len(labels), 4)],
                             'value_rectangles': [values[i:i+4] for i in range(0, len(values), 4)],
                             'title_index': title, 'label_start': label, 'value_start': value,
                             'back_icon': True, 'header_object_id': hex(0xA18),
                             'ancestor_sidebar_visible': True, 'layout_flags': hex(header[0])}
    for site, name, length in ((0x1045E33C, 'row', 8), (0x104571FE, 'getter', 12),
                               (0x10072598, 'shot', 8), (0x1006BEDE, 'config', 12),
                               (0x1006BE90, 'data', 8), (0x103CE804, 'route', 8)):
        plan.edit(site, jump(site, f[name], length), 'Sound hook: ' + name)
    plan.features['shutter_sound'] = {
        'functions': {k: hex(v | 1) for k, v in f.items()}, 'code_sizes': code_sizes,
        'pages': page_report, 'state_anchor': hex(STATE_ANCHOR + 16), 'anchor_table': hex(anchor_table),
        'page_template': hex(template), 'persistent_state_size': (group['allocation_size'] if grouped else NEW_SIZE) + 31,
        'root_parent': hex(setup_parent),
        'page_offsets': {str(PAGE_INDEX): PAGE_OFFSET, str(FILES_INDEX): FILES_OFFSET, str(SWITCH_INDEX): SWITCH_OFFSET, str(POPUP_INDEX): POPUP_OFFSET, **{str(page['index']): page['page_offset'] for page in pages}, **({str(SOUND_GROUP_INDEX):group['page_offset']} if grouped else {})},
        'menu_categories': ([dict(key=key,row=row,control_id=category_ids[key],
                                  page_index=by_key[key]['index'] if key in by_key else SOUND_GROUP_INDEX)
                             for row,key in enumerate(group['order'])] if grouped else []),
        'text_slots': {str(index): dict(title=title, label=label, value=value)
                       for index, _, label, value, title, _ in page_specs},
        'entry_slot': ENTRY_SLOT, 'root_label_slots': group['labels'] if grouped else {},
        'native_rows': {menu['name']: dict(object=hex(menu['object']), first_row=menu['first_row'],
                                           rows=[dict(key=key, row=menu['first_row']+i, control_id=menu['ids'][i],
                                                      label_slot=by_key[key]['root_slot'], page_index=by_key[key]['index'],
                                                      direct=hex(f[f'direct_{key}'] | 1))
                                                 for i, key in enumerate(menu['keys'])])
                        for menu in native},
        'sound_settings_page': SOUND_GROUP_INDEX if grouped else PAGE_INDEX,
        'control_identifiers': {str(index): list(identifiers) for index, _, _, _, _, identifiers in page_specs},
        'setup_entry_id': hex(ENTRY_ID), 'settings_count': 39,
        'setup_entry_controls': {'ascii': hex(controls_address), 'utf16': hex(unicode_controls)},
        'setup_entry_prefetch_refresh': True,
        'default_enabled': False, 'settings_persistence': 'SD WL-SOUND.CFG; restore on first menu entry or photo trigger',
        'error_ui': 'Native dialog body and OK control; stable menu titles',
        'ui_languages': {'26': 'zh-Hans', '27': 'zh-Hant', 'other': 'en'},
        'ui_text_api': {'ascii': '0x10454efe', 'utf16': '0x10454f5a', 'language_key': '0x1400',
                        'ascii_widget_kind': 2, 'utf16_widget_kind': 3, 'unicode_style': 120,
                        'chinese_fonts': {'26': 2, '27': 0}, 'chinese_module_output_limit': 24},
        'sample_format': '8 kHz, mono, signed 16-bit, canonical 44-byte WAV, <=1.08 seconds',
        'card_paths': ['SHUTTER.WAV'] + [f'SHUTTER/{i}.WAV' for i in range(1, 5)],
        'selection_mark': '*', 'native_back_icon_preserved': True, 'no_status_ui': True,
        'physical_menu_audio_verified': False,
    }
