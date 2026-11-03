"""QEMU-only: Setup -> Firmware version must open and reach its update action.

Every build handed over for camera tests must keep the stock update path, the
only way back from a bad build. Without --image the stock firmware runs; with
--image/--report the built EG edits and owned tail are replayed. The setup
list is walked upward from its first row (wrapping to the end) with stock key
events until Firmware version (stock row 37, the last stock row; owned rows
may follow it) has focus; touching it opens the page, which must be the stock
run's page (--expect-page 0xC1839680) and is saved as a frame. The stock page
offers its update action only with a package on a mounted card, which this
QEMU machine does not mount (the card insert event arrives but no sector is
read), so --update-y and the update itself stay a camera check.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

SETUP = 0xc1837160
FIRMWARE_ROW = 37
EVENT_UP = 0x253


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--image', type=Path)
    p.add_argument('--report', type=Path)
    p.add_argument('--language', choices=('en', 'zh-Hans', 'zh-Hant'), default='zh-Hans')
    p.add_argument('--update-y', type=int, help='screen row of the update action on the firmware page')
    p.add_argument('--expect-page', type=lambda x: int(x, 0), help='firmware page address of the stock run')
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session

    s = Session(str(a.output_dir.resolve()))
    def write(address, data):
        for off in range(0, len(data), 1024):
            v = data[off:off + 1024]
            assert s.remote.command(f'M{address + off:x},{len(v):x}:' + v.hex()) == 'OK'
    def half(address): return int.from_bytes(s.remote.memory(address, 2), 'little')
    def active(): return int(s.input(0x1046109a, [])['registers']['r0'], 16)
    def key(event):
        s.input(0x103ce96e, [event], seconds=15); s.run_for(.7)
    def touch(x, y):
        for contact in (1, 0):
            packet = bytearray(60); struct.pack_into('<I', packet, 0, 1)
            packet[8], packet[9] = contact, 1; struct.pack_into('<2H', packet, 0x12, x, y)
            s.input(0x103ce7e6, [('scratch', 0)], payload=packet); s.run_for(.5)
    try:
        if a.image:
            report = json.loads(a.report.read_text(encoding='utf-8'))
            image = a.image.read_bytes()
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
        if a.image:
            for address, new in scatter:
                write(address, new)
        lang, font = {'en': (3, 1), 'zh-Hans': (26, 8), 'zh-Hant': (27, 9)}[a.language]
        s.input(0x10465990, [0x1400, lang]); s.input(0x10452cc0, [font])
        s.input(0x1045f732, [2, 397]); s.run_for(1)
        assert active() == SETUP, hex(active())
        rows = half(int.from_bytes(s.remote.memory(SETUP + 40, 4), 'little') + 20)
        s.audit.append({'setup_rows': rows})
        s.frame('00-setup')
        for step in range(1, rows + 1):
            key(EVENT_UP)
            focus = half(SETUP + 24) + half(SETUP + 22)
            s.audit.append({'up': step, 'focused_row': focus})
            s.frame(f'01-up-{step}-row-{focus}')
            if focus == FIRMWARE_ROW:
                break
        assert focus == FIRMWARE_ROW, focus
        touch(250, 86 + half(SETUP + 22) * 52); s.run_for(1)
        page = active()
        assert page != SETUP, 'Firmware version did not open'
        if a.expect_page is not None:
            assert page == a.expect_page, (hex(page), hex(a.expect_page))
        s.frame('02-firmware-version')
        after = None
        if a.update_y is not None:
            touch(250, a.update_y); s.run_for(2)
            after = active()
            s.frame('03-update')
        s.audit.append({'firmware_page': hex(page), 'after_update_action': after and hex(after)})
        # The UI must still answer stock calls after starting the update action.
        s.input(0x10465984, [0x1400])
        s.write_audit()
        print(f'firmware page {page:#x} after {step} up; update page {after and hex(after)}; check the frames', flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
