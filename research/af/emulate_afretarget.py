"""Unicorn: the AF retarget module (modules/af_retarget, v9) on a stubbed camera.

Usage: emulate_afretarget.py AF_RETARGET.bin [OUT.BIN]
The module arms the face_hook cell (0xC1832D40) and runs once per detection
update cb(result, xy, wh). Scenarios: tracking started with no subject, a
subject appears -> after 3 updates the tracker position (0xC23AA028/02C) is
overwritten with the face centre on 6 updates, no key press; tracking started
with a subject in view -> no write; not tracking -> no write; Run 2 saves the log.
v9: arming checks the hook sites (stock code -> refuses); the IMU callback
runs the same rule from the face box in RAM only when no detection update came
for 200 ms.
"""
import struct, sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
from unicorn.arm_const import *


def main(path, out=None):
    code = open(path, 'rb').read()
    LOAD, API, CTX, STACK, POOL, ARGS = 0x30000000, 0x20000000, 0x20000100, 0x20008000, 0xC4000000, 0x20009000
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    for a, n in ((LOAD, 0x10000), (0x20000000, 0x10000), (POOL, 0x100000), (0xC237D000, 0x3000), (0xC1832000, 0x1000),
                 (0xC23AA000, 0x1000), (0xC2ABC000, 0x1000), (0xC18EB000, 0x1000), (0xC2A1C000, 0x1000), (0x10000000, 0x600000)):
        u.mem_map(a, n)
    u.mem_write(LOAD, code)
    u.mem_write(0xC2ABC8B8, struct.pack('<6I', 1, 1, 0, 0, 0, 3)); u.mem_write(0xC18EB038, struct.pack('<I', 2)); u.mem_write(0xC2A1C7F0, struct.pack('<I', 0x100527A8))
    u.mem_write(API, struct.pack('<5I', 2, 20, 0x10300001, 0x10300011, 0x10300021))
    st = dict(handler=0, text='', files={}, fd=None)
    u.mem_write(0x101944B0, bytes.fromhex('70b5'))          # stock code: no hooks
    u.mem_write(0x1021C360, bytes.fromhex('fff7'))
    t = [1_000_000]
    def r(n): return u.reg_read((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[n])
    def ret(v): u.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    def hook(uc, addr, size, _):
        if addr == 0x10300010: st['text'] = bytes(u.mem_read(r(0), 41)).split(b'\0')[0].decode(); ret(1)
        elif addr == 0x10300020: st['handler'] = r(0); ret(1)
        elif addr == 0x101D8300: u.mem_write(r(0), struct.pack('<Q', t[0])); ret(0)
        elif addr == 0x10211F4C: ret(POOL)
        elif addr == 0x10035AAC: ret(1)
        elif addr in (0x102F0D14, 0x102F1844, 0x102F199A, 0x103CE96E): raise AssertionError('method B must not press keys')
        elif addr == 0x102C8AFC: name = bytes(u.mem_read(r(0), 32)).split(b'\0')[0].decode(); st['files'][name] = bytearray(); st['fd'] = name; ret(3)
        elif addr == 0x102C8A6A: st['files'][st['fd']] += bytes(u.mem_read(r(1), r(2))); u.mem_write(r(3), struct.pack('<I', r(2))); ret(0)
        elif addr == 0x102C89EE: ret(0)
    for a in (0x10300010, 0x10300020, 0x101D8300, 0x10211F4C, 0x10035AAC, 0x102F0D14, 0x102F1844, 0x102F199A, 0x103CE96E, 0x102C8AFC, 0x102C8A6A, 0x102C89EE):
        u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a + 1)
    RET = 0x10310000
    def call(fn, *args):
        for i, v in enumerate(args): u.reg_write((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[i], v)
        u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, RET | 1)
        u.emu_start(fn | 1, RET, count=50_000_000)
        return u.reg_read(UC_ARM_REG_R0)
    W = lambda a, v: u.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))
    R = lambda a: struct.unpack('<I', u.mem_read(a, 4))[0]
    assert call(LOAD, API, CTX) == 1 and st['text'] == 'AF retarget 9 ready'
    assert call(st['handler'], API, CTX) == 1 and st['text'] == 'No hooks: flash retarget EG', st['text']
    assert R(0xC1832D40) == 0 and R(0xC1832D48) == 0
    u.mem_write(0x101944B0, bytes.fromhex('dff8'))          # patched sites
    u.mem_write(0x1021C360, bytes.fromhex('00f0'))
    assert call(st['handler'], API, CTX) == 1 and st['text'] == 'Retarget armed D+I, Run=save', st['text']
    cb, imu = R(0xC1832D40), R(0xC1832D48)
    assert cb & 1 and imu & 1, (hex(cb), hex(imu))
    TRACK_POS = 0x00370055                      # grid (85, 55): the centre start
    FACE = (35, 21, 13, 14)                     # x, y, w, h cells
    want = (((2 * 21 + 14) * 1041) // 1000 + 8) << 16 | ((2 * 35 + 13) * 1350) // 1000
    def upd(tracking, subjects=0, face=FACE):
        W(0xC237F688, int(tracking)); W(0xC237E3E4, subjects); W(0xC237E2C0, 1 if subjects else 4)
        if tracking and R(0xC23AA028) == 0: W(0xC23AA028, TRACK_POS); W(0xC23AA02C, TRACK_POS)
        if not tracking: W(0xC23AA028, 0); W(0xC23AA02C, 0)
        u.mem_write(ARGS, struct.pack('<4h', face[0], face[1], face[2], face[3]))
        t[0] += 60_000
        call(cb, 0xC237E2E8, ARGS, ARGS + 4)
        pos = R(0xC23AA028)
        if tracking and pos != 0: W(0xC23AA028, TRACK_POS); W(0xC23AA02C, TRACK_POS)   # the tracker writes back next frame
        return pos
    writes = []
    # session 1: no subject at start, one appears
    for _ in range(5): assert upd(True) == TRACK_POS
    for i in range(12):
        p = upd(True, 1)
        if p != TRACK_POS: assert p == want and R(0xC23AA02C) in (want, TRACK_POS), (hex(p), hex(want)); writes.append(i)
    assert writes == [2, 3, 4, 5, 6, 7], writes          # fires on the 3rd update with it, 6 writes, once
    # tracking off, re-arm; session 2 starts with the subject in view: blocked
    for _ in range(12): assert upd(False) == 0
    for _ in range(12): assert upd(True, 1) == TRACK_POS
    # not tracking with subjects: nothing
    for _ in range(12): assert upd(False, 2) == 0
    # an IMU tick right after a detection update does nothing
    t[0] += 50_000; call(imu, 0)
    # IMU path only (no detection updates): session 3, face box from RAM
    def imu_upd(tracking, subjects=0):
        W(0xC237F688, int(tracking)); W(0xC237E3E4, subjects); W(0xC237E2C0, 1 if subjects else 4)
        if tracking and R(0xC23AA028) == 0: W(0xC23AA028, TRACK_POS); W(0xC23AA02C, TRACK_POS)
        if not tracking: W(0xC23AA028, 0); W(0xC23AA02C, 0)
        u.mem_write(0xC237F624, struct.pack('<4i', *FACE))
        t[0] += 250_000
        call(imu, 0)
        pos = R(0xC23AA028)
        if tracking and pos != 0: W(0xC23AA028, TRACK_POS); W(0xC23AA02C, TRACK_POS)
        return pos
    for _ in range(12): imu_upd(False)
    imu_writes = [i for i in range(10) if (imu_upd(True, 0 if i < 2 else 1)) == want]
    assert imu_writes == [4, 5, 6, 7, 8, 9], imu_writes
    assert call(st['handler'], API, CTX) == 1 and st['text'] == 'Saved D53 I23 T39 R2', st['text']
    assert R(0xC1832D40) == 0 and R(0xC1832D48) == 0      # disarmed
    (name, data), = st['files'].items()
    assert name.startswith('A:\\Z30\\RT') and data[:8] == b'AFRTGT9\0', (name, data[:8])
    count, fires, ticks, rb, _, _, imu_ticks, hooks = struct.unpack_from('<8I', data, 8)
    assert fires == 2 and rb == 104 and hooks == 3 and imu_ticks == 23 and len(data) == 40 + count * 104 and count == 5 + 12 + 12 + 10, (count, fires, ticks)
    if out: open(out, 'wb').write(data)
    print('af_retarget OK: 2 retargets (detection, IMU), 6 writes each of %08x, %d records' % (want, count))


if __name__ == '__main__':
    main(*sys.argv[1:])
