# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""The extension menu as an eighth tab of the stock menu tab bar (build option menu_tab).

The tab bar is stock page 1 (object 0xC1832D50); its seven tabs are the seven
controls of that page, described by RAM metadata at 0xC1872230 (scatter). The
page gets an eighth control, an event table whose 0x256 entry opens the
extension root page, 52 px rows so eight tabs fit above the help icon, and an
eighth entry in the tab -> page table. The menu-open path realizes the tab bar
from this metadata on every MENU press, so card builds (loaded ~3 s after
power-on) show the tab from the next press.

The icon is our own (tools/build_tab_icon.py, five 64 x 60 PNGs, one per tab
state): its images are served as new IDs by a hook on the only image-table
reader, and both readers of the stock icon-set table use a copy with a tenth set.

On menu exit the stock code stores the tab bar row as property 0x3D07 and reads
it back without a range check, so the store is hooked: a row >= 7 is saved as
the setup tab (4), and a camera without the extension never reads 7.
The framework gives the root page the top-level layout (no back icon) and drops
the setup-menu row. See docs/menu-framework.md and docs/menu-tab-research.md.
"""
import struct
from importlib.resources import files

from ..assembly import assemble, jump
from .sound_layout import PAGE_INDEX as ROOT_PAGE

TAB_META = 0xC1872230           # RAM: flags, geometry, layout, controls, dispatch, rows, extra
TABS = 7
TAB_ROW = 52                    # 8 x 52 = 416 px, above the help icon at 420
CONTROL_ID = 0x14CF             # free control ID next to the stock tab IDs 0x14C3..0x14C9
ICON = 0x1B6                    # control +0 (the firmware draws the icon set of control +20 instead)
ICON_SETS = 0x10734DB4          # 9 x 6 image IDs, one set per tab (index = control +20)
ICON_SET_LITERALS = (0x1045E958,  # tab widget build (0x1045E578)
                     0x1045F2B0)  # tab refresh (0x1045F19C): My menu, and every tab's
                                  # normal image when the right-hand page is not a stock tab page
ICON_SET_COUNT = 9
ICON_SET = ICON_SET_COUNT       # a tenth set, appended
RESOURCES = 0xC1810194          # image resource globals; +0x28 = image table (20-byte entries)
IMAGE_LOOKUP = 0x104ED4E2       # the only reader of the image table
IMAGE_LOOKUP_END = 0x104ED52E
IMAGE_CLAMP = 0x104ED418        # stock ID check, called first
IMAGE_TABLE = 0x10E41BFC
FIRST_IMAGE = 0x15E6            # first ID after the stock images (0..0x15E5)
PNG_SIGNATURE = bytes.fromhex('89504e470d0a1a0a')
STATES = 5                      # focused, disabled, selected, current, normal
STRIP_IMAGE = FIRST_IMAGE + STATES   # the teal strip beside the tab bar
IMAGES = STATES + 1
STRIPS = 0x10734D74             # 8 x (strip, separator) image IDs, indexed by tab bar row
STRIP_LITERAL = 0x1045DAEC      # its only reference (0x1045D79A)
SETUP_TAB = 4
TEMPLATE_TAB = SETUP_TAB        # event table copied from the setup tab
PAGE_TABLE_LITERAL = 0x10428A40  # literal of the tab -> page halfword table (0x1074BFAC)
STORE_SITE = 0x1042898A         # movw r0,#0x3d07; bl 0x10465990; movs r1,#0x12
STORE_STOCK = bytes.fromhex('43f607503cf0ffff1221')
STORE_RESUME = 0x10428994
PROPERTY_SET = 0x10465990


def icon_files():
    """The five tab icon PNGs drawn by tools/build_tab_icon.py, in state order."""
    root = files('z30_extensions.platform')
    return [root.joinpath(f'menu_tab_icon_{n}.png').read_bytes() for n in range(STATES)]


def strip_file():
    return files('z30_extensions.platform').joinpath('menu_tab_strip.png').read_bytes()


def add_icon(plan):
    """Serve the tab icon as images FIRST_IMAGE.. and give it icon set ICON_SET.

    The image table itself is in read-only flash; its only reader is hooked so
    IDs from FIRST_IMAGE come from a small table of ours. The tab widget build
    and the tab refresh both index the icon sets by control +20; both literals
    move to a copy with a tenth set. Slots 4 and 5 are both the normal image
    (refresh uses slot 4 while a stock tab page is shown, slot 5 otherwise).
    Returns the icon set index.
    """
    im = plan.image
    last = im.read(IMAGE_TABLE + (FIRST_IMAGE - 1) * 20, 20)
    if im.read(struct.unpack_from('<I', last, 16)[0], 8) != PNG_SIGNATURE:
        raise ValueError('Unexpected stock image table')
    if any(im.words(literal, 1)[0] != ICON_SETS for literal in ICON_SET_LITERALS):
        raise ValueError('Unexpected stock tab icon set literal')
    entries = b''
    for data in icon_files() + [strip_file()]:
        entries += struct.pack('<4xI8xI', len(data), plan.alloc(data))
    images = plan.alloc(entries)
    stub = plan.alloc(bytes(96))
    plan.put(stub, assemble(f'''
        push {{r4,r5,r6,lr}}; mov r4,r1; mov r5,r0; movs r1,#0;
        ldr.w r12,={IMAGE_CLAMP | 1}; blx r12;
        ldr r1,[r5]; ldr r2,={RESOURCES}; ldr r2,[r2,#0x28];
        movw r3,#{FIRST_IMAGE}; subs r0,r1,r3; blo lookup; cmp r0,#{IMAGES}; bhs lookup;
        mov r1,r0; ldr r2,={images};
        lookup: add.w r1,r1,r1,lsl #2; add.w r2,r2,r1,lsl #2;
        ldrb r1,[r2]; strb r1,[r4]; ldrb r1,[r2,#1]; strb r1,[r4,#1];
        ldr r1,[r2,#4]; str r1,[r4,#4]; ldr r1,[r2,#16]; str r1,[r4,#16];
        pop {{r4,r5,r6,pc}};
    ''', stub, 96))
    stock_lookup = im.read(IMAGE_LOOKUP, 10)
    plan.edit(IMAGE_LOOKUP, jump(IMAGE_LOOKUP, stub, 10), 'Menu tab: image lookup with the tab icon',
              expected=stock_lookup)
    # Icon sets: slots are states 0, 1, 2, 3, 4, 4 (as the stock sets).
    ids = [FIRST_IMAGE + n for n in range(STATES)] + [FIRST_IMAGE + STATES - 1]
    new_sets = plan.alloc(im.read(ICON_SETS, ICON_SET_COUNT * 24) + struct.pack('<6I', *ids))
    for literal in ICON_SET_LITERALS:
        plan.edit(literal, struct.pack('<I', new_sets), 'Menu tab: icon sets with the tab icon',
                  struct.pack('<I', ICON_SETS))
    # Strip: row 7 of the stock table is a spare light grey one; ours is teal.
    if im.words(STRIP_LITERAL, 1)[0] != STRIPS:
        raise ValueError('Unexpected stock tab strip literal')
    strips = bytearray(im.read(STRIPS, 8 * 8))
    struct.pack_into('<I', strips, TABS * 8, STRIP_IMAGE)
    new_strips = plan.alloc(bytes(strips))
    plan.edit(STRIP_LITERAL, struct.pack('<I', new_strips), 'Menu tab: strip beside the tab bar',
              struct.pack('<I', STRIPS))
    return ICON_SET, dict(strips=hex(new_strips), strip_image=hex(STRIP_IMAGE), images=hex(images), first_image=hex(FIRST_IMAGE), image_lookup=hex(stub),
                          icon_sets=hex(new_sets), icon_set=ICON_SET)


def add(plan, open_function):
    """open_function: the framework's root `open` (menu_ui), used as the key handler."""
    im = plan.image
    flags, geometry, layout, controls, dispatch, rows, extra = im.words(TAB_META, 7)
    if rows != (TABS << 16) | TABS:
        raise ValueError('Unexpected stock tab bar row count')
    if im.read(STORE_SITE, len(STORE_STOCK)) != STORE_STOCK:
        raise ValueError('Unexpected stock last-tab store')
    icon_set, icon_report = add_icon(plan)
    # Controls: the seven stock tabs plus ours (no alternates; +20 picks the icon set).
    stock_controls = im.read(controls, TABS * 28)
    ours = bytearray(stock_controls[SETUP_TAB * 28:(SETUP_TAB + 1) * 28])
    struct.pack_into('<3I', ours, 0, ICON, 0xFFFF, 0xFFFF)
    struct.pack_into('<I', ours, 20, icon_set)
    struct.pack_into('<I', ours, 24, CONTROL_ID)
    new_controls = plan.alloc(stock_controls + bytes(ours))
    # Geometry: eight 64 x 52 boxes; the stock label rectangles are unused (-1).
    count, boxes, labels, values = im.words(geometry, 4)
    if count != TABS:
        raise ValueError('Unexpected stock tab bar geometry')
    rects = [plan.alloc(struct.pack('<32h', *[v for i in range(8) for v in
                                              (0, i * TAB_ROW, 64, (i + 1) * TAB_ROW)]))
             for _ in ('boxes', 'values')]
    label_rects = plan.alloc(struct.pack('<32h', *([-1] * 32)))
    new_geometry = plan.alloc(struct.pack('<4I', 8, rects[0], label_rects, rects[1]))
    # Events: a copy of the setup tab's table; every event that enters the setup
    # page (right 0x256, OK 0x25B, 0x26F, 0x27E; keys from the table at
    # 0x1073B400) enters the extension root instead. The stock navigation cannot
    # enter page 955 (it leaves the focus on the tab bar), so these call the
    # framework's open, as the setup-menu row did, and stay (0x3BB). Touching a
    # tab is handled elsewhere and needs no event here.
    template = im.words(dispatch + TEMPLATE_TAB * 4, 1)[0]
    template_page = struct.unpack('<8H', im.read(im.words(PAGE_TABLE_LITERAL, 1)[0], 16))[TEMPLATE_TAB]
    words = []
    redirected = []
    for n in range(200):
        event, fn, predicate, destination = im.words(template + n * 16, 4)
        if destination == template_page:
            redirected.append(event)
            fn, predicate, destination = open_function | 1, 0, 0x3BB
        words.extend((event, fn, predicate, destination))
        if event == 0x31E:
            break
    else:
        raise ValueError('Missing tab event-map terminator')
    if sorted(redirected) != [0x256, 0x25B, 0x26F, 0x27E]:
        raise ValueError('Unexpected stock setup tab events')
    events = plan.alloc(struct.pack(f'<{len(words)}I', *words))
    new_dispatch = plan.alloc(struct.pack(f'<{TABS}I', *im.words(dispatch, TABS)) + struct.pack('<I', events))
    # Tab -> page table used when the menu opens on the stored tab.
    page_table = im.words(PAGE_TABLE_LITERAL, 1)[0]
    stock_pages = struct.unpack('<8H', im.read(page_table, 16))
    if stock_pages[TABS] != 0:
        raise ValueError('Unexpected stock tab page table')
    new_pages = plan.alloc(struct.pack('<8H', *stock_pages[:TABS], ROOT_PAGE))
    plan.edit(PAGE_TABLE_LITERAL, struct.pack('<I', new_pages), 'Menu tab: page table with the extension tab',
              struct.pack('<I', page_table))
    # The menu-open path realizes the tab bar from this metadata on every MENU
    # press, so a card loaded later (even with the menu open) takes effect on
    # the next press. Tables first, the row count last: a realize between two
    # writes then sees new tables with the old count, never the reverse.
    for address, value in ((TAB_META + 4, new_geometry), (TAB_META + 12, new_controls),
                           (TAB_META + 16, new_dispatch), (TAB_META + 20, (8 << 16) | 8)):
        plan.edit(address, struct.pack('<I', value), 'Menu tab: eight tabs')
    # Never store the extension tab as the last tab: a stock camera would read 7.
    stub = plan.alloc(bytes(48))
    plan.put(stub, assemble(f'''
        cmp r1,#{TABS}; blo store; movs r1,#{SETUP_TAB};
        store: movw r0,#0x3d07; ldr.w r12,={PROPERTY_SET | 1}; blx r12;
        movs r1,#0x12; ldr.w pc,={STORE_RESUME | 1};
    ''', stub, 48))
    plan.edit(STORE_SITE, jump(STORE_SITE, stub, len(STORE_STOCK)), 'Menu tab: last-tab store clamp',
              expected=STORE_STOCK)
    plan.features['menu_tab'] = dict(
        tab_index=TABS, root_events=[hex(e) for e in redirected], root_page=ROOT_PAGE, control_id=hex(CONTROL_ID), icon=hex(ICON),
        row_height=TAB_ROW, controls=hex(new_controls), geometry=hex(new_geometry),
        dispatch=hex(new_dispatch), events=hex(events), page_table=hex(new_pages), store_clamp=hex(stub),
        last_tab_property='0x3d07', **icon_report)
