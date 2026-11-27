# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""The extension menu framework: one entry row in the stock setup menu, a root
page listing the feature pages, an error popup, native direct rows in the photo
and movie shooting menus, and the state block every page lives in.

Feature pages register through `add(...)`; the shutter sound is an optional
part (`sound=True`) that adds its own pages and audio hooks. Without it the
root page lists only the feature pages and no sound buffers are allocated.

Allocation order: with the sound present, functions, texts and tables are
allocated in the same order as before the framework was split from the sound
(SLOT_ORDER, TEXT_ORDER), so those builds stay byte for byte identical.
"""
import itertools
import struct
from ...assembly import assemble, constant, invoke, jump
from .layout import *
from .slots import BLOCKS

# The root page lists these categories in order (docs/menu-layout.md). A category
# whose build has one member puts that member straight on the root; an empty one
# is left out; a root that would hold a single category lists its members instead.
# A None title marks a member that is always a root row (the sound's own group page).
CATEGORIES = (('photo', 'Photo', ('aspect', 'zebra', 'stamp', 'grain', 'nef')),
              ('focus', 'Focus & display', ('trk3d', 'afcol', 'portrait')),
              ('video', 'Video', ('bitrate', 'gyro')),
              ('sound', None, ('sound',)),
              ('lab', 'Experimental', ('modules',)))
# Each category page keeps one page number and one title slot whatever the build holds.
CATEGORY_PAGES = {key: 970 + i for i, key in enumerate(key for key, title, _ in CATEGORIES if title)}
CATEGORY_TITLE_SLOTS = {key: BLOCKS['category'][0] + i for i, key in enumerate(CATEGORY_PAGES)}
if len(CATEGORY_TITLE_SLOTS) > BLOCKS['category'][1]:
    raise ValueError('More category pages than reserved title slots')
# Every key that can take a root row, in root order (the categories expanded).
ROOT_ORDER = tuple(key for _, _, members in CATEGORIES for key in members)
# The status page: the root's first row in every grouped build (docs/menu-layout.md).
# System rows first, then the current value of the pages that change the files shot;
# those rows open their page. Slots: title (the root label), root value, labels, values.
STATUS_INDEX = 974
# The status page only shows information: no row opens a page or changes a setting
# (2026-11-25; the feature values are on the category pages).
STATUS_FEATURES = ()
STATUS_ROWS = ('card', 'version', 'features', 'settings')
STATUS_SLOTS = tuple(base + i for name in ('status', 'status2', 'status3')
                     for base, size in [BLOCKS[name]] for i in range(size))
STATUS_TITLE_SLOT, STATUS_ROOT_VALUE_SLOT = STATUS_SLOTS[:2]
STATUS_LABEL_SLOTS = dict(zip(STATUS_ROWS, STATUS_SLOTS[2:6]))
STATUS_VALUE_SLOTS = dict(zip(STATUS_ROWS, STATUS_SLOTS[6:10]))
STATUS_TEXTS = dict(title='Status', card='Card file', version='Version', features='Features',
                    settings='Settings', loaded='Loaded', check='Check', built_in='Built in',
                    saved='Saved', not_saved='Not saved', normal='Normal')
# Load state the resident loader leaves in its cell (card_loader.CELL): DONE | code << 8.
CARD_LOADED = 0x8000 | 1 << 8


def root_layout(present):
    """Root rows (feature, 'sound' or category keys) and the category pages a build needs."""
    rows, categories = [], []
    for key, title, members in CATEGORIES:
        have = tuple(m for m in members if m in present)
        if not have:
            continue
        if title is None or len(have) == 1:
            rows.extend(have)
        else:
            rows.append(key)
            categories.append(dict(key=key, title=title, members=have, index=CATEGORY_PAGES[key],
                                   title_slot=CATEGORY_TITLE_SLOTS[key]))
    if len(rows) == 1 and categories:
        return list(categories[0]['members']), []
    return rows, categories
# Historical allocation order of the function slots; the sound's own slots are
# interleaved here because they were allocated together before the split.
SLOT_ORDER = ('get', 'cas', 'validate', 'convert', 'load', 'start', 'stop', 'trigger', 'complete', 'open',
              'config', 'data', 'shot', 'ensure', 'labels', 'render', 'action', 'row', 'getter', 'route',
              'setup_init', 'show_error', 'save_settings', 'restore_settings', 'settings_close',
              'dismiss_error', 'page_return', 'touch_page')
TEXT_ORDER = ('entry', 'title', 'files', 'switch', 'enable', 'file', 'preview', 'reload', 'off', 'on', 'none',
              'root', 'one', 'two', 'three', 'four', 'empty', 'current', 'missing', 'read', 'invalid', 'close',
              'busy', 'queue', 'audio', 'ok', 'save')
FRAMEWORK_TEXTS = dict(entry='Extensions', title='Extensions', empty='', current='*', ok='OK',
                       save='Settings not saved')
# Error popup messages by RESULT code; codes 1-7 belong to the sound.
ERROR_KEYS = ('empty', 'missing', 'read', 'invalid', 'close', 'busy', 'queue', 'audio', 'save')
# Without the sound the popup sits right after the framework header and root page.
PLAIN_POPUP_OFFSET = WAV_OFFSET

# A menu object (scatter RAM) holds at +16 a pointer to its 5-word callback record;
# the first word is the page init callback the menus replace.
SEED_RECORD_BYTES = 20


def menu_paths(group, categories, category_ids, by_key):
    """Per page key, the rows to activate from the root: page, row, control id and the page it opens."""
    paths = {}
    if group.get('status'):
        paths['status'] = [dict(page=PAGE_INDEX, row=group['status']['root_row'],
                                control_id=category_ids['status'], opens=STATUS_INDEX)]
    for key in list(by_key) + (['sound'] if 'sound' in group['order'] else []):
        target = by_key[key]['index'] if key in by_key else SOUND_GROUP_INDEX
        cat = next((c for c in categories if key in c['members']), None)
        if cat:
            paths[key] = [dict(page=PAGE_INDEX, row=cat['root_row'], control_id=category_ids[cat['key']], opens=cat['index']),
                          dict(page=cat['index'], row=cat['members'].index(key), control_id=category_ids[key], opens=target)]
        elif key in group['order']:
            paths[key] = [dict(page=PAGE_INDEX, row=group['order'].index(key), control_id=category_ids[key], opens=target)]
    return paths


def seed_record(plan, menu_object, callback, name):
    record = plan.image.words(menu_object + 16, 1)[0]
    if getattr(plan, 'card', False):
        # The records sit in EG flash the camera cannot write. Card builds give
        # the object a RAM copy of its record instead (the object itself is RAM).
        copy = plan.copy_table(record, SEED_RECORD_BYTES, [(0, struct.pack('<I', callback | 1), None)])
        plan.edit(menu_object + 16, struct.pack('<I', copy), f'{name}: RAM record', struct.pack('<I', record))
    else:
        plan.edit(record, struct.pack('<I', callback | 1), name)


def add(plan, sound=True, menu_tab=False, modules=False, bitrate=False, aspect=False, zebra=False, stamp=False, grain=False, portrait=False, nef=False, gyro=False, trk3d=False, afcol=False, wireless=False, aspect_trace=False, aspect_border=False, aspect_border_diagnostic=False, aspect_border_lease=False, aspect_mask=False, aspect_mask_trial=False, precapture_probe=False, cmos_dump=False, cmos_trace=False, cmos_spi=False, cmos_window=False, lut_sd=False, lut_cube_text=None, lut_size=17,
        build_tag='dev', resident_id=None, feature_count=None):
    if lut_sd and (wireless or bitrate or precapture_probe):
        raise ValueError('SD LUT research menu requires the independent experimental page slot')
    if precapture_probe and (wireless or bitrate):
        raise ValueError('JPG capture probe needs the independent experimental page slot')
    if sum(map(bool, (cmos_dump, cmos_trace, cmos_spi, cmos_window))) > 1:
        raise ValueError('Choose one CMOS research page: dump, trace, spi or window')
    cmos_dump = cmos_dump or cmos_trace or cmos_spi or cmos_window
    if cmos_dump and (wireless or bitrate or precapture_probe or lut_sd):
        raise ValueError('CMOS data dump needs the independent experimental page slot')
    if wireless and bitrate:
        raise ValueError('Wireless research menu and bitrate menu currently share one owned page slot')
    bitrate = bitrate or wireless or precapture_probe or lut_sd or cmos_dump
    # A menu tab is always grouped, so its root has the status page even without feature pages.
    grouped = bool(menu_tab or modules or bitrate or aspect or zebra or stamp or grain or portrait or nef or gyro or trk3d or afcol)
    if not (sound or grouped):
        raise ValueError('The extension menu needs the shutter sound or at least one feature page')
    if sound:
        from ..sound import shutter_sound
    im = plan.image
    if im.read(STATE_ANCHOR, 44) != bytes(44):
        raise ValueError('The extension menu state requires the unused stock page-zero slot')
    setup_header = list(im.words(SETUP_META_SOURCE, 7))
    if setup_header[5] & 65535 != 38:
        raise ValueError('Unexpected stock settings row count')
    # Copy a valid native callback table into the private state prefix. Page 0
    # remains absent from the flash layout; only its unused RAM pointer anchors
    # this allocation after opening the menu. No Camera Status page is registered.
    anchor_table = im.words(0xC1832D50 + 16, 1)[0]
    sizes = {'get': 128, 'cas': 96, 'open': 192, 'ensure': 512, 'labels': 1024, 'render': 512,
             'action': 768, 'row': 256, 'getter': 256, 'route': 320, 'setup_init': 128}
    sizes.update(ensure=768, action=1024, getter=384, show_error=192)
    sizes['dismiss_error'] = 96
    sizes['page_return'] = 96
    sizes['touch_page'] = 48
    if sound:
        sizes.update(shutter_sound.SIZES)
    sizes = {name: sizes[name] for name in SLOT_ORDER if name in sizes}
    if aspect:
        sizes.update(ensure=1024, getter=512, route=448, row=384)
    if grouped:
        sizes.update(ensure=1280, getter=640, route=640, action=1280)
    if stamp:
        sizes.update(ensure=1536, getter=768, route=768, action=1536)
    if nef:
        sizes.update(ensure=sizes['ensure'] + 128, getter=sizes['getter'] + 64,
                     route=sizes['route'] + 64, action=sizes['action'] + 128)
    if grain:
        sizes.update(ensure=sizes['ensure'] + 128, getter=sizes['getter'] + 64,
                     route=sizes['route'] + 64, action=sizes['action'] + 128)
    # Research builds keep their single-purpose page inside the custom menu only.
    present = {key for key, on in dict(modules=modules, bitrate=bitrate, aspect=aspect, zebra=zebra, stamp=stamp, grain=grain,
               portrait=portrait, nef=nef, gyro=gyro, trk3d=trk3d, afcol=afcol, sound=sound and grouped).items() if on}
    root_keys, categories = root_layout(present) if grouped else ([], [])
    card = bool(getattr(plan, 'card', False))
    status = None
    if grouped:
        # Card builds show the loader's result and the resident id; flash builds have neither.
        system = STATUS_ROWS
        status = dict(key='status', title=STATUS_TEXTS['title'], index=STATUS_INDEX, title_slot=STATUS_TITLE_SLOT,
                      system=system, members=tuple(k for k in STATUS_FEATURES if k in present), card=card,
                      banner_debug=getattr(plan, 'banner_debug', None))
        root_keys = ['status'] + list(root_keys)
        sizes.update(ensure=sizes['ensure'] + 64, getter=sizes['getter'] + 48, route=sizes['route'] + 32,
                     action=sizes['action'] + 96 + 64 * len(status['members']),
                     labels=sizes['labels'] + 384, show_error=sizes['show_error'] + 32)
    if categories:
        n = len(categories)
        members = sum(len(cat['members']) for cat in categories)
        sizes.update(ensure=sizes['ensure'] + 64 * n, getter=sizes['getter'] + 48 * n,
                     route=sizes['route'] + 32 * n, action=sizes['action'] + 96 * n + 64 * members,
                     labels=sizes['labels'] + 32 * n)
    native_menus = () if wireless or lut_sd or precapture_probe or cmos_dump else tuple(
        menu for menu in NATIVE_MENUS if any(dict(aspect=aspect, zebra=zebra, portrait=portrait, stamp=stamp, bitrate=bitrate, trk3d=trk3d, afcol=afcol)[key] for key in menu[3]))
    if native_menus:
        sizes.update(row=768)
        for name, _, _, keys in native_menus:
            sizes[f'select_{name}'] = 128
            sizes[f'init_{name}'] = 128
            # Builds without the portrait page keep their earlier layout byte for byte.
            sizes.update({f'direct_{key}': 96 for key in keys if key != 'portrait' or portrait})
    f = {name: plan.alloc(bytes(size)) for name, size in sizes.items()}
    from . import text as ui_text
    if grain:
        # Its title uses characters outside the stock fonts; only builds with the page add them.
        from ..photo.film_grain_ui import TITLE
        plan.feature_labels = getattr(plan, 'feature_labels', ()) + (TITLE,)
    text_resources = ui_text.prepare(plan, f)
    own_indexes = [PAGE_INDEX] + ([FILES_INDEX, SWITCH_INDEX] if sound else []) + [POPUP_INDEX]
    tables = {i: plan.alloc(bytes(32)) for i in own_indexes}
    metas = {i: plan.alloc(bytes(28)) for i in own_indexes}
    texts = dict(FRAMEWORK_TEXTS)
    if sound:
        texts.update(shutter_sound.TEXTS)
        if grouped:
            texts['entry'] = texts['title'] = 'Extensions'
    strings = {key: text_resources.alloc(texts[key]) for key in TEXT_ORDER if key in texts}
    for cat in categories:
        strings[f"category_{cat['key']}"] = text_resources.alloc(cat['title'])
    if status:
        status['strings'] = {key: text_resources.alloc(text) for key, text in STATUS_TEXTS.items()}
        status['strings']['version_value'] = text_resources.alloc(build_tag)
        status['features'] = total = feature_count if feature_count is not None else len(present)
        status['strings']['features_value'] = text_resources.alloc(f'{total} features', (f'{total} 项', f'{total} 項'))
        status['string'] = status['strings']['title']
    if sound:
        parts = shutter_sound.prepare(plan, text_resources, strings, grouped)
    errors = plan.alloc(struct.pack('<9I', *[strings.get(k, strings['empty']) for k in ERROR_KEYS]))
    if sound:
        parts.update(shutter_sound.prepare_ids(plan))
    # Settings is a child of the native tab container. Returning to the
    # settings object directly skips reconstruction of the shared sidebar.
    # Match native settings leaf pages by parenting to that container.
    setup_parent = im.words(SETUP_OBJECT + 4, 1)[0]
    template = plan.alloc(struct.pack('<11I', (PAGE_INDEX << 16) | 2, setup_parent, 0, 0,
                                    tables[PAGE_INDEX], 0, 0, 0, 0, 0, metas[PAGE_INDEX]))
    child_templates = {i: plan.alloc(struct.pack('<11I', (i << 16) | 2, 0, 0, 0, tables[i],
                                                0, 0, 0, 0, 0, metas[i])) for i in own_indexes[1:]}
    def declare(index):
        tables[index], metas[index] = plan.alloc(bytes(32)), plan.alloc(bytes(28))
        child_templates[index] = plan.alloc(struct.pack('<11I',
            (index << 16) | 2, 0, 0, 0, tables[index], 0, 0, 0, 0, 0, metas[index]))
    # The one place a feature page is registered. Each entry names its module,
    # how many rows it shows and how its state restores; the state block,
    # control IDs, root row and all native routing derive from this list.
    pages = []
    popup_offset = POPUP_OFFSET if sound else PLAIN_POPUP_OFFSET
    allocation_size = NEW_SIZE if sound else popup_offset + 44
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
        from ..modules import host as mh
        register('modules', mh, 4, prefix='module', value_slot=mh.STATUS_SLOT,
                 prepare=lambda: mh.prepare(plan, f, text_resources))
    if bitrate:
        if lut_sd:
            from ..lut import ui as bu
        elif precapture_probe:
            from ..research import precapture_probe as bu
        elif cmos_window:
            from ..research import cmos_window as bu
        elif cmos_spi:
            from ..research import cmos_spi as bu
        elif cmos_trace:
            from ..research import cmos_trace as bu
        elif cmos_dump:
            from ..research import cmos_dump as bu
        elif wireless:
            from ..research import wireless_ui as bu
        else:
            from ..video import bitrate_ui as bu
        video = register('bitrate', bu, getattr(bu, 'ROWS', 4 if wireless or lut_sd else 3), restore=('bitrate_restore',),
                         **(dict(cube_text=lut_cube_text, size=lut_size) if lut_sd else {}))
        video['default'] = video.get('default', 2)
    if grain and aspect:
        # Both write into the NEF crd XMP block at its closing 0x101B86B4: the aspect gate
        # calls the grain elements first (inside crd:), film_grain.add_crd fills this hook.
        from ..photo.film_grain import CRD_HOOK_BYTES
        f['grain_crd'] = plan.alloc(bytes(CRD_HOOK_BYTES))
    if aspect:
        from ..aspect import ui as au
        register('aspect', au, 7, restore=('aspect_restore', 'aspect_sync'), trace=aspect_trace,
                 border=aspect_border, border_diagnostic=aspect_border_diagnostic, border_lease=aspect_border_lease,
                 mask=aspect_mask, mask_trial=aspect_mask_trial)
    if zebra:
        from ..photo import zebra_ui as zu
        register('zebra', zu, 2, restore=('zebra_restore',))
    if portrait:
        from ..portrait import menu as pm
        register('portrait', pm, pm.ROWS, restore=('portrait_restore',))
    if stamp:
        from ..photo import date_stamp_ui as su
        register('stamp', su, su.ROWS, restore=('stamp_restore',))
    if grain:
        from ..photo import film_grain_ui as gr
        register('grain', gr, gr.ROWS, restore=('grain_restore',))
    if nef:
        from ..nef import ui as nu
        register('nef', nu, 3, restore=('nef_restore',))
    if gyro:
        from ..gyro import ui as gu
        register('gyro', gu, gu.ROWS, restore=('gyro_restore',))
    if trk3d:
        # A direct row in the stock photo shooting menu, and a row of the focus category.
        from ..tracking3d import ui as fu
        register('trk3d', fu, fu.ROWS, restore=('trk3d_restore',))
    if afcol:
        # Also a direct photo-menu row and a row of the focus category.
        from ..photo import af_frame_colour_ui as au_col
        register('afcol', au_col, au_col.ROWS, restore=('afcol_restore',))
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
        order = list(root_keys)
        if sound:
            declare(SOUND_GROUP_INDEX)
            group = dict(order=order, page_offset=allocation_size, allocation_size=allocation_size+44,
                         template=child_templates[SOUND_GROUP_INDEX],
                         labels={'sound': SOUND_GROUP_TITLE_SLOT}, values={'sound': ENABLED_VALUE_SLOT})
        else:
            group = dict(order=order, page_offset=None, allocation_size=allocation_size, labels={}, values={})
        for cat in categories:
            declare(cat['index'])
            cat.update(page_offset=group['allocation_size'], template=child_templates[cat['index']],
                       root_row=order.index(cat['key']), string=strings[f"category_{cat['key']}"],
                       first_id_word=plan.alloc(bytes(4)))   # filled once the row IDs are claimed
            group['allocation_size'] += 44
            group['labels'][cat['key']], group['values'][cat['key']] = cat['title_slot'], EMPTY_SLOT
        group['categories'] = categories
        if status:
            declare(STATUS_INDEX)
            status.update(page_offset=group['allocation_size'], template=child_templates[STATUS_INDEX],
                          root_row=order.index('status'), first_id_word=plan.alloc(bytes(4)),
                          flag_offset=group['allocation_size'] + 44)   # set once a settings save failed
            group['allocation_size'] += 48
            group['labels']['status'], group['values']['status'] = STATUS_TITLE_SLOT, STATUS_ROOT_VALUE_SLOT
        group['status'] = status
        for page in pages:
            group['labels'][page['key']], group['values'][page['key']] = page['root_slot'], page['value_slot']
            cat = next((cat for cat in categories if page['key'] in cat['members']), None)
            if cat:
                page.update(category=cat, category_row=cat['members'].index(page['key']),
                            parent_offset=cat['page_offset'])
            elif page['key'] in order:
                page['root_row'] = order.index(page['key'])
            if status and page['key'] in status['members']:
                page['status_row'] = len(status['system']) + status['members'].index(page['key'])
        if modules:
            plan.features['module_host']['root_row'] = by_key['modules']['root_row']
    codes = shutter_sound.code(plan, f, parts, anchor_table) if sound else {}
    from .ui import ui_code
    fragments = shutter_sound.fragments(f, strings, parts, child_templates, group) if sound else None
    codes.update(ui_code(f, strings, errors, anchor_table, template, child_templates, pages,
                         sidebar_parent=setup_parent, group=group, sound=fragments,
                         popup_offset=popup_offset, allocation_size=NEW_SIZE if sound else popup_offset + 44,
                         ensure_root=menu_tab))
    c = constant
    call = lambda name: invoke(f[name])
    owned_pages = own_indexes + [page['index'] for page in pages]
    if grouped and sound:
        owned_pages.append(SOUND_GROUP_INDEX)
    owned_pages += [cat['index'] for cat in categories] + ([STATUS_INDEX] if status else [])
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
    # callbacks into owned pages for unrelated messages.
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
    if not menu_tab:
        # With the menu tab the extension root is the eighth tab; no setup row.
        plan.edit(SETUP_META_SOURCE + 12, struct.pack('<2I', controls_address, dispatch_address), 'Extension menu entry tables')
        plan.edit(SETUP_META_SOURCE + 20, struct.pack('<I', (setup_header[5] & 0xFFFF0000) | 39), 'Settings row count with the extension entry')
        seed_record(plan, SETUP_OBJECT, f['setup_init'], 'Seed the extension entry before settings rows')
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
        seed_record(plan, menu['object'], f[f"init_{menu['name']}"], f"Seed native {menu['name']} menu rows")

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
    # As a menu tab the root is a top-level page: the setup page's centred title
    # (the same 0xA18 header widget) and no back icon.
    top_layout = bytearray(im.read(setup_header[2], 64))
    title_record = im.words(setup_header[2], 8)
    if title_record[1] != 1 or title_record[7] != 0xA18 << 16 or im.words(setup_header[2] + 32, 8)[1] != 0:
        raise ValueError('Unexpected stock settings layout')

    if set(ROOT_IDS + FILE_IDS + SWITCH_IDS + (ENTRY_ID, POPUP_ID)) & used:
        raise ValueError('Custom control identifiers conflict with stock')
    page_report = {}
    page_specs = [(PAGE_INDEX, 4, ROOT_LABEL_SLOT, ENABLED_VALUE_SLOT, TITLE_SLOT, ROOT_IDS)]
    if sound:
        page_specs += shutter_sound.PAGE_SPECS
    page_specs.append((POPUP_INDEX, 1, POPUP_OK_SLOT, EMPTY_SLOT, TITLE_SLOT, (POPUP_ID,)))
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
    row_slots = {}
    if grouped:
        if sound:
            category_ids['sound'] = claim_ids(1)[0]
        for cat in categories:
            category_ids[cat['key']] = claim_ids(1)[0]
        if status:
            category_ids['status'] = claim_ids(1)[0]
            status['ids'] = claim_ids(len(status['system']) + len(status['members']))
            plan.put(status['first_id_word'], struct.pack('<I', status['ids'][0]))
        page_specs[0] = (PAGE_INDEX,len(group['order']),ROOT_LABEL_SLOT,ENABLED_VALUE_SLOT,TITLE_SLOT,
                         tuple(category_ids[key] for key in group['order']))
        row_slots[PAGE_INDEX] = [(group['labels'][key], group['values'][key]) for key in group['order']]
        for cat in categories:
            plan.put(cat['first_id_word'], struct.pack('<I', category_ids[cat['members'][0]]))
            page_specs.append((cat['index'], len(cat['members']), ROOT_LABEL_SLOT, EMPTY_SLOT, cat['title_slot'],
                               tuple(category_ids[key] for key in cat['members'])))
            row_slots[cat['index']] = [(group['labels'][key], group['values'][key]) for key in cat['members']]
        if status:
            page_specs.append((STATUS_INDEX, len(status['ids']), ROOT_LABEL_SLOT, EMPTY_SLOT, STATUS_TITLE_SLOT,
                               status['ids']))
            row_slots[STATUS_INDEX] = ([(STATUS_LABEL_SLOTS[k], STATUS_VALUE_SLOTS[k]) for k in status['system']] +
                                       [(group['labels'][k], group['values'][k]) for k in status['members']])
        if sound:
            page_specs.append((SOUND_GROUP_INDEX,4,ROOT_LABEL_SLOT,ENABLED_VALUE_SLOT,SOUND_GROUP_TITLE_SLOT,ROOT_IDS))
    for index, count, label, value, title, identifiers in page_specs:
        if count > MAX_ROWS:
            raise ValueError(f'Owned menu page {index} has {count} rows; only {MAX_ROWS} fit one screen')
        is_root = index == PAGE_INDEX
        is_sound_group = grouped and sound and index == SOUND_GROUP_INDEX
        is_module = modules and index == mh.PAGE_INDEX
        controls = bytearray()
        for i in range(count):
            control = bytearray(entry)
            label_slot = row_slots[index][i][0] if index in row_slots else label+i
            struct.pack_into('<I', control, 0, label_slot)
            struct.pack_into('<I', control, 16, 0x1209)
            if index in row_slots:
                value_slot = row_slots[index][i][1]
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
        layout = bytearray(top_layout if menu_tab and is_root else base_layout)
        struct.pack_into('<I', layout, 0, title)
        if menu_tab and is_root:
            # An empty text widget over the back button of the feature pages:
            # returning to the root repaints that corner.
            blank = bytearray(top_layout[:32])
            struct.pack_into('<I', blank, 0, EMPTY_SLOT)
            struct.pack_into('<4h', blank, 16, 628, 0, 720, 54)
            struct.pack_into('<I', blank, 28, ROOT_BLANK_OBJECT << 16)
            layout = layout[:32] + blank + layout[32:]
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
            if index in row_slots:
                # Category rows on the root have no value; feature rows show their current value.
                has_value = row_slots[index][row][1] != EMPTY_SLOT
            elif is_root or is_sound_group:
                has_value = row < 2
            elif is_module:
                has_value = row == 2
            else:
                has_value = None
            if has_value is None:
                label_right, value_left = 550,570
            else:
                label_right, value_left = (350,366) if has_value else (696,696)
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
                             'back_icon': not (menu_tab and is_root), 'header_object_id': hex(0xA18),
                             'ancestor_sidebar_visible': True, 'layout_flags': hex(header[0])}
    hooks = ((0x1045E33C, 'row', 8), (0x104571FE, 'getter', 12), (0x10072598, 'shot', 8),
             (0x1006BEDE, 'config', 12), (0x1006BE90, 'data', 8), (0x103CE804, 'route', 8),
             (0x10475A58, 'touch_page', 8))
    for site, name, length in hooks:
        if name in f:
            plan.edit(site, jump(site, f[name], length),
                      ('Sound hook: ' if name in ('shot', 'config', 'data') else 'Menu hook: ') + name)
    report = {
        'functions': {k: hex(v | 1) for k, v in f.items()}, 'code_sizes': code_sizes,
        'pages': page_report, 'state_anchor': hex(STATE_ANCHOR + 16), 'anchor_table': hex(anchor_table),
        'page_template': hex(template),
        'persistent_state_size': (group['allocation_size'] if grouped else NEW_SIZE) + 31,
        'root_parent': hex(setup_parent), 'shutter_sound': bool(sound), 'popup_offset': popup_offset,
        'page_offsets': {str(PAGE_INDEX): PAGE_OFFSET, str(POPUP_INDEX): popup_offset,
                         **({str(FILES_INDEX): FILES_OFFSET, str(SWITCH_INDEX): SWITCH_OFFSET} if sound else {}),
                         **{str(page['index']): page['page_offset'] for page in pages},
                         **({str(SOUND_GROUP_INDEX): group['page_offset']} if grouped and sound else {}),
                         **{str(cat['index']): cat['page_offset'] for cat in categories},
                         **({str(STATUS_INDEX): status['page_offset']} if status else {})},
        'menu_categories': ([dict(key=key,row=row,control_id=category_ids[key],
                                  page_index=(by_key[key]['index'] if key in by_key else
                                              CATEGORY_PAGES[key] if key in CATEGORY_PAGES else
                                              STATUS_INDEX if key == 'status' else SOUND_GROUP_INDEX))
                             for row,key in enumerate(group['order'])] if grouped else []),
        'category_rows': {cat['key']: [r[0] for r in row_slots[cat['index']]] for cat in categories},
        'category_pages': {cat['key']: dict(index=cat['index'], page_offset=cat['page_offset'], root_row=cat['root_row'],
                                            title_slot=cat['title_slot'], title=cat['title'], members=list(cat['members']))
                           for cat in categories},
        # Rows to activate from the root to reach each page: one (page, row, control id) per step.
        'menu_paths': menu_paths(group, categories, category_ids, by_key) if grouped else {},
        'status_page': (dict(index=STATUS_INDEX, page_offset=status['page_offset'], root_row=status['root_row'],
                             flag_offset=status['flag_offset'], rows=list(status['system'] + status['members']),
                             control_ids=list(status['ids']), title_slot=STATUS_TITLE_SLOT,
                             root_value_slot=STATUS_ROOT_VALUE_SLOT, label_slots=STATUS_LABEL_SLOTS,
                             value_slots=STATUS_VALUE_SLOTS, build_tag=build_tag, card=card,
                             features=status['features'], resident_id=hex(resident_id) if card and resident_id is not None else None)
                        if status else None),
        'text_slots': {str(index): dict(title=title, label=label, value=value)
                       for index, _, label, value, title, _ in page_specs},
        'entry_slot': ENTRY_SLOT, 'root_label_slots': group['labels'] if grouped else {},
        'native_rows': {menu['name']: dict(object=hex(menu['object']), first_row=menu['first_row'],
                                           rows=[dict(key=key, row=menu['first_row']+i, control_id=menu['ids'][i],
                                                      label_slot=by_key[key]['root_slot'], page_index=by_key[key]['index'],
                                                      direct=hex(f[f'direct_{key}'] | 1))
                                                 for i, key in enumerate(menu['keys'])])
                        for menu in native},
        'control_identifiers': {str(index): list(identifiers) for index, _, _, _, _, identifiers in page_specs},
        'setup_entry_id': hex(ENTRY_ID), 'settings_count': 39,
        'setup_entry_controls': {'ascii': hex(controls_address), 'utf16': hex(unicode_controls)},
        'setup_entry_prefetch_refresh': True,
        'error_ui': 'Native dialog body and OK control; stable menu titles',
        'ui_languages': {'26': 'zh-Hans', '27': 'zh-Hant', 'other': 'en'},
        'ui_text_api': {'ascii': '0x10454efe', 'utf16': '0x10454f5a', 'language_key': '0x1400',
                        'ascii_widget_kind': 2, 'utf16_widget_kind': 3, 'unicode_style': 120,
                        'chinese_fonts': {'26': 2, '27': 0}, 'chinese_module_output_limit': 24},
        'selection_mark': '*', 'native_back_icon_preserved': True, 'no_status_ui': True,
    }
    if menu_tab:
        from . import tab
        tab.add(plan, f['open'])
    plan.features['menu_framework'] = report
    if sound:
        report.update(shutter_sound.report(grouped))
        plan.features['shutter_sound'] = report
    return report
