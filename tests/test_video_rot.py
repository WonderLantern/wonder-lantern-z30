"""Synthetic stock identity; real Plan, preset build and CRC paths, no vendor data."""
import binascii
import struct
import unittest
import importlib.util
from unittest.mock import patch

from z30_extensions.build import build
from z30_extensions.features.liveview.dual import VIDEO_STOCK_WINDOWS
from z30_extensions.features.video.rotation import MATRIX_ADDRESS, EXPECTED_MATRIX, ROT90_MATRIX, POSE_MATRICES, POSE_DEGREES, CONFIGURE, CONFIGURE_PREFIX, VIDEO_MATRIX_COPY, VIDEO_COPY_PREFIX, GET_ORIENTATION, ORIENTATION_PREFIX
from z30_extensions.platform.z30_120 import BASE, SCATTER, sha256


@unittest.skipUnless(importlib.util.find_spec('keystone'), 'Automatic video-rot needs Keystone')
class VideoRotTests(unittest.TestCase):
    def synthetic_source(self, matrix=EXPECTED_MATRIX):
        source = bytearray((CONFIGURE - BASE + 64 + 3) & ~3)
        struct.pack_into('<4I', source, 0x14000, *SCATTER)
        for address, expected in VIDEO_STOCK_WINDOWS:
            source[address - BASE:address - BASE + len(expected)] = expected
        source[MATRIX_ADDRESS - BASE:MATRIX_ADDRESS - BASE + 36] = matrix
        for address, expected in ((CONFIGURE, CONFIGURE_PREFIX),
                                  (VIDEO_MATRIX_COPY, VIDEO_COPY_PREFIX),
                                  (GET_ORIENTATION, ORIENTATION_PREFIX)):
            source[address - BASE:address - BASE + len(expected)] = expected
        source += binascii.crc_hqx(source, 0).to_bytes(2, 'big')
        return bytes(source)

    def build_synthetic(self, source, selection='video-rot'):
        # Only identity constants are replaced; production validation remains active.
        with patch('z30_extensions.platform.z30_120.STOCK_SIZE', len(source)), \
             patch('z30_extensions.platform.z30_120.STOCK_SHA256', sha256(source)), \
             patch('z30_extensions.plan.STOCK_SHA256', sha256(source)):
            return build(source, selection)

    def test_matrix_semantics_and_two_hooks_with_owned_tail(self):
        self.assertEqual(struct.unpack('>9i', EXPECTED_MATRIX),
                         (65536, 0, 0, 0, 65536, 0, 0, 0, 1073741824))
        self.assertEqual(struct.unpack('>9i', ROT90_MATRIX),
                         (0, -65536, 0, 65536, 0, 0, 0, 0, 1073741824))
        source = self.synthetic_source()
        output, report = self.build_synthetic(source)
        offset = MATRIX_ADDRESS - BASE
        self.assertEqual(output[offset:offset + 36], EXPECTED_MATRIX)
        restored = bytearray(output[:len(source) - 2])
        for address, expected in ((CONFIGURE, CONFIGURE_PREFIX), (VIDEO_MATRIX_COPY, VIDEO_COPY_PREFIX)):
            restored[address - BASE:address - BASE + len(expected)] = expected
        self.assertEqual(restored, source[:-2])
        self.assertEqual(len(output), len(source) + report['append_length'])
        self.assertEqual(binascii.crc_hqx(output, 0), 0)
        self.assertEqual(report['sha256'], sha256(output))
        self.assertGreater(report['append_length'], 144)
        self.assertEqual(len(report['edits']), 2)
        self.assertEqual({e['offset']:e['expected_hex'] for e in report['edits']},
                         {hex(CONFIGURE - BASE):CONFIGURE_PREFIX.hex(),
                          hex(VIDEO_MATRIX_COPY - BASE):VIDEO_COPY_PREFIX.hex()})
        table = int(report['features']['video_rot']['matrix_table'], 16) - BASE
        self.assertEqual(output[table:table + 144], b''.join(POSE_MATRICES))
        self.assertEqual(POSE_DEGREES, (0, 90, 270, 180))
        self.assertEqual(report['enabled_features'], ['video-rot'])
        self.assertTrue(report['video_shutter_stock'])
        self.assertFalse(report['camera_verified'])
        self.assertTrue(report['features']['video_rot']['automatic_orientation'])

    def test_expected_matrix_rejected_even_with_matching_identity_and_crc(self):
        source = self.synthetic_source(bytes(36))
        with self.assertRaisesRegex(ValueError, 'Unexpected stock video matrix'):
            self.build_synthetic(source)

    def test_duplicates_apply_once(self):
        source = self.synthetic_source()
        output, _ = self.build_synthetic(source)
        repeated, report = self.build_synthetic(source, ['video-rot', 'video-rot'])
        self.assertEqual(output, repeated)
        self.assertEqual(len(report['edits']), 2)

    def test_expected_hook_windows_rejected(self):
        for address in (CONFIGURE, VIDEO_MATRIX_COPY, GET_ORIENTATION):
            source = bytearray(self.synthetic_source())
            source[address - BASE] ^= 1
            source[-2:] = binascii.crc_hqx(source[:-2], 0).to_bytes(2, 'big')
            with self.subTest(address=hex(address)), self.assertRaisesRegex(ValueError, 'Unexpected'):
                self.build_synthetic(source)


if __name__ == '__main__':
    unittest.main()
