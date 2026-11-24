"""Unicorn parity proof: ARM LUT core vs the host integer reference.

Runs the assembled gate + lut_core + lut_lookup from lut_core.py over synthetic
format-1 image descriptors and compares every output byte against
lut.apply_frame_int. The stock cache services are replaced with recorders and
the params object mirrors 0xC295BE94. No firmware is produced here.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions import lut
from z30_extensions.container import extract_eg
from z30_extensions.features import lut_core
from z30_extensions.platform.z30_120 import StockImage, sha256
from native_runtime import Context, RAM, STACK, STOP
from trace_lut_retouch import put, returning
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_PC

CODE = RAM + 0x20000
PARAMS = RAM + 0x19000
DESC_IN = RAM + 0x1A000
DESC_OUT = RAM + 0x1B000
Y_IN, C_IN = RAM + 0x30000, RAM + 0x38000
Y_OUT, C_OUT = RAM + 0x40000, RAM + 0x48000


def image_descriptor(u, address, stride_y, stride_c, width, height, y, c):
    u.mem_write(address, bytes(0x1C))
    u.mem_write(address, bytes([1]))
    u.mem_write(address + 2, struct.pack('<4H', stride_y, stride_c, stride_y, height))
    u.mem_write(address + 0xA, struct.pack('<2H', width, height))
    put(u, address + 0x14, y)
    put(u, address + 0x18, c)


def gradient_image(width, height, stride_y, stride_c):
    y_plane = bytearray(stride_y * height)
    c_plane = bytearray(stride_c * height)
    for row in range(height):
        for column in range(stride_y):
            y_plane[row * stride_y + column] = (row * 7 + column * 3) % 256
        for column in range(0, stride_c - 1, 2):
            c_plane[row * stride_c + column] = (row * 5 + column * 11) % 256
            c_plane[row * stride_c + column + 1] = (row * 13 + column * 2) % 256
    return bytes(y_plane), bytes(c_plane)


def run_case(data, name, width, height, stride_y, stride_c, binary, simd=False,
             in_place=False, x0=0, y0=0, full_desc=None):
    c = Context(data, data)
    u = c.u
    block_address = CODE + 0x8000
    code = lut_core.build_lut_core(block_address, CODE, simd=simd)
    block = lut_core.build_block(binary)
    u.mem_write(CODE, bytes(0x8000))
    u.mem_write(CODE, code)
    u.mem_write(block_address, block)
    cache = []

    def record(uc, address, size, unused):
        if address in (lut_core.CACHE_INVALIDATE, lut_core.CACHE_CLEAN):
            cache.append((address, uc.reg_read(1)))
            returning(uc, 0)

    u.hook_add(UC_HOOK_CODE, record)
    rows = height + y0
    y_in, c_in = gradient_image(width, rows, stride_y, stride_c)
    u.mem_write(Y_IN, bytes(0x8000))
    u.mem_write(Y_IN, y_in)
    u.mem_write(C_IN, c_in)
    u.mem_write(Y_OUT, bytes(0x8000))
    u.mem_write(Y_OUT, y_in if in_place else bytes(0x8000))
    u.mem_write(C_OUT, bytes(0x8000))
    if in_place:
        u.mem_write(C_OUT, c_in)
        image_descriptor(u, DESC_OUT, stride_y, stride_c, width, height, Y_IN, C_IN)
    else:
        image_descriptor(u, DESC_OUT, stride_y, stride_c, width, height, Y_OUT, C_OUT)
    image_descriptor(u, DESC_IN, stride_y, stride_c, width, height, Y_IN, C_IN)
    if x0 or y0:
        u.mem_write(DESC_IN + 0xE, struct.pack('<2H', x0, y0))
        u.mem_write(DESC_OUT + 0xE, struct.pack('<2H', x0, y0))
    u.mem_write(PARAMS, bytes(0x18))
    put(u, PARAMS + 4, DESC_IN)
    put(u, PARAMS + 0x10, DESC_OUT)
    result = c.call(CODE, (PARAMS,))
    status = u.mem_read(PARAMS + 0x14, 1)[0]
    assert result == 1 and status == 1, (name, result, status)
    out_y, out_c = (Y_IN, C_IN) if in_place else (Y_OUT, C_OUT)
    y_got = bytes(u.mem_read(out_y, stride_y * rows))
    c_got = bytes(u.mem_read(out_c, stride_c * rows))
    if in_place:
        y_want, c_want = lut.apply_frame_int(binary, y_in, c_in, width, height,
                                             stride_y, stride_c, x0=x0, y0=y0)
    else:
        # Bytes outside the window keep whatever the fresh output planes held (0).
        y_want, c_want = lut.apply_frame_int(binary, y_in, c_in, width, height,
                                             stride_y, stride_c, x0=x0, y0=y0,
                                             base_y=bytes(stride_y * rows),
                                             base_c=bytes(stride_c * rows))
    y_diff = [(index, a, b) for index, (a, b) in enumerate(zip(y_got, y_want)) if a != b]
    c_diff = [(index, a, b) for index, (a, b) in enumerate(zip(c_got, c_want)) if a != b]
    assert not y_diff and not c_diff, (name, y_diff[:8], c_diff[:8])
    spans = [r1 for address, r1 in cache]
    return dict(case=name, core='simd' if simd else 'scalar', width=width, height=height,
                stride_y=stride_y, stride_c=stride_c, result=1, y_bytes=len(y_got),
                cache_calls=[hex(a) for a, _ in cache], in_place=in_place, x0=x0, y0=y0,
                pixel_parity=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output', type=Path,
                        default=Path('build/research/lut-postprocess/lut-core-native.json'))
    args = parser.parse_args()
    data = extract_eg(args.source.read_bytes())
    StockImage(data)

    identity = lut.pack_binary(4, (0, 0, 0), (1, 1, 1),
                               lut.parse_cube(lut.identity_cube(4))['table'])
    # A recognizable but invertible test transform: invert red, boost green.
    size = 4
    rows = []
    for blue in range(size):
        for green in range(size):
            for red in range(size):
                rows.append((1.0 - red / 3, min(1.0, green / 3 * 1.5), blue / 3))
    test_cube = (f'LUT_3D_SIZE {size}\n'
                 + '\n'.join(' '.join(f'{v:.6f}' for v in row) for row in rows) + '\n')
    parsed = lut.parse_cube(test_cube)
    test_lut = lut.pack_binary(size, parsed['domain_min'], parsed['domain_max'],
                               parsed['table'])
    big17 = lut.pack_binary(17, (0, 0, 0), (1, 1, 1),
                            lut.parse_cube(lut.identity_cube(17))['table'])
    # A nonzero shared domain exercises the min clamps on every axis.
    big_rows = []
    for blue in range(17):
        for green in range(17):
            for red in range(17):
                big_rows.append((0.1 + 0.8 * red / 16, 0.15 + 0.7 * green / 16,
                                 0.2 + 0.6 * blue / 16))
    big_parsed = lut.parse_cube('DOMAIN_MIN 0.1 0.15 0.2\nDOMAIN_MAX 0.9 0.85 0.8\n'
                                + 'LUT_3D_SIZE 17\n'
                                + '\n'.join(' '.join(f'{v:.6f}' for v in row) for row in big_rows) + '\n')
    domain17 = lut.pack_binary(17, big_parsed['domain_min'], big_parsed['domain_max'],
                               big_parsed['table'])

    cases = [
        run_case(data, 'identity_even', 16, 8, 16, 16, identity),
        run_case(data, 'identity_wide', 64, 12, 64, 64, identity),
        run_case(data, 'test_odd_width', 15, 9, 16, 16, test_lut),
        run_case(data, 'test_padded_stride', 33, 10, 64, 64, test_lut),
        run_case(data, 'identity_even_simd', 16, 8, 16, 16, identity, simd=True),
        run_case(data, 'identity_wide_simd', 64, 12, 64, 64, identity, simd=True),
        run_case(data, 'test_odd_width_simd', 15, 9, 16, 16, test_lut, simd=True),
        run_case(data, 'test_padded_stride_simd', 33, 10, 64, 64, test_lut, simd=True),
        run_case(data, 'simd_in_place', 33, 10, 64, 64, test_lut, simd=True, in_place=True),
        run_case(data, 'simd_window_x0', 33, 10, 64, 64, test_lut, simd=True, x0=18),
        run_case(data, 'simd_window_y0', 33, 10, 64, 64, test_lut, simd=True, y0=4),
        run_case(data, 'simd_one_row', 64, 1, 64, 64, test_lut, simd=True),
        run_case(data, 'simd_17cube', 33, 10, 64, 64, big17, simd=True),
        run_case(data, 'simd_17cube_domain', 33, 10, 64, 64, domain17, simd=True),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(source_sha256=sha256(data), cases=cases,
        limitations=['Synthetic planes and params; stock cache services replaced with recorders.',
                     'Parity is against lut.apply_frame_int, not against real camera decode output.',
                     'No firmware output; correctness contract only.']), indent=2) + '\n',
        encoding='utf-8')
    print(f'PASS: {len(cases)} ARM-vs-host parity cases; every byte matched.')


if __name__ == '__main__':
    main()
