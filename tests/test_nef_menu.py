import importlib.util
import struct
import unittest

from z30_extensions.features.nef_menu import SITES, cave_source


@unittest.skipUnless(importlib.util.find_spec('unicorn') and importlib.util.find_spec('keystone'),
                     'Hook execution needs Unicorn and Keystone')
class NefMenuHookTests(unittest.TestCase):
    CODE, DATA, STACK = 0x20000000, 0x30000000, 0x40000000
    STATE_OFFSET = 0x100
    GET = 0x20001000
    ENTRY = 0x20000000
    OBJECT = 0x30008000
    PACKET = 0x30009000
    TARGET = 0x3000A000
    SNAPSHOT = 0x3000B000
    FLAGS = 0xB0000000

    def run_to_resume(self, site, selection, live, state_present=True, snapshot=None):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                       UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                                       UC_ARM_REG_R10, UC_ARM_REG_R12, UC_ARM_REG_LR, UC_ARM_REG_SP,
                                       UC_ARM_REG_CPSR, UC_ARM_REG_PC)
        from z30_extensions.assembly import assemble
        base = self.DATA if state_present else 0
        stub = assemble(f'movw r0,#{base & 0xFFFF};movt r0,#{base >> 16};bx lr;', self.GET, 32)
        cave = assemble(cave_source(site, self.GET, self.STATE_OFFSET), self.ENTRY, 192)
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(self.CODE, 0x10000)
        uc.mem_map(self.DATA, 0x20000)
        uc.mem_map(self.STACK, 0x10000)
        uc.mem_map(site['resume'] & ~0xFFF, 0x1000)
        uc.mem_write(site['resume'] & ~1, b'\x00\xbf\x00\xbf')
        uc.mem_write(self.ENTRY, cave)
        uc.mem_write(self.GET, stub)
        uc.mem_write(self.DATA + self.STATE_OFFSET, struct.pack('<I', selection))
        uc.mem_write(self.OBJECT + 3, bytes([live]))
        uc.mem_write(self.OBJECT + 4, bytes([0x66]))
        uc.mem_write(self.OBJECT + 0x25A, bytes([live]))
        uc.mem_write(self.OBJECT + 0x259, bytes([0x55]))
        uc.mem_write(self.SNAPSHOT + 0x22C, bytes([live]))
        sp = self.STACK + 0x8000
        uc.mem_write(sp + 4, struct.pack('<I', 0xABCDEF01))
        r1 = self.SNAPSHOT if site['kind'] == 'snapshot' else 0x11111111
        if snapshot is not None:
            r1 = snapshot
        registers = {UC_ARM_REG_R0: self.OBJECT, UC_ARM_REG_R1: r1, UC_ARM_REG_R2: 0x22222222,
                     UC_ARM_REG_R3: 0x33333333, UC_ARM_REG_R4: 0x44444444, UC_ARM_REG_R5: self.OBJECT,
                     UC_ARM_REG_R6: self.PACKET, UC_ARM_REG_R7: 0x77777777, UC_ARM_REG_R10: self.TARGET,
                     UC_ARM_REG_R12: 0xCCCCCCCC, UC_ARM_REG_LR: 0x20000F01, UC_ARM_REG_SP: sp}
        for register, value in registers.items():
            uc.reg_write(register, value)
        uc.reg_write(UC_ARM_REG_CPSR, (uc.reg_read(UC_ARM_REG_CPSR) & 0x0FFFFFFF) | self.FLAGS)
        flags_before = uc.reg_read(UC_ARM_REG_CPSR) & 0xF0000000
        uc.emu_start(self.ENTRY | 1, site['resume'], count=400)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC) & ~1, site['resume'] & ~1)
        return uc, registers, sp, flags_before

    def stored_mode(self, uc, site):
        address = site['address']
        if address == 0x101E77B8:
            return uc.mem_read(self.TARGET + 0x25A, 1)[0]
        if address == 0x10153A06:
            return uc.mem_read(self.PACKET + 0x24, 1)[0]
        if site['kind'] == 'snapshot':
            return uc.mem_read(self.SNAPSHOT + 0x22C, 1)[0]
        return uc.mem_read(self.OBJECT + 0x22C, 1)[0]

    def check_site(self, site, selection, live, expected, state_present=True):
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                       UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                                       UC_ARM_REG_R10, UC_ARM_REG_R12, UC_ARM_REG_LR, UC_ARM_REG_SP,
                                       UC_ARM_REG_CPSR)
        uc, registers, sp, flags_before = self.run_to_resume(site, selection, live, state_present)
        self.assertEqual(self.stored_mode(uc, site), expected, hex(site['address']))
        address = site['address']
        if address == 0x10153A06:
            self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 0x55)
        elif address == 0x1015F956:
            self.assertEqual(uc.reg_read(UC_ARM_REG_R0), 0xABCDEF01)
        elif address == 0x101E77B8:
            self.assertEqual(uc.reg_read(UC_ARM_REG_R1), 0x66)
        elif site['kind'] == 'snapshot':
            self.assertEqual(uc.reg_read(UC_ARM_REG_R4), self.SNAPSHOT)
            self.assertEqual(uc.reg_read(UC_ARM_REG_R5), 0)
            self.assertEqual(uc.reg_read(UC_ARM_REG_R6), self.OBJECT)
            self.assertEqual(uc.reg_read(UC_ARM_REG_SP), sp - 16 - 0x178)
            saved = struct.unpack('<I', uc.mem_read(sp - 4, 4))[0]
            self.assertEqual(saved, registers[UC_ARM_REG_LR])
            return
        else:
            self.assertEqual(uc.reg_read(UC_ARM_REG_R0), self.OBJECT)
            self.assertEqual(uc.reg_read(UC_ARM_REG_R1), expected)
        self.assertEqual(uc.reg_read(UC_ARM_REG_CPSR) & 0xF0000000, flags_before)
        self.assertEqual(uc.reg_read(UC_ARM_REG_SP), sp)
        for register in (UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
                         UC_ARM_REG_R7, UC_ARM_REG_R10, UC_ARM_REG_R12, UC_ARM_REG_LR):
            self.assertEqual(uc.reg_read(register), registers[register], register)
        if address in (0x101536A0, 0x1015F956):
            self.assertEqual(uc.reg_read(UC_ARM_REG_R1), expected)

    def test_lossless_selection_keeps_the_live_value(self):
        for site in SITES:
            for live in (0, 1, 2):
                self.check_site(site, 0, live, live)

    def test_compressed_selection_forces_mode_two(self):
        for site in SITES:
            for live in (0, 1, 2):
                self.check_site(site, 1, live, 2)

    def test_uncompressed_selection_forces_mode_zero(self):
        for site in SITES:
            for live in (0, 1, 2):
                self.check_site(site, 2, live, 0)

    def test_uninitialized_state_keeps_the_live_value(self):
        for site in SITES:
            self.check_site(site, 2, 1, 1, state_present=False)

    def test_out_of_range_selection_keeps_the_live_value(self):
        for site in SITES:
            for selection in (3, 7, 0xFFFFFFFF):
                self.check_site(site, selection, 1, 1)

    def test_snapshot_hook_skips_a_null_snapshot(self):
        from unicorn.arm_const import UC_ARM_REG_R4
        site = [s for s in SITES if s['kind'] == 'snapshot'][0]
        for selection in (1, 2):
            uc, registers, sp, flags_before = self.run_to_resume(site, selection, 1, snapshot=0)
            self.assertEqual(uc.reg_read(UC_ARM_REG_R4), 0)
            self.assertEqual(uc.mem_read(self.SNAPSHOT + 0x22C, 1)[0], 1)

    def test_every_hook_window_matches_the_jump_length(self):
        from z30_extensions.assembly import jump
        for site in SITES:
            self.assertEqual(len(jump(site['address'], 0x12080000, len(site['expected']))),
                             len(site['expected']))
            self.assertEqual(site['resume'], site['address'] + len(site['expected']))

    def test_every_site_of_the_fixed_presets_has_a_menu_hook(self):
        from z30_extensions.features.nef_compression import SITES as FIXED
        fixed = {address for address, _, _, _, _ in FIXED}
        hooked = {site['address'] for site in SITES}
        self.assertTrue({0x101E77B8, 0x101536A0, 0x10153A06, 0x1015F956} <= hooked)
        self.assertEqual(hooked - fixed, {0x101436C2})
        self.assertEqual(fixed - hooked, {0x10143468, 0x101434B6})


if __name__ == '__main__':
    unittest.main()
