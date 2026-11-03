"""Execute the date stamp gate, ARM core and menu state under Unicorn.

Each frame case compares both planes byte for byte with date_stamp.stamp_frame.
Stock cache services and the framework state getter are explicit boundaries; no
codec, sensor or camera is involved, so this cannot prove which frames the real
capture task sends through the gate.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]

Y_BASE, C_BASE = 0x70000000, 0x74000000
DESCRIPTOR, ARGS, CTX, INFO, FAKE_STATE = 0x50000000, 0x50000100, 0x50000200, 0x50010000, 0x50020000
SIZES = ((5568, 3712, 5568, 1), (5568, 3712, 5568, 2), (4952, 3712, 4960, 1), (4176, 2784, 4176, 1),
         (2784, 1856, 2784, 2), (5568, 2328, 5568, 1), (1620, 1080, 1632, 1), (640, 480, 640, 2), (160, 120, 160, 1))
DATE = (2026, 11, 2, 14, 5, 9)
BACKGROUND_C = 0x83


def background_y(stride, height):
    """Diagonal luma ramp through every value, so the glow meets both darker and brighter pixels."""
    ramp = bytes(range(256)) * (stride // 256 + 2)
    return b''.join(ramp[row % 256:row % 256 + stride] for row in range(height))


class Frame:
    """One gate invocation with mapped planes and recorded boundary calls."""

    def __init__(self, stock, image, report):
        from native_runtime import Context
        from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
        from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1
        from z30_extensions.features import date_stamp as feature
        self.feature = feature
        self.context = Context(stock, image)
        self.u = u = self.context.u
        self.control = report['features']['date_stamp_control']
        self.entry = int(report['features']['date_stamp']['gate_entry'], 16)
        get = int(report['features']['shutter_sound']['functions']['get'], 16)
        u.mem_map(Y_BASE, 0x2000000)
        u.mem_map(C_BASE, 0x2000000)
        self.cache = []
        self.writes = []
        self.state = FAKE_STATE

        def leave(value):
            u.reg_write(UC_ARM_REG_R0, value)
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

        def get_state(unicorn, address, size, data):
            leave(self.state)

        def maintain(unicorn, address, size, data):
            self.cache.append((address, u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1), len(self.writes)))
            leave(0)

        def write(unicorn, access, address, size, value, data):
            self.writes.append((address, size))

        u.hook_add(UC_HOOK_CODE, get_state, begin=get & ~1, end=get & ~1)
        for address in (feature.CACHE_INVALIDATE, feature.CACHE_CLEAN):
            u.hook_add(UC_HOOK_CODE, maintain, begin=address, end=address)
        u.hook_add(UC_HOOK_MEM_WRITE, write, begin=Y_BASE, end=C_BASE + 0x2000000)

    def setup(self, width, height, stride, image_format, date, mode, style=0, orientation=0):
        u = self.u
        self.width, self.height, self.stride, self.image_format = width, height, stride, image_format
        u.mem_write(Y_BASE, background_y(stride, height))
        u.mem_write(C_BASE, bytes((BACKGROUND_C,)) * (stride * height))
        descriptor = bytearray(0x20)
        descriptor[0] = image_format
        struct.pack_into('<3H', descriptor, 2, stride, stride, stride)
        struct.pack_into('<2H', descriptor, 0xA, width, height)
        struct.pack_into('<2I', descriptor, 0x14, Y_BASE, C_BASE)
        u.mem_write(DESCRIPTOR, bytes(descriptor))
        u.mem_write(ARGS, struct.pack('<I', DESCRIPTOR))
        u.mem_write(CTX + self.feature.INFO_FIELD, struct.pack('<I', INFO))
        u.mem_write(INFO + self.feature.DATE_OFFSET, struct.pack('<H5B', *date))
        u.mem_write(INFO + self.feature.ORIENTATION_OFFSET, bytes((orientation,)))
        u.mem_write(FAKE_STATE + self.control['state_offset'], struct.pack('<I', mode))
        u.mem_write(FAKE_STATE + self.control['color_offset'], struct.pack('<I', style))

    def run(self, lr=None):
        """Enter the gate as the stock caller would; returns the checked continuation."""
        from native_runtime import STACK
        from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R4,
                                       UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
                                       UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_SP)
        u = self.u
        lr = self.feature.CALL_SITES[0] + 5 if lr is None else lr
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
        resume = self.feature.RESUME
        u.emu_start(self.entry, resume, timeout=120000000, count=60000000)
        # The displaced stock window must have run exactly once with the caller's state.
        assert u.reg_read(UC_ARM_REG_PC) == resume
        assert u.reg_read(UC_ARM_REG_SP) == top - 24
        assert u.reg_read(UC_ARM_REG_R0) == ARGS and u.reg_read(UC_ARM_REG_R5) == ARGS
        assert u.reg_read(UC_ARM_REG_R6) == 1 and u.reg_read(UC_ARM_REG_R1) == 0
        for register, value in saved.items():
            assert u.reg_read(register) == value
        frame = struct.unpack('<6I', bytes(u.mem_read(top - 24, 24)))
        assert frame == (CTX, frame[1], frame[2], 0x77770007, 0x77770008, lr), frame
        return dict(writes=len(self.writes), cache_calls=len(self.cache))

    def planes(self):
        return (bytes(self.u.mem_read(Y_BASE, self.stride * self.height)),
                bytes(self.u.mem_read(C_BASE, self.stride * self.height)))

    def expected(self, date, mode, style=0, orientation=0):
        from z30_extensions import date_stamp as host
        y_plane = bytearray(background_y(self.stride, self.height))
        c_plane = bytearray(bytes((BACKGROUND_C,)) * (self.stride * self.height))
        drawn = host.stamp_frame(y_plane, c_plane, self.width, self.height, self.stride, self.stride,
                                 self.image_format, date, mode, style, orientation)
        return bytes(y_plane), bytes(c_plane), drawn


def _glow_branches(frame, y_plane, style):
    from z30_extensions import date_stamp as host
    before = background_y(frame.stride, frame.height)
    glow = {triple[0] for triple in host.PASS_COLORS[style % host.GLOW_FLAG][:-1]}
    raised = any(new in glow and new > old for new, old in zip(y_plane, before))
    kept = any(old > max(glow) and new == old for new, old in zip(y_plane, before))
    return raised and kept


def check_frame_cases(frame):
    from z30_extensions import date_stamp as host
    feature = frame.feature
    cases = []
    for width, height, stride, image_format in SIZES:
        # Every format on every size; the colors share one path, so vary them per format,
        # alternate the glow so each size draws with and without it, and rotate through
        # landscape, both portrait codes and an unknown code that must draw as landscape.
        for mode in host.FORMATS:
            color = mode % len(host.COLORS) | (host.GLOW_FLAG if mode % 2 else 0)
            orientation = (0, 1, 2, 3, 1)[mode - 1]
            frame.setup(width, height, stride, image_format, DATE, mode, color, orientation)
            result = frame.run()
            y_plane, c_plane, drawn = frame.expected(DATE, mode, color, orientation)
            if drawn is False and orientation and width < 400:
                # A turned stamp may not fit a thumbnail's short side; then nothing is drawn.
                assert frame.planes() == (bytes(y_plane), bytes(c_plane)) and result['writes'] == 0
                cases.append(dict(width=width, height=height, mode=mode, orientation=orientation, skipped=True))
                continue
            assert drawn and frame.planes() == (y_plane, c_plane), (width, height, image_format, mode, color,
                                                                    orientation)
            if mode == host.MODE_DATE:
                # The glow must have both raised darker pixels and left brighter ones alone.
                assert _glow_branches(frame, y_plane, color)
            # Every CPU write sits between all invalidates and all cleans.
            first_write, last_write = 0, len(frame.writes)
            invalidates = [c for c in frame.cache if c[0] == feature.CACHE_INVALIDATE]
            cleans = [c for c in frame.cache if c[0] == feature.CACHE_CLEAN]
            assert invalidates and all(c[3] == first_write for c in invalidates)
            assert cleans and all(c[3] == last_write for c in cleans)
            assert [c[1:3] for c in invalidates] == [c[1:3] for c in cleans]
            # Row-sized requests stay in the stock line-maintenance branch.
            assert max(c[2] for c in frame.cache) <= 0x45000
            covered = set()
            for _, base, length, _ in frame.cache:
                covered.update(range(base >> 6, (base + length + 63) >> 6))
            assert all(address >> 6 in covered for address, _ in frame.writes)
            cases.append(dict(width=width, height=height, format=image_format, mode=mode, color=color,
                              orientation=orientation, **result))
    for color in range(host.STYLE_MAX + 1):
        frame.setup(1280, 720, 1296, 1, DATE, host.MODE_DATE_TIME, color)
        frame.run()
        y_plane, c_plane, drawn = frame.expected(DATE, host.MODE_DATE_TIME, color)
        assert drawn and frame.planes() == (y_plane, c_plane), color
        cases.append(dict(every_style_on_one_size=color))
    # Both portrait codes with the glow on a full frame, in both chroma formats.
    for orientation in (host.ORIENT_CCW, host.ORIENT_CW):
        for image_format in (1, 2):
            frame.setup(5568, 3712, 5568, image_format, DATE, host.MODE_DATE_TIME, host.GLOW_FLAG, orientation)
            frame.run()
            y_plane, c_plane, drawn = frame.expected(DATE, host.MODE_DATE_TIME, host.GLOW_FLAG, orientation)
            assert drawn and frame.planes() == (y_plane, c_plane), (orientation, image_format)
            cases.append(dict(portrait_full_frame=orientation, format=image_format))
    return cases


def check_passthrough_cases(frame):
    from z30_extensions import date_stamp as host
    feature = frame.feature
    cases = []
    good = (1280, 720, 1296, 1)

    def untouched(name, date=DATE, mode=host.MODE_DATE, size=good, lr=None, before=None, color=0):
        frame.state = FAKE_STATE
        frame.setup(*size, date, mode, color)
        if before:
            before()
        result = frame.run(lr)
        assert result == dict(writes=0, cache_calls=0), (name, result)
        assert frame.planes() == (background_y(size[2], size[1]),
                                  bytes((BACKGROUND_C,)) * (size[2] * size[1])), name
        cases.append(dict(passthrough=name))

    untouched('mode off', mode=host.MODE_OFF)
    untouched('unknown saved mode', mode=len(host.FORMATS) + 1)
    untouched('unknown saved style', color=host.STYLE_MAX + 1)
    for name, date in (('year below range', (1999, 1, 1, 0, 0, 0)), ('zero date', (0, 0, 0, 0, 0, 0)),
                       ('month 13', (2026, 13, 1, 0, 0, 0)), ('day 32', (2026, 1, 32, 0, 0, 0)),
                       ('hour 24', (2026, 1, 1, 24, 0, 0)), ('minute 60', (2026, 1, 1, 0, 60, 0))):
        untouched(name, date=date)
    untouched('unsupported format', size=(1280, 720, 1296, 3))
    untouched('too small for the stamp', size=(60, 40, 60, 1))
    # Families that do not hold a capture context in r4 must never reach the core.
    for caller in (0x1009F11E, 0x1009F25A, 0x1009F83A, 0x100A0BE6):
        untouched(f'other encode caller {caller:#x}', lr=caller + 5)

    def clear_state():
        frame.state = 0
    untouched('framework not initialized', before=clear_state)
    frame.state = FAKE_STATE

    def clear_info():
        frame.u.mem_write(CTX + feature.INFO_FIELD, bytes(4))
    untouched('capture info missing', before=clear_info)

    def clear_planes():
        frame.u.mem_write(DESCRIPTOR + 0x14, bytes(8))
    untouched('null planes', before=clear_planes)
    for site in feature.CALL_SITES:
        frame.setup(*good, DATE, host.MODE_DATE)
        result = frame.run(site + 5)
        assert result['writes'] > 0
        cases.append(dict(stamped_from_call_site=hex(site)))
    # A second encode of the same planes must not change them, with or without the glow.
    for style in (0, host.GLOW_FLAG):
        frame.setup(*good, DATE, host.MODE_DATE, style)
        frame.run()
        once = frame.planes()
        frame.run()
        assert frame.planes() == once, style
    cases.append(dict(second_encode_of_same_planes_is_identical=True))
    return cases


def check_menu_cases(stock, image, report):
    from verify_sound_settings import Fixture
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R1
    from z30_extensions.features.date_stamp_ui import MAGIC
    control = report['features']['date_stamp_control']
    offset, color_offset = control['state_offset'], control['color_offset']
    functions = {key: int(value, 16) for key, value in control['functions'].items()}
    cases = []
    for mode in (0, 1, 2):
        files = {}
        first = Fixture(stock, image, report, files, properties={})
        first.set(offset, mode)
        assert first.call(functions['stamp_save']) == 1
        second = Fixture(stock, image, report, files, properties={})
        assert second.word(offset) == mode and not second.opened
        cases.append(dict(fresh_boot_restores_mode=mode))
    # (style, mode) pairs survive a fresh boot, including a record from the first three-mode build.
    for color, mode in ((1, 5), (3, 3), (2, 4), (0, 2), (5, 1), (7, 3)):
        files = {}
        first = Fixture(stock, image, report, files, properties={})
        first.set(offset, mode)
        first.set(color_offset, color)
        assert first.call(functions['stamp_save']) == 1
        second = Fixture(stock, image, report, files, properties={})
        assert (second.word(color_offset), second.word(offset)) == (color, mode)
        cases.append(dict(fresh_boot_restores=dict(color=color, mode=mode)))
    old = struct.pack('<4I', MAGIC, 0, 2, ~(MAGIC ^ 2) & 0xFFFFFFFF)
    fixture = Fixture(stock, image, report, {'A:\\WL-STAMP.CFG': old}, properties={})
    assert (fixture.word(color_offset), fixture.word(offset)) == (0, 2)
    cases.append(dict(first_build_record_still_restores=True))
    valid = struct.pack('<4I', MAGIC, 0, 1, ~(MAGIC ^ 1) & 0xFFFFFFFF)
    for payload in (b'', valid[:8], valid + b'X', valid[:-1] + b'\0',
                    struct.pack('<4I', MAGIC, 0, 6, ~(MAGIC ^ 6) & 0xFFFFFFFF),
                    struct.pack('<4I', MAGIC, 8, 1, ~(MAGIC ^ 8 ^ 1) & 0xFFFFFFFF)):
        fixture = Fixture(stock, image, report, {'A:\\WL-STAMP.CFG': payload}, properties={})
        assert fixture.word(offset) == 0 and not fixture.opened
    cases.append(dict(invalid_records_stay_off=6))
    fixture = Fixture(stock, image, report, {}, properties={})
    navigation = []

    def boundary(u, address, size, data):
        if address == 0x1045F732:
            navigation.append(u.reg_read(UC_ARM_REG_R1))
        fixture.ret(0)
    for address in (fixture.f['labels'] & ~1, 0x1045F732, 0x1045D54C, 0x10453FAC):
        fixture.u.hook_add(UC_HOOK_CODE, boundary, begin=address, end=address)
    page = fixture.state + control['page_offset']
    for row in (5, 4, 3, 2, 1, 0):
        fixture.u.mem_write(page + 22, struct.pack('<2H', row, 0))
        fixture.call(functions['stamp_action'], (page,))
        assert fixture.word(offset) == row and navigation[-1] == 955
        fixture.call(functions['stamp_restore'])
        assert fixture.word(offset) == row
    cases.append(dict(menu_choice_saves_and_returns_to_root=True))
    # The color row reopens its own page: mode kept, value saved, never the parent.
    fixture.set(offset, 2)
    labels = functions['stamp_labels'] & ~1
    fixture.u.hook_add(UC_HOOK_CODE, boundary, begin=labels, end=labels)
    for expected in (1, 2, 3, 0):
        navigation.clear()
        fixture.u.mem_write(page + 22, struct.pack('<2H', control['color_row'], 0))
        fixture.call(functions['stamp_action'], (page,))
        assert fixture.word(color_offset) == expected and fixture.word(offset) == 2
        assert navigation == [control['page_index']], navigation
        fixture.call(functions['stamp_restore'])
        assert fixture.word(color_offset) == expected
    cases.append(dict(color_row_reopens_its_page_and_saves=True))
    # The glow row toggles its flag in place; colors then cycle without dropping it.
    glow = control['glow_flag']
    for row, expected in ((control['glow_row'], glow), (control['color_row'], glow | 1),
                          (control['color_row'], glow | 2), (control['glow_row'], 2)):
        navigation.clear()
        fixture.u.mem_write(page + 22, struct.pack('<2H', row, 0))
        fixture.call(functions['stamp_action'], (page,))
        assert fixture.word(color_offset) == expected and fixture.word(offset) == 2, (row, expected)
        assert navigation == [control['page_index']], navigation
        fixture.call(functions['stamp_restore'])
        assert fixture.word(color_offset) == expected
    cases.append(dict(glow_row_toggles_in_place_and_colors_keep_it=True))
    fixture.short_write = True
    fixture.u.mem_write(page + 22, struct.pack('<2H', 1, 0))
    fixture.call(functions['stamp_action'], (page,))
    assert fixture.word(offset) == 1 and fixture.word(52) == 8
    cases.append(dict(save_failure_keeps_active_mode_and_reports=True))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--preset', nargs='+', default=['date-stamp'])
    parser.add_argument('--output', type=Path, default=ROOT / 'build/research/date-stamp/verify.json')
    args = parser.parse_args()
    from z30_extensions.build import build
    from z30_extensions.container import extract_eg
    stock = extract_eg(args.source.read_bytes())
    image, report = build(stock, args.preset)
    frame = Frame(stock, image, report)
    frames = check_frame_cases(frame)
    passthrough = check_passthrough_cases(frame)
    menu = check_menu_cases(stock, image, report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(
        image_sha256=report['sha256'], presets=args.preset, frames=frames, passthrough=passthrough, menu=menu,
        scope=__doc__, physical_verified=False), indent=2) + '\n')
    print(f'PASS date stamp: {len(frames)} frame parity, {len(passthrough)} gate, {len(menu)} menu cases '
          f'({args.preset}, code {report["features"]["date_stamp"]["code_bytes"]} bytes)')


if __name__ == '__main__':
    main()
