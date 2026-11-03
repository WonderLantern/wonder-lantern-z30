"""Stock Z30 C 1.20 CJK UI fonts: character coverage and glyph outlines.

Fonts are `hif` blobs listed by a pointer array; a native font engine parses
them with a byte-stream reader and rasterizes the outlines for OpenVG. The CJK
fonts end their header with a character map of (first code point, glyph, run
length) entries; a code point outside every run draws a placeholder box. Only
the coverage ranges are checked in (`z30_120_fonts.json`), never glyph data.

Glyphs (engine 0x10507288, index lookup 0x10506D6E) are found through an index
at header offset +0x2C: u16 range count, then per range u16 first glyph,
u32 base, u16 count and count u16 offsets; a glyph lives at data offset
(header +0x30) + base + offset, and a zero offset is empty except at `first`.
Each record is a TrueType-like quadratic outline in 2048 units per em, see
`decode_glyph`. docs/ui-qemu.md has the probe evidence.
"""
import json
import struct
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files

FONT_TABLE_LITERAL = 0x104530F4
FONT_TABLE = 0x109F62E4
# The boot code at 0x10452F34 hands the engine 25 font pointers and, from a
# second literal, the 25 matching font sizes (the language page uses font 24).
FONT_COUNT = 25
FONT_SIZES_LITERAL = 0x104530F8
FONT_SIZES = 0x109F6348
HANT_FONT, HANS_FONT = 0, 2
CJK_FONTS = (HANT_FONT, HANS_FONT)
# Glyphs added to fonts 0/2 at build time (tools/build_cjk_glyphs.py).
EXTRA_GLYPHS = 'z30_120_extra_glyphs.json'
_HEADER = 0x40          # u16 section count, u32 descriptor length, descriptors, u16 run count
_MAX_HEADER = 0x80
# Glyph record flags.
HAS_OUTLINE = 0x0002     # u16 contour count and the bounding box follow
COMPOSITE = 0x0008       # not used by the CJK fonts; unsupported here
ADVANCE_SHIFT = 7        # 0..2: header default advance, 3: explicit int16
VERTICAL_SHIFT = 5       # 3: one extra int16 (unused by the CJK fonts)
EXTRA_INT16 = 0x0200     # one trailing int16 before the contours
NO_VERTICAL = 0x0400     # no vertical bearings in the bounding box block


def font_address(image, font):
    if image.words(FONT_TABLE_LITERAL, 1)[0] != FONT_TABLE:
        raise ValueError('Unexpected stock font table reference')
    address, = image.words(FONT_TABLE + font * 4, 1)
    header = image.read(address, _MAX_HEADER)
    if header[:4] != b'hif\x01' or header[6] != 6:
        raise ValueError(f'Font {font} is not a mapped hif font')
    return address, header


def character_map(image, font):
    """[(first code point, glyph, run length), ...] from the stock image."""
    address, header = font_address(image, font)
    sections, length = struct.unpack_from('<HI', header, _HEADER)
    descriptors = 5 * (sections - 1)
    if not 2 <= sections <= 8 or length != descriptors + 6:
        raise ValueError(f'Unexpected font {font} section descriptors')
    cursor = _HEADER + 6 + descriptors
    count, = struct.unpack_from('<H', header, cursor)
    entries = list(struct.iter_unpack('<3H', image.read(address + cursor + 2, count * 6)))
    previous = -1
    for first, _glyph, run in entries:
        if first <= previous:
            raise ValueError(f'Font {font} character map is not sorted')
        previous = first + max(run, 1) - 1
    return entries


def font_ranges(image, font):
    """[(first code point, length), ...] the stock font can draw."""
    return [(first, run) for first, _glyph, run in character_map(image, font) if run]


def default_advances(image, font):
    """Advance widths selected by glyph flag values 0..2."""
    return struct.unpack_from('<3h', font_address(image, font)[1], 0x18)


def glyph_addresses(image, font):
    """{glyph id: record address} from the font's glyph index."""
    address, header = font_address(image, font)
    index, data = struct.unpack_from('<II', header, 0x2C)
    cursor = address + index
    ranges, = struct.unpack('<H', image.read(cursor, 2))
    cursor += 2
    glyphs = {}
    for _ in range(ranges):
        first, base, count = struct.unpack('<HIH', image.read(cursor, 8))
        offsets = struct.unpack(f'<{count}H', image.read(cursor + 8, count * 2))
        cursor += 8 + count * 2
        for i, offset in enumerate(offsets):
            if offset or i == 0:
                glyphs[first + i] = address + data + base + offset
    return glyphs


@dataclass
class Glyph:
    flags: int
    # Each contour: [(x, y, on_curve), ...], absolute font units, y up.
    contours: list = field(default_factory=list)
    advance: int = None          # explicit advance when (flags >> 7) & 3 == 3
    vertical: int = None         # present when (flags >> 5) & 3 == 3
    size: tuple = None           # (width, height) of the outline bounds
    x_min: int = None
    y_max: int = None
    bearings: tuple = None       # vertical layout: (x_min - advance/2, 1760 - y_max)
    extra: int = None            # present with EXTRA_INT16


def decode_glyph(raw, offset=0):
    """(Glyph, end offset) for the record at `offset` of `raw`.

    Layout: u16 flags; u16 contours if HAS_OUTLINE; optional int16 advance and
    vertical value; bounding box u16 width, u16 height, int16 x_min, int16 y_max
    and, unless NO_VERTICAL, int16 vertical bearings (an outline-less record
    always has this block); optional int16 extra. Contours: u16 point count, end
    points of all but the last contour (u8, or u16 above 255 points), on-curve
    bits LSB first, u16 descriptor count and descriptors. A descriptor covers
    (d & 0x3F) + 1 points whose x/y deltas are int8 when bit 0x80/0x40 is set,
    else int16; the first point is a delta from (0, 0).
    """
    def take(fmt):
        nonlocal offset
        values = struct.unpack_from(fmt, raw, offset)
        offset += struct.calcsize(fmt)
        return values if len(values) > 1 else values[0]
    flags = take('<H')
    if flags & COMPOSITE:
        raise ValueError('Composite glyph records are not supported')
    glyph = Glyph(flags)
    contours = take('<H') if flags & HAS_OUTLINE else 0
    if (flags >> ADVANCE_SHIFT) & 3 == 3:
        glyph.advance = take('<h')
    if (flags >> VERTICAL_SHIFT) & 3 == 3:
        glyph.vertical = take('<h')
    width, height, glyph.x_min, glyph.y_max = take('<HHhh')
    glyph.size = (width, height)
    if not flags & NO_VERTICAL:
        glyph.bearings = take('<hh')
    if flags & EXTRA_INT16:
        glyph.extra = take('<h')
    if not contours:
        return glyph, offset
    points = take('<H')
    ends = [take('<H' if points > 0xFF else '<B') for _ in range(contours - 1)] + [points - 1]
    on_curve = [raw[offset + i // 8] >> (i % 8) & 1 for i in range(points)]
    offset += (points + 7) // 8
    count = take('<H')
    descriptors = raw[offset:offset + count]
    offset += count
    x = y = 0
    flat, remaining, descriptor = [], 0, iter(descriptors)
    for bit in on_curve:
        if not remaining:
            kind = next(descriptor)
            remaining = (kind & 0x3F) + 1
        x += take('<b' if kind & 0x80 else '<h')
        y += take('<b' if kind & 0x40 else '<h')
        flat.append((x, y, bit))
        remaining -= 1
    if remaining or next(descriptor, None) is not None:
        raise ValueError('Glyph descriptors do not match its point count')
    starts = [0] + [end + 1 for end in ends[:-1]]
    glyph.contours = [flat[start:end + 1] for start, end in zip(starts, ends)]
    return glyph, offset


def encode_glyph(glyph):
    """Record bytes for `glyph`, in the stock encoder's exact layout."""
    flags = glyph.flags
    out = bytearray(struct.pack('<H', flags))
    if flags & HAS_OUTLINE:
        out += struct.pack('<H', len(glyph.contours))
    if (flags >> ADVANCE_SHIFT) & 3 == 3:
        out += struct.pack('<h', glyph.advance)
    if (flags >> VERTICAL_SHIFT) & 3 == 3:
        out += struct.pack('<h', glyph.vertical)
    out += struct.pack('<HHhh', *glyph.size, glyph.x_min, glyph.y_max)
    if not flags & NO_VERTICAL:
        out += struct.pack('<hh', *glyph.bearings)
    if flags & EXTRA_INT16:
        out += struct.pack('<h', glyph.extra)
    if not glyph.contours:
        return bytes(out)
    flat = [point for contour in glyph.contours for point in contour]
    out += struct.pack('<H', len(flat))
    end = -1
    for contour in glyph.contours[:-1]:
        end += len(contour)
        out += struct.pack('<H' if len(flat) > 0xFF else '<B', end)
    bits = bytearray((len(flat) + 7) // 8)
    for i, (_x, _y, on) in enumerate(flat):
        bits[i // 8] |= bool(on) << (i % 8)
    out += bits
    deltas, x, y = [], 0, 0
    for px, py, _on in flat:
        deltas.append((px - x, py - y))
        x, y = px, py
    kinds = [(0x80 if -128 <= dx <= 127 else 0) | (0x40 if -128 <= dy <= 127 else 0) for dx, dy in deltas]
    descriptors, i = bytearray(), 0
    while i < len(kinds):
        j = i
        while j < len(kinds) and kinds[j] == kinds[i] and j - i < 64:
            j += 1
        descriptors.append(kinds[i] | (j - i - 1))
        i = j
    out += struct.pack('<H', len(descriptors)) + descriptors
    for (dx, dy), kind in zip(deltas, kinds):
        out += struct.pack('<b' if kind & 0x80 else '<h', dx) + struct.pack('<b' if kind & 0x40 else '<h', dy)
    return bytes(out)


def font_blob(image, font):
    """Raw bytes of a stock CJK font, as long as its entry in the size table."""
    address, _header = font_address(image, font)
    if image.words(FONT_SIZES_LITERAL, 1)[0] != FONT_SIZES:
        raise ValueError('Unexpected stock font size table reference')
    size, = image.words(FONT_SIZES + font * 4, 1)
    return image.read(address, size)


def _runs(mapping):
    """Character map entries from {code point: glyph}: runs of consecutive pairs."""
    entries = []
    for cp in sorted(mapping):
        if entries and entries[-1][0] + entries[-1][2] == cp and entries[-1][1] + entries[-1][2] == mapping[cp]:
            first, glyph, run = entries[-1]
            entries[-1] = (first, glyph, run + 1)
        else:
            entries.append((cp, mapping[cp], 1))
    return entries


def extend_font(blob, glyphs):
    """Font bytes with {code point: Glyph} added or replacing the primary mapping.

    Layout: header, primary map at +0x40 (u16 sections, u32 descriptor length,
    (tag, offset from +0x40) descriptors, u16 count, entries), the tagged
    maps, glyph index (+0x2C), feature table (+0x3C) and glyph data (+0x30),
    in that order. New glyphs get ids after the stock maximum, one new index
    range and records appended to the glyph data; the header offsets and
    section descriptors are shifted by the bytes inserted before them.
    """
    blob = bytes(blob)
    if blob[:4] != b'hif\x01' or blob[6] != 6:
        raise ValueError('Not a mapped hif font')
    index, data = struct.unpack_from('<II', blob, 0x2C)
    features, = struct.unpack_from('<I', blob, 0x3C)
    sections, length = struct.unpack_from('<HI', blob, _HEADER)
    descriptors = [(blob[_HEADER + 6 + 5 * i], struct.unpack_from('<I', blob, _HEADER + 7 + 5 * i)[0])
                   for i in range(sections - 1)]
    primary = _HEADER + 6 + 5 * (sections - 1)
    count, = struct.unpack_from('<H', blob, primary)
    primary_end = primary + 2 + 6 * count
    if not (primary_end <= _HEADER + min(o for _t, o in descriptors) and index < features < data):
        raise ValueError('Unexpected font section order')
    stock = list(struct.iter_unpack('<3H', blob[primary + 2:primary_end]))
    mapping = {cp: glyph + cp - first for first, glyph, run in stock for cp in range(first, first + run)}
    ranges, = struct.unpack_from('<H', blob, index)
    cursor, last = index + 2, 0
    for _ in range(ranges):
        first, _base, span = struct.unpack_from('<HIH', blob, cursor)
        cursor += 8 + 2 * span
        last = max(last, first + span)
    if cursor != features:
        raise ValueError('Glyph index does not end at the feature table')
    first_new = last
    records, offsets = bytearray(), []
    for i, cp in enumerate(sorted(glyphs)):
        offsets.append(len(records))
        records += encode_glyph(glyphs[cp])
        mapping[cp] = first_new + i
    if first_new + len(glyphs) > 0xFFFF or len(records) > 0xFFFF:
        raise ValueError('Too many added glyphs for one index range')
    entries = [(0, 0, 0)] * (stock[0] == (0, 0, 0)) + _runs({cp: g for cp, g in mapping.items() if cp})
    cmap = struct.pack('<H', len(entries)) + b''.join(struct.pack('<3H', *e) for e in entries)
    grow_map = len(cmap) - (primary_end - primary)
    base = len(blob) - data          # appended after every stock record
    new_range = struct.pack('<HIH', first_new, base, len(glyphs)) + struct.pack(f'<{len(glyphs)}H', *offsets)
    grow_index = len(new_range) if glyphs else 0
    header = bytearray(blob[:primary])
    struct.pack_into('<II', header, 0x2C, index + grow_map, data + grow_map + grow_index)
    struct.pack_into('<I', header, 0x3C, features + grow_map + grow_index)
    for i, (tag, offset) in enumerate(descriptors):
        struct.pack_into('<I', header, _HEADER + 7 + 5 * i, offset + grow_map)
    index_body = bytearray(blob[index:features])
    if glyphs:
        struct.pack_into('<H', index_body, 0, ranges + 1)
        index_body += new_range
    return (bytes(header) + cmap + blob[primary_end:index] + bytes(index_body)
            + blob[features:] + bytes(records))


@lru_cache(maxsize=None)
def coverage(font, extended=True):
    """Code points the font can draw, including the added glyphs if `extended`."""
    data = json.loads(files(__package__).joinpath('z30_120_fonts.json').read_text(encoding='utf-8'))
    stock = frozenset(cp for first, run in data['fonts'][str(font)] for cp in range(first, first + run))
    return stock | frozenset(map(ord, extra_glyphs(font))) if extended else stock


@lru_cache(maxsize=None)
def extra_glyphs(font):
    """{char: encoded record hex} added to `font` at build time."""
    data = json.loads(files(__package__).joinpath(EXTRA_GLYPHS).read_text(encoding='utf-8'))
    return data['fonts'].get(str(font), {})


def missing_glyphs(text, font, extended=True):
    """Characters of `text` the (extended) font cannot draw, in order of first use."""
    covered = coverage(font, extended)
    return ''.join(dict.fromkeys(ch for ch in text if ord(ch) not in covered))
