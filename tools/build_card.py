"""Build the SD-card feature kit: resident EG + Z30/WL.PATCH (docs/card-loader.md).

Writes OUTDIR/EG209020.BIN and .json (the resident firmware: stock, CJK fonts and
the card loader) and OUTDIR/Z30/WL.PATCH for the chosen presets. A card patch
only loads on the resident firmware it was built with.

Usage: build_card.py --source Z_30_0120.bin --output OUTDIR [--preset full ...] [--patch-only]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--preset', nargs='+', default=['full'])
    p.add_argument('--patch-only', action='store_true', help='only Z30/WL.PATCH (same resident firmware)')
    a = p.parse_args()
    from z30_extensions import card
    from z30_extensions.container import extract_eg, CONTAINER_SHA256
    eg, meta, patch_path = (a.output / 'EG209020.BIN', a.output / 'EG209020.json',
                            a.output / 'Z30' / 'WL.PATCH')
    outputs = [patch_path] + ([] if a.patch_only else [eg, meta])
    existing = [str(path) for path in outputs if path.exists()]
    if existing:
        sys.exit(f'Refusing to overwrite: {", ".join(existing)}')
    stock = extract_eg(a.source.read_bytes())
    image, report, resident, identity = card.build_resident(stock)
    if a.patch_only:
        # The card patch must match the resident EG already on the camera.
        if not meta.exists():
            sys.exit(f'--patch-only needs the resident report {meta} of the flashed EG')
        flashed = json.loads(meta.read_text(encoding='utf-8')).get('resident_id')
        if flashed != hex(identity):
            sys.exit(f'The resident firmware changed ({flashed} -> {identity:#x}), for example new label '
                     'characters outside the common sets or a loader change: build without --patch-only '
                     'and reflash the EG.')
    preset = a.preset[0] if len(a.preset) == 1 else a.preset
    patch, card_report = card.build_card(stock, preset, resident, identity, image)
    blob = card.pack(patch)
    a.output.mkdir(parents=True, exist_ok=True)
    if not a.patch_only:
        report['container_sha256'] = CONTAINER_SHA256
        report['card_patch'] = dict(presets=a.preset, **card_report['card'])
        meta.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        eg.write_bytes(image)
        print(f'Built {eg}: {report["sha256"]} (resident id {identity:#010x})')
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_bytes(blob)
    c = card_report['card']
    print(f'Built {patch_path}: {len(blob)} bytes, tail {c["tail_bytes"]}, {c["tail_relocs"]} relocations, '
          f'{c["edits"]} edits, presets {", ".join(a.preset)}')


if __name__ == '__main__':
    main()
