# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Film grain on the luma plane of in-camera JPEGs, before the stock encoder.

Port of the Wonder Lantern Z5 FILM feature (A52, MIT): FUJI400 and HP5 grain,
strength 1..6, chosen on the FILM page (film_grain_ui.py). The pixel work is
the C payload modules/film_grain (film_grain_payload.py); its tables are
host.film_grain.data_blob(), computed here at build time.

The gate is the stock JPEG encode gateway 0x10096EE6 that the date stamp also
uses, and only its five capture-context call sites. With date-stamp in the
build, the date stamp gate calls grain_hook first, so the date is drawn on top
of the grain; without it this module installs the same gate with grain only.

grain_hook(args, ctx, lr): reads the menu state through the framework getter
(no state before the extension menu first initialised it after power-on, as
with the date stamp), returns at once while the style is OFF, otherwise calls
the payload with a call block {args, ctx, lr, state, data}.

Which encodes get grain (the Z5 policy, by the capture info's recording kind
+0x228, read by stock 0x101D5AD0 / 0x101D5AE0): JPEG only -> main image at
full strength and the three previews at 5/8; RAW+JPEG -> the main image only
(the NEF preview shares its plane on the Z5); RAW only -> the NEF's full-size
preview. The payload refuses a plane whose pages fault, whose CPU address is
not the physical address, or that is device memory; on cacheable memory it
cleans and invalidates each row before reading and cleans it after writing.
"""
from ...assembly import assemble, constant as c, invoke as n, jump
from ...host import film_grain as host
from . import date_stamp
from .film_grain_payload import CODE, SHA256, SIZE

HOOK_BYTES = 128
GATE_BYTES = 192


def _hook(get, state_offset, data, code):
    return (f'push {{r4-r7,lr}};sub sp,#20;mov r4,r0;mov r5,r1;mov r6,r2;'
            f'{n(get)}cmp r0,#0;beq out;'
            + c('r1', state_offset) + 'add r7,r0,r1;ldr r0,[r7];cmp r0,#0;beq out;'
            'str r4,[sp];str r5,[sp,#4];str r6,[sp,#8];str r7,[sp,#12];'
            + c('r0', data) + 'str r0,[sp,#16];mov r0,sp;'
            + c('r12', code | 1) + 'blx r12;'
            'out:add sp,#20;pop {r4-r7,pc};')


def gate_call(hook):
    """Gate fragment: r4 = capture context, r5 = lr of the encode call, [sp] = saved r0."""
    return f'ldr r0,[sp];mov r1,r4;mov r2,r5;{n(hook)}'


def _gate(hook):
    sites = ''.join(c('r1', site + 5) + 'cmp r5,r1;beq gate_hit;' for site in date_stamp.CALL_SITES)
    return (f'push {{r0-r3,r5,lr}};mov r5,lr;{sites}b gate_out;'
            f'gate_hit:{gate_call(hook)}'
            f'gate_out:pop {{r0-r3,r5,lr}};push.w {{r4-r8,lr}};movs r6,#1;mov r5,r0;movs r1,#0;'
            f'.balign 4;ldr.w pc,[pc,#0];.word {date_stamp.RESUME | 1};')


def add(plan, stamp=False):
    """stamp=True: date-stamp follows in this build and installs the shared gate."""
    if len(CODE) != SIZE:
        raise ValueError('film grain payload is truncated')
    control = plan.features['film_grain_control']
    get = int(plan.features['menu_framework']['functions']['get'], 16)
    for site in date_stamp.CALL_SITES:
        if plan.image.read(site, 4) != date_stamp._thumb_bl(site, date_stamp.ENCODE_GATEWAY):
            raise ValueError(f'Unexpected stock encode call at {site:#x}')
    data = plan.alloc(host.data_blob())
    code = plan.alloc(CODE)
    hook = plan.alloc(bytes(HOOK_BYTES))
    plan.put(hook, assemble(_hook(get, control['state_offset'], data, code), hook, HOOK_BYTES))
    report = dict(hook=hex(hook | 1), payload=hex(code | 1), payload_bytes=SIZE, payload_sha256=SHA256,
                  data=hex(data), data_bytes=host.DATA_BYTES, styles=list(host.STYLES[1:]),
                  strengths=host.STRENGTHS, call_sites=[hex(site) for site in date_stamp.CALL_SITES],
                  source='modules/film_grain/film_grain.c; algorithm from Wonder Lantern Z5 FILM A52 (MIT)',
                  native_parity='tools/verify_film_grain.py', camera_verified=False,
                  limitations=['Luma only, JPEG encoder input only: NEF raw data is never altered.',
                               'Grain is part of the JPEG pixels and cannot be removed afterwards.',
                               'Off until the extension menu has initialised after power-on (as the date stamp).',
                               'Retouch copies through the same step family get grain again while it is on.'])
    if stamp:
        report['gate'] = 'shared with date-stamp (grain first)'
    else:
        entry = plan.alloc(bytes(GATE_BYTES))
        plan.put(entry, assemble(_gate(hook), entry, GATE_BYTES))
        address, expected, _ = date_stamp.GATE
        plan.edit(address, jump(address, entry, len(expected)), 'Film grain gate at the stock JPEG encode gateway',
                  expected)
        report.update(gate=hex(address), gate_entry=hex(entry | 1), resume=hex(date_stamp.RESUME | 1))
    plan.features['film_grain'] = report
    return report
