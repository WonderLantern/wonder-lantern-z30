#!/usr/bin/env python3
"""Probe the lifetime of the extension settings block across a menu session.

Replays a rebuilt image into the stock-gated QEMU machine, opens the extension
menu (which allocates the persistent RAM block and anchors it at
``STATE_ANCHOR+16``), records the block pointer and MAGIC, leaves the menu back
to the live view, then re-reads both.  This tells us whether the block is freed
or merely un-anchored, which decides where dynamic soft-mask state can live.

QEMU display model only; not a camera test.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / 'z30-qemu' / 'tools'))
sys.path.insert(0, str(ROOT / 'src'))

from display_session import Session  # noqa: E402
from liveview_session import DEVICE_ENV, refresh  # noqa: E402

BYTE_OFFSET_BASE = 0x10020000
BLOCK_SLOT = 0xC1832D34
LIVE_SLOTS = (0xC1832D50, 0xC1837160)
SCATTER_FIRST = 0x11F81C94
SCATTER_LAST = 0x12079EEC
SCATTER_RAM = 0xC1800000


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', default='build/research/mask-build/aspect-mask-v26-dynamic/EG209020.BIN')
    p.add_argument('--report', default='build/research/mask-build/aspect-mask-v26-dynamic/EG209020.json')
    p.add_argument('--qemu-root', default='../z30-qemu')
    p.add_argument('--output-dir', default='build/research/state-lifetime')
    a = p.parse_args()
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    report = json.loads(Path(a.report).read_text(encoding='utf-8'))
    image = Path(a.image).read_bytes()
    dialog = report['features']['menu_framework']
    soft = report['features']['aspect_profiles']['soft_mask']
    mask_offset = int(soft['state'].rsplit('+', 1)[1], 16)
    stock = Path(a.qemu_root) / '.local/firmware/eg2090_0120a0.bi'
    stock_size = stock.stat().st_size

    s = Session('state-lifetime', device_env=DEVICE_ENV)
    remote = s.remote
    entries = []

    def word(address):
        return int.from_bytes(remote.memory(address, 4), 'little')

    def write(address, data):
        for offset in range(0, len(data), 1024):
            chunk = data[offset:offset + 1024]
            assert remote.command(f'M{address + offset:x},{len(chunk):x}:' + chunk.hex()) == 'OK'
        assert remote.memory(address, len(data)) == data

    def touch(x, y):
        for contact in (1, 0):
            packet = bytearray(60)
            struct.pack_into('<I', packet, 0, 1)
            packet[8], packet[9] = contact, 1
            struct.pack_into('<2H', packet, 0x12, x, y)
            s.input(0x103CE7E6, [('scratch', 0)], payload=packet)
            s.run_for(0.5)

    def mask_state(block):
        if not block:
            return None
        blob = remote.memory(block + mask_offset, 0x20)
        flags, image_address, alpha, count = struct.unpack('<4I', blob[:16])
        return {'flags': flags, 'image': hex(image_address), 'alpha': alpha,
                'count': count, 'bytes': blob.hex()}

    def stage(name):
        pointer = word(BLOCK_SLOT)
        entries.append({'stage': name, 'slot': hex(pointer) if pointer else None,
                        'magic': hex(word(pointer + 56)) if pointer else None,
                        'alloc_url': hex(word(pointer + 60)) if pointer else None,
                        'mask': mask_state(pointer)})

    try:
        scatter = []
        for edit in report['edits']:
            address = BYTE_OFFSET_BASE + int(edit['offset'], 16)
            old = bytes.fromhex(edit['expected_hex'])
            new = bytes.fromhex(edit['replacement_hex'])
            assert remote.memory(address, len(old)) == old, hex(address)
            write(address, new)
            if SCATTER_FIRST <= address and address + len(old) <= SCATTER_LAST:
                scatter.append((SCATTER_RAM + address - SCATTER_FIRST, new))
        write(BYTE_OFFSET_BASE + stock_size - 2, image[stock_size - 2:])
        s.input(0x102355EE, [0xC182E688, 1])
        s.run_for(2)
        for address, new in scatter:
            write(address, new)
        s.input(0x10465990, [0x1400, 3])
        s.input(0x10452CC0, [1])
        s.input(0x1045F732, [2, 397])
        s.run_for(0.5)
        refresh(s)
        s.run_for(0.4)
        stage('live-before-menu')
        s.input(int(dialog['functions']['open'], 16) & ~1, [])
        s.run_for(1)
        stage('menu-open')
        refresh(s)
        s.run_for(0.4)
        s.frame('01-menu-open')
        touch(675, 28)
        s.run_for(0.6)
        stage('after-first-back')
        touch(675, 28)
        s.run_for(0.6)
        stage('after-second-back')
        s.frame('02-live')
        pointer = word(BLOCK_SLOT)
        if pointer:
            entries.append({'stage': 'block-after-close',
                            'magic': hex(word(pointer + 56)),
                            'mask': mask_state(pointer)})
        entries.append({'stage': 'live-slots', 'root': hex(word(0xC1832D50)),
                        'live': word(0xC1832D50) in LIVE_SLOTS})
    finally:
        s.close()
    (out / 'lifetime.json').write_text(json.dumps(entries, indent=2), encoding='utf-8')
    print(json.dumps(entries, indent=2))
    print('PASS state lifetime probe')
    return 0


if __name__ == '__main__':
    sys.exit(main())
