import struct
import unittest
from pathlib import Path

from z30_extensions.platform.fonts import (FONT_TABLE, FONT_TABLE_LITERAL, HANS_FONT, HANT_FONT,
                                           coverage, font_ranges, missing_glyphs)
from z30_extensions.features.ui_text import TRANSLATIONS, TextResources, check_glyphs

FIRMWARE = Path(__file__).resolve().parents[1] / 'Z_30_0120.bin'


class SyntheticImage:
    def __init__(self, entries, sections=3):
        header = bytearray(b'hif\x01\x00\x02\x06\x00') + bytes(0x38)
        header += struct.pack('<HI', sections, 5 * (sections - 1) + 6) + bytes(5 * (sections - 1))
        header += struct.pack('<H', len(entries)) + b''.join(struct.pack('<3H', *e) for e in entries)
        self.memory = {FONT_TABLE_LITERAL: struct.pack('<I', FONT_TABLE),
                       FONT_TABLE + HANS_FONT * 4: struct.pack('<I', 0x10800001),
                       0x10800001: bytes(header) + bytes(0x80)}

    def read(self, address, size):
        for start, raw in self.memory.items():
            if start <= address and address + size <= start + len(raw):
                return raw[address - start:address - start + size]
        raise ValueError(hex(address))

    def words(self, address, count):
        return struct.unpack(f'<{count}I', self.read(address, count * 4))


class FontCoverageTests(unittest.TestCase):
    def test_character_map_runs(self):
        image = SyntheticImage([(0, 0, 0), (0x20, 2, 4), (0x41, 9, 16), (0x8bbe, 40, 1)])
        self.assertEqual(font_ranges(image, HANS_FONT), [(0x20, 4), (0x41, 16), (0x8bbe, 1)])
        for entries in ([(0x41, 1, 2), (0x42, 3, 1)], [(0x41, 1, 1), (0x30, 2, 1)]):
            with self.assertRaisesRegex(ValueError, 'not sorted'):
                font_ranges(SyntheticImage(entries), HANS_FONT)
        with self.assertRaisesRegex(ValueError, 'descriptors'):
            font_ranges(SyntheticImage([(0x41, 1, 1)], sections=1), HANS_FONT)

    def test_known_stock_gaps(self):
        # Gaps found earlier by QEMU screenshots, now read from the stock map.
        self.assertEqual(missing_glyphs('橙橘晕厂扩展听块', HANS_FONT), '橙橘晕厂扩展听块')
        self.assertEqual(missing_glyphs('橙橘找', HANT_FONT), '橙橘找')
        self.assertEqual(missing_glyphs('设置默认柔光原始', HANS_FONT), '')
        self.assertEqual(missing_glyphs('預設光暈', HANT_FONT), '')
        self.assertEqual(missing_glyphs('Quick', HANS_FONT), 'Q')
        self.assertGreater(len(coverage(HANS_FONT)), 700)

    def test_every_translation_has_stock_glyphs(self):
        for english, (hans, hant) in TRANSLATIONS.items():
            with self.subTest(english=english):
                check_glyphs(english, hans, hant)

    def test_build_rejects_missing_glyph(self):
        class Plan:
            def alloc(self, raw):
                return 0
        with self.assertRaisesRegex(ValueError, '橙'):
            check_glyphs('Orange', '橙色', '橙色')
        # Untranslated labels reuse the English text in the CJK fonts.
        with self.assertRaisesRegex(ValueError, 'Q'):
            TextResources(Plan()).alloc('Quit')

    @unittest.skipUnless(FIRMWARE.exists(), 'Needs the stock Z_30_0120.bin in the repository root')
    def test_checked_in_coverage_matches_firmware(self):
        from z30_extensions.container import extract_eg
        from z30_extensions.platform.z30_120 import StockImage
        image = StockImage(extract_eg(FIRMWARE.read_bytes()))
        for font in (HANT_FONT, HANS_FONT):
            expected = {cp for first, run in font_ranges(image, font) for cp in range(first, first + run)}
            self.assertEqual(coverage(font), expected)


if __name__ == '__main__':
    unittest.main()
