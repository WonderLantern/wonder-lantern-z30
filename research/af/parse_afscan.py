"""Rank the words of an AF scan (A:\\Z30\\SCnnnnnn.BIN from modules/af_scan).

Usage: parse_afscan.py SCnnnnnn.BIN [--all]
A word is a tracking candidate when it changed in a good share of the tracking
sweeps of its chunk and (almost) never in the idle ones (at most 2 + 2 %:
a tracking session's last change can be seen by the next, idle, sweep). Candidates are printed
grouped into runs of nearby addresses (structs), with the change rates and the
value range seen while tracking (box coordinates have a small, plausible range).
A box scan (af_scan built with -DSCAN_BOX, header word 7 = 1) counts the
"key" sweeps while the tracking box is shown but not tracking (screen 1);
the box position changes only when it is moved, so any word changed at least
3 times there and at most twice otherwise is listed.
"""
import struct, sys


def load(path):
    d = open(path, 'rb').read()
    assert d[:8] == b'AFSCAN1\0', d[:8]
    base, size, chunk, ticks, ticks_t, variant = struct.unpack_from('<6I', d, 8)
    n, w = size // chunk, size // 4
    hdr = 32 + 8 * n
    sw_t = struct.unpack_from(f'<{n}I', d, 32); sw_i = struct.unpack_from(f'<{n}I', d, 32 + 4 * n)
    ct = struct.unpack_from(f'<{w}H', d, hdr); ci = struct.unpack_from(f'<{w}H', d, hdr + 2 * w)
    mn = struct.unpack_from(f'<{w}i', d, hdr + 4 * w); mx = struct.unpack_from(f'<{w}i', d, hdr + 8 * w)
    return dict(base=base, size=size, chunk=chunk, ticks=ticks, ticks_t=ticks_t, sw_t=sw_t, sw_i=sw_i,
                ct=ct, ci=ci, mn=mn, mx=mx, box=variant == 1)


def candidates(s, min_rate=0.2, max_idle=0.02):
    out = []
    for i in range(len(s['ct'])):
        c = i * 4 // s['chunk']
        st, si = s['sw_t'][c], s['sw_i'][c]
        if not st: continue
        rt, ri = s['ct'][i] / st, s['ci'][i] / si if si else 0.0
        # a tracking session's last change can land in the following idle sweep: allow a few
        hit = s['ct'][i] >= 3 and s['ci'][i] <= 2 if s['box'] else rt >= min_rate and s['ci'][i] <= 2 + max_idle * si
        if hit:
            out.append((s['base'] + 4 * i, rt, ri, s['mn'][i], s['mx'][i]))
    return out


def main(path, show_all=False):
    s = load(path)
    print(f"{path}: {s['base']:#x}+{s['size']:#x}, {s['ticks']} ticks, {s['ticks_t']} in the key state; "
          f"{'box shown' if s['box'] else 'tracking'} sweeps per chunk {min(s['sw_t'])}-{max(s['sw_t'])}, idle {min(s['sw_i'])}-{max(s['sw_i'])}")
    cand = candidates(s)
    print(f"{len(cand)} {'box-shown' if s['box'] else 'tracking'}-only candidates")
    run = []
    for c in cand + [None]:
        if c and run and c[0] - run[-1][0] <= 0x40:
            run.append(c); continue
        if run and (show_all or len(run) >= 2 or abs(run[0][4] - run[0][3]) < 100000):
            print(f'-- {run[0][0]:#x}..{run[-1][0]:#x} ({len(run)} words)')
            for a, rt, ri, lo, hi in run:
                print(f'   {a:#x}  tracking {rt:5.2f}  idle {ri:5.2f}  range {lo} .. {hi}')
        run = [c] if c else []


if __name__ == '__main__':
    main(sys.argv[1], '--all' in sys.argv)
