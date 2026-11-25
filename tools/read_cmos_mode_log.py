"""Print the drive-mode log WL-MODE.BIN saved by the CMOS research page.

Each record is one stock command-4 call (main CMOS drive-mode write), oldest
first: the drive type the firmware chose and the upper parameters it came from.

Usage:
  python tools/read_cmos_mode_log.py --input path/to/WL-MODE.BIN [--json out.json]
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.features.research import cmos_trace as ct


def parse(data):
    if len(data) != ct.LOG_HEADER + ct.RECORDS * ct.RECORD_BYTES:
        raise ValueError(f'Unexpected size {len(data)}')
    magic, version, sequence, records, size, request, parameters = struct.unpack_from('<7I', data)
    if (magic, version, records, size) != (ct.LOG_MAGIC, ct.LOG_VERSION, ct.RECORDS, ct.RECORD_BYTES):
        raise ValueError('Not a WL-MODE v1 file from this build')
    rows = []
    for slot in range(ct.RECORDS):
        raw = data[ct.LOG_HEADER + slot * size:][:size]
        number, command = struct.unpack_from('<2I', raw)
        if not number:
            continue
        req, par = raw[8:24], raw[24:64]
        rows.append(dict(sequence=number, command=command, drive_type=req[0], p0=par[0], p1=par[1],
                         p2=par[2], p3=par[3], p27=par[0x27], request=req.hex(), parameters=par.hex()))
    rows.sort(key=lambda row: row['sequence'])
    lost = max(0, sequence - ct.RECORDS)
    return dict(next_sequence=sequence, records=len(rows), overwritten=lost, rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    log = parse(args.input.read_bytes())
    print(f"{log['records']} records, next sequence {log['next_sequence']}, overwritten {log['overwritten']}")
    print(' seq  type  p0 p1 p2 p3 p27  request')
    for row in log['rows']:
        print(f"{row['sequence']:4}  {row['drive_type']:4}  {row['p0']:2} {row['p1']:2} {row['p2']:2} "
              f"{row['p3']:2} {row['p27']:3}  {row['request']}")
    if args.json:
        args.json.write_text(json.dumps(log, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
