"""Offline checks for automatic video rotation; uses caller-supplied stock firmware.

Checks the actual build/CRC path and stock-relative composition. --emulate
additionally executes native configuration, pose getter and video tkhd builder.
No camera or recorded MP4 is accessed; this does not prove camera behavior.
"""
import argparse
import binascii
import json
from pathlib import Path

from z30_extensions.build import build, resolve_presets
from z30_extensions.container import extract_eg
from z30_extensions.features.video_rot import (
    MATRIX_ADDRESS, EXPECTED_MATRIX, CONFIGURE, VIDEO_MATRIX_COPY,
    CONFIGURE_PREFIX, VIDEO_COPY_PREFIX)
from z30_extensions.patch import Edit, apply_patch
from z30_extensions.platform.z30_120 import BASE, CAPACITY, STOCK_SHA256, sha256


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(source, *, combinations=False, execute=False):
    offset = MATRIX_ADDRESS - BASE
    # Explicit no-op matrix edit must reproduce the complete stock image.
    identity, _ = apply_patch(source, [Edit(offset, EXPECTED_MATRIX, EXPECTED_MATRIX, 'rot0')],
                              b'', expected_sha256=STOCK_SHA256, base=BASE, capacity=CAPACITY)
    require(identity == source, 'rot0 changed stock')
    candidate, report = build(source, 'video-rot')
    require(len(candidate) == len(source) + report['append_length'] and report['append_length'] > 0,
            'Incorrect wrapper append length')
    require(len(report['edits']) == 2 and all(e['size'] == 10 for e in report['edits']),
            'Automatic video-rot must have two checked hooks')
    require(candidate[offset:offset + 36] == EXPECTED_MATRIX, 'Shared matrix changed')
    reverted = bytearray(candidate[:len(source) - 2])
    for address, expected in ((CONFIGURE, CONFIGURE_PREFIX), (VIDEO_MATRIX_COPY, VIDEO_COPY_PREFIX)):
        reverted[address - BASE:address - BASE + len(expected)] = expected
    require(reverted == source[:-2], 'Unexpected stock payload edits')
    require(binascii.crc_hqx(candidate, 0) == 0, 'Invalid output CRC')
    require(report['sha256'] == sha256(candidate) and report['camera_verified'] is False,
            'Incorrect build report')
    checks = []
    if combinations:
        for preset in ('liveview-dual', 'm900', 'shutter-sound', 'mtp-fast', 'full', 'modular'):
            # full now includes rotation: derive its non-video baseline explicitly.
            _, enabled = resolve_presets(preset)
            baseline, baseline_report = build(source, [f for f in enabled if f != 'video-rot'])
            require(baseline[offset:offset + 36] == EXPECTED_MATRIX,
                    f'{preset} changed the stock matrix without opt-in')
            combined, combined_report = build(source, [preset, 'video-rot'])
            if 'video-rot' in enabled:
                require(build(source, preset)[0] == combined,
                        f'{preset} duplicated the included video feature')
            restored = bytearray(combined[:len(baseline) - 2])
            for address, expected in ((CONFIGURE, CONFIGURE_PREFIX), (VIDEO_MATRIX_COPY, VIDEO_COPY_PREFIX)):
                restored[address - BASE:address - BASE + len(expected)] = expected
            restored += binascii.crc_hqx(restored, 0).to_bytes(2, 'big')
            require(restored == baseline and binascii.crc_hqx(combined, 0) == 0,
                    f'{preset} composition changed unrelated bytes or CRC')
            require(combined_report['append_length'] == baseline_report['append_length'] + report['append_length'],
                    f'{preset} composition changed unrelated tail length')
            reordered, _ = build(source, ['video-rot', preset, 'video-rot'])
            require(reordered == combined, f'{preset} order/duplicate instability')
            checks.append({'preset': preset, 'baseline_sha256': baseline_report['sha256'],
                           'combined_sha256': combined_report['sha256'], 'crc_residue': 0})
        default, _ = build(source)
        full, _ = build(source, 'full')
        require(default == full, 'Default differs from full')
    native = None
    if execute:
        from video_rot_fixture import verify_native
        native = verify_native(source, candidate)
    return {'passed': True, 'candidate_sha256': report['sha256'], 'crc_residue': 0,
            'rot0_matches_stock': True, 'standalone_edits': 2, 'append_length': report['append_length'],
            'combinations': checks, 'native_execution': native,
            'camera_verified': False,
            'limitations': 'No full muxer, recorded MP4, playback or physical camera verification.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--extracted-eg', action='store_true')
    parser.add_argument('--combinations', action='store_true', help='Check other presets; needs Keystone')
    parser.add_argument('--emulate', action='store_true', help='Execute native configuration and tkhd; needs Unicorn')
    args = parser.parse_args()
    supplied = args.source.read_bytes()
    source = supplied if args.extracted_eg else extract_eg(supplied)
    print(json.dumps(verify(source, combinations=args.combinations, execute=args.emulate), indent=2))


if __name__ == '__main__':
    main()
