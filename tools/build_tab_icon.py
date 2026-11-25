"""Draw the extension menu tab icon: five 64 x 60 palette PNGs in the stock tab style.

The stock tab icons (image table 0x10E41BFC, icon sets at 0x10734DB4) are
opaque 64 x 60 8-bit palette PNGs, five per tab:
  0 focused tab: yellow ground, black symbol
  1 disabled: near-black ground, dark grey symbol
  2 selected, focus in the menu: dark yellow ground, black symbol
  3 current tab: the tab's own colour, white symbol
  4 normal: near-black ground, light grey symbol
The grounds and symbol colours below are measured from the stock setup tab;
the current-tab colour (teal) is one no stock tab uses. The symbol is a
lantern. Also the 3 x 480 strip drawn beside the tab bar while the tab is
current, in the same teal (stock strips: solid 1-bit palette PNGs, table
0x10734D74). Writes src/z30_extensions/platform/menu_tab_icon_<n>.png and
menu_tab_strip.png; builds only read those files (Pillow is needed here, not
for builds).

Usage: build_tab_icon.py [--preview build/tab-icons/lantern.png]
"""
import argparse
import io
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'src/z30_extensions/platform'
W, H, S = 64, 60, 8          # icon size and supersampling factor
STATES = (((252, 215, 35), (20, 20, 20)),     # 0 focused
          ((14, 14, 14), (70, 70, 70)),       # 1 disabled
          ((126, 103, 4), (20, 20, 20)),      # 2 selected
          ((0, 118, 128), (255, 255, 255)),   # 3 current (teal)
          ((14, 14, 14), (160, 160, 160)))    # 4 normal


def lantern_mask():
    """Antialiased coverage of the lantern symbol (L mode, 64 x 60)."""
    big = Image.new('L', (W * S, H * S), 0)
    d = ImageDraw.Draw(big)
    sx = lambda v: round(v * S)
    def poly(points):
        d.polygon([(sx(x), sx(y)) for x, y in points], fill=255)
    # Handle ring.
    d.ellipse([sx(26.5), sx(7.5), sx(37.5), sx(18)], outline=255, width=sx(2.6))
    # Cap.
    poly([(25.5, 16.5), (38.5, 16.5), (42, 22), (22, 22)])
    # Glass: a frame with two side posts, open in the middle.
    d.rounded_rectangle([sx(22.5), sx(21.5), sx(41.5), sx(42.5)], radius=sx(2.5), outline=255, width=sx(2.6))
    # Flame: a drop pointing up.
    poly([(32, 25.5), (35.2, 32), (28.8, 32)])
    d.ellipse([sx(28.6), sx(29), sx(35.4), sx(38.6)], fill=255)
    # Base.
    poly([(22, 42), (42, 42), (38.5, 47.5), (25.5, 47.5)])
    return big.resize((W, H), Image.LANCZOS)


def icon(mask, ground, symbol):
    rgb = Image.composite(Image.new('RGB', (W, H), symbol), Image.new('RGB', (W, H), ground), mask)
    pal = rgb.quantize(colors=64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = io.BytesIO()
    pal.save(out, format='PNG', optimize=True)
    return out.getvalue()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--preview', type=Path, help='also write a 4x preview sheet of the five states')
    a = p.parse_args()
    mask = lantern_mask()
    images = [icon(mask, ground, symbol) for ground, symbol in STATES]
    for n, data in enumerate(images):
        path = OUT / f'menu_tab_icon_{n}.png'
        path.write_bytes(data)
        print(f'{path.relative_to(ROOT)}: {len(data)} bytes')
    strip = Image.new('P', (3, 480), 0)
    strip.putpalette(list(STATES[3][0]))
    out = io.BytesIO()
    strip.save(out, format='PNG', optimize=True, bits=1)
    path = OUT / 'menu_tab_strip.png'
    path.write_bytes(out.getvalue())
    print(f'{path.relative_to(ROOT)}: {len(out.getvalue())} bytes')
    if a.preview:
        sheet = Image.new('RGB', (5 * (W + 6), H), (255, 0, 255))
        for n, data in enumerate(images):
            sheet.paste(Image.open(io.BytesIO(data)).convert('RGB'), (n * (W + 6), 0))
        a.preview.parent.mkdir(parents=True, exist_ok=True)
        sheet.resize((sheet.width * 4, sheet.height * 4), Image.NEAREST).save(a.preview)


if __name__ == '__main__':
    main()
