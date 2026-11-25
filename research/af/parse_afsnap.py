"""Compare AF snapshots (A:\\Z30\\SNnnnnnn.BIN from research/af/modules/af_snap).

Usage: parse_afsnap.py SNnnnnnn.BIN GROUPS [--show N]
       parse_afsnap.py SNnnnnnn.BIN shown [--show N]
GROUPS splits the snapshots in start-method groups in session order, e.g.
"3,3,2" = 3 started with OK, 3 by touch, 2 with a half-press. Lists the words
that hold one value inside each group and differ between at least two groups
(the start-method candidates), grouped into runs of nearby addresses.
"shown" (af_snap built with -DSNAP_SHOWN): the snapshots are grouped by their
kind (bit 31 of the start time: 1 = tracking box shown, screen 1; 0 = hidden).
"focus" (-DSNAP_FOCUS): grouped by the focus result in bits 30..31 of the start
time, in the order 2 (in focus), 1 (not in focus), 0 (idle).
"""
import struct, sys


def load(path):
    d = open(path, 'rb').read()
    assert d[:8] == b'AFSNAP1\0', d[:8]
    used, nwin, ticks, sessions = struct.unpack_from('<4I', d, 8)
    wins = [struct.unpack_from('<2I', d, 24 + 8 * w) for w in range(nwin)]
    slots = 8
    times = [struct.unpack_from('<2I', d, 24 + 8 * nwin + 8 * s) for s in range(slots)]
    hdr = 24 + 8 * nwin + 8 * slots
    size = sum(n for _, n in wins)
    snaps = [d[hdr + s * size: hdr + (s + 1) * size] for s in range(used)]
    return dict(used=used, ticks=ticks, sessions=sessions, wins=wins, times=times[:used], snaps=snaps, size=size)


def address(s, off):
    for base, n in s['wins']:
        if off < n: return base + off
        off -= n


def candidates(s, groups):
    if groups == 'focus':
        kinds = [s['times'][k][0] >> 30 for k in range(s['used'])]
        order, groups = [], []
        for v in (2, 1, 0):
            ks = [k for k in range(s['used']) if kinds[k] == v]
            if ks: order += ks; groups.append(len(ks))
        s = dict(s, snaps=[s['snaps'][k] for k in order])
    elif groups == 'shown':
        order = [k for k in range(s['used']) if s['times'][k][0] >> 31] + [k for k in range(s['used']) if not s['times'][k][0] >> 31]
        n = sum(1 for k in range(s['used']) if s['times'][k][0] >> 31)
        s = dict(s, snaps=[s['snaps'][k] for k in order])
        groups = [n, s['used'] - n]
    assert sum(groups) <= s['used'], (groups, s['used'])
    bounds, at = [], 0
    for g in groups: bounds.append((at, at + g)); at += g
    words = [struct.unpack(f'<{s["size"] // 4}I', sn) for sn in s['snaps']]
    out = []
    for i in range(s['size'] // 4):
        vals = []
        for a, b in bounds:
            v = {words[k][i] for k in range(a, b)}
            if len(v) != 1: break
            vals.append(v.pop())
        else:
            if len(set(vals)) > 1:
                out.append((address(s, 4 * i), vals))
    return out


def main(path, groups, *opts):
    s = load(path)
    g = groups if groups in ('shown', 'focus') else [int(x) for x in groups.split(',')]
    print(f"{s['used']} snapshots of {s['sessions']} sessions, {s['ticks']} ticks; start times (s):",
          ' '.join(f'{(a & 0x3FFFFFFF) / 1000:.1f}:{a >> 30}' if groups == 'focus' else f'{(a & 0x7FFFFFFF) / 1000:.1f}{"S" if a >> 31 else ""}' for a, _ in s['times']))
    cand = candidates(s, g)
    print(f'{len(cand)} words constant within each group {g} and different between groups')
    show = int(opts[opts.index('--show') + 1]) if '--show' in opts else 300
    prev = None
    for a, vals in cand[:show]:
        if prev is None or a - prev > 0x40: print('--')
        print(f'  {a:#010x}  ' + '  '.join(f'{v:#010x}' for v in vals))
        prev = a


if __name__ == '__main__':
    main(*sys.argv[1:])
