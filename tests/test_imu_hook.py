"""Execute the dormant IMU-task hook against the stock call and branches."""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'Z_30_0120.bin'

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_R5,
                                   UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_LR)
except ImportError:  # pragma: no cover
    Uc = None


@unittest.skipUnless(FIRMWARE.exists() and Uc, 'needs Z_30_0120.bin and Unicorn')
class ImuHookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        from z30_extensions.build import build
        cls.stock = extract_eg(FIRMWARE.read_bytes())
        cls.image, cls.report = build(cls.stock, 'shutter-sound', module_host=True,
                                      evr_hook=True, imu_hook=True)

    def run_site(self, image, stock_result, cell=0):
        from z30_extensions.features.modules.imu_hook import SITE, SUCCESS, FAILURE, ACCEL_READ, IMU_CELL
        from z30_extensions.platform.z30_120 import BASE
        u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(BASE, (len(image) + 0xFFFF) & ~0xFFFF)
        u.mem_write(BASE, image)
        u.mem_map(0xC1832000, 0x1000)
        u.mem_map(0x20000000, 0x10000)
        u.mem_map(0x30000000, 0x1000)
        u.mem_write(IMU_CELL, struct.pack('<I', cell))
        calls = []
        def fake_read(uc, addr, size, _):
            calls.append(('stock', uc.reg_read(UC_ARM_REG_R0)))
            uc.reg_write(UC_ARM_REG_R0, stock_result)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, fake_read, begin=ACCEL_READ, end=ACCEL_READ + 1)
        def module_cb(uc, addr, size, _):
            calls.append(('module', uc.reg_read(UC_ARM_REG_R0)))
            uc.reg_write(UC_ARM_REG_R0, 0x5555)          # clobber: hook must restore
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, module_cb, begin=0x30000000, end=0x30000001)
        u.reg_write(UC_ARM_REG_R0, 0x20001020)
        u.reg_write(UC_ARM_REG_R4, 0x20004444)
        u.reg_write(UC_ARM_REG_R5, 0x20005555)
        u.reg_write(UC_ARM_REG_SP, 0x20008000)
        stops = []
        def stop(uc, addr, size, _):
            stops.append(addr); uc.emu_stop()
        for a in (SUCCESS, FAILURE):
            u.hook_add(UC_HOOK_CODE, stop, begin=a, end=a + 1)
        u.emu_start(SITE | 1, 0, count=200)
        regs = (u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R4),
                u.reg_read(UC_ARM_REG_R5), u.reg_read(UC_ARM_REG_SP))
        return stops, regs, calls

    def test_dormant_matches_stock(self):
        for result in (1, 0):
            stock = self.run_site(self.stock, result)
            hooked = self.run_site(self.image, result)
            self.assertEqual(stock, hooked)
            self.assertEqual(len(hooked[0]), 1)

    def test_armed_keeps_stock_result_and_branch(self):
        from z30_extensions.features.modules.imu_hook import SUCCESS, FAILURE
        for result, target in ((1, SUCCESS), (0, FAILURE)):
            stops, regs, calls = self.run_site(self.image, result, 0x30000001)
            self.assertEqual(stops, [target])
            self.assertEqual(regs, (result, 0x20004444, 0x20005555, 0x20008000))
            self.assertEqual(calls, [('stock', 0x20001020), ('module', result)])

    def test_only_expected_edit(self):
        names = [e['name'] for e in self.report['edits'] if e['name'] in ('evr-result-hook', 'imu-task-hook')]
        self.assertEqual(sorted(names), ['evr-result-hook', 'imu-task-hook'])


if __name__ == '__main__':
    unittest.main()
