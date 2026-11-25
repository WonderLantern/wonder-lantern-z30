"""Collect peripheral registers that stock firmware writes in QEMU traces.

Scans z30-qemu devices.log files for `Z30_MMIO W` lines inside the ReadMemory
peripheral windows and writes the sorted register list with the writing PCs.
These are configuration registers the stock code programs, the first set that
is reasonable to read back on a camera.

Usage:
  python tools/collect_mmio_writes.py --qemu-local ../z30-qemu/.local --output build/research/mmio-candidates.json
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.features.research.ptp_read import MMIO_WINDOWS

LINE = re.compile(rb'Z30_MMIO W (0x[0-9a-f]{8}) size=4 value=(0x[0-9a-f]+) pc=(0x[0-9a-f]+)')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--qemu-local', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    registers = defaultdict(lambda: dict(values=set(), pcs=set()))
    logs = sorted(args.qemu_local.glob('*/devices.log'))
    for log in logs:
        for match in LINE.finditer(log.read_bytes()):
            address = int(match.group(1), 16)
            if any(low <= address < high for low, high in MMIO_WINDOWS):
                entry = registers[address]
                if len(entry['values']) < 16:
                    entry['values'].add(int(match.group(2), 16))
                entry['pcs'].add(int(match.group(3), 16))
    rows = [dict(address=hex(a), values=[hex(v) for v in sorted(e['values'])], pcs=[hex(p) for p in sorted(e['pcs'])])
            for a, e in sorted(registers.items())]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(logs=len(logs), windows=[[hex(a), hex(b)] for a, b in MMIO_WINDOWS],
                                           registers=rows), indent=1), encoding='utf-8')
    per_window = {hex(low): sum(1 for r in rows if low <= int(r['address'], 16) < high) for low, high in MMIO_WINDOWS}
    print(f'{len(rows)} registers from {len(logs)} logs: {per_window}')


if __name__ == '__main__':
    main()
