"""Poll the traced redraw requests (0x10453FAC) and UI events (10310BF8 wrappers) over USB.

Redraw requests from the periodic refresh are written to --out only; everything else is printed.

  python tools/portrait_watch_requests.py --report output/portrait-ui-live-v6/EG209020.json --seconds 90
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
from portrait_live import Live  # noqa: E402

PERIODIC = 0x10324325


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=90)
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    live = Live(json.loads(a.report.read_text()))
    block = live.block_address()
    base = block + pm.REQ
    seen = struct.unpack('<I', live.read(base + 4, 4))[0]
    events = block + pm.EVT if hasattr(pm, 'EVT') else None
    seen_events = struct.unpack('<I', live.read(events + 4, 4))[0] if events else 0
    names = ('action', 'update', 'pair', 'slot20')
    out = a.out.open('w', encoding='utf-8') if a.out else None
    start = time.monotonic()
    while time.monotonic() - start < a.seconds:
        try:
            count = struct.unpack('<I', live.read(base + 4, 4))[0]
        except Exception as error:  # the WPD connection drops now and then; reopen it
            print(f'{time.monotonic() - start:6.2f} reconnect after {type(error).__name__}', flush=True)
            time.sleep(1)
            try:
                live = Live(json.loads(a.report.read_text()))
            except (Exception, SystemExit):
                pass
            continue
        if events:
            try:
                total = struct.unpack('<I', live.read(events + 4, 4))[0]
                if total > seen_events:
                    ring = live.read(events + 16, pm.EVT_CAPACITY * pm.EVT_STRIDE)
                    first = max(seen_events, total - pm.EVT_CAPACITY)
                    if first > seen_events:
                        print(f'{time.monotonic() - start:6.2f} events skipped {first - seen_events}', flush=True)
                    for seq in range(first, total):
                        tag, caller, r0, r1 = struct.unpack_from('<4I', ring, (seq % pm.EVT_CAPACITY) * pm.EVT_STRIDE)
                        row = dict(t=round(time.monotonic() - start, 2), event=names[tag & 3], seq=seq, pose=(tag >> 8) & 255,
                                   caller=hex(caller), r0=hex(r0), r1=hex(r1))
                        print(json.dumps(row), flush=True)
                        if out:
                            out.write(json.dumps(row) + chr(10))
                    seen_events = total
            except Exception:
                pass
        if count > seen:
            ring = live.read(base + 16, pm.REQ_CAPACITY * pm.REQ_STRIDE)
            first = max(seen, count - pm.REQ_CAPACITY)
            if first > seen:
                print(f'{time.monotonic() - start:6.2f} skipped {first - seen}', flush=True)
            for seq in range(first, count):
                caller, predicate, what, screens = struct.unpack_from('<4I', ring, (seq % pm.REQ_CAPACITY) * pm.REQ_STRIDE)
                try:
                    pose = live.word(0xC1D41318 + 0x90, 1)
                except Exception:
                    pose = None
                row = dict(t=round(time.monotonic() - start, 2), seq=seq, caller=hex(caller), what=hex(what),
                           screens=hex(screens), predicate=hex(predicate), pose=pose)
                if out:
                    out.write(json.dumps(row) + '\n')
                if caller != PERIODIC:
                    print(json.dumps(row), flush=True)
            seen = count
        time.sleep(0.05)
    if out:
        out.close()


if __name__ == '__main__':
    main()
