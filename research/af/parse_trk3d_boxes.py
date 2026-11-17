"""Summarise A:\\Z30\\AFnnnnnn.BIN from af_log -DLOG11 (3D tracking grey box diagnostics).

Usage: parse_trk3d_boxes.py AFnnnnnn.BIN
Prints, whenever it changes: screen (F684 shown / F688 tracking), subject counts (stock
and AF list), the 3D tracking switch, the drawer's call count, the stages it ever reached
(bits 1 state, 2 UI phase, 3 live view descriptor, 4 switch on, 5 screen 1, 6 surface,
7 subjects, 8 drawn), its last stage this call, boxes drawn, and the surface address.
"""
import struct, sys, runpy
from pathlib import Path

DIAG = 12 + 8 * 8


def main(path):
    p = runpy.run_path(str(Path(__file__).with_name('parse_aflog.py')))
    w, recs, ticks, dropped = p['load'](path)
    base = w[0][0]
    g = lambda r, a: p['word'](w, r, a) & 0xFFFFFFFF
    print(f'{len(recs)} records, {ticks} ticks; 3D tracking state at {base:#x}')
    prev = None
    for (ms, _), r in recs:
        h = base + 16
        row = (g(r, 0xC237F684), g(r, 0xC237F688), g(r, 0xC237E3E4), g(r, 0xC259EF50), g(r, base),
               g(r, h + DIAG + 4), g(r, h + DIAG + 8), g(r, h + DIAG + 12), g(r, h + DIAG + 16), g(r, 0xC18200C0), g(r, 0xC181F6FC) & 0xFF)
        style = (r and None)
        a = lambda o: p['word'](w, r, 0xC181A27C + (o & ~3)) >> (8 * (o & 3)) & 0xFF
        style = (a(1), a(2), a(3), a(9), p['word'](w, r, 0xC1F8F136 - 2) >> 16 & 0xFFFF)
        row = row + style
        if row != prev:
            calls = g(r, h + DIAG)
            print(f'{ms / 1000:7.2f}s shown {row[0]} trk {row[1]} subj {row[2]}/{row[3]} on {row[4]} calls {calls} '
                  f'stages {row[5]:#05x} last {row[6]} drawn {row[7]} surf {row[8]:#x} slot {row[9]:#x} phase {row[10]}  style: mode {style[0]} col {style[1]} result {style[2]} code {style[3]} out {style[4]:#06x}')
        prev = row


if __name__ == '__main__':
    main(sys.argv[1])
