"""Build the MMU-probe research kit for the SD-card feature loader (step 0).

Writes OUTDIR/Z30/CORE.ZMOD (src/z30_extensions/mmu_probe.py) and, unless
--zmod-only, OUTDIR/EG209020.BIN + .json: `full` without gyro-evr plus the SD
module host (the same EG as build_af_log.py --module log, minus the IMU hook).
A camera that already runs any module-host EG only needs the ZMOD.
See docs/card-loader-probe.md for the camera steps and how to read the lines.

Usage: build_mmu_probe.py --source Z_30_0120.bin --output OUTDIR [--zmod-only] [--probe 1|2]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--zmod-only', action='store_true', help='only Z30/CORE.ZMOD')
    p.add_argument('--probe', type=int, choices=(1, 2), default=2,
                   help='1: registers and tail write test; 2: DRAM copy range and stock-code write tests')
    a = p.parse_args()
    if not a.zmod_only and not a.source:
        p.error('--source is required unless --zmod-only')
    from z30_extensions.mmu_probe import build as build_probe
    zmod = a.output / 'Z30' / 'CORE.ZMOD'
    outputs = [zmod]
    if not a.zmod_only:
        outputs += [a.output / 'EG209020.BIN', a.output / 'EG209020.json']
    existing = [str(path) for path in outputs if path.exists()]
    if existing:
        sys.exit(f'Refusing to overwrite: {", ".join(existing)}')
    if not a.zmod_only:
        from z30_extensions.container import extract_eg, CONTAINER_SHA256
        from z30_extensions.build import build, resolve_presets
        _, features = resolve_presets('full')
        research = [f for f in features if f != 'gyro-evr']
        image, report = build(extract_eg(a.source.read_bytes()), research, module_host=True)
        report['container_sha256'] = CONTAINER_SHA256
        report['research'] = 'mmu-probe: is the EG code region RAM (docs/card-loader-probe.md)'
        a.output.mkdir(parents=True, exist_ok=True)
        outputs[2].write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        outputs[1].write_bytes(image)
        print(f'Built {outputs[1]}: {report["sha256"]}')
    zmod.parent.mkdir(parents=True, exist_ok=True)
    zmod.write_bytes(build_probe(a.probe))
    print(f'Built {zmod} (probe v{a.probe})')


if __name__ == '__main__':
    main()
