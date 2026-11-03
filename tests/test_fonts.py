import importlib.util
import struct
import unittest
from pathlib import Path

from z30_extensions.platform.fonts import (FONT_TABLE, FONT_TABLE_LITERAL, HANS_FONT, HANT_FONT, Glyph,
                                           character_map, coverage, decode_glyph, encode_glyph,
                                           extend_font, font_blob, font_ranges,
                                           extra_glyphs, glyph_addresses, missing_glyphs)
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
        stock = dict(extended=False)
        self.assertEqual(missing_glyphs('橙橘晕厂扩展听块', HANS_FONT, **stock), '橙橘晕厂扩展听块')
        self.assertEqual(missing_glyphs('橙橘找', HANT_FONT, **stock), '橙橘找')
        self.assertEqual(missing_glyphs('设置默认柔光原始', HANS_FONT, **stock), '')
        self.assertEqual(missing_glyphs('預設光暈', HANT_FONT, **stock), '')
        self.assertEqual(missing_glyphs('Quick', HANS_FONT, **stock), 'Q')
        self.assertGreater(len(coverage(HANS_FONT, extended=False)), 700)

    def test_added_glyphs_cover_ascii_and_labels(self):
        ascii = ''.join(map(chr, range(0x20, 0x7F)))
        for font in (HANT_FONT, HANS_FONT):
            self.assertEqual(missing_glyphs(ascii, font), '')
            added = extra_glyphs(font)
            self.assertFalse(set(map(ord, added)) & coverage(font, extended=False))
            for char, record in added.items():
                glyph, end = decode_glyph(bytes.fromhex(record))
                self.assertEqual(end, len(record) // 2, char)
                self.assertEqual(glyph.flags, 0x92 if char.isascii() else 0x12, char)
        self.assertEqual(missing_glyphs('橙色光晕原厂', HANS_FONT), '')
        self.assertEqual(missing_glyphs('橙色光暈原廠找', HANT_FONT), '')

    def test_every_translation_has_stock_glyphs(self):
        for english, (hans, hant) in TRANSLATIONS.items():
            with self.subTest(english=english):
                check_glyphs(english, hans, hant)

    def test_build_rejects_missing_glyph(self):
        class Plan:
            def alloc(self, raw):
                return 0
        with self.assertRaisesRegex(ValueError, '鑫'):
            check_glyphs('Gold', '鑫色', '金色')
        # Untranslated labels reuse the English text in the CJK fonts, which
        # now have all of ASCII.
        TextResources(Plan()).alloc('Quit @ ~/WL-LUT.CUBE')
        with self.assertRaisesRegex(ValueError, 'é'):
            TextResources(Plan()).alloc('Café')

    @unittest.skipUnless(FIRMWARE.exists(), 'Needs the stock Z_30_0120.bin in the repository root')
    def test_checked_in_coverage_matches_firmware(self):
        image = stock_image()
        for font in (HANT_FONT, HANS_FONT):
            expected = {cp for first, run in font_ranges(image, font) for cp in range(first, first + run)}
            self.assertEqual(coverage(font, extended=False), expected)

    @unittest.skipUnless(FIRMWARE.exists(), 'Needs the stock Z_30_0120.bin in the repository root')
    def test_build_installs_extended_fonts(self):
        from z30_extensions.features import cjk_fonts
        from z30_extensions.plan import Plan
        plan, functions = Plan(stock_image().data), {}
        table = cjk_fonts.add(plan, functions)
        self.assertEqual(cjk_fonts.add(plan, functions), table)
        edit, = plan.edits
        report = plan.features['cjk_fonts']
        sizes_at = int(report['size_table'], 16)
        self.assertEqual((edit.expected, edit.replacement),
                         (struct.pack('<2I', FONT_TABLE, FONT_TABLE + 100), struct.pack('<2I', table, sizes_at)))
        tail = plan.address - len(plan.tail)
        def read(address, size):
            if address >= tail:
                return bytes(plan.tail[address - tail:address - tail + size]).ljust(size, bytes(1))
            return plan.image.read(address, size)
        # All 25 pointers and sizes (the language page draws with font 24).
        pointers = struct.unpack('<25I', read(table, 100))
        sizes = struct.unpack('<25I', read(sizes_at, 100))
        stock = struct.unpack('<25I', plan.image.read(FONT_TABLE, 100))
        stock_sizes = struct.unpack('<25I', plan.image.read(FONT_TABLE + 100, 100))
        for font in (HANT_FONT, HANS_FONT):
            self.assertGreaterEqual(pointers[font], tail)
            self.assertEqual(sizes[font], len(extend_font(font_blob(plan.image, font), {
                ord(c): decode_glyph(bytes.fromhex(r))[0] for c, r in extra_glyphs(font).items()})))
            image = BlobImage(read(pointers[font], sizes[font]), font)
            for char, record in extra_glyphs(font).items():
                self.assertEqual(glyph_of(image, font, ord(char)), decode_glyph(bytes.fromhex(record))[0], char)
            self.assertEqual(glyph_of(image, font, ord('设' if font == HANS_FONT else '設')),
                             glyph_of(BlobImage(font_blob(plan.image, font), font), font,
                                      ord('设' if font == HANS_FONT else '設')))
        for i in set(range(25)) - {HANT_FONT, HANS_FONT}:
            self.assertEqual((pointers[i], sizes[i]), (stock[i], stock_sizes[i]))

    @unittest.skipUnless(FIRMWARE.exists(), 'Needs the stock Z_30_0120.bin in the repository root')
    def test_stock_glyph_records_roundtrip(self):
        # Records tile the glyph data exactly and re-encode to the stock bytes.
        image = stock_image()
        for font in (HANT_FONT, HANS_FONT):
            addresses = sorted(set(glyph_addresses(image, font).values()))
            self.assertGreater(len(addresses), 800)
            for address, following in zip(addresses, addresses[1:]):
                raw = image.read(address, following - address)
                glyph, end = decode_glyph(raw)
                self.assertEqual(end, len(raw), hex(address))
                self.assertEqual(encode_glyph(glyph), raw, hex(address))


    @unittest.skipUnless(FIRMWARE.exists(), 'Needs the stock Z_30_0120.bin in the repository root')
    def test_extended_font_keeps_stock_glyphs(self):
        image = stock_image()
        for font in (HANT_FONT, HANS_FONT):
            blob = font_blob(image, font)
            self.assertEqual(extend_font(blob, {}), blob)
            stock = BlobImage(blob, font)
            added = Glyph(0x92, [[(0, 0, 1), (900, 0, 1), (450, 1400, 1)]], size=(900, 1400),
                          x_min=0, y_max=1400, bearings=(-512, 360))
            extended = BlobImage(extend_font(blob, {ord('橙'): added, 0x41: added}), font)
            for first, _glyph, run in character_map(stock, font):
                for cp in range(first, first + run):
                    if cp != 0x41:
                        self.assertEqual(glyph_of(extended, font, cp), glyph_of(stock, font, cp), hex(cp))
            self.assertEqual(glyph_of(extended, font, ord('橙')), added)
            self.assertEqual(glyph_of(extended, font, 0x41), added)


class BlobImage:
    """A lone font blob behind a one-entry stock-style font table."""
    def __init__(self, blob, font):
        self.memory = {FONT_TABLE_LITERAL: struct.pack('<I', FONT_TABLE),
                       FONT_TABLE + font * 4: struct.pack('<2I', 0x20000000, 0x20000000 + len(blob)),
                       0x20000000: bytes(blob) + bytes(4096)}

    read, words = SyntheticImage.read, SyntheticImage.words


def glyph_of(image, font, cp):
    glyph = next(g + cp - first for first, g, run in character_map(image, font) if first <= cp < first + run)
    return decode_glyph(image.read(glyph_addresses(image, font)[glyph], 4096))[0]


class GlyphRecordTests(unittest.TestCase):
    def test_outline_roundtrip_and_delta_widths(self):
        square = [(150, 1467, 1), (917, 1467, 1), (917, -100, 0), (150, -100, 1)]
        glyph = Glyph(0x92, [square, [(400, 400, 1), (500, 400, 1), (450, 520, 1)]],
                      size=(767, 1567), x_min=150, y_max=1467, bearings=(-362, 293))
        raw = encode_glyph(glyph)
        self.assertEqual(raw[:4], bytes.fromhex('92000200'))
        decoded, end = decode_glyph(raw + b'tail')
        self.assertEqual((decoded, end), (glyph, len(raw)))
        # Hand-encoded: flags, contours, bounds, bearings, 3 points, on-curve
        # bits 0b011, descriptors x16/y16, x16/y8, x8/y16, then the deltas.
        triangle = Glyph(0x92, [[(150, 1467, 1), (917, 1467, 1), (917, 1300, 0)]],
                         size=(767, 167), x_min=150, y_max=1467, bearings=(-362, 293))
        expected = bytes.fromhex('9200 0100 ff02a700 9600bb05 96fe2501 0300 03 0300 004080'
                                 '9600bb05 ff0200 0059ff')
        self.assertEqual(encode_glyph(triangle), expected)
        self.assertEqual(decode_glyph(expected), (triangle, len(expected)))
        empty = Glyph(0x90, size=(0, 0), x_min=0, y_max=0, bearings=(0, 0))
        self.assertEqual(decode_glyph(encode_glyph(empty)), (empty, 14))

    def test_explicit_advance_and_long_runs(self):
        line = [(x * 3, (x % 2) * 300, 1) for x in range(70)]
        glyph = Glyph(0x392, [line], advance=1170, size=(207, 300), x_min=0, y_max=300,
                      bearings=(-585, 1460), extra=12)
        decoded, _ = decode_glyph(encode_glyph(glyph))
        self.assertEqual(decoded, glyph)
        with self.assertRaisesRegex(ValueError, 'Composite'):
            decode_glyph(bytes.fromhex('0800'))


NOTO_SC = Path(__file__).resolve().parents[1] / 'build/fonts/noto-cjk/NotoSansCJKsc-Regular.otf'


@unittest.skipUnless(NOTO_SC.exists() and importlib.util.find_spec('fontTools'),
                     'Needs fontTools and build/fonts/noto-cjk/NotoSansCJKsc-Regular.otf')
class OutlineSourceTests(unittest.TestCase):
    def test_converted_glyph_has_stock_metrics(self):
        from z30_extensions.platform.font_source import OutlineSource
        glyph = OutlineSource(NOTO_SC).glyph('橙')
        self.assertEqual(glyph.flags, 0x12)
        self.assertEqual(decode_glyph(encode_glyph(glyph)), (glyph, len(encode_glyph(glyph))))
        points = [p for c in glyph.contours for p in c]
        self.assertEqual(glyph.bearings, (glyph.x_min - 1024, 1760 - glyph.y_max))
        self.assertTrue(0 <= glyph.x_min and glyph.x_min + glyph.size[0] <= 2048)
        self.assertTrue(-300 < min(y for _x, y, _on in points) and glyph.y_max < 1760)
        # TrueType direction: clockwise overall, like the stock outlines.
        signed = sum(c[i][0] * c[(i + 1) % len(c)][1] - c[(i + 1) % len(c)][0] * c[i][1]
                     for c in glyph.contours for i in range(len(c)))
        self.assertLess(signed, 0)


def stock_image():
    from z30_extensions.container import extract_eg
    from z30_extensions.platform.z30_120 import StockImage
    return StockImage(extract_eg(FIRMWARE.read_bytes()))


if __name__ == '__main__':
    unittest.main()
