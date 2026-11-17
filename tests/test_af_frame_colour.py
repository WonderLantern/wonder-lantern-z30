"""AF frame colour (af-frame-colour, v51): the stock style table stays untouched, the
edited copy carries exactly the planned entries, and the lookup hook picks the copy
when the photo-menu switch is on (Enable, the default) and the stock table when off."""
import struct, unittest
from pathlib import Path

from z30_extensions.build import build, resolve_presets
from z30_extensions.features import af_frame_colour as fc

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Z_30_0120.bin'

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R12,
                                   UC_ARM_REG_SP, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_CPSR)
except ImportError:  # pragma: no cover
    Uc = None


class PresetTest(unittest.TestCase):
    def test_in_full_and_brings_the_menu_framework(self):
        self.assertIn('af-frame-colour', resolve_presets('full')[1])
        self.assertEqual(resolve_presets('af-frame-colour')[1], ['shutter-sound', 'af-frame-colour'])

    def test_page_is_a_native_photo_row(self):
        from z30_extensions.features.shutter_sound import ROOT_ORDER
        from z30_extensions.features.sound_layout import NATIVE_MENUS
        self.assertNotIn('afcol', ROOT_ORDER)
        self.assertIn('afcol', dict((m[0], m[3]) for m in NATIVE_MENUS)['photo'])


@unittest.skipUnless(SOURCE.exists(), 'needs Z_30_0120.bin')
class TableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        cls.stock = extract_eg(SOURCE.read_bytes())
        cls.image, cls.report = build(cls.stock, 'full')

    def words(self, image, address):
        from z30_extensions.platform.z30_120 import BASE
        return list(struct.unpack_from('<%dH' % (29 * fc.COLUMNS), image, address - BASE))

    def test_stock_table_untouched_and_copy_edited(self):
        control = self.report['features']['af_frame_colour_control']
        stock, kept = self.words(self.stock, fc.TABLE), self.words(self.image, fc.TABLE)
        self.assertEqual(stock, kept)
        copy = self.words(self.image, int(control['table'], 16))
        planned = {r * fc.COLUMNS + c: (s, n) for r, c, s, n in fc.EDITS}
        for i, (a, b) in enumerate(zip(stock, copy)):
            self.assertEqual((a, b), planned.get(i, (a, a)), i)

    def test_targets(self):
        copy = self.words(self.image, int(self.report['features']['af_frame_colour_control']['table'], 16))
        at = lambda r, c: copy[r * fc.COLUMNS + c]
        for base in (fc.AF_S, fc.AF_C):
            for col in (fc.SUBJECT, fc.SCREEN1, fc.TRACKING):
                self.assertEqual(at(base + fc.IDLE, col), fc.WHITE)
                self.assertEqual(at(base + fc.IN_FOCUS, col), fc.GREEN)
                self.assertEqual(at(base + 4, col), 0x0204)
        self.assertEqual(at(fc.AF_C + fc.IN_FOCUS, fc.PLAIN_AUTO), fc.GREEN)
        for col in (fc.SUBJECT, fc.SCREEN1, fc.TRACKING):
            self.assertEqual(at(fc.AF_F + fc.IDLE, col), fc.WHITE)

    @unittest.skipUnless(Uc, 'needs Unicorn')
    def test_lookup_hook_switches_tables_and_keeps_the_stock_contract(self):
        from z30_extensions.platform.z30_120 import BASE
        from z30_extensions.features import af_frame_colour_ui as ui
        control = self.report['features']['af_frame_colour_control']
        choice = int(control['choice'], 16) & ~1
        for setting, table in ((ui.ENABLE, int(control['table'], 16)), (ui.DISABLE, fc.TABLE)):
            for column in (5, 8):
                u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
                u.mem_map(BASE, (len(self.image) + 0xFFFF) & ~0xFFFF); u.mem_write(BASE, self.image)
                u.mem_map(0x20000000, 0x10000)
                def stub(uc, addr, size, _):
                    for reg in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R12):
                        uc.reg_write(reg, 0xDEAD)
                    uc.reg_write(UC_ARM_REG_R0, setting); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
                u.hook_add(UC_HOOK_CODE, stub, begin=choice, end=choice + 1)
                stop = []
                u.hook_add(UC_HOOK_CODE, lambda uc, a, s, _: (stop.append(1), uc.emu_stop()),
                           begin=ui.LOOKUP_RETURN, end=ui.LOOKUP_RETURN + 1)
                u.reg_write(UC_ARM_REG_R0, 0xC1F8F12C); u.reg_write(UC_ARM_REG_R2, 3); u.reg_write(UC_ARM_REG_R12, column)
                u.reg_write(UC_ARM_REG_LR, 0x1039C001); u.reg_write(UC_ARM_REG_SP, 0x20008000)
                u.emu_start(ui.LOOKUP_SITE | 1, 0, count=500)
                self.assertTrue(stop)
                r0, r1, r2, r3, r12, lr, sp = (u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                                                         UC_ARM_REG_R12, UC_ARM_REG_LR, UC_ARM_REG_SP))
                self.assertEqual((r0, r1, r2, r3, r12, lr, sp), (0xC1F8F12C, table, 3 * 11, 9, column, 0x1039C001, 0x20008000))
                z = (u.reg_read(UC_ARM_REG_CPSR) >> 30) & 1
                self.assertEqual(z, int(column == 5))          # flags of the displaced cmp.w ip,#5


if __name__ == '__main__':
    unittest.main()
