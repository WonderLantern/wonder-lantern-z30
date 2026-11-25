# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Host reference for the film grain added to the JPEG encoder input.

Ported from the Wonder Lantern Z5 FILM feature (MIT, Z5 Extensions contributors,
docs/FILM_GRAIN.md of github.com/WonderLantern/wonder-lantern-z5, version A52):
the same grain fields, tone curves and per-word arithmetic, so a Z30 JPEG and a
Z5 JPEG with the same setting carry the same grain statistics.

Only luma changes. Each style has one periodic 256x256 field of levels 0..63
(32 is zero): hashed white noise, then a symmetric 7-tap filter across and down,
which leaves clumps a few pixels wide. Four neighbouring pixels share one step
(0..20) chosen by their summed luma, so mid-tones carry more grain than shadows
and highlights. Pixel change: ((level - 32) * step + 8) >> 4, added with signed
saturation. Each photo picks a seed; every 256x256 region of the image takes its
own row and column phase in the field so the tile does not repeat visibly.

data_blob() is the table the camera code reads; grain_plane() is the pixel
contract the ARM payload (modules/film_grain) must match byte for byte
(tools/verify_film_grain.py).
"""
import struct

STYLE_OFF, STYLE_FUJI400, STYLE_HP5 = range(3)
STYLES = ('OFF', 'FUJI400', 'HP5')
STRENGTHS = 6
DEFAULT_STRENGTH = 3
# Saved strength word: 0 is the default strength 3, so a zeroed state block starts there;
# each press steps 3, 4, 5, 6, 1, 2.
STRENGTH_ORDER = tuple((DEFAULT_STRENGTH - 1 + i) % STRENGTHS + 1 for i in range(STRENGTHS))

EDGE = 256
FIELD_BYTES = EDGE * EDGE
LUT_BYTES = 1024                 # indexed by the USAD8 sum of four luma bytes
# Filter taps (k1, k2, k3) and output scale per grain style (centre weight 64).
FILTERS = {STYLE_FUJI400: (34, 17, -2, 14), STYLE_HP5: (30, 12, -4, 16)}
# Q8 gain at luma 0, 32, ..., 256, from flat patches of 13 Fuji 400 colour negative
# and 22 black-and-white scans (Z5 FILM_GRAIN.md section 3).
GAINS = {STYLE_FUJI400: (0, 77, 118, 156, 182, 151, 109, 64, 0),
         STYLE_HP5: (0, 102, 128, 174, 187, 169, 139, 77, 0)}
FIELDS_OFFSET = 0
LUTS_OFFSET = 2 * FIELD_BYTES
DATA_BYTES = LUTS_OFFSET + 2 * STRENGTHS * 2 * LUT_BYTES
SEED_ROW = 0x9E3779B1
SEED_COLUMN = 0x85EBCA6B
M32 = 0xFFFFFFFF


def hash32(value):
    value &= M32
    value ^= value >> 16
    value = (value * 0x7FEB352D) & M32
    value ^= value >> 15
    value = (value * 0x846CA68B) & M32
    return value ^ (value >> 16)


def _white(row, x):
    r = hash32((row << 8) | (x & 255))
    return (r & 255) - ((r >> 8) & 255)


def field(style):
    """256x256 bytes, levels 0..63 with 32 as zero."""
    k1, k2, k3, scale = FILTERS[style]
    tmp = []
    for y in range(EDGE):
        w = [_white(y, x) for x in range(EDGE)]
        for x in range(EDGE):
            n = (64 * w[x] + k1 * (w[x - 1] + w[(x + 1) & 255]) + k2 * (w[x - 2] + w[(x + 2) & 255])
                 + k3 * (w[x - 3] + w[(x + 3) & 255]))
            tmp.append((n + 32) >> 6)
    out = bytearray(FIELD_BYTES)
    for y in range(EDGE):
        rows = [tmp[((y + d) & 255) << 8:(((y + d) & 255) << 8) + EDGE] for d in (0, -1, 1, -2, 2, -3, 3)]
        for x in range(EDGE):
            n = (64 * rows[0][x] + k1 * (rows[1][x] + rows[2][x]) + k2 * (rows[3][x] + rows[4][x])
                 + k3 * (rows[5][x] + rows[6][x]))
            level = (n * scale + 8192) >> 14
            out[(y << 8) | x] = max(-31, min(31, level)) + 32
    return bytes(out)


def steps(style, strength, preview):
    """Step 0..20 for each average luma 0..255; previews get 5/8 of the main image's."""
    gain = GAINS[style]
    out = []
    for y in range(256):
        k, f = y >> 5, y & 31
        q8 = (gain[k] * (32 - f) + gain[k + 1] * f + 16) >> 5
        scaled = q8 * (strength + 1)
        out.append((scaled * 5 + 256) >> 9 if preview else (scaled + 32) >> 6)
    return out


def lut(style, strength, preview):
    """1024 bytes indexed by the sum of four luma bytes."""
    tone = steps(style, strength, preview)
    return bytes(tone[s >> 2] for s in range(LUT_BYTES))


def lut_offset(style, strength, preview):
    return LUTS_OFFSET + (((style - 1) * STRENGTHS + strength - 1) * 2 + bool(preview)) * LUT_BYTES


def field_offset(style):
    return FIELDS_OFFSET + (style - 1) * FIELD_BYTES


_BLOB = None


def data_blob():
    """Fields of both styles, then [style][strength][main, preview] lookups."""
    global _BLOB
    if _BLOB is None:
        parts = [field(STYLE_FUJI400), field(STYLE_HP5)]
        for style in (STYLE_FUJI400, STYLE_HP5):
            for strength in range(1, STRENGTHS + 1):
                parts += [lut(style, strength, False), lut(style, strength, True)]
        _BLOB = b''.join(parts)
        if len(_BLOB) != DATA_BYTES:
            raise AssertionError('film grain table layout')
    return _BLOB


def _sat(v):
    return 0 if v < 0 else 255 if v > 255 else v


def grain_word(v, noise, table):
    """Four luma bytes v with four field levels noise (both little-endian words)."""
    total = sum((v >> s) & 255 for s in (0, 8, 16, 24))
    step = table[total]
    out = 0
    for s in (0, 8, 16, 24):
        level = (noise >> s) & 255
        delta = ((level - 32) * step + 8) >> 4
        out |= _sat(((v >> s) & 255) + delta) << s
    return out


def phase(seed, y, x_word):
    return hash32(seed ^ (((y >> 8) * SEED_ROW) & M32) ^ ((x_word * SEED_COLUMN) & M32))


def grain_plane(plane, stride, width, height, style, strength, preview, seed, data=None):
    """Return a copy of the luma plane (bytes, stride*height or more) with grain added.

    Only the first width // 4 words of each row change; trailing pixels of a row
    whose width is not a multiple of 4 stay as they were.
    """
    data = data or data_blob()
    tiles = data[field_offset(style):field_offset(style) + FIELD_BYTES]
    table = data[lut_offset(style, strength, preview):lut_offset(style, strength, preview) + LUT_BYTES]
    out = bytearray(plane)
    words = width // 4
    seed |= 1
    for y in range(height):
        base = y * stride
        row = None
        offset = 0
        for x in range(words):
            if not x & 63:
                p = phase(seed, y, x)
                row = ((y + (p >> 8)) & 255) << 8
                offset = p & 56
            noise = struct.unpack_from('<I', tiles, row + 4 * ((x + offset) & 63))[0]
            v = struct.unpack_from('<I', out, base + 4 * x)[0]
            struct.pack_into('<I', out, base + 4 * x, grain_word(v, noise, table))
    return bytes(out)


def rms(style, strength, luma, preview=False):
    """Expected grain RMS in grey levels on a flat patch of this luma."""
    tiles = field(style)
    step = steps(style, strength, preview)[luma]
    values = [_sat(luma + (((t - 32) * step + 8) >> 4)) for t in tiles]
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
