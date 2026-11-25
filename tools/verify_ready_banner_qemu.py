"""QEMU: the live-view ready banner appears, goes after SHOW_MS and leaves the stock picture.

Needs a flash build made with build(..., ready_banner='qemu-test') (the banner
without the card loader's load-time call). Boots the stock photo live view
(z30-qemu liveview_session), writes the build's edits and tail into the guest,
then calls banner_start as the loader would and lets the live view run without
forcing refreshes. Frames at fixed times; checks: the state word walks armed ->
showing -> done, the banner box differs from the stock frame while showing, and
after it the screen equals a stock frame taken under the same conditions.
QEMU display model; not hardware evidence.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('qemu-root', 'image', 'report', 'output-dir'):
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--language', choices=('en', 'zh-Hans', 'zh-Hant'), default='zh-Hans')
    a = p.parse_args()
    image = a.image.read_bytes()
    report = json.loads(a.report.read_text())
    if hashlib.sha256(image).hexdigest() != report['sha256']:
        raise ValueError('Image/report identity mismatch')
    banner = report['features']['ready_banner']
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import DEVICE_ENV, start
    s = Session(str(a.output_dir.resolve()), device_env=DEVICE_ENV)

    def write(address, data):
        for offset in range(0, len(data), 1024):
            block = data[offset:offset + 1024]
            assert s.remote.command(f'M{address + offset:x},{len(block):x}:' + block.hex()) == 'OK'

    def word(address):
        return struct.unpack('<I', s.remote.memory(address, 4))[0]

    def frame(name):
        s.frame(name)
        raw = (s.graphics / 'gui-latest.ppm').read_bytes()
        return raw.split(b'\n', 3)[3]

    x, y, w, h = banner['box']
    def box(pixels):
        return b''.join(pixels[((y + r) * 720 + x) * 3:((y + r) * 720 + x + w) * 3] for r in range(h))

    state = int(banner['state'], 16)
    steps = []
    try:
        start(s)
        s.run_for(1.0)
        stock = frame('00-stock')
        for edit in report['edits']:
            address = 0x10020000 + int(edit['offset'], 16)
            assert s.remote.memory(address, len(bytes.fromhex(edit['expected_hex']))) == bytes.fromhex(edit['expected_hex'])
            write(address, bytes.fromhex(edit['replacement_hex']))
        size = (a.qemu_root / '.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000 + size - 2, image[size - 2:])
        # Card builds keep the pixels in RAM (the loaded tail); this flash test build has them
        # in flash, which the renderer does not take. Copy them into a stock heap block.
        index = ('en', 'zh-Hans', 'zh-Hant').index(a.language)
        pixels_at = int(banner['pixels'][a.language], 16)
        size = banner['size'][0] * banner['size'][1] * 4
        block = int(s.input(0x10211f4c, [size + 64, 0])['registers']['r0'], 16)
        assert block, 'no heap block'
        ram = (block + 31) & ~31
        write(ram, s.remote.memory(pixels_at, size))
        write(int(banner['table'], 16) + 4 * index, struct.pack('<I', ram))
        lang = {'en': 3, 'zh-Hans': 26, 'zh-Hant': 27}[a.language]
        s.input(0x10465990, [0x1400, lang])
        s.run_for(0.5)
        before = frame('01-patched-before-start')
        probe = s.input(0x10465984, [0x1400])        # the language getter banner_start uses
        steps.append(dict(step='language getter', completed=probe['completed'], r0=probe['registers']['r0']))
        print(json.dumps(steps[-1]), flush=True)
        s.input(int(banner['on_load'], 16) & ~1, [0, 0, 0])
        phase = word(state)
        steps.append(dict(step='banner_start', phase=phase, language=word(state + 8)))
        assert phase == 1, phase
        shown = None
        for t in (0.5, 1.0, 1.5, 2.5, 3.5):
            s.run_for(0.5 if t <= 1.5 else 1.0)
            pixels = frame(f'1{int(t * 10):02d}-after-{t}s')
            phase = word(state)
            differs = box(pixels) != box(stock)
            steps.append(dict(step=f'{t}s', phase=phase, banner_box_differs=differs,
                              screen_equals_stock=pixels == stock))
            if differs and shown is None:
                shown = t
            print(json.dumps(steps[-1]), flush=True)
        assert shown is not None, 'the banner never appeared'
        assert word(state) == 4, 'the banner did not finish'
        assert box(pixels) == box(stock), 'the banner box was not restored'
        s.audit.append(dict(ready_banner=steps, first_seen_s=shown, language=a.language,
                            box=banner['box'], physical_verified=False))
        s.write_audit()
        print('PASS ready banner: seen at', shown, 's; gone and the box back to stock', flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
