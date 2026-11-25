"""Build independent emulator-only aspect experiments from identity-checked stock.

Each trial borrows area 2. These are NOT multiple selectable menu entries.
"""
import struct

from ...plan import Plan

PROFILES = {'4-3': (4, 3), '5-4': (5, 4), '2-1': (2, 1),
            '235-100': (235, 100), '239-100': (239, 100), '65-24': (65, 24)}
DX = ((5568, 3712), (4176, 2784), (2784, 1856))


def nearest8(numerator, denominator, maximum):
    # Nearest multiple of eight; ties round down. Clamp inside source frame.
    low = numerator // (denominator * 8) * 8
    high = low + 8
    return high if high <= maximum and high * denominator - numerator < numerator - low * denominator else low


def dimensions(p, q):
    if p <= 0 or q <= 0:
        raise ValueError('Positive ratio required')
    result = []
    for width, height in DX:
        if width * q >= height * p:
            width = nearest8(height * p, q, width)
        else:
            height = nearest8(width * q, p, height)
        if not width or not height:
            raise ValueError('Ratio is too extreme')
        result.append((width, height))
    return result


def preview_dimensions(width, height, box):
    """Fit a captured picture into a stock preview allocation, aligned to four.

    Use the actual quantized L dimensions, not the nominal menu ratio. Never
    grow either side beyond its original 16:9 preview buffer dimensions.
    """
    max_w, max_h = box
    def nearest4(numerator, denominator, maximum):
        low = numerator // (denominator * 4) * 4
        high = low + 4
        return high if high <= maximum and high * denominator - numerator < numerator - low * denominator else low
    if max_w * height <= max_h * width:
        return max_w, nearest4(max_w * height, width, max_h)
    return nearest4(max_h * width, height, max_w), max_h


STOCK_RAW_PLANES = {
    # Stock RAW plane (selector 0x1020BFC8 L / 0x1020C05C M) and its sensor
    # origin (0x1020C072) for the native area each custom profile builds on.
    'dx': dict(large=(5600, 3728), reduced=(4200, 2796), origin=(0, 0)),
    '16:9': dict(large=(5600, 3144), reduced=(4200, 2358), origin=(0, 292)),
}


def raw_plane(profile):
    p, q = PROFILES[profile]
    return STOCK_RAW_PLANES['dx' if p * 2 < q * 3 else '16:9']


def build_trial(stock, profile, stock_raw_plane=True):
    """Build one independent area-2 geometry.

    stock_raw_plane=True (issue 38): NEF RAW keeps the native DX or 16:9
    plane, which raw converters know. The JPEG processing window keeps the
    camera-verified custom position: the edge table compensates for the
    stock vertical origin. stock_raw_plane=False reproduces v43 geometry
    (custom RAW plane such as 5600x2072), kept for regression comparison.
    """
    p, q = PROFILES[profile]
    sizes = dimensions(p, q)
    width, height = sizes[0]
    medium_w, medium_h = sizes[1]
    x, y = (5568 - width) // 2, (3712 - height) // 2
    plan = Plan(stock)
    edits = []

    def pair(address, value, label):
        before = plan.image.read(address, 4)
        after = struct.pack('<2H', *value)
        if before != after:
            plan.edit(address, after, 'Aspect research: ' + label, before)
            edits.append({'address': hex(address), 'before': list(struct.unpack('<2H', before)),
                          'after': list(value), 'purpose': label})

    # Narrow profiles use DX's vertical padding/variant conventions; wide
    # profiles retain the stock 16:9 variant deltas. This is an experimental
    # policy whose actual sensor/ISP suitability is not yet established.
    narrow = p * 2 < q * 3
    source_crop = 0x115A4E20 if narrow else 0x115A4E38
    source_edges = 0x115A4E98 if narrow else 0x115A4EB0
    source_sizes = DX if narrow else ((5568, 3128), (4176, 2344), (2784, 1560))
    plane = raw_plane(profile)
    if stock_raw_plane:
        origin = plane['origin']
        large, reduced = plane['large'], plane['reduced']
    else:
        origin = (x, y)
        large = (width + 32, height + 16)
        reduced = (medium_w + 24, medium_h + (12 if narrow else 14))
    pair(0x115A4D54, origin, 'internal area offset')
    for i, wh in enumerate(sizes):
        pair(0x115A4DB4 + i * 4, wh, f'output size {i}')
    for table in (0x115A4D78, 0x115A4DF0):
        pair(table, large, 'internal large dimensions')
        pair(table + 8, reduced, 'internal reduced dimensions')
    # Native configuration adds the origin y to the edge y (absolute sensor
    # rows), while RAW-plane-relative consumers use the edge alone. Moving
    # the origin by -dy and the edge by +dy keeps both at the v43 position.
    # The x origin is not part of the processing window; RAW-plane-relative
    # descriptors need +dx, applied by a separate selector table in the UI.
    dx, dy = x - origin[0], y - origin[1]
    descriptor_edges = []
    for i in range(6):
        original = struct.unpack('<2H', plan.image.read(source_crop + i * 4, 4))
        index = 0 if i < 3 else 1
        delta = tuple(a - b for a, b in zip(sizes[index], source_sizes[index]))
        pair(0x115A4E38 + i * 4, tuple(a + b for a, b in zip(original, delta)),
             f'crop variant {i}')
        edge_x, edge_y = struct.unpack('<2H', plan.image.read(source_edges + i * 4, 4))
        pair(0x115A4EB0 + i * 4, (edge_x, edge_y + dy), f'crop edge variant {i}')
        descriptor_edges.append((edge_x + dx, edge_y + dy))
    # Native configuration keeps a full-width parent and applies horizontal
    # trim separately. All three JPEG size selections use this sensor crop.
    for i in range(3):
        pair(0x11571E30 + i * 4, (x, 0), f'normal horizontal trim {i}')
        # Real capture with info+0x228 == 3 reads a separate direct table.
        # Keep its native vertical alignment; only add the horizontal crop.
        alternate = 0x11571DF4 + i * 4
        native_y = struct.unpack('<2H', plan.image.read(alternate, 4))[1]
        pair(alternate, (x, native_y), f'alternate horizontal trim {i}')
    candidate, report = plan.finish()
    report.update(profile=profile, ratio=[p, q], target_jpeg_wh=sizes,
                  target_large_in_dx_xywh=[x, y, width, height], geometry_edits=edits,
                  raw_plane_wh=list(large), raw_origin_xy=list(origin),
                  stock_raw_plane=stock_raw_plane,
                  descriptor_edges=[list(v) for v in descriptor_edges],
                  status='emulator_only_not_camera_ready',
                  replaces='Existing area 2 in this independent trial; no menu expansion',
                  limitations=[
                      'Independent research EG, not a camera update. Do not install.',
                      'Normal synthetic still-photo geometry only; alternate/RAW/live-view paths unvalidated.',
                      'Global table edits affect all consumers, without a live mode guard.',
                      'Six separate images do not provide a six-choice camera menu.',
                      'No sensor, ISP, JPEG image, metadata-file or SD-output verification.'])
    return candidate, report
