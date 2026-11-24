"""Open the CMOS dump page in assisted QEMU and press its single action; no camera.

Run inside WSL. Edits and the owned tail are replayed onto the stock boot.
QEMU mounts no card and the stock file services then never answer, so the
open/write/close services are replaced by explicit diagnostic overlays: write
records its call count, total length, word sum and the first 0x190 bytes. The
patched export, stock path setter, allocator and memcpy run in the guest. The
emulator's drive data is whatever QEMU holds there, not camera evidence.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

EXTENSIONS_PAGE = 955
KEY_OK = 0x27E
MODEL = 0x12100000
RECORD = 0xC6001000  # page the sound-settings fixture already writes
HEADER = 0x190


def overlays():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from z30_extensions.assembly import constant as c, invoke as n, assemble, jump
    from z30_extensions.features.sound_layout import SUCCESS
    sources = {
        0x103382A2: f'movs r3,#7;str r3,[r0,#8];{c("r0", SUCCESS)}bx lr;',
        0x1033832E: f'mvn r1,#0;str r1,[r0,#8];{c("r0", SUCCESS)}bx lr;',
        0x102C8A6A: f'''push {{r4-r7,lr}};{c('r4', RECORD)}mov r5,r1;mov r6,r2;str r6,[r3];
            ldr r0,[r4,#4];cmp r0,#0;bne counted;
            add r0,r4,#16;mov r1,r5;movw r2,#{HEADER};cmp r6,r2;it lo;movlo r2,r6;{n(0x10199BDC, thumb=False)}
            counted:ldr r0,[r4];adds r0,#1;str r0,[r4];ldr r0,[r4,#4];add r0,r6;str r0,[r4,#4];
            ldr r7,[r4,#8];mov r0,r5;add r1,r5,r6;
            sum:cmp r0,r1;bhs summed;ldr r2,[r0],#4;add r7,r2;b sum;
            summed:str r7,[r4,#8];movs r0,#0;pop {{r4-r7,pc}};''',
    }
    out = []
    for index, (site, source) in enumerate(sources.items()):
        address = MODEL + index * 256
        out.append((site, address, assemble(source, address, 256), jump(site, address, 12)))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('qemu-root', 'image', 'report', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.qemu_root.resolve() / 'tools'))
    from display_session import Session, png_from_ppm
    image = args.image.read_bytes()
    report = json.loads(args.report.read_text(encoding='utf-8'))
    window = 'cmos_window' in report['features']
    spi = 'cmos_spi' in report['features']
    trace = spi or 'cmos_trace' in report['features']
    feature = report['features']['cmos_window' if window else 'cmos_spi' if spi else 'cmos_trace' if trace else 'cmos_dump']
    sound = report['features']['shutter_sound']
    session = Session(str(args.output_dir.resolve()), fresh=True)
    result = dict(scope=__doc__.splitlines()[0], candidate_sha256=hashlib.sha256(image).hexdigest(),
                  camera_verified=False, sd_fixture=False)

    def write(address, data):
        for offset in range(0, len(data), 1024):
            part = data[offset:offset + 1024]
            assert session.remote.command(f'M{address + offset:x},{len(part):x}:' + part.hex()) == 'OK'
        assert session.remote.memory(address, len(data)) == data

    def word(address):
        return int.from_bytes(session.remote.memory(address, 4), 'little')

    def current():
        return int(session.input(0x1046109A, [])['registers']['r0'], 16)

    def frame(name):
        session.run_for(1)
        raw = (session.graphics / 'gui-latest.ppm').read_bytes()
        (session.out / (name + '.png')).write_bytes(png_from_ppm(raw))

    try:
        scatter = []
        for edit in report['edits']:
            address = 0x10020000 + int(edit['offset'], 16)
            old, new = bytes.fromhex(edit['expected_hex']), bytes.fromhex(edit['replacement_hex'])
            assert session.remote.memory(address, len(old)) == old
            write(address, new)
            if 0x11F81C94 <= address and address + len(old) <= 0x12079EEC:
                scatter.append((0xC1800000 + address - 0x11F81C94, new))
        size = (args.qemu_root / '.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000 + size - 2, image[size - 2:])
        session.input(0x102355EE, [0xC182E688, 1])
        session.run_for(2)
        for address, data in scatter:
            write(address, data)
        assert session.remote.memory(RECORD, 16 + HEADER) == bytes(16 + HEADER), 'record page in use'
        for site, address, code, branch in overlays():
            write(address, code)
            write(site, branch)
        result['sd_fixture'] = 'open/write/close overlays; path setter, allocator and memcpy are stock'
        context =int(feature['context'], 16) + int(feature['blob_field'], 16)
        result['emulator_drive_data_pointer'] = hex(word(context))
        result['emulator_area_address_word'] = hex(word(int(feature['area_descriptor'], 16) + 8))
        session.input(0x10452CC0, [1])
        session.input(0x1045F732, [2, 397])
        session.input(int(sound['functions']['open'], 16) & ~1, [])
        session.run_for(1)
        assert int.from_bytes(session.remote.memory(current() + 2, 2), 'little') == EXTENSIONS_PAGE
        frame('01-extensions')
        functions = {k: int(v, 16) & ~1 for k, v in feature['functions'].items()}
        session.input(functions['bitrate_open'], [])
        session.run_for(1)
        page = current()
        assert int.from_bytes(session.remote.memory(page + 2, 2), 'little') == feature['page_index']
        frame('02-cmos-page')
        state = int(session.input(functions['bitrate_get'], [])['registers']['r0'], 16)
        assert word(state) == 0
        controls = report['features']['shutter_sound']['control_identifiers'][str(feature['page_index'])]
        session.input(0x1045D54C, [page, controls[0]])
        session.input(0x103CE96E, [KEY_OK], seconds=60)
        session.run_for(2)
        returned = int.from_bytes(session.remote.memory(current() + 2, 2), 'little')
        status = word(state)
        frame('03-after-save')
        result.update(page_opened=True, returned_page=returned, status=status,
                      status_text=['Not saved', 'Saved WL-CMOS.BIN', 'No CMOS data', 'No memory',
                                   'Open failed', 'Write failed', 'Close failed'][status] if status < 7 else None,
                      file_open_flag=word(state + 8), bounce_buffer=hex(word(state + 4)),
                      images=['01-extensions.png', '02-cmos-page.png', '03-after-save.png'])
        assert returned == EXTENSIONS_PAGE, returned
        assert status == 1 and word(state + 8) == 0, status
        calls, total, written_sum = (word(RECORD + i) for i in (0, 4, 8))
        header = session.remote.memory(RECORD + 16, HEADER)
        magic, version, pointer, length, blob_sum = struct.unpack_from('<5I', header)
        blob = b''.join(session.remote.memory(pointer + offset, min(0x10000, length - offset)) for offset in range(0, length, 0x10000))
        words = lambda data: sum(struct.unpack(f'<{len(data) // 4}I', data)) & 0xFFFFFFFF
        assert magic == int(feature['magic'], 16) and length == int(feature['blob_bytes'], 16)
        assert pointer == int(result['emulator_drive_data_pointer'], 16)
        assert blob_sum == words(blob), 'header sum differs from guest drive data'
        assert calls == 1 + length // 0x8000 and total == HEADER + length
        assert written_sum == (words(header) + words(blob)) & 0xFFFFFFFF, 'written bytes differ from guest RAM'
        assert header[0x40:0x140] == session.remote.memory(int(feature['context'], 16), 256)
        result.update(write_calls=calls, file_bytes=total, header_sum_matches_guest=True,
                      written_words_match_guest=True,
                      emulator_blob=dict(sha256=hashlib.sha256(blob).hexdigest(),
                                         zero_bytes=blob.count(0), ff_bytes=blob.count(255)))
        if window:
            def press(row, name):
                session.input(functions['bitrate_open'], [])
                session.run_for(1)
                page = current()
                assert int.from_bytes(session.remote.memory(page + 2, 2), 'little') == feature['page_index']
                session.input(0x1045D54C, [page, controls[row]])
                session.input(0x103CE96E, [KEY_OK], seconds=60)
                session.run_for(2)
                frame(name)
                return int.from_bytes(session.remote.memory(current() + 2, 2), 'little'), word(state)
            codes, w = feature['status_codes'], feature['window']
            returned, status = press(1, '04-after-arm')
            assert returned == EXTENSIONS_PAGE and codes[status] == 'SPI log armed', (returned, status)
            labels = []
            for step in range(3):
                returned, status = press(2, f'05-window-{step}')
                preset = word(state + w['preset_offset'])
                assert returned == EXTENSIONS_PAGE and preset == (step + 1) % 3, (returned, preset)
                labels.append(codes[status])
            assert labels == ['Window: +64 lines', 'Window: 3:2 test', 'Window: stock'], labels
            result.update(window_cycle=labels)
            trace = spi = False
        if trace:
            observer = feature['observer']
            index = word(state + observer['log_index_offset'])
            result['observer_records_before_log_save'] = index
            write(RECORD, bytes(16 + HEADER))
            session.input(functions['bitrate_open'], [])
            session.run_for(1)
            page = current()
            assert int.from_bytes(session.remote.memory(page + 2, 2), 'little') == feature['page_index']
            session.input(0x1045D54C, [page, controls[1]])
            session.input(0x103CE96E, [KEY_OK], seconds=60)
            session.run_for(2)
            returned = int.from_bytes(session.remote.memory(current() + 2, 2), 'little')
            status = word(state)
            frame('04-after-log-save')
            log_header = session.remote.memory(RECORD + 16, 32)
            ring = session.remote.memory(state + observer['log_base_offset'],
                                         observer['records'] * observer['record_bytes'])
            magic, version, sequence, records, size, request, parameters = struct.unpack_from('<7I', log_header)
            assert returned == EXTENSIONS_PAGE and status == 7, (returned, status)
            assert word(RECORD) == 1 and word(RECORD + 4) == 32 + len(ring)
            assert (magic, records, size) == (int(feature['log_magic'], 16), observer['records'], observer['record_bytes'])
            assert word(RECORD + 8) == (words(log_header) + words(ring)) & 0xFFFFFFFF
            result.update(log_saved=True, log_sequence=sequence, log_file_bytes=word(RECORD + 4),
                          log_records=[ring[i:i + 64].hex() for i in range(0, len(ring), 64)
                                       if any(ring[i:i + 4])][:8],
                          images=result['images'] + ['04-after-log-save.png'])
        if 'ptp_read' in report['features']:
            ptp = report['features']['ptp_read']
            found = int(session.input(int(ptp['lookup'], 16), [int(ptp['opcode'], 16)])['registers']['r0'], 16)
            stock_found = int(session.input(int(ptp['lookup'], 16), [0x1001])['registers']['r0'], 16)
            assert found == int(ptp['record'], 16) and stock_found == 0x115CD870, (hex(found), hex(stock_found))
            result.update(ptp_lookup_record=hex(found), ptp_lookup_getdeviceinfo=hex(stock_found))
        if spi:
            observer = feature['spi_observer']
            def press(row, name):
                session.input(functions['bitrate_open'], [])
                session.run_for(1)
                page = current()
                assert int.from_bytes(session.remote.memory(page + 2, 2), 'little') == feature['page_index']
                session.input(0x1045D54C, [page, controls[row]])
                session.input(0x103CE96E, [KEY_OK], seconds=60)
                session.run_for(2)
                frame(name)
                return int.from_bytes(session.remote.memory(current() + 2, 2), 'little'), word(state)
            codes = feature['status_codes']
            returned, status = press(2, '05-after-arm')
            assert returned == EXTENSIONS_PAGE and codes[status] == 'SPI log armed', (returned, status)
            buffer = word(state + observer['buffer_offset'])
            assert buffer and word(state + observer['armed_offset']) == 1 and word(state + observer['segment_offset']) == 1
            entries = word(state + observer['index_offset'])
            write(RECORD, bytes(16 + HEADER))
            returned, status = press(3, '06-after-spi-save')
            assert returned == EXTENSIONS_PAGE and codes[status] == 'Saved WL-SPI.BIN', (returned, status)
            spi_header = session.remote.memory(RECORD + 16, 32)
            log_bytes = observer['capacity'] * observer['entry_bytes']
            log = b''.join(session.remote.memory(buffer + o, 0x10000) for o in range(0, log_bytes, 0x10000))
            assert word(RECORD) == 1 + log_bytes // 0x8000 and word(RECORD + 4) == 32 + log_bytes
            assert word(RECORD + 8) == (words(spi_header) + words(log)) & 0xFFFFFFFF
            assert struct.unpack_from('<I', spi_header)[0] == int(feature['spi_magic'], 16)
            result.update(spi_armed=True, spi_saved=True, spi_buffer=hex(buffer), spi_entries_in_emulator=entries,
                          spi_file_bytes=word(RECORD + 4),
                          images=result['images'] + ['05-after-arm.png', '06-after-spi-save.png'])
    finally:
        (session.out / 'checks.json').write_text(json.dumps(result, indent=2) + '\n')
        session.close()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
