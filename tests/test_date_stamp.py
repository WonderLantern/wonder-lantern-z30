import unittest

from z30_extensions import date_stamp as stamp

DATE = (2026, 11, 2, 14, 5, 9)


def planes(stride, height):
    return bytearray(b'\x40' * (stride * height)), bytearray(b'\x80' * (stride * height))


class DateStampTests(unittest.TestCase):
    def test_text_uses_film_data_back_order(self):
        self.assertEqual(stamp.stamp_text(DATE, stamp.MODE_DATE),
                         [stamp.APOSTROPHE, 2, 6, stamp.SPACE, 1, 1, stamp.SPACE, 0, 2])
        self.assertEqual(stamp.stamp_text(DATE, stamp.MODE_DATE_TIME)[-6:],
                         [stamp.SPACE, 1, 4, stamp.COLON, 0, 5])

    def test_other_formats_keep_the_apostrophe_on_the_year(self):
        self.assertEqual(stamp.stamp_text(DATE, stamp.MODE_MDY),
                         [1, 1, stamp.SPACE, 0, 2, stamp.SPACE, stamp.APOSTROPHE, 2, 6])
        self.assertEqual(stamp.stamp_text(DATE, stamp.MODE_DMY),
                         [0, 2, stamp.SPACE, 1, 1, stamp.SPACE, stamp.APOSTROPHE, 2, 6])
        self.assertEqual(stamp.stamp_text(DATE, stamp.MODE_DAY_TIME),
                         [0, 2, stamp.SPACE, 1, 4, stamp.COLON, 0, 5])

    def test_saved_mode_numbers_stay_stable(self):
        self.assertEqual((stamp.MODE_OFF, stamp.MODE_DATE, stamp.MODE_DATE_TIME), (0, 1, 2))

    def test_every_color_draws_its_own_values_and_bad_colors_are_skipped(self):
        for index, (luma, cb, cr) in enumerate(stamp.COLORS):
            with self.subTest(color=index):
                y_plane, c_plane = planes(800, 600)
                self.assertTrue(stamp.stamp_frame(y_plane, c_plane, 800, 600, 800, 800, 1, DATE,
                                                  stamp.MODE_DATE, index))
                self.assertIn(luma, y_plane)
                self.assertIn(bytes((cb, cr)), c_plane)
        self.assertEqual(len(set(stamp.COLORS)), len(stamp.COLORS))
        y_plane, c_plane = planes(800, 600)
        self.assertFalse(stamp.stamp_frame(y_plane, c_plane, 800, 600, 800, 800, 1, DATE,
                                           stamp.MODE_DATE, stamp.STYLE_MAX + 1))
        self.assertEqual((y_plane, c_plane), planes(800, 600))

    def test_glow_is_off_unless_its_flag_is_set(self):
        text = stamp.stamp_text(DATE, stamp.MODE_DATE)
        plain, glowing = stamp.layout(5568, 3712, text), stamp.layout(5568, 3712, text, glow=True)
        border = stamp.PASS_GROWTH[0] * 5568 // stamp.SCALE_DIVISOR
        self.assertEqual(glowing[3:], (plain[3] + 2 * border, plain[4] + 2 * border))
        for style in range(stamp.STYLE_MAX + 1):
            with self.subTest(style=style):
                y_plane, c_plane = (bytearray(b'\x10' * (800 * 600)), bytearray(b'\x80' * (800 * 600)))
                stamp.stamp_frame(y_plane, c_plane, 800, 600, 800, 800, 1, DATE, stamp.MODE_DATE, style)
                passes = stamp.PASS_COLORS[style % stamp.GLOW_FLAG]
                self.assertEqual(set(y_plane) - {0x10, passes[-1][0]} != set(), bool(style & stamp.GLOW_FLAG))

    def test_stamp_sits_in_the_lower_right_of_every_stock_size(self):
        for width, height in ((5568, 3712), (4176, 2784), (2784, 1856), (4952, 3712),
                              (5568, 2328), (640, 480), (160, 120)):
            for mode in stamp.FORMATS:
                with self.subTest(width=width, height=height, mode=mode):
                    x0, y0, scale, total_width, total_height = stamp.layout(
                        width, height, stamp.stamp_text(DATE, mode))
                    self.assertGreaterEqual(scale, stamp.MIN_SCALE)
                    self.assertEqual((x0 % 2, y0 % 2), (0, 0))
                    self.assertLessEqual(x0 + total_width, width)
                    self.assertLessEqual(y0 + total_height, height)
                    self.assertGreater(x0, width // 2 - total_width)
                    self.assertGreater(y0, height // 2)

    def test_drawing_stays_inside_the_stamp_box(self):
        width, height, stride = 1280, 720, 1296
        y_plane, c_plane = planes(stride, height)
        self.assertTrue(stamp.stamp_frame(y_plane, c_plane, width, height, stride, stride, 1,
                                          DATE, stamp.MODE_DATE_TIME, stamp.GLOW_FLAG))
        x0, y0, _, total_width, total_height = stamp.layout(
            width, height, stamp.stamp_text(DATE, stamp.MODE_DATE_TIME), glow=True)
        touched = [(index % stride, index // stride) for index, value in enumerate(y_plane) if value != 0x40]
        self.assertTrue(touched)
        self.assertGreaterEqual(min(x for x, _ in touched), x0)
        self.assertLess(max(x for x, _ in touched), x0 + total_width)
        self.assertGreaterEqual(min(y for _, y in touched), y0)
        self.assertLess(max(y for _, y in touched), y0 + total_height)
        glow = {triple[0] for triple in stamp.PASS_COLORS[0]}
        self.assertLessEqual({y_plane[y * stride + x] for x, y in touched}, glow)
        self.assertIn(stamp.COLORS[0][0], {y_plane[y * stride + x] for x, y in touched})

    def test_portrait_stamps_turn_into_the_corner_the_viewer_sees_lower_right(self):
        width, height = 5568, 3712
        text = stamp.stamp_text(DATE, stamp.MODE_DATE_TIME)
        landscape = stamp.layout(width, height, text)
        for orientation in (stamp.ORIENT_CCW, stamp.ORIENT_CW):
            with self.subTest(orientation=orientation):
                x0, y0, _, box_width, box_height = stamp.layout(width, height, text, orientation=orientation)
                # The box turns a quarter, and the corner inset matches landscape.
                self.assertEqual((box_width, box_height), landscape[4:2:-1])
                if orientation == stamp.ORIENT_CW:      # stored top right
                    self.assertEqual((x0, y0), (landscape[0] + landscape[3] - box_width & ~1,
                                                height // stamp.MARGIN_Y_DIVISOR & ~1))
                else:                                   # stored bottom left
                    self.assertEqual((x0, y0), (width // stamp.MARGIN_X_DIVISOR & ~1,
                                                height - height // stamp.MARGIN_Y_DIVISOR - box_height & ~1))

    def test_portrait_drawing_matches_the_landscape_stamp_turned(self):
        # Drawn on a square frame, a turned stamp read back upright equals the landscape one.
        size = 1600
        upright = planes(size, size)
        stamp.stamp_frame(*upright, size, size, size, size, 1, DATE, stamp.MODE_DATE)
        lit = lambda y_plane: {(i % size, i // size) for i, v in enumerate(y_plane) if v != 0x40}
        x0, y0, _, w, h = stamp.layout(size, size, stamp.stamp_text(DATE, stamp.MODE_DATE))
        reference = {(x - x0, y - y0) for x, y in lit(upright[0])}
        for orientation in (stamp.ORIENT_CCW, stamp.ORIENT_CW):
            with self.subTest(orientation=orientation):
                turned = planes(size, size)
                stamp.stamp_frame(*turned, size, size, size, size, 1, DATE, stamp.MODE_DATE, 0, orientation)
                tx, ty = stamp.layout(size, size, stamp.stamp_text(DATE, stamp.MODE_DATE),
                                      orientation=orientation)[:2]
                if orientation == stamp.ORIENT_CW:      # viewer turns clockwise: u = w-1-(y-ty), v = x-tx
                    seen = {(w - 1 - (y - ty), x - tx) for x, y in lit(turned[0])}
                else:                                   # viewer turns counter-clockwise: u = y-ty, v = h-1-(x-tx)
                    seen = {(y - ty, h - 1 - (x - tx)) for x, y in lit(turned[0])}
                self.assertEqual(seen, reference)

    def test_unknown_orientation_draws_as_landscape(self):
        once, unknown = planes(800, 600), planes(800, 600)
        stamp.stamp_frame(*once, 800, 600, 800, 800, 1, DATE, stamp.MODE_DATE)
        stamp.stamp_frame(*unknown, 800, 600, 800, 800, 1, DATE, stamp.MODE_DATE, 0, 3)
        self.assertEqual(once, unknown)

    def test_glyph_runs_fit_the_grid_and_their_advance(self):
        for bit, x, y, w, h in stamp.PRIMITIVES:
            self.assertTrue(w and h)
            self.assertLessEqual(y + h, stamp.GLYPH_HEIGHT)
            if bit < 7:
                self.assertLessEqual(x + w, stamp.ADVANCES[0] - stamp.TRAILING_GAP)
        # Every segment and both colon dots exist, so each digit draws all its parts.
        self.assertEqual({p[0] for p in stamp.PRIMITIVES}, set(range(10)))

    def test_drawing_is_idempotent(self):
        for image_format in (1, 2):
            for style in (0, stamp.GLOW_FLAG):
                with self.subTest(image_format=image_format, style=style):
                    once = (bytearray(bytes(range(256)) * (800 * 600 // 256) + bytes(800 * 600 % 256)),
                            bytearray(b'\x80' * (800 * 600)))
                    stamp.stamp_frame(*once, 800, 600, 800, 800, image_format, DATE, stamp.MODE_DATE, style)
                    twice = (bytearray(once[0]), bytearray(once[1]))
                    stamp.stamp_frame(*twice, 800, 600, 800, 800, image_format, DATE, stamp.MODE_DATE, style)
                    self.assertEqual(once, twice)

    def test_glow_only_lightens_dark_pixels(self):
        width, height = 1280, 720
        dark, bright = ((bytearray(bytes((luma,)) * (width * height)), bytearray(b'\x80' * (width * height)))
                        for luma in (0x10, 0xF0))
        for y_plane, c_plane in (dark, bright):
            stamp.stamp_frame(y_plane, c_plane, width, height, width, width, 1, DATE, stamp.MODE_DATE,
                              stamp.GLOW_FLAG)
        glow = {triple[0] for triple in stamp.PASS_COLORS[0][:-1]}
        self.assertTrue(glow & set(dark[0]))
        # A bright frame only receives the digits themselves.
        self.assertEqual(set(bright[0]), {0xF0, stamp.COLORS[0][0]})

    def test_invalid_input_leaves_the_frame_untouched(self):
        cases = (dict(mode=stamp.MODE_OFF), dict(image_format=3), dict(date=(1999, 1, 1, 0, 0, 0)),
                 dict(date=(2026, 0, 1, 0, 0, 0)), dict(date=(2026, 13, 1, 0, 0, 0)),
                 dict(date=(2026, 1, 32, 0, 0, 0)), dict(date=(2026, 1, 1, 24, 0, 0)),
                 dict(date=(2026, 1, 1, 0, 60, 0)), dict(stride_y=799), dict(stride_c=799),
                 dict(width=0), dict(height=0), dict(height=10))
        for changes in cases:
            with self.subTest(changes=changes):
                arguments = dict(width=800, height=600, stride_y=800, stride_c=800, image_format=1,
                                 date=DATE, mode=stamp.MODE_DATE)
                arguments.update(changes)
                y_plane, c_plane = planes(800, 600)
                self.assertFalse(stamp.stamp_frame(y_plane, c_plane, **arguments))
                self.assertEqual((y_plane, c_plane), planes(800, 600))

    def test_font_table_offsets_match_its_layout(self):
        table = stamp.font_table()
        self.assertEqual(stamp.TABLE_ADVANCES % 4, 0)
        self.assertEqual(table[stamp.TABLE_ADVANCES:stamp.TABLE_ADVANCES + len(stamp.ADVANCES)],
                         bytes(stamp.ADVANCES))
        self.assertEqual(table[stamp.TABLE_PRIMITIVES:stamp.TABLE_PRIMITIVES + stamp.PRIMITIVE_BYTES],
                         bytes(stamp.PRIMITIVES[0]))
        self.assertEqual(table[stamp.TABLE_FORMATS:stamp.TABLE_FORMATS + 6],
                         bytes(stamp.FORMATS[stamp.MODE_DATE] + (stamp.TOKEN_END,)))
        core = stamp.TABLE_COLORS + stamp.COLOR_BYTES - 3
        self.assertEqual(table[core:core + 3], bytes(stamp.COLORS[0]))
        self.assertEqual(table[stamp.TABLE_GROWTH:], bytes(stamp.PASS_GROWTH))
        self.assertEqual(stamp.TABLE_GROWTH, stamp.TABLE_COLORS + stamp.COLOR_BYTES * len(stamp.COLORS))
        self.assertTrue(all(len(tokens) < stamp.FORMAT_BYTES for tokens in stamp.FORMATS.values()))


if __name__ == '__main__':
    unittest.main()
