# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Reuse of native highlight derivation in photo live view."""
from ...assembly import assemble, invoke, jump, constant

GATES = (
    (0x101777F2, bytes.fromhex('ecf798fe9db1032c11d106e0'), 'Photo zebra effective-pattern gate'),
    (0x1017791E, bytes.fromhex('ecf702fe9db1032f11d106e0'), 'Photo zebra availability gate'),
)


def add(plan):
    # These callbacks consume the same 34-property dependency record. Validate
    # the binding as well as both instructions before changing mode eligibility.
    if plan.image.words(0x114E8F8C, 6) != (
            0x1182, 0, 0x114E08B0, 0x114E611C, 0x101776FF, 0x1017784B):
        raise ValueError('Unexpected native highlight callback binding')
    if plan.image.words(0x114E08B0, 2) != (34, 0x114E0828):
        raise ValueError('Unexpected native highlight dependency list')
    control = plan.features['photo_zebra_control']
    get = plan.features['menu_framework']['functions']['get']
    switch = f"""cmp r5,#0;bne switch_done;
        push {{r0-r3,r12,lr}};{invoke(int(get,16))}cmp r0,#0;beq disabled;
        {constant('r1',control['state_offset'])}ldr r0,[r0,r1];cmp r0,#1;
        pop {{r0-r3,r12,lr}};bne reject;b switch_done;
        disabled:pop {{r0-r3,r12,lr}};b reject;
        switch_done:"""
    hooks = []
    for (address, expected, name), peaking, accept, reject in zip(
            GATES, ('r4', 'r7'), (0x1017780C, 0x10177938), (0x10177820, 0x1017794C)):
        entry = plan.address
        code = assemble(f'''
            push {{r2, r12}};
            {invoke(0x10164526)}
            pop {{r2, r12}};
            {switch}
            push {{r1}};
            cmp r5, #0; bne restore;
            movw r1, #0x2a1; tst r6, r1;
            pop {{r1}}; bne reject;
            b peaking;
          restore:
            pop {{r1}};
          peaking:
            cmp {peaking}, #3; bne reject;
            ldr.w pc, ={accept | 1};
          reject:
            ldr.w pc, ={reject | 1};
        ''', entry, 128)
        plan.alloc(code)
        plan.edit(address, jump(address, entry, len(expected)), name, expected)
        hooks.append(dict(site=hex(address), entry=hex(entry | 1), size=len(code)))
    plan.features['photo_zebra'] = dict(
        pattern_property='0x1182', threshold_property='0x1183',
        effective_pattern_property='0x189a', native_threshold_levels=8,
        patches=[hex(address) for address, _, _ in GATES],
        hooks=hooks,
        controls='Independent photo switch plus native highlight pattern and threshold',
        switch_default=0, switch_settings_file='WL-ZEBRA.CFG',
        remaining_native_gates=True, native_request_builder_unchanged=True,
        camera_verified=False, realtime_pixels_verified=False,
        capture_file_integrity_verified=False)
