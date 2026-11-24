"""Decode a fixed-size WL-PRE.DBG without reading any recorded addresses."""
import argparse
import json
import struct
from pathlib import Path

MAGIC = 0x31504A57
SIZE = 64 + 64*24 + 6*16
STATUS_NAMES = ('Off', 'Waiting for JPG', 'Copy ready', 'Saved to SD', 'No memory',
                'Too large', 'Unsupported segments', 'Invalid JPG', 'Export failed', 'Busy', 'Log saved',
                'Enable silent photo', 'Worker unavailable', 'Capture refused', 'Capture pending')


def decode(data):
    if len(data) not in (SIZE, SIZE+116, SIZE+188):
        raise ValueError(f'Expected {SIZE}, {SIZE+116} or {SIZE+188} bytes, got {len(data)}')
    header = struct.unpack_from('<16I', data)
    if header[1] != MAGIC:
        raise ValueError('Not a WJP1 capture probe record')
    fields = ('selection', 'magic', 'enabled', 'armed', 'copy_lock', 'buffer_address',
              'copied_bytes', 'stock_mode', 'source_object', 'status', 'reserved',
              'copies', 'sequence', 'declared_bytes', 'open_file', 'log_lock')
    report = dict(header=dict(zip(fields, header)), events=[], segments=[])
    if len(data) >= SIZE+116:
        tail = data[SIZE:]
        words = struct.unpack('<29I', tail[:116])
        report['native_worker_snapshot'] = dict(
            task_id=words[0], task_entry=hex(words[1]), priority=words[2],
            stack_bytes=words[3], core_field=words[4] & 255,
            event_id=words[8], handler=hex(words[11]), event_mask=words[12],
            config_hex=tail[52:80].hex(), request_hex=tail[80:100].hex(),
            phase=words[25], ready=words[26], ticket=words[27], result=words[28])
        if len(tail) == 188:
            info = tail[116:180]
            report['native_worker_snapshot'].update(
                task_query_result=struct.unpack_from('<I', tail, 180)[0],
                entry_hit=struct.unpack_from('<I', tail, 184)[0],
                task_info_hex=info.hex(),
                task_info_words=list(struct.unpack('<16I', info)))
            if report['native_worker_snapshot']['task_query_result'] == 0xfffffffe:
                report['native_worker_snapshot']['task_query_note'] = 'Disabled after v7 camera export hang; no kernel query performed'
    for index in range(64):
        values = struct.unpack_from('<6I', data, 64+index*24)
        if values[0]:
            report['events'].append(dict(zip(('sequence','kind','arg0','arg1','status','copied_bytes'), values)))
    report['events'].sort(key=lambda row: row['sequence'])
    for row in report['events']:
        if row['kind'] == 5:
            row['worker_event'] = {1:'Task ready', 2:'Half-press wake result',
                3:'Native capture request', 4:'Native capture return',
                6:'Task creation failed', 7:'Silent photography required',
                8:'Input released or full-pressed before capture',
                9:'Capture callback already occupied', 10:'Native capture already active',
                11:'Worker entered; arg1 is event mask',
                12:'Worker phase before delay', 13:'Worker enabled after delay',
                14:'Admission wait timed out; arg1 polls, request still submitted once',
                15:'Admission gates clear; arg1 polls waited'}.get(row['arg0'], 'Unknown')
        if row['kind'] == 8:
            packed = row['arg0']
            row['admission_gates'] = dict(
                bbc4=packed & 255, bbc4_busy=(packed & 255) == 0x12,
                bc2e=packed >> 8 & 255, baec=packed >> 16 & 255, gate_65964=packed >> 24 & 255,
                attr_1b2e=row['arg1'])
        if row['kind'] == 9:
            row['admission_gates'] = dict(word_6316e=row['arg0'], word_63166=row['arg1'])
        if row['kind'] == 10:
            row['admission_context'] = dict(
                attr_11b=row['arg0'], deferred_entry_path=row['arg0'] == 1,
                native_phase_bb8a=row['arg1'])
        if row['kind'] == 7:
            row['task_entry_arguments'] = dict(r0=hex(row['arg0']), r1=hex(row['arg1']))
        if row['kind'] == 6:
            row['native_capture_callback'] = hex(row['arg0'])
        if row['kind'] == 4:
            current, previous = row['arg0'], row['arg1']
            row['physical_release_buttons'] = {
                name: dict(pressed=bool(current & mask),
                           rising=bool(current & ~previous & mask),
                           falling=bool(previous & ~current & mask))
                for name, mask in (('half', 0x80000000), ('full', 0x40000000))}
    for index in range(6):
        values = struct.unpack_from('<4I', data, 64+64*24+index*16)
        report['segments'].append(dict(zip(('kind','address','logical_length','padding'), values)))
    report['camera_capture_interpretation'] = (
        'Kinds 8/9/10 (v10) snapshot stock 0x1020A630 admission getters before the '
        'request: attr 0x1B2E/0x1020BBC4==0x12 predict 0x20, 0x1020BC2E/0x1006316E/'
        '0x10063166/0x10065964 predict 0x1D, 0x1020BAEC predicts 0xD; attr 0x11B==1 '
        'takes a deferred path that ignores the request. '
        'Kind 4 records physical SW0 current/previous button bits. Kind 3 is a '
        'release-controller notification ID, not a physical button event. '
        'Stock mode values do not identify electronic/mechanical operation.')
    status = header[9]
    report['status_name'] = STATUS_NAMES[status] if status < len(STATUS_NAMES) else 'Unknown'
    report['export_note'] = 'copy_lock/log_lock/open_file may be set because this snapshot is written during export.'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.source.resolve():
        parser.error('Output must not overwrite the diagnostic input')
    text = json.dumps(decode(args.source.read_bytes()), indent=2)+'\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
