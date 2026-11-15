"""QEMU-only, stock firmware: open the i menu from photo live view and record how it is drawn.

Touches the on-screen i button (landscape 660..718, 128..176) with the stock
touch entry, records every renderer command while the menu builds (box, colour,
target, caller), and diffs UI RAM (C1800000..C1840000) before and after so the
state that tells the i menu from live view can be found. Then steps the cursor
with the stock right and down key events and diffs again, to find the cursor
slot. Read-only guest apart from the inputs.
"""
import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_portrait_draws_qemu import SITES, TARGET, summary  # noqa: E402

RAM, RAM_SIZE = 0xC1800000, 0x40000
EVENTS = dict(up=0x253, down=0x254, left=0x257, right=0x258, ok=0x27e, back=0x255)
CONTEXT = dict(view_kind=(0xC18292AC, 1), phase=(0xC181F6FC, 1), backend=(0xC181B278, 4),
               busy_a=(0xC2016904, 4), busy_b=(0xC20168F8, 4), target=(TARGET, 1))
DRAW_SITES = {k: v for k, v in SITES.items() if v not in ('vgDrawImage', 'vgDrawPath', 'dispatch')}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--seconds', type=float, default=3.0)
    p.add_argument('--cap', type=int, default=3000)
    p.add_argument('--events', help='comma-separated key events to try one by one instead of the touch')
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session
    from liveview_session import start, DEVICE_ENV

    records = []
    state = {'armed': False, 'phase': ''}

    class ObservedSession(Session):
        def run_for(self, seconds):
            if not state['armed'] or len(records) >= a.cap:
                super().run_for(seconds)
                return
            remote = self.remote
            deadline = time.monotonic() + seconds
            for address in DRAW_SITES:
                assert remote.command('Z1,{:x},2'.format(address)) == 'OK'
            try:
                while time.monotonic() < deadline and len(records) < a.cap:
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
                    for address in DRAW_SITES:
                        assert remote.command('z1,{:x},2'.format(address)) == 'OK'
                    if pc in DRAW_SITES:
                        site = DRAW_SITES[pc]
                        rec = dict(n=len(records), step=state['phase'], site=site, lr=hex(int(regs['r14'], 16)),
                                   target=remote.memory(TARGET, 1)[0],
                                   view=remote.memory(0xC18292AC, 1)[0], **summary(remote, site, regs))
                        if site in ('rect', 'clear', 'rect_blend'):
                            rec['rgba'] = rec.get('rgba')
                        records.append(rec)
                    assert remote.command('Hc' + thread) == 'OK'
                    remote.send('s')
                    remote.sock.settimeout(5)
                    self.packet = remote.receive()
                    assert remote.command('Hc-1') == 'OK'
                    for address in DRAW_SITES:
                        assert remote.command('Z1,{:x},2'.format(address)) == 'OK'
            finally:
                for address in DRAW_SITES:
                    remote.command('z1,{:x},2'.format(address))

    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    s = ObservedSession(str(out), device_env=DEVICE_ENV)

    def context():
        return {k: int.from_bytes(s.remote.memory(addr, size), 'little') for k, (addr, size) in CONTEXT.items()}

    def snapshot():
        return b''.join(s.remote.memory(RAM + o, 0x1000) for o in range(0, RAM_SIZE, 0x1000))

    def diff(old, new, limit=400):
        changes = []
        for o in range(0, RAM_SIZE, 4):
            if old[o:o + 4] != new[o:o + 4]:
                changes.append((hex(RAM + o), old[o:o + 4].hex(), new[o:o + 4].hex()))
                if len(changes) >= limit:
                    break
        return changes

    def touch(x, y):
        for contact in (1, 0):
            packet = bytearray(60)
            struct.pack_into('<I', packet, 0, 1)
            packet[8], packet[9] = contact, 1
            struct.pack_into('<2H', packet, 0x12, x, y)
            s.input(0x103ce7e6, [('scratch', 0)], payload=packet)
            s.run_for(.3)

    result = dict(probe=__doc__.splitlines()[0], steps=[])
    try:
        start(s)
        s.run_for(1.0)
        s.frame('00-live')
        live_ctx, live_ram = context(), snapshot()
        # Settle twice to see what changes on its own (counters, timers), so they can be told apart.
        s.run_for(1.0)
        idle_ram = snapshot()
        idle = {c[0] for c in diff(live_ram, idle_ram, 4000)}
        if a.events:   # which hardware key event opens the i menu: count the draws after each
            tries = []
            for ev in (int(v, 16) for v in a.events.split(',')):
                start_n = len(records)
                state['armed'], state['phase'] = True, hex(ev)
                try:
                    s.input(0x103ce96e, [ev], seconds=15)
                    s.run_for(1.5)
                except RuntimeError as error:
                    tries.append(dict(event=hex(ev), error=str(error)[:200])); break
                state['armed'] = False
                mine = records[start_n:]
                kinds = {}
                for rec in mine:
                    if not (rec['site'] == 'clear' and rec['lr'] == '0x103bc19b'):
                        kinds[rec['site']] = kinds.get(rec['site'], 0) + 1
                tries.append(dict(event=hex(ev), draws=len(mine), non_background=kinds, context=context()))
                s.frame(f'event-{ev:x}')
                print(tries[-1], flush=True)
            result['events'] = tries; result['records'] = records
            (out / 'imenu-events.json').write_text(json.dumps(result, indent=1) + '\n')
            return
        state['armed'], state['phase'] = True, 'open'
        touch(689, 152)
        s.run_for(a.seconds)
        state['armed'] = False
        s.frame('01-imenu')
        open_ctx, open_ram = context(), snapshot()
        result['steps'].append(dict(name='open', live=live_ctx, after=open_ctx,
                                    changed=[c for c in diff(live_ram, open_ram, 4000) if c[0] not in idle][:400]))
        for name in ('right', 'down'):
            before = snapshot()
            state['armed'], state['phase'] = True, name
            s.input(0x103ce96e, [EVENTS[name]], seconds=15)
            s.run_for(1.5)
            state['armed'] = False
            s.frame(f'02-{name}')
            after = snapshot()
            result['steps'].append(dict(name=name, after=context(),
                                        changed=[c for c in diff(before, after, 4000) if c[0] not in idle][:400]))
        result['records'] = records
        (out / 'imenu.json').write_text(json.dumps(result, indent=1) + '\n')
        for step in result['steps']:
            print(step['name'], step.get('after'), len(step['changed']), 'words changed')
        print(len(records), 'draw records')
    finally:
        s.close()


if __name__ == '__main__':
    main()
