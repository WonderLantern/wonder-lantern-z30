"""Follow the CMOS research logs live over USB with the 0x9FF1 ReadMemory operation.

Needs the cmos-ptp research build on the camera and the camera connected over
USB (Windows WPD). Read-only: nothing is sent to the camera except ReadMemory.
Open Setup > Extensions once after power-on (allocates the page state) and press
Arm SPI log once before `follow` so serial writes are recorded.

Usage:
  python tools/cmos_live.py --report output/cmos-ptp/EG209020.json status
  python tools/cmos_live.py --report output/cmos-ptp/EG209020.json follow --seconds 0 --out build/research/live.jsonl   # 0 = until Ctrl+C
  python tools/cmos_live.py --report ... read 0x10020000 64
  python tools/cmos_live.py --report ... drive-data --out build/research/drive-usb.bin
  python tools/cmos_live.py --report ... regs --window 0x20200000 --out build/research/regs-4k30.json
  python tools/cmos_live.py --report ... regdiff build/research/regs-4k30.json build/research/regs-4k24.json
"""
import argparse
import json
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from z30_extensions.features import cmos_dump as cd, cmos_trace as ct, cmos_spi as cs
from z30_extensions.features.sound_layout import STATE_ANCHOR, MAGIC as SOUND_MAGIC
from ptp_wpd import Camera
from read_cmos_spi_log import decode_ring

OPCODE = 0x9FF1


class Live:
    def __init__(self, report):
        features = report['features']
        if 'ptp_read' not in features:
            raise SystemExit('This build report has no ptp_read feature')
        self.feature = features.get('cmos_window') or features['cmos_spi']
        self.state_offset = self.feature['state_offset']
        self.camera = Camera()

    def read(self, address, length):
        code, data = self.camera.run(OPCODE, [address, length])
        if code != 0x2001:
            raise OSError(f'ReadMemory({address:#x}, {length}) answered {code:#06x}')
        return data

    def word(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def state(self):
        anchor = self.word(STATE_ANCHOR + 16)
        if not 0x80000000 <= anchor < 0xE0000000 or self.word(anchor + 56) != SOUND_MAGIC:
            return None
        return anchor + self.state_offset

    def status(self):
        page = self.state()
        if page is None:
            return dict(page_state=None, hint='Open Setup > Extensions once to allocate the page state')
        words = struct.unpack('<4I', self.read(page + cs.SPI_BUFFER, 16))
        return dict(page_state=hex(page), status=self.word(page + cd.STATUS_OFF),
                    mode_records=self.word(page + ct.LOG_INDEX),
                    spi_buffer=hex(words[0]), spi_armed=words[1], spi_segment=words[2], spi_entries=words[3])

    def mode_records(self, page):
        count = self.word(page + ct.LOG_INDEX)
        ring = self.read(page + ct.LOG_BASE, ct.RECORDS * ct.RECORD_BYTES)
        rows = []
        for slot in range(ct.RECORDS):
            raw = ring[slot * 64:(slot + 1) * 64]
            number, command = struct.unpack_from('<2I', raw)
            if number and number > count - ct.RECORDS:
                rows.append(dict(index=number, drive_type=raw[8], request=raw[8:24].hex()))
        return count, sorted(rows, key=lambda row: row['index'])


def follow(live, seconds, out):
    """Append every new mode record and SPI entry to `out` as JSON Lines; safe to stop at any time."""
    page = live.state()
    if page is None:
        raise SystemExit('Page state not allocated: open Setup > Extensions on the camera first')
    buffer = live.word(page + cs.SPI_BUFFER)
    if not buffer:
        raise SystemExit('SPI log buffer not allocated: press Arm SPI log on the camera first')
    seen_spi = live.word(page + cs.SPI_INDEX)
    seen_mode, _ = live.mode_records(page)
    print(f'following: spi entries start at {seen_spi}, mode records at {seen_mode}')
    out.parent.mkdir(parents=True, exist_ok=True)
    counts = dict(spi=0, modes=0, lost=0)
    start = time.time()
    with out.open('w', encoding='utf-8') as sink:
        def emit(kind, **fields):
            sink.write(json.dumps(dict(kind=kind, **fields)) + chr(10))
        emit('start', spi_index=seen_spi, mode_index=seen_mode, page=hex(page), buffer=hex(buffer))
        while seconds <= 0 or time.time() - start < seconds:
            now = round(time.time() - start, 3)
            count, rows = live.mode_records(page)
            for row in rows:
                if row['index'] > seen_mode:
                    emit('mode', host_seconds=now, **row)
                    counts['modes'] += 1
                    print(f'{now:7.2f}s  mode record {row["index"]}: drive type {row["drive_type"]}')
            seen_mode = count
            index = live.word(page + cs.SPI_INDEX)
            if index > seen_spi:
                if index - seen_spi > cs.CAPACITY:
                    counts['lost'] += index - seen_spi - cs.CAPACITY
                    emit('lost', host_seconds=now, entries=index - seen_spi - cs.CAPACITY)
                for entry in decode_ring(live.read(buffer, cs.LOG_BYTES), index):
                    if entry['index'] > seen_spi:
                        emit('spi', host_seconds=now, index=entry['index'], segment=entry['segment'],
                             length=entry['length'], packet=entry['packet'].hex())
                        counts['spi'] += 1
                seen_spi = index
            sink.flush()
            time.sleep(0.2)
    print(f"{counts['spi']} SPI entries, {counts['lost']} lost, {counts['modes']} mode changes -> {out}")


def load_follow(path):
    """Read a follow log back: (mode rows, spi entries)."""
    modes, entries = [], []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        (modes if row['kind'] == 'mode' else entries if row['kind'] == 'spi' else []).append(row)
    return modes, entries


def register_runs(candidates, windows=None):
    """Merge candidate registers into runs of consecutive words (each run <= 4 KiB, one window)."""
    addresses = sorted(int(row['address'], 16) for row in candidates['registers'])
    if windows:
        addresses = [a for a in addresses if any(low <= a < high for low, high in windows)]
    runs = []
    for address in addresses:
        if runs and address == runs[-1][1] and address - runs[-1][0] < 0x1000 and address & 0xFFF:
            runs[-1][1] = address + 4
        else:
            runs.append([address, address + 4])
    return runs


def snapshot(live, candidates, out, windows=None):
    runs = register_runs(candidates, windows)
    values = {}
    for start, end in runs:
        data = live.read(start, end - start)
        for offset in range(0, len(data), 4):
            values[f'{start + offset:#010x}'] = struct.unpack_from('<I', data, offset)[0]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(time=time.time(), runs=len(runs), values=values), indent=1), encoding='utf-8')
    print(f'{len(values)} registers in {len(runs)} reads -> {out}')


def regdiff(paths):
    shots = [json.loads(Path(p).read_text(encoding='utf-8'))['values'] for p in paths]
    keys = sorted(set().union(*shots))
    rows = [k for k in keys if len({shot.get(k) for shot in shots}) > 1]
    print('register    ' + '  '.join(f'{Path(p).stem:>12}' for p in paths))
    for k in rows:
        print(f'{k}  ' + '  '.join(f'{shot.get(k, 0):#12x}' if k in shot else f'{"-":>12}' for shot in shots)
              + '   ' + '  '.join(f'{shot.get(k, 0):>10d}' for shot in shots))
    print(f'{len(rows)} of {len(keys)} registers differ')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--report', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    f = sub.add_parser('follow')
    f.add_argument('--seconds', type=float, default=0, help='0 = run until interrupted')
    f.add_argument('--out', type=Path, required=True)
    r = sub.add_parser('read')
    r.add_argument('address', type=lambda x: int(x, 0))
    r.add_argument('length', type=lambda x: int(x, 0))
    r.add_argument('--out', type=Path)
    d = sub.add_parser('drive-data')
    d.add_argument('--out', type=Path, required=True)
    g = sub.add_parser('regs', help='snapshot candidate peripheral registers')
    g.add_argument('--candidates', type=Path, default=Path('build/research/mmio-candidates.json'))
    g.add_argument('--window', type=lambda x: int(x, 0), action='append', help='limit to the window starting here')
    g.add_argument('--out', type=Path, required=True)
    x = sub.add_parser('regdiff', help='compare snapshots (no camera needed)')
    x.add_argument('snapshots', nargs='+')
    args = parser.parse_args()
    if args.action == 'regdiff':
        return regdiff(args.snapshots)
    live = Live(json.loads(args.report.read_text(encoding='utf-8')))
    if args.action == 'status':
        print(json.dumps(live.status(), indent=2))
    elif args.action == 'follow':
        try:
            follow(live, args.seconds, args.out)
        except KeyboardInterrupt:
            print(f'stopped; everything read so far is in {args.out}')
    elif args.action == 'read':
        data = live.read(args.address, args.length)
        if args.out:
            args.out.write_bytes(data)
        print(f'{len(data)} bytes'); print(data[:512].hex(' '))
    elif args.action == 'regs':
        from z30_extensions.features.ptp_read import MMIO_WINDOWS
        windows = [w for w in MMIO_WINDOWS if not args.window or w[0] in args.window]
        snapshot(live, json.loads(args.candidates.read_text(encoding='utf-8')), args.out, windows)
    else:
        pointer = live.word(cd.CTX + cd.BLOB_FIELD)
        data = live.read(pointer, cd.BLOB_BYTES)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(data)
        print(f'drive data at {pointer:#x}: {len(data)} bytes -> {args.out} (private per-body data; keep local)')


if __name__ == '__main__':
    main()
