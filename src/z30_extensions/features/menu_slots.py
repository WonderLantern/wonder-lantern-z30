# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Native text slots owned by the extension pages, one reserved block per page.

The stock table at 0xC2013368 holds 300 slots of 51 bytes. STOCK_SLOTS lists every
slot stock code writes with an immediate number or names in a menu row; a further
288 stock call sites compute their slot, so the blocks also sit outside everything
a QEMU session observed there. Slots stay below 256 because the owned code loads
them with 8-bit immediates.
"""

SLOT_COUNT = 256
STOCK_SLOTS = frozenset((*range(14), *range(20, 25), 26, 30, 31, 40, 59, 70, *range(72, 75), 77,
                         *range(79, 83), *range(84, 90), *range(100, 115), 177, *range(190, 200), 215))
BLOCKS = dict(sound_group=(117, 1), zebra=(118, 8), stamp=(126, 19), sound=(145, 28),
              nef=(200, 9), modules=(216, 8), video=(224, 11), aspect=(235, 17))
_owned = [slot for base, size in BLOCKS.values() for slot in range(base, base + size)]
if len(set(_owned)) != len(_owned) or max(_owned) >= SLOT_COUNT or STOCK_SLOTS.intersection(_owned):
    raise ValueError('Text slot blocks overlap each other or stock text')


def page_slots(name, rows):
    """Title, root label, row labels, row marks and current value of a choice page."""
    base, size = BLOCKS[name]
    if 3 + 2 * rows > size:
        raise ValueError(f'{name} needs {3 + 2 * rows} text slots but reserves {size}')
    return base, base + 1, base + 2, base + 2 + rows, base + 2 + 2 * rows
