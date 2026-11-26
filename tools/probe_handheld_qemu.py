"""QEMU: the handheld page of a card build runs the built-in Game Boy demo full screen.

Two steps, because the card build needs Keystone and the QEMU session runs in WSL:

  python tools/probe_handheld_qemu.py --prepare --source Z_30_0120.bin --resident output/handheld-v1 --out build/handheld/card
  python3 tools/probe_handheld_qemu.py --qemu-root ../z30-qemu --card build/handheld/card --output-dir handheld-probe

The run boots stock firmware and does what the resident loader does on the
camera: it takes a block from the stock pool (64 KiB aligned), relocates the
WL.PATCH tail there and writes its edits (code edits straight to the code
address; QEMU has no DRAM alias). Load-time functions (the ready banner) are
not called. Then it opens the extension root, the Handheld page, touches Play
(no SD card in QEMU, so the built-in demo runs), saves frames, holds the A
touch zone (the demo inverts its palette while A is held), and leaves the page
by opening the setup menu, which must pause the game and give back every
input pointer and the timer. QEMU display model only; physical keys, touch
and speed are for the camera.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
APPMENU_INPUT = 0xc182e688 + 0x78
STOCK_INPUT = (0x103cf513, 0x103cf78f, 0x103cf897, 0x103cf897, 0x103cf8bf)
TIMER45 = 0xc20060b0 + 45 * 24
DEMO_COLOURS = {(0xe0, 0xf8, 0xd0), (0x88, 0xc0, 0x70), (0x34, 0x68, 0x56), (0x08, 0x18, 0x20)}


def prepare(a):
    from z30_extensions import card
    from z30_extensions.container import extract_eg
    stock = extract_eg(a.source.read_bytes())
    resident = card.load_resident(a.resident / card.RESIDENT_NAME, a.resident / card.REPORT_NAME)
    patch, report = card.build_card(stock, ['full', 'handheld'], resident, menu_tab=True, build_tag='qemu')
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / 'WL.PATCH').write_bytes(card.pack(patch))
    from z30_extensions.features.experimental import handheld_payload
    report['features']['handheld']['state_offset'] = handheld_payload.STATE_OFFSET
    (a.out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Prepared', a.out, len(patch.tail), 'tail bytes,', len(patch.edits), 'edits')


def run(a):
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session, png_from_ppm
    from z30_extensions import card
    from z30_extensions.features.card import loader as card_loader
    from menu_path import enter_parent, wait_built
    patch = card.parse((a.card / 'WL.PATCH').read_bytes())
    report = json.loads((a.card / 'report.json').read_text(encoding='utf-8'))
    menu, hh = report['features']['menu_framework'], report['features']['handheld']
    s = Session(a.output_dir); results = {}

    def write(address, data):
        for off in range(0, len(data), 1024):
            v = data[off:off + 1024]
            assert s.remote.command(f'M{address + off:x},{len(v):x}:' + v.hex()) == 'OK'
        assert s.remote.memory(address, len(data)) == data
    def word(address): return int.from_bytes(s.remote.memory(address, 4), 'little')
    def current(): return int(s.input(0x1046109a, [])['registers']['r0'], 16)
    def packet(contact, x, y):
        p = bytearray(60); struct.pack_into('<I', p, 0, 1)
        p[8], p[9] = contact, 1; struct.pack_into('<2H', p, 0x12, x, y)
        s.input(0x103ce7e6, [('scratch', 0)], payload=p); s.run_for(.5)
    def touch(x, y):
        packet(1, x, y); packet(0, x, y)
    def activate(page, ident, row):
        wait_built(s, page, row)
        for _ in range(20):
            s.input(0x1045d54c, [page, ident]); s.run_for(.5)
            if sum(struct.unpack('<2H', s.remote.memory(page + 22, 4))) == row: break
        for _ in range(3):
            touch(250, 86 + row * 52)
            if current() != page: break
    def shown(slot):
        length, ptr, _ = struct.unpack('<3I', s.remote.memory(0xc2013368 + slot * 12, 12))
        if not length: return ''
        if a.language == 'en': return s.remote.memory(ptr, length).decode('ascii', 'replace')
        return s.remote.memory(ptr, 2 * length).decode('utf-16-le', 'replace')
    def grab(name):
        raw = (s.graphics / 'gui-latest.ppm').read_bytes()
        (s.out / (name + '.png')).write_bytes(png_from_ppm(raw))
        head, pixels = raw.split(b'\n', 3)[:3], raw.split(b'\n', 3)[3]
        width, height = map(int, head[1].split())
        return width, height, pixels
    def pixel(img, x, y):
        width, _, pixels = img
        o = (y * width + x) * 3
        return tuple(pixels[o:o + 3])
    try:
        # The card loader's job, on stock firmware.
        raw = int(s.input(0x10211f4c, [len(patch.tail) + 0x10000, 0])['registers']['r0'], 16)
        assert raw, 'pool allocation'
        base = (raw + 0xffff) & ~0xffff
        delta = base - patch.link_base
        write(base, card.relocate(patch.tail, patch.relocs, delta))
        skipped = []
        for edit in patch.edits:
            old = s.remote.memory(edit.address, len(edit.expected))
            if old != edit.expected:
                # The resident EG's own loader trigger: on the camera gyro-evr takes it over.
                assert edit.address == card_loader.IMU_SITE, (edit.name, hex(edit.address))
                skipped.append(edit.name)
            write(edit.address, card.relocate(edit.replacement, edit.relocs, delta))
        # build_card's report comes from its last link, at the second check base.
        runtime = lambda linked: (linked & ~1) - card.CHECK_BASES[-1] + base
        results['card'] = dict(base=hex(base), tail=len(patch.tail), edits=len(patch.edits), trigger_edits=skipped)
        s.input(0x102355ee, [0xc182e688, 1]); s.run_for(2)
        lang, font = {'en': (3, 1), 'zh-Hans': (26, 8), 'zh-Hant': (27, 9)}[a.language]
        s.input(0x10465990, [0x1400, lang]); s.input(0x10452cc0, [font])
        s.input(0x1045f732, [2, 397]); s.run_for(.5)
        s.input(runtime(int(menu['functions']['open'], 16)), []); s.run_for(1)
        state = word(int(menu['state_anchor'], 16))                          # stock RAM anchor
        assert state, 'extension state'
        page = state + hh['page_offset']
        parent, control, row = enter_parent(menu, 'handheld', state, activate, s)
        activate(parent, control, row)
        assert current() == page, 'handheld page opened'
        wait_built(s, page, 3)
        labels = [shown(14 + i) for i in range(6)] + [shown(54), shown(55)]
        results['page'] = labels
        print('labels', labels, flush=True)
        assert labels[6] == 'Ready', labels
        grab('01-page')
        if a.dump_widgets:
            words = struct.unpack('<11I', s.remote.memory(page, 44))
            print('page', [hex(w) for w in words], flush=True)
            for r in range(4):
                w = int(s.input(0x1045745e, [words[9], 1, r])['registers']['r0'], 16)
                print('row', r, hex(w), w and [hex(x) for x in struct.unpack('<20I', s.remote.memory(w, 80))], flush=True)
            group = words[3]
            print('group', [hex(x) for x in struct.unpack('<20I', s.remote.memory(group, 80))], flush=True)
            return
        # QEMU's GPU model never completes vgWritePixels (the graphics task waits
        # forever), so the paint skips VG here and the checks read the composed screen.
        hstate = runtime(int(hh['payload'], 16)) + hh['state_offset']
        write(hstate + 29 * 4, struct.pack('<I', 1))
        activate(page, hh['controls'][0], 0)
        s.run_for(4)
        results['play'] = [shown(54), shown(55), hex(current()), hex(page)]
        print('after Play:', results['play'], flush=True)
        slots = [word(APPMENU_INPUT + 4 * i) for i in range(5)]
        payload = runtime(int(hh['payload'], 16))
        assert all(v != w for v, w in zip(slots, STOCK_INPUT)), [hex(v) for v in slots]
        # The tick re-arms slot 45 itself, so a stop between ticks may see it empty.
        ticks = word(hstate + 26 * 4)
        s.run_for(2)
        assert word(hstate + 21 * 4) == 1 and word(hstate + 26 * 4) > ticks and current() == page, \
            'game running on its page'
        screen, width, height = word(hstate + 12 * 4), word(hstate + 13 * 4), word(hstate + 14 * 4)
        def composed(name):
            data = s.remote.memory(screen, width * height * 4)
            rgb = bytearray(width * height * 3)      # 0x00RRGGBB words -> RGB
            rgb[0::3], rgb[1::3], rgb[2::3] = data[2::4], data[1::4], data[0::4]
            (s.out / (name + '.png')).write_bytes(png_from_ppm(b'P6\n%d %d\n255\n' % (width, height) + bytes(rgb)))
            return width, height, bytes(rgb)
        img = composed('02-game')
        results['surface'] = (width, height)
        centre = pixel(img, width // 2, height // 2)
        game = {pixel(img, x, y) for x in range(width // 2 - 200, width // 2 + 200, 7) for y in range(40, height - 40, 9)}
        results['game_colours'] = sorted(game)
        assert game <= DEMO_COLOURS | set(), game - DEMO_COLOURS
        assert len(game) >= 2, game
        # Hold the A zone (right edge, middle third): the demo inverts its palette.
        packet(1, width - 40, height // 2); s.run_for(2)
        held = composed('03-touch-a')
        inverted = {pixel(held, x, y) for x in range(width // 2 - 200, width // 2 + 200, 7) for y in range(40, height - 40, 9)}
        results['held_colours'] = sorted(inverted)
        assert pixel(held, width // 2, 60) != pixel(img, width // 2, 60), 'A held changes the picture'
        packet(0, width - 40, height // 2); s.run_for(2)
        released = composed('04-released')
        assert pixel(released, width // 2, 60) == pixel(img, width // 2, 60), 'release restores the palette'
        # Leaving the page pauses the game and hands everything back.
        s.input(0x1045f732, [2, 397]); s.run_for(2)
        after = [word(APPMENU_INPUT + 4 * i) for i in range(5)]
        assert tuple(after) == STOCK_INPUT, [hex(v) for v in after]
        assert not word(TIMER45) or word(TIMER45 + 16) != payload, 'timer released'
        grab('05-left')
        s.input(runtime(int(menu['functions']['open'], 16)), []); s.run_for(1)
        parent, control, row = enter_parent(menu, 'handheld', state, activate, s)
        activate(parent, control, row); wait_built(s, page, 3)
        results['after'] = [shown(54), shown(55)]
        grab('06-paused-page')
        s.audit.append(dict(handheld=results, physical_verified=False))
        s.write_audit()
        print('PASS handheld page:', json.dumps(results), flush=True)
    except Exception:
        if 'base' in locals():
            at = runtime(int(hh['payload'], 16)) + hh['state_offset']
            names = ('phase error detail gb rom ram fb bounce rom_size ram_size demo screen_raw screen '
                     'screen_w screen_h screen_bytes gx gy scale zones_dirty page active timer lock '
                     'vg_error painted ticks paints reason skip_vg').split()
            values = struct.unpack(f'<{len(names)}I', s.remote.memory(at, 4 * len(names)))
            print('state', {k: hex(v) for k, v in zip(names, values)}, flush=True)
            print('registers', s.remote.registers(), flush=True)
        raise
    finally:
        s.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--prepare', action='store_true')
    p.add_argument('--source', type=Path)
    p.add_argument('--resident', type=Path)
    p.add_argument('--out', type=Path)
    p.add_argument('--qemu-root', type=Path)
    p.add_argument('--card', type=Path)
    p.add_argument('--output-dir')
    p.add_argument('--dump-widgets', action='store_true')
    p.add_argument('--language', choices=('en', 'zh-Hans', 'zh-Hant'), default='en')
    a = p.parse_args()
    prepare(a) if a.prepare else run(a)


if __name__ == '__main__':
    main()
