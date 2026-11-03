"""QEMU-only: open Setup -> Language and report whether the UI keeps running.

Without --image the stock firmware runs unmodified; with --image/--report the
built EG edits and owned tail are replayed as in verify_aspect_menu_qemu.
Touches the Language row (fourth setup row), then samples frames and checks
that a stock API call still returns. Optionally records the font getter.
"""
import argparse
import json
import struct
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--image', type=Path)
    p.add_argument('--report', type=Path)
    p.add_argument('--language', choices=('en', 'zh-Hans', 'zh-Hant'), default='zh-Hans')
    p.add_argument('--observe-fonts', action='store_true')
    p.add_argument('--scroll', type=int, default=0, help='up-key presses through the language list after it opens')
    p.add_argument('--region', type=int, help='sales region byte (0..12) read by 0x101F4FBC; QEMU defaults to an English-only list')
    p.add_argument('--observe-properties', action='store_true',
                   help='record stock property reads (0x10465984) while the page builds')
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session

    s = Session(str(a.output_dir.resolve()))
    def write(address, data):
        for off in range(0, len(data), 1024):
            v = data[off:off + 1024]
            assert s.remote.command(f'M{address + off:x},{len(v):x}:' + v.hex()) == 'OK'
    def touch(x, y, settle=.5):
        for contact in (1, 0):
            packet = bytearray(60); struct.pack_into('<I', packet, 0, 1)
            packet[8], packet[9] = contact, 1; struct.pack_into('<2H', packet, 0x12, x, y)
            s.input(0x103ce7e6, [('scratch', 0)], payload=packet)
            if settle: s.run_for(settle)
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
        if a.region is not None:
            # 0x101F4FBC: ldr r0,=REGION; ldrb r0,[r0]. The literal lives at 0x101F51E4.
            region = int.from_bytes(s.remote.memory(0x101f51e4, 4), 'little')
            s.audit.append({'region_byte': hex(region), 'was': s.remote.memory(region, 1).hex(), 'set': a.region})
            write(region, bytes([a.region]))
        lang, font = {'en': (3, 1), 'zh-Hans': (26, 8), 'zh-Hant': (27, 9)}[a.language]
        s.input(0x10465990, [0x1400, lang]); s.input(0x10452cc0, [font])
        s.input(0x1045f732, [2, 397]); s.run_for(1)
        s.frame('01-setup')
        try:
            if a.observe_fonts or a.observe_properties:
                # The page draws asynchronously after the touch: run on with
                # the getter observed (never-reached checkpoint).
                touch(250, 86 + 3 * 52, settle=0)
                site = 0x104ed66e if a.observe_fonts else 0x10465984
                try:
                    s.run_to(0x100200ac, seconds=4, observe=[site],
                             capture=lambda remote, regs: {'value': int(regs['r0'], 16), 'lr': regs['r14']})
                except RuntimeError as error:
                    if 'T02' in (s.packet or ''):
                        raise RuntimeError('Guest stopped unexpectedly: ' + s.packet) from error
            else:
                touch(250, 86 + 3 * 52)
            s.run_for(2)
        except RuntimeError as error:
            regs = s.remote.registers()
            sp = int(regs['r13'], 16)
            threads, reply = [], s.remote.command('qfThreadInfo')
            while reply.startswith('m'):
                threads += reply[1:].split(','); reply = s.remote.command('qsThreadInfo')
            cores = {}
            for thread in threads:
                assert s.remote.command('Hg' + thread) == 'OK'
                core = s.remote.registers()
                cores[thread] = dict(core, stack=s.remote.memory(int(core['r13'], 16), 128).hex())
            s.audit.append({'stopped': str(error), 'registers': regs, 'threads': cores,
                            'stack': s.remote.memory(sp, 256).hex()})
            s.write_audit()
            print('STOPPED', error, json.dumps({t: (c['r15'], c['r14'], c['cpsr']) for t, c in cores.items()}), flush=True)
            raise
        s.frame('02-language')
        for step in range(1, a.scroll + 1):
            try:
                s.input(0x103ce96e, [0x253], seconds=15); s.run_for(.7)
            except RuntimeError as error:
                s.audit.append({'scroll_stopped': step, 'error': str(error)}); s.write_audit()
                print(f'STOPPED while scrolling at step {step}: {error}', flush=True)
                raise
            s.frame(f'03-scroll-{step:02d}')
        page = int(s.input(0x1046109a, [])['registers']['r0'], 16)
        metadata = int.from_bytes(s.remote.memory(page + 40, 4), 'little')
        rows = int.from_bytes(s.remote.memory(metadata + 20, 2), 'little')
        controls = int.from_bytes(s.remote.memory(metadata + 12, 4), 'little')
        s.audit.append({'language_page': hex(page), 'page_bytes': s.remote.memory(page, 0x80).hex(),
                        'metadata': hex(metadata), 'metadata_bytes': s.remote.memory(metadata, 0x40).hex(),
                        'rows': rows, 'controls': hex(controls),
                        'control_bytes': s.remote.memory(controls, 28 * max(rows, 1)).hex() if controls else None})
        s.input(0x10465984, [0x1400])
        s.audit.append({'language_page_responsive': True})
        s.write_audit()
        print('PASS language page opened and UI responsive', flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
