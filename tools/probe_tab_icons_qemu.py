"""QEMU-only observer: which stock calls draw the menu tab bar icons.

Boots stock firmware, opens the setup menu and records calls of the OpenVG
image draw (0x1050B5F0) and the stock image-resource helpers given with
--watch, with r0-r3 and the return address, while the tab bar draws.
Read-only guest. Used to find the tab icon table (docs/menu-tab-research.md).
"""
import argparse
import json
import sys
from pathlib import Path

NEVER = 0x100200AC


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--watch', type=lambda x: int(x, 0), nargs='*', default=[0x1050B5F0])
    p.add_argument('--seconds', type=float, default=3)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session

    s = Session(str(a.output_dir.resolve()))
    try:
        s.input(0x102355ee, [0xc182e688, 1])
        s.run_for(2)
        s.input(0x1045f732, [2, 397])
        try:
            s.run_to(NEVER, seconds=a.seconds, observe=a.watch,
                     capture=lambda remote, regs: {k: regs[k] for k in ('r0', 'r1', 'r2', 'r3', 'r14')})
        except RuntimeError:
            pass
        for record in reversed(s.audit):
            if 'observations' in record:
                for o in record['observations']:
                    print(json.dumps({k: o[k] for k in ('pc', 'r0', 'r1', 'r2', 'r3', 'r14')}), flush=True)
                break
        s.write_audit()
    finally:
        s.close()


if __name__ == '__main__':
    main()
