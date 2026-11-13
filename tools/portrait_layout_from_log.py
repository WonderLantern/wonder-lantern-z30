"""Summarize live-view element boxes from a portrait_live.py follow log.

Groups the recorded landscape boxes (render target 2, view kind 0) by kind and
position, drops repeats, and prints them per portrait zone so a per-element
layout table can be written from real coordinates instead of zone guesses.

  python tools/portrait_layout_from_log.py build/research/portrait-live.jsonl
"""
import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.features import portrait_ui as pu  # noqa: E402


def zone(x, y, w, h):
    if w >= pu.FULL_W and h >= pu.FULL_H:
        return 'full'
    cy, cx = y + h // 2, x + w // 2
    if cy < pu.TOP_BAND:
        return 'top'
    if cy >= pu.BOTTOM_BAND:
        return 'bottom'
    if cx < pu.LEFT_COLUMN:
        return 'left'
    if cx >= pu.RIGHT_COLUMN:
        return 'right'
    return 'middle'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('log', type=Path)
    p.add_argument('--target', type=int, default=pu.OSD_TARGET)
    a = p.parse_args()
    boxes = collections.Counter()
    other_targets = collections.Counter()
    for line in a.log.read_text(encoding='utf-8').splitlines():
        r = json.loads(line)
        if 'kind' not in r:
            continue
        if r['target'] != a.target:
            other_targets[(r['target'], r['kind'], r['caller'])] += 1
            continue
        if r['view'] != 0:
            continue
        boxes[(r['kind'], r['x'], r['y'], r['w'], r['h'])] += 1
    by_zone = collections.defaultdict(list)
    for (kind, x, y, w, h), number in boxes.items():
        by_zone[zone(x, y, w, h)].append((y, x, kind, w, h, number))
    for name in ('top', 'left', 'middle', 'right', 'bottom', 'full'):
        print(f'== {name}')
        for y, x, kind, w, h, number in sorted(by_zone.get(name, [])):
            print(f'  {kind:7s} x{x:4d} y{y:4d} w{w:4d} h{h:4d}  x{number}')
    if other_targets:
        print('== other render targets (not moved by portrait-ui)')
        for (target, kind, caller), number in other_targets.most_common(30):
            print(f'  target {target:2d} {kind:7s} from {caller}  x{number}')


if __name__ == '__main__':
    main()
