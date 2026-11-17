"""Build the auto-arm firmware: `full` without gyro-evr, plus the SD module host and
the auto_arm module built in (tracking box up by default, photo shooting menu row
"Auto tracking box", Enable without a saved file). Research option, local only.

The module source is modules/auto_arm; it is compiled here with
-DREMAP -DDIRECT -DBUILTIN (needs arm-none-eabi-gcc).

Usage: build_auto_arm.py --source Z_30_0120.bin --output OUTDIR
Writes OUTDIR/EG209020.BIN and OUTDIR/EG209020.json.
"""
import argparse, hashlib, json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--module-dir', type=Path, default=ROOT / 'modules' / 'auto_arm')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    from z30_extensions.container import extract_eg, CONTAINER_SHA256
    from z30_extensions.build import build, resolve_presets
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / 'auto_arm.bin'
        subprocess.run(['sh', str(a.module_dir / 'build.sh'), str(raw), '-DREMAP', '-DDIRECT', '-DBUILTIN'], check=True)
        code = raw.read_bytes()
    _, features = resolve_presets('full')
    features = [f for f in features if f != 'gyro-evr']
    image, report = build(extract_eg(a.source.read_bytes()), features, module_host=True, auto_arm=code)
    report['container_sha256'] = CONTAINER_SHA256
    report['research'] = 'auto-arm built in (modules/auto_arm -DREMAP -DDIRECT -DBUILTIN)'
    report['auto_arm_module_sha256'] = hashlib.sha256(code).hexdigest()
    a.output.mkdir(parents=True, exist_ok=True)
    eg, meta = a.output / 'EG209020.BIN', a.output / 'EG209020.json'
    if eg.exists() or meta.exists():
        sys.exit(f'{eg} or {meta} exists; refusing to overwrite')
    meta.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    eg.write_bytes(image)
    print(f'Built {eg}: {report["sha256"]}')


if __name__ == '__main__':
    main()
