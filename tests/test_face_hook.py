"""Execute the dormant detection-result hook (face_hook) against the stock writer."""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'Z_30_0120.bin'

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R4,
                                   UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_LR)
except ImportError:  # pragma: no cover
    Uc = None


@unittest.skipUnless(FIRMWARE.exists() and Uc, 'needs Z_30_0120.bin and Unicorn')
class FaceHookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        from z30_extensions.build import build, resolve_presets
        cls.stock = extract_eg(FIRMWARE.read_bytes())
        # the handover research kit: full (gyro-evr included) without nef-menu (the extension
        # root holds 8 rows, the module host is the 9th) + module host + this hook
        _, features = resolve_presets('full')
        cls.image, cls.report = build(cls.stock, [f for f in features if f not in ('nef-menu', '3d-tracking')], module_host=True, face_hook=True)

    def run_site(self, image, cell=0):
        from z30_extensions.features.modules.face_hook import SITE, RETURN, FACE_CELL
        from z30_extensions.platform.z30_120 import BASE
        u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(BASE, (len(image) + 0xFFFF) & ~0xFFFF)
        u.mem_write(BASE, image)
        u.mem_map(0xC1832000, 0x1000)
        u.mem_map(0x20000000, 0x10000)
        u.mem_map(0x30000000, 0x1000)
        u.mem_write(FACE_CELL, struct.pack('<I', cell))
        calls = []
        def module_cb(uc, addr, size, _):
            sp = uc.reg_read(UC_ARM_REG_SP)
            calls.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2), sp % 8))
            for reg in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2):
                uc.reg_write(reg, 0x5555)                # clobber: the hook must restore
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, module_cb, begin=0x30000000, end=0x30000001)
        u.reg_write(UC_ARM_REG_R0, 0x20001000)
        u.reg_write(UC_ARM_REG_R1, 0x20002000)
        u.reg_write(UC_ARM_REG_R2, 0x20003000)
        u.reg_write(UC_ARM_REG_R4, 0x44); u.reg_write(UC_ARM_REG_R5, 0x55); u.reg_write(UC_ARM_REG_R6, 0x66)
        u.reg_write(UC_ARM_REG_LR, 0x10000001)
        u.reg_write(UC_ARM_REG_SP, 0x20008000)
        stops = []
        def stop(uc, addr, size, _):
            stops.append(addr); uc.emu_stop()
        u.hook_add(UC_HOOK_CODE, stop, begin=RETURN, end=RETURN + 1)
        u.emu_start(SITE | 1, 0, count=200)
        sp = u.reg_read(UC_ARM_REG_SP)
        frame = struct.unpack('<4I', u.mem_read(sp, 16))           # push {r4,r5,r6,lr}
        regs = tuple(u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R4,
                                             UC_ARM_REG_R5, UC_ARM_REG_SP))
        return stops, regs, frame, calls

    def test_dormant_matches_stock(self):
        stock = self.run_site(self.stock)
        hooked = self.run_site(self.image)
        self.assertEqual(stock, hooked)
        from z30_extensions.features.modules.face_hook import RETURN
        self.assertEqual(stock[0], [RETURN])
        # stock state at 0x101944B8: r5 = xy, r4 = wh, r2 = 0xF0, frame r4,r5,r6,lr
        self.assertEqual(stock[1], (0x20001000, 0x20002000, 0xF0, 0x20003000, 0x20002000, 0x20008000 - 16))
        self.assertEqual(stock[2], (0x44, 0x55, 0x66, 0x10000001))

    def test_armed_calls_module_with_the_writer_arguments(self):
        stock = self.run_site(self.stock)
        stops, regs, frame, calls = self.run_site(self.image, 0x30000001)
        self.assertEqual((stops, regs, frame), stock[:3])          # identical stock state afterwards
        self.assertEqual(calls, [(0x20001000, 0x20002000, 0x20003000, 0)])   # args, 8-byte aligned stack

    def test_report_and_edit(self):
        self.assertIn('face-hook', self.report['enabled_features'])
        self.assertIn('gyro-evr', self.report['enabled_features'])
        names = [e['name'] for e in self.report['edits'] if e['name'] == 'detection-result-hook']
        self.assertEqual(names, ['detection-result-hook'])
