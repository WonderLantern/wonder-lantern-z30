"""Decode WL-VUI.DBG, written when the Portrait UI page opens.

Prints the readout words and the last draw commands recorded while the camera
was upright (oldest first): kind, render target, view kind, phase, the
landscape box (x, y-down, w, h) and the caller. Text records the glyph origin
and scale instead of a box. Pass --json to keep the decoded records.
"""
import argparse
import collections
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.features import portrait_menu as pm  # noqa: E402
from z30_extensions.features import portrait_ui as pu  # noqa: E402

KIND_NAMES = {v: k for k, v in pu.KINDS.items()}


def decode(data):
    if len(data) != pm.STATE_SIZE:
        raise ValueError(f'expected {pm.STATE_SIZE} bytes, got {len(data)}')
    words = lambda offset, count: struct.unpack_from(f'<{count}I', data, offset)
    switch, = words(0, 1)
    mode, pending, repaint, passes, forced, kinds, targets, phases, busy, copies = words(pu.R_MODE, 10)
    magic, count, frozen, capacity = words(pm.LOG, 4)
    records = []
    if magic == pm.LOG_MAGIC:
        first = max(0, count - capacity)
        for seq in range(first, count):
            kind, target, view, phase, x, y, w, h, caller = struct.unpack_from(
                '<4B4hI', data, pm.LOG + 16 + (seq % capacity) * pm.LOG_STRIDE)
            records.append(dict(seq=seq, kind=KIND_NAMES.get(kind, kind), target=target, view=view, phase=phase,
                                x=x, y=y, w=w, h=h, caller=hex(caller)))
    requests = []
    if hasattr(pm, 'REQ') and words(pm.REQ, 1)[0] == pm.REQ_MAGIC:
        total = words(pm.REQ + 4, 1)[0]
        for seq in range(max(0, total - pm.REQ_CAPACITY), total):
            caller, predicate, what, screens = struct.unpack_from('<4I', data, pm.REQ + 16 + (seq % pm.REQ_CAPACITY) * pm.REQ_STRIDE)
            requests.append(dict(seq=seq, caller=hex(caller), predicate=hex(predicate), what=hex(what), screens=hex(screens)))
    bits = lambda value: [i for i in range(32) if value >> i & 1]
    return dict(switch=switch, mode=mode, pending=pending, repaint_waiting=repaint, refresh_passes=passes,
                forced_repaints=forced, view_kinds=bits(kinds), targets=bits(targets), phases=bits(phases),
                overlay_bits=bits(busy), full_copy_targets=bits(copies),
                log=dict(valid=magic == pm.LOG_MAGIC, recorded=count, kept=len(records), frozen=frozen),
                records=records, requests=requests)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('file', type=Path)
    p.add_argument('--json', type=Path)
    a = p.parse_args()
    result = decode(a.file.read_bytes())
    for key, value in result.items():
        if key not in ('records', 'requests'):
            print(f'{key}: {value}')
    by = collections.Counter((r['kind'], r['target'], r['caller']) for r in result['records'])
    print('\ncommands by kind, target, caller:')
    for (kind, target, caller), number in by.most_common():
        print(f'  {number:4d}  {kind:7s} target {target:2d}  from {caller}')
    print('\nrecords (oldest first):')
    for r in result['records']:
        print(f"  {r['seq']:6d} {r['kind']:7s} t{r['target']:<2d} v{r['view']} p{r['phase']} "
              f"x{r['x']:5d} y{r['y']:5d} w{r['w']:5d} h{r['h']:5d}  {r['caller']}")
    print('\nredraw requests (oldest first):')
    for r in result['requests']:
        print(f"  {r['seq']:6d} from {r['caller']} predicate {r['predicate']} what {r['what']} screens {r['screens']}")
    if a.json:
        a.json.write_text(json.dumps(result, indent=1) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
