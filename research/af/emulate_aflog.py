"""Unicorn: the AF log module (modules/af_log) init / Run / IMU tick / save on a stubbed camera.

Usage: emulate_aflog.py AF_LOG.bin (raw payload from modules/af_log/build.sh)
"""
import struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
from unicorn.arm_const import *


def main(path, v5=False, v6=False, v7=False, v8=False, v9=False, v10=False, v11=False, v12=False):
    code = open(path, 'rb').read()
    LOAD, API, CTX, STACK, POOL, REC = 0x30000000, 0x20000000, 0x20000100, 0x20008000, 0xC4000000, 0xC237F578
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    for a, n in ((LOAD, 0x10000), (0x20000000, 0x10000), (POOL, 0x500000), (0xC237D000, 0x7000), (0xC23A9000, 0x2000), (0xC23EE000, 0x1000), (0xC23FC000, 0x2000), (0xC1832000, 0x1000), (0xC2387000, 0x5000), (0xC181E000, 0x1000), (0xC18C7000, 0x1000), (0xC259E000, 0x4000), (0xC1820000, 0x1000), (0xC237C000, 0x1000),
                 (0xC2ABC000, 0x1000), (0xC18EB000, 0x1000), (0xC2A1C000, 0x1000), (0x10000000, 0x400000), (0xC181F000, 0x1000), (0xC181A000, 0x1000), (0xC1F8F000, 0x1000), (0x12100000, 0x100000)):
        u.mem_map(a, n)
    u.mem_write(LOAD, code)
    u.mem_write(0xC2ABC8B8, struct.pack('<6I', 1, 1, 0, 0, 0, 3)); u.mem_write(0xC18EB038, struct.pack('<I', 2)); u.mem_write(0xC2A1C7F0, struct.pack('<I', 0x100527A8))
    STUB = {'get': 0x10300001, 'text': 0x10300011, 'handler': 0x10300021}
    u.mem_write(API, struct.pack('<5I', 2, 20, STUB['get'], STUB['text'], STUB['handler']))
    t = [1_000_000]; st = dict(handler=0, text='', files={}, fd=None, cleans=0)
    def r(n): return u.reg_read((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[n])
    def ret(v): u.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    def hook(uc, addr, size, _):
        if addr == 0x10300010: st['text'] = bytes(u.mem_read(r(0), 41)).split(b'\0')[0].decode(); ret(1)
        elif addr == 0x10300020: st['handler'] = r(0); ret(1)
        elif addr == 0x12100100: ret(0x20004000)       # log 11: trk3d_get stub
        elif addr == 0x101D8300: u.mem_write(r(0), struct.pack('<Q', t[0])); ret(0)
        elif addr == 0x10211F4C: ret(POOL)
        elif addr == 0x10035AAC: st['cleans'] += 1; ret(1)
        elif addr == 0x102C8AFC: name = bytes(u.mem_read(r(0), 32)).split(b'\0')[0].decode(); st['files'][name] = bytearray(); st['fd'] = name; ret(3)
        elif addr == 0x102C8A6A: st['files'][st['fd']] += bytes(u.mem_read(r(1), r(2))); u.mem_write(r(3), struct.pack('<I', r(2))); ret(0)
        elif addr == 0x102C89EE: ret(0)
    for a in (0x12100100, 0x10300010, 0x10300020, 0x101D8300, 0x10211F4C, 0x10035AAC, 0x102C8AFC, 0x102C8A6A, 0x102C89EE):
        u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a + 1)
    RET = 0x10310000
    def call(fn, *args):
        for i, v in enumerate(args): u.reg_write((UC_ARM_REG_R0, UC_ARM_REG_R1)[i], v)
        u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, RET | 1)
        u.emu_start(fn | 1, RET, count=50_000_000)
        return u.reg_read(UC_ARM_REG_R0)
    assert call(LOAD, API, CTX) == 1 and st['handler'] and st['text'] == 'AF log ready', st
    reads = []
    u.hook_add(UC_HOOK_CODE, lambda *a: None)
    assert call(st['handler'], API, CTX) == 1; print('run 1:', st['text'])
    tick = struct.unpack('<I', u.mem_read(0xC1832D48, 4))[0]; assert tick & 1, hex(tick)
    # log 4: the tracking flag flips every 10 ticks, a message id is queued every 25
    for k in range(100):
        u.mem_write(0xC237F688, struct.pack('<I', (k // 10) & 1))
        u.mem_write(0xC23AA410, struct.pack('<I', 0x1803 + k // 25)); t[0] += 50_000; call(tick, 1)
    assert call(st['handler'], API, CTX) == 1; print('run 2:', st['text'])
    (name, data), = st['files'].items()
    h = struct.unpack_from('<40I', data, 0)
    if v12:
        assert data[:8] == b'AFLOG12\0' and h[2] == 0x6D8 and h[6] == 4 and h[9:11] == (0xC237C7B4, 0x468), h
        print('file', name, len(data), 'bytes, v12 windows - OK')
        return h[3]
    if v11:
        assert data[:8] == b'AFLOG11\0' and h[2] == 0x1D4 and h[6] == 9 and h[7:9] == (0x20004000, 0x80), h
        print('file', name, len(data), 'bytes, v11 windows (dynamic state base) - OK')
        return h[3]
    if v10:
        assert data[:8] == b'AFLOG10\0' and h[2] == 0x338 and h[6] == 4 and h[7:9] == (0xC259EF18, 0x300), h
        print('file', name, len(data), 'bytes, v10 windows - OK')
        return h[3]
    if v9:
        assert data[:7] == b'AFLOG9\0' and h[2] == 0x278 and h[4] == 100 and h[6] == 10, h
        assert h[7:9] == (0xC237D590, 0x40) and h[11:13] == (0xC237E2E8, 0xF0) and h[25:27] == (0xC25A1E50, 0x70), h
        assert h[3] == 10, h[3]
        print('file', name, len(data), 'bytes, v9 windows - OK')
        return h[3]
    if v8:
        assert data[:7] == b'AFLOG8\0' and h[4] == 100 and h[6] == 5, h
        assert h[7:9] == (0xC1820040, 0x80) and h[9:11] == (0xC237C300, 0x900), h
        assert h[3] == 10, h[3]
        print('file', name, len(data), 'bytes, v8 windows - OK')
        return h[3]
    if v7:
        assert data[:7] == b'AFLOG7\0' and h[2] == 0x330 and h[4] == 100 and h[6] == 7, h
        assert h[13:15] == (0xC237F578, 0x200), h
        assert h[3] == 10, h[3]
        print('file', name, len(data), 'bytes, v7 windows - OK')
        return h[3]
    if v6:
        assert data[:7] == b'AFLOG6\0' and h[2] == 0x1E0 and h[4] == 100 and h[6] == 11, h
        assert h[17:19] == (0xC23A9CA0, 0x20) and h[27:29] == (0xC25A1E50, 0x48), h
        assert h[3] == 10, h[3]
        print('file', name, len(data), 'bytes, v6 windows - OK')
        return h[3]
    if v5:
        assert data[:7] == b'AFLOG5\0' and h[2] == 0x1F8 and h[4] == 100 and h[6] == 12, h
        assert h[7:9] == (0xC237D590, 0x40) and h[21:23] == (0xC2387200, 0x90) and h[29:31] == (0xC181E4C0, 0x10), h
        assert h[3] == 10, h[3]                    # only the tracking flag changes: ticks 1, 11, ..., 91
        print('file', name, len(data), 'bytes, v5 windows - OK')
        return h[3]
    assert data[:7] == b'AFLOG4\0' and h[2] == 0x1D8 and h[3] == 12 and h[4] == 100 and h[5] == 0 and h[6] == 10, h
    assert h[7:9] == (0xC237D590, 0x20) and h[15:17] == (0xC23A9FE0, 0x60), h
    # changes at ticks 1, 11, 21, 26, 31, ...: the fifth entry is tick 31, flag 1, id 0x1804
    e = data[160 + 4 * (8 + 0x1D8):]
    t_ms, tk = struct.unpack_from('<2I', e, 0)
    flag = struct.unpack_from('<I', e, 8 + 0x20 + 0x20 + 0x8 + 0x8)[0]
    msg = struct.unpack_from('<I', e, 8 + 0x20 + 0x20 + 0x8 + 0x10 + 0x60 + 0x40 + 0x10)[0]
    assert tk == 31 and flag == 1 and msg == 0x1804, (tk, flag, hex(msg))
    assert struct.unpack('<I', u.mem_read(0xC1832D48, 4))[0] == tick and st['cleans'] == 1
    assert call(st['handler'], API, CTX) == 1 and st['text'].startswith('Logging'), st['text']
    print('file', name, len(data), 'bytes, 12 changes of 100 ticks; tick', tk, 'flag', flag, 'msg', hex(msg), '- OK')
    box = (30, 40, 20, 30, 40)       # the test's historic return value
    return box


if __name__ == '__main__':
    main(sys.argv[1], '--v5' in sys.argv, '--v6' in sys.argv, '--v7' in sys.argv, '--v8' in sys.argv, '--v9' in sys.argv)
