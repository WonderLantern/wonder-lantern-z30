import struct
import unittest
from z30_extensions.features.modules.files import pack, parse, MAX_CODE


class ModuleFormatTests(unittest.TestCase):
    def test_payload_and_nonzero_entry_roundtrip(self):
        code = bytes.fromhex('01207047 00207047')
        self.assertEqual(parse(pack(code, 4))['entry'], 4)
        self.assertEqual(parse(pack(code))['code'], code)

    def test_platform_header_damage_and_wrong_abi_rejected(self):
        blob = pack(bytes.fromhex('01207047'))
        for offset in (0, 4, 8, 12, 16, 32, 60):
            damaged = bytearray(blob)
            damaged[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                parse(damaged)

    def test_bounds_and_crc_rejected(self):
        blob = pack(bytes.fromhex('01207047'))
        for payload in (blob[:20], blob[:-1], blob + b'X', blob[:-1] + b'X'):
            with self.assertRaises(ValueError):
                parse(payload)
        for offset, value in ((20, 0), (20, MAX_CODE + 4), (24, 1), (24, 4)):
            payload = bytearray(blob)
            struct.pack_into('<I', payload, offset, value)
            with self.assertRaises(ValueError):
                parse(payload)

    def test_pack_refuses_invalid_payload(self):
        for code, entry in ((b'', 0), (b'x', 0), (b'x' * (MAX_CODE + 4), 0),
                            (b'abcd', -2), (b'abcd', 1), (b'abcd', 4)):
            with self.assertRaises(ValueError):
                pack(code, entry)
