"""QEMU: the 3D tracking screen-1 subject boxes drawn in the stock UI renderer.

Loads a built EG (edits + tail) into the stock QEMU session, starts the stock live
view, switches 3D tracking on, then at every pass of the patched renderer hook
plants two subjects in the AF subject list (one with an eye, the focused one under
the stock frame, one face only) with screen 1 shown and no tracking. After the
hook returns the UI canvas (0xD045F200, 720 x 480 RGBA, rows bottom-up) must hold
the white eye outline and the grey face outline at the computed places, and the
middle of each box must be untouched. With screen 1 off the canvas must have no
box pixels. Frames are saved for a visual check. QEMU only: no AF, no detector.
"""
import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
CANVAS = 0xD045F200


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('qemu-root', 'image', 'report', 'output-dir'):
        p.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, DEVICE_ENV
    from z30_extensions.features.tracking3d import boxes as tb
    report = json.loads(a.report.read_text())
    image = a.image.read_bytes()
    control = report['features']['tracking3d_control']
    hook = None
    for e in report['edits']:
        if 0x10020000 + int(e['offset'], 16) == tb.RENDER_SITE:
            hook = True
    assert hook, 'renderer hook not in the build'
    out = a.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    s = Session(str(out), device_env=DEVICE_ENV)
    r = s.remote

    def write(address, data):
        for off in range(0, len(data), 1024):
            v = data[off:off + 1024]
            assert r.command(f'M{address + off:x},{len(v):x}:' + v.hex()) == 'OK'

    def canvas(x, y):
        return struct.unpack('<I', r.memory(CANVAS + ((480 - y - 1) * 720 + x) * 4, 4))[0]

    result = dict(passes=0)
    try:
        scatter = []
        for e in report['edits']:
            address = 0x10020000 + int(e['offset'], 16)
            old, new = bytes.fromhex(e['expected_hex']), bytes.fromhex(e['replacement_hex'])
            assert r.memory(address, len(old)) == old, hex(address)
            write(address, new)
            if 0x11f81c94 <= address and address + len(old) <= 0x12079eec:
                scatter.append((0xc1800000 + address - 0x11f81c94, new))
        size = (a.qemu_root / '.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000 + size - 2, image[size - 2:])
        start(s)
        for address, new in scatter:
            write(address, new)
        choice = int(control['choice'], 16)
        get = int(control['functions']['trk3d_get'], 16)
        s.input(choice, [])
        state = int(s.input(get, [])['registers']['r0'], 16)
        assert state, 'no 3D tracking state'
        write(state, struct.pack('<I', 1))
        t = lambda v: v * 135 // 10 + 140          # list -> tracking units
        eye_face, eye = (200, 140, 24, 24), (194, 136, 5, 5)
        face = (100, 70, 30, 30)

        move = dict(dx=0)

        def plant(shown):
            write(tb.SUBJECT_COUNT, struct.pack('<I', 2))
            e0 = bytearray(tb.ENTRY_BYTES); struct.pack_into('<IBB4h', e0, 0, 0, 1, 0, *eye_face)
            struct.pack_into('<4h', e0, 0x36, *eye)
            e1 = bytearray(tb.ENTRY_BYTES); struct.pack_into('<IBB4h', e1, 0, 1, 1, 1, face[0] + move['dx'], *face[1:])
            write(tb.SUBJECT_ENTRIES, bytes(e0) + bytes(e1))
            write(tb.STOCK_FRAME, struct.pack('<4h', t(194) - 55, t(136) - 55, t(194) + 55, t(136) + 55))
            write(tb.BOX_SHOWN, struct.pack('<I', shown)); write(tb.TRACKING, struct.pack('<I', 0))

        def geometry(x, y, size, is_eye):
            cx, cy = ((x * tb.X_MUL) >> 8) + tb.X_ADD, ((y * tb.Y_MUL) >> 8) + tb.Y_ADD
            half = tb.EYE_HALF if is_eye else ((size * tb.FACE_HALF_MUL) >> 8) + tb.FACE_HALF_ADD
            return cx, cy, half

        def passes(shown, count, seconds=25):
            boxes = int(control['boxes'], 16) & ~1
            entry_bp, exit_bp = boxes, tb.RENDER_CONTINUE
            seen = 0; samples = []
            deadline = time.monotonic() + seconds
            assert r.command(f'Z1,{entry_bp:x},2') == 'OK'
            assert r.command(f'Z1,{exit_bp:x},2') == 'OK'
            try:
                while seen < count and time.monotonic() < deadline:
                    r.send('c'); r.sock.settimeout(max(.01, deadline - time.monotonic()))
                    try: packet = r.receive()
                    except socket.timeout:
                        r.sock.sendall(b'\x03'); r.sock.settimeout(5); s.packet = r.receive(); break
                    s.packet = packet
                    thread = packet.split('thread:')[1].split(';')[0]
                    assert r.command('Hg' + thread) == 'OK'
                    pc = int(r.registers()['r15'], 16)
                    if pc == entry_bp:
                        plant(shown)
                    elif pc == exit_bp:
                        seen += 1
                        ex, ey, eh = geometry(*eye[:2], 0, True)
                        fx, fy, fh = geometry(*face[:2], face[2], False)
                        samples.append(dict(eye_top=hex(canvas(ex, ey - eh)), eye_mid=hex(canvas(ex, ey)),
                                            face_top=hex(canvas(fx, fy - fh)), face_left=hex(canvas(fx - fh, fy)),
                                            face_mid=hex(canvas(fx, fy))))
                    bp = entry_bp if pc == entry_bp else exit_bp
                    assert r.command(f'z1,{bp:x},2') == 'OK'
                    assert r.command('Hc' + thread) == 'OK'
                    r.send('s'); r.sock.settimeout(5); s.packet = r.receive()
                    assert r.command('Hc-1') == 'OK'
                    assert r.command(f'Z1,{bp:x},2') == 'OK'
            finally:
                r.command(f'z1,{entry_bp:x},2'); r.command(f'z1,{exit_bp:x},2')
            return samples

        # control: the same planted screen 1 with the 3D tracking switch off (no drawing)
        write(state, struct.pack('<I', 0))
        passes(1, 60, seconds=40)
        s.frame('control-switch-off')
        write(state, struct.pack('<I', 1))
        on = passes(1, 6)
        passes(1, 60, seconds=40)            # keep screen 1 planted while the frames compose
        s.frame('screen1-boxes')
        result.update(screen1=on, passes=len(on))
        # moving subject: the face box jumps 40 list units every pass; the old place must be clear
        moved = []
        for k in range(1, 6):
            move['dx'] = 40 * (k % 2)
            sample = passes(1, 1)
            fx, fy, fh = geometry(face[0] + 40 * ((k + 1) % 2), face[1], face[2], False)
            nx, ny, nh = geometry(face[0] + move['dx'], face[1], face[2], False)
            moved.append(dict(old_top=hex(canvas(fx, fy - fh)), new_top=hex(canvas(nx, ny - nh))))
        result['moved'] = moved
        move['dx'] = 0
        off = passes(0, 4)
        passes(0, 60, seconds=40)            # let the cleared canvas compose
        s.frame('screen1-off')
        result.update(off=off, drawn=struct.unpack('<2I', r.memory(state + 16, 8)))
        last = on[-1] if on else {}
        result['raw_last'] = last
    finally:
        (out / 'boxes.json').write_text(json.dumps(result, indent=2) + '\n')
        s.close()
    print(json.dumps(result, indent=1))


if __name__ == '__main__':
    main()
