"""Decode the bounded WL-ASPECT.DBG diagnostic record, without editing it."""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

MAGIC, CAPACITY, STRIDE, HEADER = 0x31444157, 64, 160, 24


def border_payload(payload):
    magic,version=struct.unpack_from('<II',payload,64)
    if magic!=0x31444257 or version not in (1,2):return None
    from z30_extensions.features.aspect.border import DIAGNOSTIC_NAMES
    result=dict(zip(DIAGNOSTIC_NAMES,struct.unpack_from('<16I',payload,72)))
    channel,grid=struct.unpack_from('<II',payload,136)
    result.update(rectangle_channel_enabled=channel&255,stock_grid_enabled=grid)
    if version==2:result['rectangle_channel_owned']=bool(channel&256)
    return result


def decode(data):
    if data[:4] == b'WMD1':
        from z30_extensions.features.aspect.mask_trace import decode as decode_mask
        return decode_mask(data)
    if len(data) < HEADER:
        raise ValueError('Incorrect trace length (possibly a short write)')
    magic, version, counter, capacity, stride, freeze = struct.unpack_from('<6I', data)
    if (magic, stride) != (MAGIC, STRIDE) or version not in (1,2,3,4) or capacity != (96 if version == 4 else CAPACITY):
        raise ValueError('Unknown trace format')
    if len(data) != HEADER + capacity * stride:
        raise ValueError('Incorrect trace length (possibly a short write)')
    result = {'version': version, 'counter': counter, 'capacity': capacity, 'records': []}
    for slot in range(capacity):
        raw = data[HEADER+slot*stride:HEADER+(slot+1)*stride]
        seq, event, profile, caller = struct.unpack_from('<4I', raw)
        if seq>counter and version==4 and event==5 and profile<=6 and slot==21+profile:
            border=border_payload(raw[16:])
            if border is not None:
                # Keep strict normal-record filtering. Report a self-identified
                # diagnostic tail separately, with the snapshot inconsistency.
                result.setdefault('excluded_menu_samples',[]).append(dict(
                    slot=slot,sequence=seq,header_counter=counter,profile=profile,border=border,
                    reason='Sequence exceeds exported header counter; not a committed normal record'))
        if not seq or seq>counter:
            continue
        pinned = 0 if version == 1 else 7 if version == 2 else 21 if version == 3 else 77
        if slot < pinned:
            if event != 2 + slot // 7 or profile != slot % 7:continue
        else:
            ring=capacity-pinned
            offset=pinned
            if seq<max(1,counter-ring+1) or offset+(seq-1)%ring!=slot:continue
        record = {'sequence': seq, 'event': event, 'profile': profile, 'caller': hex(caller)}
        payload = raw[16:]
        if event == 1 or version == 4 and event in range(6,13):
            area, size, pointer, pair, *registers = struct.unpack_from('<8I', payload)
            record.update(area=area, size=size, pointer=hex(pointer),
                          wh=[pair & 65535, pair >> 16], saved_r4_r7=[hex(v) for v in registers])
            if event != 1:
                record['caller_input_wh'] = list(struct.unpack_from('<2H', payload, 32))
                record['sample'] = 'first' if event in (11,12) else 'latest_change'
        elif event == 5 and version == 4:
            record['user_area'], record['derived_area'] = struct.unpack_from('<2I', payload)
            border=border_payload(payload)
            if border is not None:record['border']=border
        elif event == 2:
            fields = struct.unpack_from('<8I', payload)
            names = (('kind', 'flag98', 'flag104', 'area208_word',
                      'mode2658', 'word228', 'flag70', 'flag2941') if version >= 3 else
                     ('kind', 'flag98', 'flag104', 'area208_word',
                      'word224', 'word228', 'word22c', 'word4d4'))
            record['capture'] = dict(zip(names, fields))
            config = payload[32:144]
            for name, offset in (('parent_wh', 0x22), ('origin_xy', 0x26),
                                 ('trim_xy', 0x38), ('trim_wh', 0x3c), ('output_wh', 0x56)):
                record[name] = list(struct.unpack_from('<2H', config, offset))
            record['config_hex'] = config.hex()
        elif event == 3 and version >= 3:
            record['request_pointers'] = [hex(v) for v in struct.unpack_from('<4I', payload)]
            record['margins_lrtb'] = list(struct.unpack_from('<4I', payload, 0x10))
            record['source_wh'] = list(struct.unpack_from('<2H', payload, 0x20))
            record['output_wh'] = list(struct.unpack_from('<2H', payload, 0x24))
            record['request_hex'] = payload[:72].hex()
        elif event == 4 and version >= 3:
            record['processing_mode'] = payload[0]
            record['processing_variant'] = payload[1]
            record['source_wh'] = list(struct.unpack_from('<2I', payload, 0x14))
            record['output_wh'] = list(struct.unpack_from('<2I', payload, 0x1c))
            record['margins_lrtb'] = list(struct.unpack_from('<4I', payload, 0x2c))
            record['parameters_hex'] = payload.hex()
        else:
            record['unknown_event'] = True
        if version == 4 and event in range(5,13):
            record['view_cache'] = {
                'full_wh': list(struct.unpack_from('<2H', payload, 36)),
                'area_rect': list(struct.unpack_from('<4H', payload, 40)),
                'buffer_wh': list(struct.unpack_from('<2H', payload, 48)),
                'buffer_rect': list(struct.unpack_from('<4H', payload, 52)),
                'flags_5b_5c': list(payload[60:62]),
            }
        result['records'].append(record)
    result['records'].sort(key=lambda item: item['sequence'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    text = json.dumps(decode(args.trace.read_bytes()), indent=2)
    if args.output:
        args.output.write_text(text+'\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
