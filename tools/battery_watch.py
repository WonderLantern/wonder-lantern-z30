"""Log the BatteryCheckService block over USB (0x9FF1 ReadMemory), read-only.

Needs a build with ptp_read (build(..., ptp_read=True)) and the camera on USB.
Prints the +0x1C field (pack voltage in mV) of the block at 0xC236CE44 and the
stock battery icon state every INTERVAL seconds. The icon is widget descriptor
0xC1823E40: 14 entries of 0x30 bytes at 0xC1823BA0, byte [1] of the shown entry
is 1 (entries 0-5 are the battery icon, 2 per grade; the rest are other states).
"icon" is the list of shown entry numbers, "grade" the stock icon pair 0-2.

  python tools/battery_watch.py --seconds 600 --interval 10 --out build/research/battery.csv
"""
import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ptp_wpd import Camera

BLOCK = 0xC236CE44
ICON_ENTRIES, ICON_COUNT, ICON_SIZE = 0xC1823BA0, 14, 0x30


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    camera = Camera()
    sink = args.out.open('a', encoding='utf-8') if args.out else None
    end = time.time() + args.seconds
    while True:
        code, data = camera.run(0x9FF1, [BLOCK, 0x50])
        if code != 0x2001:
            raise SystemExit(f'ReadMemory answered {code:#06x}')
        words = struct.unpack('<20I', data)
        code, entries = camera.run(0x9FF1, [ICON_ENTRIES, ICON_COUNT * ICON_SIZE])
        if code != 0x2001:
            raise SystemExit(f'ReadMemory answered {code:#06x}')
        shown = [k for k in range(ICON_COUNT) if entries[k * ICON_SIZE + 1] == 1]
        grade = shown[0] // 2 if shown and shown[0] < 6 else -1
        line = (f'{time.strftime("%H:%M:%S")} mV={words[7]} +0x18={words[6]} '
                f'icon={shown} grade={grade}')
        print(line, flush=True)
        if sink:
            sink.write(line + '\n')
            sink.flush()
        if time.time() >= end:
            break
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
