# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Build-time pixels of the live-view ready banner, drawn with the camera's own fonts.

The stock CJK UI fonts (plus the glyphs cjk_fonts adds) are quadratic outlines;
this module fills them with a nonzero-winding scanline filler at 4x4 samples per
pixel, in pure Python, so builds need no imaging library. The banner is a dark
rounded box with a teal accent bar (the menu tab's colour) and white text, as
32-bit pixels in memory order R, G, B, A (OpenVG VG_sABGR_8888, not premultiplied).
"""
import struct
import zlib
from functools import lru_cache

from ...platform.fonts import character_map, decode_glyph, default_advances, extra_glyphs, glyph_addresses

EM_SIZE = 2048                   # font units per em (platform/fonts.py)

SAMPLES = 4
TEXT_PX = 28                     # em size in pixels
HEIGHT = 48
PAD_LEFT, PAD_RIGHT = 22, 16
ACCENT_W = 6
RADIUS = 8
BACKGROUND = (18, 20, 22, 224)
ACCENT = (0, 168, 182, 255)
TEXT = (255, 255, 255, 255)
BRAND = (110, 226, 236, 255)     # 'Wonder Lantern' in a light teal
ASCENT = 1760                    # stock CJK fonts' baseline from the top, in font units


def _glyph(image, font, ch, cache={}):
    key = (id(image), font)
    if key not in cache:
        cmap = {}
        for first, glyph, run in character_map(image, font):
            for i in range(max(run, 1) if run else 1):
                cmap[first + i] = glyph + i
        cache[key] = (cmap, glyph_addresses(image, font), default_advances(image, font))
    cmap, addresses, advances = cache[key]
    extra = extra_glyphs(font)
    if ch in extra:
        glyph = decode_glyph(bytes.fromhex(extra[ch]))[0]
    else:
        gid = cmap.get(ord(ch))
        if gid is None or gid not in addresses:
            raise ValueError(f'Font {font} cannot draw {ch!r}')
        glyph = decode_glyph(image.read(addresses[gid], 0x1000))[0]
    kind = (glyph.flags >> 7) & 3
    advance = glyph.advance if kind == 3 else advances[kind]
    return glyph, advance


def _flatten(contour, steps=6):
    """TrueType contour [(x, y, on), ...] -> closed polygon (on-curve points and curve samples)."""
    pts = [(x, y, bool(on)) for x, y, on in contour]
    n = len(pts)
    if n < 2:
        return []
    seq = []
    for i, p in enumerate(pts):          # two off-curve points imply the on-curve midpoint
        q = pts[(i + 1) % n]
        seq.append(p)
        if not p[2] and not q[2]:
            seq.append(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, True))
    k = next(i for i, p in enumerate(seq) if p[2])
    seq = seq[k:] + seq[:k]
    m, out, cur, i = len(seq), [seq[0][:2]], seq[0], 1
    while i < m + 1:
        p = seq[i % m]
        if p[2]:
            out.append(p[:2])
            cur, i = p, i + 1
            continue
        e = seq[(i + 1) % m]
        for s in range(1, steps + 1):
            t = s / steps
            u = 1 - t
            out.append((u * u * cur[0] + 2 * u * t * p[0] + t * t * e[0],
                        u * u * cur[1] + 2 * u * t * p[1] + t * t * e[1]))
        cur, i = e, i + 2
    return out[:-1]


def _edges(polygons):
    edges = []
    for poly in polygons:
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
            if y0 != y1:
                edges.append((x0, y0, x1, y1, 1 if y1 > y0 else -1))
    return edges


def _fill(edges, width, height):
    """Coverage 0..SAMPLES*SAMPLES per pixel (row-major) for `edges` in sample units."""
    sw = width * SAMPLES
    cover = [0] * (width * height)
    for sy in range(height * SAMPLES):
        yc = sy + 0.5
        crossings = []
        for x0, y0, x1, y1, d in edges:
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                crossings.append((x0 + (yc - y0) * (x1 - x0) / (y1 - y0), d))
        if not crossings:
            continue
        crossings.sort()
        winding, row = 0, (sy // SAMPLES) * width
        for (xa, d), (xb, _) in zip(crossings, crossings[1:] + [(sw, 0)]):
            winding += d
            if winding:
                a, b = max(0, int(xa + 0.5)), min(sw, int(xb + 0.5))
                for sx in range(a, b):
                    cover[row + sx // SAMPLES] += 1
    return cover


def _text_edges(image, font, text, x0):
    """Edges of `text` in sample units with its pen starting at pixel x0; and the end pen x."""
    scale = TEXT_PX / EM_SIZE * SAMPLES
    middle = HEIGHT * SAMPLES / 2          # the em box (ASCENT .. ASCENT - EM) centred vertically
    pen = x0 * SAMPLES
    polygons = []
    for ch in text:
        if ch == ' ':
            pen += EM_SIZE / 4 * scale
            continue
        if ch == '·':
            # Our own separator dot: the fonts' middle dots sit differently in Hans and Hant.
            import math
            cx, r = pen + EM_SIZE / 4 * scale, 0.07 * TEXT_PX * SAMPLES
            polygons.append([(cx + r * math.cos(k * math.pi / 8), middle + r * math.sin(k * math.pi / 8))
                             for k in range(16)])
            pen += EM_SIZE / 2 * scale
            continue
        glyph, advance = _glyph(image, font, ch)
        for contour in glyph.contours:
            polygons.append([(pen + x * scale, middle - (y - (ASCENT - EM_SIZE / 2)) * scale)
                             for x, y in _flatten(contour)])
        pen += advance * scale
    return _edges(polygons), pen / SAMPLES


def _inside_rounded(x, y, w, h, r):
    cx = min(max(x, r), w - r)
    cy = min(max(y, r), h - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def text_width(image, font, brand, text):
    """Width in pixels of the banner box for this text (before any widening)."""
    _, pen = _text_edges(image, font, brand, PAD_LEFT)
    _, end = _text_edges(image, font, text, pen)
    return int(end + 0.999) + PAD_RIGHT


@lru_cache(maxsize=None)
def banner(image, font, brand, text, width=None):
    """(width, height, RGBA bytes) of the banner: `brand` in light teal, then `text` in white.

    `width` widens the box (text stays left), so several languages can share one size.
    """
    brand_edges, pen = _text_edges(image, font, brand, PAD_LEFT)
    edges, end = _text_edges(image, font, text, pen)
    width = max(width or 0, int(end + 0.999) + PAD_RIGHT)
    cover = _fill(edges, width, HEIGHT)
    brand_cover = _fill(brand_edges, width, HEIGHT)
    full = SAMPLES * SAMPLES
    out = bytearray(width * HEIGHT * 4)
    for y in range(HEIGHT):
        for x in range(width):
            # Background box with antialiased rounded corners (2x2 samples).
            inside = sum(_inside_rounded(x + (i + .5) / 2, y + (j + .5) / 2, width, HEIGHT, RADIUS)
                         for i in range(2) for j in range(2)) / 4
            if not inside:
                continue
            r, g, b, a = ACCENT if x < ACCENT_W else BACKGROUND
            a = a * inside
            for colour, coverage in ((BRAND, brand_cover), (TEXT, cover)):
                t = coverage[y * width + x] / full
                if t:
                    r, g, b = (round(c0 + (c1 - c0) * t) for c0, c1 in zip((r, g, b), colour[:3]))
                    a = a + (255 - a) * t
            out[(y * width + x) * 4:(y * width + x) * 4 + 4] = bytes((r, g, b, round(a)))
    return width, HEIGHT, bytes(out)


def png(width, height, rgba):
    """A plain RGBA PNG (preview only)."""
    raw = b''.join(b'\0' + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))
