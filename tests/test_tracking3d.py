"""3D tracking (3d-tracking): the detection-writer cave, executed in Unicorn on the built
full image, and the native photo-menu page."""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'Z_30_0120.bin'

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                   UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                                   UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_LR)
except ImportError:  # pragma: no cover
    Uc = None


class Tracking3dPresetTest(unittest.TestCase):
    def test_full_includes_it_and_it_brings_the_menu_framework(self):
        from z30_extensions.build import resolve_presets
        self.assertIn('3d-tracking', resolve_presets('full')[1])
        self.assertEqual(resolve_presets('3d-tracking')[1], ['3d-tracking'])

    def test_page_is_a_native_photo_row_and_in_the_focus_category(self):
        from z30_extensions.features.menu.framework import CATEGORIES
        from z30_extensions.features.menu.layout import NATIVE_MENUS
        self.assertIn('trk3d', dict((key, members) for key, _, members in CATEGORIES)['focus'])
        self.assertIn('trk3d', dict((m[0], m[3]) for m in NATIVE_MENUS)['photo'])


@unittest.skipUnless(FIRMWARE.exists() and Uc, 'needs Z_30_0120.bin and Unicorn')
class Tracking3dCaveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        from z30_extensions.build import build
        cls.stock = extract_eg(FIRMWARE.read_bytes())
        cls.image, cls.report = build(cls.stock, 'full')

    def test_research_detection_hook_cannot_build_with_it(self):
        from z30_extensions.build import build
        with self.assertRaises(ValueError):
            build(self.stock, ['3d-tracking'], module_host=True, face_hook=True)

    def run_site(self, choice, tracking=1, live=0x10007, flag=0, subjects=0, face=(29, 19, 2, 3), box=(2508, 1580),
                 af=0, shown=0, armed=0, result=None, area=6, detect=1, select=1):
        from z30_extensions.features.tracking3d.tracking import SITE, RETURN, TRACKING, TRACKER_LIVE, TOUCH_FLAG, SUBJECTS, AF_WORKING, BOX_SHOWN, ARMED_OFFSET, ENTER_TRACKING_MODE, AREA_MODE, DETECT_SELECT, GET_SETTING
        BOX0, BOX1 = 0xC237D59C, 0xC237D5A0   # tracking box (no longer read by the cave)
        from z30_extensions.platform.z30_120 import BASE
        image, report = self.image, self.report
        fn = int(report['features']['tracking3d_control']['choice'], 16) & ~1
        get = int(report['features']['tracking3d_control']['functions']['trk3d_get'], 16) & ~1
        STATE = 0x20040000
        u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(BASE, (len(image) + 0xFFFF) & ~0xFFFF)
        u.mem_write(BASE, image)
        for a in (0xC18C7000, 0xC237C000, 0xC237D000, 0xC237E000, 0xC237F000, 0xC23AA000):
            u.mem_map(a, 0x1000)
        u.mem_map(0x20000000, 0x80000)
        W = lambda a, v: u.mem_write(a, struct.pack('<I', v))
        W(TRACKING, tracking); W(TRACKER_LIVE, live); W(TOUCH_FLAG, flag); W(SUBJECTS, subjects)
        W(AF_WORKING, af); W(BOX_SHOWN, shown); W(STATE + ARMED_OFFSET, armed)
        u.mem_write(AREA_MODE, bytes([area])); u.mem_write(DETECT_SELECT, bytes([select]))
        W(BOX0, box[0] << 16); W(BOX1, ((box[0] + 552) << 16) | box[1])
        XY, WH = 0x20001000, 0x20002000
        u.mem_write(XY, struct.pack('<2h', face[0], face[1])); u.mem_write(WH, struct.pack('<2h', face[2], face[3]))
        def stub_choice(uc, addr, size, _):
            for r in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3):
                uc.reg_write(r, 0xDEAD)                      # clobbered by the call
            uc.reg_write(UC_ARM_REG_R0, choice)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, stub_choice, begin=fn, end=fn + 1)
        def stub_get(uc, addr, size, _):
            uc.reg_write(UC_ARM_REG_R0, STATE); uc.reg_write(UC_ARM_REG_R1, 0xDEAD)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, stub_get, begin=get, end=get + 1)
        keys = []
        def stub_setting(uc, addr, size, _):
            keys.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_SP) % 8))
            for r in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3):
                uc.reg_write(r, 0xDEAD)
            uc.reg_write(UC_ARM_REG_R0, detect); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, stub_setting, begin=GET_SETTING, end=GET_SETTING + 1)
        enters = []
        def stub_enter(uc, addr, size, _):
            ptr = uc.reg_read(UC_ARM_REG_R0)
            enters.append((ptr, uc.mem_read(ptr, 1)[0], uc.reg_read(UC_ARM_REG_SP) % 8))
            uc.reg_write(UC_ARM_REG_R0, 1); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, stub_enter, begin=ENTER_TRACKING_MODE, end=ENTER_TRACKING_MODE + 1)
        writes = []
        u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, acc, a, sz, v, _: writes.append(a) if a >= 0xC0000000 else None)
        out = []
        def ret(uc, addr, size, _):
            out.append(tuple(uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R4,
                                                      UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_SP)))
            uc.emu_stop()
        u.hook_add(UC_HOOK_CODE, ret, begin=RETURN, end=RETURN + 1)
        RESULT = 0x20003000
        regs = dict(r0=RESULT, r1=XY, r2=WH, r4=0x44, r5=0x55, r6=0x66, r7=0x77)
        for name, v in regs.items():
            u.reg_write(globals()['UC_ARM_REG_' + name.upper()], v)
        u.reg_write(UC_ARM_REG_SP, 0x20008000); u.reg_write(UC_ARM_REG_LR, 0x10000001)
        u.emu_start(SITE | 1, 0, count=2000)
        self.assertEqual(len(out), 1)
        r0, r1, r2, r4, r5, r6, r7, sp = out[0]
        # the displaced stock prologue ran on the caller's registers: push {r4-r6,lr}; mov r5,r1; mov r4,r2; movs r2,#0xF0
        self.assertEqual((r0, r1, r2, r4, r5, r6, r7, sp), (RESULT, XY, 0xF0, WH, XY, 0x66, 0x77, 0x20008000 - 16))
        self.assertEqual(struct.unpack('<4I', u.mem_read(sp, 16)), (0x44, 0x55, 0x66, 0x10000001))
        if result is not None:
            result.update(keys=keys, enters=enters, armed=struct.unpack('<I', u.mem_read(STATE + ARMED_OFFSET, 4))[0])
        return struct.unpack('<I', u.mem_read(TOUCH_FLAG, 4))[0], writes

    def test_enable_sets_the_flag_for_any_detected_subject(self):
        from z30_extensions.features.tracking3d.tracking import TOUCH_FLAG
        flag, writes = self.run_site(1, subjects=1)
        self.assertEqual((flag, writes), (1, [TOUCH_FLAG]))
        self.assertEqual(self.run_site(1, subjects=0)[0], 0)                       # no subject
        # no area: stock hands over only when the subject is on the box anyway (AF000207)
        self.assertEqual(self.run_site(1, subjects=1, face=(54, 35, 2, 3))[0], 1)
        self.assertEqual(self.run_site(1, subjects=1, face=(2, 2, 2, 3))[0], 1)

    def test_only_with_auto_area_people_or_animals(self):
        from z30_extensions.features.tracking3d.tracking import TOUCH_FLAG
        for detect in (1, 2):                                        # people, animals
            r = {}
            flag, writes = self.run_site(1, subjects=1, detect=detect, result=r)
            self.assertEqual((flag, writes), (1, [TOUCH_FLAG]), detect)
            self.assertEqual(r['keys'], [(0xC3D, 0)])               # 8-byte aligned stack
        r = {}
        self.run_site(1, subjects=1, select=0, result=r)            # the stock code's other (movie) key
        self.assertEqual(r['keys'], [(0x58C, 0)])
        # only the low byte counts, as in the stock uxtb (0x1039CC2C)
        self.assertEqual(self.run_site(1, subjects=1, detect=0x7F01)[0], 1)
        self.assertEqual(self.run_site(1, subjects=1, detect=0xFFFFFF02)[0], 1)
        for case in (dict(detect=0),                                 # plain Auto-area AF
                     dict(detect=0x100),                             # low byte 0
                     dict(detect=3),
                     dict(area=4), dict(area=0), dict(area=5)):      # not auto area
            flag, writes = self.run_site(1, subjects=1, **case)
            self.assertEqual((flag, writes), (0, []), case)

    def test_detection_keys_are_the_stock_auto_area_sub_modes(self):
        # PR 36 second review: revision 6 read 0xC3C / 0x58B, the wide-area L sub-modes.
        # The stock area setters store base * 100 + sub: base 4 -> 0xC3C / 0x58B, base 6 -> 0xC3D / 0x58C,
        # and the stock menu reads 0xC3D for base 6 (0 plain, 1 people, 2 animals).
        from z30_extensions.features.tracking3d.tracking import DETECT_KEYS
        from z30_extensions.platform.z30_120 import BASE
        def at(a, n=4): return self.stock[a - BASE:a - BASE + n]
        movw_r0 = lambda k: (0xF2400000 | ((k >> 12) << 16) | (((k >> 11) & 1) << 26)
                             | (((k >> 8) & 7) << 12) | (k & 0xFF))
        enc = lambda k: struct.pack('<2H', movw_r0(k) >> 16, movw_r0(k) & 0xFFFF)
        self.assertEqual(DETECT_KEYS, (0x58C, 0xC3D))
        self.assertEqual(at(0x1042B2F2, 2), bytes.fromhex('062c'))          # photo setter: cmp r4,#6
        self.assertEqual(at(0x1042B2F8), enc(0xC3D))
        self.assertEqual(at(0x1042B306), enc(0xC3C))                        # base 4 (wide-area L)
        self.assertEqual(at(0x1042B328, 2), bytes.fromhex('062c'))          # movie setter: cmp r4,#6
        self.assertEqual(at(0x1042B32E), enc(0x58C))
        self.assertEqual(at(0x10489858, 2), bytes.fromhex('0628'))          # menu: cmp r0,#6
        self.assertEqual(at(0x10489888), enc(0xC3D))

    def test_nothing_is_written_otherwise(self):
        for case in (dict(choice=0, subjects=1),                      # OFF (default)
                     dict(choice=2, subjects=1),                      # out-of-range choice (old Close)
                     dict(choice=1, subjects=1, tracking=0),          # not tracking
                     dict(choice=1, subjects=1, live=0),              # tracker cleared: handed over
                     dict(choice=1, subjects=1, flag=1)):             # touch start: stock already set it
            flag, writes = self.run_site(**case)
            self.assertEqual(writes, [], case)

    # back to the tracking mode after a half-press handover (method B)
    def test_handover_during_half_press_arms_the_return(self):
        r = {}
        flag, _ = self.run_site(1, subjects=1, af=1, result=r)
        self.assertEqual((flag, r['armed'], r['enters']), (1, 1, []))
        r = {}
        self.run_site(1, subjects=1, af=0, result=r)                 # OK-started tracking: no return
        self.assertEqual((r['armed'], r['enters']), (0, []))

    def test_release_after_the_handover_enters_the_tracking_mode_once(self):
        r = {}
        self.run_site(1, tracking=0, live=0, armed=1, af=0, shown=0, result=r)
        self.assertEqual(len(r['enters']), 1)
        ptr, byte, align = r['enters'][0]
        self.assertEqual((byte, align, r['armed']), (0, 0, 0))     # &byte 0 as the stock OK handler; 8-byte aligned stack

    def test_no_return_while_half_pressed_or_when_stock_is_back(self):
        for case, armed_after in ((dict(af=1), 1), (dict(af=0, shown=1), 0), (dict(af=0, tracking=1), 1)):
            r = {}
            args = dict(tracking=0, live=0, armed=1, result=r); args.update(case)
            self.run_site(1, **args)
            self.assertEqual((r['enters'], r['armed']), ([], armed_after), case)
        r = {}
        self.run_site(0, tracking=0, live=0, armed=1, result=r)     # switch OFF: nothing at all
        self.assertEqual((r['enters'], r['armed']), ([], 1))
