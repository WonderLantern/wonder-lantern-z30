import binascii
import struct
import unittest
from unittest.mock import patch

from z30_extensions.build import PRESETS, build, resolve_presets
from z30_extensions.features.liveview_dual import VIDEO_STOCK_WINDOWS
from z30_extensions.features.nef_compression import (
    MODE_COMPRESSED, MODE_UNCOMPRESSED, SITES, add, load_immediate, small_immediate)
from z30_extensions.platform.z30_120 import BASE, SCATTER, sha256


class NefCompressionTests(unittest.TestCase):
    def synthetic_source(self, corrupt=None):
        top = max([address + len(expected) for address, expected in VIDEO_STOCK_WINDOWS] +
                  [address + len(expected) for address, expected, _, _, _ in SITES])
        size = (top - BASE + 64 + 3) & ~3
        source = bytearray(size)
        struct.pack_into('<4I', source, 0x14000, *SCATTER)
        for address, expected in VIDEO_STOCK_WINDOWS:
            source[address - BASE:address - BASE + len(expected)] = expected
        for address, expected, _, _, _ in SITES:
            source[address - BASE:address - BASE + len(expected)] = expected
        if corrupt is not None:
            source[corrupt - BASE] ^= 0xFF
        source += binascii.crc_hqx(source, 0).to_bytes(2, 'big')
        return bytes(source)

    def build_synthetic(self, source, selection):
        with patch('z30_extensions.platform.z30_120.STOCK_SIZE', len(source)), \
             patch('z30_extensions.platform.z30_120.STOCK_SHA256', sha256(source)), \
             patch('z30_extensions.plan.STOCK_SHA256', sha256(source)):
            return build(source, selection)

    def test_immediate_encoding_matches_thumb2_movw(self):
        self.assertEqual(load_immediate(1, 0), bytes.fromhex('40f20001'))
        self.assertEqual(load_immediate(1, 2), bytes.fromhex('40f20201'))
        self.assertEqual(load_immediate(0, 2), bytes.fromhex('40f20200'))
        self.assertEqual(load_immediate(0, 0), bytes.fromhex('40f20000'))
        self.assertEqual(small_immediate(1, 0), bytes.fromhex('0021'))
        self.assertEqual(small_immediate(1, 2), bytes.fromhex('0221'))
        with self.assertRaises(ValueError):
            small_immediate(8, 0)
        with self.assertRaises(ValueError):
            load_immediate(8, 0)
        with self.assertRaises(ValueError):
            load_immediate(0, 256)

    def test_menu_is_in_full_and_the_fixed_presets_are_not(self):
        self.assertEqual(PRESETS[-3:], ('nef-menu', 'nef-uncompressed', 'nef-compressed'))
        self.assertEqual(PRESETS[PRESETS.index('date-stamp') + 1], 'nef-menu')
        self.assertIn('nef-menu', resolve_presets('full')[1])
        self.assertNotIn('nef-uncompressed', resolve_presets('full')[1])
        self.assertNotIn('nef-compressed', resolve_presets('full')[1])
        self.assertEqual(resolve_presets('nef-uncompressed')[1], ['nef-uncompressed'])
        self.assertIn('shutter-sound', resolve_presets('nef-menu')[1])

    def test_fixed_presets_replace_the_menu_inside_full(self):
        base = resolve_presets('full')[1]
        for name in ('nef-uncompressed', 'nef-compressed'):
            expected = [feature for feature in base if feature != 'nef-menu'] + [name]
            self.assertEqual(resolve_presets(['full', name, name])[1], expected)
            self.assertEqual(resolve_presets([name, 'full'])[1], expected)

    def test_explicit_nef_presets_are_exclusive(self):
        for selection in (['nef-uncompressed', 'nef-compressed'],
                          ['nef-menu', 'nef-compressed'],
                          ['nef-menu', 'nef-uncompressed'],
                          ['full', 'nef-menu', 'nef-compressed'],
                          ['full', 'nef-uncompressed', 'nef-compressed']):
            with self.assertRaises(ValueError):
                resolve_presets(selection)

    def test_only_combinations_with_two_nef_presets_are_rejected(self):
        import itertools
        from z30_extensions.build import FEATURE_ORDER, NEF_COMPRESSION_FEATURES
        for count in range(1, len(FEATURE_ORDER) + 1):
            for combo in itertools.combinations(FEATURE_ORDER, count):
                nef = sum(name in NEF_COMPRESSION_FEATURES for name in combo)
                try:
                    resolve_presets(list(combo))
                    accepted = True
                except ValueError:
                    accepted = False
                self.assertEqual(accepted, nef <= 1, combo)

    def test_unknown_preset_is_rejected(self):
        with self.assertRaises(ValueError):
            add(object(), 'nef-lossless')

    def check_build(self, preset, mode):
        source = self.synthetic_source()
        output, report = self.build_synthetic(source, preset)
        self.assertEqual(len(report['edits']), len(SITES))
        for address, expected, encode, register, _ in SITES:
            offset = address - BASE
            self.assertEqual(output[offset:offset + len(expected)], encode(register, mode))
        restored = bytearray(output[:len(source) - 2])
        for address, expected, _, _, _ in SITES:
            restored[address - BASE:address - BASE + len(expected)] = expected
        self.assertEqual(restored, source[:-2])
        self.assertEqual(binascii.crc_hqx(output, 0), 0)
        feature = report['features']['nef_compression']
        self.assertEqual(feature['mode'], mode)
        self.assertEqual(feature['preset'], preset)
        self.assertFalse(feature['camera_verified'])
        self.assertFalse(feature['menu_entry_added'])
        self.assertEqual({e['offset'] for e in report['edits']},
                         {hex(address - BASE) for address, _, _, _, _ in SITES})

    def test_uncompressed_build_changes_only_the_hook_sites(self):
        self.check_build('nef-uncompressed', MODE_UNCOMPRESSED)

    def test_compressed_build_changes_only_the_hook_sites(self):
        self.check_build('nef-compressed', MODE_COMPRESSED)

    def test_unexpected_source_bytes_abort_the_build(self):
        source = self.synthetic_source(corrupt=SITES[1][0])
        with self.assertRaises(ValueError):
            self.build_synthetic(source, 'nef-uncompressed')


if __name__ == '__main__':
    unittest.main()
