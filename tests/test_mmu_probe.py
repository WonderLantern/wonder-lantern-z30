"""Offline execution of the mmu-probe research module (Unicorn, optional)."""
import struct
import unittest

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_LR,
                                   UC_ARM_REG_PC, UC_ARM_REG_SP, UC_ARM_REG_CPSR)
    import keystone  # noqa: F401
except ImportError:  # optional research dependencies
    Uc = None

from z30_extensions.modules import parse, ABI, API_BYTES

TABLE = 0x00000000      # TTBR0 resets to 0 in Unicorn, so the L1 table lives here
API = 0x00200000        # host API table; its L1 slot is TABLE + 8
STUBS = 0x00300000      # get_property, set_text, set_handler, return
CODE = 0x00400000
CONTEXT = 0x00500000
STACK = 0x00610000
EG_DESC, API_DESC = 0x1000C426, 0x0020C426


@unittest.skipUnless(Uc, 'needs unicorn and keystone')
class MmuProbeTests(unittest.TestCase):
    VERSION = 1

    def setUp(self):
        from z30_extensions.mmu_probe import build
        module = parse(build(self.VERSION))
        uc = self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(0, 0x700000)
        uc.mem_write(TABLE + (0x10020000 >> 20) * 4, struct.pack('<I', EG_DESC))
        uc.mem_write(TABLE + (API >> 20) * 4, struct.pack('<I', API_DESC))
        uc.mem_write(API, struct.pack('<5I', ABI, API_BYTES, 0x12345679,
                                      STUBS + 5, STUBS + 9))
        uc.mem_write(STUBS, bytes.fromhex('7047' * 8))  # bx lr
        uc.mem_write(CODE, module['code'])
        self.prepare(uc)
        self.entry = CODE + module['entry']
        self.texts, self.handler = [], None
        uc.hook_add(UC_HOOK_CODE, self._stub, begin=STUBS, end=STUBS + 15)

    def prepare(self, uc):
        pass

    def _stub(self, uc, address, size, user):
        r0 = uc.reg_read(UC_ARM_REG_R0)
        if address == STUBS + 4:
            self.texts.append(uc.mem_read(r0, 64).split(b'\0')[0].decode())
        elif address == STUBS + 8:
            self.handler = r0
        else:
            return
        uc.reg_write(UC_ARM_REG_R0, 1)

    def call(self, address):
        uc = self.uc
        uc.reg_write(UC_ARM_REG_R0, API)
        uc.reg_write(UC_ARM_REG_R1, CONTEXT)
        uc.reg_write(UC_ARM_REG_SP, STACK)
        uc.reg_write(UC_ARM_REG_LR, STUBS + 12 + 1)
        uc.emu_start(address | 1, STUBS + 12, count=20000)
        return uc.reg_read(UC_ARM_REG_R0)

    def test_init_then_seven_lines_and_restore(self):
        self.assertEqual(self.call(self.entry), 1)
        self.assertEqual(self.texts, ['mmu-probe ready'])
        self.assertTrue(self.handler & 1)
        mode = self.uc.reg_read(UC_ARM_REG_CPSR) & 0x1F
        for _ in range(8):
            self.call(self.handler)
        lines = self.texts[1:]
        if mode == 0x10:
            self.assertEqual(set(lines), {'USR mode'})
            return
        self.assertEqual([line.split(' ')[0] for line in lines], list('12345671'))
        self.assertTrue(all(len(line) <= 24 for line in lines), lines)
        self.assertEqual(lines[3], f'4 E={EG_DESC:08X} T={API_DESC:08X}')
        self.assertEqual(lines[5], '6 O=12345679 V=EDCBA986')
        self.assertEqual(lines[6], '7 F=12345679')
        self.assertEqual(struct.unpack('<I', self.uc.mem_read(API + 8, 4))[0], 0x12345679)

    def test_wrong_abi_rejected(self):
        self.uc.mem_write(API, struct.pack('<I', ABI + 1))
        self.assertEqual(self.call(self.entry), 0)
        self.assertIsNone(self.handler)



BOOT_WORD = 0x10031000


@unittest.skipUnless(Uc, 'needs unicorn and keystone')
class MmuProbeV2Tests(MmuProbeTests):
    VERSION = 2
    LAST = 0x120  # the DRAM copy covers sections 0x100..0x120

    def prepare(self, uc):
        for index in range(0x100, self.LAST + 1):
            uc.mem_write(TABLE + index * 4, struct.pack('<I', 0xCC40C426 + ((index - 0x100) << 20)))
        uc.mem_write(TABLE + (self.LAST + 1) * 4, struct.pack('<I', 0x1210C426))
        uc.mem_write(TABLE + 0xCC4 * 4, struct.pack('<I', 0xCC414426))
        uc.mem_map(BOOT_WORD & ~0xFFFF, 0x10000)
        uc.mem_write(BOOT_WORD, struct.pack('<I', 0x40000000))

    def test_init_then_seven_lines_and_restore(self):
        self.assertEqual(self.call(self.entry), 1)
        self.assertEqual(self.texts, ['mmu-probe v2 ready'])
        for _ in range(6):
            self.call(self.handler)
        lines = self.texts[1:]
        self.assertTrue(all(len(line) <= 24 for line in lines), lines)
        self.assertEqual(lines[0], f'1 A=00000000 L={self.LAST:08X}')
        self.assertEqual(lines[1], '2 N=1210C426 P=CC414426')
        # Unicorn runs with the MMU off, so PAR returns the VA itself.
        # PAR bits [11:0] are attributes; bit 0 = 0 means the translation succeeded.
        self.assertRegex(lines[2], r'^3 Q=10031[0-9A-F]{2}[02468ACE] G=10031[0-9A-F]{2}[02468ACE]$')
        self.assertEqual(lines[3], '4 O=40000000 V=BFFFFFFF')
        self.assertEqual(lines[4], '5 V=BFFFFFFF F=40000000')
        self.assertEqual(lines[5][0], '1')
        self.assertEqual(struct.unpack('<I', self.uc.mem_read(BOOT_WORD, 4))[0], 0x40000000)


if __name__ == '__main__':
    unittest.main()
