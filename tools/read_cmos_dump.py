"""Validate a WL-CMOS.BIN saved by the CMOS dump page and split out its parts.

The file holds Nikon drive data and per-body calibration. Keep it local; do not
commit or share it. Outputs go to a new directory: header.json, context.bin and
drive-data.bin.

Usage:
  python tools/read_cmos_dump.py --input path/to/WL-CMOS.BIN --output-dir build/research/cmos-dump-camera
  python tools/read_cmos_dump.py --input first.BIN --compare second.BIN --output-dir build/research/cmos-dump-diff

The header sum is taken before the copy. If part of the area changes while
the camera runs, the written bytes differ from that sum; --allow-sum-mismatch
keeps going and records it, and --compare lists which ranges differ between
two dumps so live ranges can be told apart from transfer errors.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.features import cmos_dump as cd


def ranges(first, second, granule=16):
    """Differing [start, end) ranges at a 16-byte granule, merged when adjacent."""
    out = []
    for offset in range(0, len(first), granule):
        if first[offset:offset + granule] != second[offset:offset + granule]:
            if out and out[-1][1] == offset:
                out[-1][1] = offset + granule
            else:
                out.append([offset, offset + granule])
    return out


def parse(data, allow_sum_mismatch=False):
    if len(data) != cd.HEADER_BYTES + cd.BLOB_BYTES:
        raise ValueError(f'Expected {cd.HEADER_BYTES + cd.BLOB_BYTES} bytes, got {len(data)}')
    magic, version, pointer, length, blob_sum, ctx, area_address, bank_address, area_word = \
        struct.unpack_from('<9I', data)
    if magic == cd.MAGIC and version == 1:
        raise ValueError('v1 dump: written without a cache clean, so most bytes are stale DRAM; '
                         'save again with the v2 build')
    if magic != cd.MAGIC or version != cd.VERSION:
        raise ValueError(f'Not a WL-CMOS v{cd.VERSION} file')
    if (length, ctx, area_address, bank_address) != (cd.BLOB_BYTES, cd.CTX, cd.AREA, cd.BANK):
        raise ValueError('Header fields differ from this firmware build')
    if any(data[36:0x40]):
        raise ValueError('Reserved header bytes are not zero')
    blob = data[cd.HEADER_BYTES:]
    actual = sum(struct.unpack(f'<{len(blob) // 4}I', blob)) & 0xFFFFFFFF
    if actual != blob_sum and not allow_sum_mismatch:
        raise ValueError(f'Drive data word sum {actual:#x} differs from header {blob_sum:#x}; '
                         'rerun with --allow-sum-mismatch and compare a second dump')
    context = data[0x40:0x140]
    area = struct.unpack_from('<7I', data, 0x140)
    bank = struct.unpack_from('<11I', data, 0x160)
    if struct.unpack_from('<I', context, cd.BLOB_FIELD)[0] != pointer:
        raise ValueError('Context pointer differs from the saved pointer')
    if area[0] != 0x4C or area[3] != cd.BLOB_BYTES or area[5] != 26 or area[2] != area_word:
        raise ValueError(f'Unexpected area descriptor {area}')
    summary = dict(
        pointer=hex(pointer), area_address_word=hex(area_word), pointer_matches_area=area_word == pointer,
        blob_sha256=hashlib.sha256(blob).hexdigest(), header_word_sum=hex(blob_sum),
        written_word_sum=hex(actual), sum_matches=actual == blob_sum,
        zero_bytes=blob.count(0), ff_bytes=blob.count(255),
        area_descriptor=[hex(x) for x in area], bank_descriptor=[hex(x) for x in bank],
        nor_address=hex(bank[0]))
    return summary, context, blob


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--compare', type=Path, help='second dump of the same body')
    parser.add_argument('--allow-sum-mismatch', action='store_true')
    args = parser.parse_args()
    allow = args.allow_sum_mismatch or bool(args.compare)
    summary, context, blob = parse(args.input.read_bytes(), allow)
    if args.compare:
        other, other_context, other_blob = parse(args.compare.read_bytes(), True)
        diff = ranges(blob, other_blob)
        summary['compare'] = dict(file=str(args.compare), sum_matches=other['sum_matches'],
                                  pointer_same=other['pointer'] == summary['pointer'],
                                  differing_bytes_16=sum(b - a for a, b in diff),
                                  differing_ranges=[[hex(a), hex(b)] for a, b in diff[:200]],
                                  range_count=len(diff),
                                  context_differs=[hex(i) for i in range(0, 256, 4)
                                                   if context[i:i + 4] != other_context[i:i + 4]])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'context.bin').write_bytes(context)
    (args.output_dir / 'drive-data.bin').write_bytes(blob)
    (args.output_dir / 'header.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))
    if summary['zero_bytes'] == len(blob) or summary['ff_bytes'] == len(blob):
        print('Warning: drive data is uniform; the area may not have been loaded yet')


if __name__ == '__main__':
    main()
