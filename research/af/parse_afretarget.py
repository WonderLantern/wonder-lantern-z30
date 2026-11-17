"""Parse an AF retarget log (A:\\Z30\\RTnnnnnn.BIN, modules/af_retarget, AFRTGT8 / AFRTGT9).

Usage: parse_afretarget.py RTnnnnnn.BIN [--all]
Prints the hook counts, then one line per tracking update around each retarget
(or all with --all): time, source (D detection / I IMU), subjects, face state,
act (F fire, W write, B blocked), face box (cells) and its tracker-grid target,
the tracker position before / after the write (grid x,y), the tracking box
centre (units), 0xC23AA034 and (v9) 0xC237F684 and 0xC23AA024.
The question it answers: after a write, does the position stay at the face
(the tracker took it) or snap back to the old target on the next update?
"""
import struct, sys

REC8 = struct.Struct('<I4B2I4h3I')
REC9 = struct.Struct('<I4B2I4h3I17I')


def load(path):
    d = open(path, 'rb').read()
    if d[:8] == b'AFRTGT8\0':
        count, fires, ticks, rb, writes, hold = struct.unpack_from('<6I', d, 8)
        rec, hdr, imu, hooks = REC8, 32, 0, None
    elif d[:8] == b'AFRTGT9\0':
        count, fires, ticks, rb, writes, hold, imu, hooks = struct.unpack_from('<8I', d, 8)
        rec, hdr = REC9, 40
    else:
        raise SystemExit('not an AFRTGT8/9 file')
    assert rb == rec.size, rb
    recs = [rec.unpack_from(d, hdr + i * rb) for i in range(count)]
    return dict(fires=fires, ticks=ticks, imu_ticks=imu, hooks=hooks, writes=writes, hold=hold), recs


def grid(v):
    return '%3d,%3d' % (v & 0xFFFF, v >> 16)


def line(r):
    t, _, n, fs, act, b0, b1, fx, fy, fw, fh, before, after, aux = r[:14]
    flags = ''.join(c for c, bit in (('F', 1), ('W', 2), ('B', 4)) if act & bit) or '-'
    tx, ty = (2 * fx + fw) * 1350 // 1000, (2 * fy + fh) * 1041 // 1000 + 8   # v10 fit (RT000114)
    cx, cy = (b0 >> 16) + 276, (b1 & 0xFFFF) + 276
    s = '%8.2f %s n=%d fs=%3x %-3s face=(%2d,%2d %2dx%2d)->%3d,%3d pos %s -> %s box=(%4d,%4d) aux=%d' % (
        t / 1000, 'I' if act & 8 else 'D', n, (fs & 0x7F) | (0x100 if fs & 0x80 else 0), flags,
        fx, fy, fw, fh, tx, ty, grid(before), grid(after), cx, cy, aux)
    if len(r) > 14:
        s += ' shown=%d a024=%05x' % (r[14], r[15 + 9])
    return s


def main(path, *opts):
    hdr, recs = load(path)
    print('detection updates %d, IMU ticks %d, hooks %s; tracking records %d, retargets %d (writes %d each, hold %d)' % (
        hdr['ticks'], hdr['imu_ticks'], 'n/a' if hdr['hooks'] is None else bin(hdr['hooks']),
        len(recs), hdr['fires'], hdr['writes'], hdr['hold']))
    if not hdr['ticks'] and not hdr['imu_ticks']:
        print('no callback ran: the camera EG has no detection / IMU hook (flash the --module retarget EG)')
    show = set(range(len(recs))) if '--all' in opts else set()
    for i, r in enumerate(recs):
        if r[4] & 3:
            show.update(range(max(0, i - 3), min(len(recs), i + 15)))
    last = -2
    for i in sorted(show):
        if i != last + 1:
            print('  ...')
        print(line(recs[i]))
        last = i


if __name__ == '__main__':
    main(*sys.argv[1:])
