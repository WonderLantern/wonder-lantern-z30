"""Render the experimental JPG probe menu in assisted QEMU, without capture."""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('qemu-root', 'image', 'report', 'fixture', 'output-dir'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--task-start-only', action='store_true',
                        help='Emulator-only native task startup diagnostic; never post a capture wake')
    parser.add_argument('--query-thunk', type=Path, help='Emulator-only ARM query thunk hex for pre_complete slot')
    parser.add_argument('--task-wake-dry-run', action='store_true', help='Wake worker with phase=0 and ticket=0; capture is refused before any native capture call')
    args = parser.parse_args()
    if args.task_wake_dry_run and not args.task_start_only:
        parser.error('--task-wake-dry-run requires --task-start-only')
    sys.path.insert(0, str(args.qemu_root.resolve()/'tools'))
    from display_session import Session, png_from_ppm
    image = args.image.read_bytes()
    report = json.loads(args.report.read_text())
    fixture = json.loads(args.fixture.read_text())
    feature = report['features']['jpg_capture_probe']
    sound = report['features']['menu_framework']
    session = Session(str(args.output_dir.resolve()), fresh=True)
    result = dict(scope=__doc__, candidate_sha256=hashlib.sha256(image).hexdigest(),
                  camera_verified=False, file_services_modeled=True)

    def write(address, data):
        for offset in range(0, len(data), 1024):
            part = data[offset:offset+1024]
            assert session.remote.command(f'M{address+offset:x},{len(part):x}:'+part.hex()) == 'OK'
        assert session.remote.memory(address, len(data)) == data

    def current():
        return int(session.input(0x1046109A, [])['registers']['r0'], 16)

    def frame(name):
        samples = []
        for index in range(6):
            session.run_for(.25)
            raw = (session.graphics/'gui-latest.ppm').read_bytes()
            pixels = raw.split(b'\n', 3)[3]
            white = sum(all(v >= 190 for v in pixels[j:j+3]) for j in range(0, len(pixels), 3))
            samples.append((white, index, raw))
        raw = max(samples, key=lambda item: item[:2])[2]
        (session.out/(name+'.png')).write_bytes(png_from_ppm(raw))

    try:
        scatter = []
        for edit in report['edits']:
            address = 0x10020000+int(edit['offset'], 16)
            old, new = bytes.fromhex(edit['expected_hex']), bytes.fromhex(edit['replacement_hex'])
            assert session.remote.memory(address, len(old)) == old
            write(address, new)
            if 0x11F81C94 <= address and address+len(old) <= 0x12079EEC:
                scatter.append((0xC1800000+address-0x11F81C94, new))
        size = (args.qemu_root/'.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000+size-2, image[size-2:])
        session.input(0x102355EE, [0xC182E688, 1])
        session.run_for(2)
        for address, data in scatter:
            write(address, data)
        for service in fixture['services']:
            write(service['address'], bytes.fromhex(service['code']))
            write(service['site'], bytes.fromhex(service['jump']))
        write(fixture['config'], bytes(16))
        write(fixture['wav_address'], bytes.fromhex(fixture['wav']))
        session.input(0x10465990, [0x1400, 3])
        session.input(0x10452CC0, [1])
        session.input(0x1045F732, [2, 397])
        session.input(int(sound['functions']['open'], 16)&~1, [])
        session.run_for(1)
        frame('01-extensions')
        functions = {k: int(v, 16)&~1 for k, v in feature['functions'].items()}
        session.input(functions['bitrate_open'], [])
        session.run_for(1)
        page = current()
        assert int.from_bytes(session.remote.memory(page+2, 2), 'little') == feature['page_index']
        frame('02-jpg-probe')
        state = int(session.input(functions['bitrate_get'], [])['registers']['r0'], 16)
        assert int.from_bytes(session.remote.memory(state+8, 4), 'little') == 0
        if args.task_start_only:
            # Exercise actual guest kernel creation/start/scheduling instead of
            # substituting these services in Unicorn. Never arm a capture ticket.
            write(state+8, (1).to_bytes(4, 'little'))
            created = session.input(functions['pre_task'], [state])
            session.run_for(2)
            result['task_start_only'] = dict(
                create_return=created['registers']['r0'],
                task_hex=session.remote.memory(state+1744, 116).hex(),
                header_hex=session.remote.memory(state, 64).hex(),
                records_hex=session.remote.memory(state+112, 1536).hex(),
                no_capture_wake_posted=True)
            records = bytes.fromhex(result['task_start_only']['records_hex'])
            entries = [struct.unpack_from('<6I', records, p) for p in range(0,1536,24)
                       if struct.unpack_from('<I', records, p+4)[0] == 7]
            result['task_start_only']['entry_events'] = [list(row) for row in entries]
            if feature['version'] >= 9:
                assert len(entries) == 1, 'The real guest kernel did not enter the new task'
                assert entries[0][2:4] == (state+1744, functions['pre_entry']|1)
                assert session.remote.memory(state+1928, 4) == (1).to_bytes(4, 'little')
                assert session.remote.memory(state+1852, 4) == bytes(4), 'Capture ticket must stay zero'
            if args.task_wake_dry_run:
                assert feature['version'] >= 9
                assert session.remote.memory(state+1844, 4) == bytes(4), 'Dry run requires idle phase'
                posted = session.input(0x10199CDC, [state+1744, 1])
                assert int(posted['registers']['r0'],16) == 0
                session.run_for(1)
                records = session.remote.memory(state+112, 1536)
                rows = [struct.unpack_from('<6I', records, p) for p in range(0,1536,24)
                        if struct.unpack_from('<I', records, p)[0]]
                assert any(row[1:4] == (5,11,1) for row in rows)
                assert any(row[1:4] == (5,12,0) for row in rows)
                assert not any(row[1:3] == (5,3) for row in rows)
                assert session.remote.memory(state+1824,20) == bytes(20)
                result['task_start_only'].update(dry_run_worker_events=[list(row) for row in rows],
                    diagnostic_event_posted_with_idle_phase=True, native_capture_request_absent=True)
                result['task_start_only']['no_capture_wake_posted'] = False
            # Optional emulator-only query experiments; not part of acceptance.
            if args.query_thunk:
                write(functions['pre_complete'], bytes.fromhex(args.query_thunk.read_text()))
                task_id = int.from_bytes(session.remote.memory(state+1744, 4), 'little')
                result['task_start_only']['raw_query'] = session.input(
                    functions['pre_complete'], [task_id, ('scratch', 0)], payload=bytes(64), seconds=10)
                result['task_start_only']['raw_query_low_id'] = session.input(
                    functions['pre_complete'], [task_id & 65535, ('scratch', 0)], payload=bytes(64), seconds=10)
                result['task_start_only']['raw_query_current'] = session.input(
                    functions['pre_complete'], [0, ('scratch', 0)], payload=bytes(64), seconds=10)
                result['task_start_only']['priority_change'] = session.input(0x10219926, [state+1744, 20])
                session.run_for(2)
                result['task_start_only']['after_priority_records_hex'] = session.remote.memory(state+112, 1536).hex()
            (session.out/'task-start.json').write_text(json.dumps(result['task_start_only'], indent=2)+'\n')
        # Exercise the actual native Off row and require return to Extensions.
        session.input(0x1045D54C, [page, feature['controls'][0]])
        session.input(0x103CE96E, [0x27E])
        session.run_for(1)
        assert int.from_bytes(session.remote.memory(current()+2, 2), 'little') == 955
        frame('03-off-return')
        result.update(menu_page=feature['page_index'], default_off=True, native_off_action_returns=True,
                      images=['01-extensions.png', '02-jpg-probe.png', '03-off-return.png'])
    finally:
        (session.out/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
        session.close()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
