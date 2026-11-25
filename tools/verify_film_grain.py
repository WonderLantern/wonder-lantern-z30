"""Execute the film grain gate, hook and C payload under Unicorn.

Enters at the stock JPEG encode gateway 0x10096EE6 of the built image, as a
capture-context caller would, and compares the luma plane byte for byte with
z30_extensions.host.film_grain.grain_plane. The chroma plane must not change.

Boundaries replaced by the fixture: the extension menu state getter, the two
stock cache services (recorded, not modelled), and the CP15 reads the payload
makes (SCTLR, TTBCR, ATS1CPW/PAR), which Unicorn has no MMU for. The PAR model
is identity mapped with chosen attributes; real camera page tables, the real
encoder and the capture task are not involved, so this does not prove which
frames the camera sends through the gate, nor its timing.
"""
import argparse
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]

Y_BASE, C_BASE = 0x70000000, 0x74000000
DESCRIPTOR, ARGS, CTX, INFO, FAKE_STATE = 0x50000000, 0x50000100, 0x50000200, 0x50010000, 0x50020000
MS_CLOCK = 0xC18EB098
CLOCK = 0x0012D687
FLUSH, CLEAN = 0x10204B7C, 0x10204B0C
KIND_OFFSET = 0x228
# PAR attribute bits (short-descriptor format): inner [6:4], outer [3:2].
UNCACHED, CACHED, DEVICE = 0x000, 0x05 << 4 | 1 << 2, 0x03 << 4
BACKGROUND_C = 0x83


def background_y(stride, height):
    """Diagonal ramp through every luma value plus flat black and white bands (saturation)."""
    ramp = bytes(range(256)) * (stride // 256 + 2)
    rows = []
    for row in range(height):
        if row % 97 < 6:
            rows.append(bytes((0 if row % 2 else 255,)) * stride)
        else:
            rows.append(ramp[(row * 3) % 256:(row * 3) % 256 + stride])
    return b''.join(rows)


class Frame:
    def __init__(self, stock, image, report):
        from native_runtime import Context
        from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
        from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1
        from z30_extensions.features.photo import date_stamp
        self.date_stamp = date_stamp
        self.report = report
        self.grain = report['features']['film_grain']
        self.control = report['features']['film_grain_control']
        self.stamp = report['features'].get('date_stamp_control')
        self.context = Context(stock, image)
        self.u = u = self.context.u
        u.mem_map(Y_BASE, 0x2000000)
        u.mem_map(C_BASE, 0x2000000)
        get = int(report['features']['menu_framework']['functions']['get'], 16)
        self.state = FAKE_STATE
        self.cache, self.writes = [], []
        self.watch_writes = True
        self.par_attributes = UNCACHED
        self.par_override = {}
        self.ats = None
        self.payload = int(self.grain['payload'], 16) & ~1
        self.payload_end = self.payload + self.grain['payload_bytes']
        self.counted = None

        def leave(value):
            u.reg_write(UC_ARM_REG_R0, value)
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

        def get_state(unicorn, address, size, data):
            leave(self.state)

        def maintain(unicorn, address, size, data):
            self.cache.append((address, u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1), len(self.writes)))
            leave(0)

        def write(unicorn, access, address, size, value, data):
            if self.watch_writes:
                self.writes.append((address, size))

        u.hook_add(UC_HOOK_CODE, get_state, begin=get & ~1, end=get & ~1)
        for address in {FLUSH, CLEAN, date_stamp.CACHE_INVALIDATE, date_stamp.CACHE_CLEAN}:
            u.hook_add(UC_HOOK_CODE, maintain, begin=address, end=address)
        u.hook_add(UC_HOOK_MEM_WRITE, write, begin=Y_BASE, end=C_BASE + 0x2000000)
        self._coprocessor_hooks()

    def _coprocessor_hooks(self):
        """Emulate the payload's MRC/MCR p15 instructions (Unicorn runs without an MMU)."""
        from unicorn import UC_HOOK_CODE
        from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0
        u = self.u
        sites = {}
        code = bytes(u.mem_read(self.payload, self.payload_end - self.payload))
        for offset in range(0, len(code) - 3, 2):
            first, second = struct.unpack_from('<2H', code, offset)
            if first & 0xFF10 == 0xEE10 and (second >> 8) & 15 == 15 and second & 0x10:
                sites[self.payload + offset] = (first & 0xFFFF, second)
            elif first & 0xFF10 == 0xEE00 and (second >> 8) & 15 == 15 and second & 0x10:
                sites[self.payload + offset] = (first & 0xFFFF, second)
        if not sites:
            raise AssertionError('no CP15 accesses found in the payload')

        def emulate(unicorn, address, size, data):
            first, second = sites[address]
            load = bool(first & 0x10)
            crn, opc1 = first & 15, (first >> 5) & 7
            rt, crm, opc2 = second >> 12, second & 15, (second >> 5) & 7
            key = (opc1, crn, crm, opc2)
            if load:
                if key == (0, 1, 0, 0):
                    value = 0x00C5187D          # SCTLR: MMU on
                elif key == (0, 2, 0, 2):
                    value = 0                   # TTBCR: short descriptors
                elif key == (0, 7, 4, 0):
                    va = self.ats
                    value = self.par_override.get(va, (va & 0xFFFFF000) | self.par_attributes)
                else:
                    raise AssertionError(f'unexpected CP15 read {key} at {address:#x}')
                u.reg_write(UC_ARM_REG_R0 + rt, value)
            else:
                if key != (0, 7, 8, 1):
                    raise AssertionError(f'unexpected CP15 write {key} at {address:#x}')
                self.ats = u.reg_read(UC_ARM_REG_R0 + rt)
            u.reg_write(UC_ARM_REG_PC, (address + 4) | 1)

        for address in sites:
            u.hook_add(UC_HOOK_CODE, emulate, begin=address, end=address)
        self.cp15_sites = len(sites)

    def setup(self, width, height, stride, style, strength_code, kind, image_format=1, stamp_mode=0):
        u = self.u
        self.width, self.height, self.stride = width, height, stride
        self.y_before = background_y(stride, height)
        u.mem_write(Y_BASE, self.y_before)
        u.mem_write(C_BASE, bytes((BACKGROUND_C,)) * (stride * height))
        descriptor = bytearray(0x20)
        descriptor[0] = image_format
        struct.pack_into('<3H', descriptor, 2, stride, stride, stride)
        struct.pack_into('<2H', descriptor, 0xA, width, height)
        struct.pack_into('<2I', descriptor, 0x14, Y_BASE, C_BASE)
        u.mem_write(DESCRIPTOR, bytes(descriptor))
        u.mem_write(ARGS, struct.pack('<I', DESCRIPTOR))
        u.mem_write(CTX + self.date_stamp.INFO_FIELD, struct.pack('<I', INFO))
        u.mem_write(INFO + KIND_OFFSET, bytes((kind,)))
        u.mem_write(INFO + self.date_stamp.DATE_OFFSET, struct.pack('<H5B', 2026, 11, 25, 21, 30, 0))
        base = FAKE_STATE + self.control['state_offset']
        u.mem_write(base, bytes(64))
        u.mem_write(base, struct.pack('<I', style))
        u.mem_write(base + 8, struct.pack('<I', strength_code))
        if self.stamp:
            u.mem_write(FAKE_STATE + self.stamp['state_offset'], struct.pack('<I', stamp_mode))
            u.mem_write(FAKE_STATE + self.stamp['color_offset'], struct.pack('<I', 0))
        u.mem_write(MS_CLOCK, struct.pack('<I', CLOCK))
        self.par_attributes, self.par_override = UNCACHED, {}

    def state_words(self):
        return struct.unpack('<16I', bytes(self.u.mem_read(FAKE_STATE + self.control['state_offset'], 64)))

    def state_text(self):
        raw = bytes(self.u.mem_read(FAKE_STATE + self.control['state_offset'] + 40, 24))
        return raw.split(b'\0')[0].decode('ascii')

    def run(self, site, count_instructions=False):
        from native_runtime import STACK
        from unicorn import UC_HOOK_CODE
        from unicorn.arm_const import (UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R4, UC_ARM_REG_R5,
                                       UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10,
                                       UC_ARM_REG_R11, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_CPSR)
        u = self.u
        lr = site + 5
        saved = {UC_ARM_REG_R7: 0x77770007, UC_ARM_REG_R8: 0x77770008, UC_ARM_REG_R9: 0x77770009,
                 UC_ARM_REG_R10: 0x7777000A, UC_ARM_REG_R11: 0x7777000B, UC_ARM_REG_R4: CTX}
        for register, value in saved.items():
            u.reg_write(register, value)
        top = STACK + 0xF0000
        u.reg_write(UC_ARM_REG_SP, top)
        u.reg_write(UC_ARM_REG_R0, ARGS)
        u.reg_write(UC_ARM_REG_LR, lr)
        self.cache.clear()
        self.writes.clear()
        handle = None
        self.counted = 0
        if count_instructions:
            def count(unicorn, address, size, data):
                self.counted += 1
            handle = u.hook_add(UC_HOOK_CODE, count, begin=self.payload, end=self.payload_end)
        resume = self.date_stamp.RESUME
        masked = u.reg_read(UC_ARM_REG_CPSR) & 0x80
        start = time.time()
        u.emu_start(self.date_stamp.ENCODE_GATEWAY | 1, resume, timeout=0, count=2_000_000_000)
        self.seconds = time.time() - start
        if handle is not None:
            u.hook_del(handle)
        assert u.reg_read(UC_ARM_REG_PC) == resume
        # The displaced stock entry ran once with the caller's state.
        assert u.reg_read(UC_ARM_REG_SP) == top - 24
        assert u.reg_read(UC_ARM_REG_R0) == ARGS and u.reg_read(UC_ARM_REG_R5) == ARGS
        assert u.reg_read(UC_ARM_REG_R6) == 1 and u.reg_read(UC_ARM_REG_R1) == 0
        for register, value in saved.items():
            assert u.reg_read(register) == value
        frame = struct.unpack('<6I', bytes(u.mem_read(top - 24, 24)))
        assert frame == (CTX, frame[1], frame[2], 0x77770007, 0x77770008, lr), frame
        assert u.reg_read(UC_ARM_REG_CPSR) & 0x80 == masked, 'interrupt mask changed'

    def luma(self):
        return bytes(self.u.mem_read(Y_BASE, self.stride * self.height))

    def chroma_untouched(self):
        return bytes(self.u.mem_read(C_BASE, self.stride * self.height)) == bytes((BACKGROUND_C,)) * (
            self.stride * self.height)

    def expected(self, style, strength_code, preview, calls=1):
        from z30_extensions.host import film_grain as host
        strength = host.STRENGTH_ORDER[strength_code]
        seed = (CLOCK ^ Y_BASE ^ ((calls * host.SEED_ROW) & host.M32)) | 1
        return host.grain_plane(self.y_before, self.stride, self.width, self.height, style, strength, preview, seed)


SITE_MAIN, SITE_SECOND, PREVIEW_A, PREVIEW_B, PREVIEW_C = (0x100A6198, 0x100A6304, 0x100A6540, 0x100A66AE,
                                                           0x100A684A)


def check_cache_order(frame):
    """Cacheable plane: per row, flush before its first write and clean after its last."""
    rows = {}
    for address, base, length, index in frame.cache:
        row = (base - Y_BASE) // frame.stride
        rows.setdefault(row, []).append((address, base, length, index))
    assert len(rows) == frame.height
    writes_by_row = {}
    for i, (address, _) in enumerate(frame.writes):
        writes_by_row.setdefault((address - Y_BASE) // frame.stride, []).append(i)
    for row, calls in rows.items():
        assert [c[0] for c in calls] == [FLUSH, CLEAN], (row, calls)
        assert calls[0][1] == calls[1][1] == Y_BASE + row * frame.stride
        assert calls[0][2] == calls[1][2] == 4 * (frame.width // 4) <= 0x45000
        indexes = writes_by_row.get(row, [])
        assert indexes and calls[0][3] <= indexes[0] and calls[1][3] > indexes[-1], row


def check_pixels(frame):
    from z30_extensions.host import film_grain as host
    cases = []
    sizes = ((1620, 1080, 1632), (640, 424, 640), (160, 120, 160), (1032, 600, 1040), (1280, 520, 1280))
    combos = [(host.STYLE_FUJI400, 0), (host.STYLE_HP5, 3), (host.STYLE_FUJI400, 5), (host.STYLE_HP5, 1),
              (host.STYLE_FUJI400, 2)]
    for (width, height, stride), (style, code) in zip(sizes, combos):
        for site, kind, preview in ((SITE_MAIN, 3, False), (PREVIEW_A, 3, True)):
            frame.setup(width, height, stride, style, code, kind)
            frame.run(site, count_instructions=(width == 640 and not preview))
            got, want = frame.luma(), frame.expected(style, code, preview)
            assert got == want, (width, height, style, code, preview,
                                 next(i for i, (a, b) in enumerate(zip(got, want)) if a != b))
            assert got != frame.y_before and frame.chroma_untouched()
            assert not frame.cache, 'uncached plane needs no maintenance'
            words = frame.state_words()
            if not preview:
                assert words[3] == 1 and words[5] == 1 and frame.state_text().endswith(' ms n #1'), frame.state_text()
                assert words[8] == width | height << 16 and words[9] == site + 5
            else:
                assert words[3] == 0 and frame.state_text() == '', 'previews do not overwrite the readout'
            case = dict(width=width, height=height, stride=stride, style=host.STYLES[style],
                        strength=host.STRENGTH_ORDER[code], preview=preview)
            if frame.counted:
                case['payload_instructions'] = frame.counted
                case['instructions_per_pixel'] = round(frame.counted / (width * height), 2)
            cases.append(case)
    # Cacheable memory: same pixels, and each row is maintained around its writes.
    frame.setup(640, 424, 640, host.STYLE_HP5, 4, 3)
    frame.par_attributes = CACHED
    frame.run(SITE_MAIN)
    assert frame.luma() == frame.expected(host.STYLE_HP5, 4, False)
    check_cache_order(frame)
    assert frame.state_text().endswith(' ms c #1'), frame.state_text()
    cases.append(dict(cacheable_plane_row_maintenance=True, rows=frame.height))
    # Recording kinds: RAW+JPEG grains the main image only, RAW only the NEF's full preview.
    for site, kind, preview in ((SITE_MAIN, 0, False), (SITE_SECOND, 1, False)):
        frame.setup(1032, 600, 1040, host.STYLE_FUJI400, 0, kind)
        frame.run(site)
        assert frame.luma() == frame.expected(host.STYLE_FUJI400, 0, preview), (site, kind)
        cases.append(dict(site=hex(site), kind=kind, grain=True))
    return cases


def check_passthrough(frame):
    from z30_extensions.host import film_grain as host
    from unicorn.arm_const import UC_ARM_REG_CPSR
    cases = []
    good = (640, 424, 640)

    def untouched(name, site=SITE_MAIN, style=host.STYLE_FUJI400, kind=3, before=None, image_format=1,
                  code=None, size=good):
        frame.state = FAKE_STATE
        frame.setup(*size, style, 0, kind, image_format)
        if before:
            before()
        frame.run(site)
        assert not frame.writes and not frame.cache, name
        assert frame.luma() == frame.y_before and frame.chroma_untouched(), name
        if code is not None:
            words = frame.state_words()
            assert words[3] == code and frame.state_text() == f'skip {code}', (name, words[3], frame.state_text())
        cases.append(dict(passthrough=name, **({'result': code} if code is not None else {})))

    untouched('style OFF', style=host.STYLE_OFF)
    untouched('unknown saved style', style=3)
    untouched('RAW only, main-image call', kind=1)
    untouched('kind 2', kind=2)
    untouched('JPEG only, NEF preview call', site=SITE_SECOND, kind=3)
    untouched('RAW+JPEG, NEF preview call', site=SITE_SECOND, kind=0)
    for site in (PREVIEW_A, PREVIEW_B, PREVIEW_C):
        untouched(f'RAW+JPEG, preview call {site:#x}', site=site, kind=0)
        untouched(f'RAW only, preview call {site:#x}', site=site, kind=1)
    for caller in (0x1009F11E, 0x1009F25A, 0x1009F83A, 0x100A0BE6):
        untouched(f'other encode caller {caller:#x}', site=caller)

    def clear_state():
        frame.state = 0
    untouched('framework not initialised', before=clear_state)
    frame.state = FAKE_STATE

    def clear_info():
        frame.u.mem_write(CTX + frame.date_stamp.INFO_FIELD, bytes(4))
    untouched('capture info missing', before=clear_info)

    def clear_descriptor():
        frame.u.mem_write(ARGS, bytes(4))
    untouched('descriptor missing', before=clear_descriptor, code=3)
    untouched('unsupported format', image_format=3, code=3)

    def misalign():
        frame.u.mem_write(DESCRIPTOR + 0x14, struct.pack('<I', Y_BASE + 2))
    untouched('misaligned luma base', before=misalign, code=3)

    def overlap():
        frame.u.mem_write(DESCRIPTOR + 0x18, struct.pack('<I', Y_BASE + 640 * 100))
    untouched('luma runs into chroma', before=overlap, code=3)

    def narrow_stride():
        frame.u.mem_write(DESCRIPTOR + 2, struct.pack('<H', 600))
    untouched('stride below width', before=narrow_stride, code=3)

    def fault():
        frame.par_override = {Y_BASE + 0x10000: 1}
    untouched('a page faults', before=fault, code=5)

    def moved():
        frame.par_override = {Y_BASE + 0x20000: (Y_BASE + 0x30000) & 0xFFFFF000}
    untouched('CPU address differs from the physical address', before=moved, code=5)

    def device():
        frame.par_attributes = DEVICE
    untouched('device memory', before=device, code=6)

    def user_mode():
        cpsr = frame.u.reg_read(UC_ARM_REG_CPSR)
        frame.u.reg_write(UC_ARM_REG_CPSR, (cpsr & ~31) | 0x10)
    try:
        untouched('user mode', before=user_mode, code=4)
    finally:
        cpsr = frame.u.reg_read(UC_ARM_REG_CPSR)
        frame.u.reg_write(UC_ARM_REG_CPSR, (cpsr & ~31) | 0x13)
    return cases


def check_with_stamp(frame):
    """Both features: grain first, then the date drawn opaque on top."""
    from z30_extensions.host import date_stamp as stamp_host, film_grain as host
    if not frame.stamp:
        return []
    width, height, stride = 1280, 720, 1296
    frame.setup(width, height, stride, host.STYLE_FUJI400, 0, 3, stamp_mode=stamp_host.MODE_DATE)
    frame.run(SITE_MAIN)
    y_plane = bytearray(frame.expected(host.STYLE_FUJI400, 0, False))
    c_plane = bytearray(bytes((BACKGROUND_C,)) * (stride * height))
    assert stamp_host.stamp_frame(y_plane, c_plane, width, height, stride, stride, 1,
                                  (2026, 11, 25, 21, 30, 0), stamp_host.MODE_DATE, 0, 0)
    assert frame.luma() == bytes(y_plane)
    assert bytes(frame.u.mem_read(C_BASE, stride * height)) == bytes(c_plane)
    return [dict(grain_then_date_stamp=True)]


def check_full_frame(frame):
    from z30_extensions.host import film_grain as host
    frame.watch_writes = False
    frame.setup(5568, 3712, 5568, host.STYLE_FUJI400, 0, 3)
    frame.run(SITE_MAIN)
    seconds = frame.seconds
    assert frame.luma() == frame.expected(host.STYLE_FUJI400, 0, False)
    frame.watch_writes = True
    return [dict(full_frame=[5568, 3712], unicorn_seconds=round(seconds, 1))]


def check_menu(stock, image, report):
    """FILM page under the settings fixture: save and fresh-boot restore, rows step in place."""
    from verify_sound_settings import Fixture
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R1
    from z30_extensions.features.photo.film_grain_ui import MAGIC
    report = dict(report, features=dict(report['features'], shutter_sound=report['features']['menu_framework']))
    control = report['features']['film_grain_control']
    offset, strength = control['state_offset'], control['strength_offset']
    functions = {key: int(value, 16) for key, value in control['functions'].items()}
    path = 'A:\\' + control['settings_file']
    cases = []
    for style, code in ((1, 0), (2, 5), (0, 3), (1, 4)):
        files = {}
        first = Fixture(stock, image, report, files, properties={})
        first.set(offset, style)
        first.set(strength, code)
        assert first.call(functions['grain_save']) == 1 and path in files
        second = Fixture(stock, image, report, files, properties={})
        assert (second.word(offset), second.word(strength)) == (style, code) and not second.opened
        cases.append(dict(fresh_boot_restores=dict(style=style, strength_code=code)))
    valid = struct.pack('<4I', MAGIC, 1, 2, ~(MAGIC ^ 1 ^ 2) & 0xFFFFFFFF)
    for payload in (b'', valid[:8], valid + b'X', valid[:-1] + b'\0',
                    struct.pack('<4I', MAGIC, 6, 1, ~(MAGIC ^ 6 ^ 1) & 0xFFFFFFFF),
                    struct.pack('<4I', MAGIC, 0, 3, ~(MAGIC ^ 3) & 0xFFFFFFFF)):
        fixture = Fixture(stock, image, report, {path: payload}, properties={})
        assert fixture.word(offset) == 0 and fixture.word(strength) == 0 and not fixture.opened
    cases.append(dict(invalid_records_stay_off=6))
    fixture = Fixture(stock, image, report, {}, properties={})
    navigation = []

    def boundary(u, address, size, data):
        if address == 0x1045F732:
            navigation.append(u.reg_read(UC_ARM_REG_R1))
        fixture.ret(0)
    for address in (fixture.f['labels'] & ~1, functions['grain_labels'] & ~1, 0x1045F732, 0x1045D54C, 0x10453FAC):
        fixture.u.hook_add(UC_HOOK_CODE, boundary, begin=address, end=address)
    page = fixture.state + control['page_offset']
    for row, field, expected in ((0, offset, 1), (0, offset, 2), (0, offset, 0), (1, strength, 1),
                                 (1, strength, 2), (1, strength, 3), (1, strength, 4), (1, strength, 5),
                                 (1, strength, 0)):
        navigation.clear()
        fixture.u.mem_write(page + 22, struct.pack('<2H', row, 0))
        fixture.call(functions['grain_action'], (page,))
        assert fixture.word(field) == expected and navigation == [control['page_index']], (row, expected, navigation)
        fixture.call(functions['grain_restore'])
        assert fixture.word(field) == expected
    cases.append(dict(rows_step_in_place_and_save=True))
    before = (fixture.word(offset), fixture.word(strength))
    navigation.clear()
    fixture.u.mem_write(page + 22, struct.pack('<2H', 2, 0))
    fixture.call(functions['grain_action'], (page,))
    assert (fixture.word(offset), fixture.word(strength)) == before and not navigation
    cases.append(dict(rows_past_the_last_do_nothing=True))
    fixture.short_write = True
    fixture.u.mem_write(page + 22, struct.pack('<2H', 0, 0))
    fixture.call(functions['grain_action'], (page,))
    assert fixture.word(offset) == 1 and fixture.word(52) == 8
    cases.append(dict(save_failure_keeps_choice_and_reports=True))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--preset', nargs='+', default=['film-grain'])
    parser.add_argument('--full-frame', action='store_true', help='also grain one 5568x3712 frame (slow)')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/research/film-grain/verify.json')
    args = parser.parse_args()
    from z30_extensions.build import build
    from z30_extensions.container import extract_eg
    stock = extract_eg(args.source.read_bytes())
    image, report = build(stock, args.preset)
    frame = Frame(stock, image, report)
    pixels = check_pixels(frame)
    passthrough = check_passthrough(frame)
    combined = check_with_stamp(frame)
    full = check_full_frame(frame) if args.full_frame else []
    menu = check_menu(stock, image, report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(
        image_sha256=report['sha256'], presets=args.preset, payload_sha256=report['features']['film_grain']['payload_sha256'],
        cp15_sites=frame.cp15_sites, pixels=pixels, passthrough=passthrough, combined=combined, full_frame=full, menu=menu,
        scope=__doc__, physical_verified=False), indent=2) + '\n')
    rate = next((c['instructions_per_pixel'] for c in pixels if 'instructions_per_pixel' in c), None)
    print(f'PASS film grain: {len(pixels)} pixel, {len(passthrough)} gate, {len(combined)} combined, '
          f'{len(full)} full-frame, {len(menu)} menu cases ({args.preset}); payload {rate} instructions per pixel')


if __name__ == '__main__':
    main()
