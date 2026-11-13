"""QEMU-only: the stock renderer drawing the photo live-view info with the portrait hooks.

Starts the assisted live-view session on stock firmware, saves a landscape
frame, then replays the built EG edits and owned tail into the guest, turns the
switch on through an explicit menu-state fixture and sets the native pose word
the orientation getter reads. Each step forces the stock UI refresh and saves
the GUI frame. Switched on but level, and back to level after portrait, the
frame should look like the stock frame; upright, the widgets should move.

The pose word, the switch state and the refresh call are fixtures, not a camera
being turned. QEMU draws only a few live-view widgets (no exposure info), so
this does not judge the layout; it shows the renderer accepts the rewritten
commands and that the context, target and pending-clear logic behave as built.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

POSE_ROOT, POSE_FLAG = 0xC1807DDC, 0xC  # getter returns 4 when [root+0xc] == 1
POSE_STATE, POSE_INDEX = 0xC1D413AC, 0x1A4  # index 1 level, 2 inverted, 3 -> 1, 4 -> 2
POSES = {0: 1, 1: 3, 2: 4, 3: 2}
STATE = 0xC6001000
SOUND_MAGIC = 0x5A33534E


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--settle', type=float, default=1.5)
    p.add_argument('--repaint', choices=('refresh', 'none', 'request', 'request-all'), default='refresh',
                   help='after each step: stock UI refresh 1032427E, nothing, or the redraw request 10453FAC(10453AC3, 3, 7)')
    a = p.parse_args()
    image = a.image.read_bytes()
    report = json.loads(a.report.read_text())
    digest = hashlib.sha256(image).hexdigest()
    if digest != report['sha256']:
        raise ValueError('Image/report identity mismatch')
    control = report['features']['portrait_ui_control']
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import DEVICE_ENV, refresh, start
    s = Session(str(a.output_dir.resolve()), device_env=DEVICE_ENV)

    def write(address, data):
        for offset in range(0, len(data), 1024):
            block = data[offset:offset + 1024]
            if s.remote.command(f'M{address + offset:x},{len(block):x}:' + block.hex()) != 'OK':
                raise RuntimeError('Guest memory write failed')
        if s.remote.memory(address, len(data)) != data:
            raise RuntimeError('Guest memory readback failed')

    def word(address):
        return struct.unpack('<I', s.remote.memory(address, 4))[0]

    def shot(name):
        if a.repaint == 'refresh' or name == '00-stock':
            refresh(s)
        elif a.repaint == 'request':
            s.input(0x10453FAC, [0x10453AC3, 3, 7])
        elif a.repaint == 'request-all':
            s.input(0x10453FAC, [0x10453AC3, 0x1F, 7])
            refresh(s)  # the stock refresh must still return afterwards
        s.run_for(a.settle)
        path = s.frame(name)
        data = (s.graphics / 'gui-latest.ppm').read_bytes()
        last, pending = struct.unpack('<2I', s.remote.memory(STATE + control['state_offset'] + control['runtime_offset'], 8))
        alive = None
        if 'event_offset' in control:  # the GUI's periodic update events keep counting unless it hangs
            at = STATE + control['state_offset'] + control['event_offset'] + 4
            before = word(at)
            s.run_for(1.0)
            alive = dict(events_before=before, events_after=word(at), injected=word(at + 8))
        return dict(frame=path.name, sha256=hashlib.sha256(data).hexdigest(), data=data,
                    runtime=dict(mode=last, pending=pending), alive=alive,
                    context=dict(view_kind=s.remote.memory(0xC18292AC, 1)[0], phase=s.remote.memory(0xC181F6FC, 1)[0],
                                 backend=word(0xC181B278), target=s.remote.memory(0xC1808758, 1)[0]))

    def pose(value):
        write(POSE_STATE + POSE_INDEX, struct.pack('<I', POSES[value]))

    steps = []
    try:
        start(s)
        if word(POSE_ROOT + POSE_FLAG) == 1:
            raise RuntimeError('Pose getter is in its fixed-value branch')
        stock_pose = word(POSE_STATE + POSE_INDEX)
        stock = shot('00-stock')
        for edit in report['edits']:
            address = 0x10020000 + int(edit['offset'], 16)
            if s.remote.memory(address, len(bytes.fromhex(edit['expected_hex']))) != bytes.fromhex(edit['expected_hex']):
                raise RuntimeError('Guest stock instruction mismatch')
            write(address, bytes.fromhex(edit['replacement_hex']))
        stock_size = (a.qemu_root / '.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000 + stock_size - 2, image[stock_size - 2:])
        # Explicit menu-state fixture: the real page and SD record are checked by verify_portrait_ui_switch.py.
        write(0xC1832D34, struct.pack('<I', STATE))
        write(STATE + 56, struct.pack('<I', SOUND_MAGIC))
        write(STATE + control['state_offset'], struct.pack('<4I', 0, 0, 0, 0) + bytes(16))
        steps.append(('01-candidate-off', shot('01-candidate-off')))
        write(STATE + control['state_offset'], struct.pack('<I', 1))
        pose(0)
        steps.append(('02-on-level', shot('02-on-level')))
        pose(1)
        steps.append(('03-upright-1', shot('03-upright-1')))
        steps.append(('04-upright-1-again', shot('04-upright-1-again')))
        pose(2)
        steps.append(('05-upright-2', shot('05-upright-2')))
        pose(0)
        steps.append(('06-level-again', shot('06-level-again')))
        write(STATE + control['state_offset'], struct.pack('<I', 0))
        steps.append(('07-off-again', shot('07-off-again')))
        write(POSE_STATE + POSE_INDEX, struct.pack('<I', stock_pose))
    except RuntimeError as error:
        # Where did the guest stop? Registers and a stack window of every core.
        crash = dict(error=str(error), threads={})
        for thread in ('01', '02'):
            try:
                assert s.remote.command('Hg' + thread) == 'OK'
                regs = s.remote.registers()
                sp = int(regs['r13'], 16)
                crash['threads'][thread] = dict(regs=regs, stack=s.remote.memory(sp, 128).hex())
            except Exception as inner:
                crash['threads'][thread] = dict(error=repr(inner))
        (a.output_dir / 'crash.json').write_text(json.dumps(crash, indent=1) + chr(10), encoding='utf-8')
        print(json.dumps(crash)[:3000])
        raise
    finally:
        s.close()
    same = lambda step: step['data'] == stock['data']
    by = dict(steps)
    # The assisted preview pattern moves between frames, so whole frames are
    # compared by eye (saved PNGs), not by hash; the runtime words are checked.
    checks = {
        'mode_tracks_pose': [by[k]['runtime']['mode'] for k in ('02-on-level', '03-upright-1', '05-upright-2', '06-level-again')] == [0, 1, 2, 0],
        'no_pending_left': all(st['runtime']['pending'] == 0 for _, st in steps),
        'gui_alive_after_each_step': all(st['alive'] is None or st['alive']['events_after'] > st['alive']['events_before']
                                         for _, st in steps if _ != '01-candidate-off' and _ != '07-off-again'),
    }
    result = dict(candidate_sha256=digest, stock=dict(frame=stock['frame'], sha256=stock['sha256'], context=stock['context']),
                  steps=[dict(name=name, frame=st['frame'], sha256=st['sha256'], runtime=st['runtime'], context=st['context'])
                         for name, st in steps],
                  checks=checks, passed=all(checks.values()), camera_verified=False,
                  fixtures=['menu state block', 'native pose word', 'stock UI refresh 1032427E', 'assisted live-view start'])
    (a.output_dir / 'portrait-ui-qemu.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(checks=checks, steps=[(n, st['runtime'], st['alive']) for n, st in steps]), indent=1))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
