"""LUT core validation: parsing subset, interpolation, binary round-trip, YCbCr."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.host import lut


def cube_text(size=3, rows=None, domain=None, header=('TITLE t', 'LUT_3D_SIZE 3')):
    if rows is None:
        rows = [(r / (size - 1), g / (size - 1), b / (size - 1))
                for b in range(size) for g in range(size) for r in range(size)]
    lines = list(header) + (list(domain) if domain else []) + \
        [' '.join(f'{v:.6f}' for v in row) for row in rows]
    return '\n'.join(lines) + '\n'


class ParseTest(unittest.TestCase):
    def test_minimal_and_identity(self):
        parsed = lut.parse_cube(lut.identity_cube(4))
        self.assertEqual(parsed['size'], 4)
        self.assertEqual(parsed['domain_min'], (0.0, 0.0, 0.0))
        self.assertEqual(parsed['domain_max'], (1.0, 1.0, 1.0))
        self.assertEqual(len(parsed['table']), 4 ** 3 * 3)

    def test_comments_and_title(self):
        text = '# a comment\nTITLE "my lut"\n\n' + cube_text(3, header=('LUT_3D_SIZE 3',))
        parsed = lut.parse_cube(text)
        self.assertEqual(parsed['title'], 'my lut')
        self.assertEqual(parsed['size'], 3)

    def test_domain(self):
        text = cube_text(3, domain=('DOMAIN_MIN 0.1 0.1 0.1', 'DOMAIN_MAX 0.9 0.9 0.9'),
                         rows=[(0.1 + 0.8 * r / 2, 0.1 + 0.8 * g / 2, 0.1 + 0.8 * b / 2)
                               for b in range(3) for g in range(3) for r in range(3)])
        parsed = lut.parse_cube(text)
        self.assertEqual(parsed['domain_min'], (0.1, 0.1, 0.1))
        # Table rows are in domain units; the first (red-fastest) row is 0.1.
        self.assertAlmostEqual(parsed['table'][0], 0.1)

    def test_reject_1d(self):
        with self.assertRaises(lut.CubeError):
            lut.parse_cube('LUT_1D_SIZE 2\n0 0 0\n1 1 1\n')

    def test_reject_bad_size(self):
        with self.assertRaises(lut.CubeError):
            lut.parse_cube(cube_text(3, header=('LUT_3D_SIZE 1',)))
        with self.assertRaises(lut.CubeError):
            lut.parse_cube(cube_text(3, header=('LUT_3D_SIZE 66',)))
        with self.assertRaises(lut.CubeError):
            lut.parse_cube(cube_text(3, header=('LUT_3D_SIZE abc',)))
        with self.assertRaises(lut.CubeError):
            lut.parse_cube(cube_text(3, header=('LUT_3D_SIZE 3', 'LUT_3D_SIZE 3')))

    def test_reject_row_count(self):
        with self.assertRaises(lut.CubeError):
            lut.parse_cube(cube_text(3, rows=[(0, 0, 0)]))

    def test_reject_unknown_directive(self):
        with self.assertRaises(lut.CubeError):
            lut.parse_cube(cube_text(3, rows=None, domain=('SHAPER 1 2 3',)))

    def test_reject_malformed_numbers(self):
        with self.assertRaises(lut.CubeError):
            lut.parse_cube(cube_text(3, header=('LUT_3D_SIZE 3',), domain=('DOMAIN_MIN 0 a 0',)))

    def test_reject_domain_inversion(self):
        with self.assertRaises(lut.CubeError):
            lut.parse_cube(cube_text(3, domain=('DOMAIN_MAX 0.9 0.9 0.9', 'DOMAIN_MIN 0.1 0.1 0.1')))

    def test_missing_size(self):
        with self.assertRaises(lut.CubeError):
            lut.parse_cube('0 0 0\n')

    def test_extra_channels_rejected(self):
        with self.assertRaises(lut.CubeError):
            lut.parse_cube(cube_text(3, rows=[(0, 0, 0, 0)] * 27))


class InterpolationTest(unittest.TestCase):
    def setUp(self):
        self.parsed = lut.parse_cube(lut.identity_cube(17))

    def test_identity_corners_and_center(self):
        for rgb in ((0, 0, 0), (1, 1, 1), (0.5, 0.5, 0.5), (0.25, 0.75, 0.125)):
            out = lut.trilinear(17, self.parsed['domain_min'], self.parsed['domain_max'],
                                self.parsed['table'], *rgb)
            for got, want in zip(out, rgb):
                self.assertAlmostEqual(got, want, places=12)

    def test_clamps_out_of_domain(self):
        out = lut.trilinear(17, self.parsed['domain_min'], self.parsed['domain_max'],
                            self.parsed['table'], -0.5, 2.0, 0.5)
        self.assertEqual(out, (0.0, 1.0, 0.5))

    def test_midpoint_of_ramp(self):
        # 2x2x2 table with a red ramp 0..1 (red fastest): corner cell midpoint
        # interpolates to 0.5 on red, 0 elsewhere.
        rows = []
        for b in range(2):
            for g in range(2):
                for r in range(2):
                    rows.append((r, g, b))
        parsed = lut.parse_cube(cube_text(2, rows=rows, header=('LUT_3D_SIZE 2',)))
        out = lut.trilinear(2, parsed['domain_min'], parsed['domain_max'],
                            parsed['table'], 0.5, 0.5, 0.5)
        self.assertAlmostEqual(out[0], 0.5)
        self.assertAlmostEqual(out[1], 0.5)
        self.assertAlmostEqual(out[2], 0.5)

    def test_channel_swap(self):
        size = 4
        rows = [(b / 3, g / 3, r / 3) for b in range(size) for g in range(size) for r in range(size)]
        parsed = lut.parse_cube(cube_text(size, rows=rows, header=(f'LUT_3D_SIZE {size}',)))
        out = lut.trilinear(size, parsed['domain_min'], parsed['domain_max'],
                            parsed['table'], 0.0, 0.25, 1.0)
        self.assertAlmostEqual(out[0], 1.0, places=6)
        self.assertAlmostEqual(out[1], 0.25, places=6)
        self.assertAlmostEqual(out[2], 0.0, places=6)


class BinaryTest(unittest.TestCase):
    def test_round_trip_and_lookup_parity(self):
        parsed = lut.parse_cube(lut.identity_cube(5))
        packed = lut.pack_binary(5, parsed['domain_min'], parsed['domain_max'], parsed['table'])
        restored = lut.unpack_binary(packed)
        self.assertEqual(restored['size'], 5)
        for rgb in ((0, 0, 0), (1, 1, 1), (0.31, 0.77, 0.5)):
            a = lut.trilinear(5, parsed['domain_min'], parsed['domain_max'], parsed['table'], *rgb)
            b = lut.trilinear(5, restored['domain_min'], restored['domain_max'],
                              restored['table'], *rgb)
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=4)

    def test_domain_binary(self):
        text = cube_text(3, domain=('DOMAIN_MIN 0.1 0.1 0.1', 'DOMAIN_MAX 0.9 0.9 0.9'),
                         rows=[(0.1 + 0.8 * r / 2, 0.1 + 0.8 * g / 2, 0.1 + 0.8 * b / 2)
                               for b in range(3) for g in range(3) for r in range(3)])
        parsed = lut.parse_cube(text)
        packed = lut.pack_binary(3, parsed['domain_min'], parsed['domain_max'], parsed['table'])
        restored = lut.unpack_binary(packed)
        for value, want in zip(restored['domain_min'], (0.1, 0.1, 0.1)):
            self.assertAlmostEqual(value, want, places=4)
        for value, want in zip(restored['domain_max'], (0.9, 0.9, 0.9)):
            self.assertAlmostEqual(value, want, places=4)

    def test_reject_truncated_and_bad_magic(self):
        with self.assertRaises(lut.CubeError):
            lut.unpack_binary(b'WL3L')
        bad = bytearray(lut.pack_binary(2, (0, 0, 0), (1, 1, 1), (0.0,) * 24))
        bad[0] = ord('X')
        with self.assertRaises(lut.CubeError):
            lut.unpack_binary(bytes(bad))

    def test_domain_outside_unit_rejected(self):
        with self.assertRaises(lut.CubeError):
            lut.pack_binary(2, (-0.5, 0, 0), (1, 1, 1), (0.0,) * 24)


class YcbcrTest(unittest.TestCase):
    def test_gray_round_trip(self):
        # Limited range: code 16 decodes to black, 235 to white; gray stays gray
        # only through the full round trip.
        for y in (16, 128, 235):
            rgb = lut.ycbcr_to_rgb(y, 128, 128)
            self.assertEqual(rgb[0], rgb[1] == rgb[2] and rgb[0])
            back = lut.rgb_to_ycbcr(*rgb)
            self.assertAlmostEqual(back[0], y, delta=1)
            self.assertEqual(back[1], 128)
            self.assertEqual(back[2], 128)
        self.assertEqual(lut.ycbcr_to_rgb(16, 128, 128), (0, 0, 0))
        self.assertEqual(lut.ycbcr_to_rgb(235, 128, 128), (255, 255, 255))

    def test_primary_bounded(self):
        for pixel in lut.ycbcr_to_rgb(235, 240, 240), lut.ycbcr_to_rgb(16, 16, 16):
            for channel in pixel:
                self.assertTrue(0 <= channel <= 255)


if __name__ == '__main__':
    unittest.main()
