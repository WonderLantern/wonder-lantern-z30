"""3D tracking screen-1 subject boxes (v49): the drawer in the stock UI renderer, run in
Unicorn on the built images with the OpenVG entry points stubbed. Checks the gate, the
four bars per box (position, OpenVG y, colour), eye-first boxes, white only on the
focused eye, and that every OpenVG state it changes is restored. The real rendering
is checked in QEMU by tools/verify_trk3d_boxes_qemu.py."""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'Z_30_0120.bin'

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R4,
                                   UC_ARM_REG_R11, UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_LR)
except ImportError:  # pragma: no cover
    Uc = None

STATE = 0x20040000


@unittest.skipUnless(FIRMWARE.exists() and Uc, 'needs Z_30_0120.bin and Unicorn')
class SubjectBoxesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        from z30_extensions.build import build
        cls.stock = extract_eg(FIRMWARE.read_bytes())
        cls.full = build(cls.stock, 'full')
        cls.alone = build(cls.stock, ['3d-tracking'])

    def run_drawer(self, built=None, on=1, shown=1, tracking=0, phase=2, subjects=(), eyes=(), frame=None, history=(), raw_hist=None):
        from z30_extensions.platform.z30_120 import BASE
        from z30_extensions.features.tracking3d import boxes as tb
        image, report = built or self.full
        control = report['features']['tracking3d_control']
        u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(BASE, (len(image) + 0xFFFF) & ~0xFFFF); u.mem_write(BASE, image)
        for a, n in ((0xC1810000, 0x20000), (0xC237C000, 0x1000), (0xC237F000, 0x1000), (0xC259E000, 0x2000),
                     (0x20000000, 0x80000)):
            u.mem_map(a, n)
        W = lambda a, v: u.mem_write(a, struct.pack('<I', v))
        W(STATE, on); W(tb.BOX_SHOWN, shown); W(tb.TRACKING, tracking); u.mem_write(tb.UI_SERVICE_PHASE, bytes([phase]))
        W(tb.SUBJECT_COUNT, len(subjects))
        for i, (x, y, w, h) in enumerate(subjects):
            e = tb.SUBJECT_ENTRIES + i * tb.ENTRY_BYTES
            u.mem_write(e, struct.pack('<IBB4h', i, 1, 1, x, y, w, h))
            if i < len(eyes) and eyes[i]:
                u.mem_write(e + 0x36, struct.pack('<4h', *eyes[i]))
        if frame:
            u.mem_write(tb.STOCK_FRAME, struct.pack('<4h', *frame))
        if history:
            u.mem_write(STATE + tb.HIST, struct.pack('<II', 1, len(history)) + b''.join(struct.pack('<4h', *h) for h in history))
        if raw_hist is not None:                                 # (active, count) as left by an older pass
            u.mem_write(STATE + tb.HIST, struct.pack('<II', *raw_hist))
        log = dict(fills=[], calls=[], overflow=[])
        end = STATE + tb.HIST + 8 + 8 * tb.MAX_BOXES               # first byte after the record
        u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, acc, a, sz, v, _: log['overflow'].append(hex(a)) if end <= a < end + 0x100 else None)
        vg = {0x1104: 3, 0x1131: 1, 0x1100: 0x1401, 0x1105: 0x1F01, 0xC1A0: 0xC201}
        paint = [0x77]

        def ret(v=0):
            u.reg_write(UC_ARM_REG_R0, v); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

        def stub(uc, addr, size, _):
            r0, r1, r2 = (u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            self.assertEqual(u.reg_read(UC_ARM_REG_SP) % 8, 0, hex(addr))           # AAPCS at every stock call
            if addr == get: ret(STATE)
            elif addr == tb.VG_GETI: log['calls'].append(('get', r0)); ret(vg[r0])
            elif addr == tb.VG_SETI: log['calls'].append(('set', r0, r1)); vg[r0] = r1; ret()
            elif addr == tb.VG_GETIV: u.mem_write(r2, struct.pack('<4I', 1, 2, 3, 4)); ret()
            elif addr == tb.VG_SETIV: log['clip'] = struct.unpack('<4I', u.mem_read(r2, 16)); ret()
            elif addr == tb.VG_GET_PAINT: ret(paint[0])
            elif addr == tb.VG_SET_PAINT: paint[0] = r0; ret()
            elif addr == tb.CLIP_RESET: ret()
            elif addr == tb.FILL:
                log['fills'].append(struct.unpack('<7I', u.mem_read(r0, 28)))
                paint[0] = 0; vg[0x1100] = 0x1400; vg[0xC1A0] = 0                    # what the stock fill leaves behind
                ret()
        get = int(control['functions']['trk3d_get'], 16) & ~1
        for a in (get, tb.VG_GETI, tb.VG_SETI, tb.VG_GETIV, tb.VG_SETIV, tb.VG_GET_PAINT, tb.VG_SET_PAINT, tb.CLIP_RESET, tb.FILL):
            u.hook_add(UC_HOOK_CODE, stub, begin=a, end=a + 1)
        fn = int(control['boxes'], 16) & ~1
        u.reg_write(UC_ARM_REG_R4, 0x4444); u.reg_write(UC_ARM_REG_R11, 0xBBBB)
        u.reg_write(UC_ARM_REG_SP, 0x20008000); u.reg_write(UC_ARM_REG_LR, 0x10000001)
        u.emu_start(fn | 1, 0x10000000, count=2_000_000)
        self.assertEqual((u.reg_read(UC_ARM_REG_R4), u.reg_read(UC_ARM_REG_R11), u.reg_read(UC_ARM_REG_SP)),
                         (0x4444, 0xBBBB, 0x20008000))
        log['vg'], log['paint'] = vg, paint[0]
        log['hist'] = struct.unpack('<II', u.mem_read(STATE + tb.HIST, 8))
        log['rects'] = [struct.unpack_from('<4h', u.mem_read(STATE + tb.HIST + 8 + 8 * i, 8)) for i in range(min(log['hist'][1], tb.MAX_BOXES))]
        return log

    @staticmethod
    def box(x, y, size, eye=False):
        from z30_extensions.features.tracking3d import boxes as tb
        cx, cy = ((x * tb.X_MUL) >> 8) + tb.X_ADD, ((y * tb.Y_MUL) >> 8) + tb.Y_ADD
        h = tb.EYE_HALF if eye else ((size * tb.FACE_HALF_MUL) >> 8) + tb.FACE_HALF_ADD
        return cx - h, cy - h, cx + h, cy + h

    def bars(self, x0, y0, x1, y1, colour):
        t = 2
        rects = ((x0, y0, x1, y0 + t), (x0, y1 - t, x1, y1), (x0, y0 + t, x0 + t, y1 - t), (x1 - t, y0 + t, x1, y1 - t))
        return [(a, 480 - d, c - a, d - b, colour, 0, 0) for a, b, c, d in rects]

    def test_draws_four_bars_per_subject_and_restores_the_renderer_state(self):
        from z30_extensions.features.tracking3d import boxes as tb
        for built in (self.full, self.alone):
            log = self.run_drawer(built, subjects=[(200, 140, 24, 24), (100, 60, 20, 20)])
            expect = self.bars(*self.box(200, 140, 24), tb.GREY) + self.bars(*self.box(100, 60, 20), tb.GREY)
            self.assertEqual(log['fills'], expect)
            self.assertIn(('set', 0x1104, tb.VG_BLEND_SRC), log['calls'])
            self.assertEqual(log['vg'], {0x1104: 3, 0x1131: 1, 0x1100: 0x1401, 0x1105: 0x1F01, 0xC1A0: 0xC201})
            self.assertEqual((log['paint'], log['clip']), (0x77, (1, 2, 3, 4)))

    def test_nothing_outside_screen_1(self):
        for case in (dict(on=0), dict(shown=0), dict(tracking=1), dict(phase=4), dict(subjects=())):
            args = dict(subjects=[(200, 140, 24, 24)]); args.update(case)
            log = self.run_drawer(**args)
            self.assertEqual((log['fills'], log['calls']), ([], []), case)

    def test_previous_boxes_are_erased_then_redrawn_and_remembered(self):
        from z30_extensions.features.tracking3d import boxes as tb
        old = (50, 60, 110, 120)
        log = self.run_drawer(subjects=[(200, 140, 24, 24)], history=[old])
        new = self.box(200, 140, 24)
        self.assertEqual(log['fills'], self.bars(*old, 0) + self.bars(*new, tb.GREY))   # transparent first
        self.assertEqual((log['hist'], log['rects']), ((1, 1), [new]))

    def test_screens_2_and_3_only_erase(self):
        old = (50, 60, 110, 120)
        for case in (dict(tracking=1), dict(shown=0), dict(on=0), dict(subjects=())):
            args = dict(subjects=[(200, 140, 24, 24)], history=[old]); args.update(case)
            log = self.run_drawer(**args)
            self.assertEqual(log['fills'], self.bars(*old, 0), case)
            self.assertEqual(log['hist'][0], 0, case)
            self.assertEqual(log['vg'], {0x1104: 3, 0x1131: 1, 0x1100: 0x1401, 0x1105: 0x1F01, 0xC1A0: 0xC201}, case)

    def test_outside_the_shooting_screen_history_is_dropped_untouched(self):
        log = self.run_drawer(phase=4, subjects=[(200, 140, 24, 24)], history=[(50, 60, 110, 120)])
        self.assertEqual((log['fills'], log['calls'], log['hist'][0]), ([], [], 0))

    def test_leaving_the_shooting_screen_clears_the_box_count(self):
        # review (camera): leaving for a menu kept HIST + 4, the next screen-1 pass appended to it
        log = self.run_drawer(phase=4, subjects=[(200, 140, 24, 24)], history=[(50, 60, 110, 120)] * 6)
        self.assertEqual((log['fills'], log['hist']), ([], (0, 0)))

    def test_a_stale_count_never_writes_past_the_record(self):
        from z30_extensions.features.tracking3d import boxes as tb
        many = [(40 + 40 * i, 60 + 20 * (i % 3), 20, 20) for i in range(tb.MAX_BOXES)]
        # the state a build without the fix left behind: no boxes flagged, but a count kept
        for stale in (6, tb.MAX_BOXES, tb.MAX_BOXES + 5, 0xFFFFFFFF):
            log = self.run_drawer(subjects=many, raw_hist=(0, stale))
            self.assertEqual(log['overflow'], [], stale)
            self.assertEqual(log['hist'], (1, tb.MAX_BOXES), stale)
            self.assertEqual(log["rects"], [self.box(m[0], m[1], m[2]) for m in many], stale)
            self.assertEqual(len(log['fills']), 4 * tb.MAX_BOXES, stale)

    def test_screen_1_menu_round_trips_keep_the_record_bounded(self):
        from z30_extensions.features.tracking3d import boxes as tb
        many = [(40 + 40 * i, 60 + 20 * (i % 3), 20, 20) for i in range(tb.MAX_BOXES)]
        hist = None
        for k in range(4):                                       # screen 1, menu, screen 1, menu ...
            log = self.run_drawer(subjects=many, raw_hist=hist)
            self.assertEqual((log['overflow'], log['hist']), ([], (1, tb.MAX_BOXES)), k)
            log = self.run_drawer(phase=4, subjects=many, raw_hist=log['hist'])
            self.assertEqual((log['overflow'], log['hist']), ([], (0, 0)), k)
            hist = log['hist']

    def test_eye_first_and_white_only_on_the_focused_eye(self):
        from z30_extensions.features.tracking3d import boxes as tb
        t = lambda v: v * 135 // 10 + 140
        frame = (t(194) - 55, t(136) - 55, t(194) + 55, t(136) + 55)
        log = self.run_drawer(subjects=[(200, 140, 24, 24), (100, 60, 20, 20)], eyes=[(194, 136, 5, 5)], frame=frame)
        self.assertEqual(log['fills'], self.bars(*self.box(194, 136, 0, eye=True), tb.WHITE) +
                         self.bars(*self.box(100, 60, 20), tb.GREY))
        log = self.run_drawer(subjects=[(200, 140, 24, 24)], eyes=[(194, 136, 5, 5)])   # no stock frame: grey
        self.assertEqual({f[4] for f in log['fills']}, {tb.GREY})

    def test_clipped_to_the_screen_and_tiny_boxes_skipped(self):
        log = self.run_drawer(subjects=[(405, 280, 40, 40), (0, 0, 1, 1)])
        self.assertTrue(log['fills'])
        for x, y, w, h, *_ in log['fills']:
            self.assertTrue(0 <= x and x + w <= 720 and 0 <= y and y + h <= 480, (x, y, w, h))

    def test_renderer_hook_runs_the_displaced_instructions(self):
        from z30_extensions.features.tracking3d import boxes as tb
        image, report = self.full
        edits = [e for e in report['edits'] if 0x10020000 + int(e['offset'], 16) == tb.RENDER_SITE]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]['expected_hex'], tb.RENDER_EXPECTED)
        self.assertEqual(report['features']['tracking3d_control']['boxes_hook'], 'UI renderer after the canvas clear (0x104785d8)')

    def test_fitted_position_matches_the_stock_frame_horizontally(self):
        # AF000123: list (124, w 36) -> stock frame centre x 1819, width 540 tracking units; 720 / 5568 px per unit
        x0, y0, x1, y1 = self.box(124, 139, 36)
        self.assertLess(abs((x0 + x1) / 2 - 1819 * 720 / 5568), 6)
        self.assertLess(abs((x1 - x0) - 540 * 720 / 5568), 8)


if __name__ == '__main__':
    unittest.main()
