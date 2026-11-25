import re
import unittest
from pathlib import Path

from z30_extensions.host import film_grain as host

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Z_30_0120.bin'


class HostReferenceTest(unittest.TestCase):
    def test_steps_match_the_z5_mid_tone_table(self):
        # Z5 FILM_GRAIN.md section 3: steps at Y=128 for strengths 1..6. Its HP5 row says 17 for
        # strength 5; the Z5 source formula (jpeg_a43.inc) gives 187 * 6 + 32 >> 6 = 18, kept here.
        self.assertEqual([host.steps(host.STYLE_FUJI400, s, False)[128] for s in range(1, 7)], [6, 9, 11, 14, 17, 20])
        self.assertEqual([host.steps(host.STYLE_HP5, s, False)[128] for s in range(1, 7)], [6, 9, 12, 15, 18, 20])
        for style in (host.STYLE_FUJI400, host.STYLE_HP5):
            for strength in range(1, 7):
                main, preview = host.steps(style, strength, False), host.steps(style, strength, True)
                self.assertEqual((main[0], max(main)), (0, max(main)))
                self.assertLessEqual(max(main), 20)
                self.assertTrue(all(p <= m for p, m in zip(preview, main)))

    def test_field_levels_and_grain_rms(self):
        tiles = host.field(host.STYLE_FUJI400)
        self.assertEqual(len(tiles), host.FIELD_BYTES)
        self.assertTrue(1 <= min(tiles) and max(tiles) <= 63)
        self.assertAlmostEqual(sum(tiles) / len(tiles), 32, delta=0.5)
        # Z5: FUJI400 strength 3 RMS about 3.1 at Y=32 and 6.7 at Y=128.
        self.assertAlmostEqual(host.rms(host.STYLE_FUJI400, 3, 32), 3.1, delta=0.4)
        self.assertAlmostEqual(host.rms(host.STYLE_FUJI400, 3, 128), 6.7, delta=0.5)

    def test_word_arithmetic_matches_the_packed_form(self):
        # The payload's even/odd lane form: ((g * step + 8) >> 4) - 2 * step per byte, then signed saturation.
        table = host.lut(host.STYLE_HP5, 6, False)
        for v, n in ((0x00000000, 0x3F3F0101), (0xFFFFFFFF, 0x01013F3F), (0x80402010, 0x20212223),
                     (0x7F7F7F7F, 0x3F013F01), (0x01FE7F80, 0x00203F10)):
            step = table[sum((v >> s) & 255 for s in (0, 8, 16, 24))]
            even = (((n & 0x00FF00FF) * step + 0x00080008) >> 4) & 0x00FF00FF
            odd = ((((n >> 8) & 0x00FF00FF) * step + 0x00080008) << 4) & 0xFF00FF00
            packed = even | odd
            out = 0
            for s in (0, 8, 16, 24):
                delta = ((((packed >> s) & 255) - 2 * step) + 128) % 256 - 128
                out |= max(0, min(255, ((v >> s) & 255) + delta)) << s
            self.assertEqual(out, host.grain_word(v, n, table), hex(v))

    def test_strength_order_starts_at_the_default(self):
        self.assertEqual(host.STRENGTH_ORDER, (3, 4, 5, 6, 1, 2))
        self.assertEqual(len(host.data_blob()), host.DATA_BYTES)
        self.assertEqual(host.lut_offset(host.STYLE_HP5, 6, True) + host.LUT_BYTES, host.DATA_BYTES)


class PayloadTest(unittest.TestCase):
    def test_payload_call_sites_and_state_layout_match_python(self):
        from z30_extensions.features.photo import date_stamp, film_grain_ui
        from z30_extensions.features.photo.film_grain_payload import CODE, SHA256, SIZE
        import hashlib
        self.assertEqual((len(CODE), hashlib.sha256(CODE).hexdigest()), (SIZE, SHA256))
        source = (ROOT / 'modules' / 'film_grain' / 'film_grain.c').read_text()
        sites = [int(v, 16) - 5 for v in re.findall(r'#define SITE_\w+\s+0x([0-9A-F]+)u', source)]
        self.assertEqual(tuple(sites), date_stamp.CALL_SITES)
        layout = dict(re.findall(r'ST_(\w+) = (\d+)', source))
        self.assertEqual(int(layout['STRENGTH']) * 4, film_grain_ui.STRENGTH_FIELD)
        self.assertEqual(int(layout['TEXT']) * 4, film_grain_ui.TEXT_FIELD)
        # The payload owns the first ST_WORDS; Write to NEF and its record follow.
        self.assertEqual(int(layout['WORDS']) * 4, film_grain_ui.NEF_FIELD)
        self.assertEqual(film_grain_ui.CRD_LEFT + 4, film_grain_ui.STATE_BYTES)

    def test_lightroom_grain_matches_the_z5_calibration(self):
        from z30_extensions.features.photo.film_grain import lightroom_grain
        # Z5 FILM_GRAIN.md section 5: FUJI400 16..56 / 40 / 65, HP5 19..65 / 33 / 65.
        self.assertEqual([lightroom_grain(host.STYLE_FUJI400, n)[0] for n in range(1, 7)], [16, 24, 32, 40, 48, 56])
        self.assertEqual([lightroom_grain(host.STYLE_HP5, n)[0] for n in range(1, 7)], [19, 28, 37, 47, 56, 65])
        self.assertEqual(lightroom_grain(host.STYLE_FUJI400, 6)[1:], (40, 65))
        self.assertEqual(lightroom_grain(host.STYLE_HP5, 1)[1:], (33, 65))
        with self.assertRaises(ValueError):
            lightroom_grain(host.STYLE_OFF, 3)


@unittest.skipUnless(SOURCE.is_file(), 'stock firmware not present')
class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        cls.stock = extract_eg(SOURCE.read_bytes())

    def test_in_full(self):
        from z30_extensions.build import FULL_FEATURES, resolve_presets
        self.assertIn('film-grain', FULL_FEATURES)
        self.assertEqual(resolve_presets('full')[1], resolve_presets(['full', 'film-grain'])[1])

    def test_alone_installs_its_gate(self):
        from z30_extensions.assembly import jump
        from z30_extensions.build import build
        from z30_extensions.features.photo import date_stamp, film_grain, film_grain_ui
        image, report = build(self.stock, 'film-grain')
        grain = report['features']['film_grain']
        self.assertEqual(grain['gate'], hex(date_stamp.ENCODE_GATEWAY))
        entry = int(grain['gate_entry'], 16) & ~1
        start = date_stamp.ENCODE_GATEWAY - 0x10020000
        self.assertEqual(image[start:start + 10], jump(date_stamp.ENCODE_GATEWAY, entry, 10))
        crd = grain['nef_crd']
        self.assertEqual(crd['site'], hex(film_grain.CRD_SITE))
        start = film_grain.CRD_SITE - 0x10020000
        self.assertEqual(image[start:start + 8], jump(film_grain.CRD_SITE, int(crd['gate'], 16) & ~1, 8))
        framework = report['features']['menu_framework']
        self.assertIn(str(film_grain_ui.PAGE_INDEX), framework['page_offsets'])
        self.assertIn('grain', [row['key'] for row in framework['menu_categories']])

    def test_with_date_stamp_shares_the_gate(self):
        from z30_extensions.build import build
        _, report = build(self.stock, ['date-stamp', 'film-grain'])
        self.assertEqual(report['features']['film_grain']['gate'], 'shared with date-stamp (grain first)')
        self.assertTrue(report['features']['date_stamp']['film_grain_first'])
        # One category in the build: the root lists its members, the grain page after the date stamp.
        rows = [row['key'] for row in report['features']['menu_framework']['menu_categories']]
        self.assertEqual(rows, ['status', 'stamp', 'grain'])
        _, report = build(self.stock, ['full', 'film-grain'])
        photo = report['features']['menu_framework']['category_pages']['photo']['members']
        self.assertEqual(photo, ['aspect', 'zebra', 'stamp', 'grain', 'nef'])
        # The crd block end is the aspect crop's site; the aspect gate calls the grain hook first.
        crd = report['features']['film_grain']['nef_crd']
        self.assertTrue(crd['gate'].startswith('shared with aspect-profiles'))
        self.assertEqual(crd['hook'], report['features']['menu_framework']['functions']['grain_crd'])

    def test_flash_fonts_gain_the_title_only_with_the_page(self):
        from z30_extensions.build import build
        _, plain = build(self.stock, 'date-stamp')
        _, grain = build(self.stock, ['date-stamp', 'film-grain'])
        before, after = plain['features']['cjk_fonts']['added_glyphs'], grain['features']['cjk_fonts']['added_glyphs']
        self.assertEqual(sorted(set(after['2']) - set(before['2'])), sorted('胶颗粒'))
        self.assertEqual(sorted(set(after['0']) - set(before['0'])), sorted('膠顆粒'))

    def test_card_title_uses_resident_characters(self):
        from z30_extensions.features.photo.film_grain_ui import TITLE
        from z30_extensions.platform.fonts import HANS_FONT, HANT_FONT, extra_glyphs, missing_glyphs
        self.assertEqual(missing_glyphs(TITLE[0], HANS_FONT, extended=False), '胶颗粒')
        self.assertTrue(all(ch in extra_glyphs(HANS_FONT) for ch in '胶颗粒'))
        self.assertTrue(all(ch in extra_glyphs(HANT_FONT) for ch in '膠顆粒'))
        # The flash-build labels need no glyph beyond the stock fonts.
        for text in ('风格强度', '風格強度'):
            font = HANS_FONT if '风' in text else HANT_FONT
            self.assertEqual(missing_glyphs(text, font, extended=False), '')


if __name__ == '__main__':
    unittest.main()
