"""Parse A:\\Z30\\AFnnnnnn.BIN written by modules/af_log (read-only AF record log, v1 to v9).

Usage: parse_aflog.py AFnnnnnn.BIN [--words]
Prints every change of the AF box (+0x90) and face box (+0xA8) of the record at
0xC237F578, and a table of which 32-bit words ever changed (by absolute address:
how often, value range), so the live fields can be told from capture snapshots.
"""
import struct, sys


def load(path):
    d = open(path, 'rb').read()
    magic = d[:8]
    if magic == b'AFLOG1\0\0':
        rb, count, ticks, dropped, base = struct.unpack_from('<5I', d, 8)
        windows, hdr = [(base, rb)], 40
    elif magic in (b'AFLOG2\0\0', b'AFLOG3\0\0', b'AFLOG4\0\0', b'AFLOG5\0\0', b'AFLOG6\0\0', b'AFLOG7\0\0', b'AFLOG8\0\0', b'AFLOG9\0\0', b'AFLOG10\0', b'AFLOG11\0', b'AFLOG12\0'):
        rb, count, ticks, dropped, n = struct.unpack_from('<5I', d, 8)
        windows = [struct.unpack_from('<2I', d, 28 + 8 * w) for w in range(n)]
        hdr = 64 if magic[5:6] == b'2' else 160
    else:
        raise ValueError(magic)
    ent = 8 + rb
    recs = [(struct.unpack_from('<2I', d, hdr + i * ent), d[hdr + 8 + i * ent:hdr + ent + i * ent]) for i in range(count)]
    return windows, recs, ticks, dropped


def word(windows, rec, address):
    k = 0
    for base, size in windows:
        if base <= address < base + size:
            return struct.unpack_from('<i', rec, k + address - base)[0]
        k += size
    raise KeyError(hex(address))


def main(path, words=False):
    windows, recs, ticks, dropped = load(path)
    print(f'{path}: windows {[(hex(b), hex(n)) for b, n in windows]}, {len(recs)} changes in {ticks} ticks, dropped {dropped}')
    last = None
    for (t, tk), r in recs:
        try:
            af = tuple(word(windows, r, 0xC237F578 + 0x90 + 4 * i) for i in range(5))
            fa = tuple(word(windows, r, 0xC237F578 + 0xA8 + 4 * i) for i in range(5))
        except KeyError:                 # log 4 has no debug record: --words shows its fields
            continue
        if (af, fa) != last:
            print(f'{t / 1000:8.2f}s  AF {af}  Fa {fa}')
            last = (af, fa)
    if words:
        print('\nchanged words (address: changes, min, max):')
        for base, size in windows:
            for a in range(base, base + size, 4):
                vals = [word(windows, r, a) for _, r in recs]
                n = sum(x != y for x, y in zip(vals, vals[1:]))
                if n: print(f'  {a:#x}: {n:5d}  {min(vals)} .. {max(vals)}')


if __name__ == '__main__':
    main(sys.argv[1], '--words' in sys.argv)
