"""Execute SD loader/parser in Unicorn; file I/O is replaced, never camera I/O."""
import argparse
import hashlib
import json
import struct
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.host import lut
from z30_extensions.assembly import assemble
from z30_extensions.container import extract_eg
from z30_extensions.features.lut import core as lut_core, sdload as sd
from z30_extensions.platform.z30_120 import StockImage, sha256
from native_runtime import Context, RAM
from trace_lut_retouch import put, word, returning
from trace_lut_buffers import registers
from verify_lut_core_native import image_descriptor, gradient_image, PARAMS, DESC_IN, DESC_OUT, Y_IN, C_IN, Y_OUT, C_OUT
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn.arm_const import UC_ARM_REG_R1

CODE, PARSER, ARENA = RAM + 0x1000, RAM + 0x5000, 0x51000000


def payload(text):
    parsed = lut.parse_cube(text)
    return lut.pack_binary(parsed['size'], parsed['domain_min'], parsed['domain_max'], parsed['table'])


def table_text(size=2, domains=False):
    lines = ['# asymmetric RGB; blue is deliberately nonzero', 'TITLE "SD test"',
             f'LUT_3D_SIZE {size}']
    if domains:
        lines += ['DOMAIN_MIN 0.1 0.2 0.3', 'DOMAIN_MAX 0.9 0.8 0.7']
    for blue in range(size):
        for green in range(size):
            for red in range(size):
                if domains:
                    row = (0.1 + 0.8 * red / (size - 1), 0.2 + 0.6 * green / (size - 1), 0.3 + 0.4 * blue / (size - 1))
                else:
                    row = (0.13 + 0.7 * red / (size - 1), 0.23 + 0.5 * green / (size - 1), 0.33 + 0.4 * blue / (size - 1))
                lines.append(' '.join(f'{v:.6f}' for v in row))
    return '\n'.join(lines) + '\n'


class Fixture:
    def __init__(self, data):
        self.c = Context(data, data)
        self.u = self.c.u
        self.binary = payload(lut.identity_cube(2))
        self.block, self.layout = sd.build_block(self.binary, ARENA)
        self.c.map(ARENA - 4096, len(self.block) + 8192)
        self.u.mem_write(CODE, sd.build_loader(CODE))
        self.u.mem_write(PARSER, assemble(sd.parse_cube_source(), PARSER, 0x4000))
        self.events = []
        self.content = b''
        self.offset = 0
        self.chunk = 65536
        self.reads = 0
        self.no_seek = False
        self.error = None
        for address in (sd.FS_OPEN, sd.FS_READ, sd.FS_CLOSE, sd.FS_SEEK):
            self.u.hook_add(UC_HOOK_CODE, self.boundary, begin=address, end=address)

    def boundary(self, u, address, size, unused):
        if address == sd.FS_OPEN:
            path, mode, attr = registers(u, 3)
            assert bytes(u.mem_read(path, len(sd.LUT_NAME))) == sd.LUT_NAME
            assert mode == attr == 0
            self.events.append(dict(kind='open', mode=mode, attr=attr))
            self.offset = 0
            returning(u, 0xA0000001 if self.error == 'open' else 17)
        elif address == sd.FS_SEEK:
            fd, offset, whence, out = registers(u, 4)
            assert fd == 17 and whence in (0, 2)
            if self.no_seek:
                # Unsupported call: the loader must fall back to streaming.
                put(u, out, 0)
                self.events.append(dict(kind='seek', whence=whence, offset=offset))
                returning(u)
                return
            self.offset = len(self.content) if whence == 2 else offset
            self.events.append(dict(kind='seek', whence=whence, offset=offset))
            put(u, out, self.offset)
            returning(u)
        elif address == sd.FS_READ:
            fd, dest, requested, actual = registers(u, 4)
            assert fd == 17 and requested > 0
            self.reads += 1
            self.events.append(dict(kind='read', requested=requested))
            if self.error == 'read' and self.reads > 1:
                returning(u, 0xA0000002)
                return
            count = min(requested, self.chunk, len(self.content) - self.offset)
            if count:
                u.mem_write(dest, self.content[self.offset:self.offset + count])
            put(u, actual, requested + 1 if self.error == 'bad_count' else count)
            self.offset += count
            returning(u)
        else:
            assert registers(u, 1)[0] == 17
            self.events.append(dict(kind='close'))
            returning(u, 0xA0000003 if self.error == 'close' else 0)

    def load(self, content, error=None, chunk=65536, read_cap=None, out_cap=None, active=None,
             no_seek=False):
        self.u.mem_write(ARENA, self.block)
        layout = self.layout
        workspace = ARENA + layout['workspace']
        if read_cap is not None:
            put(self.u, workspace + 4, read_cap)
        if out_cap is not None:
            put(self.u, workspace + 12, out_cap)
        if active is not None:
            put(self.u, ARENA + 4, active)
        self.content, self.error, self.chunk, self.offset = content, error, chunk, 0
        self.reads = 0
        self.no_seek = no_seek
        self.events.clear()
        before = bytes(self.u.mem_read(ARENA + 4, 12))
        result = self.c.call(CODE, (ARENA, workspace), limit=max(30_000_000, len(content) * 100))
        assert word(self.u, ARENA + 8) == result
        assert self.u.reg_read(UC_ARM_REG_R1) == (17 if error == 'close' else 0xFFFFFFFF)
        closes = sum(e['kind'] == 'close' for e in self.events)
        assert closes == int(error != 'open' and any(e['kind'] == 'open' for e in self.events))
        if result:
            assert word(self.u, ARENA + 4) == struct.unpack_from('<I', before)[0]
            assert word(self.u, ARENA + 12) == struct.unpack_from('<I', before, 8)[0]
            assert bytes(self.u.mem_read(ARENA + 16, len(self.binary))) == self.binary
        else:
            assert word(self.u, ARENA + 4) == ARENA + layout['sd_slot']
        return result


def run(data):
    f = Fixture(data)
    u = f.u
    rows = []
    text = table_text()
    for name, content in [('asymmetric', text), ('identity', lut.identity_cube(3)),
                          ('nondefault_domain', table_text(domains=True)),
                          ('case_crlf', text.lower().replace('\n', '\r\n')),
                          ('no_final_newline', text.rstrip()),
                          ('leading_dot_sign', text.replace('0.130000', '+.130000')),
                          ('identity_65_text', lut.identity_cube(65))]:
        expected = payload(content)
        assert f.load(content.encode(), chunk=65536 if name == 'identity_65_text' else 17) == 0, name
        got = bytes(u.mem_read(word(u, ARENA + 4), word(u, ARENA + 12)))
        assert got == expected, (name, next((i for i, pair in enumerate(zip(got, expected)) if pair[0] != pair[1]), None))
        rows.append(dict(case=name, bytes=len(expected), parity=True, calls=list(f.events)))
    for size in (2, 17, 65):
        binary = lut.BINARY_HEADER.pack(b'WL3L', 1, size, 0, 0, 0, 0, 65535, 65535, 65535) + bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC]) * size ** 3
        assert f.load(binary, chunk=4093) == 0
        assert bytes(u.mem_read(word(u, ARENA + 4), len(binary))) == binary
        rows.append(dict(case=f'binary_{size}', bytes=len(binary), parity=True, calls=list(f.events)))
    invalid = [
        ('missing_size', '0 0 0\n'), ('extra_row', text + '0 0 0\n'),
        ('missing_row', '\n'.join(text.splitlines()[:-1])),
        ('six_numbers', text.replace('0.130000 0.230000 0.330000\n', '0.13 0.23 0.33 0.1 0.2 0.3\n')),
        ('out_of_domain', text.replace('0.130000', '-0.1')),
        ('nan', text.replace('0.130000', 'nan')), ('exponent_rejected', text.replace('0.130000', '1.3e-1')),
        ('duplicate_size', text.replace('LUT_3D_SIZE 2', 'LUT_3D_SIZE 2\nLUT_3D_SIZE 2')),
        ('inverted_domain', text.replace('LUT_3D_SIZE 2', 'LUT_3D_SIZE 2\nDOMAIN_MIN 1 1 1')),
        ('collapsed_u16_domain', text.replace('LUT_3D_SIZE 2', 'LUT_3D_SIZE 2\nDOMAIN_MAX .000000001 1 1')),
        ('late_domain', text + 'DOMAIN_MAX 1 1 1\n'),
        ('nul', text + '\x00'), ('too_many_fraction_digits', text.replace('0.130000', '.1300000000')),
        ('unknown', text.replace('LUT_3D_SIZE', 'LUT_1D_SIZE')),
        ('size_66', text.replace('SIZE 2', 'SIZE 66')),
    ]
    for name, content in invalid:
        assert f.load(content.encode()) == 3, name
        rows.append(dict(case=name, rejected=True, calls=list(f.events)))
    binary = payload(text)
    bad_binary = [('truncated', binary[:-1]), ('trailing_bytes', binary + b'!'),
                  ('bad_version', binary[:4] + b'\x02\0' + binary[6:]),
                  ('reserved', binary[:8] + b'\x01\0' + binary[10:]),
                  ('empty_domain', binary[:16] + bytes(6) + binary[22:])]
    for name, content in bad_binary:
        assert f.load(content) == 3, name
        rows.append(dict(case=name, rejected=True, calls=list(f.events)))
    for error, expected in [('open', 1), ('read', 2), ('close', 2), ('bad_count', 2)]:
        assert f.load(text.encode(), error=error, chunk=13) == expected
        rows.append(dict(case=error, rejected=True, calls=list(f.events)))
    for name, kwargs, expected in [
        # The size from seek(END) decides the request, so a buffer that matches
        # the file exactly is a success; one byte short is still a bounds error.
        ('exact_input_capacity', dict(read_cap=len(binary)), 0),
        ('stream_without_seek', dict(no_seek=True), 0),
        ('overflow_input', dict(read_cap=len(binary) - 1), 4),
        ('output_small', dict(out_cap=len(binary) - 1), 3),
        ('active_candidate', dict(active=ARENA + f.layout['sd_slot']), 4),
        ('active_input', dict(active=ARENA + f.layout['read_buf']), 4),
    ]:
        assert f.load(binary, **kwargs) == expected, name
        rows.append(dict(case=name, result=expected, calls=list(f.events)))
    # Direct parser guards: capacity failures cannot write outside output;
    # incomplete final tokens cannot read beyond the exact input end.
    inp, out = RAM + 0x80000, RAM + 0x90000
    for length in (0, 1, 7, 17, len(text) - 4, len(text)):
        blob = text.encode()[:length]
        u.mem_write(inp, blob + b'\xCC' * 64)
        u.mem_write(out, b'\xA5' * 256)
        bad_reads = []
        def reads(uc, access, address, size, value, unused):
            if inp <= address < inp + len(text) + 64 and address + size > inp + length:
                bad_reads.append((address, size))
        hook = u.hook_add(UC_HOOK_MEM_READ, reads)
        result = f.c.call(PARSER, (inp, inp + length, out, 30))
        u.hook_del(hook)
        assert result == 0xFFFFFFFF and not bad_reads
        assert bytes(u.mem_read(out + 30, 226)) == b'\xA5' * 226
        rows.append(dict(case=f'parser_guard_{length}', rejected=True, no_overread=True, no_overwrite=True))
    # Arithmetic oracle is independent of the host's float packer.
    quant = RAM + 0xD000
    u.mem_write(quant, assemble(sd._quantize_source(), quant, 1024))
    for numerator, denominator in ((0, 1), (1, 1), (1, 2), (1, 6), (3, 10),
                                   (123456789, 999999999), (999999999, 1000000000)):
        got = f.c.call(quant, (numerator, denominator))
        assert got == round(Fraction(numerator * 65535, denominator))
        rows.append(dict(case=f'rational_{numerator}_{denominator}', result=got))
    # Deliberate arithmetic-contract distinction: float(.3)-float(.1) is
    # slightly below the exact half-domain position. Record it, don't claim
    # all decimal inputs are byte-identical to the host float packer.
    tie_text = ('LUT_3D_SIZE 2\nDOMAIN_MIN .1 .1 .1\nDOMAIN_MAX .5 .5 .5\n'
                + '.3 .3 .3\n' * 8)
    assert f.load(tie_text.encode()) == 0
    actual = bytes(u.mem_read(word(u, ARENA + 4), word(u, ARENA + 12)))
    assert struct.unpack_from('<24H', actual, 22) == (32768,) * 24
    host_channels = struct.unpack_from('<24H', payload(tie_text), 22)
    assert max(abs(a - b) for a, b in zip((32768,) * 24, host_channels)) <= 1
    rows.append(dict(case='exact_decimal_half_domain', exact_channels=32768,
                     host_float_channels=host_channels[0], possible_host_difference_lsb=1))
    # Reload uses another inactive slot; failure preserves the previous SD data.
    assert f.load(text.encode()) == 0
    previous = word(u, ARENA + 4)
    previous_bytes = bytes(u.mem_read(previous, word(u, ARENA + 12)))
    alternate = ARENA + len(f.block) + 0x1000
    f.c.map(alternate, sd.SD_SLOT_SIZE)
    put(u, ARENA + f.layout['workspace'] + 8, alternate)
    for name, content, expected in [('reload_failure', (text + '0 0 0\n').encode(), 3),
                                     ('reload_success', lut.identity_cube(2).encode(), 0)]:
        f.content, f.offset, f.error = content, 0, None
        f.events.clear()
        result = f.c.call(CODE, (ARENA, ARENA + f.layout['workspace']))
        assert result == expected
        assert bytes(u.mem_read(previous, len(previous_bytes))) == previous_bytes
        assert word(u, ARENA + 4) == (previous if result else alternate)
        if not result:
            assert bytes(u.mem_read(alternate, word(u, ARENA + 12))) == f.binary
        rows.append(dict(case=name, result=result, prior_payload_preserved=True, calls=list(f.events)))
    # Loader output is consumed through the active pointer by the actual core.
    assert f.load(text.encode()) == 0
    core_address = RAM + 0x10000
    u.mem_write(core_address, lut_core.build_lut_core(ARENA, core_address, active_payload=True))
    for address in (lut_core.CACHE_INVALIDATE, lut_core.CACHE_CLEAN):
        u.hook_add(UC_HOOK_CODE, lambda uc, a, s, x: returning(uc), begin=address, end=address)
    width, height, stride = 8, 3, 16
    yin, cin = gradient_image(width, height, stride, stride)
    u.mem_write(Y_IN, yin); u.mem_write(C_IN, cin)
    u.mem_write(Y_OUT, bytes(len(yin))); u.mem_write(C_OUT, bytes(len(cin)))
    image_descriptor(u, DESC_IN, stride, stride, width, height, Y_IN, C_IN)
    image_descriptor(u, DESC_OUT, stride, stride, width, height, Y_OUT, C_OUT)
    u.mem_write(PARAMS, bytes(24)); put(u, PARAMS + 4, DESC_IN); put(u, PARAMS + 16, DESC_OUT)
    assert f.c.call(core_address, (PARAMS,)) == 1
    # The fresh output planes were zeroed; bytes outside the window stay zero.
    want_y, want_c = lut.apply_frame_int(payload(text), yin, cin, width, height, stride, stride,
                                         base_y=bytes(len(yin)), base_c=bytes(len(cin)))
    assert bytes(u.mem_read(Y_OUT, len(yin))) == want_y
    assert bytes(u.mem_read(C_OUT, len(cin))) == want_c
    rows.append(dict(case='loaded_payload_to_core', pixel_parity=True))
    stock_calls = []
    def stock_validator(uc, address, size, unused):
        stock_calls.append(address)
        returning(uc)
    u.hook_add(UC_HOOK_CODE, stock_validator, begin=lut_core.DISPLACED_CALL, end=lut_core.DISPLACED_CALL)
    put(u, ARENA, 0)
    assert f.c.call(core_address, (PARAMS,)) == 0 and len(stock_calls) == 1
    assert bytes(u.mem_read(Y_OUT, len(yin))) == want_y
    assert bytes(u.mem_read(C_OUT, len(cin))) == want_c
    rows.append(dict(case='dynamic_mode_disabled', stock_fallback=True, output_unchanged=True))
    return rows


def run_cubes(data, paths):
    """Load caller-supplied vector files through the real loader.

    Files the host parser accepts must activate with byte parity (one LSB of
    decimal-vs-float rounding tolerated); files it rejects must report status 3
    and leave the previously active table untouched.
    """
    f = Fixture(data)
    u = f.u
    rows = []
    for path in paths:
        content = path.read_bytes()
        result = f.load(content, chunk=65536)
        row = dict(cube=path.name, bytes=len(content), status=result)
        try:
            parsed = lut.unpack_binary(content) if content[:4] == lut.BINARY_MAGIC else lut.parse_cube(content.decode())
        except (lut.CubeError, UnicodeDecodeError):
            parsed = None
        if parsed is None:
            assert result == 3, (path.name, result)
            rows.append(row)
            continue
        assert result == 0, (path.name, result)
        active = word(u, ARENA + 4)
        got = bytes(u.mem_read(active, word(u, ARENA + 12)))
        expected = lut.pack_binary(parsed['size'], parsed['domain_min'], parsed['domain_max'], parsed['table'])
        assert len(got) == len(expected), (path.name, len(got), len(expected))
        delta = max(abs(a - b) for a, b in zip(struct.unpack(f'<{len(got) // 2}H', got),
                                              struct.unpack(f'<{len(expected) // 2}H', expected)))
        assert delta <= 1, (path.name, delta)
        row.update(size=parsed['size'], parity_lsb=delta)
        rows.append(row)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path, default=Path('build/research/lut-postprocess/sdload.json'))
    p.add_argument('--cube', type=Path, action='append', default=[],
                   help='test vector file to load through the real parser; repeatable')
    args = p.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('Source and output must differ')
    data = extract_eg(args.source.read_bytes()); StockImage(data)
    rows = run(data)
    if args.cube:
        rows += run_cubes(data, args.cube)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(source_sha256=sha256(data), cases=rows,
        loader_sha256=hashlib.sha256(sd.build_loader(CODE)).hexdigest(), camera_access=False,
        limitations=['File services and caches replaced; no card, scheduler or runtime allocation.',
                     'Caller owns RAM and serializes loading/processing; component not installed in firmware.',
                     'Text is a documented decimal subset; no exponent support or arbitrary float equivalence.',
                     'Color matrix/range/CbCr order remain unverified on device.']), indent=2) + '\n', encoding='utf-8')
    print(f'PASS: {len(rows)} SD loader/parser/core cases; no camera or SD access.')


if __name__ == '__main__':
    main()
