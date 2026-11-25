"""Decode WL-SPI.BIN: CMOS register writes per Arm segment, and compare segments.

Each entry is one call of the stock serial writer. Write packets are
`81 addr_hi addr_lo data...`; data beyond the first 21 bytes was not stored
(the length field still says how long the packet was).

Usage:
  python tools/read_cmos_spi_log.py --input path/to/WL-SPI.BIN
  python tools/read_cmos_spi_log.py --input WL-SPI.BIN --compare 1 2 --json build/research/spi-12.json
"""
import argparse
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.features.research import cmos_spi as cs


def parse(data):
    if len(data) != cs.SPI_HEADER + cs.LOG_BYTES:
        raise ValueError(f'Unexpected size {len(data)}')
    magic, version, used, capacity, size, segment = struct.unpack_from('<6I', data)
    if (magic, version, capacity, size) != (cs.SPI_MAGIC, cs.SPI_VERSION, cs.CAPACITY, cs.ENTRY_BYTES):
        raise ValueError(f'Not a WL-SPI v{cs.SPI_VERSION} file from this build')
    return dict(used=used, capacity=capacity, last_segment=segment, full=used > capacity,
                entries=decode_ring(data[cs.SPI_HEADER:], used))


def decode_ring(ring, used, capacity=cs.CAPACITY, size=cs.ENTRY_BYTES):
    """Completed entries still in the ring, oldest first (entry n sits in slot (n-1) mod capacity)."""
    entries = []
    for slot in range(capacity):
        raw = ring[slot * size:][:size]
        number, meta = struct.unpack_from('<2I', raw)
        if not number or (number - 1) % capacity != slot or number > used or number + capacity <= used:
            continue  # empty, claimed but unfinished, or stale
        length = meta >> 16
        entries.append(dict(index=number, segment=meta & 0xFFFF, length=length,
                            packet=raw[8:8 + min(length, cs.ENTRY_DATA)]))
    entries.sort(key=lambda entry: entry['index'])
    return entries


def writes(entry):
    """Expand a write packet into (register, byte) pairs for the stored bytes."""
    packet = entry['packet']
    if len(packet) < 4 or packet[0] != 0x81:
        return []
    base = packet[1] << 8 | packet[2]
    return [(base + offset, value) for offset, value in enumerate(packet[3:])]


def summarize(entries):
    last, changes, order = {}, defaultdict(int), []
    for entry in entries:
        for register, value in writes(entry):
            if register in last and last[register] != value:
                changes[register] += 1
            if register not in last:
                order.append(register)
            last[register] = value
    return last, changes, order


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--compare', type=int, nargs=2, metavar=('A', 'B'))
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    log = parse(args.input.read_bytes())
    segments = defaultdict(list)
    for entry in log['entries']:
        segments[entry['segment']].append(entry)
    print(f"{len(log['entries'])} entries, {len(segments)} segments, full={log['full']}")
    summaries = {}
    for segment, entries in sorted(segments.items()):
        last, changes, _ = summarize(entries)
        summaries[segment] = (last, changes)
        others = sum(1 for e in entries if not writes(e))
        print(f"segment {segment}: {len(entries)} writes, {len(last)} register bytes, "
              f"{len(changes)} change during the segment, {others} non-write packets")
    out = dict(log={k: v for k, v in log.items() if k != 'entries'},
               segments={s: dict(writes=len(e), last={f'{r:04x}': v for r, v in sorted(summaries[s][0].items())},
                                 changing={f'{r:04x}': n for r, n in sorted(summaries[s][1].items())})
                         for s, e in segments.items()})
    if args.compare:
        a, b = args.compare
        missing = [s for s in (a, b) if s not in summaries]
        if missing:
            raise SystemExit(f'Segment(s) {missing} not in the log (full={log["full"]}); '
                             'shorten each segment and record again')
        last_a, change_a = summaries[a]
        last_b, change_b = summaries[b]
        rows = []
        for register in sorted(set(last_a) | set(last_b)):
            va, vb = last_a.get(register), last_b.get(register)
            if va != vb:
                rows.append(dict(register=f'{register:04x}', a=va, b=vb,
                                 changing_a=change_a.get(register, 0), changing_b=change_b.get(register, 0)))
        print(f'\nregister  seg{a}  seg{b}  (changes within segment)')
        for row in rows:
            fmt = lambda v: '   -' if v is None else f'{v:4d}'
            print(f"  {row['register']}  {fmt(row['a'])}  {fmt(row['b'])}   ({row['changing_a']}, {row['changing_b']})")
        out['compare'] = dict(a=a, b=b, rows=rows)
    if args.json:
        args.json.write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
