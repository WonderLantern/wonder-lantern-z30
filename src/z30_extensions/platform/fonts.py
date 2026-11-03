"""Character coverage of the stock Z30 C 1.20 CJK UI fonts.

Fonts are `hif` blobs listed by a pointer array. The CJK fonts end their
header with a character map of (first code point, glyph, run length) entries;
a code point outside every run has no glyph and draws a placeholder box. Only the
coverage ranges are checked in (`z30_120_fonts.json`), never glyph bitmaps.
"""
import json
import struct
from functools import lru_cache
from importlib.resources import files

FONT_TABLE_LITERAL = 0x104530F4
FONT_TABLE = 0x109F62E4
HANT_FONT, HANS_FONT = 0, 2
CJK_FONTS = (HANT_FONT, HANS_FONT)
_HEADER = 0x40          # u16 section count, u32 descriptor length, descriptors, u16 run count
_MAX_HEADER = 0x80


def font_ranges(image, font):
    """[(first code point, length), ...] from the stock image's character map."""
    if image.words(FONT_TABLE_LITERAL, 1)[0] != FONT_TABLE:
        raise ValueError('Unexpected stock font table reference')
    address, = image.words(FONT_TABLE + font * 4, 1)
    header = image.read(address, _MAX_HEADER)
    if header[:4] != b'hif\x01' or header[6] != 6:
        raise ValueError(f'Font {font} is not a mapped hif font')
    sections, length = struct.unpack_from('<HI', header, _HEADER)
    descriptors = 5 * (sections - 1)
    if not 2 <= sections <= 8 or length != descriptors + 6:
        raise ValueError(f'Unexpected font {font} section descriptors')
    cursor = _HEADER + 6 + descriptors
    count, = struct.unpack_from('<H', header, cursor)
    entries = struct.iter_unpack('<3H', image.read(address + cursor + 2, count * 6))
    ranges, previous = [], -1
    for first, _glyph, run in entries:
        if first <= previous:
            raise ValueError(f'Font {font} character map is not sorted')
        if run:
            ranges.append((first, run))
        previous = first + max(run, 1) - 1
    return ranges


@lru_cache(maxsize=None)
def coverage(font):
    """Frozen set of code points the stock font can draw."""
    data = json.loads(files(__package__).joinpath('z30_120_fonts.json').read_text(encoding='utf-8'))
    return frozenset(cp for first, run in data['fonts'][str(font)] for cp in range(first, first + run))


def missing_glyphs(text, font):
    """Characters of `text` the stock font cannot draw, in order of first use."""
    covered = coverage(font)
    return ''.join(dict.fromkeys(ch for ch in text if ord(ch) not in covered))
