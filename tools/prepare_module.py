"""Create an SD host example, or wrap a caller's PIC Thumb binary."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.modules import example, pack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--example', choices=['counter', 'grid', 'menu-fix', 'mmu-probe', 'mmu-probe-v2'])
    source.add_argument('--code', type=Path)
    parser.add_argument('--entry', type=lambda s: int(s, 0), default=0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.example and args.entry:
        parser.error('--entry is only used with --code')
    if args.example in ('mmu-probe', 'mmu-probe-v2'):
        from z30_extensions.mmu_probe import build
        payload = build(2 if args.example.endswith('v2') else 1)
    else:
        payload = example(args.example) if args.example else pack(args.code.read_bytes(), args.entry)
    with args.output.open('xb') as stream:
        stream.write(payload)
    print(f'Created {args.output}: {len(payload)} bytes')


if __name__ == '__main__':
    main()
