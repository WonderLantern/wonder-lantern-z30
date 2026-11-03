# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
"""Explicit SD LUT actions on the existing owned Extensions menu framework.

The framework owns the RAM allocation and page dispatch; no loader runs from
the pixel worker. State is pinned with the host until power-off, while Disable
and release restores fallback and drains heap/file resources through session.
This page is enabled only by the independent SD research builder.
"""
import struct
from .. import lut
from ..assembly import assemble, constant as c, invoke as n, jump
from . import lut_core, lut_session
from .lut_retouch import GATE
from .menu_slots import page_slots
from .sound_layout import STATE_ANCHOR

PAGE_INDEX = 960
# Owned word inside the unused page-zero descriptor: +16 anchors the host block
# and +20 is its open lock, so +24 stays free for the committed session pointer.
ACTIVATION_CELL = STATE_ANCHOR + 24
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('video', 4)
SIZES = dict(bitrate_get=96, bitrate_labels=768, bitrate_open=128,
             bitrate_action=384, bitrate_restore=128)
ACTIONS = ('Load SD LUT', 'Use embedded LUT', 'Disable and release', 'State')
STATUS = ('', 'File not found', 'File access failed', 'Invalid LUT', 'LUT too large',
          'No memory', 'Host busy', 'Cleanup pending', 'Loaded; cleanup pending')


def prepare(plan, f, resources, allocation_size, cube_text=None, size=17):
    page_offset, state_offset = allocation_size, allocation_size + 44
    for name, capacity in SIZES.items():
        f[name] = plan.alloc(bytes(capacity))
    parsed = lut.parse_cube(lut.identity_cube(size) if cube_text is None else cube_text)
    binary = lut.pack_binary(parsed['size'], parsed['domain_min'], parsed['domain_max'], parsed['table'])
    fallback = plan.alloc(binary)
    template = plan.alloc(lut_session.build_session(fallback, len(binary), flag=0))
    path = plan.alloc(b'A:\\WL-LUT.CUBE\0')
    manager = plan.address
    manager_bytes = lut_session.build_manager(manager)
    plan.alloc(manager_bytes)
    entry = plan.address
    core = lut_core.build_lut_core(0, entry, active_payload=True, session_lock=True,
                                   cell=ACTIVATION_CELL)
    plan.alloc(core)
    address, expected, name = GATE
    plan.edit(address, jump(address, entry, len(expected)), name + ' with menu-owned SD session', expected)
    from . import lut_failure
    lut_failure.add(plan)
    strings = {k: resources.alloc(v) for k, v in dict(title='Photo LUT', empty='', mark='*',
                  disabled='Disabled', embedded='Embedded LUT', loaded='SD LUT ready').items()}
    labels = plan.alloc(struct.pack('<4I', *(resources.alloc(s) for s in ACTIONS)))
    errors = plan.alloc(struct.pack('<9I', *(resources.alloc(s) for s in STATUS)))
    call = lambda name: n(f[name])
    codes = {}
    codes['bitrate_get'] = ('push {r4, lr};' + call('get') + 'cmp r0, #0; beq done;'
        + c('r1', state_offset) + 'add r0, r0, r1; done: pop {r4, pc};')
    codes['bitrate_restore'] = ('push {r4, lr};' + call('bitrate_get') +
        'cmp r0, #0; beq done; mov r4, r0;' + c('r1', template) + 'movs r2, #48;'
        + n(0x10199BDC, thumb=False) + 'movs r0, #0; str r0, [r4, #48];'
        + c('r1', ACTIVATION_CELL) + 'str r0, [r1];'
        'done: movs r0, #1; pop {r4, pc};')
    codes['bitrate_open'] = ('push {r4, lr};' + call('ensure') +
        'cmp r0, #0; beq done;' + call('bitrate_labels') +
        'movs r0, #2;' + c('r1', PAGE_INDEX) + n(0x1045F732) +
        'done: movs r0, #0; pop {r4, pc};')
    codes['bitrate_action'] = (
        'push {r4-r6, lr}; mov r4, r0; ldrh r5, [r4, #22]; ldrh r0, [r4, #24];'
        'adds r5, r5, r0; cmp r5, #3; bhi done;' + call('bitrate_get') +
        'cmp r0, #0; beq done; mov r6, r0; cmp r5, #3; beq refresh;'
        'cmp r5, #0; bne reset;' + c('r1', path) + n(manager) + 'b result;'
        'reset: ' + n(manager + 4) +
        'result: str r0, [r6, #48]; cmp r0, #0; beq committed;'
        'cmp r0, #8; bne refresh; committed: movs r0, #0; cmp r5, #2; beq enable;'
        + c('r0', lut_core.LUT_FLAG) + 'enable: dmb sy; str r0, [r6];'
        + c('r1', ACTIVATION_CELL) + 'cmp r0, #0; it eq; moveq r6, #0; str r6, [r1];'
        'refresh: ' + call('bitrate_labels') + 'mov r0, r4;' + call('render') +
        'done: movs r0, #0; pop {r4-r6, pc};')
    codes['bitrate_labels'] = (
        'push {r4-r8, lr};' + c('r0', strings['title']) + f'movs r1, #{TITLE_SLOT}; movs r2, #0;'
        + call('text') + c('r0', strings['title']) + f'movs r1, #{ROOT_SLOT}; movs r2, #0;'
        + call('text') + call('bitrate_get') + 'mov r4, r0; movs r6, #2;'
        'cmp r4, #0; beq choose; ldr r0, [r4]; cmp r0, #0; beq choose;'
        'movs r6, #1; ldr r0, [r4, #16]; cmp r0, #0; beq choose; movs r6, #0;'
        'choose: ' + c('r7', strings['disabled']) + 'cmp r6, #2; beq status;'
        + c('r7', strings['embedded']) + 'cmp r6, #1; beq status;' + c('r7', strings['loaded']) +
        'status: cmp r4, #0; beq status_ready; ldr r0, [r4, #48]; cmp r0, #0; beq status_ready;'
        'cmp r0, #8; bhi status_ready;' + c('r1', errors) + 'ldr r7, [r1, r0, lsl #2];'
        f'status_ready: mov r0, r7; movs r1, #{CURRENT_SLOT}; movs r2, #0;' + call('text') +
        'movs r5, #0; label_loop: ' + c('r0', labels) + 'ldr r0, [r0, r5, lsl #2];'
        f'adds r1, r5, #{LABEL_SLOT}; movs r2, #0;' + call('text') +
        c('r0', strings['empty']) + 'cmp r5, #3; beq state_value; cmp r5, r6; bne value;'
        + c('r0', strings['mark']) + 'b value; state_value: mov r0, r7;'
        f'value: adds r1, r5, #{MARK_SLOT}; movs r2, #0;' + call('text') +
        'adds r5, #1; cmp r5, #4; bne label_loop; movs r0, #0; pop {r4-r8, pc};')
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + 52, default=0, codes=codes,
                activation_cell=hex(ACTIVATION_CELL),
                manager=manager, manager_bytes=len(manager_bytes), core=entry, core_bytes=len(core),
                fallback=fallback, fallback_bytes=len(binary), path=path,
                embedded_identity=cube_text is None, lut_size=parsed['size'])


def finish(plan, f, extension, identifiers):
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['lut_sd_menu'] = dict(
        page_index=PAGE_INDEX, page_offset=extension['page_offset'], state_offset=extension['state_offset'],
        activation_cell=extension['activation_cell'],
        controls=list(identifiers), actions=list(ACTIONS), path='A:\\WL-LUT.CUBE',
        functions={name: hex(f[name] | 1) for name in SIZES},
        manager=hex(extension['manager'] | 1), manager_bytes=extension['manager_bytes'],
        gate=hex(GATE[0]), core=hex(extension['core'] | 1), core_bytes=extension['core_bytes'],
        fallback=hex(extension['fallback']), fallback_bytes=extension['fallback_bytes'],
        identity_lut=extension['embedded_identity'], lut_size=extension['lut_size'],
        session_default='disabled; explicitly loaded/enabled by owned menu action',
        resident_state='owned Extensions host RAM pinned until power-off',
        process_context='original index-6 worker; no filesystem/allocator calls from processing gate',
        camera_verified=False)
