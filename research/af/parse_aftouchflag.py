"""Parse an AF touch-flag log (A:\\Z30\\TFnnnnnn.BIN, research/af/modules/af_touchflag, AFTFLG1).

Usage: parse_aftouchflag.py TFnnnnnn.BIN
One line per IMU tick while tracking, collapsed to the lines where something
changes: time, AF working, subjects, face state, the touch flag 0xC18C7240,
the tracker mode 0xC23A9CB4 (0x202 tracking, 0x203 face AF after an upgrade),
whether the tracker struct is live, and S where the module set the flag.
"""
import struct, sys

REC = struct.Struct('<I4B4I')


def load(path):
    d = open(path, 'rb').read()
    assert d[:8] == b'AFTFLG1\0', d[:8]
    count, sets, ticks, rb = struct.unpack_from('<4I', d, 8)
    assert rb == REC.size
    return dict(sets=sets, ticks=ticks), [REC.unpack_from(d, 32 + i * rb) for i in range(count)]


def main(path):
    h, recs = load(path)
    print(f"{len(recs)} tracking ticks of {h['ticks']}, flag set {h['sets']} times")
    last = None
    for t, _, af, n, act, fs, flag, mode, trk in recs:
        key = (af, n, fs & 0x1FF, flag, mode, trk != 0, act)
        if key != last or act:
            print(f'{t / 1000:8.2f} af={af} n={n} fs={fs & 0x1FF:3x} flag={flag} mode={mode:#x} tracker={"on" if trk else "--"}{"  S" if act else ""}')
        last = key


if __name__ == '__main__':
    main(*sys.argv[1:])
