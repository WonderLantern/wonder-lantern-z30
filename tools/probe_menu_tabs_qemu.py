"""QEMU-only observer: what the stock menu tab bar writes when tabs change.

Boots stock firmware (or, with --image/--report, a build overlaid on it),
opens the setup menu, touches each tab and then sends the tab bar's key
events (0x24E, 0x24F, 0x250). After every step it records calls of the
property setter 0x10465990 (key, value), the active page, the tab container's
cursor (+22 / +24 of page 1 at 0xC1832D50) and its current child (+8).

Questions it answers: does the camera persist the last menu tab, and where
(stock); does the menu_tab prototype draw eight tabs, open the extension root
from the eighth and store a stock tab on exit (--image). See
docs/menu-tab-research.md.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

TAB = 0xC1832D50
TAB_X = 32
SETTER = 0x10465990
NEVER = 0x100200AC


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-root', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--image', type=Path)
    p.add_argument('--report', type=Path)
    p.add_argument('--language', choices=('en', 'zh-Hans', 'zh-Hant'), default='en')
    p.add_argument('--late-meta', action='store_true',
                   help='write the scatter (RAM) edits only after the menu is open, as a card loader '
                        'running while the menu is shown would; then close and reopen the menu')
    a = p.parse_args()
    sys.path.insert(0, str(a.qemu_root.resolve() / 'tools'))
    from display_session import Session, png_from_ppm

    report = json.loads(a.report.read_text(encoding='utf-8')) if a.report else None
    tabs = 8 if report and 'menu_tab' in report['features'] else 7
    pitch = report['features']['menu_tab']['row_height'] if tabs == 8 else 60
    s = Session(str(a.output_dir.resolve()))
    steps = []
    try:
        def half(address):
            return int.from_bytes(s.remote.memory(address, 2), 'little')

        def word(address):
            return int.from_bytes(s.remote.memory(address, 4), 'little')

        def write(address, data):
            for off in range(0, len(data), 1024):
                v = data[off:off + 1024]
                assert s.remote.command(f'M{address + off:x},{len(v):x}:' + v.hex()) == 'OK'

        def active():
            return int(s.input(0x1046109a, [])['registers']['r0'], 16)

        def observe(seconds, name, **extra):
            calls = []
            try:
                s.run_to(NEVER, seconds=seconds, observe=[SETTER],
                         capture=lambda remote, regs: {'key': regs['r0'], 'value': regs['r1'], 'lr': regs['r14']})
            except RuntimeError:
                pass
            for record in reversed(s.audit):
                if 'observations' in record:
                    calls = [dict(key=o['key'], value=o['value'], lr=o['lr']) for o in record['observations']]
                    break
            state = dict(step=name, active=hex(active()), tab_cursor=half(TAB + 22), tab_top=half(TAB + 24),
                         tab_child=hex(word(TAB + 8)), setter_calls=calls, **extra)
            raw = (s.graphics / 'gui-latest.ppm').read_bytes()
            (s.out / (name + '.png')).write_bytes(png_from_ppm(raw))
            steps.append(state)
            print(json.dumps(state), flush=True)
            return state

        def touch(x, y):
            for contact in (1, 0):
                packet = bytearray(60)
                struct.pack_into('<I', packet, 0, 1)
                packet[8], packet[9] = contact, 1
                struct.pack_into('<2H', packet, 0x12, x, y)
                s.input(0x103ce7e6, [('scratch', 0)], payload=packet)
                s.run_for(.3)

        scatter = []
        if a.image:
            image = a.image.read_bytes()
            for e in report['edits']:
                address = 0x10020000 + int(e['offset'], 16)
                old, new = bytes.fromhex(e['expected_hex']), bytes.fromhex(e['replacement_hex'])
                assert s.remote.memory(address, len(old)) == old
                in_scatter = 0x11f81c94 <= address and address + len(old) <= 0x12079eec
                if in_scatter:
                    scatter.append((0xc1800000 + address - 0x11f81c94, new))
                if not (in_scatter and a.late_meta):
                    write(address, new)
            size = (a.qemu_root / '.local/firmware/eg2090_0120a0.bi').stat().st_size
            write(0x10020000 + size - 2, image[size - 2:])
        s.input(0x102355ee, [0xc182e688, 1])
        s.run_for(2)
        if not a.late_meta:
            for address, new in scatter:
                write(address, new)
        lang, font = {'en': (3, 1), 'zh-Hans': (26, 8), 'zh-Hant': (27, 9)}[a.language]
        s.input(0x10465990, [0x1400, lang])
        s.input(0x10452cc0, [font])
        s.input(0x1045f732, [2, 397])
        s.run_for(1)
        observe(1.5, '00-setup', tab_rows=hex(word(0xC1872230 + 20)))
        if a.late_meta:
            # The menu is open with the stock seven tabs; the card loader changes them now.
            for address, new in scatter:
                write(address, new)
            s.run_for(1)
            observe(1, '01-late-meta', tab_rows=hex(word(0xC1872230 + 20)))
            for tab in (6, 0, 4):
                touch(TAB_X, 60 * tab + 30)
                observe(1, f'02-late-touch-{tab}')
            for name in ('03-late-down-a', '03-late-down-b', '03-late-down-c', '03-late-down-d'):
                s.input(0x103ce96e, [0x254], seconds=10)
                s.run_for(.7)
                observe(1, name)
            s.input(0x103ce96e, [0x24E], seconds=10)
            observe(1.5, '04-late-closed')
            # Reopen through the menu-open path (MENU), which realizes the tab bar again.
            s.input(0x102355ee, [0xc182e688, 1])
            s.run_for(2)
            observe(1.5, '05-late-reopened', tab_rows=hex(word(0xC1872230 + 20)))
            s.input(0x1045f732, [2, 397])
            s.run_for(1)
        for tab in range(tabs):
            touch(TAB_X, pitch * tab + pitch // 2)
            observe(1.5, f'10-tab-{tab}')
        if tabs == 8:
            framework = report['features']['menu_framework']
            state = word(int(framework['state_anchor'], 16))
            root = state + framework['page_offsets']['955'] if state else 0
            observe(1, '30-extension-tab', state=hex(state), root=hex(root),
                    root_is_child=word(TAB + 8) == root and root != 0)
            # Into a feature page from the root (row 0): the first touch moves the
            # focus from the tab bar into the list, a later one opens the row.
            for press in range(3):
                touch(250, 86)
                if active() not in (TAB, root):
                    break
            page = active()
            observe(1, '31-root-row-0', presses=press + 1, page_opened=page not in (TAB, root))
            for press in range(4):
                touch(675, 28)
                if active() != page:
                    break
            observe(1, '32-back', presses=press + 1, back_at_root=active() == root,
                    root_still_tab_child=word(TAB + 8) == root)
            # Keys on a focused tab: right (0x256), OK (0x25B) and the other enter
            # events open the tab's page; the setup tab is the stock reference.
            for tab, event in ((4, 0x256), (4, 0x25B), (7, 0x256), (7, 0x25B), (7, 0x26F), (7, 0x27E)):
                for _ in range(3):                      # left (0x255) back to the tab bar
                    if active() == TAB:
                        break
                    s.input(0x103ce96e, [0x255], seconds=10)
                    s.run_for(.7)
                touch(TAB_X, pitch * tab + pitch // 2)
                s.run_for(.5)
                focused = active() == TAB
                try:
                    s.input(0x103ce96e, [event], seconds=10)
                except RuntimeError as error:
                    print('event stopped', hex(event), str(error)[:80], flush=True)
                s.run_for(.7)
                observe(1, f'38-key-{tab}-{event:x}', tab_focused_before=focused,
                        opened_root=active() == root, setup_opened=active() == 0xC1837160)
            # Down from the seventh tab reaches the eighth; down again must stay in range.
            touch(TAB_X, pitch * 6 + pitch // 2)
            for name in ('33-down-a', '34-down-b', '35-down-c', '36-down-d'):
                s.input(0x103ce96e, [0x254], seconds=10)
                s.run_for(.7)
                observe(1, name)
            s.input(0x103ce96e, [0x253], seconds=10)
            s.run_for(.7)
            observe(1, '37-up')
        for event in (0x24E, 0x24F, 0x250):
            try:
                s.input(0x103ce96e, [event], seconds=10)
            except RuntimeError as error:
                steps.append(dict(step=f'event-{event:x}', error=str(error)[:200]))
            observe(1.5, f'20-event-{event:x}')
        s.audit.append(dict(menu_tab_steps=steps))
        s.write_audit()
        print('DONE', len(steps), flush=True)
    finally:
        s.close()


if __name__ == '__main__':
    main()
