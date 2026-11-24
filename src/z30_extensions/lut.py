# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""Host-side 3D LUT core: .cube parsing, trilinear lookup, camera binary format.

The supported on-camera subset is 3D integer-size tables (2..65 per side),
shared domain via DOMAIN_MIN/MAX, RGB channels in .cube order (red fastest).
Everything else in a .cube file is rejected, not ignored. The camera-side
loader consumes the packed binary produced by pack_binary(); both sides must
agree on :data:`BINARY_HEADER`.
"""
import struct

BINARY_MAGIC = b'WL3L'
BINARY_HEADER = struct.Struct('<4s9H')
# Header: magic, version=1, size, reserved, domain_min r/g/b and domain_max
# r/g/b as u16 on a 0..65535 scale of [0,1]. Data follows: size**3 pixels,
# .cube order (red fastest), each pixel three little-endian u16 channels
# normalized into the domain.
BINARY_VERSION = 1
MAX_SIZE = 65
MIN_SIZE = 2


class CubeError(ValueError):
    """The .cube file is outside the supported subset or malformed."""


def parse_cube(text):
    """Parse a .cube document into (size, domain, table) or raise CubeError.

    table is a flat tuple of 3*size**3 floats in .cube order. Unrecognised
    directives and missing data are errors, matching the reject-don't-ignore
    subset rule.
    """
    size = None
    domain_min = (0.0, 0.0, 0.0)
    domain_max = (1.0, 1.0, 1.0)
    title = None
    values = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        keyword = parts[0].upper()
        if keyword == '#':
            continue
        if keyword == 'TITLE':
            title = line[5:].strip().strip('"')
            continue
        if keyword == 'LUT_3D_SIZE':
            if len(parts) != 2 or size is not None:
                raise CubeError(f'line {number}: bad or duplicate LUT_3D_SIZE')
            try:
                size = int(parts[1])
            except ValueError:
                raise CubeError(f'line {number}: LUT_3D_SIZE is not an integer') from None
            if not MIN_SIZE <= size <= MAX_SIZE:
                raise CubeError(f'line {number}: LUT_3D_SIZE {size} outside {MIN_SIZE}..{MAX_SIZE}')
            continue
        if keyword == 'LUT_1D_SIZE':
            raise CubeError(f'line {number}: 1D LUT files are not supported')
        if keyword == 'DOMAIN_MIN' or keyword == 'DOMAIN_MAX':
            if len(parts) != 4:
                raise CubeError(f'line {number}: {keyword} needs exactly three values')
            try:
                triple = tuple(float(part) for part in parts[1:])
            except ValueError:
                raise CubeError(f'line {number}: {keyword} value is not a number') from None
            if keyword == 'DOMAIN_MIN':
                domain_min = triple
            else:
                domain_max = triple
            continue
        # Everything else must be a table row of exactly three numbers.
        try:
            values.append(tuple(float(part) for part in parts))
        except ValueError:
            raise CubeError(f'line {number}: unknown directive {parts[0]!r}') from None
        if len(values[-1]) != 3:
            raise CubeError(f'line {number}: table rows need exactly three channels')
    if size is None:
        raise CubeError('missing LUT_3D_SIZE')
    if len(values) != size ** 3:
        raise CubeError(f'expected {size ** 3} table rows, found {len(values)}')
    if any(not (lo < hi) for lo, hi in zip(domain_min, domain_max)):
        raise CubeError('DOMAIN_MIN must be below DOMAIN_MAX on every axis')
    table = []
    for row in values:
        for channel, (lo, hi) in zip(row, zip(domain_min, domain_max)):
            if not lo - 1e-9 <= channel <= hi + 1e-9:
                raise CubeError(f'table value {channel} outside domain {lo}..{hi}')
        table.extend(row)
    return dict(size=size, domain_min=domain_min, domain_max=domain_max,
                title=title, table=tuple(table))


def trilinear(size, domain_min, domain_max, table, red, green, blue):
    """One RGB lookup through the table; input clamped to the domain.

    Coordinates map linearly from the domain onto node indices, then a
    trilinear interpolation runs inside the enclosing cell. Pure Python; the
    ARM core implements the same arithmetic in fixed point for on-camera use.
    """
    spans = tuple(hi - lo for lo, hi in zip(domain_min, domain_max))
    coords = []
    for value, lo, span in ((red, domain_min[0], spans[0]),
                            (green, domain_min[1], spans[1]),
                            (blue, domain_min[2], spans[2])):
        clamped = min(max(value, lo), lo + span)
        coords.append((clamped - lo) / span * (size - 1) if span else 0.0)
    out = []
    for channel in range(3):
        base = [min(int(coords[axis]), size - 2) for axis in range(3)]
        frac = [coords[axis] - base[axis] for axis in range(3)]
        total = 0.0
        for index in range(8):
            dx = base[0] + (index & 1)
            dy = base[1] + ((index >> 1) & 1)
            dz = base[2] + ((index >> 2) & 1)
            weight = ((frac[0] if index & 1 else 1.0 - frac[0])
                      * (frac[1] if index & 2 else 1.0 - frac[1])
                      * (frac[2] if index & 4 else 1.0 - frac[2]))
            total += table[(dx + size * (dy + size * dz)) * 3 + channel] * weight
        out.append(total)
    return tuple(out)


def pack_binary(size, domain_min, domain_max, table):
    """Pack the parsed table into the camera binary format.

    Domain endpoints are stored on a full 0..65535 scale of [0,1]; the camera
    maps incoming 8-bit RGB (x*257) into [domain_min, domain_max] with the
    same arithmetic the host reference uses here.
    """
    scale = 65535.0
    spans = tuple(hi - lo for lo, hi in zip(domain_min, domain_max))
    endpoints = tuple(domain_min) + tuple(domain_max)
    if any(not -1e-9 <= value <= 1.0 + 1e-9 for value in endpoints):
        raise CubeError('binary LUT domain endpoints must lie within [0, 1]')
    data = bytearray()
    for index in range(size ** 3):
        for channel in range(3):
            value = table[index * 3 + channel]
            lo, span = domain_min[channel], spans[channel]
            normalized = (value - lo) / span if span else 0.0
            data += struct.pack('<H', max(0, min(65535, round(normalized * scale))))
    domain_fields = tuple(int(round(value * scale)) for value in endpoints)
    header = BINARY_HEADER.pack(BINARY_MAGIC, BINARY_VERSION, size, 0, *domain_fields)
    return header + bytes(data)


def unpack_binary(payload):
    """Inverse of pack_binary; returns the parse_cube-shaped dict."""
    if len(payload) < BINARY_HEADER.size:
        raise CubeError('truncated binary LUT header')
    fields = BINARY_HEADER.unpack_from(payload)
    if fields[0] != BINARY_MAGIC or fields[1] != BINARY_VERSION:
        raise CubeError('not a WL3L v1 binary LUT')
    size = fields[2]
    if not MIN_SIZE <= size <= MAX_SIZE:
        raise CubeError(f'binary LUT size {size} outside {MIN_SIZE}..{MAX_SIZE}')
    mins = [fields[4 + i] / 65535.0 for i in range(3)]
    maxs = [fields[7 + i] / 65535.0 for i in range(3)]
    if any(not (lo < hi) for lo, hi in zip(mins, maxs)):
        raise CubeError('binary LUT domain is empty')
    expected = BINARY_HEADER.size + size ** 3 * 6
    if len(payload) != expected:
        raise CubeError(f'binary LUT data length {len(payload)} != {expected}')
    table = []
    for index in range(size ** 3):
        for channel in range(3):
            raw = struct.unpack_from('<H', payload, BINARY_HEADER.size + (index * 3 + channel) * 2)[0]
            table.append(mins[channel] + raw / 65535.0 * (maxs[channel] - mins[channel]))
    return dict(size=size, domain_min=tuple(mins), domain_max=tuple(maxs),
                title=None, table=tuple(table))


def identity_cube(size=17):
    """A reference identity table with the canonical domain."""
    rows = []
    for blue in range(size):
        for green in range(size):
            for red in range(size):
                rows.append((red / (size - 1), green / (size - 1), blue / (size - 1)))
    lines = [f'TITLE Identity {size}', f'LUT_3D_SIZE {size}', '']
    for row in rows:
        lines.append(' '.join(f'{value:.6f}' for value in row))
    return '\n'.join(lines) + '\n'


# BT.601 limited-range (JFIF) YCbCr <-> 8-bit RGB, the assumed camera plane
# contract pending the on-device controlled test. Cb first in the interleaved
# pair is also pending; both knobs live here so device trials tune one module.
Y_OFFSET, Y_SPAN = 16, 219
C_OFFSET, C_SPAN = 128, 224

# Integer fixed-point contract shared with the ARM core (tools/lut_core_asm.py):
# forward uses Q8.8 products of the float factors, reverse uses Q2.14 of the
# limited-range factors. The ARM core must match these bit for bit.
FWD_Y_SCALE = 1193      # (y-16) * 1193 >> 10
FWD_CR_R = 359          # 1.402 << 8
FWD_CB_G = 88           # 0.344136 << 8
FWD_CR_G = 183          # 0.714136 << 8
FWD_CB_B = 454          # 1.772 << 8
REV_Y_R, REV_Y_G, REV_Y_B = 4208, 8262, 1604      # limited-range rows, Q2.14
REV_CB_R, REV_CB_G, REV_CB_B = -2428, -4768, 7196
REV_CR_R, REV_CR_G, REV_CR_B = 7196, -6026, -1170


def clamp8(value):
    return max(0, min(255, value))


def ycbcr_to_rgb_int(y, cb, cr):
    """The exact integer forward conversion the ARM core performs."""
    ys = (y - Y_OFFSET) * FWD_Y_SCALE >> 10
    cb8, cr8 = cb - C_OFFSET, cr - C_OFFSET
    red = ys + ((cr8 * FWD_CR_R + 128) >> 8)
    green = ys - ((cb8 * FWD_CB_G + 128) >> 8) - ((cr8 * FWD_CR_G + 128) >> 8)
    blue = ys + ((cb8 * FWD_CB_B + 128) >> 8)
    return clamp8(red), clamp8(green), clamp8(blue)


def rgb_to_ycbcr_int(red, green, blue):
    """The exact integer reverse conversion the ARM core performs."""
    def channel(r_coeff, g_coeff, b_coeff, offset, low, high):
        value = offset + ((red * r_coeff + green * g_coeff + blue * b_coeff + 8192) >> 14)
        return max(low, min(high, value))
    y = channel(REV_Y_R, REV_Y_G, REV_Y_B, Y_OFFSET, Y_OFFSET, Y_OFFSET + Y_SPAN)
    cb = channel(REV_CB_R, REV_CB_G, REV_CB_B, C_OFFSET, 16, 240)
    cr = channel(REV_CR_R, REV_CR_G, REV_CR_B, C_OFFSET, 16, 240)
    return y, cb, cr


def trilinear_int(block_data, red, green, blue):
    """The exact fixed-point lookup the ARM core performs over a packed binary.

    block_data is a full WL3L payload. Returns three 0..255 integers.
    """
    fields = BINARY_HEADER.unpack_from(block_data)
    size = fields[2]
    mins = fields[4:7]
    maxs = fields[7:10]
    table_offset = BINARY_HEADER.size

    def axis(value8, lo, hi):
        coord = max(lo, min(hi, value8 * 257))
        span = hi - lo
        t = ((coord - lo) << 16) // span
        pos = t * (size - 1) >> 16
        base = min(pos, size - 2)
        # Both weights must stay inside u16: at an exact node position one
        # weight would be 0 or 65536, and a 65536 weight multiplied by another
        # 65536 weight overflows the 32-bit product the ARM core computes.
        hi_w = min(t * (size - 1) - (base << 16), 65535)
        lo_w = min(65536 - hi_w, 65535)
        return base, lo_w, hi_w

    base_r, wlo_r, whi_r = axis(red, mins[0], maxs[0])
    base_g, wlo_g, whi_g = axis(green, mins[1], maxs[1])
    base_b, wlo_b, whi_b = axis(blue, mins[2], maxs[2])
    row_y = size * 6
    row_z = size * row_y
    out = []
    for channel in range(3):
        acc = 0
        for index in range(8):
            dx = base_r + (index & 1)
            dy = base_g + ((index >> 1) & 1)
            dz = base_b + ((index >> 2) & 1)
            value = struct.unpack_from('<H', block_data,
                table_offset + dx * 6 + dy * row_y + dz * row_z + channel * 2)[0]
            weight = ((whi_r if index & 1 else wlo_r)
                      * (whi_g if index & 2 else wlo_g) >> 16)
            weight = (weight * (whi_b if index & 4 else wlo_b)) >> 16
            acc += value * weight >> 16
        out.append(clamp8(acc >> 8))
    return tuple(out)


def lut_effect_int(block_data, y, cb, cr):
    """Full pixel contract: forward conversion, LUT, reverse conversion."""
    return rgb_to_ycbcr_int(*trilinear_int(block_data, *ycbcr_to_rgb_int(y, cb, cr)))


def apply_frame_int(block_data, y_plane, c_plane, width, height, stride_y, stride_c,
                    x0=0, y0=0, base_y=None, base_c=None):
    """The exact frame contract the ARM core implements, as host reference.

    Processes chroma pairs: both pixels of a pair are converted independently
    and the output Cb/Cr pair is the rounded average of the two results. An
    odd trailing pixel writes its own chroma to both bytes. The window starts
    at (x0, y0); base_y/base_c override the untouched-output bytes (the core
    leaves bytes outside the window at whatever the output planes held).
    Returns the new (y_plane, c_plane) as bytearrays.
    """
    y_out = bytearray(base_y if base_y is not None else y_plane)
    c_out = bytearray(base_c if base_c is not None else c_plane)
    for row in range(y0, y0 + height):
        y_row = row * stride_y
        c_row = row * stride_c
        x = x0
        while x + 1 < x0 + width:
            cb, cr = c_plane[c_row + x], c_plane[c_row + x + 1]
            first = lut_effect_int(block_data, y_plane[y_row + x], cb, cr)
            second = lut_effect_int(block_data, y_plane[y_row + x + 1], cb, cr)
            y_out[y_row + x] = first[0]
            y_out[y_row + x + 1] = second[0]
            c_out[c_row + x] = (first[1] + second[1]) >> 1
            c_out[c_row + x + 1] = (first[2] + second[2]) >> 1
            x += 2
        if width & 1:
            cb, cr = c_plane[c_row + x], c_plane[c_row + x + 1]
            single = lut_effect_int(block_data, y_plane[y_row + x], cb, cr)
            y_out[y_row + x] = single[0]
            c_out[c_row + x] = single[1]
            c_out[c_row + x + 1] = single[2]
    return y_out, c_out


def ycbcr_to_rgb(y, cb, cr):
    """Limited-range BT.601 one pixel; inputs/outputs 0..255 integers."""
    ys = (y - Y_OFFSET) * (255 / Y_SPAN)
    cs = cb - C_OFFSET
    crs = cr - C_OFFSET
    red = ys + 1.402 * crs
    green = ys - 0.344136 * cs - 0.714136 * crs
    blue = ys + 1.772 * cs
    return tuple(max(0, min(255, round(value))) for value in (red, green, blue))


def rgb_to_ycbcr(red, green, blue):
    """Inverse of ycbcr_to_rgb; returns (y, cb, cr) 0..255 integers."""
    red_s, green_s, blue_s = red, green, blue
    y = Y_OFFSET + 0.256788 * red_s + 0.504129 * green_s + 0.097906 * blue_s
    cb = C_OFFSET - 0.148223 * red_s - 0.290993 * green_s + 0.439216 * blue_s
    cr = C_OFFSET + 0.439216 * red_s - 0.367788 * green_s - 0.071427 * blue_s
    return tuple(max(0, min(255, round(value))) for value in (y, cb, cr))
