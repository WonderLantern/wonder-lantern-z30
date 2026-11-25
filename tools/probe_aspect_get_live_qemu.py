"""QEMU-only probe: is the extension state block reachable in live view?

Replays the built image into the stock-gated display machine, then calls the
appended aspect_get() in three states (before any menu, while the extension
menu is open, after returning to live view) and reports the resolved state
pointer plus the stock anchor slot and grid record words.  This decides whether
the grid hook can run while the EVF is displayed, which the dynamic soft mask
depends on.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

BYTE_OFFSET_BASE = 0x10020000
GRID_RECORD = 0xC201A7F4
ANCHOR_SLOT = 0xC1832D34


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('qemu-root', 'image', 'report', 'output-dir'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--language', choices=('en', 'zh-Hans', 'zh-Hant'), default='zh-Hans')
    parser.add_argument('--fixture', type=Path, default=None)
    args = parser.parse_args()
    sys.path.insert(0, str(args.qemu_root.resolve() / 'tools'))
    from display_session import Session, png_from_ppm
    from liveview_session import refresh, start

    report = json.loads(args.report.read_text())
    image = args.image.read_bytes()
    stock = (args.qemu_root / '.local/firmware/eg2090_0120a0.bi').read_bytes()
    aspect = report['features']['aspect_profiles']
    sound = report['features']['menu_framework']
    aspect_get = int(aspect['functions']['aspect_get'], 16) & ~1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    session = Session(str((args.output_dir / 'session').resolve()),
                      firmware_image=(args.qemu_root / '.local/firmware/eg2090_0120a0.bi').resolve())
    context: dict[str, object] = dict(build_sha=hashlib.sha256(image).hexdigest(),
                                      aspect_get=hex(aspect_get), observations={})
    s = session

    def write(address, data):
        for off in range(0, len(data), 1024):
            chunk = data[off:off + 1024]
            assert s.remote.command(f'M{address + off:x},{len(chunk):x}:' + chunk.hex()) == 'OK'
        assert s.remote.memory(address, len(data)) == data

    def word(address):
        return int.from_bytes(s.remote.memory(address, 4), 'little')

    def get_state():
        r0 = int(s.input(aspect_get, [])['registers']['r0'], 16)
        anchor = word(ANCHOR_SLOT)
        record = s.remote.memory(GRID_RECORD, 24)
        x0 = struct.unpack_from('<H', record, 0)[0]
        width = struct.unpack_from('<I', record, 4)[0]
        y0 = struct.unpack_from('<H', record, 14)[0]
        height = struct.unpack_from('<I', record, 16)[0]
        return dict(state=hex(r0), anchor=hex(anchor), grid_record=[x0, y0, width, height])

    def touch(x, y):
        for contact in (1, 0):
            packet = bytearray(60)
            struct.pack_into('<I', packet, 0, 1)
            packet[8], packet[9] = contact, 1
            struct.pack_into('<2H', packet, 0x12, x, y)
            s.input(0x103ce7e6, [('scratch', 0)], payload=packet)
            s.run_for(.4)

    def present():
        s.input(0x1032427e, [])
        s.run_for(.4)

    def frame(name):
        s.run_for(.3)
        raw = (s.graphics / 'gui-latest.ppm').read_bytes()
        (s.out / (name + '.png')).write_bytes(png_from_ppm(raw))

    try:
        scatter = []
        for edit in report['edits']:
            address = BYTE_OFFSET_BASE + int(edit['offset'], 16)
            old = bytes.fromhex(edit['expected_hex'])
            new = bytes.fromhex(edit['replacement_hex'])
            assert s.remote.memory(address, len(old)) == old
            write(address, new)
            if 0x11f81c94 <= address and address + len(old) <= 0x12079eec:
                scatter.append((0xc1800000 + address - 0x11f81c94, new))
        size = len(stock)
        write(0x10020000 + size - 2, image[size - 2:])
        s.input(0x102355ee, [0xc182e688, 1])
        s.run_for(2)
        for address, new in scatter:
            write(address, new)
        if args.fixture:
            fixture = json.loads(args.fixture.read_text())
            for item in fixture['services']:
                write(item['address'], bytes.fromhex(item['code']))
                write(item['site'], bytes.fromhex(item['jump']))
            write(fixture['config'], bytes(16))
        lang, font = {'en': (3, 1), 'zh-Hans': (26, 8), 'zh-Hant': (27, 9)}[args.language]
        s.input(0x10465990, [0x1400, lang])
        s.input(0x10452cc0, [font])
        s.input(0x1045f732, [2, 397])
        s.run_for(.5)
        start(session)
        present()
        s.run_for(.5)
        context['observations']['live-before-menu'] = get_state()
        frame('01-live-before-menu')

        s.input(int(sound['functions']['open'], 16) & ~1, [])
        s.run_for(1)
        context['observations']['menu-open'] = get_state()
        frame('02-menu-open')

        for _ in range(4):
            touch(675, 28)
        present()
        s.run_for(.5)
        context['observations']['live-after-menu'] = get_state()
        frame('03-live-after-menu')
        context['verdict'] = dict(
            block_reachable_in_live=bool(int(context['observations']['live-after-menu']['state'], 16)),
            grid_written_in_live=any(context['observations']['live-after-menu']['grid_record']),
        )
    finally:
        s.close()
        (args.output_dir / 'context.json').write_text(json.dumps(context, indent=2),
                                                      encoding='utf-8')
    print(json.dumps(context, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
