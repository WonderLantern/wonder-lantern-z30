import struct
import unittest
from types import SimpleNamespace

from z30_extensions.features.connect.wifi_5ghz import EXPECTED_TABLE, RECORD_SIZE, RESERVED_PROFILES, SLOT_COUNT, TABLE_BASE, VALID_PROFILES, add


class FakePlan:
    def __init__(self, table):
        self.table = bytearray(table)
        self.features = {}
        self.image = SimpleNamespace(read=self.read)

    def read(self, address, size):
        offset = address - TABLE_BASE
        return bytes(self.table[offset:offset + size])

    def edit(self, address, replacement, name, expected=None):
        if expected is not None and self.read(address, len(expected)) != expected:
            raise ValueError(name)
        offset = address - TABLE_BASE
        self.table[offset:offset + len(replacement)] = replacement


class Wifi5GhzTests(unittest.TestCase):
    def test_table_covers_22_profiles_and_keeps_the_original_profile_7_entry(self):
        self.assertEqual(len(VALID_PROFILES), 22)
        self.assertEqual(len(RESERVED_PROFILES), 12)
        self.assertEqual(TABLE_BASE + RECORD_SIZE * 7, 0x114A3810)

    def test_rewrites_requests_and_preserves_index_and_reserved_slots(self):
        plan = FakePlan(EXPECTED_TABLE)
        add(plan)
        for slot in range(SLOT_COUNT):
            record = bytes(plan.table[slot * RECORD_SIZE:(slot + 1) * RECORD_SIZE])
            if slot in VALID_PROFILES:
                self.assertEqual(record, struct.pack('<4siI', b'US\0\0', -1, slot))
            else:
                self.assertEqual(record, EXPECTED_TABLE[slot * RECORD_SIZE:(slot + 1) * RECORD_SIZE])
        self.assertEqual(plan.features['wifi_5ghz']['profiles'], list(VALID_PROFILES))

    def test_rejects_an_unexpected_reserved_slot(self):
        table = bytearray(EXPECTED_TABLE)
        table[0] = 1
        with self.assertRaisesRegex(ValueError, 'Unexpected stock Wi-Fi profile table'):
            add(FakePlan(table))


if __name__ == '__main__':
    unittest.main()
