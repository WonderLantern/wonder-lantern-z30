"""Card patches: relocation, file format, capabilities and the reference loader."""
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from z30_extensions import card

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Z_30_0120.bin'
try:
    import keystone  # noqa: F401
except ImportError:  # optional build dependency
    keystone = None

MARK = 0xC1832D24          # +0 of the unused page-0 menu record: zero in stock data
RAM_SLOT = 0xC5000000      # heap address the synthetic feature patches


def synthetic_feature():
    """focal-length plus, in card builds, a RAM edit and a load-time function."""
    from z30_extensions.assembly import assemble, constant
    from z30_extensions.features import focal_length
    stock_add = focal_length.add

    def add(plan):
        stock_add(plan)
        if not plan.card:
            return
        fn = plan.alloc(bytes(32))
        plan.put(fn, assemble(f'''{constant('r3', MARK)} str r1,[r3]; str r0,[r3,#4]; str r2,[r3,#8];
                                  movs r0,#1; bx lr;''', fn, 32))
        plan.on_load(fn | 1)
        plan.ram_edit(RAM_SLOT, struct.pack('<I', fn | 1), bytes(4), 'test pointer into the tail')
    return mock.patch.object(focal_length, 'add', add)


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

    def test_runtime_and_target_addresses(self):
        self.assertEqual(card.runtime_address(0x10355798 - card.BASE, 4), 0x10355798)
        source, destination, _, _ = card.SCATTER
        self.assertEqual(card.runtime_address(source + 16 - card.BASE, 4), destination + 16)
        for address in (0x10713168, 0x114A37C8, card.DRAM_END - 2):
            with self.assertRaises(ValueError):
                card.runtime_address(address - card.BASE, 4)
        self.assertEqual(card.target_address(0x10355798, 4), 0x10355798 + card.DRAM_ALIAS)
        self.assertEqual(card.target_address(0xC1D818B4, 4), 0xC1D818B4)
        for address in (0x10700000, 0xC17FFFFC, card.RAM_HI - 2, 0xCC400000):
            self.assertIsNone(card.target_address(address, 4))

    def test_plan_card_only_operations(self):
        from z30_extensions.plan import Plan
        plan = Plan.__new__(Plan)
        plan.card = False
        with self.assertRaises(ValueError):
            plan.ram_edit(RAM_SLOT, b'\1', b'\0', 'x')
        with self.assertRaises(ValueError):
            plan.on_load(0x20000001)


class Memory:
    """Runtime view: the DRAM alias of the EG copy, main RAM and free RAM."""

    def __init__(self, image):
        self.regions = {card.BASE + card.DRAM_ALIAS: bytearray(image[:card.DRAM_END - card.BASE]),
                        card.RAM_LO: bytearray(card.RAM_HI - card.RAM_LO)}
        source, destination, length, _ = card.SCATTER
        ram = self.regions[card.RAM_LO]
        ram[destination - card.RAM_LO:destination - card.RAM_LO + length] = \
            image[source - card.BASE:source - card.BASE + length]
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
        cls.resident = card.build_resident(cls.stock)
        cls.patch, cls.report = card.build_card(cls.stock, 'full', cls.resident)
        cls.blob = card.pack(cls.patch)

    def fresh(self):
        memory = Memory(self.resident.image)
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

    def test_unknown_sections(self):
        optional = card.parse(self.blob)
        optional.extra.append((99, 0, b'abcd'))
        self.assertEqual(card.parse(card.pack(optional)).extra, [(99, 0, b'abcd')])
        required = card.parse(self.blob)
        required.extra.append((99, card.REQUIRED, b'abcd'))
        with self.assertRaisesRegex(ValueError, 'newer resident'):
            card.parse(card.pack(required))
        # A loader that predates INIT sections must refuse a patch that has them.
        with_init = card.parse(self.blob)
        with_init.init = [0]
        with self.assertRaisesRegex(ValueError, 'newer resident'):
            card.parse(card.pack(with_init), known=(card.TAIL, card.RELOCS, card.EDITS))

    def test_loaded_memory_matches_direct_link(self):
        memory = self.fresh()
        card.load(card.parse(self.blob), memory, self.LOAD, self.resident.identity)
        from z30_extensions.build import build
        plan, _ = build(self.stock, 'full', card=True, tail_base=self.LOAD, resident=self.resident.symbols,
                        resident_id=self.resident.identity)
        self.assertEqual(memory.read(self.LOAD, len(plan.tail)), bytes(plan.tail))
        for edit in plan.edits:
            address = card.runtime_address(edit.offset, len(edit.expected))
            target = card.target_address(address, len(edit.expected))
            self.assertEqual(memory.read(target, len(edit.replacement)), edit.replacement, edit.name)

    def test_nothing_written_on_mismatch(self):
        memory = self.fresh()
        with self.assertRaises(ValueError):
            card.load(self.patch, memory, self.LOAD, self.resident.identity ^ 1)
        edit = self.patch.edits[-1]
        target = card.target_address(edit.address, len(edit.expected))
        memory.write(target, bytes(b ^ 0xFF for b in edit.expected))
        memory.writes = 0
        with self.assertRaises(ValueError):
            card.load(self.patch, memory, self.LOAD, self.resident.identity)
        self.assertEqual(memory.writes, 0)
        with self.assertRaises(ValueError):
            card.load(self.patch, self.fresh(), self.LOAD + 0x8000, self.resident.identity)

    def test_card_edits_avoid_unwritable_eg(self):
        for edit in self.patch.edits:
            self.assertIsNotNone(card.target_address(edit.address, len(edit.expected)), edit.name)

    def test_resident_changes_only_fonts_trigger_and_banner(self):
        changed = [card.BASE + i for i in range(0, len(self.stock) - 2, 4)
                   if self.stock[i:i + 4] != self.resident.image[i:i + 4]]
        self.assertEqual(changed, [0x1021C360, 0x1021C364, 0x104530F4, 0x104530F8,
                                   0x104D075C, 0x104D0760, 0x104D0764])

    def test_ram_edit_and_load_time_function(self):
        with synthetic_feature():
            patch, report = card.build_card(self.stock, 'focal-length', self.resident, ready_banner=False)
        self.assertEqual((report['card']['ram_edits'], report['card']['init_functions']), (1, 1))
        ram, = [e for e in patch.edits if e.address == RAM_SLOT]
        self.assertEqual(ram.relocs, [(0, card.ABS32)])
        memory = self.fresh()
        calls = []
        results = card.load(card.parse(card.pack(patch)), memory, self.LOAD, self.resident.identity,
                            call=lambda address: calls.append(address) or 1)
        self.assertEqual(results, [1])
        function = self.LOAD + patch.init[0]
        self.assertEqual(calls, [function])
        self.assertEqual(struct.unpack('<I', memory.read(RAM_SLOT, 4))[0], function | 1)

    def test_patch_for_the_flashed_resident(self):
        with tempfile.TemporaryDirectory() as folder:
            eg, meta = Path(folder) / 'EG209020.BIN', Path(folder) / 'EG209020.json'
            eg.write_bytes(self.resident.image)
            meta.write_text(json.dumps(self.resident.report), encoding='utf-8')
            flashed = card.load_resident(eg, meta)
            self.assertEqual(card.pack(card.build_card(self.stock, 'full', flashed)[0]), self.blob)
            # Capabilities of the resident decide what a patch may use.
            old = dict(self.resident.report, capabilities=dict(self.resident.capabilities,
                                                               sections=[1, 2, 3]))
            meta.write_text(json.dumps(old), encoding='utf-8')
            with synthetic_feature(), self.assertRaisesRegex(ValueError, 'load-time'):
                card.build_card(self.stock, 'focal-length', card.load_resident(eg, meta))
            glyphs = {k: v.replace('事', '') for k, v in self.resident.capabilities['glyphs'].items()}
            meta.write_text(json.dumps(dict(self.resident.report, capabilities=dict(
                self.resident.capabilities, glyphs=glyphs))), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'characters'):
                card.build_card(self.stock, 'full', card.load_resident(eg, meta))
            meta.write_text(json.dumps(dict(self.resident.report, capabilities=None)), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'older card loader'):
                card.load_resident(eg, meta)
            eg.write_bytes(self.resident.image[:-1] + b'\0')
            meta.write_text(json.dumps(self.resident.report), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                card.load_resident(eg, meta)


if __name__ == '__main__':
    unittest.main()
