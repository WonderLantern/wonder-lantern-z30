"""Offline execution of the focal-length gate hook (Unicorn fixture, optional)."""
import struct
import unittest
from pathlib import Path

try:
    import unicorn  # noqa: F401
    import keystone  # noqa: F401
except ImportError:  # optional research dependencies
    unicorn = None

SOURCE = Path(__file__).resolve().parents[1] / 'Z_30_0120.bin'


@unittest.skipUnless(unicorn and SOURCE.exists(), 'needs unicorn, keystone and the stock container')
class FocalLengthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
        from z30_extensions.build import build
        from z30_extensions.container import extract_eg
        cls.stock = extract_eg(SOURCE.read_bytes())
        cls.image, cls.report = build(cls.stock, 'focal-length')

    def run_hook(self, element_value):
        from native_runtime import Context, STACK
        from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_R0
        from z30_extensions.features.photo import focal_length as fl
        cx = Context(self.stock, self.image)
        u = cx.u
        descriptor = struct.unpack('<I', cx.stock.read(fl.TABLE + fl.WIDGET * 4, 4))[0]
        element = 0xC1850000
        u.mem_write(descriptor + 0x30, struct.pack('<I', element if element_value is not None else 0))
        u.mem_write(descriptor + 0x2C, b'\x01' if element_value is not None else b'\x00')
        u.mem_write(element, bytes(4))
        u.reg_write(UC_ARM_REG_SP, STACK + 0x80000)
        u.emu_start(fl.SITE | 1, fl.RESUME, count=500)
        return bytes(u.mem_read(element, 4)), u.reg_read(UC_ARM_REG_PC)

    def test_show_byte_is_set_and_flow_resumes_at_the_stock_branch(self):
        data, pc = self.run_hook(1)
        self.assertEqual(data[1], 1)
        self.assertEqual(pc, 0x1032AC60)

    def test_null_element_pointer_is_left_alone(self):
        data, pc = self.run_hook(None)
        self.assertEqual(data, bytes(4))
        self.assertEqual(pc, 0x1032AC60)

    def test_only_the_gate_window_changes(self):
        base = 0x10020000
        changed = [i for i in range(len(self.stock) - 2) if self.stock[i] != self.image[i]]
        self.assertTrue(changed)
        self.assertGreaterEqual(min(changed) + base, 0x1032AC58)
        self.assertLessEqual(max(changed) + base, 0x1032AC5F)


if __name__ == '__main__':
    unittest.main()
