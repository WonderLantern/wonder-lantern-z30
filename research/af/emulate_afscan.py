"""Unicorn: the AF scan module (modules/af_scan) on a stubbed camera.

Usage: emulate_afscan.py AF_SCAN.bin [OUT.BIN]
One word changes only while 0xC237F688 (tracking) is 1, another changes all the
time, a third never: after the save, the counts must tell them apart and the
tracking min / max must hold the tracked word's range.
box=True: the module built with -DSCAN_BOX; the key state is 0xC237F684 = 1
with 0xC237F688 = 0 (box shown, not tracking), for three sweeps.
"""
import struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
from unicorn.arm_const import *

BASE, BYTES, CHUNK = 0xC2300000, 0x100000, 0x10000
NCH, W = BYTES // CHUNK, BYTES // 4
HDR = 32 + 8 * NCH


def main(path, out=None, box=False):
    code = open(path, 'rb').read()
    LOAD, API, CTX, STACK, POOL = 0x30000000, 0x20000000, 0x20000100, 0x20008000, 0xC4000000
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    for a, n in ((LOAD, 0x10000), (0x20000000, 0x10000), (POOL, 0x500000), (BASE, BYTES), (0xC1832000, 0x1000),
                 (0xC2ABC000, 0x1000), (0xC18EB000, 0x1000), (0xC2A1C000, 0x1000), (0x10000000, 0x400000)):
        u.mem_map(a, n)
    u.mem_write(LOAD, code)
    u.mem_write(0xC2ABC8B8, struct.pack('<6I', 1, 1, 0, 0, 0, 3)); u.mem_write(0xC18EB038, struct.pack('<I', 2)); u.mem_write(0xC2A1C7F0, struct.pack('<I', 0x100527A8))
    u.mem_write(API, struct.pack('<5I', 2, 20, 0x10300001, 0x10300011, 0x10300021))
    st = dict(handler=0, text='', files={}, fd=None)
    def r(n): return u.reg_read((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[n])
    def ret(v): u.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    t = [1_000_000]
    def hook(uc, addr, size, _):
        if addr == 0x10300010: st['text'] = bytes(u.mem_read(r(0), 41)).split(b'\0')[0].decode(); ret(1)
        elif addr == 0x10300020: st['handler'] = r(0); ret(1)
        elif addr == 0x101D8300: u.mem_write(r(0), struct.pack('<Q', t[0])); ret(0)
        elif addr == 0x10211F4C: assert r(0) <= 0x500000, hex(r(0)); ret(POOL)
        elif addr == 0x10035AAC: ret(1)
        elif addr == 0x102C8AFC: name = bytes(u.mem_read(r(0), 32)).split(b'\0')[0].decode(); st['files'][name] = bytearray(); st['fd'] = name; ret(3)
        elif addr == 0x102C8A6A: st['files'][st['fd']] += bytes(u.mem_read(r(1), r(2))); u.mem_write(r(3), struct.pack('<I', r(2))); ret(0)
        elif addr == 0x102C89EE: ret(0)
    for a in (0x10300010, 0x10300020, 0x101D8300, 0x10211F4C, 0x10035AAC, 0x102C8AFC, 0x102C8A6A, 0x102C89EE):
        u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a + 1)
    RET = 0x10310000
    def call(fn, *args):
        for i, v in enumerate(args): u.reg_write((UC_ARM_REG_R0, UC_ARM_REG_R1)[i], v)
        u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, RET | 1)
        u.emu_start(fn | 1, RET, count=200_000_000)
        return u.reg_read(UC_ARM_REG_R0)
    assert call(LOAD, API, CTX) == 1 and st['text'] == ('AF box scan ready' if box else 'AF scan ready'), st['text']
    assert call(st['handler'], API, CTX) == 1 and st['text'].startswith('Scanning'), st['text']
    tick = struct.unpack('<I', u.mem_read(0xC1832D48, 4))[0]
    BOX, NOISE, TRK, SHOWN = 0xC2345670, 0xC2311110, 0xC237F688, 0xC237F684
    KEY = (2, 3, 4) if box else (2, 3)
    SWEEPS = 10 if box else 6                      # the box rule needs more idle sweeps than key ones
    for k in range(NCH * SWEEPS):                  # 1 snapshot, then idle, key, idle...
        sweep = k // NCH
        tracking = sweep in KEY
        if box:                                    # shown, not tracking; idle sweeps track (must not count)
            u.mem_write(SHOWN, struct.pack('<I', int(tracking))); u.mem_write(TRK, struct.pack('<I', int(not tracking)))
        else:
            u.mem_write(TRK, struct.pack('<I', int(tracking)))
        u.mem_write(NOISE, struct.pack('<I', k))
        if tracking: u.mem_write(BOX, struct.pack('<i', 100 + 7 * k))
        t[0] += 50_000; call(tick, 1)
    assert call(st['handler'], API, CTX) == 1 and st['text'].startswith('Saved'), st['text']
    (name, data), = st['files'].items()
    h = struct.unpack_from('<8I', data, 0)
    assert data[:8] == b'AFSCAN1\0' and h[2:5] == (BASE, BYTES, CHUNK) and h[5] == NCH * SWEEPS and h[6] == NCH * len(KEY) and h[7] == int(box), h
    ct = struct.unpack_from(f'<{W}H', data, HDR); ci = struct.unpack_from(f'<{W}H', data, HDR + 2 * W)
    mn = struct.unpack_from(f'<{W}i', data, HDR + 4 * W); mx = struct.unpack_from(f'<{W}i', data, HDR + 8 * W)
    b, n = (BOX - BASE) // 4, (NOISE - BASE) // 4
    # the last write of a tracking sweep after its chunk was read shows up in the next (idle) sweep
    assert ct[b] == len(KEY) and ci[b] <= 1, (ct[b], ci[b])
    assert ct[n] == len(KEY) and ci[n] == SWEEPS - 1 - len(KEY), (ct[n], ci[n])          # changes in every counted sweep
    if not box:
        assert ct[n] < 2 * ci[n] and ct[b] >= 2 * ci[b]             # the parser's tracking-only rule
    assert mn[b] == 100 + 7 * (KEY[0] * NCH + (BOX - BASE) // CHUNK) and mx[b] == 100 + 7 * (KEY[-1] * NCH + (BOX - BASE) // CHUNK), (mn[b], mx[b])
    print(name, len(data), 'bytes; tracking-only words:', [hex(BASE + 4 * i) for i in range(W) if ct[i] >= 2 * ci[i] and ct[i]], '- OK')
    if out: open(out, 'wb').write(bytes(data))
    return True


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != '--box' else None, '--box' in sys.argv)
