"""The live-view ready banner of card builds (features/ready_banner.py)."""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Z_30_0120.bin'
try:
    import keystone  # noqa: F401
except ImportError:  # optional build dependency
    keystone = None


@unittest.skipUnless(keystone and SOURCE.exists(), 'needs keystone and Z_30_0120.bin')
class ReadyBannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        from z30_extensions.plan import Plan
        cls.stock = extract_eg(SOURCE.read_bytes())
        cls.image = Plan(cls.stock).image

    def test_images_share_one_aligned_size(self):
        from z30_extensions.features import ready_banner as rb
        pictures = rb.images(self.image, 16)
        sizes = {(w, h) for w, h, _ in pictures.values()}
        self.assertEqual(len(sizes), 1)
        (width, height), = sizes
        self.assertEqual((width % 16, height % 8), (0, 0))
        self.assertLessEqual(width, 720)
        for w, h, pixels in pictures.values():
            self.assertEqual(len(pixels), w * h * 4)
            # Corners are transparent (rounded box); the middle of the box is opaque-ish.
            self.assertEqual(pixels[3], 0)
            middle = ((h // 2) * w + w // 2) * 4
            self.assertGreater(pixels[middle + 3], 200)

    def test_descriptor_matches_the_stock_icon_layout(self):
        from z30_extensions.features import ready_banner as rb
        d = rb.descriptor(0x12345678, 480, 48, 120, 64)
        self.assertEqual(len(d), 0x5C)
        self.assertEqual(struct.unpack_from('<3I', d, 0), (1, 0, 0x12345678))
        self.assertEqual(struct.unpack_from('<4h', d, 0x10), (0, 0, 480, 48))
        self.assertEqual(struct.unpack_from('<4h', d, 0x18), (120, 64, 600, 112))
        self.assertEqual((d[0x20], struct.unpack_from('<h', d, 0x40)[0]), (0xFF, 360))
        self.assertEqual(struct.unpack_from('<2I', d, 0x4C), (480, 48))
        self.assertEqual(struct.unpack_from('<I', d, 0x2C)[0], 0)   # not the SRC / banded-clear path

    def test_only_card_builds_have_it(self):
        from z30_extensions.build import build
        report = build(self.stock, 'photo-zebra')[1]
        self.assertNotIn('ready_banner', report['features'])
        self.assertNotIn('Ready banner on the live view', [e['name'] for e in report['edits']])
        test = build(self.stock, 'photo-zebra', ready_banner='qemu-test')[1]['features']['ready_banner']
        self.assertEqual(test['features'], 1)
        self.assertIn('1 项功能', test['texts']['zh-Hans'])

    def test_pixels_are_aligned_in_card_builds(self):
        # The camera's GPU drew nothing from pixels at 8, 28 or 40 mod 64 (2026-11-25).
        from z30_extensions import card
        from z30_extensions.features import ready_banner as rb
        resident = card.build_resident(self.stock)
        for base in (card.LINK_BASE,) + card.CHECK_BASES:
            from z30_extensions.build import build
            plan, report = build(self.stock, 'full', card=True, tail_base=base, resident=resident.symbols)
            for address in report['features']['ready_banner']['pixels'].values():
                self.assertEqual(int(address, 16) % rb.PIXEL_ALIGN, 0)

    def test_battery_percent_shares_the_draw_call(self):
        from z30_extensions.build import build
        with self.assertRaises(ValueError):
            build(self.stock, ['battery-percent'], ready_banner='qemu-test')


if __name__ == '__main__':
    unittest.main()
