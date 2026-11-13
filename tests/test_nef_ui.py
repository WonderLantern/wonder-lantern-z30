import importlib.util
import struct
import unittest

from z30_extensions.features import nef_ui as nu


class FakePlan:
    BASE = 0x12000000

    def __init__(self):
        self.memory = bytearray()
        self.features = {}

    def alloc(self, raw):
        address = self.BASE + len(self.memory)
        self.memory.extend(raw + bytes(-len(raw) % 4))
        return address

    def put(self, address, raw):
        offset = address - self.BASE
        self.memory[offset:offset + len(raw)] = raw


class FakeResources:
    def __init__(self):
        self.names = {}

    def alloc(self, text):
        address = 0x13000000 + 16 * len(self.names)
        self.names[address] = text
        return address


@unittest.skipUnless(importlib.util.find_spec('unicorn') and importlib.util.find_spec('keystone'),
                     'Menu page execution needs Unicorn and Keystone')
class NefPageTests(unittest.TestCase):
    STATE_BASE = 0x30000000
    ALLOCATION = 0x1000
    IDS = (0x2401, 0x2402, 0x2403)
    STUBS = dict(get=0x14000000, ensure=0x14000100, text=0x14000200, show_error=0x14000300,
                 page_return=0x14000400)
    NATIVE_OPEN, NATIVE_FOCUS = 0x1045F732, 0x1045D54C

    def build(self):
        from z30_extensions.assembly import assemble
        plan, resources = FakePlan(), FakeResources()
        functions = dict(self.STUBS)
        extension = nu.prepare(plan, functions, resources, self.ALLOCATION)
        nu.finish(plan, functions, extension, self.IDS)
        self.functions, self.extension, self.resources, self.plan = functions, extension, resources, plan
        self.assemble = assemble

    def machine(self, selection=None, save_result=1, state_present=True):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP
        self.build()
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(FakePlan.BASE, 0x100000)
        uc.mem_write(FakePlan.BASE, bytes(self.plan.memory))
        uc.mem_map(self.STATE_BASE, 0x20000)
        uc.mem_map(0x14000000, 0x1000)
        uc.mem_map(0x1045F000, 0x1000)
        uc.mem_map(0x1045D000, 0x1000)
        uc.mem_map(0x40000000, 0x10000)
        base = self.STATE_BASE if state_present else 0
        uc.mem_write(self.functions['get'], self.assemble(
            f'movw r0,#{base & 0xFFFF};movt r0,#{base >> 16};bx lr;', self.functions['get'], 32))
        uc.mem_write(self.functions['ensure'], self.assemble('movs r0,#1;bx lr;', self.functions['ensure'], 16))
        uc.mem_write(self.functions['text'], bytes.fromhex('7047'))
        uc.mem_write(self.functions['show_error'], bytes.fromhex('7047'))
        uc.mem_write(self.functions['page_return'], bytes.fromhex('7047'))
        uc.mem_write(self.NATIVE_OPEN, bytes.fromhex('7047'))
        uc.mem_write(self.NATIVE_FOCUS, bytes.fromhex('7047'))
        uc.mem_write(self.functions['nef_save'] & ~1, self.assemble(
            f'movs r0,#{save_result};bx lr;', self.functions['nef_save'], 16))
        if selection is not None:
            uc.mem_write(self.STATE_BASE + self.extension['state_offset'], struct.pack('<I', selection))
        self.calls = []
        watched = {self.functions['text'] & ~1: 'text', self.functions['show_error'] & ~1: 'error', self.functions['page_return'] & ~1: 'return',
                   self.NATIVE_OPEN: 'open', self.NATIVE_FOCUS: 'focus'}
        def record(emu, address, size, user):
            if address in watched:
                self.calls.append((watched[address], emu.reg_read(UC_ARM_REG_R0),
                                   emu.reg_read(UC_ARM_REG_R1), emu.reg_read(UC_ARM_REG_R2)))
        uc.hook_add(UC_HOOK_CODE, record)
        uc.reg_write(UC_ARM_REG_SP, 0x40008000)
        return uc

    def call(self, uc, name, r0=0):
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_LR
        uc.reg_write(UC_ARM_REG_R0, r0)
        uc.reg_write(UC_ARM_REG_LR, 0x40000F01)
        uc.emu_start(self.functions[name] | 1, 0x40000F00, count=5000)
        return uc.reg_read(UC_ARM_REG_R0)

    def stored_selection(self, uc):
        return struct.unpack('<I', uc.mem_read(self.STATE_BASE + self.extension['state_offset'], 4))[0]

    def event(self, uc, first, second=0):
        uc.mem_write(self.STATE_BASE + 0x8000 + 22, struct.pack('<HH', first, second))
        return self.STATE_BASE + 0x8000

    def test_get_returns_state_or_null(self):
        uc = self.machine()
        self.assertEqual(self.call(uc, 'nef_get'), self.STATE_BASE + self.extension['state_offset'])
        uc = self.machine(state_present=False)
        self.assertEqual(self.call(uc, 'nef_get'), 0)

    def test_labels_fill_every_slot_for_each_selection(self):
        for selection in (0, 1, 2):
            uc = self.machine(selection)
            self.call(uc, 'nef_labels')
            names = self.resources.names
            labels = ('Lossless', 'Compressed', 'Uncompressed')
            expected = [('NEF compression', nu.TITLE_SLOT), ('NEF compression', nu.ROOT_SLOT)]
            for i, label in enumerate(labels):
                expected += [(label, nu.LABEL_SLOT + i), ('*' if i == selection else '', nu.MARK_SLOT + i)]
            expected.append((labels[selection], nu.CURRENT_SLOT))
            actual = [(names[r0], r1) for kind, r0, r1, r2 in self.calls if kind == 'text']
            self.assertEqual(actual, expected)
            self.assertTrue(all(r2 == 0 for kind, r0, r1, r2 in self.calls))

    def test_labels_default_to_lossless_without_state_or_with_bad_value(self):
        for kwargs in (dict(selection=0, state_present=False), dict(selection=9)):
            uc = self.machine(**kwargs)
            self.call(uc, 'nef_labels')
            last = [(self.resources.names[r0], r1) for kind, r0, r1, r2 in self.calls if kind == 'text'][-1]
            self.assertEqual(last, ('Lossless', nu.CURRENT_SLOT))

    def test_open_focuses_the_row_of_the_current_selection(self):
        for selection in (0, 1, 2):
            uc = self.machine(selection)
            self.call(uc, 'nef_open')
            self.assertEqual([c[0] for c in self.calls], ['open', 'focus'])
            self.assertEqual(self.calls[0][1:3], (2, nu.PAGE_INDEX))
            page = self.STATE_BASE + self.extension['page_offset']
            self.assertEqual(self.calls[1][1:3], (page, self.IDS[selection]))

    def test_action_stores_each_row_and_returns_to_the_root(self):
        for row in (0, 1, 2):
            for split in ((row, 0), (0, row)):
                uc = self.machine(selection=0)
                event = self.event(uc, *split)
                self.call(uc, 'nef_action', event)
                self.assertEqual(self.stored_selection(uc), row)
                self.assertEqual([c[0] for c in self.calls], ['return'])
                self.assertEqual(self.calls[0][1], event)

    def test_action_ignores_rows_outside_the_page(self):
        uc = self.machine(selection=1)
        self.call(uc, 'nef_action', self.event(uc, 3))
        self.assertEqual(self.stored_selection(uc), 1)
        self.assertEqual(self.calls, [])

    def test_action_reports_a_failed_save_without_leaving_the_page(self):
        uc = self.machine(selection=0, save_result=0)
        event = self.event(uc, 2)
        self.call(uc, 'nef_action', event)
        self.assertEqual(self.stored_selection(uc), 2)
        self.assertEqual([c[0] for c in self.calls], ['error'])
        self.assertEqual(self.calls[0][1], event)

    def test_action_without_state_changes_nothing(self):
        uc = self.machine(selection=1, state_present=False)
        self.call(uc, 'nef_action', self.event(uc, 2))
        self.assertEqual(self.calls, [])

    def test_page_indices_are_unique_across_coexisting_pages(self):
        from z30_extensions.features import (aspect_ui, bitrate_ui, date_stamp_ui, module_host,
                                             portrait_menu, sound_layout, zebra_ui)
        indices = [sound_layout.PAGE_INDEX, sound_layout.FILES_INDEX, sound_layout.SWITCH_INDEX,
                   sound_layout.POPUP_INDEX, sound_layout.SOUND_GROUP_INDEX, module_host.PAGE_INDEX,
                   bitrate_ui.PAGE_INDEX, aspect_ui.PAGE_INDEX, zebra_ui.PAGE_INDEX,
                   date_stamp_ui.PAGE_INDEX, portrait_menu.PAGE_INDEX, nu.PAGE_INDEX]
        self.assertEqual(len(indices), len(set(indices)))

    def test_report_describes_the_control(self):
        self.build()
        control = self.plan.features['nef_compression_control']
        self.assertEqual(control['default'], 0)
        self.assertEqual(control['page_index'], 966)
        self.assertEqual(control['settings_file'], 'WL-NEF.CFG')
        self.assertTrue(control['physical_verified'])


if __name__ == '__main__':
    unittest.main()
