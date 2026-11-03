# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""Host reference for the film-style date stamp: font, layout and pixel contract.

Glyphs are slanted seven-segment shapes with pointed segment ends, like the LCD of
a film data back. Each segment is rasterised once on a 42-row grid into row runs;
the runs are scaled to the frame and drawn into the stock Y/C planes.

Like an LED exposed through the film base, the digits can glow: widened passes
only lighten, raising luma to a dim copy of the color and taking its chroma where
the frame is darker, then the digits themselves are drawn opaque. Both rules are
projections, so drawing twice with the same date on the same planes gives the
same pixels. The ARM core in
features.date_stamp must match stamp_frame() byte for byte;
tools/verify_date_stamp.py checks that in Unicorn.
"""
import struct

from .lut import rgb_to_ycbcr_int

# Saved in WL-STAMP.CFG: new formats only ever append, so old records keep their meaning.
MODE_OFF, MODE_DATE, MODE_DATE_TIME, MODE_MDY, MODE_DMY, MODE_DAY_TIME = range(6)
# Data-back colors, first the amber of an LED exposed through the film base. Cb-first
# interleaving is the same pending assumption as z30_extensions.lut.
COLORS_RGB = ((255, 112, 24), (235, 38, 28), (250, 205, 40), (245, 245, 245))


def _ycbcr(rgb):
    """The one place stamp colors become plane bytes; swap Cb and Cr here if the camera disagrees."""
    return rgb_to_ycbcr_int(*rgb)


COLORS = tuple(_ycbcr(rgb) for rgb in COLORS_RGB)
# Glow passes, outermost first: (grid units each run grows by, percent of the color).
# The digits follow as a final pass that grows by 0 and is drawn opaque.
HALO = ((7, 14), (5, 20), (3, 28), (1, 40))
PASS_GROWTH = tuple(grow for grow, _ in HALO) + (0,)
# The saved style word: color index in the low bits, this flag for the glow. Records
# from before the glow existed hold a bare color, so they restore with it off.
GLOW_FLAG = 4
STYLE_MAX = 2 * GLOW_FLAG - 1
PASS_COLORS = tuple(tuple(_ycbcr([v * percent // 100 for v in rgb]) for _, percent in HALO)
                    + (core,) for rgb, core in zip(COLORS_RGB, COLORS))
if len(COLORS) != GLOW_FLAG:
    raise ValueError('Date stamp colors must fill the bits below the glow flag')

# Format tokens: the date field offsets, then literals. The year draws its apostrophe.
YEAR, MONTH, DAY, HOUR, MINUTE, TOKEN_SPACE, TOKEN_COLON, TOKEN_END = 0, 2, 3, 4, 5, 6, 7, 8
FORMATS = {
    MODE_DATE: (YEAR, TOKEN_SPACE, MONTH, TOKEN_SPACE, DAY),
    MODE_DATE_TIME: (YEAR, TOKEN_SPACE, MONTH, TOKEN_SPACE, DAY, TOKEN_SPACE, HOUR, TOKEN_COLON, MINUTE),
    MODE_MDY: (MONTH, TOKEN_SPACE, DAY, TOKEN_SPACE, YEAR),
    MODE_DMY: (DAY, TOKEN_SPACE, MONTH, TOKEN_SPACE, YEAR),
    MODE_DAY_TIME: (DAY, TOKEN_SPACE, HOUR, TOKEN_COLON, MINUTE),
}
FORMAT_BYTES = 12

SPACE, APOSTROPHE, COLON = 10, 11, 12
GLYPH_HEIGHT = 42
DIGIT_WIDTH, STROKE, TIP_GAP, SLANT = 22, 6, 1.2, 0.1   # grid units; SLANT is shift per row
# Pixels = grid * scale // SCALE_DIVISOR, scale being the image width: glyphs reach about
# 3% of a 3:2 frame's height. Narrow thumbnails keep MIN_SCALE so the digits stay legible.
SCALE_DIVISOR = 2088
MIN_SCALE = 696
MARGIN_X_DIVISOR = 28
MARGIN_Y_DIVISOR = 20
# Stock capture orientation (info +0x2615) as the EXIF writer maps it: 1 is EXIF 8,
# shown turned 90 degrees counter-clockwise; 2 is EXIF 6, shown turned clockwise.
ORIENT_LANDSCAPE, ORIENT_CCW, ORIENT_CW = 0, 1, 2


def _segment_shapes():
    """Membership tests in unslanted grid space: segments a-g, apostrophe, colon dots."""
    half, left, right = STROKE / 2, STROKE / 2, DIGIT_WIDTH - STROKE / 2
    top, middle, bottom = STROKE / 2, GLYPH_HEIGHT / 2, GLYPH_HEIGHT - STROKE / 2
    def across(yc):
        return lambda x, y: abs(y - yc) <= half and min(x - left, right - x) - TIP_GAP >= abs(y - yc)
    def down(xc, y1, y2):
        return lambda x, y: abs(x - xc) <= half and min(y - y1, y2 - y) - TIP_GAP >= abs(x - xc)
    def dot(yc):
        return lambda x, y: abs(x - half) + abs(y - yc) <= STROKE * 0.7
    return (across(top), down(right, top, middle), down(right, middle, bottom), across(bottom),
            down(left, middle, bottom), down(left, top, middle), across(middle),
            lambda x, y: abs(x - half) <= half and GLYPH_HEIGHT * 0.27 - y >= abs(x - half) * 1.5,
            dot(middle - GLYPH_HEIGHT * 0.18), dot(middle + GLYPH_HEIGHT * 0.18))


def _primitives():
    """Rows sampled at cell centres after the slant, merged into (bit, x, y, w, h) runs."""
    runs = []
    for bit, inside in enumerate(_segment_shapes()):
        for y in range(GLYPH_HEIGHT):
            centre = y + 0.5
            cells = [x for x in range(2 * DIGIT_WIDTH)
                     if inside(x + 0.5 - (GLYPH_HEIGHT - centre) * SLANT, centre)]
            if not cells:
                continue
            x, w = cells[0], len(cells)
            last = runs[-1] if runs else None
            if last and last[0] == bit and last[1:2] + last[3:4] == (x, w) and last[2] + last[4] == y:
                runs[-1] = last[:4] + (last[4] + 1,)
            else:
                runs.append((bit, x, y, w, 1))
    return tuple(runs)


PRIMITIVES = _primitives()
SEGMENTS = (0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F)
GLYPH_MASKS = SEGMENTS + (0, 1 << 7, (1 << 8) | (1 << 9))
DIGIT_RIGHT = max(x + w for bit, x, _, w, _ in PRIMITIVES if bit < 7)
TRAILING_GAP = 6      # the last glyph is always a digit, whose advance includes this gap
ADVANCES = (DIGIT_RIGHT + TRAILING_GAP,) * 10 + (13, 13, 13)
PRIMITIVE_BYTES = 5
MASK_BYTES = 2 * len(GLYPH_MASKS)
ADVANCE_BYTES = (len(ADVANCES) + 3) & ~3
TABLE_ADVANCES = MASK_BYTES + ((-MASK_BYTES) % 4)
TABLE_PRIMITIVES = TABLE_ADVANCES + ADVANCE_BYTES
TABLE_FORMATS = TABLE_PRIMITIVES + PRIMITIVE_BYTES * len(PRIMITIVES)
TABLE_COLORS = TABLE_FORMATS + FORMAT_BYTES * len(FORMATS)
COLOR_BYTES = 3 * len(PASS_GROWTH)    # one Y/Cb/Cr triple per pass
TABLE_GROWTH = TABLE_COLORS + COLOR_BYTES * len(COLORS)
if any(x + w > ADVANCES[APOSTROPHE] for bit, x, _, w, _ in PRIMITIVES if bit == 7) or \
        any(x + w > ADVANCES[COLON] for bit, x, _, w, _ in PRIMITIVES if bit > 7):
    raise ValueError('Date stamp punctuation overruns its advance')


def font_table():
    """The read-only table the ARM core indexes: glyphs, per-mode formats, pass colors, growth."""
    masks = struct.pack(f'<{len(GLYPH_MASKS)}H', *GLYPH_MASKS)
    masks += bytes(TABLE_ADVANCES - len(masks))
    advances = bytes(ADVANCES) + bytes(ADVANCE_BYTES - len(ADVANCES))
    formats = b''.join(bytes(FORMATS[mode] + (TOKEN_END,)).ljust(FORMAT_BYTES, b'\0')
                       for mode in sorted(FORMATS))
    colors = b''.join(bytes(triple) for passes in PASS_COLORS for triple in passes)
    return (masks + advances + b''.join(bytes(p) for p in PRIMITIVES) + formats + colors
            + bytes(PASS_GROWTH))


def valid_date(date):
    year, month, day, hour, minute = date[:5]
    return (2000 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31
            and hour <= 23 and minute <= 59)


def stamp_text(date, mode):
    """Glyph codes of one format, e.g. "'YY MM DD HH:MM" or "DD MM 'YY"."""
    year, month, day, hour, minute = date[:5]
    fields = {MONTH: month, DAY: day, HOUR: hour, MINUTE: minute}
    text = []
    for token in FORMATS[mode]:
        if token == YEAR:
            text += [APOSTROPHE, (year - 2000) // 10, (year - 2000) % 10]
        elif token == TOKEN_SPACE:
            text.append(SPACE)
        elif token == TOKEN_COLON:
            text.append(COLON)
        else:
            text += [fields[token] // 10, fields[token] % 10]
    return text


def layout(width, height, text, glow=False, orientation=ORIENT_LANDSCAPE):
    """Placement (x0, y0, scale, box_width, box_height) in stored pixels, or None if it cannot fit.

    With the glow, the box includes a border wide enough for its outermost pass.
    A portrait shot turns the box a quarter and moves it to the stored corner that
    is lower right once the viewer applies the EXIF rotation. Each stored edge
    keeps its landscape margin, so the inset from the corner is the same.
    """
    scale = max(width, MIN_SCALE)
    border = 2 * (PASS_GROWTH[0] * scale // SCALE_DIVISOR) if glow else 0
    text_width = (sum(ADVANCES[glyph] for glyph in text) - TRAILING_GAP) * scale // SCALE_DIVISOR + border
    text_height = GLYPH_HEIGHT * scale // SCALE_DIVISOR + border
    box_width, box_height = (text_height, text_width) if orientation else (text_width, text_height)
    margin_x, margin_y = width // MARGIN_X_DIVISOR, height // MARGIN_Y_DIVISOR
    x0 = margin_x if orientation == ORIENT_CCW else width - margin_x - box_width
    y0 = margin_y if orientation == ORIENT_CW else height - margin_y - box_height
    if x0 < 0 or y0 < 0 or x0 + box_width > width or y0 + box_height > height:
        return None
    return x0 & ~1, y0 & ~1, scale, box_width, box_height


def _fill(y_plane, c_plane, x, y, w, h, stride_y, stride_c, image_format, color):
    luma, cb, cr = color
    for row in range(y, y + h):
        start = row * stride_y + x
        y_plane[start:start + w] = bytes((luma,)) * w
        chroma_row = row >> 1 if image_format == 2 else row
        first = x & ~1
        last = (x + w + 1) & ~1
        for pair in range(first, last, 2):
            c_plane[chroma_row * stride_c + pair] = cb
            c_plane[chroma_row * stride_c + pair + 1] = cr


def _lighten(y_plane, c_plane, x, y, w, h, stride_y, stride_c, image_format, color):
    """Raise darker pixels to the glow luma and give their chroma pair the glow's color."""
    luma, cb, cr = color
    for row in range(y, y + h):
        chroma_row = row >> 1 if image_format == 2 else row
        for column in range(x, x + w):
            if y_plane[row * stride_y + column] < luma:
                y_plane[row * stride_y + column] = luma
                pair = chroma_row * stride_c + (column & ~1)
                c_plane[pair], c_plane[pair + 1] = cb, cr


def stamp_frame(y_plane, c_plane, width, height, stride_y, stride_c, image_format, date, mode, style=0,
                orientation=ORIENT_LANDSCAPE):
    """Draw in place with the ARM core's exact arithmetic; returns False if skipped.

    style is the saved word: a color index, plus GLOW_FLAG for the glow passes.
    orientation is the stock capture value; anything but the two portrait codes
    draws as landscape, as the stock EXIF writer does.
    """
    if mode not in FORMATS or image_format not in (1, 2) or not 0 <= style <= STYLE_MAX:
        return False
    if not width or not height or stride_y < width or stride_c < width or not valid_date(date):
        return False
    color, glow = style % GLOW_FLAG, bool(style & GLOW_FLAG)
    orientation = orientation if orientation in (ORIENT_CCW, ORIENT_CW) else ORIENT_LANDSCAPE
    text = stamp_text(date, mode)
    placed = layout(width, height, text, glow, orientation)
    if placed is None:
        return False
    x0, y0, scale, box_width, box_height = placed
    text_width, text_height = (box_height, box_width) if orientation else (box_width, box_height)

    def place(u, v, w, h):
        """A rectangle in upright text coordinates to stored pixels."""
        if orientation == ORIENT_CW:
            return x0 + v, y0 + text_width - u - w, h, w
        if orientation == ORIENT_CCW:
            return x0 + text_height - v - h, y0 + u, h, w
        return x0 + u, y0 + v, w, h
    # Edges, not sizes, are scaled from the grid, so neighbouring runs never gap or overlap.
    edge = lambda grid: grid * scale // SCALE_DIVISOR
    border = edge(PASS_GROWTH[0]) if glow else 0
    first = 0 if glow else len(PASS_GROWTH) - 1
    for growth, pass_color in zip(PASS_GROWTH[first:], PASS_COLORS[color][first:]):
        grow = edge(growth)
        draw = _lighten if growth else _fill
        cursor = 0
        for glyph in text:
            for bit, x, y, w, h in PRIMITIVES:
                if GLYPH_MASKS[glyph] >> bit & 1:
                    left, top = edge(cursor + x), edge(y)
                    width_px, height_px = edge(cursor + x + w) - left, edge(y + h) - top
                    if width_px and height_px:
                        draw(y_plane, c_plane, *place(border - grow + left, border - grow + top,
                                                      width_px + 2 * grow, height_px + 2 * grow),
                             stride_y, stride_c, image_format, pass_color)
            cursor += ADVANCES[glyph]
    return True
