"""Execute built SD-host functions with explicit SD, heap and cache fixtures.

The candidate loader, validator, PIC payload and callback code execute. This
fixture does not emulate a camera boot, physical SD, kernel pool or cache.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from native_runtime import Context, RAM
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR
from z30_extensions.build import build
from z30_extensions.container import extract_eg
from z30_extensions.modules import example, pack, MAX_TEXT
from z30_extensions.assembly import assemble
from z30_extensions.features import module_host as mh


class Fixture(Context):
    def __init__(self, stock, image, report, payload, *, chunk=None, cache=True):
        super().__init__(stock, image)
        self.f = {k: int(v, 16) for k, v in report['features']['shutter_sound']['functions'].items()}
        self.payload, self.chunk = payload, chunk
        self.position, self.fd, self.close_failures = 0, None, 0
        self.read_error = self.free_error = self.alloc_error = False
        self.blocks, self.cache_calls, self.instructions = {}, [], []
        self.next_block = RAM + 0x10000
        self.seed(0x1129, 2)
        for address in (0x10211F4C, 0x10211F5E, 0x103382A2, 0x10338414, 0x1033832E, 0x102C89EE):
            self.u.hook_add(UC_HOOK_CODE, self.service, begin=address, end=address)
        if cache:
            self.u.hook_add(UC_HOOK_CODE, self.cache, begin=self.f['module_sync'] & ~1, end=self.f['module_sync'] & ~1)
        self.host = self.function('ensure')
        assert self.host and self.function('ensure') == self.host
        self.state = self.host + mh.STATE_OFFSET
        self.baseline = dict(self.blocks)
        self.u.hook_add(UC_HOOK_CODE, self.observe)

    def ret(self, result):
        self.u.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
        self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def word(self, offset):
        return int.from_bytes(self.u.mem_read(self.state + offset, 4), 'little')

    def set_module(self, offset, value):
        self.u.mem_write(self.state + offset, struct.pack('<I', value))

    def text(self):
        return bytes(self.u.mem_read(self.state + mh.TEXT, 64)).split(b'\0')[0].decode('ascii')

    def function(self, name, args=()):
        return self.call(self.f[name], args)

    def cache(self, u, a, n, d):
        self.cache_calls.append((u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)))
        self.ret(1)

    def observe(self, u, a, n, d):
        allocation, total = self.word(mh.ALLOCATION), self.word(mh.TOTAL)
        if allocation and allocation + 64 <= a < allocation + total:
            assert self.fd is None and self.cache_calls
            self.instructions.append(a)

    def service(self, u, address, n, d):
        r = [u.reg_read(reg) for reg in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        if address == 0x10211F4C:
            assert r[1] == 0, 'General pool is explicit; alignment is not a pool index'
            if self.alloc_error:
                return self.ret(0)
            pointer = (self.next_block + 31) & ~31
            self.next_block = pointer + r[0] + 32
            assert self.next_block < RAM + 0xF0000
            self.blocks[pointer] = r[0]
            self.u.mem_write(pointer, b'\xA5' * r[0])
            return self.ret(pointer)
        if address == 0x10211F5E:
            assert r[0] in self.blocks, 'Unknown or duplicate release'
            if self.free_error:
                return self.ret(1)
            del self.blocks[r[0]]
            return self.ret(0)
        if address == 0x103382A2:
            assert r[1:3] == [0, 0], 'Only read-only open is supported'
            path = int.from_bytes(u.mem_read(r[0], 4), 'little')
            if bytes(u.mem_read(path, 16)).split(b'\0')[0] in (b'A:\\WL-SOUND.CFG',b'A:\\WL-VIDEO.CFG'):
                u.mem_write(r[0] + 8, struct.pack('<i', -1))
                return self.ret(mh.SUCCESS + 1)
            assert bytes(u.mem_read(path, 17)).split(b'\0')[0] == b'A:\\Z30\\CORE.ZMOD'
            if self.payload is None:
                u.mem_write(r[0] + 8, struct.pack('<i', -1))
                return self.ret(mh.SUCCESS + 1)
            assert self.fd is None
            self.fd, self.position = 7, 0
            u.mem_write(r[0] + 8, struct.pack('<I', self.fd))
            return self.ret(mh.SUCCESS)
        if address == 0x10338414:
            assert self.fd == int.from_bytes(u.mem_read(r[0] + 8, 4), 'little') == 7
            if self.read_error:
                return self.ret(mh.SUCCESS + 1)
            count = min(r[2], self.chunk or r[2])
            data = self.payload[self.position:self.position + count]
            self.position += len(data)
            u.mem_write(r[1], data)
            u.mem_write(r[3], struct.pack('<I', len(data)))
            return self.ret(mh.SUCCESS)
        assert self.fd == 7
        if self.close_failures:
            self.close_failures -= 1
            # Native close resets its object even when its backend reports an
            # error. The host must retain the raw fd independently for retry.
            u.mem_write(self.state + mh.FILE_OBJECT + 8, struct.pack('<i', -1))
            return self.ret(1 if address == 0x102C89EE else mh.SUCCESS + 1)
        self.fd = None
        return self.ret(0 if address == 0x102C89EE else mh.SUCCESS)


def verify(stock, image, report):
    rows = []
    for kind in ('counter', 'grid'):
        for chunk in (None, 3, 7):
            s = Fixture(stock, image, report, example(kind), chunk=chunk)
            assert s.function('module_load') == 1 and s.word(mh.PHASE) == 2
            assert s.text() == kind + ' module ready' and s.instructions
            allocation = s.word(mh.ALLOCATION)
            assert s.cache_calls == [(allocation + 64, s.word(mh.TOTAL) - 64)]
            for i in range(1, 4):
                assert s.function('module_run') == 1
                assert s.text() == ('Runs: ' + str(i) if kind == 'counter' else 'Grid: 2')
            if kind == 'counter':
                s.set_module(mh.CONTEXT, 0xFFFFFFFE)
                assert s.function('module_run') == 1 and s.text() == 'Runs: 4294967295'
                assert s.function('module_run') == 1 and s.text() == 'Runs: 0'
            assert s.function('module_load') == 0 and s.word(mh.ERROR) == 9
            assert s.word(mh.ALLOCATION) == allocation and len(s.blocks) == len(s.baseline) + 1
            # String validation is transactional and bounds the source to the
            # allocation/context. Failed writes must preserve the old output.
            s.set_module(mh.PHASE, 3)
            source = s.state + mh.CONTEXT
            for text in (b'X' * (MAX_TEXT + 1) + b'\0', b'bad\x01text\0'):
                previous = s.text()
                s.u.mem_write(source, text)
                assert s.function('module_set_text', (source,)) == 0 and s.text() == previous
            assert s.function('module_set_text', (source + 64,)) == 0
            s.u.mem_write(source, b'X' * MAX_TEXT + b'\0')
            assert s.function('module_set_text', (source,)) == 1 and s.text() == 'X' * MAX_TEXT
            s.set_module(mh.PHASE, 2)
            assert s.function('module_set_handler', (allocation + 65,)) == 0
            rows.append(dict(module=kind,chunk=chunk,allocated_code=hex(allocation + 64),callback_and_context=True))
    valid = example('counter')
    bad = [('missing', None, 1), ('truncated', valid[:-1], 2), ('trailing', valid + b'X', 3),
           ('crc', valid[:-1] + bytes([valid[-1] ^ 1]), 3)]
    for offset in (0, 4, 8, 12, 16, 24, 32, 60):
        payload = bytearray(valid)
        payload[offset] ^= 1
        bad.append(('header_' + str(offset), bytes(payload), 3))
    init_fail = pack(assemble('movs r0,#0; bx lr;', 0, 4))
    no_handler = pack(assemble('movs r0,#1; bx lr;', 0, 4))
    bad += [('init_failed', init_fail, 7), ('missing_handler', no_handler, 8)]
    for name, payload, error in bad:
        s = Fixture(stock, image, report, payload)
        assert s.function('module_load') == 0 and s.word(mh.ERROR) == error
        assert s.blocks == s.baseline and not s.word(mh.HANDLER) and not s.word(mh.ENTRY) and s.fd is None
        assert not s.instructions if error < 7 else s.instructions
        rows.append(dict(case=name,cleaned=True))
    s = Fixture(stock, image, report, valid, cache=False)
    assert s.function('module_load') == 0 and s.word(mh.ERROR) == 6 and s.blocks == s.baseline
    assert not s.instructions
    rows.append(dict(case='native_cache_registry_unavailable',entry_prevented=True))
    s = Fixture(stock, image, report, valid)
    s.close_failures = 4
    for _ in range(2):
        assert s.function('module_load') == 0 and s.word(mh.ERROR) == 4 and s.word(mh.FD) == 7
    assert s.function('module_load') == 1 and s.word(mh.FD) == 0xFFFFFFFF
    rows.append(dict(case='retained_fd_and_close_retry',recovered=True))
    s = Fixture(stock, image, report, valid[:-1])
    s.free_error = True
    assert s.function('module_load') == 0 and s.word(mh.ERROR) == 12 and s.word(mh.ALLOCATION)
    assert not s.word(mh.HANDLER) and not s.word(mh.ENTRY)
    retained = s.word(mh.ALLOCATION)
    assert s.function('module_load') == 0 and s.word(mh.ALLOCATION) == retained
    s.free_error = False
    s.payload = valid
    assert s.function('module_load') == 1 and retained not in s.blocks
    rows.append(dict(case='release_failure_retains_ownership',recovered=True))
    for failure, error in (('read_error', 2), ('alloc_error', 5)):
        s = Fixture(stock, image, report, valid)
        setattr(s, failure, True)
        assert s.function('module_load') == 0 and s.word(mh.ERROR) == error and s.blocks == s.baseline and s.fd is None
        rows.append(dict(case=failure,cleaned=True))
    return dict(passed=True, candidate_sha256=report['sha256'], cases=rows,
                limitations='Synthetic SD/heap/cache/RAM; no camera boot, GUI, physical execution permission or cache coherence.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--extracted-eg', action='store_true')
    args = parser.parse_args()
    supplied = args.source.read_bytes()
    stock = supplied if args.extracted_eg else extract_eg(supplied)
    image, report = build(stock, 'modular', module_host=True)
    print(json.dumps(verify(stock, image, report), indent=2))


if __name__ == '__main__':
    main()
