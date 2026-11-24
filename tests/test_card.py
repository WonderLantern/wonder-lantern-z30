"""Card patches: relocation, file format and the reference loader."""
import struct
import unittest
from pathlib import Path

from z30_extensions import card

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Z_30_0120.bin'
try:
    import keystone  # noqa: F401
except ImportError:  # optional build dependency
    keystone = None


class RelocationTests(unittest.TestCase):
    def test_abs32_and_movt_roundtrip(self):
        # movw r0,#0x1234; movt r0,#0x2000; .word 0x20000101
        code = bytes.fromhex('41f23420') + struct.pack('<HH', 0xF2C2, 0x0000) + struct.pack('<I', 0x20000101)
        moved = card.relocate(code, [(4, card.MOVT), (8, card.ABS32)], 0x11230000)
        self.assertEqual(moved[:4], code[:4])
        h1, h2 = struct.unpack_from('<HH', moved, 4)
        self.assertEqual(card._movt_imm(h1, h2), 0x3123)
        self.assertEqual(struct.unpack_from('<I', moved, 8)[0], 0x31230101)
        self.assertEqual(card.find_relocs(code, moved, 0x11230000), [(4, card.MOVT), (8, card.ABS32)])

    def test_unexplained_difference_rejected(self):
        with self.assertRaises(ValueError):
            card.find_relocs(b'\0' * 8, b'\0\0\0\0\x05\0\0\0', 0x10000)
        with self.assertRaises(ValueError):
            card.relocate(b'\0' * 4, [(0, card.ABS32)], 0x8000)

    def test_runtime_address(self):
        self.assertEqual(card.runtime_address(0x10355798 - card.BASE, 4), 0x10355798)
        source, destination, _, _ = card.SCATTER
        self.assertEqual(card.runtime_address(source + 16 - card.BASE, 4), destination + 16)
        for address in (0x10713168, 0x114A37C8, card.DRAM_END - 2):
            with self.assertRaises(ValueError):
                card.runtime_address(address - card.BASE, 4)


class Memory:
    """Runtime view: the DRAM alias of the EG copy, scatter RAM and free RAM."""

    def __init__(self, image):
        self.regions = {}
        lo = card.BASE + card.DRAM_ALIAS
        self.regions[lo] = bytearray(image[:card.DRAM_END - card.BASE])
        source, destination, length, _ = card.SCATTER
        self.regions[destination] = bytearray(image[source - card.BASE:source - card.BASE + length])
        self.writes = 0

    def add(self, address, size):
        self.regions[address] = bytearray(size)

    def _find(self, address, size):
        for start, data in self.regions.items():
            if start <= address and address + size <= start + len(data):
                return data, address - start
        raise KeyError(hex(address))

    def read(self, address, size):
        data, offset = self._find(address, size)
        return bytes(data[offset:offset + size])

    def write(self, address, value):
        data, offset = self._find(address, len(value))
        data[offset:offset + len(value)] = value
        self.writes += 1


@unittest.skipUnless(keystone and SOURCE.exists(), 'needs keystone and Z_30_0120.bin')
class CardBuildTests(unittest.TestCase):
    LOAD = 0x50000000

    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        cls.stock = extract_eg(SOURCE.read_bytes())
        cls.resident_image, _, cls.resident, cls.rid = card.build_resident(cls.stock)
        cls.patch, cls.report = card.build_card(cls.stock, 'full', cls.resident, cls.rid, cls.resident_image)
        cls.blob = card.pack(cls.patch)

    def fresh(self):
        memory = Memory(self.resident_image)
        memory.add(self.LOAD, len(self.patch.tail))
        return memory

    def test_format_roundtrip(self):
        self.assertEqual(card.pack(card.parse(self.blob)), self.blob)

    def test_damage_rejected(self):
        for offset in (0, 4, len(self.blob) // 2, len(self.blob) - 1):
            damaged = bytearray(self.blob)
            damaged[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                card.parse(bytes(damaged))
        with self.assertRaises(ValueError):
            card.parse(self.blob[:-4])

    def test_loaded_memory_matches_direct_link(self):
        memory = self.fresh()
        card.load(card.parse(self.blob), memory, self.LOAD, self.rid)
        from z30_extensions.build import build
        plan, _ = build(self.stock, 'full', card=True, tail_base=self.LOAD, resident=self.resident)
        self.assertEqual(memory.read(self.LOAD, len(plan.tail)), bytes(plan.tail))
        for edit in plan.edits:
            address = card.runtime_address(edit.offset, len(edit.expected))
            target = address + card.DRAM_ALIAS if address < card.DRAM_END else address
            self.assertEqual(memory.read(target, len(edit.replacement)), edit.replacement, edit.name)

    def test_nothing_written_on_mismatch(self):
        memory = self.fresh()
        with self.assertRaises(ValueError):
            card.load(self.patch, memory, self.LOAD, self.rid ^ 1)
        edit = self.patch.edits[-1]
        target = edit.address + card.DRAM_ALIAS if edit.address < card.DRAM_END else edit.address
        memory.write(target, bytes(b ^ 0xFF for b in edit.expected))
        memory.writes = 0
        with self.assertRaises(ValueError):
            card.load(self.patch, memory, self.LOAD, self.rid)
        self.assertEqual(memory.writes, 0)
        with self.assertRaises(ValueError):
            card.load(self.patch, self.fresh(), self.LOAD + 0x8000, self.rid)

    def test_card_edits_avoid_unwritable_eg(self):
        source, destination, length, _ = card.SCATTER
        for edit in self.patch.edits:
            self.assertTrue(edit.address < card.DRAM_END or destination <= edit.address < destination + length,
                            edit.name)

    def test_resident_changes_only_fonts_and_trigger(self):
        changed = [card.BASE + i for i in range(0, len(self.stock) - 2, 4)
                   if self.stock[i:i + 4] != self.resident_image[i:i + 4]]
        self.assertEqual(changed, [0x1021C360, 0x1021C364, 0x104530F4, 0x104530F8])


if __name__ == '__main__':
    unittest.main()
