"""Time the repaint after a turn over USB: prints every change of the pose byte,
the layout mode, the pending clear and the two-pass repaint word, with a host
timestamp relative to the start.

  python tools/portrait_timing.py --report output/portrait-ui-live-v19/EG209020.json --seconds 60
"""
import argparse
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]
from z30_extensions.features import portrait_ui as pu  # noqa: E402
from portrait_live import Live  # noqa: E402

POSE_BYTE = 0xC1D41318 + 0x90


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--report', required=True)
    p.add_argument('--seconds', type=float, default=60)
    a = p.parse_args()
    live = Live(json.loads(Path(a.report).read_text()))
    block = live.block_address()
    if block is None:
        raise SystemExit('State block not allocated yet')
    start = time.monotonic()
    last = None
    while time.monotonic() - start < a.seconds:
        pose = live.word(POSE_BYTE, 1)
        mode, pending, repaint = struct.unpack('<3I', live.read(block + pu.R_MODE, 12))
        now = (pose, mode, pending, repaint)
        if now != last:
            print(f'{time.monotonic() - start:7.3f}s pose={pose} mode={mode} pending={pending} repaint={repaint}', flush=True)
            last = now
        time.sleep(0.01)


if __name__ == '__main__':
    main()
