"""Poll the portrait runtime words over USB and print each change (mode, pending clear,
repaint flag, injected repaints, half-press flag, event count, pose).

  python tools/portrait_watch_state.py --report output/portrait-ui-live-v10/EG209020.json --seconds 60
"""
import argparse
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]
from z30_extensions.features import portrait_menu as pm, portrait_ui as pu  # noqa: E402
from portrait_live import Live  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=60)
    a = p.parse_args()
    live = Live(json.loads(a.report.read_text()))
    block = live.block_address()
    start, last = time.monotonic(), None
    while time.monotonic() - start < a.seconds:
        try:
            mode, pending, repaint = struct.unpack('<3I', live.read(block + pu.R_MODE, 12))
            _, events, held, injected = struct.unpack('<4I', live.read(block + pm.EVT, 16))
            pose = live.word(pu.POSE_BYTE, 1)
            view = live.word(pu.VIEW_KIND, 1)
            phase = live.word(pu.UI_SERVICE_PHASE, 1)
        except Exception as error:
            print(f'{time.monotonic() - start:6.2f} read failed: {type(error).__name__}', flush=True)
            time.sleep(1)
            continue
        state = (pose, mode, pending, repaint, injected, held, view, phase)
        if state != last:
            print(f'{time.monotonic() - start:6.2f} pose={pose} mode={mode} pending={pending} repaint={repaint} '
                  f'injected={injected} held={held} view={view} phase={phase} events={events}', flush=True)
            last = state
        time.sleep(0.1)


if __name__ == '__main__':
    main()
