"""Issue 38: custom profiles keep a stock NEF RAW plane, JPEG geometry unchanged.

Executes the stock processing configuration (0x1014237A, default and
alternate branches), buffer setup (0x10142BEA), RAW plane selector
(0x1020BFC8) and RAW-plane descriptor (0x101D5B36) in Unicorn, for:

1. the independent v43 geometry (custom RAW plane, camera-verified JPEG),
2. the independent issue-38 geometry (stock DX/16:9 RAW plane), and
3. the built EG with each profile selected through its runtime state word.

The processing window must be byte-identical to v43; the RAW plane must be
the stock DX or 16:9 plane; descriptor crop rectangles must keep the same
absolute sensor position. No sensor, ISP, NEF writer or camera is modeled.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
from z30_extensions.container import extract_eg
from z30_extensions.features.aspect_geometry import PROFILES, build_trial, raw_plane
from z30_extensions.platform.z30_120 import sha256

# Descriptor u16 layout (0x101D5B36): 0-1 full DX plane, 2-3 area RAW plane,
# 4-5 sensor origin, 6-7 crop centre and 8-11 crop rectangle (both relative
# to the RAW plane), 12-13 output (sizes M/S only).
DESCRIPTOR_X = (6, 8, 10)
DESCRIPTOR_Y = (7, 9, 11)


def run(context, alternate=False):
    from native_runtime import RAM
    obj, info, holder, header = RAM, RAM + 0x1000, RAM + 0x5000, RAM + 0x4000
    u = context.u
    cases = []

    def reset(area, size):
        u.mem_write(obj, bytes(256))
        u.mem_write(info, bytes(0x3000))
        u.mem_write(obj + 4, struct.pack('<I', holder))
        u.mem_write(holder, struct.pack('<I', header))
        u.mem_write(info + 0x208, bytes([area]))
        u.mem_write(info + 0x229, bytes([size]))
        if alternate:
            u.mem_write(info + 0x228, bytes([3]))

    for size in range(3):
        reset(2, size)
        context.call(0x10142BEA, (obj, info))
        buffer = bytes(u.mem_read(obj, 44))
        reset(2, size)
        context.call(0x1014237A, (obj, info, 0))
        config = bytes(u.mem_read(obj, 0x70))
        reset(2, size)
        u.mem_write(info, bytes([2]))
        u.mem_write(info + 0x22, bytes([size]))
        context.call(0x101D5B36, (info, obj))
        descriptor = list(struct.unpack('<14H', bytes(u.mem_read(obj, 28))))
        internal = context.call(0x1020BFC8, (0, 2, 0))
        cases.append(dict(size=size, buffer=buffer.hex(), config=config.hex(), descriptor=descriptor,
                          raw_plane=list(struct.unpack('<2H', u.mem_read(internal, 4)))))
    return cases


def absolute(descriptor):
    ox, oy = descriptor[4], descriptor[5]
    return ([descriptor[i] + ox for i in DESCRIPTOR_X], [descriptor[i] + oy for i in DESCRIPTOR_Y],
            descriptor[12:14])


def verify(stock, image, report):
    from native_runtime import Context
    from verify_sound_settings import Fixture
    feature = report['features']['aspect_profiles']
    assert feature.get('stock_raw_plane_version') == 1
    functions = {k: int(v, 16) for k, v in feature['functions'].items()}
    properties = {0x501: 2, 0x188c: 0}
    built = Fixture(stock, image, report, {}, properties=properties)
    state = built.call(functions['aspect_get'])
    assert state
    result = dict(source_sha256=sha256(stock), image_sha256=sha256(image), profiles=[],
                  limitations=['Native geometry functions only; sensor readout, ISP, NEF writer, '
                               'live view and on-camera JPEG framing are not modeled.',
                               'Adobe/Lightroom import requires camera NEF samples.'])
    for index, profile in enumerate(PROFILES, 1):
        legacy_image, _ = build_trial(stock, profile, stock_raw_plane=False)
        fixed_image, fixed_report = build_trial(stock, profile)
        plane = raw_plane(profile)
        built.u.mem_write(state, struct.pack('<I', index))
        entry = dict(profile=profile, raw_plane=list(plane['large']), branches={})
        for alternate in (False, True):
            legacy = run(Context(stock, legacy_image), alternate)
            fixed = run(Context(stock, fixed_image), alternate)
            actual = run(built, alternate)
            for old, new, got in zip(legacy, fixed, actual):
                where = (profile, alternate, old['size'])
                # JPEG side: v43 processing window and buffers, byte for byte.
                assert new['config'] == old['config'], where
                assert new['buffer'] == old['buffer'], where
                assert got['config'] == old['config'], where
                assert got['buffer'] == old['buffer'], where
                # RAW side: stock plane and origin for the profile's base area.
                assert new['raw_plane'] == got['raw_plane'] == list(plane['large']), where
                assert got['descriptor'][0:2] == [5600, 3728], where  # area-0 full plane
                assert got['descriptor'][2:4] == list(plane['large']), (where, got['descriptor'])
                assert got['descriptor'][4:6] == list(plane['origin']), where
                # Crop rectangle: same absolute sensor position and output.
                assert absolute(got['descriptor']) == absolute(old['descriptor']), where
                # Rectangle must lie inside the stock RAW plane.
                x0, y0, x1, y1 = got['descriptor'][8:12]
                assert 0 <= x0 < x1 <= plane['large'][0] and 0 <= y0 < y1 <= plane['large'][1], where
            entry['branches']['alternate' if alternate else 'default'] = [
                dict(size=g['size'], descriptor=g['descriptor'], v43_descriptor=o['descriptor'])
                for g, o in zip(actual, legacy)]
        result['profiles'].append(entry)
    # Selection off: every function returns the exact stock result.
    built.u.mem_write(state, struct.pack('<I', 0))
    for alternate in (False, True):
        assert run(built, alternate) == run(Context(stock, stock), alternate)
    result['passed_cases'] = len(PROFILES) * 2 * 3 + 6
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'image', 'report', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--deps', type=Path)
    args = parser.parse_args()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    outcome = verify(extract_eg(args.source.read_bytes()), args.image.read_bytes(),
                     json.loads(args.report.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(outcome, indent=2) + '\n', encoding='utf-8')
    print(f'Passed {outcome["passed_cases"]} RAW-plane cases; saved {args.output}')
