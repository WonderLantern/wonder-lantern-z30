"""Execute the 4K readout-window rewrite offline over real drive data; never a camera.

The stock selector, sequence writer and serial writer run in Unicorn (SIO and
GPIO stubbed). At 0x1019D870, after the displaced prologue, the bytes the stock
writer will send are compared with a stock-image run for every command and
drive type, for each preset.

Usage:
  python tools/verify_cmos_window.py --dump-dir build/research/cmos-ab --output build/research/cmos-window-verify.json
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from z30_extensions.container import extract_eg
from z30_extensions.features.research import cmos_window as cw
from z30_extensions.features.menu.layout import STATE_ANCHOR, MAGIC as SOUND_MAGIC
from build_cmos_dump import build
from decode_cmos_modes import COMMANDS, TYPES
from verify_cmos_spi import Machine
from verify_cmos_dump import Fixture as MenuFixture, STATE

SOUND_STATE = 0x54000000


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--dump-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    stock = extract_eg(args.source.read_bytes())
    image, report = build(stock, window=True)
    feature = report['features']['cmos_window']
    header = json.loads((args.dump_dir / 'header.json').read_text(encoding='utf-8'))
    blob = (args.dump_dir / 'drive-data.bin').read_bytes()
    context = (args.dump_dir / 'context.bin').read_bytes()
    pointer = int(header['pointer'], 16)
    reference = Machine(stock, stock, blob, context, pointer)
    patched = Machine(stock, image, blob, context, pointer)
    state = SOUND_STATE + feature['state_offset']
    size = feature['state_offset'] + cw.STATE_BYTES + 64
    patched.cx.map(SOUND_STATE, size); patched.u.mem_write(SOUND_STATE, bytes(size))
    patched.put(SOUND_STATE + 56, SOUND_MAGIC); patched.put(STATE_ANCHOR + 16, SOUND_STATE)
    run = lambda machine: {(cmd, t): machine.select(cmd, t) for cmd in COMMANDS for t in TYPES}
    expected = run(reference)
    four_k = expected[(4, 6)][1][0]
    assert four_k[:5] == bytes(cw.SIGNATURE) and four_k[13:17] == struct.pack('<2H', cw.STOCK_START, cw.STOCK_LINES)
    cases = []
    for preset, (label, value) in enumerate(cw.PRESETS):
        patched.put(state + cw.WINDOW_PRESET, preset)
        got = run(patched)
        changed = [(key, i) for key in expected for i, (a, b) in enumerate(zip(expected[key][1], got[key][1])) if a != b]
        assert all(got[key][0] == expected[key][0] and len(got[key][1]) == len(expected[key][1]) for key in expected)
        if value is None:
            assert not changed, changed
        else:
            # Only the first packet of command 4 for drive type 6 (4K) differs, exactly in bytes 13..16.
            assert changed == [((4, 6), 0)], changed
            sent = got[(4, 6)][1][0]
            want = four_k[:13] + struct.pack('<2H', *value) + four_k[17:]
            assert sent == want, sent.hex()
        assert bytes(patched.u.mem_read(pointer, len(blob))) == blob, 'drive data changed'
        cases.append(dict(name=f'preset_{preset}_{label}', passed=True, changed_packets=len(changed),
                          sent=got[(4, 6)][1][0].hex()))
    # 1080p (types 7/8) uses the same rows at another density; it must never be rewritten.
    patched.put(state + cw.WINDOW_PRESET, 2)
    for t in (7, 8, 9):
        assert patched.select(4, t) == expected[(4, t)]
    cases.append(dict(name='other_drive_types_untouched_under_3x2_preset', passed=True))
    patched.put(state + cw.WINDOW_PRESET, 7)
    assert run(patched) == expected
    cases.append(dict(name='out_of_range_preset_is_ignored', passed=True))

    # Raw-size getter: only 5600x3174 (codes 4 and 8) follows the preset; offsets and other sizes unchanged.
    REQ, OUT, OFF = 0x57100000, 0x57100100, 0x57100200
    for machine in (reference, patched):
        machine.cx.map(REQ, 0x1000)
    def sizes(machine):
        out = {}
        for code in range(16):
            for flag in (0, 1):
                request = bytearray(16); request[0] = code; request[2] = flag
                machine.u.mem_write(REQ, bytes(request)); machine.u.mem_write(OUT, bytes([0xEE]) * 8); machine.u.mem_write(OFF, bytes([0xEE]) * 8)
                machine.cx.call(cw.RAW_SIZE, (REQ, OUT, OFF))
                out[(code, flag)] = (bytes(machine.u.mem_read(OUT, 4)), bytes(machine.u.mem_read(OFF, 4)))
        return out
    stock_sizes = sizes(reference)
    for preset, (label, value) in enumerate(cw.PRESETS):
        patched.put(state + cw.WINDOW_PRESET, preset)
        got = sizes(patched)
        for key, (size, offset) in stock_sizes.items():
            want = size
            if value and size == struct.pack('<2H', cw.RAW_WIDTH, cw.STOCK_LINES):
                want = struct.pack('<2H', cw.RAW_WIDTH, value[1])
            assert got[key] == (want, offset), (preset, key, got[key], want)
        changed = sorted(k[0] for k in got if got[k][0] != stock_sizes[k][0])
        assert changed == ([] if value is None else [4, 4, 8, 8]), changed
        cases.append(dict(name=f'raw_size_preset_{preset}', passed=True, changed_codes=sorted(set(changed))))

    # Capture-record lookups: Stock returns the ROM pointers; a preset serves the 4K records
    # (codes 4 and 8) as RAM copies that differ only in the height halfword.
    XY = 0x57100300
    def records(machine):
        out = {}
        for code in range(18):
            for variant in range(4):
                for key, spec in cw.LOOKUPS.items():
                    ptr = machine.cx.call(spec['site'], (code, variant)) & 0xFFFFFFFF
                    out[(key, code, variant)] = (ptr, bytes(machine.u.mem_read(ptr, spec['size'])) if ptr else b'')
                machine.u.mem_write(XY, bytes([0xEE]) * 4)
                ok = machine.cx.call(cw.LOOKUP_XY, (code, variant, XY)) & 0xFFFFFFFF
                out[('xy', code, variant)] = (ok, bytes(machine.u.mem_read(XY, 4)))
        return out
    stock_records = records(reference)
    for preset, (label, value) in enumerate(cw.PRESETS):
        patched.put(state + cw.WINDOW_PRESET, preset)
        got = records(patched)
        moved = []
        for key, (ptr, data) in stock_records.items():
            gptr, gdata = got[key]
            if key[0] == 'xy' or value is None or ptr not in cw.LOOKUPS[key[0]]['records']:
                assert (gptr, gdata) == (ptr, data), (preset, key)
                continue
            spec = cw.LOOKUPS[key[0]]
            want = bytearray(data); struct.pack_into('<H', want, spec['height'], value[1])
            if ptr == cw.LV_RECORD:
                assert struct.unpack_from('<H', data, spec['lv_output'])[0] == 794
                struct.pack_into('<H', want, spec['lv_output'], (value[1] // 4) & ~1)
            assert gptr != ptr and gdata == bytes(want), (preset, key)
            assert struct.unpack_from('<H', data, spec['height'])[0] == cw.STOCK_LINES
            moved.append(key)
        assert (value is None) == (not moved), moved
        cases.append(dict(name=f'capture_records_preset_{preset}', passed=True,
                          served_copies=sorted({(k[0], k[1]) for k in moved})))

    # Crop window: the writer must receive the firmware struct for Stock or any non-4K size,
    # and a copy differing only in the height for 5600x3174 under a preset.
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_LR, UC_ARM_REG_PC
    REC = 0x57100400
    seen_writer = []
    def at_writer(uc, address, size, _):
        r1 = uc.reg_read(UC_ARM_REG_R1)
        seen_writer.append((uc.reg_read(UC_ARM_REG_R0), r1, bytes(uc.mem_read(r1, cw.CROP_BYTES))))
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    for machine in (reference, patched):
        machine.u.hook_add(UC_HOOK_CODE, at_writer, begin=cw.CROP_WRITER, end=cw.CROP_WRITER)
    def crop(machine, width, height):
        rec = bytearray(range(cw.CROP_BYTES)); struct.pack_into('<2H', rec, 0x22, width, height)
        machine.u.mem_write(REC, bytes(rec)); seen_writer.clear()
        machine.cx.call(cw.CROP_WRAPPER, (REC,))
        assert len(seen_writer) == 1 and bytes(machine.u.mem_read(REC, cw.CROP_BYTES)) == bytes(rec), 'struct changed'
        return seen_writer[0], bytes(rec)
    for preset, (label, value) in enumerate(cw.PRESETS):
        patched.put(state + cw.WINDOW_PRESET, preset)
        for width, height in ((5600, 3174), (5600, 1058), (5600, 3396), (1864, 1242)):
            (ctx, ptr, got), rec = crop(patched, width, height)
            (sctx, sptr, sgot), _ = crop(reference, width, height)
            assert ctx == sctx, (hex(ctx), hex(sctx))
            if value and (width, height) == (5600, cw.STOCK_LINES):
                want = bytearray(rec); struct.pack_into('<H', want, 0x24, value[1])
                assert ptr != REC and got == bytes(want), (preset, got.hex())
            else:
                assert ptr == REC and got == rec, (preset, width, height)
        cases.append(dict(name=f'crop_window_preset_{preset}', passed=True))

    # Live-view frame size: only the 16:9 entry (aspect 2) follows the preset.
    def lv(machine):
        out = []
        for aspect in range(3):
            ptr = machine.cx.call(cw.LV_SIZE_GETTER, (aspect,)) & 0xFFFFFFFF
            out.append((ptr, struct.unpack('<2H', machine.u.mem_read(ptr, 4))))
        return out
    stock_lv = lv(reference)
    assert [v for _, v in stock_lv] == [(1400, 932), (0, 0), (1400, 794)], stock_lv
    for preset, (label, value) in enumerate(cw.PRESETS):
        patched.put(state + cw.WINDOW_PRESET, preset)
        got = lv(patched)
        assert got[:2] == stock_lv[:2]
        if value is None:
            assert got[2] == stock_lv[2]
        else:
            assert got[2][1] == (1400, (value[1] // 4) & ~1), got
        cases.append(dict(name=f'live_view_size_preset_{preset}', passed=True, size=list(got[2][1])))

    # Buffer planner: only a planned 1400x794 (4K live view) follows the preset.
    PLAN, SET = 0x57110000, 0x57120000
    for machine in (reference, patched):
        machine.cx.map(PLAN, 0x1000); machine.cx.map(SET, 0x4000)
    def planned(machine, mode, movie, flags):
        settings = bytearray(0x3000); settings[0] = mode; struct.pack_into('<I', settings, 4, movie)
        settings[0x4d5], settings[0x4d6] = flags
        machine.u.mem_write(SET, bytes(settings)); machine.u.mem_write(PLAN, bytes(16))
        machine.cx.call(cw.PLANNER, (PLAN, SET))
        return struct.unpack('<8H', machine.u.mem_read(PLAN, 16))
    variants = [(m, mv, fl) for m in (0, 1, 2) for mv in (0, 1) for fl in ((0, 0), (2, 0), (2, 2), (1, 1))]
    stock_plans = {v: planned(reference, *v) for v in variants}
    assert any(p[2:4] == (1400, 794) for p in stock_plans.values()), stock_plans
    for preset, (label, value) in enumerate(cw.PRESETS):
        patched.put(state + cw.WINDOW_PRESET, preset)
        for v, want in stock_plans.items():
            got = planned(patched, *v)
            if value and want[2:4] == (1400, 794):
                h = (value[1] // 4) & ~1
                exp = list(want); exp[3] = h
                if want[5] == 794: exp[5] = h
                assert got == tuple(exp), (v, got)
            else:
                assert got == want, (preset, v, got, want)
        cases.append(dict(name=f'planner_preset_{preset}', passed=True))

    menu = MenuFixture(stock, image, dict(functions=dict(cmos_export=feature['functions']['window_cycle'])))
    from verify_cmos_dump import BLOB
    from z30_extensions.features.research import cmos_dump as cdump
    menu.reset(); menu.u.mem_write(STATE, bytes(cw.STATE_BYTES))
    heights = [BLOB + off for off in cw.HEIGHT_OFFSETS]
    for h in heights:
        menu.u.mem_write(h, struct.pack('<H', cw.STOCK_LINES))
    neighbours = [bytes(menu.u.mem_read(h - 6, 16)) for h in heights]
    seen = []
    display_seen = []
    stock_display = [struct.unpack('<I', menu.u.mem_read(a, 4))[0] for a, _ in cw.DISPLAY_FIELDS]
    assert stock_display == [v[0] for _, v in cw.DISPLAY_FIELDS], stock_display
    for _ in range(4):
        status = menu.run()
        display_seen.append([struct.unpack('<I', menu.u.mem_read(a, 4))[0] for a, _ in cw.DISPLAY_FIELDS])
        values = tuple(struct.unpack('<H', menu.u.mem_read(h, 2))[0] for h in heights)
        seen.append((struct.unpack('<I', menu.u.mem_read(STATE + cw.WINDOW_PRESET, 4))[0], status, values))
        for h, before in zip(heights, neighbours):
            around = bytes(menu.u.mem_read(h - 6, 16))
            assert around[:6] == before[:6] and around[8:] == before[8:], 'neighbouring bytes changed'
    three = lambda v: (v, v, v)
    assert seen == [(1, cw.WINDOW_STATUS + 1, three(3238)), (2, cw.WINDOW_STATUS + 2, three(3396)),
                    (0, cw.WINDOW_STATUS, three(cw.STOCK_LINES)), (1, cw.WINDOW_STATUS + 1, three(3238))], seen
    cases.append(dict(name='menu_cycles_presets_and_three_4k_heights', passed=True, sequence=seen))
    assert display_seen == [[v[p] for _, v in cw.DISPLAY_FIELDS] for p in (1, 2, 0, 1)], display_seen
    cases.append(dict(name='menu_cycles_display_crop_table', passed=True, sequence=display_seen))
    menu.u.mem_write(heights[1], struct.pack('<H', 1234))
    menu.run()
    assert struct.unpack('<H', menu.u.mem_read(heights[1], 2))[0] == 1234
    assert struct.unpack('<H', menu.u.mem_read(heights[0], 2))[0] == 3396
    menu.word(cdump.CTX + cdump.BLOB_FIELD, 0)
    menu.run()
    cases.append(dict(name='geometry_untouched_for_unknown_value_or_missing_data', passed=True))

    result = dict(image_sha256=hashlib.sha256(image).hexdigest(), feature=feature['window'], cases=cases, passed=True,
                  limitations=['SIO and GPIO are stubbed; whether the sensor accepts the window is a camera test.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'{len(cases)} cases passed')


if __name__ == '__main__':
    main()
