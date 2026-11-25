# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""Propagate synchronous index-6 processing failure into the stock job error.

Only the independent SD research image installs these edits. Guarded phase-4
tables skip remaining image work after an error; later native interpreter
entries already skip encode/write when job+4 is nonzero. Outer object cleanup
is still owned by stock, and needs further device/upper-layer verification.
"""
import struct
from ...assembly import assemble, invoke, jump

SITE = 0x100A5552
EXPECTED = bytes.fromhex('44f22c71 41f80800 d7f80c23')
TABLES = ((0xC1888EB4, 0xC1888DC4, 'save'), (0xC18872A8, 0xC18871B8, 'preview'))


def add(plan):
    if plan.image.read(SITE, 12) != EXPECTED:
        raise ValueError('Unexpected post-submit effect result window')
    stub = plan.address
    source = (
        'movw r1, #0x472c; str.w r0, [r1, r8]; push {r0-r3};'
        'ldrb r1, [r4, #4]; cmp r1, #6; bne done; cmp r0, #0; beq failed;'
        'ldr r1, [r4, #8]; cmp r1, #0; beq failed;'
        'ldr r2, [r1]; cmp r2, #0; bne done;'  # Async result is not ready yet.
        'ldrb r1, [r1, #0x14]; cmp r1, #0; bne done;'
        'failed: movs r0, #1; str r0, [r8, #4]; movs r0, #0; movw r1, #0x472c; str.w r0, [r1, r8];'
        'done: pop {r0-r3}; ldr.w r2, [r7, #0x30c];'
        + f'ldr.w pc, ={SITE + 12 | 1};')
    code = assemble(source, stub, 256)
    plan.alloc(code)
    plan.edit(SITE, jump(SITE, stub, 12), 'LUT synchronous processing failure sets stock job error', EXPECTED)
    records = []
    for node, original, name in TABLES:
        if plan.image.words(node + 0x14, 1)[0] != original:
            raise ValueError('Unexpected phase-4 step table')
        steps = list(plan.image.words(original, 60))
        guards = []
        for step in range(20):
            function = steps[step * 3 + 2]
            if not function:
                continue
            address = plan.address
            guard = assemble('push {r4, lr}; ldr r1, [r0, #4]; cmp r1, #0; bne failed;'
                             + invoke(function) + 'pop {r4, pc};'
                             'failed: movs r0, #0; pop {r4, pc};', address, 128)
            plan.alloc(guard)
            steps[step * 3 + 2] = address | 1
            guards.append(dict(step=step, original=hex(function), wrapper=hex(address | 1)))
        address = plan.alloc(struct.pack('<60I', *steps))
        plan.edit(node + 0x14, struct.pack('<I', address), f'LUT {name} phase-4 error guards',
                  struct.pack('<I', original))
        records.append(dict(name=name, node=hex(node), table=hex(address), guards=guards))
    plan.features['lut_failure'] = dict(result_site=hex(SITE), result_stub=hex(stub | 1),
        guarded_tables=records, scope='Synchronous index-6 failures set job+4 and clear acceptance; no generic interpreter patch.',
        limitations=['Skip image work/encode is not a proof of outer object release or on-card rollback.'])
