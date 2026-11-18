"""Print an AF handover log (A:\\Z30\\HOnnnnnn.BIN from modules/af_handover).

Usage: parse_afhandover.py HOnnnnnn.BIN
One line per logged tracking tick: time, subjects, face state, tracking box
(left, top, size), face grid box, the face point's offset from the box centre
and whether OK was pressed. Lines where nothing changed are folded.
"""
import struct, sys


def main(path):
    d = open(path, 'rb').read()
    assert d[:8] in (b'AFHAND1\0', b'AFHAND2\0', b'AFHAND3\0', b'AFHAND4\0', b'AFHAND5\0', b'AFHAND6\0', b'AFHAND7\0'), d[:8]
    v2 = d[6:7] in (b'2', b'3', b'4', b'5', b'6', b'7')
    count, presses, ticks, rb, scale, hold = struct.unpack_from('<6I', d, 8)
    print(f'{path}: {count} tracking ticks of {ticks}, OK pressed {presses} times (scale {scale}, hold {hold})')
    last = None
    for i in range(count):
        t, tr, n, st, pr, b0, b1, fx, fy, fw, fh, dx, dy = struct.unpack_from('<IBBBB2I6i', d, 32 + rb * i)
        x0, x1, y0 = b0 >> 16, b1 >> 16, b1 & 0xFFFF
        if v2: st = (st & 0x7F) | (0x100 if st & 0x80 else 0)       # v1 clamped the state to 255
        row = (n, st, x0, y0, x1 - x0, fx, fy, fw, fh, dx, dy, pr)
        if row == last: continue
        last = row
        extra = f'blocked {dx} seen {dy}' if d[6:7] >= b'7' else f'offset ({dx},{dy})'
        print(f'{t / 1000:8.2f}s subjects {n} face {st:#05x}  box ({x0},{y0}) {x1 - x0}  face ({fx},{fy},{fw},{fh})'
              f'  {extra}{"  << OK" if pr else ""}{f" (synth {tr & 0x7F})" if pr and tr & 0x80 else ""}')


    if d[6:7] in (b'4', b'5', b'6', b'7'):
        tail = 32 + rb * count
        rcount = struct.unpack_from('<I', d, tail)[0]
        press = [p / 1000 for p in struct.unpack_from('<8I', d, tail + 4) if p]
        print(f'input ring: {rcount} snapshots; OK pressed by the module at {press} s')
        last = None
        for i in range(rcount):
            t, idx, *w = struct.unpack_from('<2I64I', d, tail + 36 + 264 * i)
            ents = [tuple(w[4 * k:4 * k + 4]) for k in range(16)]
            diff = [(k, e) for k, e in enumerate(ents) if last is None or e != last[k]]
            last = ents
            print(f'  {t / 1000:8.2f}s idx {idx:#010x} ' + ' '.join(
                f'[{k}] st {e[0]:#x} w1 {e[1]:#x} w2 {e[2]:#x} w2new {e[3]:#x}' for k, e in diff))
        if d[6:7] in (b'5', b'6', b'7'):
            held = struct.unpack_from('<4I', d, tail + 36 + 264 * rcount)
            print('held key words passed with the last press:', ' '.join(f'{x:#x}' for x in held))


if __name__ == '__main__':
    main(sys.argv[1])
