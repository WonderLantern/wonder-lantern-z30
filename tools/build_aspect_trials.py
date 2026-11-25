"""Build independent emulator-only aspect trials from stock C1.20."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from z30_extensions.container import extract_eg
from z30_extensions.features.aspect.geometry import PROFILES, DX, dimensions, build_trial

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'Z_30_0120.bin')
    parser.add_argument('--profile', choices=list(PROFILES) + ['all'], default='all')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build/research/aspect-trials')
    args = parser.parse_args()
    stock = extract_eg(args.input.read_bytes())
    profiles = list(PROFILES) if args.profile == 'all' else [args.profile]
    for profile in profiles:
        candidate, report = build_trial(stock, profile)
        folder = args.output_dir / profile
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'research.eg.bin').write_bytes(candidate)
        (folder / 'research.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(profile, report['target_jpeg_wh'])
