"""Build the SD-card feature kit: resident EG + Z30/WL.PATCH (docs/card-loader.md).

Writes OUTDIR/EG209020.BIN and .json (the resident firmware: stock, CJK fonts and
the card loader) and OUTDIR/Z30/WL.PATCH for the chosen presets.

With --patch-only the card patch is built for the resident EG already in OUTDIR
(the one on the camera), even if this tool would now build a newer resident. It
stops when the patch needs something that resident lacks: a newer loader, or
label characters outside its fonts. Then build a new resident and flash it.

--menu-tab puts the extension menu in an eighth tab of the menu tab bar instead
of a setup-menu row (docs/menu-framework.md); not yet verified on camera.

The same kit is built by `patch.py --card` and the GUI; this tool keeps the
developer switches.

Usage: build_card.py --source Z_30_0120.bin --output OUTDIR [--preset full ...] [--patch-only] [--menu-tab]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--preset', nargs='+', default=['full'])
    p.add_argument('--patch-only', action='store_true', help='only Z30/WL.PATCH, for the resident EG in OUTDIR')
    p.add_argument('--menu-tab', action='store_true', help='extension menu as an eighth menu tab')
    p.add_argument('--no-banner', action='store_true', help='no ready banner on the live view after loading')
    p.add_argument('--banner-debug', action='store_true',
                   help="show the ready banner's counters in the status page's version row")
    p.add_argument('--build-tag', help='version shown on the status page (default: date and git commit)')
    a = p.parse_args()
    from z30_extensions import card
    try:
        kit = card.write_kit(a.source, a.preset, a.output, patch_only=a.patch_only, menu_tab=a.menu_tab,
                             build_tag=a.build_tag, ready_banner=not a.no_banner,
                             banner_debug=a.banner_debug)
    except (ValueError, OSError) as error:
        sys.exit(f'{error}')
    print_kit(kit, a.preset)


def print_kit(kit, presets):
    resident, c = kit['resident'], kit['card']
    for path in kit['written']:
        if path.suffix == '.BIN':
            print(f'Built {path}: {kit["resident_sha256"]} (resident id {resident.identity:#010x})')
        elif path.name == 'WL.PATCH':
            print(f'Built {path}: {kit["patch_bytes"]} bytes, tail {c["tail_bytes"]}, {c["tail_relocs"]} relocations, '
                  f'{c["edits"]} edits ({c["ram_edits"]} in RAM), {c["init_functions"]} load-time functions, '
                  f'presets {", ".join(presets)} (resident id {resident.identity:#010x})')


if __name__ == '__main__':
    main()
