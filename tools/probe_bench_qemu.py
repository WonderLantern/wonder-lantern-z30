"""QEMU: the benchmark page of a card build runs a whole benchmark on stock firmware.

Two steps, because the card build needs Keystone and the QEMU session runs in WSL:

  python tools/probe_bench_qemu.py --prepare --source Z_30_0120.bin --resident output/bench-v2 --out build/bench/card
  python3 tools/probe_bench_qemu.py --qemu-root ../z30-qemu --card build/bench/card --output-dir bench-probe

As tools/probe_handheld_qemu.py, the run does the resident loader's job on stock
firmware, opens the extension root and the Benchmark page, presses Run, and
lets the menu timer drive the run to its end. It checks the rows, that the run
finishes with every test done or skipped, that the timer slot is free again,
and saves the report text the payload rendered (QEMU has no SD card, so the
file write itself fails there). QEMU's numbers are the emulator's, not the
camera's; the run checks the mechanics (PMU, CP15 reads, the FPU guard).
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
TIMER45 = 0xc20060b0 + 45 * 24
PHASES = ('idle', 'running', 'done', 'stopped', 'error')


def prepare(a):
    from z30_extensions import card
    from z30_extensions.container import extract_eg
    stock = extract_eg(a.source.read_bytes())
    resident = card.load_resident(a.resident / card.RESIDENT_NAME, a.resident / card.REPORT_NAME)
    patch, report = card.build_card(stock, ['full', 'bench'], resident, menu_tab=True, build_tag='qemu')
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / 'WL.PATCH').write_bytes(card.pack(patch))
    from z30_extensions.features.experimental import bench_payload
    report['features']['bench']['state_offset'] = bench_payload.STATE_OFFSET
    (a.out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Prepared', a.out, len(patch.tail), 'tail bytes,', len(patch.edits), 'edits')


def run(a):
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session, png_from_ppm
    from z30_extensions import card
    from z30_extensions.features.card import loader as card_loader
    from z30_extensions.features.experimental import bench as bm
    from menu_path import enter_parent, wait_built
    patch = card.parse((a.card / 'WL.PATCH').read_bytes())
    report = json.loads((a.card / 'report.json').read_text(encoding='utf-8'))
    menu, bf = report['features']['menu_framework'], report['features']['bench']
    s = Session(a.output_dir); results = {}

    def write(address, data):
        for off in range(0, len(data), 1024):
            v = data[off:off + 1024]
            assert s.remote.command(f'M{address + off:x},{len(v):x}:' + v.hex()) == 'OK'
    def word(address): return int.from_bytes(s.remote.memory(address, 4), 'little')
    def current(): return int(s.input(0x1046109a, [])['registers']['r0'], 16)
    def packet(contact, x, y):
        p = bytearray(60); struct.pack_into('<I', p, 0, 1)
        p[8], p[9] = contact, 1; struct.pack_into('<2H', p, 0x12, x, y)
        s.input(0x103ce7e6, [('scratch', 0)], payload=p); s.run_for(.5)
    def touch(x, y):
        packet(1, x, y); packet(0, x, y)
    def activate(page, ident, row):
        wait_built(s, page, row)
        for _ in range(20):
            s.input(0x1045d54c, [page, ident]); s.run_for(.5)
            if sum(struct.unpack('<2H', s.remote.memory(page + 22, 4))) == row: break
        for _ in range(3):
            touch(250, 86 + row * 52)
            if current() != page: break
    def shown(slot):
        length, ptr, _ = struct.unpack('<3I', s.remote.memory(0xc2013368 + slot * 12, 12))
        if not length: return ''
        return s.remote.memory(ptr, length).decode('ascii', 'replace')
    def rows():
        return [shown(bm.STATUS_SLOT)] + [shown(slot) for slot in bm.LABEL_SLOTS[1:]]
    def grab(name):
        raw = (s.graphics / 'gui-latest.ppm').read_bytes()
        (s.out / (name + '.png')).write_bytes(png_from_ppm(raw))
    try:
        raw = int(s.input(0x10211f4c, [len(patch.tail) + 0x10000, 0])['registers']['r0'], 16)
        assert raw, 'pool allocation'
        base = (raw + 0xffff) & ~0xffff
        delta = base - patch.link_base
        write(base, card.relocate(patch.tail, patch.relocs, delta))
        for edit in patch.edits:
            old = s.remote.memory(edit.address, len(edit.expected))
            if old != edit.expected:
                assert edit.address == card_loader.IMU_SITE, (edit.name, hex(edit.address))
            write(edit.address, card.relocate(edit.replacement, edit.relocs, delta))
        runtime = lambda linked: (linked & ~1) - card.CHECK_BASES[-1] + base
        s.input(0x102355ee, [0xc182e688, 1]); s.run_for(2)
        s.input(0x10465990, [0x1400, 3]); s.input(0x10452cc0, [1])          # English
        s.input(0x1045f732, [2, 397]); s.run_for(.5)
        s.input(runtime(int(menu['functions']['open'], 16)), []); s.run_for(1)
        state = word(int(menu['state_anchor'], 16))
        assert state, 'extension state'
        page = state + bf['page_offset']
        parent, control, row = enter_parent(menu, 'bench', state, activate, s)
        activate(parent, control, row)
        assert current() == page, 'benchmark page opened'
        wait_built(s, page, bm.ROWS - 1)
        results['title'] = [shown(bm.TITLE_SLOT), shown(bm.LABEL_SLOTS[0])]
        results['before'] = rows()
        print('before', results['title'], results['before'], flush=True)
        assert results['before'][0] == 'Ready', results['before']
        grab('01-page')
        bstate = runtime(int(bf['payload'], 16)) + bf['state_offset']
        activate(page, bf['controls'][0], 0)
        trace = []
        for second in range(a.seconds):
            s.run_for(1)
            phase, error, _, _, timer, step, ticks = struct.unpack('<7I', s.remote.memory(bstate, 28))
            trace.append((second, PHASES[phase], step, ticks, rows()[0]))
            print(trace[-1], flush=True)
            grab(f'run-{second:02d}')
            if phase != 1: break
        results['trace_tail'] = trace[-3:]
        results['after'] = rows()
        print('after', results['after'], flush=True)
        grab('02-done')
        phase, error = struct.unpack('<2I', s.remote.memory(bstate, 8))
        assert PHASES[phase] == 'done', (PHASES[phase], error)
        assert results['after'][0].startswith('Done'), results['after']
        assert not word(TIMER45), 'timer slot 45 free again'
        # The rendered report: find the bounce buffer pointer among the state words.
        blob = s.remote.memory(bstate, 0x100)
        text = None
        for (ptr,) in struct.iter_unpack('<I', blob):
            if 0xc1800000 <= ptr < 0xc7a00000 and not ptr & 63:
                head = s.remote.memory(ptr, 16)
                if head.startswith(b'Wonder Lantern'):
                    text = s.remote.memory(ptr, 8192).split(b'\0')[0].decode('ascii', 'replace'); break
        assert text, 'report text'
        (s.out / 'BENCH.TXT').write_text(text, encoding='ascii', errors='replace')
        print(text, flush=True)
        results['report_lines'] = text.count('\n')
        s.audit.append(dict(bench=results, physical_verified=False))
        s.write_audit()
        print('PASS benchmark page:', json.dumps(results), flush=True)
    except Exception:
        if 'base' in locals():
            at = runtime(int(bf['payload'], 16)) + bf['state_offset']
            names = 'phase error detail page timer step ticks started_ms run_ms mpidr_end fpu'.split()
            print('state', dict(zip(names, map(hex, struct.unpack(f'<{len(names)}I', s.remote.memory(at, 4 * len(names)))))), flush=True)
            print('registers', s.remote.registers(), flush=True)
        raise
    finally:
        s.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--prepare', action='store_true')
    p.add_argument('--source', type=Path)
    p.add_argument('--resident', type=Path)
    p.add_argument('--out', type=Path)
    p.add_argument('--qemu-root', type=Path)
    p.add_argument('--card', type=Path)
    p.add_argument('--output-dir')
    p.add_argument('--seconds', type=int, default=180)
    a = p.parse_args()
    prepare(a) if a.prepare else run(a)


if __name__ == '__main__':
    main()
