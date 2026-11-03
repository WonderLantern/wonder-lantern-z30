"""Native QEMU checks of direct rows in the stock photo and movie menus.

Rows are reached through the stock tab bar, then opened and left with synthetic
touch packets. The same pages are opened from the custom menu to show the parent
follows the entry path. SD services are the explicit diagnostic models of the fixture.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from z30_extensions.features.ui_text import TRANSLATIONS

TAB = 0xc1832d50
TAB_X, PHOTO_TAB_Y = 32, 90
EVENT_UP, EVENT_DOWN, EVENT_RIGHT = 0x253, 0x254, 0x25b
LABELS = dict(aspect='Photo aspect', zebra='Photo zebra', stamp='Date stamp', bitrate='Video bitrate')
# A choice that writes the record without depending on the area of the current lens.
CHOICE = {'aspect': 2, 'zebra': 1, 'stamp': 1, 'bitrate': 1}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('qemu-root', 'image', 'report', 'fixture', 'output-dir'):
        p.add_argument('--'+key, type=Path, required=True)
    p.add_argument('--language', choices=('en', 'zh-Hans', 'zh-Hant'), default='zh-Hans')
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve()/'tools'))
    from display_session import Session, png_from_ppm
    report = json.loads(a.report.read_text())
    image = a.image.read_bytes()
    fixture = json.loads(a.fixture.read_text())
    sound = report['features']['shutter_sound']
    s = Session(str(a.output_dir.resolve()))
    s.audit.append({'candidate_sha256': hashlib.sha256(image).hexdigest(),
                    'selected_presets': report['selected_presets']})
    observations = []

    def write(address, data):
        for off in range(0, len(data), 1024):
            v = data[off:off+1024]
            assert s.remote.command(f'M{address+off:x},{len(v):x}:'+v.hex()) == 'OK'
        assert s.remote.memory(address, len(data)) == data

    def word(address): return int.from_bytes(s.remote.memory(address, 4), 'little')
    def half(address): return int.from_bytes(s.remote.memory(address, 2), 'little')

    def active():
        return int(s.input(0x1046109a, [])['registers']['r0'], 16)

    def tab_child():
        """The stock menu shown now: the active page, or the tab container's current child."""
        pointer = active()
        return word(pointer+8) if pointer == TAB else pointer

    def touch(x, y):
        for contact in (1, 0):
            packet = bytearray(60)
            struct.pack_into('<I', packet, 0, 1)
            packet[8], packet[9] = contact, 1
            struct.pack_into('<2H', packet, 0x12, x, y)
            s.input(0x103ce7e6, [('scratch', 0)], payload=packet)
            s.run_for(.5)

    def frame(name):
        samples = []
        for _ in range(5):
            s.run_for(.25)
            raw = (s.graphics/'gui-latest.ppm').read_bytes()
            pixels = raw.split(b'\n', 3)[3]
            white = sum(all(v >= 190 for v in pixels[j:j+3]) for j in range(0, len(pixels), 3))
            samples.append((white, len(samples), raw))
        raw = max(samples, key=lambda x: x[:2])[2]
        (s.out/(name+'.png')).write_bytes(png_from_ppm(raw))
        assert max(x[0] for x in samples) > 500, name
        s.audit.append({'frame': name, 'white_pixels': max(x[0] for x in samples)})

    def slot_text(slot, chinese):
        length, pointer, _ = struct.unpack('<3I', s.remote.memory(0xc2013368+slot*12, 12))
        data = s.remote.memory(pointer, length*(2 if chinese else 1)) if length else b''
        return data.decode('utf-16-le' if chinese else 'ascii')

    def open_tab(menu_object):
        """Touch the photo tab, then step the stock tab bar to the wanted shooting menu."""
        touch(TAB_X, PHOTO_TAB_Y)
        s.run_for(.5)
        for _ in range(6):
            if tab_child() == menu_object:
                s.audit.append({'tab_selected': hex(menu_object)})
                return
            s.input(0x103ce96e, [EVENT_DOWN], seconds=15)
            s.run_for(.7)
        raise AssertionError(f'Tab bar did not reach {menu_object:#x}, last {tab_child():#x}')

    def menu_rows(menu_object):
        return half(word(menu_object+40)+20)

    def press_row(menu_object, row, control):
        """Walk the stock list with up events so it scrolls, then touch the row on screen."""
        if active() == TAB:
            s.input(0x103ce96e, [EVENT_RIGHT], seconds=15)
            s.run_for(.7)
        assert active() == menu_object, hex(active())
        for _ in range(menu_rows(menu_object)+2):
            if half(menu_object+22)+half(menu_object+24) == row:
                break
            s.input(0x103ce96e, [EVENT_UP], seconds=15)
            s.run_for(.5)
        cursor = half(menu_object+22)
        assert half(menu_object+24)+cursor == row, (half(menu_object+24), cursor, row)
        frame(f'focus-{control:x}')
        before = active()
        touch(250, 86+cursor*52)
        s.run_for(.5)
        s.audit.append({'row': row, 'screen_position': cursor, 'touch_opened': active() != before})

    try:
        scatter = []
        for e in report['edits']:
            address = 0x10020000+int(e['offset'], 16)
            old, new = bytes.fromhex(e['expected_hex']), bytes.fromhex(e['replacement_hex'])
            assert s.remote.memory(address, len(old)) == old
            write(address, new)
            if 0x11f81c94 <= address and address+len(old) <= 0x12079eec:
                scatter.append((0xc1800000+address-0x11f81c94, new))
        size = (a.qemu_root/'.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000+size-2, image[size-2:])
        s.input(0x102355ee, [0xc182e688, 1])
        s.run_for(2)
        for address, new in scatter:
            write(address, new)
        for item in fixture['services']:
            write(item['address'], bytes.fromhex(item['code']))
            write(item['site'], bytes.fromhex(item['jump']))
        write(fixture['config'], bytes(16))
        s.audit.append({'diagnostic_sd_services': True, 'physical_card': False})
        lang, font = {'en': (3, 1), 'zh-Hans': (26, 8), 'zh-Hant': (27, 9)}[a.language]
        chinese = a.language != 'en'
        s.input(0x10465990, [0x1400, lang])
        s.input(0x10452cc0, [font])
        s.input(0x1045f732, [2, 397])
        s.run_for(1)

        for name, menu in sound['native_rows'].items():
            menu_object = int(menu['object'], 16)
            open_tab(menu_object)
            frame(f'{name}-00-menu')
            metadata = word(menu_object+40)
            total = menu['first_row']+len(menu['rows'])
            assert half(metadata+20) == total, (name, half(metadata+20))
            for item in menu['rows']:
                key, row, control = item['key'], item['row'], item['control_id']
                controls = word(metadata+12)
                flags = word(controls+row*28+16)
                assert flags == (0x3001 if chinese else 0x1201), (key, hex(flags))
                assert word(controls+row*28) == item['label_slot']
                # The text is seeded by the menu's own init hook, before any custom page opened.
                label = slot_text(item['label_slot'], chinese)
                expected = LABELS[key] if not chinese else TRANSLATIONS[LABELS[key]][a.language == 'zh-Hant']
                assert label == expected, (key, label)

                # Row -> feature page -> stock back -> the same shooting menu.
                press_row(menu_object, row, control)
                s.run_for(1)
                state = word(int(sound['state_anchor'], 16))
                assert state, 'State block missing after opening a direct row'
                page = state+sound['page_offsets'][str(item['page_index'])]
                assert active() == page, (key, hex(active()), hex(page))
                assert word(page+4) == TAB, (key, hex(word(page+4)))
                frame(f'{name}-{key}-1-page')
                if key == 'stamp':
                    # The color row reopens its own page; that must not bury the way back.
                    color_row = report['features']['date_stamp_control']['color_row']
                    s.input(0x1045d54c, [page, sound['control_identifiers'][str(item['page_index'])][color_row]])
                    s.run_for(1)
                    touch(250, 86+color_row*52)
                    s.run_for(1)
                    assert active() == page, (key, 'color row', hex(active()))
                    frame(f'{name}-{key}-1b-color')
                presses = 0
                for presses in range(1, 4):
                    touch(675, 28)
                    if active() != page:
                        break
                assert tab_child() == menu_object, (key, hex(tab_child()))
                s.audit.append(dict(direct_row_back=key, presses=presses))
                s.run_for(1)
                frame(f'{name}-{key}-2-back')

                # Row -> confirmed choice -> the same shooting menu, with the record saved.
                press_row(menu_object, row, control)
                s.run_for(1)
                assert active() == page
                choice = CHOICE[key]
                ids = sound['control_identifiers'][str(item['page_index'])]
                frame(f'{name}-{key}-3-before-choice')
                if key == 'stamp':
                    # A format chosen after the page reopened itself must still leave it.
                    control = report['features']['date_stamp_control']
                    color_offset = control['color_offset']
                    for style_row in (control['color_row'], control['glow_row']):
                        previous = word(state+color_offset)
                        s.input(0x1045d54c, [page, ids[style_row]])
                        s.run_for(1)
                        for _ in range(3):
                            touch(250, 86+style_row*52)
                            s.run_for(.5)
                            if word(state+color_offset) != previous:
                                break
                        assert word(state+color_offset) != previous and active() == page, (key, 'color step')
                    frame(f'{name}-{key}-3b-after-colors')
                s.input(0x1045d54c, [page, ids[choice]])
                s.run_for(1)
                for _ in range(3):
                    touch(250, 86+choice*52)
                    s.run_for(.5)
                    if active() != page:
                        break
                assert active() == menu_object, (key, 'after choice', hex(active()))
                s.run_for(1)
                frame(f'{name}-{key}-4-after-choice')

                # Opened from the custom menu the page answers to the custom menu again.
                s.input(int(sound['functions']['open'], 16) & ~1, [])
                s.run_for(1)
                root = state+sound['page_offsets']['955']
                assert active() == root
                frame(f'{name}-{key}-5-custom-root')
                category = next(c for c in sound['menu_categories'] if c['key'] == key)
                s.input(0x1045d54c, [root, category['control_id']])
                s.run_for(.5)
                for _ in range(3):
                    touch(250, 86+category['row']*52)
                    s.run_for(.5)
                    if active() != root:
                        break
                assert active() == page, (key, 'custom path', hex(active()))
                assert word(page+4) == root, (key, hex(word(page+4)))
                for _ in range(3):
                    touch(675, 28)
                    if active() != page:
                        break
                assert active() == root, (key, 'custom back', hex(active()))
                # Leave through the root's back arrow to the settings tab, then return.
                for _ in range(3):
                    touch(675, 28)
                    if active() != root:
                        break
                s.run_for(1)
                open_tab(menu_object)
                observations.append(dict(menu=name, key=key, row=row, label=label))
                s.audit.append(dict(native_row=observations[-1]))
                s.write_audit()
        s.audit.append(dict(native_rows=observations, language=a.language,
                            diagnostic_sd_services=True, physical_verified=False))
        s.write_audit()
        print('PASS native rows:', a.language, observations, flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
