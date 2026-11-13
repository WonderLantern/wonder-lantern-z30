"""Snapshot the recorded live-view widgets over USB, or compare two snapshots.

  python tools/portrait_widgets.py --report R.json snap --out a.json
  python tools/portrait_widgets.py diff a.json b.json

A snapshot holds, for each widget the draw-record hook saw, its last-draw stamp,
its box (+0x10, four halfwords), its dirty flag (+0x44) and draw function (+0x4C).
diff lists the widgets the stock code drew between the two snapshots.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]
from z30_extensions.features import portrait_menu as pm  # noqa: E402


def snap(report, out):
    from portrait_live import Live
    live = Live(json.loads(Path(report).read_text()))
    block = live.block_address()
    if block is None:
        raise SystemExit('State block not allocated yet')
    head = live.read(block + pm.WID, 16 + pm.WID_CAPACITY * pm.WID_STRIDE)
    count, stamp = struct.unpack_from('<2I', head)
    rows = []
    for i in range(min(count, pm.WID_CAPACITY)):
        widget, last = struct.unpack_from('<2I', head, 16 + i * 8)
        raw = live.read(widget, 0x50)
        box = struct.unpack_from('<4h', raw, 0x10)
        flag, = struct.unpack_from('<I', raw, 0x44)
        draw, = struct.unpack_from('<I', raw, 0x4C)
        rows.append(dict(widget=hex(widget), stamp=last, box=box, dirty=flag, draw=hex(draw), raw=raw.hex()))
    Path(out).write_text(json.dumps(dict(count=count, stamp=stamp, widgets=rows), indent=1))
    print(f'{count} widgets, stamp {stamp} -> {out}')


def diff(a, b):
    a, b = (json.loads(Path(p).read_text()) for p in (a, b))
    before = {w['widget']: w for w in a['widgets']}
    print(f"stamp {a['stamp']} -> {b['stamp']}")
    for w in b['widgets']:
        old = before.get(w['widget'])
        if old is None or old['stamp'] != w['stamp']:
            changed = '' if old is None or old['raw'] == w['raw'] else ' (fields changed)'
            print(f"{w['widget']} stamp {old and old['stamp']}->{w['stamp']} box {w['box']} draw {w['draw']}{changed}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('snap')
    s.add_argument('--report', required=True)
    s.add_argument('--out', required=True)
    d = sub.add_parser('diff')
    d.add_argument('a')
    d.add_argument('b')
    a = p.parse_args()
    snap(a.report, a.out) if a.command == 'snap' else diff(a.a, a.b)


if __name__ == '__main__':
    main()
