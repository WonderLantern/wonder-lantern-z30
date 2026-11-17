"""Unicorn check of the built-in gyro e-VR (gyro-evr feature) on a real built EG image.

Usage: emulate_builtin.py EG.bin CHOICE_FN
CHOICE_FN is the gyro_choice routine of the Gyro e-VR menu page (report
features.gyro_evr_control.choice); it is stubbed here because the real one
restores WL-GYRO.CFG through the stock file system.

Run A (menu Standard, e-VR on):
 1. Escape hatch: IMU-task hook alone (photo mode, no e-VR frames) never
    starts it: no I2C, gyro registers untouched, cell 0, stock result kept.
 2. A failed stock accelerometer read never starts it.
 3. e-VR path-1 hook requests; the next good IMU tick starts it: code copy,
    shared-cache sync, arm, menu choice applied: gyro 100 Hz, IMU period 10 ms;
    frames corrected, zoom 0.93, every corner >= 32 px inside; path 2 untouched.
 4. Menu OFF: no more FIFO reads, gyro registers back to stock, period 50 ms,
    frames untouched. Menu Enhanced: stabilizes again, zoom 0.916.
 5. (Standard (RS) was removed from the menu on 2026-11-14: rows are now
    0 Standard, 1 Enhanced, 2 Native crop, 3 OFF.) Standard at 50 mm and 200 mm,
    steady yaw + pitch: zoom 0.93, no shear, the gyro stays at 100 Hz.
 6. No e-VR frames (out of movie live view): idle, gyro registers back to stock.
Run B (fresh boot):
 7. Menu OFF at boot: the request is dropped, nothing starts, no I2C.
Nothing touches the file system except A:\\WL-GYROZ.CFG (v51): read once when it
arms, written when it goes idle; run B starts from the bias run A saved.
"""
import struct, sys
sys.path.insert(0, __file__.rsplit('/', 3)[0] + '/src')
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
from unicorn.arm_const import *
from z30_extensions.features import evr_hook, imu_hook, evr_builtin
from z30_extensions.features.gyro_evr_payload import CODE

BASE = 0x10020000
STACK, POOL = 0x2000F000, 0xC4000000
INP, META, RES_CTX = 0x20001000, 0x20010000, 0x20003000
SAFETY = 32
CORNERS = [0x28, 0x30, 0x38, 0x40, 0x58, 0x60, 0x68, 0x70]
STOCK_REGS = {0x4E: 0x23, 0x4F: 0x06, 0x50: 0x46, 0x16: 0x00, 0x5F: 0x00}


class Imu:
    def __init__(s):
        s.r = bytearray(128); s.r[0x75] = 0x42; s.r[0x4C] = 0x22
        for k, v in STOCK_REGS.items(): s.r[k] = v
        s.fifo = bytearray(); s.rate = (0, 0, 0); s.bias = (-262, 1638, -786); s.flushes = 0
    def tick(s, ms):
        if not (s.r[0x4E] & 0x0C and s.r[0x5F] & 2 and s.r[0x16] == 0x40): return
        step = {0x07: 5, 0x08: 10}.get(s.r[0x4F] & 0x0F, 2)
        g = struct.pack('<3h', *[a + b for a, b in zip(s.rate, s.bias)])
        for _ in range(ms // step):
            s.fifo += bytes([0x20]) + g + b'\x19'
        del s.fifo[:max(0, len(s.fifo) - 2048)]
    def read(s, reg, n):
        if reg == 0x2E: return struct.pack('>H', len(s.fifo))
        if reg == 0x30:
            out = bytes(s.fifo[:n]) + b'\xff' * max(0, n - len(s.fifo)); del s.fifo[:n]; return out
        return bytes(s.r[reg:reg + n])
    def write(s, reg, v):
        if reg == 0x4B and v & 2: s.fifo.clear(); s.flushes += 1
        else: s.r[reg] = v


class Camera:
    def __init__(s, img, choice_fn, card=None):
        u = s.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(BASE, (len(img) + 0xFFFF) & ~0xFFFF); u.mem_write(BASE, img)
        for a, n in ((0xC1807000, 0x1000), (0xC1832000, 0x1000), (0xC2A01000, 0x1000), (0xC2ABC000, 0x1000),
                     (0xC18EB000, 0x1000), (0xC2A1C000, 0x1000), (0xC8759000, 0x1000), (0xC259E000, 0x1000),
                     (0x20000000, 0x20000), (POOL, 0x800000)):
            u.mem_map(a, n)
        u.mem_write(0xC1807DDC + 0x2C, struct.pack('<I', 0x30001))
        u.mem_write(0xC1807DDC + 4, struct.pack('<I', 0xC1807100)); u.mem_write(0xC1807100 + 0x1A8, struct.pack('<I', 0xC8759400))
        u.mem_write(0xC8759420, struct.pack('<I', 50))
        u.mem_write(0xC2ABC8B8, struct.pack('<6I', 1, 1, 0, 0, 0, 3)); u.mem_write(0xC18EB038, struct.pack('<I', 2))
        u.mem_write(0xC2A1C7F0, struct.pack('<I', 0x100527A8))
        s.imu = Imu(); s.t = [5_000_000]; s.st = dict(opened=0, xfers=0, allocs=[], cleans=0, choice_calls=0)
        s.choice = [0]; s.fs = 0; s.bad = []
        # a card holding only what the module writes; only WL-GYROZ.CFG may be used
        s.card = card if card is not None else {}; s.paths = {}; s.gz = dict(reads=[], writes=0, writing=None)
        heap = [POOL]
        def r(n): return u.reg_read((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)[n])
        def ret(uc, v): uc.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        st, imu, t = s.st, s.imu, s.t
        def stub(uc, addr, size, _):
            if addr == 0x101D8300: uc.mem_write(r(0), struct.pack('<Q', t[0])); t[0] += 1; ret(uc, 0)
            elif addr == 0x10211F4C:
                n = (r(0) + 63) & ~63
                if heap[0] + n > POOL + 0x800000: ret(uc, 0); return
                a = heap[0]; heap[0] += n; st['allocs'].append((a, r(0))); ret(uc, a)
            elif addr == 0x101DCFE4: assert not st['opened']; st['opened'] = 1; ret(uc, 0)
            elif addr == 0x101DD026: st['opened'] = 0; ret(uc, 0)
            elif addr == 0x101DD060:
                st['xfers'] += 1; assert st['opened']
                op, _, a, tx, rx, txn, rxn = struct.unpack('<BBHIIII', uc.mem_read(r(1), 20))
                d = bytes(uc.mem_read(tx, txn))
                if rxn: uc.mem_write(rx, imu.read(d[0], rxn))
                else: imu.write(d[0], d[1])
                t[0] += 200; ret(uc, 0)
            elif addr == 0x1021BC38: ret(uc, st.get('stock', 1))
            elif addr == 0x10035AAC: st['cleans'] += 1; uc.reg_write(UC_ARM_REG_R0, 1); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            elif addr == 0x100BD46A: ret(uc, 0)
            elif addr == choice_fn & ~1: st['choice_calls'] += 1; ret(uc, s.choice[0])
            elif addr == 0x1033829E:                          # settings file init(st, path)
                s.paths[r(0)] = bytes(uc.mem_read(r(1), 64)).split(b'\0')[0].decode(); ret(uc, 0)
            elif addr == 0x103382A2:                          # open(st, mode, 0)
                path = s.paths.get(r(0))
                if path != 'A:\\WL-GYROZ.CFG': s.fs += 1; ret(uc, 1); return
                if r(1) == 0:
                    s.gz['reads'].append(path)
                    if path not in s.card: ret(uc, 1); return
                    uc.mem_write(r(0) + 8, struct.pack('<I', 7)); ret(uc, 0x04370080)
                elif r(1) == 0x301:
                    s.gz['writing'] = path; uc.mem_write(r(0) + 8, struct.pack('<I', 8)); ret(uc, 0x04370080)
                else: s.fs += 1; ret(uc, 1)
            elif addr == 0x10338414:                          # read(st, buf, len, &n)
                data = s.card[s.paths[r(0)]][:r(2)]; uc.mem_write(r(1), data)
                uc.mem_write(r(3), struct.pack('<I', len(data))); ret(uc, 0x04370080)
            elif addr == 0x1033832E: ret(uc, 0x04370080)       # close
            elif addr == 0x102C8A6A and r(0) == 8 and s.gz['writing']:   # raw write on the opened bias file
                s.card[s.gz['writing']] = bytes(uc.mem_read(r(1), r(2))); s.gz['writes'] += 1
                uc.mem_write(r(3), struct.pack('<I', r(2))); ret(uc, 0)
            elif addr in (0x102C8AFC, 0x102C8A6A, 0x102C89EE, 0x102C88D0): s.fs += 1; ret(uc, -1)
        for a in (0x101D8300, 0x10211F4C, 0x101DCFE4, 0x101DD026, 0x101DD060, 0x1021BC38, 0x10035AAC, 0x100BD46A,
                  choice_fn & ~1, 0x102C8AFC, 0x102C8A6A, 0x102C89EE, 0x102C88D0,
                  0x1033829E, 0x103382A2, 0x10338414, 0x1033832E):
            u.hook_add(UC_HOOK_CODE, stub, begin=a, end=a + 1)
        s.stops = []
        def stop(uc, addr, size, _): s.stops.append(addr); uc.emu_stop()
        for a in (imu_hook.SUCCESS, imu_hook.FAILURE): u.hook_add(UC_HOOK_CODE, stop, begin=a, end=a + 1)

    def cell(s, a): return struct.unpack('<I', s.u.mem_read(a, 4))[0]
    def per(s): return s.cell(0xC8759420)

    def imu_period(s, res=1, ms=50):
        u = s.u
        s.imu.tick(ms); s.t[0] += ms * 1000; s.st['stock'] = res
        u.reg_write(UC_ARM_REG_R0, 0x20004000); u.reg_write(UC_ARM_REG_R4, 0x20004444)
        u.reg_write(UC_ARM_REG_SP, STACK - 0x1000); s.stops.clear()
        u.emu_start(imu_hook.SITE | 1, 0, count=40_000_000)
        assert s.stops == [imu_hook.SUCCESS if res == 1 else imu_hook.FAILURE], (s.stops, res)
        assert u.reg_read(UC_ARM_REG_R0) == res, 'stock result must be kept'

    @staticmethod
    def body_for(w, h):
        quad = [(60, 64), (w - 160, 66), (62, h - 64), (w - 162, h - 66)]
        body = bytearray(0x78)
        for i, off in enumerate(CORNERS):
            struct.pack_into('<2i', body, off, (quad + quad)[i][0] * 256, (quad + quad)[i][1] * 256)
        return bytes(body)

    def vrmx(s, n, w=1866, h=1058, focal=50):
        u = s.u
        sp = STACK - 0x400; body = s.body_for(w, h); res = sp + 0x84; u.mem_write(res, body)
        u.mem_write(INP, struct.pack('<I', 1)); u.mem_write(INP + 0x34, struct.pack('<2i', w, h)); u.mem_write(INP + 0x64, struct.pack('<I', n))
        meta = bytearray(0x2C00); struct.pack_into('<H', meta, 0x258A, focal)
        u.mem_write(0xC2A01D08 + 4, struct.pack('<I', RES_CTX)); u.mem_write(RES_CTX + 0x144, struct.pack('<I', META)); u.mem_write(META, bytes(meta))
        u.reg_write(UC_ARM_REG_R4, INP); u.reg_write(UC_ARM_REG_R5, 0xC2A01D08); u.reg_write(UC_ARM_REG_SP, sp)
        u.emu_start(evr_hook.SITE | 1, evr_hook.RETURN, count=5_000_000)
        assert u.reg_read(UC_ARM_REG_PC) == evr_hook.RETURN
        out = bytes(u.mem_read(res, 0x78))
        for off in CORNERS:
            x, y = struct.unpack_from('<2i', out, off)
            if not (SAFETY * 256 <= x <= (w - SAFETY) * 256 and SAFETY * 256 <= y <= (h - SAFETY) * 256): s.bad.append((n, off))
        return body, out

    def vrmx2(s, n):
        u = s.u
        sp = STACK - 0x400; body = s.body_for(1866, 1058); u.mem_write(sp, body)
        u.mem_write(INP, struct.pack('<I', 2)); u.mem_write(INP + 0x64, struct.pack('<I', n))
        u.mem_write(0xC2A01D08 + 8, struct.pack('<I', RES_CTX)); u.mem_write(RES_CTX + 0x144, struct.pack('<I', META))
        u.reg_write(UC_ARM_REG_R5, INP); u.reg_write(UC_ARM_REG_SP, sp)
        u.emu_start(evr_hook.SITE2 | 1, evr_hook.RETURN2, count=5_000_000)
        return body, bytes(u.mem_read(sp, 0x78))


def zoom_of(b, o):
    xs = lambda q: [struct.unpack_from('<i', q, 0x28 + 8 * k)[0] for k in range(4)]
    return (max(xs(o)) - min(xs(o))) / (max(xs(b)) - min(xs(b)))


def corners(q, base=0x28):
    return [struct.unpack_from('<2i', q, base + 8 * k) for k in range(4)]


def shear(q):
    # (bottom - top) / 2 of the A-D quad, px: x from yaw, y from pitch
    tl, tr, bl, br = corners(q)
    return ((bl[0] + br[0] - tl[0] - tr[0]) / 1024, (bl[1] + br[1] - tl[1] - tr[1]) / 1024)


def run_mode(cam, choice, focal, n0):
    cam.choice[0] = choice
    cam.imu.rate = (0, 0, 0)
    for n in range(n0, n0 + 30):                       # settle at the same 30 ms frame cadence
        cam.imu_period(ms=10); cam.imu_period(ms=10); cam.imu_period(ms=10); cam.vrmx(n, focal=focal)
    cam.imu.rate = (int(8 * 65.5), int(6 * 65.5), 0)
    for n in range(n0 + 30, n0 + 160):                 # past the 60-frame distress back-off of any mode switch
        cam.imu_period(ms=10); cam.imu_period(ms=10); cam.imu_period(ms=10)
        b, o = cam.vrmx(n, focal=focal)
    return b, o


def main(eg_path, choice_fn):
    choice_fn = int(choice_fn, 0) if isinstance(choice_fn, str) else choice_fn
    img = open(eg_path, 'rb').read()
    AUTO = evr_builtin.AUTO_CELL
    # ---- run A -------------------------------------------------------------
    cam = Camera(img, choice_fn); imu, st = cam.imu, cam.st
    for _ in range(100): cam.imu_period()
    assert cam.cell(AUTO) == 0 and st['xfers'] == 0 and not st['allocs'] and imu.r[0x4E] == 0x23 and cam.per() == 50
    print('escape hatch: 100 IMU ticks without the e-VR hook -> never started, no I2C, period 50 ms')
    b, o = cam.vrmx(1); assert b == o and cam.cell(AUTO) == evr_builtin.REQUESTED
    cam.imu_period(res=0); assert cam.cell(AUTO) == evr_builtin.REQUESTED and st['xfers'] == 0
    print('request pending; a failed stock IMU read does not start it')
    cam.imu_period()
    blk = cam.cell(AUTO); assert blk >= POOL, hex(blk)
    got = bytes(cam.u.mem_read(blk, len(CODE)))
    diff = [i for i in range(len(CODE)) if got[i] != CODE[i]]
    tail = len(CODE.rstrip(b'\0'))
    assert diff and min(diff) >= tail, ('code copy differs outside .bss', hex(min(diff)), hex(tail))
    assert imu.r[0x4E] & 0x0C == 0x0C and imu.r[0x4F] == 0x48 and st['cleans'] >= 1 and st['choice_calls'] >= 2
    print('autostarted: block %s, code copied (%d B), cache synced, menu asked %d times, gyro 100 Hz' % (hex(blk), len(CODE), st['choice_calls']))
    for _ in range(3): cam.vrmx(2); cam.imu_period(ms=10)
    assert cam.per() == 10, cam.per()
    for n in range(3, 160): cam.vrmx(n); cam.imu_period(ms=10)            # calibrate while still
    imu.rate = (int(10 * 65.5), 0, 0)
    corrected = 0
    for n in range(160, 360):
        cam.imu_period(ms=10); cam.imu_period(ms=10); cam.imu_period(ms=10)
        b, o = cam.vrmx(n); corrected += b != o
    z = zoom_of(b, o)
    assert corrected > 150 and abs(z - 0.93) < 0.003 and not cam.bad, (corrected, z, cam.bad[:3])
    print('Standard: frames corrected %d/200, zoom %.3f, period 10 ms, all corners >= %d px inside' % (corrected, z, SAFETY))
    b, o = cam.vrmx2(400); assert b == o, 'path 2 must stay stock'
    # menu OFF
    cam.choice[0] = 3  # OFF
    for n in range(400, 440): cam.vrmx(n); cam.imu_period(ms=10)
    x0 = st['xfers']
    for n in range(440, 480): b, o = cam.vrmx(n); assert b == o; cam.imu_period(ms=10)
    assert st['xfers'] == x0 and cam.per() == 50, (st['xfers'] - x0, cam.per())
    assert all(imu.r[k] == v for k, v in STOCK_REGS.items()), {hex(k): hex(imu.r[k]) for k in STOCK_REGS}
    print('menu OFF: frames untouched, no I2C, gyro registers back to stock, period 50 ms')
    cam.choice[0] = 1  # Enhanced
    for n in range(480, 640): cam.vrmx(n); cam.imu_period(ms=10)
    for n in range(640, 700): cam.imu_period(ms=10); b, o = cam.vrmx(n)
    z = zoom_of(b, o); assert b != o and abs(z - 0.916) < 0.003 and imu.r[0x4F] == 0x48, z
    print('menu Enhanced: stabilizing again, zoom %.3f' % z)
    # Standard after Enhanced (menu rows: 0 Standard, 1 Enhanced, 2 Native crop, 3 OFF)
    n0 = 1000
    for focal in (50, 200):
        b, op = run_mode(cam, 0, focal, n0); n0 += 200
        assert imu.r[0x4F] == 0x48, ('Standard at 100 Hz', hex(imu.r[0x4F]))
        assert not cam.bad, cam.bad[:3]
        zp = zoom_of(b, op)
        assert abs(zp - 0.93) < 0.004, zp
        print('Standard %d mm: zoom %.3f, gyro 100 Hz, corners inside' % (focal, zp))
    cam.choice[0] = 0
    for n in range(2000, 2040): cam.vrmx(n); cam.imu_period(ms=10)
    assert imu.r[0x4F] == 0x48, 'back to 100 Hz in Standard'
    # v51: the FIFO stream two bytes out of step with the 8-byte packets (the axes would be
    # read shifted: pitch as roll); the next read must flush and resync, then work again
    f0 = imu.flushes
    imu.fifo[:0] = b'\x19\x00'
    cam.vrmx(2040); cam.imu_period(ms=10)
    assert imu.flushes == f0 + 1 and not imu.fifo, ('desync flushed', imu.flushes - f0)
    bad0 = len(cam.bad)
    for n in range(2041, 2080): cam.vrmx(n); cam.imu_period(ms=10)
    assert len(cam.bad) == bad0, cam.bad[bad0:bad0 + 3]
    print('FIFO out of step by 2 bytes: flushed and resynced at once, stabilizing again')
    # no e-VR frames (left movie live view): idle
    for _ in range(60): cam.imu_period(ms=10)
    assert all(imu.r[k] == v for k, v in STOCK_REGS.items()) and cam.per() == 50, 'idle without e-VR frames'
    x0 = st['xfers']
    for _ in range(40): cam.imu_period(ms=10)
    assert st['xfers'] == x0
    print('no e-VR frames: idle, gyro registers back to stock, no I2C, period 50 ms')
    assert cam.fs == 0, 'the built-in module touches no file but WL-GYROZ.CFG'
    # zero drift kept on the card (v51): arm found no file; the still calibration
    # (sensor bias -262 / 1638 / -786 LSB = -4.0 / 25.0 / -12.0 deg/s) was saved when idle
    assert cam.gz['reads'] == ['A:\\WL-GYROZ.CFG'] and cam.gz['writes'] >= 1, cam.gz
    rec = cam.card['A:\\WL-GYROZ.CFG']
    magic, b0, b1, b2, check = struct.unpack('<I3iI', rec)
    assert magic == 0x315A4757 and check == ~(magic ^ (b0 & 0xFFFFFFFF) ^ (b1 & 0xFFFFFFFF) ^ (b2 & 0xFFFFFFFF)) & 0xFFFFFFFF, rec.hex()
    want = [round(v / 65.5 * 1000) for v in (-262, 1638, -786)]
    assert all(abs(a - w) <= 20 for a, w in zip((b0, b1, b2), want)), ((b0, b1, b2), want)
    print('zero drift saved to WL-GYROZ.CFG: %d %d %d mdps (%d write(s))' % (b0, b1, b2, cam.gz['writes']))
    card = {'A:\\WL-GYROZ.CFG': struct.pack('<I3iI', 0x315A4757, 300, -200, 150,
            ~(0x315A4757 ^ 300 ^ (-200 & 0xFFFFFFFF) ^ 150) & 0xFFFFFFFF)}   # a plausible saved bias
    # ---- run B -------------------------------------------------------------
    cam = Camera(img, choice_fn, card); imu, st = cam.imu, cam.st
    cam.choice[0] = 3  # OFF
    for n in range(40): cam.vrmx(n); cam.imu_period()
    assert cam.cell(AUTO) in (0, evr_builtin.REQUESTED) and st['xfers'] == 0 and not st['allocs'] and imu.r[0x4E] == 0x23
    print('menu OFF at boot: requests dropped, nothing started, no I2C')
    cam.choice[0] = 0
    for n in range(40, 80): cam.vrmx(n); cam.imu_period()
    assert cam.cell(AUTO) >= POOL and imu.r[0x4F] == 0x48 and cam.fs == 0
    assert cam.gz['reads'] == ['A:\\WL-GYROZ.CFG'], cam.gz          # arm loaded the saved bias
    print('menu Standard afterwards: the next e-VR request starts it, from the saved zero drift')
    print('built-in gyro e-VR OK')


if __name__ == '__main__':
    main(*sys.argv[1:])
