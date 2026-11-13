import unittest

from z30_extensions.features import date_stamp_ui, menu_slots, module_host, portrait_menu, sound_layout, zebra_ui
from z30_extensions.features.menu_slots import BLOCKS, STOCK_SLOTS, page_slots


class MenuSlotTests(unittest.TestCase):
    def test_blocks_avoid_each_other_and_stock_text(self):
        owned = [slot for base, size in BLOCKS.values() for slot in range(base, base + size)]
        self.assertEqual(len(owned), len(set(owned)))
        self.assertFalse(STOCK_SLOTS.intersection(owned))

    def test_blocks_fit_the_eight_bit_immediates_of_owned_code(self):
        self.assertLess(max(base + size for base, size in BLOCKS.values()), menu_slots.SLOT_COUNT + 1)
        self.assertLessEqual(menu_slots.SLOT_COUNT, 256)

    def test_page_layout_is_title_root_labels_marks_current(self):
        self.assertEqual(page_slots('zebra', 2), (118, 119, 120, 122, 124))

    def test_a_page_cannot_outgrow_its_block(self):
        with self.assertRaises(ValueError):
            page_slots('zebra', 3)

    def test_every_owned_slot_stays_inside_its_block(self):
        base, size = BLOCKS['stamp']
        self.assertLess(date_stamp_ui.CURRENT_SLOT, base + size)
        base, size = BLOCKS['zebra']
        self.assertLess(zebra_ui.CURRENT_SLOT, base + size)
        base, size = BLOCKS['portrait']
        self.assertLess(portrait_menu.CURRENT_SLOT, base + size)
        base, size = BLOCKS['modules']
        self.assertLess(module_host.STATUS_SLOT, base + size)
        base, size = BLOCKS['sound']
        self.assertLess(sound_layout.POPUP_OK_SLOT, base + size)


if __name__ == '__main__':
    unittest.main()
