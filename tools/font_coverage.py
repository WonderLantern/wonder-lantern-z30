"""Extract or query the stock CJK UI font coverage of Z30 C 1.20.

  python tools/font_coverage.py --firmware Z_30_0120.bin --write   # refresh the JSON
  python tools/font_coverage.py --firmware Z_30_0120.bin --check   # JSON matches firmware
  python tools/font_coverage.py 橙色 光晕                          # which characters are missing

Reads the firmware only; writes nothing but the coverage JSON with --write.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.fonts import CJK_FONTS, HANS_FONT, HANT_FONT, font_ranges, missing_glyphs
from z30_extensions.platform.z30_120 import StockImage

OUTPUT = Path(__file__).resolve().parents[1] / 'src/z30_extensions/platform/z30_120_fonts.json'


def extract(firmware):
    image = StockImage(extract_eg(Path(firmware).read_bytes()))
    return {'firmware': 'Nikon Z30 C 1.20',
            'note': 'Code point runs (first, length) of the stock UI fonts; 0 = Traditional, 2 = Simplified.',
            'fonts': {str(font): font_ranges(image, font) for font in CJK_FONTS}}


def render(data):
    lines = [f'    "{font}": [' + ', '.join(f'[{a}, {b}]' for a, b in runs) + ']'
             for font, runs in data['fonts'].items()]
    return ('{\n' + f'  "firmware": {json.dumps(data["firmware"])},\n  "note": {json.dumps(data["note"])},\n'
            + '  "fonts": {\n' + ',\n'.join(lines) + '\n  }\n}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('text', nargs='*', help='labels to check against both fonts')
    parser.add_argument('--firmware', help='stock Z_30_0120.bin')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--write', action='store_true')
    action.add_argument('--check', action='store_true')
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    if args.write or args.check:
        if not args.firmware:
            parser.error('--write/--check need --firmware')
        text = render(extract(args.firmware))
        if args.write:
            OUTPUT.write_text(text, encoding='utf-8', newline='\n')
            print(f'wrote {OUTPUT}')
        elif OUTPUT.read_text(encoding='utf-8') != text:
            sys.exit(f'{OUTPUT.name} differs from the firmware fonts')
        else:
            print(f'{OUTPUT.name} matches the firmware fonts')
    failed = False
    for label in args.text:
        for name, font in (('Hans', HANS_FONT), ('Hant', HANT_FONT)):
            missing = missing_glyphs(label, font)
            failed |= bool(missing)
            print(f'{name} {label}: ' + (f'missing {" ".join(missing)}' if missing else 'ok'))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
