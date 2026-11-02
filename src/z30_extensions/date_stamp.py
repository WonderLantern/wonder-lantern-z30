# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""Host reference for the film-style date stamp: font, layout and pixel contract.

Glyphs are seven-segment shapes on an 8x14 unit grid, drawn as opaque rectangles
into the stock Y/C planes. Opaque writes never read the frame, so drawing twice
with the same date on the same planes gives the same pixels. The ARM core in
features.date_stamp must match stamp_frame() byte for byte;
tools/verify_date_stamp.py checks that in Unicorn.
"""
import struct

from .lut import rgb_to_ycbcr_int

MODE_OFF, MODE_DATE, MODE_DATE_TIME = 0, 1, 2
# Amber of a data-back LED exposed through the film base. Cb-first interleaving
# is the same pending assumption as z30_extensions.lut.
STAMP_RGB = (255, 112, 24)
STAMP_Y, STAMP_CB, STAMP_CR = rgb_to_ycbcr_int(*STAMP_RGB)

SPACE, APOSTROPHE, COLON = 10, 11, 12
GLYPH_HEIGHT = 14
UNIT_DIVISOR = 620    # image width per font unit: glyphs reach about 3% of a 3:2 frame's height
MARGIN_X_DIVISOR = 28
MARGIN_Y_DIVISOR = 20
TRAILING_GAP = 2      # the last glyph is always a digit, whose advance includes this gap

# Primitive rectangles (x, y, w, h): segments a-g, apostrophe, colon top/bottom.
# Segment corners stay empty, which gives the separated-bar look of a data back.
PRIMITIVES = ((2, 0, 4, 2), (6, 2, 2, 4), (6, 8, 2, 4), (2, 12, 4, 2),
              (0, 8, 2, 4), (0, 2, 2, 4), (2, 6, 4, 2),
              (1, 0, 2, 4), (1, 4, 2, 2), (1, 9, 2, 2))
SEGMENTS = (0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F)
GLYPH_MASKS = SEGMENTS + (0, 1 << 7, (1 << 8) | (1 << 9))
ADVANCES = (10,) * 10 + (6, 5, 5)
MASK_BYTES = 2 * len(GLYPH_MASKS)
ADVANCE_BYTES = (len(ADVANCES) + 3) & ~3
TABLE_ADVANCES = MASK_BYTES + ((-MASK_BYTES) % 4)
TABLE_PRIMITIVES = TABLE_ADVANCES + ADVANCE_BYTES


def font_table():
    """The read-only table the ARM core indexes: masks, advances, primitives."""
    masks = struct.pack(f'<{len(GLYPH_MASKS)}H', *GLYPH_MASKS)
    masks += bytes(TABLE_ADVANCES - len(masks))
    advances = bytes(ADVANCES) + bytes(ADVANCE_BYTES - len(ADVANCES))
    return masks + advances + b''.join(bytes(p) for p in PRIMITIVES)


def valid_date(date):
    year, month, day, hour, minute = date[:5]
    return (2000 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31
            and hour <= 23 and minute <= 59)


def stamp_text(date, mode):
    """Glyph codes for "'YY MM DD", plus " HH:MM" in date-and-time mode."""
    year, month, day, hour, minute = date[:5]
    def two(value):
        return [value // 10, value % 10]
    text = [APOSTROPHE, *two(year - 2000), SPACE, *two(month), SPACE, *two(day)]
    if mode == MODE_DATE_TIME:
        text += [SPACE, *two(hour), COLON, *two(minute)]
    return text


def layout(width, height, text):
    """Placement (x0, y0, unit, total_width, total_height), or None if it cannot fit."""
    unit = max(width // UNIT_DIVISOR, 1)
    total_width = (sum(ADVANCES[glyph] for glyph in text) - TRAILING_GAP) * unit
    total_height = GLYPH_HEIGHT * unit
    x0 = width - width // MARGIN_X_DIVISOR - total_width
    y0 = height - height // MARGIN_Y_DIVISOR - total_height
    if x0 < 0 or y0 < 0:
        return None
    return x0 & ~1, y0 & ~1, unit, total_width, total_height


def _fill(y_plane, c_plane, x, y, w, h, stride_y, stride_c, image_format):
    for row in range(y, y + h):
        start = row * stride_y + x
        y_plane[start:start + w] = bytes((STAMP_Y,)) * w
        chroma_row = row >> 1 if image_format == 2 else row
        first = x & ~1
        last = (x + w + 1) & ~1
        for pair in range(first, last, 2):
            c_plane[chroma_row * stride_c + pair] = STAMP_CB
            c_plane[chroma_row * stride_c + pair + 1] = STAMP_CR


def stamp_frame(y_plane, c_plane, width, height, stride_y, stride_c, image_format, date, mode):
    """Draw in place with the ARM core's exact arithmetic; returns False if skipped."""
    if mode not in (MODE_DATE, MODE_DATE_TIME) or image_format not in (1, 2):
        return False
    if not width or not height or stride_y < width or stride_c < width or not valid_date(date):
        return False
    text = stamp_text(date, mode)
    placed = layout(width, height, text)
    if placed is None:
        return False
    cursor, y0, unit = placed[:3]
    for glyph in text:
        for index, (x, y, w, h) in enumerate(PRIMITIVES):
            if GLYPH_MASKS[glyph] >> index & 1:
                _fill(y_plane, c_plane, cursor + x * unit, y0 + y * unit, w * unit, h * unit,
                      stride_y, stride_c, image_format)
        cursor += ADVANCES[glyph] * unit
    return True
