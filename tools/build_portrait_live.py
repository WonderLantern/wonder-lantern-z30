"""Build full + portrait-ui with the read-only USB PTP operation 0x9FF1 ReadMemory.

For live readouts with tools/portrait_live.py while testing on the camera.
Research build: the extra USB operation only reads memory, and the Portrait UI page
shows its readout rows and exports WL-VUI.DBG when opened.

  python tools/build_portrait_live.py --source Z_30_0120.bin --output output/portrait-ui-live/EG209020.BIN
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.build import build  # noqa: E402
from z30_extensions.container import extract_eg  # noqa: E402
from z30_extensions.features import portrait_menu  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True, help='Z_30_0120.bin')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--presets', nargs='+', default=['full'])
    a = p.parse_args()
    report_path = a.output.with_suffix('.json')
    if a.output.exists() or report_path.exists():
        raise SystemExit('Output exists; choose a new path')
    portrait_menu.set_diagnostics(True)
    image, report = build(extract_eg(a.source.read_bytes()), a.presets, ptp_read=True)
    report['research'] = 'portrait-ui live readout over USB (0x9FF1 ReadMemory)'
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_bytes(image)
    report_path.write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8')
    print(f"Built {a.output}: {report['sha256']}")
    print('Enabled features:', ', '.join(report['enabled_features']))


if __name__ == '__main__':
    main()
