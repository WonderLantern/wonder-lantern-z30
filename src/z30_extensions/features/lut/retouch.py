# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""CPU 3D LUT processing in the stock retouch effect slot.

Replaces the entry of the stock index-6 processing function (0x1025FB8E) with
a gate: flagged jobs run the Thumb-2 LUT core (decode target -> LUT -> encode
source on the stock-bound planes); unflagged jobs fall through to the stock
effect untouched. The LUT table is a packed WL3L payload embedded in the
append area, produced from a .cube file by z30_extensions.host.lut on the host.
"""
from ...host import lut
from ...assembly import jump
from . import core as lut_core

# cmp r0,#0; push {r4,lr}; beq; mov r4,r0; bl 0x1025fb00 (first half)
GATE = (0x1025FB8E, bytes.fromhex('002810b50ad00446fff7'),
        'Index-6 effect processing entry (retouch ops 5/6/7)')


def add(plan, cube_text=None, size=17, flag=lut_core.LUT_FLAG):
    """Install the LUT gate; embeds an identity table when cube_text is None."""
    embedded_identity = cube_text is None
    if cube_text is None:
        cube_text = lut.identity_cube(size)
    parsed = lut.parse_cube(cube_text)
    binary = lut.pack_binary(parsed['size'], parsed['domain_min'],
                             parsed['domain_max'], parsed['table'])
    block_address = plan.address
    plan.alloc(lut_core.build_block(binary, flag))
    gate_address = plan.address
    code = lut_core.build_lut_core(block_address, gate_address)
    plan.alloc(code)
    address, expected, name = GATE
    plan.edit(address, jump(address, gate_address, len(expected)), name, expected)
    plan.features['lut_retouch'] = dict(
        gate=hex(address), gate_entry=hex(gate_address | 1),
        block=hex(block_address), code_bytes=len(code), lut_size=parsed['size'],
        lut_title=parsed['title'], embedded_bytes=len(binary),
        flag=hex(flag), processing_falls_through_to_stock=True,
        pixel_contract='BT.601 limited range, Cb-first interleaved 4:2:2 pending on-device chart',
        chroma_pair_averaging=True, cache='stock 0x10204B48/0x10204B0C by region',
        identity_lut=embedded_identity,
        camera_verified=False, native_parity='tools/verify_lut_core_native.py',
        limitations=['Trial: every retouch option 0/1/2 job runs the embedded LUT while enabled.',
                     'Color matrix/range and CbCr byte order are configurable constants pending a controlled chart.'])
