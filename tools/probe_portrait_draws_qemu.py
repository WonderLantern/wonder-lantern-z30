"""QEMU-only, stock firmware: which entries draw the photo live-view widgets.

Breaks on the renderer entries the portrait hooks use, on the unhooked path
entries and on vgDrawImage / vgDrawPath, during one stock UI refresh. Records
the caller, the current render target and the box each command carries, so a
widget that does not move in portrait can be traced to the path it uses.
Read-only guest.
"""
import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path

SITES = {
    0x1047743C: 'rect', 0x1047797E: 'clear', 0x104777FC: 'frame', 0x10477C9C: 'image',
    0x104787F2: 'image2', 0x10479022: 'image3', 0x104775C4: 'path_grad', 0x10477B1E: 'path_ellipse',
    0x104794EE: 'path_line', 0x104773C6: 'rect_blend', 0x1054E652: 'text_matrix',
    0x1050B5F0: 'vgDrawImage', 0x1050AE18: 'vgDrawPath', 0x103BBFAC: 'dispatch',
}
TARGET = 0xC1808758
CAP = 600


def summary(remote, site, regs):
    r0 = int(regs['r0'], 16)
    try:
        if site in ('rect', 'clear', 'rect_blend'):
            x, y, w, h, c = struct.unpack('<4iI', remote.memory(r0, 20))
            return dict(x=x, y_down=480 - y - h, w=w, h=h, rgba=hex(c))
        if site == 'frame':
            x, y, w, h = (int(regs[f'r{i}'], 16) for i in range(4))
            return dict(x=x, y_down=480 - y - h, w=w, h=h)
        if site in ('image', 'image2'):
            d = remote.memory(r0, 0x44)
            mode, = struct.unpack_from('<I', d, 0)
            src = struct.unpack_from('<4h', d, 0x10)
            box = struct.unpack_from('<4h', d, 0x18)
            return dict(mode=mode, src=src, box=box, angle=struct.unpack_from('<H', d, 0x40)[0])
        if site == 'image3':
            d = remote.memory(r0, 0x28)
            return dict(box=[v >> 16 for v in struct.unpack_from('<4i', d, 0x18)])
        if site == 'text_matrix':
            m = struct.unpack('<9f', remote.memory(r0, 36))
            return dict(sx=round(m[0], 2), sy=round(m[4], 2), tx=round(m[6], 1), ty=round(m[7], 1))
        if site == 'dispatch':
            return dict(command=hex(struct.unpack('<I', remote.memory(r0 + 0x10, 4))[0]))
    except (OSError, ValueError, struct.error):
        return dict(unreadable=True)
    return {}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=2.0)
    p.add_argument('--sites', help='comma-separated hex entries to break on instead of the draw entries')
    p.add_argument('--no-refresh', action='store_true', help='observe without the forced UI refresh')
    a = p.parse_args()
    if a.sites:
        SITES.clear()
        SITES.update({int(v, 16): v for v in a.sites.split(',')})
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, refresh, DEVICE_ENV

    records = []
    state = {'armed': False}

    class ObservedSession(Session):
        def run_for(self, seconds):
            if not state['armed'] or len(records) >= CAP:
                super().run_for(seconds)
                return
            remote = self.remote
            deadline = time.monotonic() + seconds
            for address in SITES:
                assert remote.command('Z1,{:x},2'.format(address)) == 'OK'
            try:
                while time.monotonic() < deadline and len(records) < CAP:
                    remote.send('c')
                    remote.sock.settimeout(max(.01, deadline - time.monotonic()))
                    try:
                        packet = remote.receive()
                    except socket.timeout:
                        remote.sock.sendall(b'\x03')
                        remote.sock.settimeout(5)
                        self.packet = remote.receive()
                        break
                    self.packet = packet
                    thread = packet.split('thread:')[1].split(';')[0]
                    assert remote.command('Hg' + thread) == 'OK'
                    regs = remote.registers()
                    pc = int(regs['r15'], 16)
                    for address in SITES:
                        assert remote.command('z1,{:x},2'.format(address)) == 'OK'
                    if pc in SITES:
                        site = SITES[pc]
                        records.append(dict(n=len(records), site=site, thread=thread,
                                            lr=hex(int(regs['r14'], 16)), target=remote.memory(TARGET, 1)[0],
                                            **summary(remote, site, regs)))
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    for address in SITES:
                        assert remote.command('Z1,{:x},2'.format(address)) == 'OK'
            finally:
                for address in SITES:
                    remote.command('z1,{:x},2'.format(address))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        s.run_for(0.5)
        state['armed'] = True
        if not a.no_refresh:
            refresh(s)
        s.run_for(a.seconds)
        s.frame('observed')
        by = {}
        for rec in records:
            key = (rec['site'], rec['lr'], rec['target'])
            by[key] = by.get(key, 0) + 1
        for key in sorted(by, key=lambda k: -by[k]):
            print(by[key], key)
        (out / 'draws.json').write_text(json.dumps(dict(probe=__doc__.splitlines()[0], records=records), indent=1) + '\n')
    finally:
        s.close()


if __name__ == '__main__':
    main()
