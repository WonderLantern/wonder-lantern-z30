"""QEMU-only, stock firmware: the image renderer's descriptors and call rate in live view.

Boots the stock photo live view (z30-qemu liveview_session) and, without forcing
a UI refresh, breaks on the image renderer 0x10477C9C (the call the battery
hook and the ready banner wrap at 0x103BC4D0) for a few seconds. Records each
call's time, caller, render target and the whole 0x5C-byte descriptor, then
summarises the calls per second and one full descriptor per image class (+0x04),
so a banner can fill its own descriptor the way stock does. Read-only guest.
"""
import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path

RENDERER = 0x10477C9C
TARGET = 0xC1808758
CAP = 400


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=4.0)
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, DEVICE_ENV

    records = []
    state = {'armed': False, 'start': 0.0}

    class ObservedSession(Session):
        def run_for(self, seconds):
            if not state['armed'] or len(records) >= CAP:
                super().run_for(seconds)
                return
            remote = self.remote
            deadline = time.monotonic() + seconds
            assert remote.command(f'Z1,{RENDERER:x},2') == 'OK'
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
                    assert remote.command(f'z1,{RENDERER:x},2') == 'OK'
                    if int(regs['r15'], 16) == RENDERER:
                        r0 = int(regs['r0'], 16)
                        d = remote.memory(r0, 0x5C)
                        records.append(dict(t=round(time.monotonic() - state['start'], 3), lr=hex(int(regs['r14'], 16)),
                                            target=remote.memory(TARGET, 1)[0], descriptor=d.hex()))
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    assert remote.command(f'Z1,{RENDERER:x},2') == 'OK'
            finally:
                remote.command(f'z1,{RENDERER:x},2')

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)
    try:
        start(s)
        s.run_for(1.0)
        s.frame('liveview')
        state['armed'], state['start'] = True, time.monotonic()
        s.run_for(a.seconds)
        classes = {}
        for rec in records:
            d = bytes.fromhex(rec['descriptor'])
            words = struct.unpack('<23I', d)
            fields = dict(type=words[0], cls=words[1], data=hex(words[2]), w0c=hex(words[3]),
                          src=struct.unpack_from('<4h', d, 0x10), box=struct.unpack_from('<4h', d, 0x18),
                          alpha=d[0x20], w24=hex(words[9]), w28=hex(words[10]), w2c=hex(words[11]), w30=hex(words[12]),
                          w34=hex(words[13]), w38=hex(words[14]), angle=struct.unpack_from('<h', d, 0x40)[0],
                          b44=d[0x44], b45=d[0x45], w48=hex(words[18]), w4c=words[19], w50=words[20],
                          w54=hex(words[21]), w58=hex(words[22]))
            rec['fields'] = fields
            classes.setdefault(words[1], dict(count=0, first=rec))['count'] += 1
        span = records[-1]['t'] - records[0]['t'] if len(records) > 1 else 0
        summary = dict(calls=len(records), seconds_observed=a.seconds, span=span,
                       calls_per_second=round(len(records) / a.seconds, 1),
                       callers=sorted({r['lr'] for r in records}), targets=sorted({r['target'] for r in records}),
                       classes={str(k): dict(count=v['count'], example=v['first']['fields'])
                                for k, v in sorted(classes.items())},
                       times=[r['t'] for r in records[:60]])
        s.audit.append(dict(image_descriptors=summary, records=records))
        s.write_audit()
        print(json.dumps(summary, indent=1))
    finally:
        s.close()


if __name__ == '__main__':
    main()
