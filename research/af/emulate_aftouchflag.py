"""Unicorn: the AF touch-flag module (modules/af_touchflag) on a stubbed camera.

Usage: emulate_aftouchflag.py AF_TOUCHFLAG.bin [OUT.BIN]
Three tracking sessions: the flag 0xC18C7240 is set 2 IMU ticks after each start
(once), not when the stock code already set it (a touch start), and never while
not tracking. Nothing else in the camera is written.
"""
import struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import *

FLAG, TRK = 0xC18C7240, 0xC237F688


def main(path, out=None):
    code = open(path, 'rb').read()
    LOAD, API, CTX, STACK, POOL = 0x30000000, 0x20000000, 0x20000100, 0x20008000, 0xC4000000
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    for a, n in ((LOAD, 0x10000), (0x20000000, 0x10000), (POOL, 0x200000), (0xC237D000, 0x3000), (0xC1832000, 0x1000),
                 (0xC18C7000, 0x1000), (0xC23A9000, 0x2000), (0xC2ABC000, 0x1000), (0xC18EB000, 0x1000), (0xC2A1C000, 0x1000), (0x10000000, 0x400000)):
        u.mem_map(a, n)
    u.mem_write(LOAD, code)
    u.mem_write(0xC2ABC8B8, struct.pack('<6I', 1, 1, 0, 0, 0, 3)); u.mem_write(0xC18EB038, struct.pack('<I', 2)); u.mem_write(0xC2A1C7F0, struct.pack('<I', 0x100527A8))
    u.mem_write(API, struct.pack('<5I', 2, 20, 0x10300001, 0x10300011, 0x10300021))
    u.mem_write(0x1021C360, bytes.fromhex('00f0'))
    st = dict(handler=0, text='', files={}, fd=None, writes=[])
    def r(n): return u.reg_read((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[n])
    def ret(v): u.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    t = [1_000_000]
    def hook(uc, addr, size, _):
        if addr == 0x10300010: st['text'] = bytes(u.mem_read(r(0), 41)).split(b'\0')[0].decode(); ret(1)
        elif addr == 0x10300020: st['handler'] = r(0); ret(1)
        elif addr == 0x101D8300: u.mem_write(r(0), struct.pack('<Q', t[0])); ret(0)
        elif addr == 0x10211F4C: ret(POOL)
        elif addr == 0x10035AAC: ret(1)
        elif addr == 0x102C8AFC: name = bytes(u.mem_read(r(0), 32)).split(b'\0')[0].decode(); st['files'][name] = bytearray(); st['fd'] = name; ret(3)
        elif addr == 0x102C8A6A: st['files'][st['fd']] += bytes(u.mem_read(r(1), r(2))); u.mem_write(r(3), struct.pack('<I', r(2))); ret(0)
        elif addr == 0x102C89EE: ret(0)
    for a in (0x10300010, 0x10300020, 0x101D8300, 0x10211F4C, 0x10035AAC, 0x102C8AFC, 0x102C8A6A, 0x102C89EE):
        u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a + 1)
    def wr(uc, access, addr, size, value, _):
        if 0xC0000000 <= addr < 0xC4000000 and addr not in (0xC1832D48,): st['writes'].append((hex(addr), value))
    u.hook_add(UC_HOOK_MEM_WRITE, wr)
    RET = 0x10310000
    def call(fn, *args):
        for i, v in enumerate(args): u.reg_write((UC_ARM_REG_R0, UC_ARM_REG_R1)[i], v)
        u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, RET | 1)
        u.emu_start(fn | 1, RET, count=50_000_000)
        return u.reg_read(UC_ARM_REG_R0)
    W = lambda a, v: u.mem_write(a, struct.pack('<I', v))
    R = lambda a: struct.unpack('<I', u.mem_read(a, 4))[0]
    assert call(LOAD, API, CTX) == 1 and st['text'] == 'AF touch flag ready'
    assert call(st['handler'], API, CTX) == 1 and st['text'] == 'Touch flag armed, Run=save', st['text']
    tick = R(0xC1832D48)
    seen = []
    for session, stock_touch in enumerate((False, True, False)):
        for i in range(20):
            tracking = 5 <= i < 15
            W(TRK, int(tracking))
            if tracking and i == 5 and stock_touch: W(FLAG, 1)
            if not tracking: W(FLAG, 0)
            st['writes'].clear()
            t[0] += 50_000; call(tick, 0)
            for a, v in st['writes']:
                assert a == hex(FLAG) and v == 1, (a, v)
                seen.append((session, i))
    assert seen == [(0, 7), (2, 7)], seen       # 2 ticks after the start, not when touch already set it
    assert call(st['handler'], API, CTX) == 1 and st['text'] == 'Saved, flag set 2x', st['text']
    (name, data), = st['files'].items()
    assert name.startswith('A:\\Z30\\TF') and data[:8] == b'AFTFLG1\0', name
    count, sets = struct.unpack_from('<2I', data, 8)
    assert count == 30 and sets == 2, (count, sets)
    if out: open(out, 'wb').write(bytes(data))
    return sets


if __name__ == '__main__':
    main(*sys.argv[1:3])
