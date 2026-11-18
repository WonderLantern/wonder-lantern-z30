"""Unicorn check of the built-in auto_arm module (build auto_arm=...) on a real built EG.

Usage: emulate_builtin.py EG.bin CHOICE_FN
CHOICE_FN is autoarm_choice (report features.auto_arm_control.choice); it is stubbed
(0 Enable, 1 Disable) because the real one restores WL-AARM.CFG from the card.
Reuses the camera model of research/evr/emulate_builtin.py (pool, time, cache
service, IMU hook stops) and adds the UI / AF state words auto_arm reads plus a stub
for the stock enter-tracking-mode function 0x1045A1AE.
"""
import runpy, struct, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from z30_extensions.features import imu_hook, evr_builtin

evr = runpy.run_path(str(ROOT / 'research' / 'evr' / 'emulate_builtin.py'))
Base, POOL = evr['Camera'], evr['POOL']
ENTER = 0x1045A1AE
UI_PHASE, UI_STATE, BACKEND = 0xC181F6FC, 0xC18292AC, 0xC181B278
AF_AREA, PHOTO_SEL = 0xC237C7F8, 0xC237CBB1
AF_BUSY, BOX, TRACKING = 0xC237F680, 0xC237F684, 0xC237F688


class Camera(Base):
    def __init__(s, img, choice_fn):
        super().__init__(img, choice_fn)
        from unicorn import UC_HOOK_CODE
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_SP
        for a in (0xC181F000, 0xC1829000, 0xC181B000, 0xC237C000, 0xC237F000):
            s.u.mem_map(a, 0x1000)
        s.enters, s.enter_args = 0, []
        def enter(uc, addr, size, _):
            s.enters += 1
            arg = uc.reg_read(UC_ARM_REG_R0)
            s.enter_args.append((arg, uc.mem_read(arg, 1)[0], uc.reg_read(UC_ARM_REG_SP) & 7))
            if s.box_follows: uc.mem_write(BOX, struct.pack('<I', 1))
            uc.reg_write(UC_ARM_REG_R0, 0); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        s.box_follows = True
        s.u.hook_add(UC_HOOK_CODE, enter, begin=ENTER, end=ENTER + 1)
        s.state(phase=4)

    def state(s, phase=2, ui=0, backend=1, area=6, photo=0, busy=0, box=0, tracking=0):
        w = lambda a, v, n=4: s.u.mem_write(a, v.to_bytes(n, 'little'))
        w(UI_PHASE, phase, 1); w(UI_STATE, ui, 1); w(BACKEND, backend); w(AF_AREA, area, 1)
        w(PHOTO_SEL, photo, 1); w(AF_BUSY, busy); w(BOX, box); w(TRACKING, tracking)

    def ticks(s, n):
        for _ in range(n): s.imu_period()


def main(eg_path, choice_fn):
    choice_fn = int(choice_fn, 0) if isinstance(choice_fn, str) else choice_fn
    img = open(eg_path, 'rb').read()
    AUTO = evr_builtin.AUTO_CELL
    cam = Camera(img, choice_fn); st = cam.st
    assert cam.cell(AUTO) == 0 and cam.cell(imu_hook.IMU_CELL) == 0
    cam.imu_period(res=0)
    assert cam.cell(AUTO) == 0 and not st['allocs'], 'a failed stock IMU read must not start it'
    print('failed stock IMU read at boot: not started, stock result kept')
    cam.imu_period()                                   # menus at boot (phase 4)
    blk, hook = cam.cell(AUTO), cam.cell(imu_hook.IMU_CELL)
    assert blk >= POOL and hook and POOL <= hook & ~1 < POOL + 0x800000 and st['cleans'] >= 1, (hex(blk), hex(hook))
    assert cam.enters == 0 and cam.fs == 0
    print('first good IMU read: started, block %s, tick hooked at %s, cache synced, no file access' % (hex(blk), hex(hook)))
    cam.ticks(60); assert cam.enters == 0, 'menus (phase 4): no call'
    cam.state(); cam.ticks(40)
    assert cam.enters == 1, cam.enters
    arg, byte, align = cam.enter_args[0]
    assert byte == 0 and align == 0, cam.enter_args
    calls = st['choice_calls']
    print('photo live view, Auto-area AF, idle: enter-tracking called once (arg -> 0, stack 8-aligned); menu asked %d times' % calls)
    cam.ticks(100); assert cam.enters == 1, 'box shown: no further calls'
    print('box up: no further calls for 100 ticks')
    cases = [('video', dict(photo=1)), ('i menu', dict(ui=3)), ('menus / playback', dict(phase=4)),
             ('Single-point AF', dict(area=1)), ('Wide-area L', dict(area=4)), ('half-press', dict(busy=1)),
             ('tracking', dict(tracking=1)), ('backend 0', dict(backend=0))]
    for name, kw in cases:
        cam.state(**kw); n0 = cam.enters; cam.ticks(60)
        assert cam.enters == n0, (name, cam.enters - n0)
        print('%s: no call' % name)
    cam.state(); cam.ticks(40); assert cam.enters == 2, cam.enters
    print('back in photo live view: called again')
    cam.choice[0] = 1; cam.state(); cam.ticks(25)       # Disable: read within ~1 s
    cam.state(phase=4); cam.ticks(5); cam.state(); n0 = cam.enters; cam.ticks(120)
    assert cam.enters == n0, 'menu Disable: no call'
    print('menu Disable: no call in photo live view for 120 ticks')
    cam.choice[0] = 0; cam.ticks(40); assert cam.enters == n0 + 1, cam.enters - n0
    print('menu Enable again: called within ~2 s')
    cam.box_follows = False; cam.state(); n0 = cam.enters; cam.ticks(70)
    assert 1 <= cam.enters - n0 <= 3, cam.enters - n0
    print('box never appears: retried %d times in 70 ticks (about every 1.5 s)' % (cam.enters - n0))
    assert cam.fs == 0, 'the built-in module touches no file'
    print('built-in auto_arm OK')


if __name__ == '__main__':
    main(*sys.argv[1:])
