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

    def test_stamp_sits_in_the_lower_right_of_every_stock_size(self):
        for width, height in ((5568, 3712), (4176, 2784), (2784, 1856), (4952, 3712),
                              (5568, 2328), (640, 480), (160, 120)):
            for mode in (stamp.MODE_DATE, stamp.MODE_DATE_TIME):
                with self.subTest(width=width, height=height, mode=mode):
                    x0, y0, unit, total_width, total_height = stamp.layout(
                        width, height, stamp.stamp_text(DATE, mode))
                    self.assertGreaterEqual(unit, 1)
                    self.assertEqual((x0 % 2, y0 % 2), (0, 0))
                    self.assertLessEqual(x0 + total_width, width)
                    self.assertLessEqual(y0 + total_height, height)
                    self.assertGreater(x0, width // 2 - total_width)
                    self.assertGreater(y0, height // 2)

    def test_drawing_stays_inside_the_stamp_box(self):
        width, height, stride = 1280, 720, 1296
        y_plane, c_plane = planes(stride, height)
        self.assertTrue(stamp.stamp_frame(y_plane, c_plane, width, height, stride, stride, 1,
                                          DATE, stamp.MODE_DATE_TIME))
        x0, y0, _, total_width, total_height = stamp.layout(
            width, height, stamp.stamp_text(DATE, stamp.MODE_DATE_TIME))
        touched = [(index % stride, index // stride) for index, value in enumerate(y_plane) if value != 0x40]
        self.assertTrue(touched)
        self.assertGreaterEqual(min(x for x, _ in touched), x0)
        self.assertLess(max(x for x, _ in touched), x0 + total_width)
        self.assertGreaterEqual(min(y for _, y in touched), y0)
        self.assertLess(max(y for _, y in touched), y0 + total_height)
        self.assertEqual({y_plane[y * stride + x] for x, y in touched}, {stamp.STAMP_Y})

    def test_opaque_drawing_is_idempotent(self):
        once = planes(800, 600)
        stamp.stamp_frame(*once, 800, 600, 800, 800, 2, DATE, stamp.MODE_DATE)
        twice = (bytearray(once[0]), bytearray(once[1]))
        stamp.stamp_frame(*twice, 800, 600, 800, 800, 2, DATE, stamp.MODE_DATE)
        self.assertEqual(once, twice)

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
        self.assertEqual(table[stamp.TABLE_PRIMITIVES:stamp.TABLE_PRIMITIVES + 4], bytes(stamp.PRIMITIVES[0]))
        self.assertEqual(len(table), stamp.TABLE_PRIMITIVES + 4 * len(stamp.PRIMITIVES))


if __name__ == '__main__':
    unittest.main()
