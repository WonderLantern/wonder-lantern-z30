"""Render text with the stock Z30 C 1.20 CJK UI fonts' decoded outlines.

  python tools/render_stock_glyphs.py --firmware Z_30_0120.bin --font hans 设定菜单 -o out.png
  python tools/render_stock_glyphs.py --firmware Z_30_0120.bin --fill-from NotoSansCJKsc-Regular.otf 橙色光晕 -o out.png

Offline preview of `platform.fonts.decode_glyph`: quadratic contours are
flattened and filled even-odd (the camera's own rasterizer may differ in
hinting and anti-aliasing). Needs Pillow; reads the firmware only.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.fonts import (HANS_FONT, HANT_FONT, character_map, decode_glyph,
                                           default_advances, glyph_addresses)
from z30_extensions.platform.z30_120 import StockImage

EM, ASCENT, STEPS = 2048, 1760, 8


def flatten(contour):
    """Polygon of one closed quadratic contour (TrueType on/off-curve rules)."""
    points = [(x, y) for x, y, _ in contour]
    on = [bool(o) for *_, o in contour]
    if not any(on):
        start = ((points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2)
        order = list(range(1, len(points))) + [0]
    else:
        k = on.index(True)
        start, order = points[k], list(range(k + 1, len(points))) + list(range(k + 1))
    polygon, current, control = [start], start, None
    def curve(end):
        for t in (i / STEPS for i in range(1, STEPS + 1)):
            polygon.append(tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * c + t * t * b
                                 for a, c, b in zip(current, control, end)))
    for i in order:
        if on[i]:
            if control is None:
                polygon.append(points[i])
            else:
                curve(points[i]); control = None
            current = points[i]
        else:
            if control is not None:
                middle = tuple((a + b) / 2 for a, b in zip(control, points[i]))
                curve(middle); current = middle
            control = points[i]
    if control is not None:
        curve(start)
    return polygon


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('text')
    parser.add_argument('--firmware', required=True)
    parser.add_argument('--font', choices=('hans', 'hant'), default='hans')
    parser.add_argument('--size', type=int, default=64, help='pixels per em')
    parser.add_argument('--fill-from', type=Path, help='OTF/TTF for characters the stock font lacks')
    parser.add_argument('-o', '--output', type=Path, required=True)
    args = parser.parse_args()
    from PIL import Image, ImageChops, ImageDraw
    image = StockImage(extract_eg(Path(args.firmware).read_bytes()))
    font = HANS_FONT if args.font == 'hans' else HANT_FONT
    entries, records = character_map(image, font), glyph_addresses(image, font)
    advances = default_advances(image, font)
    source = None
    if args.fill_from:
        from z30_extensions.platform.font_source import OutlineSource
        source = OutlineSource(args.fill_from)
    supersample, scale = 4, args.size * 4 / EM
    canvas = Image.new('L', (int(len(args.text) * args.size * 4) + 32, args.size * 4 + 32), 0)
    pen = 16
    for char in args.text:
        glyph_id = next((glyph + ord(char) - first for first, glyph, run in entries
                         if first <= ord(char) < first + run), None)
        if glyph_id is not None:
            glyph, _ = decode_glyph(image.read(records[glyph_id], 4096))
        elif source is not None and char in source:
            glyph = source.glyph(char)
        else:
            sys.exit(f'{char!r} is not in the stock font')
        mask = Image.new('1', canvas.size, 0)
        for contour in glyph.contours:
            shape = Image.new('1', canvas.size, 0)
            ImageDraw.Draw(shape).polygon([(pen + x * scale, 16 + (ASCENT - y) * scale)
                                           for x, y in flatten(contour)], fill=1)
            mask = ImageChops.logical_xor(mask, shape)
        canvas.paste(255, mask=mask)
        advance = glyph.advance if glyph.advance is not None else advances[(glyph.flags >> 7) & 3]
        pen += advance * scale
    canvas = canvas.crop((0, 0, int(pen) + 16, canvas.height))
    canvas.resize((canvas.width // supersample, canvas.height // supersample), Image.LANCZOS).save(args.output)
    print(f'wrote {args.output}')


if __name__ == '__main__':
    main()
