"""Offline execution of the battery-percent image hook (Unicorn fixture, optional)."""
import struct
import sys
import unittest
from pathlib import Path

try:
    import unicorn  # noqa: F401
    import keystone  # noqa: F401
except ImportError:  # optional research dependencies
    unicorn = None

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Z_30_0120.bin'
GLYPH_ROWS = {  # 3 x 5 pictures the draw must produce
    '0': ('###', '# #', '# #', '# #', '###'), '1': ('.#.', '##.', '.#.', '.#.', '###'),
    '5': ('###', '#..', '###', '..#', '###'), '%': ('#.#', '..#', '.#.', '#..', '#.#'),
}
ALL_ROWS = {
    '0': ('###', '#.#', '#.#', '#.#', '###'), '1': ('.#.', '##.', '.#.', '.#.', '###'),
    '2': ('###', '..#', '###', '#..', '###'), '3': ('###', '..#', '###', '..#', '###'),
    '4': ('#.#', '#.#', '###', '..#', '..#'), '5': ('###', '#..', '###', '..#', '###'),
    '6': ('###', '#..', '###', '#.#', '###'), '7': ('###', '..#', '.#.', '.#.', '.#.'),
    '8': ('###', '#.#', '###', '#.#', '###'), '9': ('###', '#.#', '###', '..#', '###'),
    '%': ('#.#', '..#', '.#.', '#..', '#.#'),
}


@unittest.skipUnless(unicorn and SOURCE.exists(), 'needs unicorn, keystone and the stock container')
class BatteryPercentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / 'tools'))
        from z30_extensions.build import build
        from z30_extensions.container import extract_eg
        cls.stock = extract_eg(SOURCE.read_bytes())
        cls.image, cls.report = build(cls.stock, 'battery-percent')

    VOLTS = {100: 8480, 80: 8120, 85: 8240, 67: 7932, 21: 7548, 20: 7540, 15: 7500, 8: 7396, 0: 6000}

    def run_hook(self, percent, width=60, height=32, x=2, y=396, volts=None):
        from native_runtime import Context, RAM, STACK, STOP
        from unicorn import UC_HOOK_CODE
        from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_LR
        from z30_extensions.features import battery_percent as bp
        cx = Context(self.stock, self.image)
        u = cx.u
        cx.map(0xC236CE00, 0x100)
        u.mem_write(bp.VOLTAGE, struct.pack('<I', volts if volts is not None else self.VOLTS[percent]))
        calls = []

        def record(name):
            def hook(emulator, address, size, data):
                r0 = emulator.reg_read(UC_ARM_REG_R0)
                calls.append((name, struct.unpack('<7i', bytes(emulator.mem_read(r0, 28)))
                              if name != 'image' else r0))
                emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))
            return hook
        for name, address in (('rect', bp.RECT), ('clear', bp.CLEAR), ('image', bp.IMAGE)):
            u.hook_add(UC_HOOK_CODE, record(name), begin=address, end=address)
        frame = STACK + 0x80000
        descriptor = frame
        u.mem_write(descriptor, bytes(0x5C))
        u.mem_write(descriptor + 0x14, struct.pack('<HHhh', width, height, x, y))
        sp = frame
        u.reg_write(UC_ARM_REG_SP, sp)
        u.mem_write(sp + 0x5C, struct.pack('<I', STOP | 1))     # return address popped by the hook
        u.reg_write(UC_ARM_REG_R0, descriptor)
        u.reg_write(UC_ARM_REG_LR, 0)
        u.emu_start(bp.SITE | 1, STOP, count=20000)
        return calls, descriptor, u.reg_read(UC_ARM_REG_SP)

    def picture(self, calls, percent):
        """Rasterize the recorded rectangles into the 60 x 32 box."""
        pixels = [[' '] * 60 for _ in range(32)]
        for name, args in calls:
            if name != 'rect':
                continue
            x, yup, w, h, colour = args[:5]
            y = 480 - yup - h - 396
            for j in range(h):
                for i in range(w):
                    pixels[y + j][x - 2 + i] = '.' if colour == 0x80 else '#'
        return pixels

    def glyph(self, pixels, index, count, text):
        left = 2 + 28 - (count * 12 - 3) // 2 + index * 12
        rows = []
        for r in range(5):
            row = ''.join('#' if pixels[8 + r * 3][left - 2 + 3 * k] == '#' else '.' for k in range(3))
            rows.append(row)
        return tuple(rows)

    def test_three_digits_are_drawn_without_the_stock_image(self):
        calls, _, sp = self.run_hook(100)
        self.assertNotIn('image', [c[0] for c in calls])
        self.assertEqual(calls[0][0], 'clear')
        self.assertEqual(calls[0][1][:4], (2, 480 - 396 - 32, 60, 32))
        self.assertEqual(sp, unicorn_stack_after())

    def test_two_digit_glyph_pictures(self):
        calls, _, _ = self.run_hook(15)
        pixels = self.picture(calls, 15)
        self.assertEqual(self.read_text(pixels, 3), '15%')

    def test_red_at_twenty_and_below_white_above(self):
        for percent, colour in ((20, 0xE62E2EFF), (21, 0xFFFFFFFF), (100, 0xFFFFFFFF), (0, 0xE62E2EFF)):
            calls, _, _ = self.run_hook(percent)
            frame = [c for c in calls if c[0] == 'rect'][1]
            self.assertEqual(frame[1][4] & 0xFFFFFFFF, colour, percent)

    def test_other_images_and_bad_readings_go_to_the_stock_entry(self):
        for kwargs, percent in (({'width': 50}, 80), ({'height': 40}, 80), ({'x': 200}, 80),
                                ({'y': 300}, 80)):
            calls, descriptor, _ = self.run_hook(percent, **kwargs)
            self.assertEqual(calls, [('image', descriptor)], kwargs)
        for volts in (0, 4999, 9501, 0xFFFFFFFF):
            calls, descriptor, _ = self.run_hook(None, volts=volts)
            self.assertEqual(calls, [('image', descriptor)], volts)

    def test_the_estimate_follows_the_curve(self):
        # the number of draw calls grows with digits; read the glyphs back instead
        for volts, expected in ((8484, '100'), (8259, '86'), (8152, '81'), (7300, '5'), (7000, '2'), (6600, '0'), (8480, '100')):
            calls, _, _ = self.run_hook(None, volts=volts)
            pixels = self.picture(calls, None)
            text = self.read_text(pixels, len(expected) + 1)
            self.assertEqual(text, expected + '%', volts)

    def read_text(self, pixels, count):
        names = {rows: ch for ch, rows in ALL_ROWS.items()}
        out = ''
        for index in range(count):
            out += names.get(self.glyph(pixels, index, count, ''), '?')
        return out

    def test_curve_is_descending_and_ends_at_zero(self):
        from z30_extensions.features import battery_percent as bp
        volts = [v for v, _ in bp.CURVE]
        percents = [p for _, p in bp.CURVE]
        self.assertEqual(volts, sorted(volts, reverse=True))
        self.assertEqual(percents, sorted(percents, reverse=True))
        self.assertEqual((percents[0], percents[-1]), (100, 0))
        self.assertLessEqual(bp.VOLT_MAX, 0xFFFF)

    def test_only_the_call_site_window_changes(self):
        base = 0x10020000
        changed = [i + base for i in range(len(self.stock) - 2) if self.stock[i] != self.image[i]]
        self.assertGreaterEqual(min(changed), 0x103BC4D0)
        self.assertLessEqual(max(changed), 0x103BC4D7)


def unicorn_stack_after():
    from native_runtime import STACK
    return STACK + 0x80000 + 0x5C + 4


if __name__ == '__main__':
    unittest.main()
