"""QEMU: the Linux page hands the emulated camera over to the handoff test kernel.

Two steps, because the card build needs Keystone and the QEMU session runs in WSL:

  python tools/probe_linux_qemu.py --prepare --source Z_30_0120.bin --resident output/bench-v2 --out build/linux/card
  python3 tools/probe_linux_qemu.py --qemu-root ../z30-qemu --card build/linux/card --output-dir linux-probe

As the other page probes, the run does the resident loader's job on stock
firmware and opens the page. QEMU has no SD card here, so the probe puts the
test kernel and DTB (tools/build_linux_kit.py) into stock pool memory and marks
them loaded (the payload's test-only `injected` field). Then:

  - Dry run: status OK; GIC, timer, LCD overlay and watchdog values read back;
  - Boot: core 1 must park (its PC in lb_park_loop), and the test kernel's signature at 0x9FFFF000 must show its uptime counter
    running; the overlay plane it painted is saved as a PNG.

The test kernel is the payload's stand-in for a zImage: same entry contract.
QEMU's GIC, timer and display are the emulator's models, not the camera. z30-qemu
models no SP805 watchdog, so the probe sets the payload's test-only skip_wdt.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))
FIELDS = ('phase error detail kernel dtb initrd kernel_size dtb_size initrd_size injected '
          'gicd gicc cbar cntfrq wdt_id wdt_control lcd_size color alpha cstride astride '
          'overlay_enable overlay_format patched park_wait_ms skip_wdt').split()
SIGNATURE = 0x9ffff000
WDT = 0x0426c000


def prepare(a):
    import subprocess
    from z30_extensions import card
    from z30_extensions.container import extract_eg
    stock = extract_eg(a.source.read_bytes())
    resident = card.load_resident(a.resident / card.RESIDENT_NAME, a.resident / card.REPORT_NAME)
    patch, report = card.build_card(stock, ['full', 'linux'], resident, menu_tab=True, build_tag='qemu')
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / 'WL.PATCH').write_bytes(card.pack(patch))
    from z30_extensions.features.experimental import linuxboot_payload
    report['features']['linux']['state_offset'] = linuxboot_payload.STATE_OFFSET
    (a.out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    kit = [sys.executable, str(ROOT / 'tools' / 'build_linux_kit.py'), '--output', str(a.out / 'kit'), '--no-mmc', '--no-usb']
    if a.kernel:
        kit += ['--kernel', str(a.kernel)]
    if a.earlycon_only:
        kit += ['--earlycon-only']
    subprocess.run(kit, check=True)
    print('Prepared', a.out, len(patch.tail), 'tail bytes,', len(patch.edits), 'edits')


def overlay_png(color, alpha, w, h, cs, as_, png_from_ppm):
    rgb = bytearray(w * h * 3)
    for y in range(h):
        for x in range(0, w, 2):
            q = color[y * cs + x * 2:y * cs + x * 2 + 4]
            if len(q) < 4:
                continue
            u, v = q[2] - 128, q[3] - 128
            for k in range(2):
                yy = q[k]
                r = yy + 1.402 * v; g = yy - 0.344 * u - 0.714 * v; b = yy + 1.772 * u
                o = (y * w + x + k) * 3
                rgb[o:o + 3] = bytes(max(0, min(255, int(c))) for c in (r, g, b))
    return png_from_ppm(b'P6\n%d %d\n255\n' % (w, h) + bytes(rgb))


PAGE_OFFSET, PHYS_OFFSET = 0xc0000000, 0x90000000


def linux_boot(s, a, results, state, png_from_ppm):
    """A real zImage: wait, then read the kernel log (via System.map) and the overlay."""
    symbols = {}
    for line in a.system_map.read_text().splitlines():
        parts = line.split()
        if len(parts) == 3:
            symbols[parts[2]] = int(parts[0], 16)
    log_buf, log_len = symbols['__log_buf'], 1 << 14
    b = state()
    w, h = b['lcd_size'] & 0xffff, b['lcd_size'] >> 16
    # Linux has the MMU on (on both cores with SMP): read physical memory from now on.
    assert s.remote.command('Qqemu.PhyMemMode:1') == 'OK', 'QEMU physical memory mode'
    text = ''
    def snap(name):
        color = s.remote.memory(b['color'], b['cstride'] * h)
        alpha = s.remote.memory(b['alpha'], b['astride'] * h)
        (s.out / name).write_bytes(overlay_png(color, alpha, w, h, b['cstride'], b['astride'], png_from_ppm))
    tux_snaps = 0
    for tick in range(16):
        s.run_for(0.25)
        raw = s.remote.memory(log_buf - PAGE_OFFSET + PHYS_OFFSET, log_len)
        text = ''.join(chr(c) if 32 <= c < 127 or c == 10 else '\n' if c == 0 else '' for c in raw)
        if tick in (3, 7, 11):
            snap(f'overlay-early-{(tick + 1) // 4}s.png')
        if 'z30-overlay-fb' in text and tux_snaps < 4:
            tux_snaps += 1
            snap(f'overlay-tux-{tux_snaps}.png')
    for second in range(4, a.seconds):
        s.run_for(1)
        raw = s.remote.memory(log_buf - PAGE_OFFSET + PHYS_OFFSET, log_len)
        # printk ring text: records separated by NULs / descriptors; keep the printable runs.
        text = ''.join(chr(c) if 32 <= c < 127 or c == 10 else '\n' if c == 0 else '' for c in raw)
        if a.min_seconds > 8 and second >= 8 and second % 4 == 0:
            snap(f'overlay-s{second}.png')
        if 'Run /init' in text and second >= a.min_seconds:
            break
    lines = [l for l in text.split('\n') if l.strip()]
    (s.out / 'kernel-log.txt').write_text('\n'.join(lines), encoding='ascii', errors='replace')
    print('\n'.join(lines[-60:]), flush=True)
    b = state()
    w, h = b['lcd_size'] & 0xffff, b['lcd_size'] >> 16
    color = s.remote.memory(b['color'], b['cstride'] * h)
    alpha = s.remote.memory(b['alpha'], b['astride'] * h)
    (s.out / 'overlay.png').write_bytes(overlay_png(color, alpha, w, h, b['cstride'], b['astride'], png_from_ppm))
    results['kernel_log_lines'] = len(lines)
    for needle in ['Linux version', 'Run /init'] + (a.expect or ['z30-overlay']):
        assert needle in text, ('kernel log lacks', needle)
    s.audit.append(dict(linux=results, physical_verified=False))
    s.write_audit()
    print('PASS Linux kernel boot:', json.dumps(results)[:400], flush=True)


def run(a):
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session, png_from_ppm
    from z30_extensions import card
    from z30_extensions.features.card import loader as card_loader
    from z30_extensions.features.experimental import linuxboot as lbm
    from menu_path import enter_parent, wait_built
    patch = card.parse((a.card / 'WL.PATCH').read_bytes())
    report = json.loads((a.card / 'report.json').read_text(encoding='utf-8'))
    menu, lf = report['features']['menu_framework'], report['features']['linux']
    kernel = (a.card / 'kit' / 'Z30' / 'LINUX' / 'ZIMAGE').read_bytes()
    dtb = (a.card / 'kit' / 'Z30' / 'LINUX' / 'Z30.DTB').read_bytes()
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
    def press(page, ident, row):
        # An action row: select it, then exactly one touch (activate() repeats while the page stays).
        wait_built(s, page, row)
        for _ in range(20):
            s.input(0x1045d54c, [page, ident]); s.run_for(.5)
            if sum(struct.unpack('<2H', s.remote.memory(page + 22, 4))) == row: break
        packet(1, 250, 86 + row * 52)
        packet(0, 250, 86 + row * 52)
    def shown(slot):
        length, ptr, _ = struct.unpack('<3I', s.remote.memory(0xc2013368 + slot * 12, 12))
        return s.remote.memory(ptr, length).decode('ascii', 'replace') if length else ''
    def state():
        return dict(zip(FIELDS, struct.unpack(f'<{len(FIELDS)}I', s.remote.memory(lstate, 4 * len(FIELDS)))))
    try:
        raw = int(s.input(0x10211f4c, [len(patch.tail) + 0x10000, 0])['registers']['r0'], 16)
        assert raw, 'pool allocation'
        base = (raw + 0xffff) & ~0xffff
        delta = base - patch.link_base
        write(base, card.relocate(patch.tail, patch.relocs, delta))
        for edit in patch.edits:
            if s.remote.memory(edit.address, len(edit.expected)) != edit.expected:
                assert edit.address == card_loader.IMU_SITE, (edit.name, hex(edit.address))
            write(edit.address, card.relocate(edit.replacement, edit.relocs, delta))
        runtime = lambda linked: (linked & ~1) - card.CHECK_BASES[-1] + base
        lstate = runtime(int(lf['payload'], 16)) + lf['state_offset']
        s.input(0x102355ee, [0xc182e688, 1]); s.run_for(2)
        s.input(0x10465990, [0x1400, 3]); s.input(0x10452cc0, [1])          # English
        s.input(0x1045f732, [2, 397]); s.run_for(.5)
        s.input(runtime(int(menu['functions']['open'], 16)), []); s.run_for(1)
        st = word(int(menu['state_anchor'], 16))
        assert st, 'extension state'
        page = st + lf['page_offset']
        parent, control, row = enter_parent(menu, 'linux', st, activate, s)
        activate(parent, control, row)
        assert current() == page, 'Linux page opened'
        wait_built(s, page, lbm.ROWS - 1)
        results['rows'] = [shown(lbm.TITLE_SLOT)] + [shown(x) for x in lbm.LABEL_SLOTS] + [shown(lbm.STATUS_SLOT)]
        print('page', results['rows'], flush=True)
        # Inject the kit (no SD card in this QEMU session).
        def pool(n):
            p = int(s.input(0x10211f4c, [n + 64, 0])['registers']['r0'], 16)
            assert p, 'pool'
            return p
        kp, dp = pool(len(kernel)), pool(len(dtb))
        write(kp, kernel); write(dp, dtb)
        write(lstate + 3 * 4, struct.pack('<7I', kp, dp, 0, len(kernel), len(dtb), 0, 1))
        write(lstate + FIELDS.index('skip_wdt') * 4, struct.pack('<I', 1))     # z30-qemu has no SP805 model
        # Dry run.
        press(page, lf['controls'][1], 1); s.run_for(2)
        dry = state(); results['dry'] = {k: hex(v) for k, v in dry.items()}
        results['dry_status'] = shown(lbm.STATUS_SLOT)
        print('dry run', results['dry_status'], results['dry'], flush=True)
        assert dry['phase'] == 2, ('dry run failed', dry)
        assert dry['patched'] & 0b11111111 == 0b11111111, hex(dry['patched'])
        assert dry['cntfrq'] == 48_000_000, dry['cntfrq']
        s.frame('01-dry-run')
        # Boot.
        # The boot never returns, so the release packet may not get through: send the press only.
        wait_built(s, page, 0)
        for _ in range(20):
            s.input(0x1045d54c, [page, lf['controls'][0]]); s.run_for(.5)
            if sum(struct.unpack('<2H', s.remote.memory(page + 22, 4))) == 0: break
        packet(1, 250, 86)
        try:
            packet(0, 250, 86)
        except RuntimeError:
            pass
        if a.system_map:
            linux_boot(s, a, results, state, png_from_ppm)
            return
        s.run_for(3)
        sig = struct.unpack('<10I', s.remote.memory(SIGNATURE, 40))
        s.run_for(2)
        sig2 = struct.unpack('<10I', s.remote.memory(SIGNATURE, 40))
        results['signature'] = [hex(x) for x in sig2]
        print('signature', results['signature'], flush=True)
        assert sig2[0] == 0x4c30335a and sig2[1] == 0x58554e49, 'test kernel signature'
        assert sig2[2] == 0x600df00d, ('test kernel did not find the overlay', hex(sig2[2]))
        assert sig2[9] > sig[9], 'test kernel loop running'
        # Core 1: its PC must be in the parking loop.
        threads = []
        for t in ('1', '2'):
            if s.remote.command('Hg' + t) == 'OK':
                threads.append((t, s.remote.registers()['r15']))
        results['threads'] = threads
        park_lo = runtime(int(lf['payload'], 16))
        print('threads', threads, flush=True)
        # The painted overlay plane.
        w, h = sig2[7] & 0xffff, sig2[7] >> 16
        b = state()
        color = s.remote.memory(b['color'], b['cstride'] * h)
        alpha = s.remote.memory(b['alpha'], b['astride'] * h)
        (s.out / 'overlay.png').write_bytes(overlay_png(color, alpha, w, h, b['cstride'], b['astride'], png_from_ppm))
        results['overlay'] = dict(width=w, height=h, color=hex(b['color']), alpha=hex(b['alpha']))
        s.audit.append(dict(linux=results, physical_verified=False))
        s.write_audit()
        print('PASS Linux handoff:', json.dumps(results), flush=True)
    except Exception:
        if 'lstate' in locals():
            print('state', {k: hex(v) for k, v in state().items()}, flush=True)
            if 'dp' in locals():
                mem = s.remote.memory(dp, len(dtb))
                print('dtb in guest matches file:', mem == dtb, 'first bytes', mem[:16].hex(),
                      'placeholders in guest', mem.count(bytes.fromhex('5a3000')), 'in file', dtb.count(bytes.fromhex('5a3000')), flush=True)
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
    p.add_argument('--kernel', type=Path, help='--prepare: a zImage instead of the test kernel')
    p.add_argument('--system-map', type=Path, help="run: the zImage's System.map (real kernel mode)")
    p.add_argument('--seconds', type=int, default=60)
    p.add_argument('--min-seconds', type=int, default=8)
    p.add_argument('--earlycon-only', action='store_true', help='--prepare: kit without the framebuffer driver')
    p.add_argument('--expect', action='append', default=[], help='run: extra text the kernel log must contain')
    a = p.parse_args()
    prepare(a) if a.prepare else run(a)


if __name__ == '__main__':
    main()
