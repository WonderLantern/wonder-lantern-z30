"""Replay three native view rectangle paths, including the v12 omissions.

Two paths are bounded native blocks; the cached-window updater runs to return.
This is scalar geometry evidence, not a camera boot/sleep or LCD pixel model.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]


def rectangle(stock, image, report, profile, user_area, area, route):
    from verify_aspect_liveview_window import fixture, update
    from native_runtime import STACK
    from unicorn.arm_const import UC_ARM_REG_R4, UC_ARM_REG_R6
    c = fixture(stock, image, report, profile, user_area)
    if route == 'cached':
        return update(c, area)[1]
    c.u.reg_write(UC_ARM_REG_R4, 0)
    if route == 'local':
        argument = 0x500E0000
        c.u.mem_write(argument, bytes([0, 0, area, 0]))
        c.u.reg_write(UC_ARM_REG_R6, argument)
        c.block(0x1018AB38, 0x1018ABA4)
        offset = 8
    else:
        assert route == 'submitted'
        c.u.reg_write(UC_ARM_REG_R6, area)
        c.block(0x101985CE, 0x1019863A)
        offset = 0
    return list(struct.unpack('<4H', c.u.mem_read(STACK + 0xF0000 + offset, 8)))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('source', 'image', 'report', 'previous-image', 'previous-report', 'output', 'deps'):
        p.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from z30_extensions.platform.z30_120 import sha256
    from verify_aspect_capture_branch import run
    from verify_aspect_liveview_window import fixture
    from z30_extensions.features.aspect.ui import LIVEVIEW_OUTPUT_CALLERS
    stock = extract_eg(a.source.read_bytes())
    image, previous = a.image.read_bytes(), a.previous_image.read_bytes()
    report = json.loads(a.report.read_text('utf-8'))
    old = json.loads(a.previous_report.read_text('utf-8'))
    cases, failures, captures, caller_guards = [], [], [], []
    native = fixture(stock, stock, None, 0, 2).call(0x1020BFDA, (2, 0))
    helper = int(report['features']['aspect_profiles']['functions']['aspect_output_data'], 16)
    for profile in (0, 1, 2, 3, 6, 7):
        c = fixture(stock, image, report, profile, 2)
        custom = c.call(0x1020BFDA, (2, 0))
        for allowed in LIVEVIEW_OUTPUT_CALLERS:
            for caller in (allowed - 2, allowed, allowed + 2):
                pointer = c.call(helper, (native, caller))
                expected = native if profile in (1, 2) and caller == allowed else custom
                assert pointer == expected, (profile, hex(caller))
                caller_guards.append(dict(profile=profile, caller=hex(caller), native_pointer=pointer == native))
    for route in ('cached', 'local', 'submitted'):
        baseline = {area: rectangle(stock, stock, None, 0, 2, area, route) for area in (0, 2, 4)}
        assert baseline[2] == [0, 292, 5568, 3420]
        for profile in (1, 2):
            before = rectangle(stock, previous, old, profile, 2, 2, route)
            if route != 'cached':
                assert before == ([308, 0, 5260, 3712] if profile == 1 else [464, 0, 5104, 3712])
                failures.append(dict(route=route, profile=profile, previous_rect=before))
        for profile in (0, 1, 2, 3, 6, 7):
            for user_area in (0, 2, 4):
                for area in (0, 2, 4):
                    actual = rectangle(stock, image, report, profile, user_area, area, route)
                    expected = (rectangle(stock, previous, old, profile, user_area, area, route)
                                if profile in (3, 6) else baseline[area])
                    assert actual == expected, (route, profile, user_area, area, actual, expected)
                    cases.append(dict(route=route, profile=profile, user_area=user_area, area=area, rect=actual))
    for profile in range(7):
        for size in range(3):
            for branch in (0, 3):
                for mode in (0, 2):
                    args = dict(profile=profile, size=size, branch=branch, mode=mode)
                    actual = run(stock, image, report, **args)
                    assert actual == run(stock, previous, old, **args)
                    captures.append(actual)
    result = dict(image_sha256=sha256(image), previous_sha256=sha256(previous),
                  reproduced_v12_omissions=failures, native_view_cases=cases,
                  exact_caller_guard_cases=caller_guards,
                  capture_equivalence_cases=captures, camera_verified=False,
                  limits=['Three native rectangle arithmetic paths plus the current exact-caller guard table.',
                          'Bounded local/submission calculations stop before downstream device calls.',
                          'No real boot, standby, task scheduling, LCD pixels or complete framing proof.',
                          'Reference-frame position and grid coexistence remain separate unresolved work.'])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f'PASS {len(cases)} view route cases, {len(failures)} reproduced v12 omissions, {len(captures)} capture comparisons')


if __name__ == '__main__':
    main()
