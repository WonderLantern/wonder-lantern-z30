"""Capture native menu frames with the existing Z30 QEMU display machine.

Run in WSL after building an EG and JSON report. Stock guard stays enabled;
checked ROM edits are injected while paused before reset. No physical I/O.
"""
import argparse
import json
import struct
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--stock-page', type=int)
    p.add_argument('--verify-fixed', action='store_true')
    p.add_argument('--verify-error-dialog', action='store_true')
    p.add_argument('--back-cycles', type=int, default=0)
    p.add_argument('--touch-back', action='store_true')
    p.add_argument('--direct-setup', action='store_true', help='Diagnostic setup entry without selecting its tab')
    p.add_argument('--verify-return', action='store_true', help='Require visible sidebar and an erased back-button region')
    p.add_argument('--verify-sidebar', action='store_true', help='Require native sidebar while custom pages are open')
    p.add_argument('--verify-tab-switch', action='store_true', help='Touch the photo tab from the custom root')
    p.add_argument('--language', choices=['auto', 'en', 'zh-Hans', 'zh-Hant'], default='auto')
    p.add_argument('--verify-entry-state', action='store_true', help='Check guest setup row encoding and text buffer')
    p.add_argument('--verify-cold-entry', action='store_true', help='Construct an entry with a prefetched stale descriptor')
    p.add_argument('--entry-only', action='store_true', help='Stop after entry state/constructor diagnostics')
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from display_session import png_from_ppm
    report = json.loads(a.report.read_text())
    image = a.image.read_bytes()
    base = 0x10020000
    s = Session(str(a.output_dir.resolve()))
    captured = {}
    def lit(raw, x0, y0, x1, y1):
        pixels = raw.split(b'\n', 3)[3]
        return sum(pixels[(y*720+x)*3:(y*720+x)*3+3] != bytes(3)
                   for y in range(y0, y1) for x in range(x0, x1))
    def sidebar(name):
        count = lit(captured[name], 14, 16, 50, 470)
        s.audit.append({'custom_sidebar_frame': name, 'nonblack_pixels': count})
        s.write_audit()
        assert count > 500, 'Custom page hid native sidebar'
    def white_text(raw, x0, y0, x1, y1):
        pixels = raw.split(b'\n', 3)[3]
        return sum(all(v >= 190 for v in pixels[(y*720+x)*3:(y*720+x)*3+3])
                   for y in range(y0,y1) for x in range(x0,x1))
    def frame(name):
        # gui-latest is also exported during GPU work. Sample native buffers
        # and retain the most populated frame, then the most complete text
        # and latest sample on ties; never draw host-side pixels.
        samples = []
        for _ in range(8):
            s.run_for(.25)
            raw = (s.graphics / 'gui-latest.ppm').read_bytes()
            pixels = raw.split(b'\n', 3)[3]
            score = sum(pixels[i:i+3] != b'\0\0\0' for i in range(0, len(pixels), 3))
            white = sum(all(v >= 190 for v in pixels[i:i+3]) for i in range(0,len(pixels),3))
            samples.append((score, white, len(samples), raw))
        score, white, _, raw = max(samples, key=lambda x: x[:3])
        target = s.out / (name + '.png')
        target.write_bytes(png_from_ppm(raw))
        captured[name] = raw
        s.audit.append({'frame': name, 'native_buffer_samples': [x[0] for x in samples],
                        'native_white_samples': [x[1] for x in samples],
                        'chosen_nonblack_pixels': score,'chosen_white_pixels':white})
        s.write_audit()
        return target
    def write(address, data):
        for off in range(0, len(data), 1024):
            part = data[off:off+1024]
            assert s.remote.command(f'M{address+off:x},{len(part):x}:' + part.hex()) == 'OK'
        assert s.remote.memory(address, len(data)) == data
    try:
        scatter_edits = []
        for e in ([] if a.stock_page is not None else report['edits']):
            address = base + int(e['offset'], 16)
            old = bytes.fromhex(e['expected_hex'])
            assert s.remote.memory(address, len(old)) == old
            new = bytes.fromhex(e['replacement_hex'])
            write(address, new)
            # Assisted QEMU preloads initialized RAM before guest execution.
            # Mirror guarded data edits that natural scatter loading copies.
            if 0x11f81c94 <= address and address + len(old) <= 0x11f81c94 + 0xf8258:
                ram = 0xc1800000 + address - 0x11f81c94
                scatter_edits.append((ram, old, new, address))
        stock_size = (a.qemu_root / '.local/firmware/eg2090_0120a0.bi').stat().st_size
        if a.stock_page is None:
            write(base + stock_size - 2, image[stock_size-2:])
        s.input(0x102355ee, [0xc182e688, 1])
        s.run_for(2)
        for ram, old, new, address in scatter_edits:
            current = s.remote.memory(ram, len(old))
            assert current in (old, new), (hex(ram), current.hex(), old.hex())
            if current == old:
                write(ram, new)
                s.audit.append({'assisted_scatter_edit': hex(ram), 'source': hex(address)})
        if a.language != 'auto':
            code, font = {'en': (3, 1), 'zh-Hans': (26, 8), 'zh-Hant': (27, 9)}[a.language]
            s.input(0x10465990, [0x1400, code])
            s.input(0x10452cc0, [font])
            result = s.input(0x10465984, [0x1400])
            assert int(result['registers']['r0'], 16) == code
            s.audit.append({'diagnostic_language': a.language, 'native_config': code, 'native_font': font})
        s.frame('01-stock-menu')
        sound = report['features']['shutter_sound']
        if a.stock_page is None:
            if a.back_cycles and not a.direct_setup:
                for contact in (1, 0):
                    packet = bytearray(60)
                    struct.pack_into('<I', packet, 0, 1)
                    packet[8], packet[9] = contact, 1
                    struct.pack_into('<2H', packet, 0x12, 32, 260)
                    s.input(0x103ce7e6, [('scratch', 0)], payload=packet)
                    s.run_for(.5)
            else:
                s.input(0x1045f732, [2, 397])
            s.run_for(1)
            s.frame('02-setup')
            if a.verify_entry_state:
                metadata = int.from_bytes(s.remote.memory(0xc1837160+40,4),'little')
                controls = int.from_bytes(s.remote.memory(metadata+12,4),'little')
                flags = int.from_bytes(s.remote.memory(controls+38*28+16,4),'little')
                chinese = a.language.startswith('zh-')
                assert flags == (0x3001 if chinese else 0x1201), hex(flags)
                length, pointer, style = struct.unpack('<3I',s.remote.memory(0xc2013368+sound['entry_slot']*12,12))
                raw = s.remote.memory(pointer,length*(2 if chinese else 1))
                label = raw.decode('utf-16-le' if chinese else 'ascii')
                expected = ('自定义菜单' if a.language == 'zh-Hans' else '自訂選單') if chinese else 'Extensions'
                assert label == expected, (label,expected)
                assert style == (120 if chinese else 0), style
                s.audit.append({'entry_flags': hex(flags), 'entry_label': label, 'entry_style': style,
                                'entry_state_verified': True})
            if a.verify_cold_entry:
                chinese = a.language.startswith('zh-')
                old_table = sound['setup_entry_controls']['ascii' if chinese else 'utf16']
                descriptor = int(old_table,16)+38*28
                result = s.input(int(sound['functions']['row'],16)&~1,
                                 [0xc1837160,descriptor,('scratch',0),38],payload=bytes(512))
                assert result['completed']
                output = bytes.fromhex(result['scratch']['result'])
                slot, kind = struct.unpack_from('<2I',output,8)
                s.audit.append({'prefetched_entry_descriptor': hex(descriptor),
                                'constructed_entry_slot': slot, 'constructed_entry_kind': kind})
                s.write_audit()
                assert (slot,kind) == (sound['entry_slot'],3 if chinese else 2), (slot,kind)
            if a.entry_only:
                return
            s.input(int(sound['functions']['ensure'], 16) & ~1, [])
        if a.back_cycles:
            state = int.from_bytes(s.remote.memory(int(sound['state_anchor'], 16), 4), 'little')
            root = state + sound['page_offsets']['955']
            def back():
                if a.touch_back:
                    for contact in (1, 0):
                        packet = bytearray(60)
                        struct.pack_into('<I', packet, 0, 1)
                        packet[8], packet[9] = contact, 1
                        struct.pack_into('<2H', packet, 0x12, 675, 28)
                        s.input(0x103ce7e6, [('scratch', 0)], payload=packet, seconds=15)
                        s.run_for(.5)
                else:
                    s.input(0x103ce96e, [0x255], seconds=15)
            for cycle in range(a.back_cycles):
                # Execute the actual setup entry callback. A synthetic key
                # notification can be consumed/coalesced before menu dispatch.
                s.input(int(sound['functions']['open'], 16) & ~1, [0xc1837160], seconds=15)
                current = s.input(0x1046109a, [])
                assert int(current['registers']['r0'], 16) == root, 'Custom root did not open'
                frame(f'back-{cycle}-custom')
                if a.verify_error_dialog:
                    popup = state + sound['page_offsets']['959']
                    for error in (1, 3, 8):
                        write(state + 52, struct.pack('<I', error))
                        s.input(int(sound['functions']['show_error'],16) & ~1, [root])
                        frame(f'error-dialog-{cycle}-{error}')
                        assert lit(captured[f'error-dialog-{cycle}-{error}'],250,280,500,332) > 2000, 'Dialog OK button was not drawn'
                        active = s.input(0x1046109a, [])
                        assert int(active['registers']['r0'],16) == popup
                        group = int.from_bytes(s.remote.memory(popup+12,4),'little')
                        body = s.input(0x10454210, [0xa19,group])
                        widget = int(body['registers']['r0'],16)
                        assert widget and int.from_bytes(s.remote.memory(widget+8,4),'little') == 65
                        # Use native touch dispatch for OK and, on the second
                        # cycle, the popup back icon. Require parent rebuilding.
                        for contact in (1, 0):
                            packet = bytearray(60)
                            struct.pack_into('<I', packet, 0, 1)
                            packet[8], packet[9] = contact, 1
                            x, y = (675,28) if error == 3 and cycle else (350,310)
                            struct.pack_into('<2H', packet, 0x12, x, y)
                            s.input(0x103ce7e6, [('scratch',0)],payload=packet)
                            s.run_for(.5)
                        frame(f'error-dialog-{cycle}-{error}-closed')
                        if 'module_host' in report['features']:
                            module_row=next((c['row'] for c in sound.get('menu_categories',[]) if c['key']=='modules'),4)
                            box=sound['pages']['955']['label_rectangles'][module_row]
                            count = white_text(captured[f'error-dialog-{cycle}-{error}-closed'],*box)
                            s.audit.append({'dialog_closed_module_label_pixels':count,'error':error,'cycle':cycle})
                            length, pointer, style = struct.unpack('<3I',s.remote.memory(0xc2013368+sound['root_label_slots']['modules']*12,12))
                            label = s.remote.memory(pointer,length*(2 if a.language.startswith('zh-') else 1)) if length else b''
                            registry = int.from_bytes(s.remote.memory(root+36,4),'little')
                            row = s.input(0x1045745e,[registry,1,module_row])
                            row_widget = int(row['registers']['r0'],16)
                            s.audit.append({'module_root_slot_length':length,'module_root_slot_raw':label.hex(),
                                            'module_root_style':style,'module_root_widget':hex(row_widget),
                                            'module_root_widget_state':s.remote.memory(row_widget,64).hex() if row_widget else None})
                            s.write_audit()
                            assert count > 100, 'Closing the dialog lost the SD Modules label'
                        active = s.input(0x1046109a, [])
                        assert int(active['registers']['r0'],16) == root
                        assert int.from_bytes(s.remote.memory(state+52,4),'little') == 0
                    s.audit.append({'error_dialog_close_cycle':cycle,'errors':[1,3,8]})
                    s.write_audit()
                if a.verify_sidebar:
                    sidebar(f'back-{cycle}-custom')
                back()
                frame(f'back-{cycle}-stock')
                if a.verify_return:
                    raw = captured[f'back-{cycle}-stock']
                    sidebar_pixels = lit(raw, 14, 16, 50, 470)
                    corner = lit(raw, 636, 4, 716, 52)
                    s.audit.append({'back_cycle_pixels': cycle, 'sidebar_nonblack': sidebar_pixels,
                                    'back_corner_nonblack': corner})
                    s.write_audit()
                    assert sidebar_pixels > 500, 'Native sidebar was not restored'
                    assert corner == 0, 'Released back button left pixels behind'
                current = s.input(0x1046109a, [])
                registry = int.from_bytes(s.remote.memory(root + 36, 4), 'little')
                s.audit.append({'back_cycle': cycle, 'current': current['registers']['r0'], 'custom_registry': hex(registry)})
                s.write_audit()
                active = int(current['registers']['r0'], 16)
                if active == 0xc1832d50:
                    assert int.from_bytes(s.remote.memory(active + 8, 4), 'little') == 0xc1837160
                else:
                    assert active == 0xc1837160
                assert registry == 0
                for event in (0x253, 0x254):
                    s.input(0x103ce96e, [event], seconds=15)
                    frame(f'back-{cycle}-stock-move-{event:x}')
            if a.verify_tab_switch:
                s.input(int(sound['functions']['open'], 16) & ~1, [0xc1837160], seconds=15)
                frame('sidebar-before-photo-touch')
                active = s.input(0x1046109a, [])
                assert int(active['registers']['r0'], 16) == root
                for contact in (1, 0):
                    packet = bytearray(60)
                    struct.pack_into('<I', packet, 0, 1)
                    packet[8], packet[9] = contact, 1
                    struct.pack_into('<2H', packet, 0x12, 32, 90)
                    s.input(0x103ce7e6, [('scratch', 0)], payload=packet)
                    s.run_for(.5)
                active = s.input(0x1046109a, [])
                pointer = int(active['registers']['r0'], 16)
                if pointer == 0xc1832d50:
                    pointer = int.from_bytes(s.remote.memory(pointer + 8, 4), 'little')
                page_id = int.from_bytes(s.remote.memory(pointer + 2, 2), 'little')
                s.audit.append({'sidebar_touch_tab_id': page_id})
                s.write_audit()
                assert page_id == 0x27, 'Sidebar touch did not switch to native photo menu'
                s.run_for(2)
                frame('sidebar-photo-tab')
            return
        for index in ([str(a.stock_page)] if a.stock_page is not None else sound['pages']):
            s.input(0x1045f732, [2, int(index)], seconds=15)
            s.run_for(2)
            print(frame('page-' + index), flush=True)
            if a.verify_sidebar:
                sidebar('page-' + index)
            if a.verify_fixed and a.stock_page is None:
                state = int.from_bytes(s.remote.memory(int(sound['state_anchor'], 16), 4), 'little')
                page = state + sound['page_offsets'][index]
                group = int.from_bytes(s.remote.memory(page + 12, 4), 'little')
                result = s.input(0x10454210, [0xa18, group])
                widget = int(result['registers']['r0'], 16)
                assert widget, 'Missing native title widget'
                slot, kind = struct.unpack('<2I', s.remote.memory(widget + 8, 8))
                assert (slot, kind) == (sound['pages'][index]['title_index'], 3 if a.language.startswith('zh-') else 2), (slot, kind)
                s.audit.append({'page': index, 'title_slot': slot, 'title_kind': kind, 'verified': True})
        if a.verify_fixed and a.stock_page is None:
            root = next(iter(sound['pages']))
            state = int.from_bytes(s.remote.memory(int(sound['state_anchor'], 16), 4), 'little')
            page = state + sound['page_offsets'][root]
            for error, name in ((3, 'error-title'), (0, 'restored-title')):
                write(state + 52, struct.pack('<I', error))
                # Enter via the native page lifecycle; do not issue a raw
                # drawing request in InitTask outside that lifecycle.
                s.input(0x1045f732, [2, int(root)])
                s.run_for(2)
                frame(name)
                group = int.from_bytes(s.remote.memory(page + 12, 4), 'little')
                result = s.input(0x10454210, [0xa18, group])
                widget = int(result['registers']['r0'], 16)
                assert struct.unpack('<2I', s.remote.memory(widget + 8, 8)) == (40, 3 if a.language.startswith('zh-') else 2)
            s.audit.append({'reentry_and_error_redraw': True})
        s.audit.append({'scope': 'Assisted boot, native menu construction and guest GPU frames; no camera validation'})
    finally:
        s.close()


if __name__ == '__main__':
    main()
