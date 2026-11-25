"""Execute the portrait UI entries of a built image under Unicorn.

Every entry runs from the patched stock site to the point where the displaced
stock window resumes. Two properties are checked:

* Inert: with the switch off, with no menu state yet, in landscape and outside
  photo live view, every register, the stack and the descriptor equal a run of
  the unmodified stock window from the same state.
* Portrait: with the switch on and the camera upright, each descriptor or glyph
  matrix is rewritten exactly as the Python model in portrait_ui says.

The orientation getter and vgLoadMatrix are boundaries (their results are
inputs here). Nothing here draws pixels or proves what the camera shows.
Also scans the stock image for branches into the displaced windows.
"""
import argparse
import json
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE  # noqa: E402
from unicorn.arm_const import *  # noqa: E402,F403
from z30_extensions.build import build  # noqa: E402
from z30_extensions.container import extract_eg  # noqa: E402
from z30_extensions.features.portrait import layout as pu  # noqa: E402
from z30_extensions.features.menu.layout import MAGIC, STATE_ANCHOR  # noqa: E402
from z30_extensions.platform.z30_120 import BASE, SCATTER, StockImage  # noqa: E402

STACK, STACK_TOP = 0x60000000, 0x600F0000
STATE, DESC = 0x50000000, 0x50200000
SENTINEL_LR = 0x5FFF0001
REGS = [UC_ARM_REG_R0 + i for i in range(13)] + [UC_ARM_REG_SP, UC_ARM_REG_LR]
FLAGS = 0xF8000000
# Bytes each displaced window pushes before allocating its locals.
PUSHED = dict(rect=24, clear=20, frame=36, image=36, image2=36, image3=36, text=0)


class Machine:
    def __init__(self, image, state_offset=None):
        self.u = u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(BASE, (len(image) + 4095) & ~4095)
        u.mem_write(BASE, image)
        u.mem_map(0xC1800000, 0x100000)
        source, destination, size, _ = SCATTER
        u.mem_write(destination, image[source - BASE:source - BASE + size])
        u.mem_map(STATE, 0x100000)
        u.mem_map(DESC, 0x1000)
        u.mem_map(0xC2016000, 0x1000)  # overlay words
        u.mem_map(0xC2017000, 0x1000)  # histogram counts (HIST_BINS)
        u.mem_map(STACK, 0x100000)
        u.mem_map(SENTINEL_LR & ~0xFFF, 0x1000)
        self.orientation = 0
        self.loaded = []
        def orientation(mu, address, size, data):
            mu.reg_write(UC_ARM_REG_R0, self.orientation)
            mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, orientation, begin=pu.ORIENTATION, end=pu.ORIENTATION)
        def load_matrix(mu, address, size, data):
            pointer = mu.reg_read(UC_ARM_REG_R0)
            self.loaded.append(bytes(mu.mem_read(pointer, 36)))
            mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, load_matrix, begin=pu.LOAD_MATRIX, end=pu.LOAD_MATRIX)
        self.requests = []
        self.intercept_redraw = True
        def redraw(mu, address, size, data):
            if not self.intercept_redraw:
                return
            self.requests.append(tuple(mu.reg_read(UC_ARM_REG_R0 + i) for i in range(3)))
            mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, redraw, begin=pu.REDRAW, end=pu.REDRAW)
        def gate(mu, address, size, data):  # 103051A0 inside the 'update' event window
            mu.reg_write(UC_ARM_REG_R0, 0)
            mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, gate, begin=0x103051A0, end=0x103051A0)
        u.mem_map(0xC201A000, 0x1000)  # UI handler table read by the event windows
        u.mem_map(pu.NAV_BLOCK & ~0xFFF, 0x1000)  # i-menu navigation block (count, slot)
        self.slot, self.set_calls = 0, []
        def cursor_get(mu, address, size, data):   # 1042B52A: the current slot is an input
            mu.reg_write(UC_ARM_REG_R0, self.slot)
            mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
        def cursor_set(mu, address, size, data):   # 1042BBFC: record the slot it is asked to show
            self.set_calls.append(mu.reg_read(UC_ARM_REG_R0))
            mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, cursor_get, begin=pu.CURSOR_GET, end=pu.CURSOR_GET)
        u.hook_add(UC_HOOK_CODE, cursor_set, begin=pu.CURSOR_SET, end=pu.CURSOR_SET)
        u.mem_map(0xC1D41000, 0x1000)  # pose byte the event trace records
        u.mem_map(0xC6100000, 0x2000)  # fake widgets for the draw-record tests
        self.refreshes = []
        def refresh(mu, address, size, data):  # 10454226 mark all / 10454218 redraw now
            self.refreshes.append((address, mu.reg_read(UC_ARM_REG_R0), mu.reg_read(UC_ARM_REG_R1)))
            mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
        for address in (pu.MARK_ALL, pu.REDRAW_NOW):
            u.hook_add(UC_HOOK_CODE, refresh, begin=address, end=address)
        self.state_offset = state_offset

    def context(self, switch=1, present=True, phase=2, kind=0, backend=1, target=2, orientation=0, busy=(0, 0),
                nav_state=0, nav_count=12):
        u = self.u
        u.mem_write(STATE, bytes(0x100000))
        if present and self.state_offset is not None:
            u.mem_write(STATE + 56, struct.pack('<I', MAGIC))
            u.mem_write(STATE + self.state_offset, struct.pack('<I', switch))
            u.mem_write(STATE_ANCHOR + 16, struct.pack('<I', STATE))
        else:
            u.mem_write(STATE_ANCHOR + 16, struct.pack('<I', 0))
        u.mem_write(pu.UI_SERVICE_PHASE, bytes([phase]))
        u.mem_write(pu.VIEW_KIND, bytes([kind]))
        u.mem_write(pu.PHOTO_BACKEND, struct.pack('<I', backend))
        u.mem_write(pu.TARGET, bytes([target]))
        for address, value in zip(pu.OVERLAY_BUSY, busy):
            u.mem_write(address, struct.pack('<I', value))
        u.mem_write(pu.NAV_TABLE, bytes([nav_state]))
        u.mem_write(pu.NAV_BLOCK, struct.pack('<2I', nav_count, 0))
        self.orientation = orientation
        self.set_calls.clear()

    def words(self, offset, count):
        return struct.unpack(f'<{count}I', self.u.mem_read(STATE + self.state_offset + offset, count * 4))

    def runtime(self):
        last, pending = struct.unpack('<2I', self.u.mem_read(STATE + self.state_offset + 16, 8))
        return last, pending

    def run(self, site, stop, regs, stack=b'', pushed=0, lr=SENTINEL_LR):
        """Run from site to stop (or the sentinel return); return registers, stack, descriptor."""
        u = self.u
        sp = STACK_TOP - len(stack)
        u.mem_write(STACK, bytes(0x100000))
        u.mem_write(sp, stack)
        for r, v in zip(REGS[:13], regs):
            u.reg_write(r, v)
        u.reg_write(UC_ARM_REG_SP, sp)
        u.reg_write(UC_ARM_REG_LR, lr)
        cpsr = u.reg_read(UC_ARM_REG_CPSR)
        u.reg_write(UC_ARM_REG_CPSR, cpsr & ~FLAGS)
        self.loaded.clear()
        self.requests.clear()
        try:
            u.emu_start(site | 1, stop, count=200000)
        except Exception as error:
            raise RuntimeError(f'emulation from {site:#x} stopped at pc={u.reg_read(UC_ARM_REG_PC):#x} '
                               f'lr={u.reg_read(UC_ARM_REG_LR):#x}: {error}') from error
        pc = u.reg_read(UC_ARM_REG_PC)
        out = [u.reg_read(r) for r in REGS]
        # Below the registers the displaced prologue pushed lies the callee's
        # not-yet-written local frame; only the caller's stack and the pushes count.
        low = sp - pushed
        return dict(pc=pc, regs=out, flags=u.reg_read(UC_ARM_REG_CPSR) & FLAGS,
                    stack=bytes(u.mem_read(low, STACK_TOP - low)), desc=bytes(u.mem_read(DESC, 0x60)))


def rect_bytes(x, yu, w, h, color=0x202020FF):
    return struct.pack('<4iI', x, yu, w, h, color) + bytes(12)


def image_bytes(x0, y0, x1, y1, sx1, sy1, mode=0, tint=0xFFFFFFFF, angle=0):
    d = bytearray(0x5C)
    struct.pack_into('<I', d, 0, mode)
    struct.pack_into('<4h', d, 0x10, 0, 0, sx1, sy1)
    struct.pack_into('<4h', d, 0x18, x0, y0, x1, y1)
    struct.pack_into('<I', d, 0x20, tint)
    struct.pack_into('<H', d, 0x40, angle)
    return bytes(d)


def f32(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


def locator(menu):
    return (pu.locate_mini if menu == 'mini' else pu.locate_slider if menu == 'slider'
            else pu.locate_menu if menu else pu.locate)


def model_rect(ori, kind, b, menu=False):
    x, yu, w, h, color = struct.unpack('<4iI', b[:20])
    if kind == 'rect' and focus(color & 0xFFFFFFFF):
        return b
    if kind == 'clear' and menu in (False, 'slider') and (w <= pu.THIN or h <= pu.THIN):
        return b
    status, px, py, pw, ph = locator(menu)(x, 480 - yu - h, w, h)
    if not status:
        return b
    if status == 3:  # not part of the i menu: clears are dropped, the rest stays
        return struct.pack('<4i', x, yu, 0, 0) + b[16:] if kind == 'clear' else b
    if pw > 480:
        px, pw = 0, 480
        if status == 2:  # a full-width bar covers both rows
            ph += pu.ROW
    return struct.pack('<4i', *pu.panel_box(ori, px, py, pw, ph)) + b[16:]


def model_image(ori, kind, d, menu=False):
    d = bytearray(d)
    mode, = struct.unpack_from('<I', d, 0)
    sx0, sy0, sx1, sy1 = struct.unpack_from('<4h', d, 0x10)
    x0, y0, x1, y1 = struct.unpack_from('<4h', d, 0x18)
    tint, = struct.unpack_from('<I', d, 0x20)
    angle, = struct.unpack_from('<H', d, 0x40)
    sw, sh = sx1 - sx0, sy1 - sy0
    if angle not in (0, 360) or sw <= 0 or sh <= 0 or (kind == 'image2' and sw >= 700 and sh >= 400) or focus(tint):
        return bytes(d)
    w, h = (sw, sh) if mode == 1 else (x1 - x0, y1 - y0)
    if w <= 0 or h <= 0:
        return bytes(d)
    status, px, py, pw, ph = locator(menu)(x0, y0, w, h)
    if status in (0, 3):
        return bytes(d)
    struct.pack_into('<I', d, 0, 0)
    if ori == 2:
        box, angle = (py, 480 - px - pw, py + ph, 480 - px), 90
    else:
        box, angle = (720 - py - ph, px, 720 - py, px + pw), 270
    struct.pack_into('<4h', d, 0x18, *box)
    struct.pack_into('<H', d, 0x40, angle)
    return bytes(d)


def f2fix(b):
    e = ((b >> 23) & 255) - 127
    if e < -8:
        return 0
    m = (b & 0x7FFFFF) | 0x800000
    if e - 15 >= 8:
        return 0
    v = m << (e - 15) if e >= 15 else m >> (15 - e)
    return -v if b >> 31 else v


def fix2f(v):
    if not v:
        return 0
    s = 0x80000000 if v < 0 else 0
    a = -v if v < 0 else v
    top = a.bit_length() - 1
    a = a >> (top - 23) if top >= 23 else a << (23 - top)
    return s | ((top + 119) << 23) | (a & 0x7FFFFF)


def model_text(ori, m, menu=False):
    m = list(struct.unpack('<9I', m))
    if (m[1] << 1) & 0xFFFFFFFF or (m[3] << 1) & 0xFFFFFFFF:
        return m
    a, d = m[0], m[4]
    gw, gh = f2fix(a) >> 8, f2fix(d) >> 8
    if gh <= 0:
        return m
    e, f = f2fix(m[6]), f2fix(m[7])
    status, bx, by, pw, ph = locator(menu)(e >> 8, 480 - (f >> 8) - gh, gw, gh)
    if status in (0, 3):
        return m
    if status == 4:  # the 2/3 setting page: the glyph scale shrinks too
        a, d = fix2f(pu.scale_mini(f2fix(a))), fix2f(pu.scale_mini(f2fix(d)))
    px = (bx << 8) + (e & 255)
    py = (by + ph) << 8
    if ori == 2:
        d ^= 0x80000000
    else:
        a ^= 0x80000000
        py, px = (720 << 8) - py, (480 << 8) - px
    return [0, a, 0, d, 0, 0, fix2f(py), fix2f(px), 0x3F800000]


def focus(rgba):
    r, g, b = rgba >> 24, (rgba >> 16) & 255, (rgba >> 8) & 255
    return (r >= 0xC0 and g < 0x80 and b < 0x60) or (g >= 0xC0 and r < 0x40 and b < 0x40)


def windows():
    out = [(site, site + len(bytes.fromhex(expected))) for _, site, expected, _ in pu.HOOKS]
    out.append((pu.TEXT_CALL, pu.TEXT_CALL + len(pu.TEXT_EXPECTED)))
    out.append((pu.REDRAW, pu.REDRAW + len(pu.REDRAW_EXPECTED)))
    out.append((pu.TRAVERSE, pu.TRAVERSE + len(pu.TRAVERSE_EXPECTED)))
    if pu.MARK_RECORDED:
        out.append((pu.DRAW_SITE, pu.DRAW_SITE + len(pu.DRAW_EXPECTED)))
    out.extend((site, site + len(bytes.fromhex(expected))) for _, site, expected, _ in pu.EVENT_HOOKS)
    if pu.REPAINT_HOOK:
        out.append((pu.REFRESH_REQUEST, pu.REFRESH_REQUEST + len(pu.REFRESH_EXPECTED)))
    out.append((pu.NAV_MOVE, pu.NAV_MOVE_RESUME))
    out.append((pu.TOUCH, pu.TOUCH + len(pu.TOUCH_EXPECTED)))
    return out


def incoming_branches(stock):
    """Thumb branch encodings anywhere in the module whose target lies inside a window (excluding its start)."""
    data = stock
    hits = []
    inside = [(lo + 2, hi) for lo, hi in windows()]
    def check(at, target):
        for lo, hi in inside:
            if lo <= target < hi:
                hits.append((hex(at), hex(target)))
    for off in range(0, len(data) - 4, 2):
        h1 = data[off] | data[off + 1] << 8
        a = BASE + off
        if (h1 & 0xF800) in (0xF000,):
            h2 = data[off + 2] | data[off + 3] << 8
            if h2 & 0x8000:
                s = (h1 >> 10) & 1
                j1, j2 = (h2 >> 13) & 1, (h2 >> 11) & 1
                if h2 & 0x1000:  # BL / B.W (T4)
                    i1, i2 = ~(j1 ^ s) & 1, ~(j2 ^ s) & 1
                    imm = (s << 24) | (i1 << 23) | (i2 << 22) | ((h1 & 0x3FF) << 12) | ((h2 & 0x7FF) << 1)
                    imm -= (1 << 25) if s else 0
                    check(a, a + 4 + imm)
                elif not (h2 & 0x4000) and ((h1 >> 6) & 0xF) < 14:  # B<c>.W (T3)
                    imm = (s << 20) | (j2 << 19) | (j1 << 18) | ((h1 & 0x3F) << 12) | ((h2 & 0x7FF) << 1)
                    imm -= (1 << 21) if s else 0
                    check(a, a + 4 + imm)
        elif (h1 & 0xF800) == 0xE000:  # B (T2)
            imm = (h1 & 0x7FF) << 1
            imm -= 0x1000 if imm & 0x800 else 0
            check(a, a + 4 + imm)
        elif (h1 & 0xF000) == 0xD000 and (h1 >> 8) & 0xF < 14:  # B<c> (T1)
            imm = (h1 & 0xFF) << 1
            imm -= 0x200 if imm & 0x100 else 0
            check(a, a + 4 + imm)
        elif (h1 & 0xF500) == 0xB100:  # CBZ / CBNZ
            check(a, a + 4 + ((((h1 >> 9) & 1) << 6) | (((h1 >> 3) & 0x1F) << 1)))
    return hits


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True, help='Z_30_0120.bin or an extracted stock EG module')
    p.add_argument('--preset', nargs='+', default=['full', 'portrait-ui'])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cases', type=int, default=120)
    a = p.parse_args()
    raw = a.source.read_bytes()
    try:
        stock = StockImage(raw).data
    except ValueError:
        stock = StockImage(extract_eg(raw)).data
    image, report = build(stock, a.preset)
    control = report['features']['portrait_ui_control']
    patched = Machine(image, control['state_offset'])
    original = Machine(stock, control['state_offset'])
    rng = random.Random(20261110)
    results = dict(preset=a.preset, sha256=report['sha256'], hooks=report['features']['portrait_ui']['hooks'])
    failures = []

    entries = [(name, site, site + len(bytes.fromhex(expected))) for name, site, expected, _ in pu.HOOKS]
    entries.append(('text', pu.TEXT_CALL, pu.TEXT_CALL + len(pu.TEXT_EXPECTED)))

    def random_input(name):
        if name in ('rect', 'clear'):
            x, y = rng.randrange(-20, 740), rng.randrange(-20, 500)
            w, h = rng.randrange(1, 760), rng.randrange(1, 490)
            if rng.random() < .25:
                w, h = rng.choice(((2, 44), (66, 2), (3, 3), (4, 2)))
            data = rect_bytes(x, 480 - y - 30, w, h, rng.getrandbits(32))
        elif name == 'frame':
            data = b''
        elif name == 'text':
            sx = rng.choice((16.0, 20.0, 24.0, 28.5))
            data = struct.pack('<9I', f32(sx), 0, 0, 0, f32(sx), 0, f32(rng.uniform(0, 700)), f32(rng.uniform(0, 470)), f32(1))
        elif name == 'image3':
            d = bytearray(image_bytes(0, 0, 0, 0, 40, 40))
            x0, y0 = rng.randrange(0, 700), rng.randrange(0, 460)
            struct.pack_into('<4i', d, 0x18, x0 << 16, y0 << 16, (x0 + rng.randrange(4, 60)) << 16, (y0 + rng.randrange(4, 60)) << 16)
            data = bytes(d)
        else:
            x0, y0 = rng.randrange(0, 700), rng.randrange(0, 460)
            w, h = rng.randrange(4, 80), rng.randrange(4, 80)
            if rng.random() < .15:
                w, h = 720, 480
            data = image_bytes(x0, y0, x0 + w, y0 + h, w, h, mode=rng.choice((0, 1)),
                               tint=rng.choice((0xFFFFFFFF, 0xE02020FF, 0x20E020FF)), angle=rng.choice((360, 360, 0, 90)))
        regs = [rng.getrandbits(32) for _ in range(13)]
        if name == 'frame':
            regs[:4] = [rng.randrange(0, 700), rng.randrange(0, 460), rng.randrange(4, 100), rng.randrange(4, 60)]
        else:
            regs[0] = DESC
        return data, regs

    def run(machine, name, site, stop, data, regs, stack=None):
        machine.u.mem_write(DESC, (data + bytes(0x60))[:0x60])
        if stack is None:
            stack = bytes(rng.getrandbits(8) for _ in range(64))
        return machine.run(site, stop, regs, stack=stack, pushed=PUSHED[name])

    # 1. Inert in every state that must leave the stock path alone.
    inert_states = dict(switch_off=dict(switch=0, orientation=1), no_state=dict(present=False, orientation=2),
                        landscape=dict(orientation=0), inverted=dict(orientation=3),
                        playback_phase=dict(phase=4, orientation=1), other_view=dict(kind=5, orientation=2),
                        menu_setting_level=dict(kind=pu.MENU_VIEW, nav_state=3, nav_count=7, orientation=0),
                        menu_level=dict(kind=pu.MENU_VIEW, orientation=0),
                        slider_level=dict(kind=13, orientation=0), slider_view_10=dict(kind=10, orientation=1),
                        slider_view_14=dict(kind=14, orientation=2),
                        no_backend=dict(backend=0, orientation=1), off_screen_target=dict(target=5, orientation=2),
                        overlay_first=dict(busy=(1, 0), orientation=1), overlay_second=dict(busy=(0, 0x20), orientation=2))
    inert = {}
    for label, state in inert_states.items():
        count = 0
        for name, site, stop in entries:
            for _ in range(a.cases // 4):
                data, regs = random_input(name)
                stack = bytes(rng.getrandbits(8) for _ in range(64))
                outs = []
                for machine in (patched, original):
                    machine.context(**state)
                    outs.append(run(machine, name, site, stop, data, regs, stack))
                    outs[-1]['loaded'] = list(machine.loaded)
                    if name == 'text':  # r12 and lr are scratch across the vgLoadMatrix call
                        outs[-1]['regs'] = outs[-1]['regs'][:12] + outs[-1]['regs'][13:14]
                mine, theirs = outs
                for key in ('pc', 'regs', 'flags', 'desc', 'stack', 'loaded'):
                    if mine[key] != theirs[key]:
                        failures.append(dict(test='inert', state=label, entry=name, field=key))
                        break
                count += 1
        inert[label] = count
    results['inert_cases'] = inert

    # 2. Portrait rewrites against the model.
    rewrites = {}
    for ori in (1, 2):
        for name, site, stop in entries:
            if name == 'image3':
                continue
            count = 0
            for _ in range(a.cases):
                data, regs = random_input(name)
                patched.context(orientation=ori)
                # Seed the mode as already active, so no pending full clear interferes.
                patched.u.mem_write(STATE + control['state_offset'] + 16, struct.pack('<2I', ori, 0))
                out = run(patched, name, site, stop, data, regs)
                if out['pc'] != stop:
                    failures.append(dict(test='portrait', entry=name, ori=ori, field='pc', pc=hex(out['pc'])))
                    continue
                if name in ('rect', 'clear'):
                    want = model_rect(ori, name, data)
                    got = out['desc'][:len(want)]
                elif name == 'frame':
                    box = struct.pack('<4i', *[v - (1 << 32) if v >> 31 else v for v in regs[:4]]) + bytes(4)
                    want = model_rect(ori, 'frame', box)[:16]
                    got = struct.pack('<4I', *out['regs'][:4])
                    want = struct.pack('<4I', *[v & 0xFFFFFFFF for v in struct.unpack('<4i', want)])
                elif name == 'text':
                    want = struct.pack('<9I', *model_text(ori, data))
                    got = patched.loaded[0] if patched.loaded else b''
                else:
                    want = model_image(ori, name, data)
                    got = out['desc'][:len(want)]
                if got != want:
                    failures.append(dict(test='portrait', entry=name, ori=ori, input=data.hex(),
                                         regs=[hex(v) for v in regs[:4]], got=got.hex(), want=want.hex()))
                count += 1
            rewrites[f'{name}-{ori}'] = count
    results['portrait_cases'] = rewrites

    # 2b. The i menu (view kind 3, grid state): the boxes read on the camera, then random ones.
    camera_boxes = [(0, 280, 720, 146), (4, 284, 113, 66), (603, 356, 113, 66), (0, 280, 2, 74), (119, 352, 2, 74),
                    (718, 352, 2, 74), (599, 424, 121, 2), (0, 280, 121, 2), (88, 228, 544, 44), (463, 430, 102, 48),
                    (575, 432, 123, 44), (2, 446, 60, 32), (90, 42, 36, 36), (4, 180, 36, 36), (0, 0, 720, 480)]
    menu_cases = {}
    for ori in (1, 2):
        for name, site, stop in entries:
            if name == 'image3':
                continue
            count = 0
            for i in range(a.cases):
                data, regs = random_input(name)
                if i < len(camera_boxes) and name in ('rect', 'clear', 'frame', 'image', 'image2'):
                    x, y, w, h = camera_boxes[i]
                    if name in ('rect', 'clear'):
                        data = rect_bytes(x, 480 - y - h, w, h)
                    elif name == 'frame':
                        regs[:4] = [x, 480 - y - h, w, h]
                    else:
                        data = image_bytes(x, y, x + w, y + h, w, h, mode=1, angle=360)
                patched.context(orientation=ori, kind=pu.MENU_VIEW)
                patched.u.mem_write(STATE + control['state_offset'] + 16, struct.pack('<2I', ori | pu.MENU_MODE, 0))
                out = run(patched, name, site, stop, data, regs)
                if out['pc'] != stop:
                    failures.append(dict(test='menu', entry=name, ori=ori, field='pc', pc=hex(out['pc'])))
                    continue
                if name in ('rect', 'clear'):
                    want = model_rect(ori, name, data, menu=True)
                    got = out['desc'][:len(want)]
                elif name == 'frame':
                    box = struct.pack('<4i', *[v - (1 << 32) if v >> 31 else v for v in regs[:4]]) + bytes(4)
                    want = model_rect(ori, 'frame', box, menu=True)[:16]
                    got = struct.pack('<4I', *out['regs'][:4])
                    want = struct.pack('<4I', *[v & 0xFFFFFFFF for v in struct.unpack('<4i', want)])
                elif name == 'text':
                    want = struct.pack('<9I', *model_text(ori, data, menu=True))
                    got = patched.loaded[0] if patched.loaded else b''
                else:
                    want = model_image(ori, name, data, menu=True)
                    got = out['desc'][:len(want)]
                if got != want:
                    failures.append(dict(test='menu', entry=name, ori=ori, input=data.hex(),
                                         regs=[hex(v) for v in regs[:4]], got=got.hex(), want=want.hex()))
                count += 1
            menu_cases[f'{name}-{ori}'] = count
    results['menu_cases'] = menu_cases

    # 2c. i-menu keys: the slot moves in 4x3 with the screen; elsewhere the stock window runs unchanged.
    key_cases = dict(moved=0, stock=0)
    key_stop = pu.NAV_MOVE_RESUME
    for ori in (1, 2):
        for direction in range(4):
            for slot in range(12):
                patched.context(orientation=ori, kind=pu.MENU_VIEW)
                patched.u.mem_write(STATE + control['state_offset'] + 16, struct.pack('<I', ori | pu.MENU_MODE))
                patched.slot = slot
                regs = [direction] + [rng.getrandbits(32) for _ in range(12)]
                out = patched.run(pu.NAV_MOVE, SENTINEL_LR & ~1, regs, lr=SENTINEL_LR)
                stored = struct.unpack('<I', patched.u.mem_read(pu.NAV_BLOCK + 4, 4))[0]
                want = pu.nav_target(ori, direction, slot)
                if (out['pc'] | 1) != SENTINEL_LR or out['regs'][0] != 0 or stored != want or patched.set_calls != [want] \
                        or out['regs'][1:4] != regs[1:4]:
                    failures.append(dict(test='menu_keys', ori=ori, direction=direction, slot=slot,
                                         stored=stored, set=patched.set_calls, want=want, pc=hex(out['pc'])))
                key_cases['moved'] += 1
    stock_states = dict(switch_off=(dict(switch=0, kind=3, orientation=1), 1 | pu.MENU_MODE),
                        live_view_mode=(dict(kind=3, orientation=1), 1),
                        live_view=(dict(kind=0, orientation=2), 2 | pu.MENU_MODE),
                        setting_page=(dict(kind=3, nav_state=3, nav_count=7, orientation=2), 2 | pu.MENU_MODE),
                        other_count=(dict(kind=3, nav_count=7, orientation=2), 2 | pu.MENU_MODE),
                        no_state=(dict(present=False, kind=3, orientation=1), 1 | pu.MENU_MODE))
    for label, (state, mode) in stock_states.items():
        for direction in (0, 1, 2, 3, 4, 9):
            regs = [direction] + [rng.getrandbits(32) for _ in range(12)]
            outs = []
            for machine in (patched, original):
                machine.context(**state)
                if state.get('present', True):
                    machine.u.mem_write(STATE + control['state_offset'] + 16, struct.pack('<I', mode))
                machine.slot = 5
                outs.append(machine.run(pu.NAV_MOVE, key_stop, regs))
            mine, theirs = outs
            # r12 is scratch in the hook's stock path (constant loads); everything else must match.
            if mine['pc'] != key_stop or [mine['regs'][i] for i in range(15) if i != 12] != \
                    [theirs['regs'][i] for i in range(15) if i != 12] or mine['stack'] != theirs['stack'] or patched.set_calls:
                failures.append(dict(test='menu_keys_stock', state=label, direction=direction, pc=hex(mine['pc'])))
            key_cases['stock'] += 1
    results['menu_key_cases'] = key_cases

    # 2d. i-menu setting page (view kind 3, navigation-table state not 0): the 2/3 page.
    mini_cases = {}
    for ori in (1, 2):
        for name, site, stop in entries:
            if name == 'image3':
                continue
            count = 0
            for _ in range(a.cases):
                data, regs = random_input(name)
                patched.context(orientation=ori, kind=pu.MENU_VIEW, nav_state=3, nav_count=7)
                patched.u.mem_write(STATE + control['state_offset'] + 16, struct.pack('<2I', ori | pu.MINI_MODE, 0))
                out = run(patched, name, site, stop, data, regs)
                if out['pc'] != stop:
                    failures.append(dict(test='mini', entry=name, ori=ori, field='pc', pc=hex(out['pc'])))
                    continue
                if name in ('rect', 'clear'):
                    want = model_rect(ori, name, data, menu='mini')
                    got = out['desc'][:len(want)]
                elif name == 'frame':
                    box = struct.pack('<4i', *[v - (1 << 32) if v >> 31 else v for v in regs[:4]]) + bytes(4)
                    want = model_rect(ori, 'frame', box, menu='mini')[:16]
                    got = struct.pack('<4I', *out['regs'][:4])
                    want = struct.pack('<4I', *[v & 0xFFFFFFFF for v in struct.unpack('<4i', want)])
                elif name == 'text':
                    want = struct.pack('<9I', *model_text(ori, data, menu='mini'))
                    got = patched.loaded[0] if patched.loaded else b''
                else:
                    want = model_image(ori, name, data, menu='mini')
                    got = out['desc'][:len(want)]
                if got != want:
                    failures.append(dict(test='mini', entry=name, ori=ori, input=data.hex(),
                                         regs=[hex(v) for v in regs[:4]], got=got.hex(), want=want.hex()))
                count += 1
            mini_cases[f'{name}-{ori}'] = count
    results['mini_cases'] = mini_cases
    # The drawing hooks enter the 2/3 mode on a setting page, with one full clear.
    patched.context(orientation=1, kind=pu.MENU_VIEW, nav_state=3, nav_count=7)
    clear_site0, clear_stop0 = next((s, e) for n, s, e in entries if n == 'clear')
    out = run(patched, 'clear', clear_site0, clear_stop0, rect_bytes(100, 400, 40, 20), [DESC] + [0] * 12)
    got_mode = patched.runtime()
    if (struct.unpack('<4i', out['desc'][:16]), got_mode) != ((0, 0, 720, 480), (1 | pu.MINI_MODE, 0)):
        failures.append(dict(test='mini_enter', box=struct.unpack('<4i', out['desc'][:16]), runtime=got_mode))

    # 2d'. Slider pages (view kinds 11..13): the boxes read on the camera, then random ones.
    slider_boxes = [(93, 358, 80, 48), (203, 358, 314, 48), (547, 358, 80, 48), (204, 359, 62, 46), (454, 359, 62, 46),
                    (115, 366, 34, 32), (285, 362, 150, 40), (225, 406, 24, 24), (314, 406, 24, 24), (470, 406, 24, 24),
                    (636, 4, 80, 48), (658, 10, 36, 36), (680, 238, 38, 188), (682, 330, 19, 4), (128, 430, 124, 48),
                    (436, 430, 153, 48), (2, 396, 60, 32), (0, 0, 720, 480)]
    slider_cases = {}
    for ori in (1, 2):
        for name, site, stop in entries:
            if name == 'image3':
                continue
            count = 0
            for i in range(a.cases):
                data, regs = random_input(name)
                if i < len(slider_boxes) and name in ('rect', 'clear', 'frame', 'image', 'image2'):
                    x, y, w, h = slider_boxes[i]
                    if name in ('rect', 'clear'):
                        data = rect_bytes(x, 480 - y - h, w, h)
                    elif name == 'frame':
                        regs[:4] = [x, 480 - y - h, w, h]
                    else:
                        data = image_bytes(x, y, x + w, y + h, w, h, mode=1, angle=360)
                kind = pu.SLIDER_VIEWS[i % len(pu.SLIDER_VIEWS)]
                patched.context(orientation=ori, kind=kind)
                patched.u.mem_write(STATE + control['state_offset'] + 16, struct.pack('<2I', ori | pu.SLIDER_MODE, 0))
                out = run(patched, name, site, stop, data, regs)
                if out['pc'] != stop:
                    failures.append(dict(test='slider', entry=name, ori=ori, field='pc', pc=hex(out['pc'])))
                    continue
                if name in ('rect', 'clear'):
                    want = model_rect(ori, name, data, menu='slider')
                    got = out['desc'][:len(want)]
                elif name == 'frame':
                    box = struct.pack('<4i', *[v - (1 << 32) if v >> 31 else v for v in regs[:4]]) + bytes(4)
                    want = model_rect(ori, 'frame', box, menu='slider')[:16]
                    got = struct.pack('<4I', *out['regs'][:4])
                    want = struct.pack('<4I', *[v & 0xFFFFFFFF for v in struct.unpack('<4i', want)])
                elif name == 'text':
                    want = struct.pack('<9I', *model_text(ori, data, menu='slider'))
                    got = patched.loaded[0] if patched.loaded else b''
                else:
                    want = model_image(ori, name, data, menu='slider')
                    got = out['desc'][:len(want)]
                if got != want:
                    failures.append(dict(test='slider', entry=name, ori=ori, kind=kind, input=data.hex(),
                                         regs=[hex(v) for v in regs[:4]], got=got.hex(), want=want.hex()))
                count += 1
            slider_cases[f'{name}-{ori}'] = count
    results['slider_cases'] = slider_cases
    # The drawing hooks enter the slider mode on a slider page, with one full clear.
    for kind in pu.SLIDER_VIEWS:
        patched.context(orientation=2, kind=kind)
        out = run(patched, 'clear', clear_site0, clear_stop0, rect_bytes(100, 400, 40, 20), [DESC] + [0] * 12)
        got_mode = patched.runtime()
        if (struct.unpack('<4i', out['desc'][:16]), got_mode) != ((0, 0, 720, 480), (2 | pu.SLIDER_MODE, 0)):
            failures.append(dict(test='slider_enter', kind=kind, box=struct.unpack('<4i', out['desc'][:16]), runtime=got_mode))

    # 2e. Touch at entry C: sequences of events against touch_map; inert where it must be.
    touch_stop = pu.TOUCH + len(pu.TOUCH_EXPECTED)
    touch_at = STATE + control['state_offset'] + control['touch_offset']
    def event(X, Y, flag, kind):
        e = bytearray(60)
        struct.pack_into('<I', e, 0, 1)
        e[8], e[9] = flag, kind
        struct.pack_into('<2H', e, 0x12, X, Y)
        return bytes(e)
    live_rows, menu_rows, slider_rows = pu.touch_tables()
    def landscape_point(ori, px, py):   # inverse of portrait_point
        return (py, 480 - px) if ori == 2 else (720 - py, px)
    def strokes(ori, mode):
        """Presses on every control (portrait), on the empty landscape places, on the picture; with drags."""
        points = []
        rows = menu_rows if mode & pu.MENU_MODE else slider_rows if mode & pu.SLIDER_MODE else live_rows
        for kind, a0, a1, a2, a3, _, _ in rows:
            if kind == pu.T_MAPPED:
                points.append(landscape_point(ori, (a0 + a2) // 2, (a1 + a3) // 2))
                points.append(landscape_point(ori, a0, a1))
                points.append(landscape_point(ori, a2 - 1, a3 - 1))
            else:
                points.append(((a0 + a2) // 2, (a1 + a3) // 2))
        points += [(rng.randrange(0, 720), rng.randrange(0, 480)) for _ in range(24)]
        out = []
        for X, Y in points:
            seq = [event(X, Y, 1, 1)]
            for _ in range(rng.randrange(0, 4)):
                X, Y = max(0, min(719, X + rng.randrange(-40, 41))), max(0, min(479, Y + rng.randrange(-40, 41)))
                seq.append(event(X, Y, 1, rng.choice((2, 2, 4))))
            seq.append(event(X, Y, 0, rng.choice((1, 1, 3))))
            out.append(seq)
        out.append([event(300, 200, 1, 1), event(310, 200, 0, 9)])   # stock cancel mid-touch
        return out
    touch_cases = dict(mapped=0, inert=0)
    for ori in (1, 2):
        for mode_bits, state in ((0, dict()), (pu.MENU_MODE, dict(kind=pu.MENU_VIEW)),
                                 (pu.MINI_MODE, dict(kind=pu.MENU_VIEW, nav_state=3, nav_count=7)),
                                 (pu.SLIDER_MODE, dict(kind=11)), (pu.SLIDER_MODE, dict(kind=13))):
            mode = ori | mode_bits
            for seq in strokes(ori, mode):
                patched.context(orientation=ori, **state)
                patched.u.mem_write(STATE + control['state_offset'] + pu.R_MODE, struct.pack('<I', mode))
                tstate, ox, oy = pu.T_NONE, 0, 0
                for i, ev in enumerate(seq):
                    regs = [DESC] + [rng.getrandbits(32) for _ in range(12)]
                    patched.u.mem_write(DESC, ev + bytes(0x60 - len(ev)))
                    out = patched.run(pu.TOUCH, touch_stop, regs, pushed=28)
                    want, tstate, ox, oy = pu.touch_map(mode, tstate, ox, oy, ev)
                    got = out['desc'][:60]
                    header = struct.unpack('<2I', patched.u.mem_read(touch_at + 4, 8))
                    record = struct.unpack('<4H4BI', patched.u.mem_read(touch_at + 16 + ((header[0] - 1) % pu.TCH_CAPACITY) * 16, 16))
                    want_record = (*struct.unpack_from('<2H', ev, 0x12), *struct.unpack_from('<2H', want, 0x12),
                                   ev[9], ev[8], state.get('kind', 0), tstate, mode)
                    if out['pc'] != touch_stop or got != want or out['regs'][0] != DESC or out['regs'][1:4] != regs[1:4] \
                            or header[1] != tstate or record != want_record:
                        failures.append(dict(test='touch', ori=ori, mode=mode, step=i, event=ev[8:0x16].hex(),
                                             got=got[8:0x16].hex(), want=want[8:0x16].hex(), state=header[1],
                                             want_state=tstate, record=record, want_record=want_record))
                        break
                    touch_cases['mapped'] += 1
    # Inert: switch off, no state, level grip (R_MODE 0): the event and registers stay stock.
    for label, state, mode in (('switch_off', dict(switch=0, orientation=1), 1), ('no_state', dict(present=False, orientation=2), 2),
                               ('level', dict(orientation=0), 0), ('setting_level', dict(kind=3, nav_state=3, orientation=0), 0)):
        for _ in range(24):
            ev = event(rng.randrange(0, 720), rng.randrange(0, 480), rng.choice((0, 1)), rng.choice((1, 2, 3, 4, 9)))
            regs = [DESC] + [rng.getrandbits(32) for _ in range(12)]
            stack = bytes(rng.getrandbits(8) for _ in range(64))
            outs = []
            for machine in (patched, original):
                machine.context(**state)
                if machine is patched and state.get('present', True):
                    machine.u.mem_write(STATE + control['state_offset'] + pu.R_MODE, struct.pack('<I', mode))
                machine.u.mem_write(DESC, ev + bytes(0x60 - len(ev)))
                outs.append(machine.run(pu.TOUCH, touch_stop, regs, stack=stack, pushed=28))
            mine, theirs = outs
            if any(mine[k] != theirs[k] for k in ('pc', 'regs', 'flags', 'stack', 'desc')):
                failures.append(dict(test='touch_inert', state=label, diff=[k for k in ('pc', 'regs', 'flags', 'stack', 'desc') if mine[k] != theirs[k]]))
            if label in ('switch_off', 'no_state', 'level') and patched.u.mem_read(touch_at + 4, 4) != bytes(4):
                failures.append(dict(test='touch_recorded_while_inert', state=label))
            touch_cases['inert'] += 1
    results['touch_cases'] = touch_cases

    # 2f. Setting-page keys: in the 2/3 mode the action entry turns direction codes with the screen.
    action_site = pu.EVENT_HOOKS[0][1]
    action_resume = action_site + len(bytes.fromhex(pu.EVENT_HOOKS[0][2]))
    seen_codes = []
    def code_seen(mu, address, size, data):
        seen_codes.append(mu.reg_read(UC_ARM_REG_R1))
        mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
    hook = patched.u.hook_add(UC_HOOK_CODE, code_seen, begin=action_resume, end=action_resume)
    key_turn = 0
    for ori in (1, 2):
        for label, state, mode, turned in (('setting_page', dict(kind=3, nav_state=3, nav_count=7), ori | pu.MINI_MODE, True),
                                           ('grid', dict(kind=3), ori | pu.MENU_MODE, False),
                                           ('live', dict(kind=0), ori, False),
                                           ('slider', dict(kind=12), ori | pu.SLIDER_MODE, True),
                                           ('slider_stale', dict(kind=0), ori | pu.SLIDER_MODE, False),
                                           ('stale_mode', dict(kind=3, nav_state=0), ori | pu.MINI_MODE, False)):
            for code in (0, 2, 3, 4, 5, 6, 0x13, 0x18):
                patched.context(orientation=ori, **state)
                patched.u.mem_write(pu.UI_TABLE, bytes(0x100))
                patched.u.mem_write(STATE + control['state_offset'] + pu.R_MODE, struct.pack('<I', mode))
                seen_codes.clear()
                patched.run(action_site, SENTINEL_LR & ~1, [code] + [0] * 12)
                want = pu.turn_code(ori, code) if turned else code
                if seen_codes != [want]:
                    failures.append(dict(test='setting_keys', ori=ori, state=label, code=code, seen=seen_codes, want=want))
                key_turn += 1
    patched.u.hook_del(hook)
    results['setting_key_cases'] = key_turn

    # 2g. Histogram: the stock one hidden (masks) and our own drawn (portrait_hist), against hist_prims.
    hist = report['features']['portrait_ui']['histogram']
    hfun = report['features']['portrait_ui']['functions']
    hst_at = STATE + control['state_offset'] + control['histogram_offset']
    raw = {int(hist['raw_rect'], 16) & ~1: 'rect', int(hist['raw_clear'], 16) & ~1: 'clear'}
    prims = []
    def raw_call(mu, address, size, data):
        d = bytes(mu.mem_read(mu.reg_read(UC_ARM_REG_R0), 28))
        x, y, w, h, colour, z1, z2 = struct.unpack('<4iIII', d)
        prims.append((raw[address], (x, y, w, h), colour, z1, z2))
        mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
    raw_hooks = [patched.u.hook_add(UC_HOOK_CODE, raw_call, begin=a, end=a) for a in raw]
    blends = []
    BLEND_BEFORE = 0x2003   # whatever the stock had set: it must come back
    def vg_call(mu, address, size, data):
        if address == pu.VG_GETI:
            blends.append(('get', mu.reg_read(UC_ARM_REG_R0)))
            mu.reg_write(UC_ARM_REG_R0, BLEND_BEFORE)
        else:
            blends.append(('set', mu.reg_read(UC_ARM_REG_R0), mu.reg_read(UC_ARM_REG_R1)))
        mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
    raw_hooks += [patched.u.hook_add(UC_HOOK_CODE, vg_call, begin=a, end=a) for a in (pu.VG_GETI, pu.VG_SETI)]
    blend_pair = [('get', pu.VG_BLEND_MODE), ('set', pu.VG_BLEND_MODE, pu.VG_BLEND_SRC), ('set', pu.VG_BLEND_MODE, BLEND_BEFORE)]
    stock_masks = struct.pack('<9I', *([0xCCC] * 9))
    pages = hist['pages']
    off_pages = [d for d in (0, 1, 2, 3, 4, 5, 6) if d not in pages]
    def hist_state(ori, disp=None, mode_bits=0, saved=True, clock=1000):
        disp = rng.choice(pages) if disp is None else disp
        patched.context(orientation=ori)
        patched.u.mem_write(STATE + control['state_offset'] + pu.R_MODE, struct.pack('<I', ori | mode_bits))
        patched.u.mem_write(pu.HIST_DISP, bytes([disp]))
        patched.u.mem_write(pu.HIST_MASKS, stock_masks)
        patched.u.mem_write(pu.MS_CLOCK, struct.pack('<I', clock))
        if saved:
            patched.u.mem_write(hst_at + pu.H_SAVED, struct.pack('<I', 1))
    def hword(offset):
        return struct.unpack('<I', patched.u.mem_read(hst_at + offset, 4))[0]
    def call_hist(ori, box=(0, 0, 0, 0)):
        prims.clear()
        blends.clear()
        patched.u.mem_write(DESC, struct.pack('<4i', *box))
        out = patched.run(int(hfun['portrait_hist'], 16) & ~1, SENTINEL_LR & ~1,
                          [STATE + control['state_offset'], ori, DESC] + [rng.getrandbits(32) for _ in range(10)])
        return out, [(fn, box, colour) for fn, box, colour, _, _ in prims], [p[3:] for p in prims]
    def set_bins(bins):
        patched.u.mem_write(pu.HIST_BINS, struct.pack('<256I', *bins))
    def random_bins():
        kind = rng.choice(('flat', 'dark', 'bright', 'spike', 'zero', 'random'))
        if kind == 'zero':
            return [0] * 256
        if kind == 'random':
            return [rng.randrange(0, 5000) for _ in range(256)]
        centre = dict(flat=128, dark=8, bright=247, spike=rng.randrange(256))[kind]
        width = 4 if kind == 'spike' else 40
        return [max(0, int(3000 * (1 - abs(i - centre) / width))) + rng.randrange(0, 50) for i in range(256)]
    hist_cases = dict(full=0, partial=0, throttled=0, overlap=0, erase=0, masks=0, end_to_end=0)
    for ori in (1, 2):
        for _ in range(a.cases // 6):
            # First draw: full.
            hist_state(ori, clock=rng.randrange(0, 1 << 30))
            bins = random_bins()
            set_bins(bins)
            out, got, extra = call_hist(ori)
            bars = pu.hist_bars(bins)
            want = pu.hist_prims(ori, bars, None, True)
            stored = list(patched.u.mem_read(hst_at + pu.H_PREV, 64))
            if (out['pc'] | 1) != SENTINEL_LR or got != want or any(e != (0, 0) for e in extra) or stored != bars or blends != blend_pair \
                    or hword(pu.H_ON) != ori or out['regs'][4:12] != [out['regs'][i] for i in range(4, 12)]:
                failures.append(dict(test='hist_full', ori=ori, got=got[:6], want=want[:6], stored=stored[:8], bars=bars[:8]))
            hist_cases['full'] += 1
            # Within the interval: nothing.
            clock = struct.unpack('<I', patched.u.mem_read(pu.MS_CLOCK, 4))[0]
            patched.u.mem_write(pu.MS_CLOCK, struct.pack('<I', (clock + pu.HIST_INTERVAL - 1) & 0xFFFFFFFF))
            set_bins(random_bins())
            _, got, _ = call_hist(ori)
            if got or blends:
                failures.append(dict(test='hist_throttle', ori=ori, got=got[:4], blends=blends))
            hist_cases['throttled'] += 1
            # After it: only the changed bars.
            patched.u.mem_write(pu.MS_CLOCK, struct.pack('<I', (clock + pu.HIST_INTERVAL) & 0xFFFFFFFF))
            new = list(bins)
            for i in rng.sample(range(256), rng.randrange(0, 40)):
                new[i] = rng.randrange(0, 6000)
            set_bins(new)
            _, got, _ = call_hist(ori)
            want = pu.hist_prims(ori, pu.hist_bars(new), bars, False)
            if got != want or blends != blend_pair:
                failures.append(dict(test='hist_partial', ori=ori, got=got[:6], want=want[:6]))
            hist_cases['partial'] += 1
            # A stock clear over the box: nothing now, then a full draw.
            own = pu.hist_box(ori, pu.HIST_X, pu.HIST_Y, pu.HIST_W, pu.HIST_H)
            cover = (own[0] + rng.randrange(-20, own[2]), own[1] + rng.randrange(-20, own[3]), rng.randrange(21, 80), rng.randrange(21, 80))
            patched.u.mem_write(pu.MS_CLOCK, struct.pack('<I', (clock + 10 * pu.HIST_INTERVAL) & 0xFFFFFFFF))
            _, got, _ = call_hist(ori, cover)
            got = got + blends
            dirty = hword(pu.H_DIRTY)
            _, got2, _ = call_hist(ori, (0, 0, 10, 10) if ori == 1 else (700, 470, 10, 10))
            want2 = pu.hist_prims(ori, pu.hist_bars(new), None, True)
            if got or dirty != 1 or got2 != want2 or hword(pu.H_DIRTY) != 0:
                failures.append(dict(test='hist_overlap', ori=ori, cover=cover, got=got[:3], dirty=dirty, got2=got2[:3]))
            hist_cases['overlap'] += 1
            # Off the histogram page (or a menu, setting or slider layout): erased once, then nothing.
            disp, bits = rng.choice([(d, 0) for d in off_pages] + [(rng.choice(pages), b) for b in (pu.MENU_MODE, pu.MINI_MODE, pu.SLIDER_MODE)])
            patched.u.mem_write(pu.HIST_DISP, bytes([disp]))
            patched.u.mem_write(STATE + control['state_offset'] + pu.R_MODE, struct.pack('<I', ori | bits))
            _, got, _ = call_hist(ori)
            erase_blends = list(blends)
            _, got2, _ = call_hist(ori)
            got2 = got2 + blends
            want = [('rect', own, 0)]
            if got != want or erase_blends != blend_pair or got2 or hword(pu.H_ON) != 0:
                failures.append(dict(test='hist_erase', ori=ori, disp=disp, bits=bits, got=got, got2=got2))
            hist_cases['erase'] += 1
    # Masks: hidden only while switched on, upright, in the live-view layout and on DISP 2; restored otherwise.
    mask_states = [(dict(orientation=1 + i % 2), 1 + i % 2, page, True) for i, page in enumerate(pages)]
    mask_states += [(dict(orientation=1), 1, page, False) for page in off_pages]
    mask_states += [(dict(orientation=0), 0, 2, False), (dict(orientation=2), 2 | pu.MENU_MODE, 2, False),
                    (dict(orientation=2), 2 | pu.SLIDER_MODE, 2, False), (dict(orientation=1, switch=0), 1, 2, False)]
    for state, mode, disp, hidden in mask_states:
        for start_saved in (False, True):
            patched.context(**state)
            patched.u.mem_write(STATE + control['state_offset'] + pu.R_MODE, struct.pack('<I', mode))
            patched.u.mem_write(pu.HIST_DISP, bytes([disp]))
            original_masks = struct.pack('<9I', *[rng.getrandbits(24) for _ in range(9)])
            if start_saved:   # hidden earlier: masks zero, the saved copy in the block
                patched.u.mem_write(pu.HIST_MASKS, bytes(36))
                patched.u.mem_write(hst_at + pu.H_MASKS, original_masks)
                patched.u.mem_write(hst_at + pu.H_SAVED, struct.pack('<I', 1))
            else:
                patched.u.mem_write(pu.HIST_MASKS, original_masks)
            patched.run(int(hfun['portrait_hmask'], 16) & ~1, SENTINEL_LR & ~1, [STATE + control['state_offset']] + [0] * 12)
            masks = bytes(patched.u.mem_read(pu.HIST_MASKS, 36))
            want = bytes(36) if hidden else original_masks
            if masks != want or hword(pu.H_SAVED) != int(hidden) or (hidden and bytes(patched.u.mem_read(hst_at + pu.H_MASKS, 36)) != original_masks):
                failures.append(dict(test='hist_masks', state=state, mode=mode, disp=disp, start_saved=start_saved,
                                     masks=masks.hex(), saved=hword(pu.H_SAVED)))
            hist_cases['masks'] += 1
    # End to end through the clear entry: the hook hides the stock masks and draws on the same clear.
    for ori in (1, 2):
        patched.context(orientation=ori)
        patched.u.mem_write(STATE + control['state_offset'] + pu.R_MODE, struct.pack('<2I', ori, 0))
        patched.u.mem_write(pu.HIST_DISP, bytes([pages[-1]]))
        patched.u.mem_write(pu.HIST_MASKS, stock_masks)
        patched.u.mem_write(pu.MS_CLOCK, struct.pack('<I', 5000))
        bins = random_bins()
        set_bins(bins)
        prims.clear()
        clear_site1, clear_stop1 = next((s_, e_) for n_, s_, e_ in entries if n_ == 'clear')
        run(patched, 'clear', clear_site1, clear_stop1, rect_bytes(2, 4, 10, 10), [DESC] + [0] * 12)
        got = [(fn, box, colour) for fn, box, colour, _, _ in prims]
        if bytes(patched.u.mem_read(pu.HIST_MASKS, 36)) != bytes(36) or got != pu.hist_prims(ori, pu.hist_bars(bins), None, True):
            failures.append(dict(test='hist_end_to_end', ori=ori, masks=bytes(patched.u.mem_read(pu.HIST_MASKS, 36)).hex(), got=got[:4]))
        # Back to landscape: the masks come back on the next command.
        patched.orientation = 0
        run(patched, 'clear', clear_site1, clear_stop1, rect_bytes(2, 4, 10, 10), [DESC] + [0] * 12)
        run(patched, 'clear', clear_site1, clear_stop1, rect_bytes(2, 4, 10, 10), [DESC] + [0] * 12)
        if bytes(patched.u.mem_read(pu.HIST_MASKS, 36)) != stock_masks:
            failures.append(dict(test='hist_restore', ori=ori, masks=bytes(patched.u.mem_read(pu.HIST_MASKS, 36)).hex()))
        hist_cases['end_to_end'] += 1
    for h in raw_hooks:
        patched.u.hook_del(h)
    patched.u.mem_write(pu.HIST_DISP, bytes([0]))   # the later tests run with no histogram page
    results['histogram_cases'] = hist_cases
    results['histogram_pages'] = pages

    # 3. Image 3 goes through image 1 (and its hook) and is then skipped.
    image3 = 0
    _, site3, expected3, _ = next(h for h in pu.HOOKS if h[0] == 'image3')
    image1_resume = pu.IMAGE + 10
    for ori in (1, 2):
        for _ in range(a.cases // 2):
            data, regs = random_input('image3')
            patched.context(orientation=ori)
            patched.u.mem_write(STATE + control['state_offset'] + 16, struct.pack('<2I', ori, 0))
            seen = []
            def capture(mu, address, size, user):
                # Image 1 replayed its prologue: push {r4-r11,lr} (36 bytes), sub sp,#0xdc.
                pointer = mu.reg_read(UC_ARM_REG_R0)
                seen.append(bytes(mu.mem_read(pointer, 0x5C)))
                sp = mu.reg_read(UC_ARM_REG_SP) + 0xDC
                saved = struct.unpack('<9I', mu.mem_read(sp, 36))
                for i, v in enumerate(saved[:8]):
                    mu.reg_write(UC_ARM_REG_R4 + i, v)
                mu.reg_write(UC_ARM_REG_SP, sp + 36)
                mu.reg_write(UC_ARM_REG_PC, saved[8] | 1)
            hook = patched.u.hook_add(UC_HOOK_CODE, capture, begin=image1_resume, end=image1_resume)
            out = run(patched, 'image3', site3, SENTINEL_LR & ~1, data, regs)
            patched.u.hook_del(hook)
            x0, y0, x1, y1 = (v >> 16 for v in struct.unpack_from('<4i', data, 0x18))
            converted = bytearray(0x5C)
            converted[:0x18] = data[:0x18]
            struct.pack_into('<I', converted, 0x20, 0xFFFFFFFF)
            struct.pack_into('<4h', converted, 0x18, x0, y0, x1, y1)
            converted[0x24:0x28] = data[0x2C:0x30]
            converted[0x2C:0x30] = data[0x28:0x2C]
            converted[0x44:0x46] = data[0x30:0x32]
            converted[0x48:0x54] = data[0x34:0x40]
            want = model_image(ori, 'image', bytes(converted))
            if len(seen) != 1 or seen[0] != want or (out['pc'] | 1) != SENTINEL_LR:
                failures.append(dict(test='image3', ori=ori, input=data.hex(), seen=[s.hex() for s in seen],
                                     want=want.hex(), pc=hex(out['pc'])))
            image3 += 1
    results['image3_cases'] = image3

    # 4. One full clear after the layout changes, only in photo live view.
    lifecycle = []
    clear_site, clear_stop = next((s, e) for n, s, e in entries if n == 'clear')
    def clear(machine, **state):
        machine.context(**state) if state else None
        out = run(machine, 'clear', clear_site, clear_stop, rect_bytes(100, 400, 40, 20), [DESC] + [0] * 12)
        return struct.unpack('<4i', out['desc'][:16]), machine.runtime()
    patched.context(orientation=1)
    steps = [clear(patched)]
    steps.append(clear(patched))
    patched.orientation = 0
    steps.append(clear(patched))
    steps.append(clear(patched))
    patched.orientation = 2
    patched.u.mem_write(pu.VIEW_KIND, bytes([3]))
    steps.append(clear(patched))
    patched.u.mem_write(pu.VIEW_KIND, bytes([0]))
    steps.append(clear(patched))
    # Upright again, now in the i menu (view kind 3): its own layout, one full clear; back to live view: another.
    expect = [((0, 0, 720, 480), (1, 0)), (model_box(1), (1, 0)), ((0, 0, 720, 480), (0, 0)),
              ((100, 400, 40, 20), (0, 0)), ((0, 0, 720, 480), (2 | pu.MENU_MODE, 0)), ((0, 0, 720, 480), (2, 0))]
    for i, (got, want) in enumerate(zip(steps, expect)):
        lifecycle.append(dict(step=i, box=got[0], runtime=got[1]))
        if (tuple(got[0]), tuple(got[1])) != (tuple(want[0]), tuple(want[1])):
            failures.append(dict(test='lifecycle', step=i, got=[list(got[0]), list(got[1])], want=[list(want[0]), list(want[1])]))
    results['lifecycle'] = lifecycle

    # 5. The clear entry's own rectangle call is moved once, by the clear hook.
    patched.context(orientation=2)
    patched.u.mem_write(STATE + control['state_offset'] + 16, struct.pack('<2I', 2, 0))
    rect_site, rect_stop = next((s, e) for n, s, e in entries if n == 'rect')
    data = rect_bytes(500, 200, 60, 30)
    out = run(patched, 'rect', rect_site, rect_stop, data, [DESC] + [0] * 12)
    plain = out['desc'][:16]
    patched.u.mem_write(DESC, data + bytes(0x60))
    stack = bytearray(64)
    regs = [DESC] + [0] * 12
    patched.u.mem_write(STACK, bytes(0x100000))
    for r, v in zip(REGS[:13], regs):
        patched.u.reg_write(r, v)
    patched.u.reg_write(UC_ARM_REG_SP, STACK_TOP - 64)
    patched.u.reg_write(UC_ARM_REG_LR, pu.CLEAR_INNER_RECT_RETURN)
    patched.u.emu_start(rect_site | 1, rect_stop, count=20000)
    inner = bytes(patched.u.mem_read(DESC, 16))
    results['clear_inner_rect'] = dict(direct_moved=plain != data[:16], inner_unchanged=inner == data[:16])
    if not (plain != data[:16] and inner == data[:16]):
        failures.append(dict(test='clear_inner_rect', plain=plain.hex(), inner=inner.hex()))

    # 7. The GUI-task repaint: stock request unless a layout change is waiting, then mask 7 once.
    site, stop = pu.REFRESH_REQUEST, pu.REFRESH_REQUEST + len(pu.REFRESH_EXPECTED)
    repaint = []
    repaint_states = (('switch_off', dict(switch=0), 1), ('no_state', dict(present=False), 1),
                      ('nothing_waiting', {}, 0), ('waiting', {}, 1))
    if not pu.REPAINT_HOOK:
        # The refresh request must be byte-for-byte stock in this build.
        if image[site - BASE:stop - BASE] != pu.REFRESH_EXPECTED:
            failures.append(dict(test='repaint_site_not_stock'))
        repaint_states = ()
    for label, state, wanted in repaint_states:
        for mask in (1, 2, 4, 7):
            regs = [rng.getrandbits(32) for _ in range(13)]
            regs[2] = mask
            outs = []
            for machine in (patched, original):
                machine.context(**state)
                if machine is patched and state.get('present', True):
                    machine.u.mem_write(STATE + control['state_offset'] + pu.R_REPAINT, struct.pack('<I', wanted))
                out = machine.run(site, stop, regs)
                outs.append((out, list(machine.requests)))
            (mine, mine_requests), (theirs, theirs_requests) = outs
            expect = [(pu.REDRAW_PREDICATE, pu.REDRAW_ALL, 7) if label == 'waiting' else (pu.REDRAW_PREDICATE, 3, mask)]
            kept = (4, 5, 6, 7, 8, 9, 10, 11, 13) if label == 'waiting' else (2, 4, 5, 6, 7, 8, 9, 10, 11, 13)
            same_regs = [mine['regs'][i] == theirs['regs'][i] for i in kept]
            ok = mine['pc'] == stop and mine_requests == expect and theirs_requests == [(pu.REDRAW_PREDICATE, 3, mask)] and all(same_regs)
            if label in ('nothing_waiting', 'waiting'):
                waiting, passes, forced = patched.words(pu.R_REPAINT, 1)[0], *patched.words(pu.R_PASSES, 2)
                ok = ok and (waiting, passes, forced) == (0, 1, int(label == 'waiting'))
            if not ok:
                failures.append(dict(test='repaint', state=label, mask=mask, requests=[list(map(hex, r)) for r in mine_requests]))
            repaint.append(dict(state=label, mask=mask, passed=ok))
    results['repaint_cases'] = len(repaint)

    # 8. The readout fills only while upright; a layout change asks for one repaint.
    patched.context(orientation=1, kind=0, target=2)
    run(patched, 'rect', rect_site, rect_stop, rect_bytes(10, 10, 20, 20), [DESC] + [0] * 12)
    patched.u.mem_write(pu.VIEW_KIND, bytes([3]))
    patched.u.mem_write(pu.OVERLAY_BUSY[0], struct.pack('<I', 5))
    run(patched, 'rect', rect_site, rect_stop, rect_bytes(10, 10, 20, 20), [DESC] + [0] * 12)
    kinds, targets, phases, busy = patched.words(pu.R_KINDS, 4)
    mode, pending, wanted = patched.words(pu.R_MODE, 3)
    readout = dict(kinds=hex(kinds), targets=hex(targets), phases=hex(phases), busy=hex(busy), mode=mode, repaint=wanted)
    if (kinds, targets, phases, busy, wanted) != (0b1001, 0b100, 0b100, 0b100, 1):
        failures.append(dict(test='readout', **readout))
    patched.context(orientation=0)
    run(patched, 'rect', rect_site, rect_stop, rect_bytes(10, 10, 20, 20), [DESC] + [0] * 12)
    if patched.words(pu.R_KINDS, 1)[0]:
        failures.append(dict(test='readout_level', kinds=patched.words(pu.R_KINDS, 1)[0]))
    results['readout'] = readout

    # 9. The draw log keeps the landscape box of each command while upright, and pauses while frozen.
    patched.context(orientation=2, kind=0, target=2)
    patched.u.mem_write(STATE + control['state_offset'] + pu.R_MODE, struct.pack('<2I', 2, 0))
    entries_by_name = {n: (s, e) for n, s, e in entries}
    samples = [('rect', rect_bytes(40, 400, 30, 20), [DESC] + [0] * 12, (pu.KINDS['rect'], 40, 60, 30, 20)),
               ('clear', rect_bytes(100, 200, 50, 40), [DESC] + [0] * 12, (pu.KINDS['clear'], 100, 240, 50, 40)),
               ('frame', b'', [300, 100, 80, 40] + [0] * 9, (pu.KINDS['frame'], 300, 340, 80, 40)),
               ('image', image_bytes(600, 430, 640, 466, 40, 36, mode=1, angle=360), [DESC] + [0] * 12,
                (pu.KINDS['image'], 600, 430, 40, 36)),
               ('text', struct.pack('<9I', f32(20.0), 0, 0, 0, f32(20.0), 0, f32(150.0), f32(30.0), f32(1)),
                [DESC] + [0] * 12, (pu.KINDS['text'], 150, 450, 20, 20))]
    log = STATE + control['state_offset'] + pu.LOG
    for name, data, regs, _ in samples:
        site, stop = entries_by_name[name]
        run(patched, name, site, stop, data, regs)
    magic, count, frozen, capacity = struct.unpack('<4I', patched.u.mem_read(log, 16))
    records = [struct.unpack('<4B4hI', patched.u.mem_read(log + 16 + 16 * i, 16)) for i in range(count)]
    got = [(r[0], *r[4:8]) for r in records]
    want = [s[3] for s in samples]
    if (magic, count, capacity) != (pu.LOG_MAGIC, len(samples), pu.LOG_CAPACITY) or got != want or \
            any(r[1:4] != (2, 0, 2) for r in records):
        failures.append(dict(test='log', header=[hex(magic), count, capacity], got=got, want=want))
    patched.u.mem_write(log + 8, struct.pack('<I', 1))
    run(patched, 'rect', *entries_by_name['rect'], rect_bytes(1, 1, 1, 1), [DESC] + [0] * 12)
    if struct.unpack('<I', patched.u.mem_read(log + 4, 4))[0] != len(samples):
        failures.append(dict(test='log_frozen'))
    results['log'] = dict(records=got, header=[hex(magic), count, capacity])

    # 10. Redraw-request trace: stock registers at the resume point; records only while switched on.
    site, stop = pu.REDRAW, pu.REDRAW + len(pu.REDRAW_EXPECTED)
    trace = 0
    requests_at = STATE + control['state_offset'] + control['request_offset']
    for machine in (patched, original):
        machine.intercept_redraw = False
    for label, state in (('switch_off', dict(switch=0)), ('no_state', dict(present=False)), ('on', {}), ('on_upright', dict(orientation=1))):
        for _ in range(16):
            regs = [rng.getrandbits(32) for _ in range(13)]
            stack = bytes(rng.getrandbits(8) for _ in range(64))
            outs = []
            for machine in (patched, original):
                machine.context(**state)
                outs.append(machine.run(site, stop, regs, stack=stack, pushed=16))
            mine, theirs = outs
            if any(mine[key] != theirs[key] for key in ('pc', 'regs', 'flags', 'stack')):
                failures.append(dict(test='request_trace_inert', state=label))
            if label.startswith('on'):
                magic, count = struct.unpack('<2I', patched.u.mem_read(requests_at, 8))
                record = struct.unpack('<4I', patched.u.mem_read(requests_at + 16 + ((count - 1) % pu.REQ_CAPACITY) * 16, 16))
                if magic != pu.REQ_MAGIC or record != (SENTINEL_LR, regs[0], regs[1], regs[2]):
                    failures.append(dict(test='request_trace_record', state=label, record=[hex(v) for v in record]))
            elif patched.u.mem_read(requests_at, 8) != bytes(8):
                failures.append(dict(test='request_trace_wrote_while_off', state=label))
            trace += 1
    # The periodic refresh caller is only counted.
    patched.context()
    for _ in range(3):
        patched.run(site, stop, [0] * 13, pushed=16, lr=pu.PERIODIC_CALLER)
    magic, count, periodic = struct.unpack('<3I', patched.u.mem_read(requests_at, 12))
    if (count, periodic) != (0, 3):
        failures.append(dict(test='request_trace_periodic', count=count, periodic=periodic))
    trace += 3
    for machine in (patched, original):
        machine.intercept_redraw = True
    results['request_trace_cases'] = trace

    # 11. UI event trace: stock registers at the resume point; records only while switched on.
    events_at = STATE + control['state_offset'] + control['event_offset']
    event_cases = 0
    for number, (name, site, expected, _) in enumerate(pu.EVENT_HOOKS):
        stop = site + len(bytes.fromhex(expected))
        pushed = 8 if name == 'update' else 0
        for label, state in (('switch_off', dict(switch=0)), ('no_state', dict(present=False)), ('on', {})):
            for _ in range(12):
                regs = [rng.getrandbits(32) for _ in range(13)]
                stack = bytes(rng.getrandbits(8) for _ in range(64))
                table = bytes(rng.getrandbits(8) for _ in range(0x100))
                ui = bytes([rng.randrange(0, 16)])
                outs = []
                for machine in (patched, original):
                    machine.context(**state)
                    machine.u.mem_write(pu.UI_TABLE, table)
                    machine.u.mem_write(pu.UI_STATE, ui)
                    outs.append(machine.run(site, stop, regs, stack=stack, pushed=pushed))
                mine, theirs = outs
                keys = ('pc', 'regs', 'flags', 'stack')
                if name == 'update':  # r12 and lr are scratch across the 103051A0 call
                    for o in outs:
                        o['regs'] = o['regs'][:12] + o['regs'][13:14]
                if any(mine[key] != theirs[key] for key in keys):
                    failures.append(dict(test='event_trace_inert', entry=name, state=label,
                                         diff=[key for key in keys if mine[key] != theirs[key]]))
                count, = struct.unpack('<I', patched.u.mem_read(events_at + 4, 4))
                if label == 'on':
                    record = struct.unpack('<4I', patched.u.mem_read(events_at + 16 + ((count - 1) % pu.EVT_CAPACITY) * 16, 16))
                    if count != 1 or record[0] & 255 != number or record[1:] != (SENTINEL_LR, regs[0], regs[1]):
                        failures.append(dict(test='event_trace_record', entry=name, record=[hex(v) for v in record]))
                elif count:
                    failures.append(dict(test='event_trace_wrote_while_off', entry=name, state=label))
                event_cases += 1
    results['event_trace_cases'] = event_cases

    # 12. Repaint injection from the periodic update entry: after a layout change
    # (R_REPAINT) and the full clear (R_PENDING 0), in photo live view and not while
    # the user half-presses, dispatch 0x13 then 0x00 through the action entry, once.
    action_site = pu.EVENT_HOOKS[0][1]
    action_resume = action_site + len(bytes.fromhex(pu.EVENT_HOOKS[0][2]))
    update_site = pu.EVENT_HOOKS[1][1]
    update_resume = update_site + len(bytes.fromhex(pu.EVENT_HOOKS[1][2]))
    dispatched = []
    def handler(mu, address, size, data):
        dispatched.append(mu.reg_read(UC_ARM_REG_R1))
        mu.reg_write(UC_ARM_REG_PC, mu.reg_read(UC_ARM_REG_LR))
    hook = patched.u.hook_add(UC_HOOK_CODE, handler, begin=action_resume, end=action_resume)
    inject_cases = []
    base = STATE + control['state_offset']
    # Held press: first update after the turn presses, HOLD_UPDATES later it releases.
    # With INJECT_HALF_PRESS off, the update entry never dispatches anything.
    patched.context()
    patched.u.mem_write(pu.UI_TABLE, bytes(0x100))
    patched.u.mem_write(base + pu.R_PENDING, struct.pack('<2I', 0, 1))
    patched.u.mem_write(events_at, struct.pack('<4I', pu.EVT_MAGIC, 0, 0, 0))
    sequence = []
    for tick in range(pu.HOLD_UPDATES + 3):
        dispatched.clear()
        patched.run(update_site, update_resume, [0] * 13, pushed=8)
        sequence.append([hex(v) for v in dispatched])
    want_sequence = ([['0x13']] + [[]] * (pu.HOLD_UPDATES - 1) + [['0x0']] + [[], []]
                     if pu.INJECT_HALF_PRESS else [[]] * (pu.HOLD_UPDATES + 3))
    injected = struct.unpack('<I', patched.u.mem_read(events_at + 12, 4))[0]
    held = struct.unpack('<I', patched.u.mem_read(events_at + 8, 4))[0]
    passes = 1 if pu.INJECT_HALF_PRESS or not pu.REPAINT_BY_MARKING else (2 if pu.MARK_RECORDED else 1)  # no background flag left: the wait ends at once
    ok = sequence == want_sequence and injected == passes and held == 0  # marking: background, then foreground pass
    if not ok:
        failures.append(dict(test='inject_hold', sequence=sequence, injected=injected, held=held))
    inject_cases.append(dict(case='after_turn_held', sequence=sequence, passed=ok))
    for label, state, repaint, pending, held, want in (
            ('after_turn', {}, 1, 0, 0, [0x13, 0x00]),
            ('nothing_changed', {}, 0, 0, 0, []),
            ('clear_not_done', {}, 1, 1, 0, []),
            ('user_half_pressing', {}, 1, 0, 1, []),
            ('switch_off', dict(switch=0), 1, 0, 0, []),
            ('menu', dict(kind=3), 1, 0, 0, []),
            ('playback', dict(phase=4), 1, 0, 0, [])):
        patched.context(**state)
        patched.u.mem_write(pu.UI_TABLE, bytes(0x100))
        patched.u.mem_write(base + pu.R_PENDING, struct.pack('<2I', pending, repaint))
        if state.get('switch', 1):
            patched.u.mem_write(events_at, struct.pack('<4I', pu.EVT_MAGIC, 0, held, 0))
        dispatched.clear()
        patched.refreshes.clear()
        widgets_at = base + control['widget_offset']
        fake = [0xC6100000 + 0x100 * i for i in range(4)]
        patched.u.mem_write(0xC6100000, bytes(0x2000))
        # widgets 0 and 1 share a box (1 drawn later), 2 and 3 have their own boxes
        for w, box in zip(fake, ((10, 10, 40, 40), (10, 10, 40, 40), (50, 50, 80, 80), (90, 10, 120, 40))):
            patched.u.mem_write(w + 0x10, struct.pack('<4h', *box))
        patched.u.mem_write(fake[3] + 0x4C, struct.pack('<I', pu.BACKGROUND_DRAW))  # a background widget
        stamps = (1, 5, 2, 3)
        patched.u.mem_write(widgets_at, struct.pack('<2I', len(fake), 9) + bytes(8)
                            + b''.join(struct.pack('<2I', w, s) for w, s in zip(fake, stamps)))
        out = patched.run(update_site, update_resume, [0] * 13, pushed=8)
        flags = [struct.unpack('<I', patched.u.mem_read(w + 0x44, 4))[0] for w in fake]
        kept = struct.unpack('<I', patched.u.mem_read(widgets_at, 4))[0]
        injected = struct.unpack('<I', patched.u.mem_read(events_at + 12, 4))[0]
        left = struct.unpack('<I', patched.u.mem_read(base + pu.R_REPAINT, 4))[0]
        refreshed = list(patched.refreshes)
        patched.refreshes.clear()
        want_refresh = []
        if not pu.INJECT_HALF_PRESS:
            if want and pu.REPAINT_BY_MARKING and not pu.MARK_RECORDED:
                want_refresh = [(pu.MARK_ALL, pu.LIVE_CONTAINER)] + (
                    [(pu.REDRAW_NOW, pu.LIVE_CONTAINER, pu.REDRAW_CALLBACK)] if pu.REDRAW_IMMEDIATELY else [])
            fired = bool(want)
            want = []
        else:
            fired = bool(want)
        got_refresh = [(a, r0) if a == pu.MARK_ALL else (a, r0, r1) for a, r0, r1 in refreshed]
        ok = (dispatched == want and got_refresh == want_refresh and out['pc'] == update_resume
              and injected == int(fired) and left == (0 if fired else repaint))
        if pu.MARK_RECORDED:
            # first pass: only the background widget; left counts down to the second pass
            ok = (dispatched == want and got_refresh == want_refresh and out['pc'] == update_resume
                  and injected == int(fired) and left == (2 + pu.PASS_GAP if fired else repaint)
                  and flags == [0, 0, 0, int(fired)]
                  and kept == (0 if state.get('kind', 0) and state.get('switch', 1) else 4))
            if fired:
                lefts = []
                for _ in range(pu.PASS_GAP + 1):
                    patched.run(update_site, update_resume, [0] * 13, pushed=8)
                    lefts.append(struct.unpack('<I', patched.u.mem_read(base + pu.R_REPAINT, 4))[0])
                flags = [struct.unpack('<I', patched.u.mem_read(w + 0x44, 4))[0] for w in fake]
                injected = struct.unpack('<I', patched.u.mem_read(events_at + 12, 4))[0]
                # widget 0 shares its box with the later-drawn widget 1: only 1 is current
                # the background flag is never cleared here, so the full count runs out
                ok = ok and lefts == list(range(1 + pu.PASS_GAP, 1, -1)) + [0] and flags == [0, 1, 1, 1] and injected == 2
                # once the traversal has drawn the background (flag cleared), the next update ends the wait
                for w in fake:
                    patched.u.mem_write(w + 0x44, bytes(4))
                patched.u.mem_write(base + pu.R_REPAINT, struct.pack('<I', 1))
                patched.run(update_site, update_resume, [0] * 13, pushed=8)
                waiting = [struct.unpack('<I', patched.u.mem_read(w + 0x44, 4))[0] for w in fake]
                patched.run(update_site, update_resume, [0] * 13, pushed=8)  # flag still set: keep waiting
                still = struct.unpack('<I', patched.u.mem_read(base + pu.R_REPAINT, 4))[0]
                patched.u.mem_write(fake[3] + 0x44, bytes(4))
                patched.run(update_site, update_resume, [0] * 13, pushed=8)
                early = struct.unpack('<I', patched.u.mem_read(base + pu.R_REPAINT, 4))[0]
                flags = [struct.unpack('<I', patched.u.mem_read(w + 0x44, 4))[0] for w in fake]
                ok = (ok and waiting == [0, 0, 0, 1] and still == 1 + pu.PASS_GAP
                      and early == 0 and flags == [0, 1, 1, 0])
        if not ok:
            failures.append(dict(test='inject', case=label, dispatched=[hex(v) for v in dispatched],
                                 refresh=[[hex(v) for v in r] for r in got_refresh], injected=injected, repaint_left=left))
        inject_cases.append(dict(case=label, dispatched=[hex(v) for v in dispatched], passed=ok))
    # The action entry itself never injects any more, also not on 0x49.
    for action, want_held in ((0x49, 0), (0x13, 1), (0x00, 0)):
        patched.context()
        patched.u.mem_write(events_at, struct.pack('<4I', pu.EVT_MAGIC, 0, 0, 0))
        dispatched.clear()
        patched.run(action_site, SENTINEL_LR & ~1, [action] + [0] * 12)
        held = struct.unpack('<I', patched.u.mem_read(events_at + 8, 4))[0]
        ok = dispatched == [action] and held == want_held
        if not ok:
            failures.append(dict(test='action_only', action=hex(action), dispatched=[hex(v) for v in dispatched]))
        inject_cases.append(dict(case=f'action_{action:#x}', dispatched=[hex(v) for v in dispatched], passed=ok))
    patched.u.hook_del(hook)
    results['inject_cases'] = inject_cases

    # 13. Redraw traversal trace: stock registers at the resume point; records only while switched on.
    site, stop = pu.TRAVERSE, pu.TRAVERSE + len(pu.TRAVERSE_EXPECTED)
    traversal_at = STATE + control['state_offset'] + control['traversal_offset']
    trv_cases = 0
    for label, state in (('switch_off', dict(switch=0)), ('no_state', dict(present=False)), ('on', {}), ('on_upright', dict(orientation=1))):
        for _ in range(12):
            regs = [rng.getrandbits(32) for _ in range(13)]
            stack = bytes(rng.getrandbits(8) for _ in range(64))
            outs = []
            for machine in (patched, original):
                machine.context(**state)
                outs.append(machine.run(site, stop, regs, stack=stack, pushed=32))
            mine, theirs = outs
            if any(mine[key] != theirs[key] for key in ('pc', 'regs', 'flags', 'stack')):
                failures.append(dict(test='traversal_trace_inert', state=label))
            count = struct.unpack('<I', patched.u.mem_read(traversal_at + 4, 4))[0]
            if label.startswith('on'):
                record = struct.unpack('<4I', patched.u.mem_read(traversal_at + 16 + ((count - 1) % pu.TRV_CAPACITY) * 16, 16))
                if count != 1 or record[:3] != (regs[0], regs[1], SENTINEL_LR):
                    failures.append(dict(test='traversal_trace_record', state=label, record=[hex(v) for v in record]))
            elif count:
                failures.append(dict(test='traversal_trace_wrote_while_off', state=label))
            trv_cases += 1
    results['traversal_trace_cases'] = trv_cases

    # 14. Draw-call record inside the traversal: stock registers for both cbz outcomes;
    # a live-view widget with a draw function is listed once, nothing else.
    if pu.MARK_RECORDED:
        widgets_at = STATE + control['state_offset'] + control['widget_offset']
        draw_cases = 0
        for label, state in (('switch_off', dict(switch=0)), ('on', {})):
            for container in (pu.LIVE_CONTAINER + 0x9C, 0xC6000000):
                for draw in (0, 0x10400001):
                    for repeat in range(2):
                        widget = 0xC6100400
                        regs = [rng.getrandbits(32) for _ in range(13)]
                        regs[4], regs[8] = widget, container
                        outs = []
                        for machine in (patched, original):
                            if repeat == 0:
                                machine.context(**state)
                            machine.u.mem_write(widget, bytes(0x60))
                            machine.u.mem_write(widget + 0x4C, struct.pack('<I', draw))
                            stop = pu.DRAW_CALL if draw else pu.DRAW_SKIP
                            outs.append(machine.run(pu.DRAW_SITE, stop, regs, stack=b'', pushed=0))
                        mine, theirs = outs
                        if any(mine[key] != theirs[key] for key in ('pc', 'regs', 'flags')):
                            failures.append(dict(test='draw_record_inert', state=label, container=hex(container), draw=hex(draw)))
                        count = struct.unpack('<I', patched.u.mem_read(widgets_at, 4))[0]
                        want = int(label == 'on' and container == pu.LIVE_CONTAINER + 0x9C and draw != 0)
                        listed, stamp = struct.unpack('<2I', patched.u.mem_read(widgets_at + 16, 8)) if count else (0, 0)
                        if count != want or (want and (listed != widget or stamp != 1 + repeat)):
                            failures.append(dict(test='draw_record_list', state=label, container=hex(container),
                                                 draw=hex(draw), repeat=repeat, count=count))
                        draw_cases += 1
        results['draw_record_cases'] = draw_cases

    # 6. No stock branch lands inside a displaced window.
    hits = incoming_branches(stock)
    results['incoming_branches'] = hits
    if hits:
        failures.append(dict(test='incoming', hits=hits))

    results['failures'] = failures[:50]
    results['failure_count'] = len(failures)
    results['passed'] = not failures
    results['camera_verified'] = False
    results['boundaries'] = ['orientation getter result is an input', 'vgLoadMatrix is not executed',
                             'renderer bodies after the displaced window are not executed',
                             'OSD target value 2 and the photo live-view context are assumptions shared with the Z 5 / aspect mask']
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(results, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({k: results[k] for k in ('inert_cases', 'portrait_cases', 'menu_cases', 'menu_key_cases', 'mini_cases',
                                              'touch_cases', 'setting_key_cases', 'image3_cases', 'repaint_cases', 'request_trace_cases', 'event_trace_cases', 'traversal_trace_cases', 'draw_record_cases', 'inject_cases', 'readout', 'log', 'failure_count', 'passed')}, indent=1))
    for failure in failures[:8]:
        print(json.dumps(failure)[:600])
    return 0 if not failures else 1


def model_box(ori):
    b = model_rect(ori, 'clear', rect_bytes(100, 400, 40, 20))
    return struct.unpack('<4i', b[:16])


if __name__ == '__main__':
    sys.exit(main())
