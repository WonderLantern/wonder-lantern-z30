"""QEMU: the Gyro e-VR extension page opens, every choice saves and returns to the root.

Uses the diagnostic SD services fixture of verify_sound_settings_qemu.py
(--prepare-fixture), so WL-GYRO.CFG is a 16-byte RAM record here. For each choice
(Native crop, OFF, Enhanced, Standard) the page is opened from the Extensions
root, the row is touched, and the check expects: back on the root, the state word
holds the row, the saved record is WYG3/0/row/check, and reopening shows the mark
on that row only. Frames are saved per step. QEMU display model only; the
stabilizer reacting to the choice is covered by research/evr/emulate_builtin.py.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('qemu-root', 'image', 'report', 'fixture', 'output-dir'):
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--language', choices=('en', 'zh-Hans', 'zh-Hant'), default='zh-Hans')
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from display_session import Session, png_from_ppm
    from z30_extensions.features.ui_text import TRANSLATIONS
    from z30_extensions.features import gyro_ui
    report = json.loads(a.report.read_text()); image = a.image.read_bytes()
    fixture = json.loads(a.fixture.read_text())
    sound = report['features']['shutter_sound']; gyro = report['features']['gyro_evr_control']
    s = Session(str(a.output_dir.resolve())); observations = []
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
    def activate(page, ident, row):
        # A focus request sent while the page is still building is dropped (the CJK
        # pages build slower), and a touch then selects the old focus row. So wait
        # for the row's widget, focus it, and confirm the focus before touching.
        # Opening a page also queues a focus on its current choice, applied once the
        # (slower) CJK page is built; let that land first so it cannot undo ours.
        for _ in range(40):
            if current() == page and built(page, row): break
            s.run_for(.25)
        s.run_for(1.5)
        for _ in range(20):
            s.input(0x1045d54c, [page, ident]); s.run_for(.5)
            if sum(struct.unpack('<2H', s.remote.memory(page + 22, 4))) == row: break
        for _ in range(3):
            touch(250, 86 + row * 52)
            if current() != page: break
    def frame(name):
        samples = []
        for _ in range(5):
            s.run_for(.25); raw = (s.graphics / 'gui-latest.ppm').read_bytes()
            pixels = raw.split(b'\n', 3)[3]
            white = sum(all(v >= 190 for v in pixels[j:j + 3]) for j in range(0, len(pixels), 3))
            samples.append((white, len(samples), raw))
        raw = max(samples, key=lambda x: x[:2])[2]
        (s.out / (name + '.png')).write_bytes(png_from_ppm(raw))
        assert max(x[0] for x in samples) > 500
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
        for item in fixture['services']:
            write(item['address'], bytes.fromhex(item['code'])); write(item['site'], bytes.fromhex(item['jump']))
        write(fixture['config'], bytes(16))
        s.audit.append({'diagnostic_sd_services': True, 'physical_card': False})
        lang, font = {'en': (3, 1), 'zh-Hans': (26, 8), 'zh-Hant': (27, 9)}[a.language]
        s.input(0x10465990, [0x1400, lang]); s.input(0x10452cc0, [font])
        s.input(0x1045f732, [2, 397]); s.run_for(.5)
        s.input(int(sound['functions']['open'], 16) & ~1, []); s.run_for(1)
        state = word(int(sound['state_anchor'], 16)); assert state
        root = state + sound['page_offsets']['955']; page = state + gyro['page_offset']
        root_ids = sound['control_identifiers']['955']; ids = sound['control_identifiers'][str(gyro['page_index'])]
        category = next(c for c in sound['menu_categories'] if c['key'] == 'gyro')
        row, control = category['row'], root_ids[category['row']]
        slots = sound['text_slots'][str(gyro['page_index'])]
        def shown(slot):
            length, ptr, _ = struct.unpack('<3I', s.remote.memory(0xc2013368 + slot * 12, 12))
            data = s.remote.memory(ptr, length * (2 if lang in (26, 27) else 1)) if length else b''
            return data.decode('utf-16-le' if lang in (26, 27) else 'ascii')
        def local(text): return text if lang == 3 else TRANSLATIONS[text][lang == 27]
        assert current() == root
        frame('01-root')
        assert word(state + gyro['state_offset']) == gyro_ui.STANDARD, 'a fresh state must read Standard'
        activate(root, control, row); assert current() == page
        assert shown(slots['title']) == local('Gyro e-VR'), shown(slots['title'])
        assert [shown(slots['label'] + i) for i in range(gyro_ui.ROWS)] == [local(t) for t in gyro_ui.LABELS]
        frame('02-page')
        for attempt in range(3):
            touch(675, 28)
            if current() == root: break
        assert current() == root, 'back from the page'
        for value in (gyro_ui.NATIVE, gyro_ui.OFF, gyro_ui.ENHANCED, gyro_ui.STANDARD):
            activate(root, control, row); assert current() == page
            activate(page, ids[value], value); s.run_for(.5)
            assert current() == root, (value, hex(current()), hex(root))
            assert word(state + gyro['state_offset']) == value, (value, word(state + gyro['state_offset']))
            magic, reserved, selected, check = struct.unpack('<4I', s.remote.memory(fixture['config'], 16))
            assert (magic, reserved, selected, check) == (gyro_ui.MAGIC, 0, value, ~(gyro_ui.MAGIC ^ value) & 0xffffffff)
            frame(f'03-selected-{value}')
            activate(root, control, row); assert current() == page
            marks = [shown(slots['value'] + i) for i in range(gyro_ui.ROWS)]
            assert marks == ['*' if i == value else '' for i in range(gyro_ui.ROWS)], marks
            frame(f'04-marker-{value}')
            for attempt in range(3):
                touch(675, 28)
                if current() == root: break
            assert current() == root, (hex(current()), hex(root))
            observations.append(dict(selected=value, marks=marks))
        s.audit.append(dict(gyro_menu=observations, language=a.language,
                            diagnostic_sd_services=True, physical_verified=False))
        s.write_audit()
        print('PASS native Gyro e-VR menu:', a.language, flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
