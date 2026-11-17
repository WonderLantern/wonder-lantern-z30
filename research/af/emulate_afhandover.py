"""Unicorn: the AF handover module (modules/af_handover, v7) on a stubbed camera.

Usage: emulate_afhandover.py AF_HANDOVER.bin [OUT.BIN]
The module arms the face_hook cell (0xC1832D40) and runs once per detection
update cb(result, xy, wh). Scenarios: tracking started with no subject, a
subject appears -> one OK after 3 updates with it; it stays -> no second OK;
tracking off 10 updates re-arms; tracking started with a subject in view ->
blocked while it stays, released after 10 updates without it, then a new
subject -> OK; a flickering detection still counts; not tracking -> no OK.
A press queues two copies of the newest ring entry through 0x102F0D14 (+
0x102F1844): source word kept, key words unchanged except key word 2 = OK down,
then OK up.
"""
import struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
from unicorn.arm_const import *


def main(path, out=None):
    code = open(path, 'rb').read()
    LOAD, API, CTX, STACK, POOL = 0x30000000, 0x20000000, 0x20000100, 0x20008000, 0xC4000000
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    for a, n in ((LOAD, 0x10000), (0x20000000, 0x10000), (POOL, 0x100000), (0xC237D000, 0x3000), (0xC1832000, 0x1000),
                 (0xC2ABC000, 0x1000), (0xC18EB000, 0x1000), (0xC2A1C000, 0x1000), (0x10000000, 0x600000),
                 (0xC2008000, 0x3000), (0xC1820000, 0x1000)):
        u.mem_map(a, n)
    u.mem_write(LOAD, code)
    u.mem_write(0xC2ABC8B8, struct.pack('<6I', 1, 1, 0, 0, 0, 3)); u.mem_write(0xC18EB038, struct.pack('<I', 2)); u.mem_write(0xC2A1C7F0, struct.pack('<I', 0x100527A8))
    u.mem_write(API, struct.pack('<5I', 2, 20, 0x10300001, 0x10300011, 0x10300021))
    st = dict(handler=0, text='', files={}, fd=None, keys=[], expect_held=(0x200, 0x10, 0, 0), states=[], posts=0)
    t = [1_000_000]
    def r(n): return u.reg_read((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[n])
    def ret(v): u.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    def hook(uc, addr, size, _):
        if addr == 0x10300010: st['text'] = bytes(u.mem_read(r(0), 41)).split(b'\0')[0].decode(); ret(1)
        elif addr == 0x10300020: st['handler'] = r(0); ret(1)
        elif addr == 0x101D8300: u.mem_write(r(0), struct.pack('<Q', t[0])); ret(0)
        elif addr == 0x10211F4C: ret(POOL)
        elif addr == 0x10035AAC: ret(1)
        elif addr == 0x102F199A: raise AssertionError('v6 must not use the stock synthesizer')
        elif addr == 0x102F0D14:
            w = struct.unpack('<99I', u.mem_read(r(0), 0x18C))
            assert w[0] == 0xC1818814 and w[98] == 0x5A5A5A5A, (hex(w[0]), hex(w[98]))   # source and tail copied
            for k in (0, 2, 3):
                h = st['expect_held'][k]
                assert w[1 + 5 * k:6 + 5 * k] == (h, h, 0, 0, 0), (k, w[1 + 5 * k:6 + 5 * k])
            h = st['expect_held'][1]
            down = (h | 1, h, 1, 1, 0); up = (h, h | 1, 1, 0, 1)
            assert w[6:11] in (down, up), w[6:11]
            st['states'].append('down' if w[6:11] == down else 'up')
            if w[6:11] == up:
                st['keys'].append(0x27E)
                # what the stock queue would hold: entry 3 gets key word 2 = OK (pressed); newest stays entry 4
                u.mem_write(0xC2008EA0 + 3 * 0x190 + 0x18, struct.pack('<4I', 1, 0, 0, 1)); u.mem_write(0xC182004C, b'\x05\x03\0\0')
            ret(1)
        elif addr == 0x102F1844: st['posts'] += 1; ret(1)
        elif addr == 0x103CE96E: raise AssertionError('v3 must not send GUI events')
        elif addr == 0x102C8AFC: name = bytes(u.mem_read(r(0), 32)).split(b'\0')[0].decode(); st['files'][name] = bytearray(); st['fd'] = name; ret(3)
        elif addr == 0x102C8A6A: st['files'][st['fd']] += bytes(u.mem_read(r(1), r(2))); u.mem_write(r(3), struct.pack('<I', r(2))); ret(0)
        elif addr == 0x102C89EE: ret(0)
    for a in (0x10300010, 0x10300020, 0x101D8300, 0x10211F4C, 0x10035AAC, 0x103CE96E, 0x102F199A, 0x102F0D14, 0x102F1844, 0x102C8AFC, 0x102C8A6A, 0x102C89EE):
        u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a + 1)
    RET = 0x10310000
    def call(fn, *args):
        for i, v in enumerate(args): u.reg_write((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[i], v)
        u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, RET | 1)
        u.emu_start(fn | 1, RET, count=50_000_000)
        return u.reg_read(UC_ARM_REG_R0)
    W = lambda a, v: u.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))
    # the stock ring as on the camera: next write slot 5, newest entry 4 holds 0x200 (+ OK and 0x10 in word 2)
    u.mem_write(0xC182004C, b'\x05\x05\0\0')
    u.mem_write(0xC2008EA0 + 4 * 0x190 + 4, struct.pack('<I', 0x200)); u.mem_write(0xC2008EA0 + 4 * 0x190 + 0x18, struct.pack('<I', 0x11))
    u.mem_write(0xC2008EA0 + 4 * 0x190, struct.pack('<I', 0xC1818814)); u.mem_write(0xC2008EA0 + 4 * 0x190 + 0x188, struct.pack('<I', 0x5A5A5A5A))
    assert call(LOAD, API, CTX) == 1 and st['text'] == 'AF handover ready'
    assert call(st['handler'], API, CTX) == 1 and st['text'] == 'Handover armed, Run=save', st['text']
    cb = struct.unpack('<I', u.mem_read(0xC1832D40, 4))[0]
    assert cb & 1, hex(cb)
    def state(tracking, subjects=0, fstate=1, box=(3416, 1580)):
        W(0xC237F688, int(tracking))
        W(0xC237D59C, box[0] << 16); W(0xC237D5A0, (box[0] + 552) << 16 | box[1])
        W(0xC237E3E4, subjects); W(0xC237E2C0, fstate if subjects else 4)
        u.mem_write(0xC237F624, struct.pack('<4i', 35, 18, 13, 14))
        t[0] += 60_000; call(cb, 0x20000400, 0x20000500, 0x20000504)
    for _ in range(10): state(True)
    for _ in range(2): state(True, 1)
    assert st['keys'] == [], '2 updates with a subject: not yet'
    state(True, 1)
    assert st['keys'] == [0x27E], ('third update with a subject: OK', st['keys'])
    for _ in range(30): state(True, 1, 0x101)
    assert st['keys'] == [0x27E], 'one press per tracking session'
    for _ in range(9): state(False, 1)
    for _ in range(5): state(True, 1)
    assert st['keys'] == [0x27E], 'tracking off too briefly to re-arm'
    for _ in range(10): state(False)
    for _ in range(20): state(True, 1)                  # tracking started with a subject in view
    for _ in range(9): state(True)
    state(True, 1); state(True, 1); state(True, 1)
    assert st['keys'] == [0x27E], 'blocked: the subject was there at the start'
    for _ in range(10): state(True)                     # gone long enough: released
    for _ in range(3): state(True, 2)                   # a pet / second subject later
    assert st['keys'] == [0x27E, 0x27E], ('released, new subject: OK', st['keys'])
    for _ in range(10): state(False)
    state(True)
    for _ in range(3): state(True, 1, 0x100); state(True)          # flickering detection
    assert st['keys'] == [0x27E, 0x27E, 0x27E], ('flickering subject counts', st['keys'])
    for _ in range(10): state(False)
    for _ in range(10): state(False, 1)
    assert len(st['keys']) == 3, 'not tracking: no press'
    assert call(st['handler'], API, CTX) == 1 and st['text'] == 'Saved, OK presses 3', st['text']
    (name, data), = st['files'].items()
    h = struct.unpack_from('<8I', data, 0)
    assert st['states'] == ['down', 'up'] * 3 and st['posts'] == 6, (st['states'], st['posts'])
    assert data[:8] == b'AFHAND7\0' and h[3] == 3 and h[5] == 40 and h[6] == 106 and h[7] == 3, h
    recs = [struct.unpack_from('<IBBBB2I6i', data, 32 + 40 * i) for i in range(h[2])]
    assert sum(r[4] for r in recs) == 3 and all(r[1] == (0x83 if r[4] else 1) for r in recs)   # press: 0x80 | down 1 | up 2
    assert any(r[3] == 0x81 for r in recs)                                    # 0x101 logged as 0x81
    tail = 32 + 40 * h[2]
    rcount = struct.unpack_from('<I', data, tail)[0]
    press_ms = struct.unpack_from('<8I', data, tail + 4)
    assert rcount >= 1 and press_ms[0] > 0 and press_ms[1] > press_ms[0], (rcount, press_ms)
    snaps = [struct.unpack_from('<2I64I', data, tail + 36 + 264 * i) for i in range(rcount)]
    assert snaps[0][2 + 4 * 4 + 1] == 0x200                                  # the preset newest entry
    assert any(x[2 + 3 * 4 + 2] == 1 for x in snaps), [x[:16] for x in snaps]   # entry 3 key word 2 after a press
    assert len(data) == tail + 36 + 264 * rcount + 16
    assert struct.unpack_from('<4I', data, tail + 36 + 264 * rcount) == (0x200, 0x10, 0, 0)
    if out: open(out, 'wb').write(bytes(data))
    print(name, len(data), 'bytes;', h[2], 'tracking ticks logged, OK pressed three times where expected - OK')
    return True


if __name__ == '__main__':
    main(*sys.argv[1:3])
