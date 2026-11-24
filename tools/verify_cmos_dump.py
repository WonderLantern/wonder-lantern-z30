"""Execute the CMOS dump page's export code offline; never a camera.

The patched export, the stock memset/memcpy and the stock scatter descriptors
execute in Unicorn. Allocation and SD file services are explicit fixtures. The
drive data is synthetic random bytes at a fixture address: this proves the
writer's layout and failure handling, not that the camera holds data there.

Usage:
  python tools/verify_cmos_dump.py --source Z_30_0120.bin --output build/research/cmos-dump-verify.json
"""
import argparse
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from z30_extensions.container import extract_eg
from z30_extensions.features import cmos_dump as cd
from z30_extensions.features.sound_layout import SUCCESS
from native_runtime import Context, RAM
from build_cmos_dump import build
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR

ALLOC, SET_PATH, OPEN, WRITE, CLOSE, CLEAN = 0x10211F4C, 0x1033829E, 0x103382A2, 0x102C8A6A, 0x1033832E, cd.CACHE_CLEAN
BLOB = 0x8A600000
STATE = RAM + 0x10000
BOUNCE = RAM + 0x20000
FD = 7


class Fixture:
    def __init__(self, stock, image, feature):
        self.context = Context(stock, image)
        self.u = self.context.u
        self.export = int(feature['functions']['cmos_export'], 16)
        self.context.map(BLOB, cd.BLOB_BYTES)
        self.blob = os.urandom(cd.BLOB_BYTES)
        self.u.mem_write(BLOB, self.blob)
        for address, handler in ((ALLOC, self.alloc), (SET_PATH, self.set_path), (OPEN, self.open),
                                 (WRITE, self.write), (CLOSE, self.close), (CLEAN, self.clean)):
            self.u.hook_add(UC_HOOK_CODE, handler, begin=address, end=address)
        self.reset()

    def word(self, address, value):
        self.u.mem_write(address, struct.pack('<I', value & 0xFFFFFFFF))

    def read_word(self, address):
        return struct.unpack('<I', self.u.mem_read(address, 4))[0]

    def reset(self, pointer=BLOB, alloc_ok=True, open_ok=True, fail_write=None,
              short_write=None, close_ok=True, keep_state=False):
        if not keep_state:
            self.u.mem_write(STATE, bytes(cd.STATE_BYTES))
            self.word(STATE + cd.FD_OFF, -1)
        self.word(cd.CTX + cd.BLOB_FIELD, pointer)
        self.alloc_ok, self.open_ok, self.close_ok = alloc_ok, open_ok, close_ok
        self.fail_write, self.short_write = fail_write, short_write
        self.output, self.calls, self.paths = bytearray(), [], []

    def ret(self, u, value):
        u.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def alloc(self, u, *unused):
        size = u.reg_read(UC_ARM_REG_R0)
        self.calls.append(('alloc', size, u.reg_read(UC_ARM_REG_R1)))
        if self.alloc_ok:
            self.context.map(BOUNCE, size)
        self.ret(u, BOUNCE if self.alloc_ok else 0)

    def set_path(self, u, *unused):
        pointer = u.reg_read(UC_ARM_REG_R1)
        self.paths.append(bytes(u.mem_read(pointer, 32)).split(b'\0')[0].decode())
        self.calls.append(('path', u.reg_read(UC_ARM_REG_R0)))
        self.ret(u, 0)

    def open(self, u, *unused):
        file_object, mode = u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)
        self.calls.append(('open', file_object, mode))
        if self.open_ok:
            self.word(file_object + 8, FD)
        self.ret(u, SUCCESS if self.open_ok else 0x0437000F)

    def write(self, u, *unused):
        fd, pointer, size, count = (u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        index = sum(1 for call in self.calls if call[0] == 'write') + 1
        assert self.calls and self.calls[-1] == ('clean', pointer, size), 'write without a cache clean of its buffer'
        self.calls.append(('write', fd, size))
        assert fd == FD
        if index == self.fail_write:
            self.word(count, 0)
            return self.ret(u, 5)
        done = size // 2 if index == self.short_write else size
        self.output.extend(u.mem_read(pointer, done))
        self.word(count, done)
        self.ret(u, 0)

    def clean(self, u, *unused):
        self.calls.append(('clean', u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)))
        self.ret(u, 0)

    def close(self, u, *unused):
        self.calls.append(('close', u.reg_read(UC_ARM_REG_R0)))
        self.ret(u, SUCCESS if self.close_ok else 0)

    def run(self):
        self.context.call(self.export, (STATE,), limit=60_000_000)
        return self.read_word(STATE + cd.STATUS_OFF)


def expected_file(fixture):
    u = fixture.u
    header = struct.pack('<9I', cd.MAGIC, cd.VERSION, BLOB, cd.BLOB_BYTES,
                         sum(struct.unpack(f'<{cd.BLOB_BYTES // 4}I', fixture.blob)) & 0xFFFFFFFF,
                         cd.CTX, cd.AREA, cd.BANK, fixture.read_word(cd.AREA + 8))
    header += bytes(0x40 - len(header))
    context = bytes(u.mem_read(cd.CTX, cd.CTX_BYTES))
    area = bytes(u.mem_read(cd.AREA, 28)) + bytes(4)
    bank = bytes(u.mem_read(cd.BANK, 44)) + bytes(4)
    head = header + context + area + bank
    assert len(head) == cd.HEADER_BYTES
    return head + fixture.blob


def kinds(calls):
    return [call[0] for call in calls if call[0] != 'clean']


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output', type=Path, default=Path('build/research/cmos-dump-verify.json'))
    args = parser.parse_args()
    stock = extract_eg(args.source.read_bytes())
    image, report = build(stock)
    feature = report['features']['cmos_dump']
    fx = Fixture(stock, image, feature)
    cases = []
    chunks = cd.BLOB_BYTES // cd.CHUNK

    # Stock scatter descriptors describe area 0x4C and NOR bank 26.
    area = struct.unpack('<7I', fx.u.mem_read(cd.AREA, 28))
    bank = struct.unpack('<11I', fx.u.mem_read(cd.BANK, 44))
    assert area[0] == 0x4C and area[3] == cd.BLOB_BYTES and area[5] == 26, area
    assert bank[0] == 0x13E80000 and bank[2] == 0x180000, bank
    cases.append(dict(name='stock_descriptors_area_0x4c_bank_26', passed=True))

    fx.reset()
    status = fx.run()
    expected = expected_file(fx)
    assert status == 1, status
    assert bytes(fx.output) == expected
    assert fx.paths == [cd.PATH]
    assert kinds(fx.calls) == ['alloc', 'path', 'open'] + ['write'] * (1 + chunks) + ['close']
    files = [call for call in fx.calls if call[0] != 'clean']
    assert files[0][1:] == (cd.CHUNK, 0) and files[2][2] == 0x301
    cleans = [call for call in fx.calls if call[0] == 'clean']
    assert cleans == [('clean', BOUNCE, cd.HEADER_BYTES)] + [('clean', BOUNCE, cd.CHUNK)] * chunks
    assert fx.read_word(STATE + cd.OPEN_OFF) == 0 and fx.read_word(STATE + cd.FD_OFF) == 0xFFFFFFFF
    cases.append(dict(name='every_write_preceded_by_cache_clean_of_its_buffer', passed=True, cleans=len(cleans)))
    cases.append(dict(name='success_writes_exact_layout', passed=True, size=len(expected),
                      sha256=hashlib.sha256(expected).hexdigest(), writes=1 + chunks))

    fx.reset(keep_state=True)
    assert fx.run() == 1 and 'alloc' not in kinds(fx.calls) and bytes(fx.output) == expected
    cases.append(dict(name='second_save_reuses_pinned_buffer', passed=True))

    for name, pointer in (('null', 0), ('unaligned', BLOB + 2), ('below_ram', 0x7FFF0000),
                          ('past_guard', cd.RAM_HIGH - cd.BLOB_BYTES + 4)):
        fx.reset(pointer=pointer)
        assert fx.run() == 2 and not fx.calls and not fx.output, name
        cases.append(dict(name=f'refuses_{name}_pointer_without_io', passed=True))

    fx.reset(alloc_ok=False)
    assert fx.run() == 3 and kinds(fx.calls) == ['alloc']
    cases.append(dict(name='allocation_failure_no_file', passed=True))

    fx.reset(open_ok=False)
    assert fx.run() == 4 and 'write' not in kinds(fx.calls) and 'close' not in kinds(fx.calls)
    assert fx.read_word(STATE + cd.OPEN_OFF) == 0
    cases.append(dict(name='open_failure_no_write_no_close', passed=True))

    for label, options in (('header', dict(fail_write=1)), ('middle_chunk', dict(fail_write=10)),
                           ('short_chunk', dict(short_write=5))):
        fx.reset(**options)
        assert fx.run() == 5, label
        assert kinds(fx.calls)[-1] == 'close' and fx.read_word(STATE + cd.OPEN_OFF) == 0, label
        cases.append(dict(name=f'write_failure_{label}_closes_file', passed=True))

    fx.reset(close_ok=False)
    assert fx.run() == 6 and bytes(fx.output) == expected
    cases.append(dict(name='close_failure_reported', passed=True))

    fx.reset()
    assert fx.run() == 1 and bytes(fx.output) == expected_file(fx)
    cases.append(dict(name='recovers_after_failures', passed=True))

    result = dict(source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),
                  image_sha256=hashlib.sha256(image).hexdigest(), feature=feature, cases=cases,
                  passed=all(case['passed'] for case in cases),
                  limitations=['Allocator and SD file services are fixtures.',
                               'Drive data is synthetic; the real pointer and contents are unverified.',
                               'Menu rendering and row dispatch are not executed here.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'{len(cases)} cases passed')


if __name__ == '__main__':
    main()
