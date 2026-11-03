"""Generate the glyphs added to the stock CJK UI fonts (z30_120_extra_glyphs.json).

  python tools/build_cjk_glyphs.py --firmware Z_30_0120.bin --write
  python tools/build_cjk_glyphs.py --firmware Z_30_0120.bin --check

Adds every printable ASCII character and every character of the Chinese UI
labels (ui_text.TRANSLATIONS) that stock fonts 0/2 lack. Han and other
full-width characters come from Noto Sans CJK TC/SC Regular, ASCII from the
half-width Noto Sans Mono CJK SC Regular (all SIL OFL 1.1, in --noto-dir).
Each font gets its own placement, least-squares fitted on the bounds of
characters both the stock font and the source font have. Needs fontTools;
builds only read the generated JSON.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.features.ui_text import TRANSLATIONS
from z30_extensions.platform.font_source import (FULL_WIDTH, HALF_WIDTH, OutlineSource,
                                                 fit_placement)
from z30_extensions.platform.fonts import (EXTRA_GLYPHS, HANS_FONT, HANT_FONT, character_map,
                                           decode_glyph, encode_glyph, glyph_addresses)
from z30_extensions.platform.z30_120 import StockImage

ASCII = ''.join(map(chr, range(0x20, 0x7F)))
SOURCES = {HANS_FONT: ('NotoSansCJKsc-Regular.otf', 0), HANT_FONT: ('NotoSansCJKtc-Regular.otf', 1)}
LATIN = 'NotoSansMonoCJKsc-Regular.otf'
OUTPUT = ROOT / 'src/z30_extensions/platform' / EXTRA_GLYPHS


def stock_glyphs(image, font):
    records = glyph_addresses(image, font)
    return {chr(cp): decode_glyph(image.read(records[glyph + cp - first], 4096))[0]
            for first, glyph, run in character_map(image, font) for cp in range(first, first + run)}


def generate(firmware, noto):
    image = StockImage(extract_eg(Path(firmware).read_bytes()))
    latin = OutlineSource(noto / LATIN)
    fonts = {}
    for font, (name, column) in sorted(SOURCES.items()):
        stock = stock_glyphs(image, font)
        han = OutlineSource(noto / name)
        han_place = fit_placement(han, {c: g for c, g in stock.items()
                                        if '一' <= c <= '鿿' and g.flags == FULL_WIDTH})
        latin_place = fit_placement(latin, {c: g for c, g in stock.items()
                                            if c in ASCII and g.flags == HALF_WIDTH})
        wanted = set(ASCII) | {c for pair in TRANSLATIONS.values() for c in pair[column]}
        added = {}
        for char in sorted(wanted - set(stock)):
            if char.isascii():
                glyph = latin.glyph(char, latin_place, HALF_WIDTH)
            else:
                glyph = han.glyph(char, han_place, FULL_WIDTH)
            added[char] = encode_glyph(glyph).hex()
        fonts[str(font)] = added
    return {'source': 'Noto Sans CJK TC/SC Regular and Noto Sans Mono CJK SC Regular (SIL OFL 1.1), '
                      'outlines converted for the Z30 C 1.20 stock UI fonts; see THIRD_PARTY_NOTICES.md',
            'fonts': fonts}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--firmware', required=True)
    parser.add_argument('--noto-dir', type=Path, default=ROOT / 'build/fonts/noto-cjk')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--write', action='store_true')
    action.add_argument('--check', action='store_true')
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    data = generate(args.firmware, args.noto_dir)
    text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + '\n'
    if args.write:
        OUTPUT.write_text(text, encoding='utf-8', newline='\n')
        for font, glyphs in data['fonts'].items():
            print(f'font {font}: {len(glyphs)} glyphs: {"".join(glyphs)}')
    elif OUTPUT.read_text(encoding='utf-8') != text:
        sys.exit(f'{OUTPUT.name} differs from the generated glyphs')
    else:
        print(f'{OUTPUT.name} matches')


if __name__ == '__main__':
    main()
