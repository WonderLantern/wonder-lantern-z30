"""Build a research EG whose only addition is a read-only CMOS drive-data dump page.

The image contains the owned menu framework (shutter-sound) and one custom-menu
page that saves A:\\WL-CMOS.BIN. It does not include the full feature set.

Usage:
  python tools/build_cmos_dump.py --source Z_30_0120.bin --output output/cmos-dump/EG209020.BIN
  python tools/build_cmos_dump.py --trace --source Z_30_0120.bin --output output/cmos-trace/EG209020.BIN
  python tools/build_cmos_dump.py --spi --source Z_30_0120.bin --output output/cmos-spi/EG209020.BIN
  python tools/build_cmos_dump.py --ptp --source Z_30_0120.bin --output output/cmos-ptp/EG209020.BIN
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.plan import Plan
from z30_extensions.features.shutter_sound import add


def build(stock, trace=False, spi=False, ptp=False, window=False):
    plan = Plan(stock)
    ptp = ptp or window
    spi = spi or ptp
    if window:
        add(plan, cmos_window=True)
    elif spi:
        add(plan, cmos_spi=True)
    elif trace:
        add(plan, cmos_trace=True)
    else:
        add(plan, cmos_dump=True)
    if ptp:
        from z30_extensions.features.ptp_read import add as add_ptp_read
        add_ptp_read(plan)
    image, report = plan.finish()
    report.update(preset='cmos-window-research' if window else 'cmos-ptp-research' if ptp else 'cmos-spi-research' if spi else 'cmos-trace-research' if trace else 'cmos-dump-research',
                  enabled_features=['shutter-sound-menu-framework', 'cmos-dump']
                  + (['cmos-mode-log'] if trace or spi else []) + (['cmos-spi-log'] if spi else [])
                  + (['ptp-read-memory'] if ptp else []) + (['cmos-4k-window-experiment'] if window else []),
                  research=True, includes_full_features=False)
    return image, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trace', action='store_true', help='also log drive-mode changes (WL-MODE.BIN)')
    parser.add_argument('--spi', action='store_true', help='trace plus an armed log of every CMOS SPI write (WL-SPI.BIN)')
    parser.add_argument('--ptp', action='store_true', help='spi plus the read-only USB PTP operation 0x9FF1 ReadMemory')
    parser.add_argument('--window', action='store_true', help='ptp plus the 4K readout-window experiment page')
    args = parser.parse_args()
    metadata = args.output.with_suffix('.json')
    if args.output.exists() or metadata.exists():
        parser.error('Choose new output paths')
    image, report = build(extract_eg(args.source.read_bytes()), trace=args.trace, spi=args.spi, ptp=args.ptp, window=args.window)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as handle:
        handle.write(image)
    with metadata.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2)
        handle.write('\n')
    print(f'Built CMOS dump research image {args.output}; camera validation pending')


if __name__ == '__main__':
    main()
