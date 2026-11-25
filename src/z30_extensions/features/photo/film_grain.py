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

Write to NEF (the page's third row; Z5 A48/A49): every NEF carries an XMP
block <rdf:Description xmlns:crd="http://ns.adobe.com/camera-raw-defaults/1.0/">
that Lightroom applies when its raw defaults are "Camera Settings". Stock
0x101B862E builds it at capture through a writer at ctx + 0x86C {cursor,
bytes left, overflow} (r7 = writer, r4 = ctx), and closes it at 0x101B86B4
with adr r1,"</rdf:Description>"; mov r0,r7; bl append 0x101B86CC. Those
8 bytes jump to crd_gate, which calls crd_hook and then replays them. While
the style is FUJI400 or HP5 and Write to NEF is ON, crd_hook adds GrainAmount,
GrainSize and GrainFrequency through the stock element writer 0x101B86DE
(<crd:name>value</crd:name>), only when the writer has not overflowed and has
at least CRD_ROOM bytes left. The menu is read when the block is built (the
capture), so each NEF gets the setting of its own shot. Amounts are the Z5's
Lightroom calibration: FUJI400 level n -> 8(n+1)/40/65, HP5 -> (28(n+1)+1)/3/33/65.
"""
from ...assembly import assemble, constant as c, invoke as n, jump
from ...host import film_grain as host
from . import date_stamp
from .film_grain_payload import CODE, SHA256, SIZE
from .film_grain_ui import CRD_LEFT, CRD_SKIPPED, CRD_WRITTEN, NEF_FIELD, STRENGTH_FIELD

HOOK_BYTES = 128
GATE_BYTES = 192
CRD_SITE = 0x101B86B4
CRD_EXPECTED = bytes.fromhex('d3a1384600f008f8')   # adr r1,0x101B8A04; mov r0,r7; bl 0x101B86CC
CRD_RESUME = CRD_SITE + len(CRD_EXPECTED)
CRD_CLOSE = 0x101B8A04                               # "</rdf:Description>"
CRD_APPEND = 0x101B86CC
CRD_ELEMENT = 0x101B86DE
CRD_ROOM = 160                                       # the three elements take 113 bytes
CRD_HOOK_BYTES = 256
CRD_GATE_BYTES = 48
GRAIN_SIZE = (40, 33)                                # FUJI400, HP5
GRAIN_FREQUENCY = 65


def lightroom_grain(style, strength):
    """(GrainAmount, GrainSize, GrainFrequency) for a style and strength 1..6 (Z5 A49)."""
    if style not in (host.STYLE_FUJI400, host.STYLE_HP5) or not 1 <= strength <= host.STRENGTHS:
        raise ValueError('No Lightroom grain for this setting')
    amount = 8 * (strength + 1) if style == host.STYLE_FUJI400 else (28 * (strength + 1) + 1) // 3
    return amount, GRAIN_SIZE[style - 1], GRAIN_FREQUENCY


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


def _crd_hook(get, state_offset, amounts, names, fmt):
    """r7 = the crd writer at entry; keeps r4-r11. r4 = style - 1, r5 = writer, r6 = state."""
    def put(name, value):
        return f'mov r0,r5;{c("r1", names[name])}{c("r2", fmt)}{value}{n(CRD_ELEMENT)}'
    return (f'push {{r3-r7,lr}};mov r5,r7;'
            f'{n(get)}cmp r0,#0;beq out;' + c('r1', state_offset) + 'add r6,r0,r1;'
            'ldr r4,[r6];subs r4,#1;cmp r4,#1;bhi out;'
            f'ldr r0,[r6,#{NEF_FIELD}];cmp r0,#0;beq out;'
            f'ldr r2,[r6,#{STRENGTH_FIELD}];cmp r2,#{host.STRENGTHS - 1};bhi out;'
            f'movs r3,#{host.STRENGTHS};mla r2,r4,r3,r2;' + c('r3', amounts) + 'ldrb r7,[r3,r2];'
            'ldr r0,[r5,#8];cmp r0,#0;bne skipped;'
            f'ldr r0,[r5,#4];str r0,[r6,#{CRD_LEFT}];cmp r0,#{CRD_ROOM};blo skipped;'
            + put('amount', 'mov r3,r7;')
            + put('size', f'movs r3,#{GRAIN_SIZE[0]};cmp r4,#0;it ne;movne r3,#{GRAIN_SIZE[1]};')
            + put('frequency', f'movs r3,#{GRAIN_FREQUENCY};')
            + f'ldr r0,[r6,#{CRD_WRITTEN}];adds r0,#1;str r0,[r6,#{CRD_WRITTEN}];b out;'
            f'skipped:ldr r0,[r6,#{CRD_SKIPPED}];adds r0,#1;str r0,[r6,#{CRD_SKIPPED}];'
            'out:pop {r3-r7,pc};')


def _crd_gate(hook):
    """Replaces the 8 stock bytes at CRD_SITE: hook, then adr/mov/bl with absolute addresses."""
    return (f'{n(hook)}' + c('r1', CRD_CLOSE) + 'mov r0,r7;' + c('r12', CRD_RESUME | 1) + 'mov lr,r12;'
            + c('r12', CRD_APPEND | 1) + 'bx r12;')


def add_crd(plan, control, get):
    """Write to NEF: Lightroom grain in each NEF's XMP crd: block."""
    image = plan.image
    if image.read(CRD_SITE, len(CRD_EXPECTED)) != CRD_EXPECTED:
        raise ValueError('Unexpected stock NEF crd block end')
    if image.read(CRD_CLOSE, 19) != b'</rdf:Description>\0':
        raise ValueError('Unexpected crd closing string')
    if (image.read(CRD_ELEMENT, 4) != bytes.fromhex('0fb40a46')
            or image.read(CRD_APPEND, 4) != bytes.fromhex('0fb410b5')):
        raise ValueError('Unexpected crd writer functions')
    amounts = plan.alloc(bytes(lightroom_grain(style, strength)[0]
                               for style in (host.STYLE_FUJI400, host.STYLE_HP5)
                               for strength in host.STRENGTH_ORDER))
    names = {key: plan.alloc(text.encode('ascii') + b'\0') for key, text in
             (('amount', 'GrainAmount'), ('size', 'GrainSize'), ('frequency', 'GrainFrequency'))}
    fmt = plan.alloc(b'%u\0')
    shared = plan.features['menu_framework']['functions'].get('grain_crd')
    hook = int(shared, 16) & ~1 if shared else plan.alloc(bytes(CRD_HOOK_BYTES))
    plan.put(hook, assemble(_crd_hook(get, control['state_offset'], amounts, names, fmt), hook, CRD_HOOK_BYTES))
    if shared:
        # aspect-profiles owns the site (xmp_crop.py) and calls this hook before the stock closer.
        gate = 'shared with aspect-profiles (grain first, then the closer and the crs: crop)'
    else:
        entry = plan.alloc(bytes(CRD_GATE_BYTES))
        plan.put(entry, assemble(_crd_gate(hook), entry, CRD_GATE_BYTES))
        plan.edit(CRD_SITE, jump(CRD_SITE, entry, len(CRD_EXPECTED)),
                  'Film grain: Lightroom grain in the NEF crd block', CRD_EXPECTED)
        gate = hex(entry | 1)
    return dict(site=hex(CRD_SITE), expected=CRD_EXPECTED.hex(), resume=hex(CRD_RESUME | 1),
                gate=gate, hook=hex(hook | 1), element_writer=hex(CRD_ELEMENT | 1),
                append=hex(CRD_APPEND | 1), room=CRD_ROOM, amounts=hex(amounts),
                lightroom={name: {str(level): lightroom_grain(style, level) for level in range(1, host.STRENGTHS + 1)}
                           for style, name in ((host.STYLE_FUJI400, 'FUJI400'), (host.STYLE_HP5, 'HP5'))},
                record=dict(written=control['crd_record_offset'], skipped=control['crd_record_offset'] + 4,
                            bytes_left=control['crd_record_offset'] + 8),
                source='Wonder Lantern Z5 A48/A49 (MIT)', camera_verified=True,
                camera_note='2026-11-26: on/off, burst with strength changes, HP5, with the aspect crop; '
                            'ACR and Lightroom Classic with raw defaults "Camera Settings" show the values.')


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
    report['nef_crd'] = add_crd(plan, control, get)
    report['limitations'].append('Write to NEF only adds Lightroom settings to the NEF XMP; Lightroom applies them '
                                 'when its raw defaults are "Camera Settings". Lightroom grain is even across '
                                 'tones, the in-camera grain is weaker in highlights.')
    plan.features['film_grain'] = report
    return report
