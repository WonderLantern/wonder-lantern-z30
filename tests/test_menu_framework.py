"""The extension menu framework works without the shutter sound."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Z_30_0120.bin'
try:
    import keystone  # noqa: F401
except ImportError:  # optional build dependency
    keystone = None


class RootLayoutTests(unittest.TestCase):
    def layout(self, *present):
        from z30_extensions.features.menu.framework import root_layout
        rows, categories = root_layout(set(present))
        return rows, {cat['key']: list(cat['members']) for cat in categories}

    def test_full_root_is_categories_and_the_sound(self):
        rows, categories = self.layout('aspect', 'zebra', 'stamp', 'nef', 'portrait', 'bitrate', 'gyro',
                                       'trk3d', 'afcol', 'sound')
        self.assertEqual(rows, ['photo', 'focus', 'video', 'sound'])
        self.assertEqual(categories, {'photo': ['aspect', 'zebra', 'stamp', 'nef'],
                                      'focus': ['trk3d', 'afcol', 'portrait'], 'video': ['bitrate', 'gyro']})

    def test_a_category_with_one_member_puts_it_on_the_root(self):
        self.assertEqual(self.layout('aspect', 'zebra', 'bitrate'), (['photo', 'bitrate'], {'photo': ['aspect', 'zebra']}))
        self.assertEqual(self.layout('zebra', 'modules'), (['zebra', 'modules'], {}))

    def test_a_single_category_root_lists_its_members(self):
        self.assertEqual(self.layout('aspect', 'zebra'), (['aspect', 'zebra'], {}))
        self.assertEqual(self.layout('zebra', 'sound'), (['zebra', 'sound'], {}))

    def test_category_pages_and_title_slots_are_fixed(self):
        from z30_extensions.features.menu.framework import CATEGORY_PAGES, CATEGORY_TITLE_SLOTS
        from z30_extensions.features.menu.slots import BLOCKS
        self.assertEqual(CATEGORY_PAGES, {'photo': 970, 'focus': 971, 'video': 972, 'lab': 973})
        base, size = BLOCKS['category']
        self.assertEqual(sorted(CATEGORY_TITLE_SLOTS.values()), list(range(base, base + size)))


@unittest.skipUnless(keystone and SOURCE.exists(), 'needs keystone and Z_30_0120.bin')
class MenuFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        cls.stock = extract_eg(SOURCE.read_bytes())

    def build(self, preset):
        from z30_extensions.build import build
        return build(self.stock, preset)[1]

    def test_menu_feature_alone_has_no_sound(self):
        from z30_extensions.features.menu.layout import NEW_SIZE, POPUP_INDEX, FILES_INDEX
        report = self.build('photo-zebra')
        features = report['features']
        self.assertNotIn('shutter_sound', features)
        menu = features['menu_framework']
        self.assertFalse(menu['shutter_sound'])
        # No sound functions, pages or hooks; no sound buffers in the state block.
        for name in ('load', 'trigger', 'shot', 'config', 'data', 'save_settings'):
            self.assertNotIn(name, menu['functions'])
        self.assertNotIn(str(FILES_INDEX), menu['pages'])
        self.assertIn(str(POPUP_INDEX), menu['pages'])
        self.assertLess(menu['persistent_state_size'], 4096)
        self.assertLess(menu['persistent_state_size'], NEW_SIZE)
        self.assertEqual([c['key'] for c in menu['menu_categories']], ['status', 'zebra'])
        names = [e['name'] for e in report['edits']]
        self.assertFalse(any(n.startswith('Sound hook') for n in names))
        self.assertEqual(sum(n.startswith('Menu hook') for n in names), 3)

    def test_root_lists_categories_and_single_pages(self):
        menu = self.build(['photo-zebra', 'bitrate-slowmo-uncap', 'aspect-profiles'])['features']['menu_framework']
        self.assertEqual([c['key'] for c in menu['menu_categories']], ['status', 'photo', 'bitrate'])
        self.assertEqual(menu['category_pages']['photo']['members'], ['aspect', 'zebra'])
        self.assertEqual(menu['menu_paths']['bitrate'], [dict(page=955, row=2, control_id=menu['control_identifiers']['955'][2],
                                                              opens=960)])
        self.assertEqual(sorted(menu['native_rows']), ['movie', 'photo'])

    def test_full_groups_every_page_into_categories(self):
        from z30_extensions.features.menu.layout import MAX_ROWS
        menu = self.build('full')['features']['menu_framework']
        self.assertEqual([c['key'] for c in menu['menu_categories']], ['status', 'photo', 'focus', 'video', 'sound'])
        self.assertEqual([c['page_index'] for c in menu['menu_categories']], [974, 970, 971, 972, 962])
        # Every feature page is reachable, the photo-menu-only pages included.
        self.assertEqual(sorted(menu['menu_paths']), sorted(['aspect', 'zebra', 'stamp', 'nef', 'portrait', 'bitrate',
                                                             'gyro', 'trk3d', 'afcol', 'sound', 'status']))
        for key, path in menu['menu_paths'].items():
            self.assertEqual(path[0]['page'], 955)
            for step, following in zip(path, path[1:]):
                self.assertEqual(step['opens'], following['page'])
        for index, page in menu['pages'].items():
            self.assertLessEqual(page['count'], MAX_ROWS, index)
        root = menu['pages']['955']
        self.assertEqual(root['count'], 5)
        # Category rows run the label to the right edge; the status and sound rows keep a value column.
        self.assertEqual([r[0] == r[2] for r in root['value_rectangles']], [False, True, True, True, False])
        photo = menu['pages']['970']
        self.assertEqual(photo['count'], 4)
        self.assertTrue(photo['back_icon'])
        self.assertEqual(photo['title_index'], menu['category_pages']['photo']['title_slot'])
        self.assertTrue(all(r[0] < r[2] for r in photo['value_rectangles']))
        self.assertEqual(len(set(menu['page_offsets'].values())), len(menu['page_offsets']))

    def test_sound_alone_and_with_pages(self):
        alone = self.build('shutter-sound')['features']
        self.assertIs(alone['shutter_sound'], alone['menu_framework'])
        self.assertEqual(alone['menu_framework']['menu_categories'], [])
        grouped = self.build(['shutter-sound', 'photo-zebra'])['features']['menu_framework']
        self.assertEqual([c['key'] for c in grouped['menu_categories']], ['status', 'zebra', 'sound'])

    def test_status_page_is_the_first_root_row(self):
        from z30_extensions.features.menu.framework import STATUS_INDEX, STATUS_SLOTS
        from z30_extensions.features.menu.slots import STOCK_SLOTS
        menu = self.build('full')['features']['menu_framework']
        status = menu['status_page']
        self.assertEqual(menu['menu_categories'][0], dict(key='status', row=0, control_id=menu['menu_categories'][0]['control_id'],
                                                          page_index=STATUS_INDEX))
        # Information only: four rows, none opens a page. Flash builds: the build tag defaults to 'dev'.
        self.assertEqual(status['rows'], ['card', 'version', 'features', 'settings'])
        self.assertEqual((status['card'], status['build_tag'], status['features']), (False, 'dev', 16))
        self.assertEqual(menu['pages'][str(STATUS_INDEX)]['count'], 4)
        self.assertFalse(any(step['page'] == STATUS_INDEX for path in menu['menu_paths'].values() for step in path))
        self.assertEqual(len(set(STATUS_SLOTS)), 10)
        self.assertFalse(set(STATUS_SLOTS) & STOCK_SLOTS)
        self.assertEqual(menu['menu_paths']['status'][0]['opens'], STATUS_INDEX)
        zebra = self.build('photo-zebra')['features']['menu_framework']['status_page']
        self.assertEqual((zebra['rows'], zebra['features']), (['card', 'version', 'features', 'settings'], 1))
        self.assertIsNone(self.build('shutter-sound')['features']['menu_framework']['status_page'])

    def test_labels_add_only_the_known_extra_glyphs(self):
        # Pulling one more extra glyph (驻, 2026-11-25) made the camera font engine drop
        # the stock 画 in the Hans font. New labels must use stock characters, or be checked
        # on every page in QEMU and on the camera (docs/menu-layout.md).
        fonts = self.build('full')['features']['cjk_fonts']['added_glyphs']
        self.assertEqual({font: len(chars) for font, chars in fonts.items()}, {'0': 34, '2': 32})

    def test_menu_tab_is_opt_in(self):
        from z30_extensions.build import build
        plain = build(self.stock, 'photo-zebra')[1]
        self.assertNotIn('menu_tab', plain['features'])
        self.assertTrue(plain['features']['menu_framework']['pages']['955']['back_icon'])
        report = build(self.stock, 'photo-zebra', menu_tab=True)[1]
        tab = report['features']['menu_tab']
        self.assertEqual((tab['tab_index'], tab['root_page'], tab['last_tab_property']), (7, 955, '0x3d07'))
        self.assertEqual((tab['icon_set'], tab['first_image']), (9, '0x15e6'))
        self.assertEqual(tab['root_events'], ['0x256', '0x25b', '0x26f', '0x27e'])
        names = [e['name'] for e in report['edits']]
        tab_names = [n for n in names if n.startswith('Menu tab')]
        self.assertEqual(len(tab_names), 10)
        for name in ('Menu tab: last-tab store clamp', 'Menu tab: image lookup with the tab icon',
                     'Menu tab: icon sets with the tab icon', 'Menu tab: strip beside the tab bar'):
            self.assertIn(name, tab_names)
        # The tab replaces the setup-menu row; the root is a top-level page.
        self.assertNotIn('Settings row count with the extension entry', names)
        self.assertIn('Settings row count with the extension entry', [e['name'] for e in plain['edits']])
        self.assertFalse(report['features']['menu_framework']['pages']['955']['back_icon'])

    def test_menu_tab_always_has_the_status_page(self):
        # A tab build brings the framework even without feature pages (GUI kits always
        # have the tab): the root holds the status page, plus the sound group with sound.
        from z30_extensions.build import build
        self.assertNotIn('menu_framework', build(self.stock, 'focal-length')[1]['features'])
        for preset, rows in (('focal-length', ['status']), (['liveview-dual', 'focal-length'], ['status']),
                             ('shutter-sound', ['status', 'sound'])):
            features = build(self.stock, preset, menu_tab=True)[1]['features']
            self.assertIn('menu_tab', features)
            menu = features['menu_framework']
            self.assertEqual([row['key'] for row in menu['menu_categories']], rows)
            self.assertEqual(menu['status_page']['rows'], ['card', 'version', 'features', 'settings'])

    def test_menu_tab_icons(self):
        from z30_extensions.features.menu import tab as menu_tab
        icons = menu_tab.icon_files()
        self.assertEqual(len(icons), menu_tab.STATES)
        for data in icons:
            self.assertEqual(data[:8], menu_tab.PNG_SIGNATURE)
            self.assertEqual(data[16:24], bytes.fromhex('00000040 0000003c'))   # 64 x 60
        strip = menu_tab.strip_file()
        self.assertEqual(strip[16:26], bytes.fromhex('00000003 000001e0 0103'))   # 3 x 480, 1-bit palette

    def test_framework_needs_a_page_or_the_sound(self):
        from z30_extensions.features.menu.framework import add
        from z30_extensions.plan import Plan
        with self.assertRaises(ValueError):
            add(Plan(self.stock), sound=False)


if __name__ == '__main__':
    unittest.main()
