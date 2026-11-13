"""Read the Portrait UI state and draw log live over USB (0x9FF1 ReadMemory).

Needs a build from tools/build_portrait_live.py and its JSON report, the camera
connected over USB, and the Extensions menu opened once after power-on (that
allocates the state block).

  python tools/portrait_live.py --report output/portrait-ui-live/EG209020.json status
  python tools/portrait_live.py --report ... follow --seconds 60 --out build/research/portrait-live.jsonl
  python tools/portrait_live.py --report ... dump --out WL-VUI-live.DBG
  python tools/portrait_live.py --report ... peek 0xC18292AC 4
  python tools/portrait_live.py --report ... screen --out build/research/screen.png

follow prints every new draw record and every change of the context words
(view kind, phase, render target, overlay words, pose), with a host timestamp.
The log ring keeps 256 records; polling faster than that fills is the reader's
job, and a skipped range is reported as such.
"""
import argparse
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]
from z30_extensions.features import portrait_menu as pm  # noqa: E402
from z30_extensions.features import portrait_ui as pu  # noqa: E402
from z30_extensions.features.sound_layout import STATE_ANCHOR, MAGIC as SOUND_MAGIC  # noqa: E402
from read_portrait_log import KIND_NAMES, decode  # noqa: E402

OPCODE = 0x9FF1
POSE_WORD = 0xC1D413AC + 0x1A4  # native pose index read by the orientation getter
CONTEXT = dict(view_kind=(pu.VIEW_KIND, 1), phase=(pu.UI_SERVICE_PHASE, 1), target=(pu.TARGET, 1),
               backend=(pu.PHOTO_BACKEND, 4), busy_a=(pu.OVERLAY_BUSY[0], 4), busy_b=(pu.OVERLAY_BUSY[1], 4),
               pose=(POSE_WORD, 4),
               # The getter returns this byte when [0xC1807DDC + 0xC] == 1 (seen on the camera).
               pose_branch=(0xC1807DDC + 0xC, 4), pose_byte=(0xC1D41318 + 0x90, 1),
               mode=(None, 4), repaint=(None, 4), passes=(None, 4), forced=(None, 4))


class Live:
    def __init__(self, report):
        if 'ptp_read' not in report['features'] or 'portrait_ui_control' not in report['features']:
            raise SystemExit('This report is not a portrait-ui build with ptp_read (tools/build_portrait_live.py)')
        self.state_offset = report['features']['portrait_ui_control']['state_offset']
        from ptp_wpd import Camera
        self.camera = Camera()

    def read(self, address, length):
        code, data = self.camera.run(OPCODE, [address, length])
        if code != 0x2001:
            raise OSError(f'ReadMemory({address:#x}, {length}) answered {code:#06x}')
        return bytes(data)

    def word(self, address, size=4):
        return int.from_bytes(self.read(address, size), 'little')

    def block_address(self):
        anchor = self.word(STATE_ANCHOR + 16)
        if not 0x80000000 <= anchor < 0xE0000000 or self.word(anchor + 56) != SOUND_MAGIC:
            return None
        return anchor + self.state_offset

    def context(self, block=None):
        values = {name: self.word(address, size) for name, (address, size) in CONTEXT.items() if address}
        if block:
            mode, _, repaint, passes, forced = struct.unpack('<5I', self.read(block + pu.R_MODE, 20))
            values.update(mode=mode, repaint=repaint, forced=forced)
        return values


def record(raw, seq):
    kind, target, view, phase, x, y, w, h, caller = struct.unpack('<4B4hI', raw)
    return dict(seq=seq, kind=KIND_NAMES.get(kind, kind), target=target, view=view, phase=phase,
                x=x, y=y, w=w, h=h, caller=hex(caller))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--report', type=Path, required=True)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    follow = sub.add_parser('follow')
    follow.add_argument('--seconds', type=float, default=60)
    follow.add_argument('--interval', type=float, default=0.05)
    follow.add_argument('--out', type=Path)
    dump = sub.add_parser('dump')
    dump.add_argument('--out', type=Path, required=True)
    screen = sub.add_parser('screen', help='save the on-screen UI canvas (720x480 RGBA) as PNG, plus a portrait-turned copy')
    screen.add_argument('--out', type=Path, required=True)
    screen.add_argument('--turn', type=int, choices=(0, 90, 270), default=90)
    screen.add_argument('--alpha', action='store_true', help='keep the canvas alpha (transparent where the live view shows through)')
    sub.add_parser('requests', help='print the traced 0x10453FAC redraw requests (newest last)')
    sub.add_parser('traversals', help='summarize the traced 0x102BD3BA redraw traversals (container, caller, view kind)')
    peek = sub.add_parser('peek')
    peek.add_argument('address', type=lambda v: int(v, 0))
    peek.add_argument('length', type=lambda v: int(v, 0))
    a = p.parse_args()
    live = Live(json.loads(a.report.read_text()))
    if a.command == 'screen':
        from PIL import Image
        canvas = live.word(0xC18200BC)  # display state word: the UI canvas above the live-view image
        size = 720 * 480 * 4
        data = b''.join(live.read(canvas + o, min(0x40000, size - o)) for o in range(0, size, 0x40000))
        picture = Image.frombytes('RGBA', (720, 480), data)
        if not a.alpha:
            background = Image.new('RGBA', (720, 480), (60, 60, 60, 255))
            background.alpha_composite(picture)
            picture = background.convert('RGB')
        picture.save(a.out)
        if a.turn:
            picture.rotate(a.turn, expand=True).save(a.out.with_name(a.out.stem + '-portrait' + a.out.suffix))
        print(f'canvas {canvas:#x} -> {a.out}')
        return
    if a.command == 'peek':
        print(live.read(a.address, a.length).hex())
        return
    block = live.block_address()
    if block is None:
        raise SystemExit('State block not allocated yet: open Setup > Extensions once after power-on')
    if a.command == 'status':
        result = decode(live.read(block, pm.STATE_SIZE))
        result.pop('records')
        print(json.dumps(dict(block=hex(block), **result, context=live.context()), indent=1))
        return
    if a.command == 'requests':
        periodic, = struct.unpack('<I', live.read(block + pm.REQ + 8, 4))
        print(f'periodic refresh requests (from {pu.PERIODIC_CALLER:#x}, counted only): {periodic}')
        for r in decode(live.read(block, pm.STATE_SIZE))['requests']:
            print(f"{r['seq']:6d} from {r['caller']} what {r['what']} screens {r['screens']} predicate {r['predicate']}")
        return
    if a.command == 'traversals':
        import collections
        base = block + pm.TRV
        total = struct.unpack('<I', live.read(base + 4, 4))[0]
        ring = live.read(base + 16, pm.TRV_CAPACITY * pm.TRV_STRIDE)
        rows = collections.Counter()
        for seq in range(max(0, total - pm.TRV_CAPACITY), total):
            obj, callback, caller, tag = struct.unpack_from('<4I', ring, (seq % pm.TRV_CAPACITY) * pm.TRV_STRIDE)
            rows[(hex(obj), hex(callback), hex(caller), tag & 255, tag >> 8)] += 1
        print(f'{total} traversals recorded; last {min(total, pm.TRV_CAPACITY)} by container, callback, caller, view kind, pose:')
        for key, number in rows.most_common():
            print(f'  {number:4d}  container {key[0]}  callback {key[1]}  from {key[2]}  view {key[3]}  pose {key[4]}')
        return
    if a.command == 'dump':
        a.out.write_bytes(live.read(block, pm.STATE_SIZE))
        print(f'wrote {a.out} ({pm.STATE_SIZE} bytes); decode with tools/read_portrait_log.py')
        return
    log = block + pm.LOG
    out = a.out.open('w', encoding='utf-8') if a.out else None
    def emit(entry):
        line = json.dumps(entry)
        print(line, flush=True)
        if out:
            out.write(line + '\n')
            out.flush()
    seen = struct.unpack('<I', live.read(log + 4, 4))[0]
    last_context = None
    deadline = time.monotonic() + a.seconds
    try:
        while time.monotonic() < deadline:
            now = round(time.time(), 3)
            context = live.context(block)
            if context != last_context:
                emit(dict(time=now, context=context))
                last_context = context
            count = struct.unpack('<I', live.read(log + 4, 4))[0]
            if count < seen:  # cleared from the menu
                emit(dict(time=now, log='cleared'))
                seen = 0
            if count > seen:
                first = max(seen, count - pm.LOG_CAPACITY)
                if first > seen:
                    emit(dict(time=now, log='skipped', records=first - seen))
                ring = live.read(log + 16, pm.LOG_CAPACITY * pm.LOG_STRIDE)
                for seq in range(first, count):
                    slot = (seq % pm.LOG_CAPACITY) * pm.LOG_STRIDE
                    emit(dict(time=now, **record(ring[slot:slot + pm.LOG_STRIDE], seq)))
                seen = count
            time.sleep(a.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if out:
            out.close()


if __name__ == '__main__':
    main()
