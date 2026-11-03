"""QEMU-only observer: how the stock UI turns a CJK code point into a drawn glyph.

Boots stock firmware in Simplified Chinese, opens the setup menu and records:
reads of the Hans font 2 character map (read watchpoint), calls of the font
getter 0x104ED66E, and the OpenVG draw entry points. Read-only guest.
"""
import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path

HANS_FONT = 0x107D06B3
HANS_CMAP, HANS_CMAP_BYTES = 0x107D0705, 766 * 6
BREAKS = {0x104ED66E: 'font-get', 0x1050AE18: 'vgDrawPath', 0x1050B5F0: 'vgDrawImage'}
CAPS = {'cmap-read': 60, 'font-get': 40, 'vgDrawPath': 40, 'vgDrawImage': 40}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=20)
    p.add_argument('--watch-start', type=lambda x: int(x, 0), default=HANS_CMAP)
    p.add_argument('--watch-bytes', type=lambda x: int(x, 0), default=HANS_CMAP_BYTES)
    p.add_argument('--watch-cap', type=int, default=CAPS['cmap-read'])
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session

    CAPS['cmap-read'] = a.watch_cap
    s = Session(str(a.output_dir.resolve()))
    records, counts = [], {k: 0 for k in CAPS}
    try:
        s.input(0x102355ee, [0xc182e688, 1])
        s.run_for(2)
        s.input(0x10465990, [0x1400, 26])
        s.input(0x10452cc0, [8])
        s.frame('01-stock-hans')
        r = s.remote
        armed = []
        def arm(on):
            if not on:
                for op in armed:
                    r.command('z' + op[1:])
                armed.clear()
                return
            ops = []
            if counts['cmap-read'] < CAPS['cmap-read']:
                ops.append(f'Z3,{a.watch_start:x},{a.watch_bytes:x}')
            for address, name in BREAKS.items():
                if counts[name] < CAPS[name]:
                    ops.append(f'Z1,{address:x},2')
            for op in ops:
                reply = r.command(op)
                if reply != 'OK':
                    raise RuntimeError(f'{op} -> {reply!r}')
                armed.append(op)
        def observe(thread, packet):
            assert r.command('Hg' + thread) == 'OK'
            regs = r.registers()
            pc = int(regs['r15'], 16)
            if 'watch' in packet:
                kind = 'cmap-read'
                field = [x for x in packet.split(';') if 'watch' in x][0]
                item = dict(kind=kind, data=hex(int(field.split(':')[1], 16)))
            elif pc in BREAKS:
                kind = BREAKS[pc]; item = dict(kind=kind)
            else:
                return False
            counts[kind] += 1
            item.update(pc=hex(pc), registers=regs, stack=r.memory(int(regs['r13'], 16), 64).hex())
            if kind in ('vgDrawPath', 'vgDrawImage'):
                try: item['object'] = r.memory(int(regs['r0'], 16), 64).hex()
                except ValueError: item['object'] = None
            records.append(item)
            arm(False)
            assert r.command('Hc' + thread) == 'OK'
            r.send('s'); r.sock.settimeout(5); r.receive()
            assert r.command('Hc-1') == 'OK'
            arm(True)
            return True
        # Open the setup menu as a stock call with observers armed: the page
        # text is drawn synchronously inside this call.
        s.run_to(0x100531bc)
        thread = s.packet.split('thread:')[1].split(';')[0]
        assert r.command('Hg' + thread) == 'OK'
        saved = r.command('g')
        regs = bytearray.fromhex(saved)
        sentinel = 0x100200ac
        struct.pack_into('<2I', regs, 0, 2, 397)
        struct.pack_into('<I', regs, 14 * 4, sentinel)
        struct.pack_into('<I', regs, 15 * 4, 0x1045f732)
        struct.pack_into('<I', regs, 64, struct.unpack_from('<I', regs, 64)[0] | 0x20)
        assert r.command(f'Z1,{sentinel:x},4') == 'OK'
        assert r.command('G' + regs.hex()) == 'OK'
        arm(True)
        deadline = time.monotonic() + a.seconds
        returned = False
        while time.monotonic() < deadline:
            r.send('c')
            r.sock.settimeout(max(.01, deadline - time.monotonic()))
            try:
                packet = r.receive()
            except socket.timeout:
                r.sock.sendall(b''); r.sock.settimeout(5); s.packet = r.receive()
                break
            s.packet = packet
            thread = packet.split('thread:')[1].split(';')[0]
            assert r.command('Hg' + thread) == 'OK'
            if int(r.registers()['r15'], 16) == sentinel:
                returned = True
                break
            if not observe(thread, packet):
                records.append(dict(kind='unexpected', packet=packet)); break
        arm(False)
        r.command(f'z1,{sentinel:x},4')
        if returned:
            assert r.command('G' + saved) == 'OK'
        s.audit.append(dict(menu_call_returned=returned))
        s.run_for(1)
        s.frame('02-setup-hans')
        s.audit.append(dict(font_glyph_probe=records, counts=counts))
        s.write_audit()
        print(json.dumps(counts))
    finally:
        s.close()


if __name__ == '__main__':
    main()
