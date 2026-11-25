"""QEMU: the extension root lists categories; each opens, lists its pages and returns.

From the Extensions root (opened through the framework `open`): for every
category row, open it and check the page, its title and row labels and that the
focus sits on the first row; open every member page from it and go back with the
back button (expect the category page), then back to the root. With --fixture
(the diagnostic SD services of verify_sound_settings_qemu.py --prepare-fixture)
it also confirms a choice on the first member that has a plain choice list and
expects the confirmed choice to return to the category page. Frames are saved
per step. QEMU display model and synthetic touches only; not hardware evidence.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

BACK = (675, 28)
# Pages whose first rows are plain choices that save and return (key: row to pick).
CHOICE = {'zebra': 0, 'nef': 0, 'gyro': 0, 'trk3d': 0, 'afcol': 0, 'bitrate': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('qemu-root', 'image', 'report', 'output-dir'):
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--fixture', type=Path)
    p.add_argument('--language', choices=('en', 'zh-Hans', 'zh-Hant'), default='zh-Hans')
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from display_session import Session, png_from_ppm
    from z30_extensions.features.ui_text import TRANSLATIONS
    report = json.loads(a.report.read_text()); image = a.image.read_bytes()
    fixture = json.loads(a.fixture.read_text()) if a.fixture else None
    menu = report['features']['menu_framework']
    assert menu['category_pages'], 'the build has no category pages'
    s = Session(str(a.output_dir.resolve())); checks = []
    s.audit.append({'candidate_sha256': hashlib.sha256(image).hexdigest(), 'selected_presets': report['selected_presets']})

    def write(address, data):
        for off in range(0, len(data), 1024):
            v = data[off:off + 1024]
            assert s.remote.command(f'M{address + off:x},{len(v):x}:' + v.hex()) == 'OK'
        assert s.remote.memory(address, len(data)) == data
    def word(address): return int.from_bytes(s.remote.memory(address, 4), 'little')
    def current(): return int(s.input(0x1046109a, [])['registers']['r0'], 16)
    def touch(x, y):
        for contact in (1, 0):
            packet = bytearray(60); struct.pack_into('<I', packet, 0, 1)
            packet[8], packet[9] = contact, 1; struct.pack_into('<2H', packet, 0x12, x, y)
            s.input(0x103ce7e6, [('scratch', 0)], payload=packet); s.run_for(.5)
    def built(page, row):
        registry = word(page + 36)
        return registry and int(s.input(0x1045745e, [registry, 1, row])['registers']['r0'], 16)
    def settle(page, row=0):
        for _ in range(40):
            if current() == page and built(page, row): break
            s.run_for(.25)
        s.run_for(1.5)
    def activate(page, ident, row):
        # Same as verify_gyro_menu_qemu: wait for the row's widget, focus it, then touch.
        settle(page, row)
        for _ in range(20):
            s.input(0x1045d54c, [page, ident]); s.run_for(.5)
            if sum(struct.unpack('<2H', s.remote.memory(page + 22, 4))) == row: break
        for _ in range(3):
            touch(250, 86 + row * 52)
            if current() != page: break
    def back(expect):
        for _ in range(4):
            touch(*BACK)
            if current() == expect: break
        settle(expect)
    def frame(name):
        samples = []
        for _ in range(5):
            s.run_for(.25); raw = (s.graphics / 'gui-latest.ppm').read_bytes()
            pixels = raw.split(b'\n', 3)[3]
            white = sum(all(v >= 190 for v in pixels[j:j + 3]) for j in range(0, len(pixels), 3))
            samples.append((white, len(samples), raw))
        raw = max(samples, key=lambda x: x[:2])[2]
        (s.out / (name + '.png')).write_bytes(png_from_ppm(raw))
        assert max(x[0] for x in samples) > 500, name
        s.audit.append({'frame': name, 'white_pixels': max(x[0] for x in samples)})
    try:
        scatter = []
        for e in report['edits']:
            address = 0x10020000 + int(e['offset'], 16)
            old, new = bytes.fromhex(e['expected_hex']), bytes.fromhex(e['replacement_hex'])
            assert s.remote.memory(address, len(old)) == old
            write(address, new)
            if 0x11f81c94 <= address and address + len(old) <= 0x12079eec:
                scatter.append((0xc1800000 + address - 0x11f81c94, new))
        size = (a.qemu_root / '.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000 + size - 2, image[size - 2:])
        s.input(0x102355ee, [0xc182e688, 1]); s.run_for(2)
        for address, new in scatter: write(address, new)
        if fixture:
            for item in fixture['services']:
                write(item['address'], bytes.fromhex(item['code'])); write(item['site'], bytes.fromhex(item['jump']))
        lang, font = {'en': (3, 1), 'zh-Hans': (26, 8), 'zh-Hant': (27, 9)}[a.language]
        s.input(0x10465990, [0x1400, lang]); s.input(0x10452cc0, [font])
        s.input(0x1045f732, [2, 397]); s.run_for(.5)
        s.input(int(menu['functions']['open'], 16) & ~1, []); s.run_for(1)
        state = word(int(menu['state_anchor'], 16)); assert state
        offsets = {int(k): state + v for k, v in menu['page_offsets'].items()}
        root = offsets[955]
        def shown(slot):
            length, ptr, _ = struct.unpack('<3I', s.remote.memory(0xc2013368 + slot * 12, 12))
            data = s.remote.memory(ptr, length * (2 if lang in (26, 27) else 1)) if length else b''
            return data.decode('utf-16-le' if lang in (26, 27) else 'ascii')
        def local(text): return text if lang == 3 else TRANSLATIONS.get(text, (text, text))[lang == 27]
        settle(root); assert current() == root
        frame('00-root')
        root_ids = menu['control_identifiers']['955']
        for key, cat in menu['category_pages'].items():
            page = offsets[cat['index']]
            activate(root, root_ids[cat['root_row']], cat['root_row'])
            settle(page)
            assert current() == page, (key, hex(current()), hex(page))
            assert shown(cat['title_slot']) == local(cat['title']), (key, shown(cat['title_slot']))
            cursor = struct.unpack('<2H', s.remote.memory(page + 22, 4))
            assert cursor == (0, 0), (key, cursor)
            frame(f'10-{key}')
            # The row labels as the widgets get them (the slot strings), to tell a damaged
            # string from a glyph the renderer leaves out.
            labels = [shown(slot) for slot in menu['category_rows'][key]] if 'category_rows' in menu else []
            ids = menu['control_identifiers'][str(cat['index'])]
            visits = []
            for row, member in enumerate(cat['members']):
                target = offsets[menu['menu_paths'][member][-1]['opens']]
                activate(page, ids[row], row); settle(target)
                assert current() == target, (key, member, hex(current()), hex(target))
                assert word(target + 4) == page, (key, member, 'parent')
                frame(f'11-{key}-{member}')
                back(page)
                assert current() == page, (key, member, 'back', hex(current()))
                visits.append(member)
            chosen = None
            if fixture:
                member = next((m for m in cat['members'] if m in CHOICE), None)
                if member:
                    target = offsets[menu['menu_paths'][member][-1]['opens']]
                    activate(page, ids[cat['members'].index(member)], cat['members'].index(member)); settle(target)
                    tids = menu['control_identifiers'][str(menu['menu_paths'][member][-1]['opens'])]
                    activate(target, tids[CHOICE[member]], CHOICE[member]); settle(page)
                    assert current() == page, (key, member, 'choice returns', hex(current()))
                    frame(f'12-{key}-{member}-chosen')
                    chosen = member
            back(root)
            assert current() == root, (key, 'back to root', hex(current()))
            frame(f'19-{key}-root')
            checks.append(dict(category=key, title=shown(cat['title_slot']), row_labels=labels, opened_members=visits,
                               choice_returned_to_category=chosen))
            print(json.dumps(checks[-1], ensure_ascii=False), flush=True)
        status = menu.get('status_page')
        if status:
            # Status page: system rows show their texts, value rows open their page and return.
            page = offsets[status['index']]
            assert shown(status['root_value_slot']) == local('Normal'), shown(status['root_value_slot'])
            activate(root, root_ids[status['root_row']], status['root_row']); settle(page)
            assert current() == page, ('status', hex(current()))
            assert shown(status['title_slot']) == local('Status')
            card = local('Loaded') if status['card'] else local('Built in')
            count = status['features']
            features = (f'{count} features' if lang == 3 else f'{count} 项' if lang == 26 else f'{count} 項')
            expected = dict(card=card, version=status['build_tag'], features=features, settings=local('Saved'))
            values = {key: shown(status['value_slots'][key]) for key in status['rows'] if key in expected}
            assert values == {key: expected[key] for key in values}, values
            frame('20-status')
            ids = status['control_ids']; opened = []
            for row, key in enumerate(status['rows']):
                if key in expected:
                    continue
                target = offsets[menu['menu_paths'][key][-1]['opens']]
                activate(page, ids[row], row); settle(target)
                assert current() == target and word(target + 4) == page, (key, hex(current()))
                frame(f'21-status-{key}')
                back(page); assert current() == page, (key, 'back to status', hex(current()))
                opened.append(key)
            # A failed save turns the settings row and the root's status value to Check.
            write(state + status['flag_offset'], struct.pack('<I', 1))
            back(root); s.input(int(menu['functions']['labels'], 16) & ~1, []); s.run_for(1)
            assert shown(status['root_value_slot']) == local('Check'), shown(status['root_value_slot'])
            assert shown(status['value_slots']['settings']) == local('Not saved')
            frame('22-root-check')
            write(state + status['flag_offset'], bytes(4))
            checks.append(dict(status=values, opened=opened, save_failure_shows_check=True))
            print(json.dumps(checks[-1], ensure_ascii=False), flush=True)
        s.audit.append(dict(menu_categories=checks, language=a.language,
                            diagnostic_sd_services=bool(fixture), physical_verified=False))
        s.write_audit()
        print('PASS', len(checks), 'categories', flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
