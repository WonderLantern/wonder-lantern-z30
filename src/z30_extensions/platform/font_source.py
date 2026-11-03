"""Glyphs for the stock CJK UI fonts from OFL OpenType fonts (build-time only).

Noto Sans CJK Regular matches the stock CJK glyphs closely (1.02-1.05x the
stock ink area, 1.00x the bounds over 300 common characters); Noto Sans Mono
CJK supplies half-width Latin like the CJK fonts' own. Outlines are mapped
into the stock font by a per-axis affine fit, converted from cubic to
quadratic curves (TrueType direction) and given stock metrics: flags 0x12
(full width, header advance 2048) or 0x92 (half width, 1024) with vertical
bearings (x_min - advance / 2, 1760 - y_max). Needs fontTools.
"""
from dataclasses import dataclass

from .fonts import Glyph

EM, ASCENT = 2048, 1760
FULL_WIDTH, HALF_WIDTH = 0x0012, 0x0092
ADVANCES = {FULL_WIDTH: 2048, HALF_WIDTH: 1024}
MAX_ERROR = 1.0            # cu2qu tolerance in source font units


@dataclass(frozen=True)
class Placement:
    """font units -> stock units: x * x_scale + dx, y * y_scale + dy."""
    x_scale: float
    y_scale: float
    dx: float = 0
    dy: float = 0


def fit_axis(pairs):
    """Least-squares (scale, offset) mapping source to stock coordinates."""
    n = len(pairs)
    sx = sum(s for s, _ in pairs); sy = sum(t for _, t in pairs)
    sxx = sum(s * s for s, _ in pairs); sxy = sum(s * t for s, t in pairs)
    scale = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    return scale, (sy - scale * sx) / n


class OutlineSource:
    def __init__(self, path):
        from fontTools.ttLib import TTFont
        self.font = TTFont(path)
        self.cmap = self.font.getBestCmap()
        self.glyphs = self.font.getGlyphSet()
        scale = EM / self.font['head'].unitsPerEm
        # Default for Noto Sans CJK: ascent 880 * 2.048 = 1802 against 1760.
        self.default = Placement(scale, scale, 0, -42)

    def __contains__(self, char):
        return ord(char) in self.cmap

    def contours(self, char):
        """Quadratic contours [(x, y, on_curve), ...] in source font units."""
        from fontTools.pens.cu2quPen import Cu2QuPen
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        pen = TTGlyphPen(None)
        self.glyphs[self.cmap[ord(char)]].draw(Cu2QuPen(pen, MAX_ERROR, reverse_direction=True))
        outline = pen.glyph()
        contours, start = [], 0
        for end in outline.endPtsOfContours:
            contour = [(x, y, flag & 1) for (x, y), flag in
                       zip(outline.coordinates[start:end + 1], outline.flags[start:end + 1])]
            if len(contour) > 2:
                contours.append(contour)
            start = end + 1
        return contours

    def bounds(self, char):
        points = [p for c in self.contours(char) for p in c]
        return (min(x for x, _y, _o in points), min(y for _x, y, _o in points),
                max(x for x, _y, _o in points), max(y for _x, y, _o in points))

    def glyph(self, char, placement=None, flags=FULL_WIDTH):
        """Stock-format Glyph for `char`."""
        p = placement or self.default
        contours = [[(round(x * p.x_scale + p.dx), round(y * p.y_scale + p.dy), on) for x, y, on in c]
                    for c in self.contours(char)]
        if not contours:
            raise ValueError(f'{char!r} has no outline')
        xs = [x for c in contours for x, _y, _on in c]
        ys = [y for c in contours for _x, y, _on in c]
        return Glyph(flags, contours, size=(max(xs) - min(xs), max(ys) - min(ys)), x_min=min(xs),
                     y_max=max(ys), bearings=(min(xs) - ADVANCES[flags] // 2, ASCENT - max(ys)))


def fit_placement(source, stock_glyphs):
    """Placement fitted on {char: stock Glyph} bounds shared with `source`."""
    xs, ys = [], []
    for char, glyph in stock_glyphs.items():
        if char not in source or not glyph.contours:
            continue
        x0, y0, x1, y1 = source.bounds(char)
        xs += [(x0, glyph.x_min), (x1, glyph.x_min + glyph.size[0])]
        ys += [(y0, glyph.y_max - glyph.size[1]), (y1, glyph.y_max)]
    if len(xs) < 8:
        raise ValueError('Too few shared glyphs to fit a placement')
    (x_scale, dx), (y_scale, dy) = fit_axis(xs), fit_axis(ys)
    return Placement(x_scale, y_scale, dx, dy)
