"""Unicorn: the AF snapshot module (research/af/modules/af_snap) on a stubbed camera.

Usage: emulate_afsnap.py AF_SNAP.bin [OUT.BIN]
Four tracking sessions; a "method" word is 1 in the first two (touch) and 0 in
the last two (button), a noise word changes every tick. Each session must be
copied 6 ticks after 0xC237F688 rises, and the parser must name the method word.
--shown: the -DSNAP_SHOWN build (trigger 0xC237F684).
--focus: the -DSNAP_FOCUS build: the focus result 0xC25A1E70 runs 2,0,1,0 twice
(20 ticks each) after a 3-tick blip that must be dropped; a "style" word in the
display info follows the result.
"""
import struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
from unicorn.arm_const import *

METHOD, NOISE, TRK = 0xC23812A0, 0xC2390040, 0xC237F688


def main(path, out=None, shown=False, focus=False):
    code = open(path, 'rb').read()
    LOAD, API, CTX, STACK, POOL = 0x30000000, 0x20000000, 0x20000100, 0x20008000, 0xC4000000
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    for a, n in ((LOAD, 0x10000), (0x20000000, 0x10000), (POOL, 0x500000), (0xC2360000, 0x60000), (0xC1810000, 0x30000), (0xC18C0000, 0x10000),
                 (0xC2ABC000, 0x1000), (0xC2000000, 0x40000), (0xC25A0000, 0x10000), (0xC18EB000, 0x1000), (0xC2A1C000, 0x1000), (0x10000000, 0x400000)):
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
        elif addr == 0x10211F4C: assert r(0) <= 0x480000, hex(r(0)); ret(POOL)
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
    W = lambda a, v: u.mem_write(a, struct.pack('<I', v))
    assert call(LOAD, API, CTX) == 1 and st['text'] == ('AF focus snapshot ready' if focus else 'AF mode snapshot ready' if shown else 'AF snapshot ready'), st['text']
    u.mem_write(0x1021C360, bytes.fromhex('fff7'))
    assert call(st['handler'], API, CTX) == 1 and st['text'] == 'No IMU hook: flash retarget EG', st['text']
    u.mem_write(0x1021C360, bytes.fromhex('00f0'))
    assert call(st['handler'], API, CTX) == 1 and st['text'] == 'Snapshots armed, Run=save', st['text']
    tick = struct.unpack('<I', u.mem_read(0xC1832D48, 4))[0]
    k = 0
    if focus:
        RESULT, STYLE = 0xC25A1E70, 0xC2016E40
        for value, n in [(1, 3), (0, 10)] + [(v, 20) for v in (2, 0, 1, 0, 2, 0, 1, 0)]:
            for i in range(n):
                W(RESULT, value); W(STYLE, 0x10 + value); W(NOISE, k); k += 1
                t[0] += 50_000; call(tick, 0)
        assert call(st['handler'], API, CTX) == 1 and st['text'] == 'Saved, snapshots 8 of 10', st['text']
        (name, data), = st['files'].items()
        if out: open(out, 'wb').write(bytes(data))
        return bytes(data)
    if shown:
        SHOWN_FLAG, MODE = 0xC237F684, 0xC18C723C
        for cycle in range(8):                    # shown, hidden, shown, ... each 20 ticks
            on = cycle % 2 == 0
            for i in range(20):
                W(SHOWN_FLAG, int(on)); W(MODE, int(on)); W(NOISE, k); k += 1
                t[0] += 50_000; call(tick, 0)
        W(SHOWN_FLAG, 0)
        assert call(st['handler'], API, CTX) == 1 and st['text'] == 'Saved, snapshots 8 of 8', st['text']
        (name, data), = st['files'].items()
        if out: open(out, 'wb').write(bytes(data))
        return bytes(data)
    for session in range(4):
        for i in range(30):                       # 10 idle, 20 tracking ticks (copy: 6 wait + 8 chunks)
            W(TRK, int(i >= 10)); W(METHOD, int(session < 2) if i >= 10 else 7); W(NOISE, k); k += 1
            t[0] += 50_000; call(tick, 0)
    W(TRK, 0)
    assert call(st['handler'], API, CTX) == 1 and st['text'] == 'Saved, snapshots 4 of 4', st['text']
    assert struct.unpack('<I', u.mem_read(0xC1832D48, 4))[0] == 0
    (name, data), = st['files'].items()
    assert name.startswith('A:\\Z30\\SN') and data[:8] == b'AFSNAP1\0', name
    if out: open(out, 'wb').write(bytes(data))
    return bytes(data)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else None,
         '--shown' in sys.argv, '--focus' in sys.argv)
