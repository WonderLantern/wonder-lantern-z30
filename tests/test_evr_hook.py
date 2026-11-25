"""Execute the dormant e-VR hook against stock instructions with Unicorn."""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'Z_30_0120.bin'

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_SP,
                                   UC_ARM_REG_PC, UC_ARM_REG_CPSR)
except ImportError:  # pragma: no cover
    Uc = None


@unittest.skipUnless(FIRMWARE.exists() and Uc, 'needs Z_30_0120.bin and Unicorn')
class EvrHookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        from z30_extensions.build import build
        cls.stock = extract_eg(FIRMWARE.read_bytes())
        cls.image, cls.report = build(cls.stock, 'shutter-sound', module_host=True, evr_hook=True)

    def run_site(self, image, cell_value, callback=None):
        from z30_extensions.features.modules.evr_hook import SITE, RETURN, HOOK_CELL
        from z30_extensions.platform.z30_120 import BASE
        u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        size = (len(image) + 0xFFFF) & ~0xFFFF
        u.mem_map(BASE, size)
        u.mem_write(BASE, image)
        u.mem_map(0xC1832000, 0x1000)            # hook cell page
        u.mem_map(0xC2A01000, 0x1000)            # e-VR context holder
        u.mem_map(0x20000000, 0x10000)           # stack, input, context, metadata
        u.mem_map(0x30000000, 0x1000)            # module callback
        u.mem_write(HOOK_CELL, struct.pack('<I', cell_value))
        stack, inp, ctx, meta = 0x20008000, 0x20001000, 0x20002000, 0x20003000
        u.mem_write(inp + 0x34, struct.pack('<I', 0x11))
        u.mem_write(0xC2A01D08 + 4, struct.pack('<I', ctx))
        u.mem_write(ctx + 0x144, struct.pack('<I', meta))
        if callback:
            u.mem_write(0x30000000, callback)
        u.reg_write(UC_ARM_REG_R4, inp)
        u.reg_write(UC_ARM_REG_R5, 0xC2A01D08)
        u.reg_write(UC_ARM_REG_SP, stack)
        u.emu_start(SITE | 1, RETURN, count=200)
        self.assertEqual(u.reg_read(UC_ARM_REG_PC), RETURN)
        return u, stack, inp, meta

    def regs(self, u):
        return [u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R4,
                                        UC_ARM_REG_R5, UC_ARM_REG_SP)] + [u.reg_read(UC_ARM_REG_CPSR) & 0xF0000000]

    def test_only_expected_edit(self):
        names = [e['name'] for e in self.report['edits'] if 'evr' in e['name']]
        self.assertEqual(sorted(names), ['evr-result-hook', 'evr-result-hook-path2'])

    def run_path2(self, image, cell_value, callback=None, channel=2):
        from z30_extensions.features.modules.evr_hook import SITE2, RETURN2, HOOK_CELL, LOGGER
        from z30_extensions.platform.z30_120 import BASE
        u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(BASE, (len(image) + 0xFFFF) & ~0xFFFF)
        u.mem_write(BASE, image)
        u.mem_map(0xC1832000, 0x1000); u.mem_map(0xC2A01000, 0x1000)
        u.mem_map(0x20000000, 0x10000); u.mem_map(0x30000000, 0x1000)
        u.mem_write(HOOK_CELL, struct.pack('<I', cell_value))
        stack, inp, ctx, meta = 0x20008000, 0x20001000, 0x20002000, 0x20003000
        u.mem_write(inp, struct.pack('<I', channel))
        u.mem_write(0xC2A01D08 + 4 * channel, struct.pack('<I', ctx))
        u.mem_write(ctx + 0x144, struct.pack('<I', meta))
        if callback:
            u.mem_write(0x30000000, callback)
        logged = []
        from unicorn import UC_HOOK_CODE
        from unicorn.arm_const import UC_ARM_REG_LR
        def logger(uc, addr, size, _):
            logged.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, logger, begin=LOGGER, end=LOGGER + 1)
        u.reg_write(UC_ARM_REG_R5, inp)
        u.reg_write(UC_ARM_REG_SP, stack)
        u.emu_start(SITE2 | 1, RETURN2, count=300)
        self.assertEqual(u.reg_read(UC_ARM_REG_PC), RETURN2)
        self.assertEqual(logged, [(stack, channel)])
        self.assertEqual(u.reg_read(UC_ARM_REG_R5), inp)
        self.assertEqual(u.reg_read(UC_ARM_REG_SP), stack)
        return u, stack, inp, meta

    def test_path2_dormant_and_armed(self):
        self.run_path2(self.image, 0)
        # str r0,[r1,#0x28]; str r2,[r1,#0x2c]; str r3,[r1,#0x30]; bx lr
        cb = bytes.fromhex('8862 ca62 0b63 7047')
        u, stack, inp, meta = self.run_path2(self.image, 0x30000001, cb)
        self.assertEqual(struct.unpack('<3I', u.mem_read(stack + 0x28, 12)), (inp, meta, 2))
        u, stack, inp, _ = self.run_path2(self.image, 0x30000001, cb, channel=7)
        self.assertEqual(struct.unpack('<3I', u.mem_read(stack + 0x28, 12)), (inp, 0, 2))

    def test_dormant_matches_stock(self):
        stock, *_ = self.run_site(self.stock, 0)
        hooked, *_ = self.run_site(self.image, 0)
        self.assertEqual(self.regs(stock), self.regs(hooked))
        self.assertEqual(hooked.reg_read(UC_ARM_REG_R0), 0x100)
        self.assertEqual(hooked.reg_read(UC_ARM_REG_R1), 0x1100)

    def test_armed_passes_arguments_and_result_edits(self):
        # str r0,[r1,#0x28]; str r2,[r1,#0x2c]; str r3,[r1,#0x30]; mov r0,#0x55; mov r1,#0x66; bx lr
        callback = bytes.fromhex('8862 ca62 0b63 5520 6621 7047')
        u, stack, inp, meta = self.run_site(self.image, 0x30000001, callback)
        result = stack + 0x84
        self.assertEqual(struct.unpack('<3I', u.mem_read(result + 0x28, 12)), (inp, meta, 1))
        # Displaced stock instructions still define r0/r1 after the callback.
        self.assertEqual(u.reg_read(UC_ARM_REG_R0), 0x100)
        self.assertEqual(u.reg_read(UC_ARM_REG_R1), 0x1100)
        self.assertEqual(u.reg_read(UC_ARM_REG_R4), inp)
        self.assertEqual(u.reg_read(UC_ARM_REG_SP), stack)

    def test_requires_module_host(self):
        from z30_extensions.build import build
        with self.assertRaises(ValueError):
            build(self.stock, 'shutter-sound', evr_hook=True)


if __name__ == '__main__':
    unittest.main()
