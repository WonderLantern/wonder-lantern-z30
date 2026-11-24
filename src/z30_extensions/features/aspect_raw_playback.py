# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Read custom NEF preview dimensions from the file, not the stock area enum.

These hooks run in native file preparation, outside the GPU submit hook.
No current shooting setting, file write, or shared geometry-table mutation.
"""
import struct

from ..assembly import constant as c, invoke as n, jump


DIMENSION_SITES = {
    'standard': (0x10340B02, (0x10340EBB, 0x103474F3, 0x1034752B)),
    'extended': (0x10340B82, (0x10340E3D, 0x10347449, 0x10347489)),
}
PACK_SITE = 0x103488B2
PACK_CALLERS = (0x103473EF, 0x10347409, 0x10347463,
                0x103474A3, 0x1034750D, 0x10347579)


def install(plan, functions, codes, sizes, profiles):
    names = {'raw_preview_query': 512, 'raw_preview_pack': 384,
             'raw_preview_standard': 256, 'raw_preview_extended': 256,
             'raw_preview_standard_original': 96, 'raw_preview_extended_original': 96}
    for key, size in names.items():
        sizes[key] = size
        functions[key] = plan.alloc(bytes(size))
    shapes = {tuple(wh) for p in profiles for wh in p['jpeg_wh']}
    # NEF may describe the padded RAW plane rather than the visible JPEG.
    # The XPAN sample has 5600x2072 in its RAW IFD and 5568x2056 in JPEG SOF.
    # Keep those v43 padded planes for photos already taken; since issue 38
    # new NEFs carry the stock DX/16:9 plane instead. Stock NEFs sharing those
    # shapes read their own preview sizes; DX previews exceed the 1920x1080
    # bound and fall back to the stock path unchanged.
    shapes.update((p['jpeg_wh'][0][0]+32, p['jpeg_wh'][0][1]+16) for p in profiles)
    shapes.update(tuple(p['raw_plane_wh']) for p in profiles)
    shapes = sorted(shapes)
    shape_table = plan.alloc(b''.join(struct.pack('<2H', *wh) for wh in shapes))

    def callers(values, register='lr'):
        return ''.join(c('r5', value) + f'cmp {register},r5;beq allowed;' for value in values)

    # r0 = native file object, r1 = native stream kind (4 small, 5 large).
    # Main dimensions only gate the custom-photo path. Actual preview sizes
    # come from the native file service; old custom photos with stock previews
    # must retain their own 640x360 / 1920x1080 dimensions.
    codes['raw_preview_query'] = f'''push {{r4-r8,lr}};sub sp,#8;
        mov r4,r0;mov r6,r1;cmp r6,#4;beq kind;cmp r6,#5;bne bad;
        kind:ldrb r7,[r4,#4];cmp r7,#1;beq format;cmp r7,#2;bne bad;
        format:ldr r0,[r4,#12];cmp r0,#0;beq bad;
        ldrh.w r1,[r0,#0x398];ldrh.w r2,[r0,#0x39a];
        {c('r3', shape_table)}movs r8,#{len(shapes)};
        next_shape:ldrh r0,[r3];cmp r0,r1;bne next;
        ldrh r0,[r3,#2];cmp r0,r2;beq query;
        next:adds r3,#4;subs r8,#1;bne next_shape;b bad;
        query:movs r0,#0;str r0,[sp];mov r0,r6;mov r1,r7;
        ldr r2,[r4];mov r3,sp;{n(0x103A1FA8)}cmp r0,#0;bne bad;
        ldrh r0,[sp];ldrh r1,[sp,#2];cmp r0,#0;beq bad;cmp r1,#0;beq bad;
        cmp r6,#4;bne large;{c('r2',640)}cmp r0,r2;bhi bad;
        {c('r2',480)}cmp r1,r2;bhi bad;b result;
        large:{c('r2',1920)}cmp r0,r2;bhi bad;
        {c('r2',1080)}cmp r1,r2;bhi bad;
        result:orr r0,r0,r1,lsl #16;b done;
        bad:movs r0,#0;done:add sp,#8;pop {{r4-r8,pc}};'''

    for name, (site, returns) in DIMENSION_SITES.items():
        key = 'raw_preview_' + name
        codes[key] = f'''push {{r0-r3,r5-r7,lr}};
            {callers(returns)}b fallback;
            allowed:mov r0,r4;ldr r1,[sp,#8];cmp r1,#0;
            ite eq;moveq r1,#5;movne r1,#4;
            {n(functions['raw_preview_query'])}cmp r0,#0;beq fallback;
            ldr r1,[sp];strh r0,[r1];lsrs r0,#16;strh r0,[r1,#2];
            pop {{r0-r3,r5-r7,pc}};
            fallback:pop {{r0-r3,r5-r7,lr}};
            {c('r12',functions[key+'_original']|1)}bx r12;'''
        # Displaced CBZ must be relocated explicitly; the branch destinations
        # are far outside CBZ's range after moving the prologue to the tail.
        regs = 'r4,r5,lr' if name == 'standard' else 'r4,lr'
        zero = 0x10340B36 if name == 'standard' else 0x10340BBE
        codes[key+'_original'] = f'''push {{{regs}}};cmp r2,#0;beq zero;
            cmp r1,#5;movw r2,#640;{c('r12',(site+10)|1)}bx r12;
            zero:{c('r12',zero|1)}bx r12;'''
        expected = '30b5bab1052940f28022' if name == 'standard' else '10b5dab1052940f28022'
        plan.edit(site, jump(site, functions[key], 10),
                  'Per-file custom NEF preview dimensions: '+name, bytes.fromhex(expected))

    codes['raw_preview_pack'] = f'''push {{r0-r3,r5-r7,lr}};
        {callers(PACK_CALLERS)}b original;
        allowed:cmp r2,#1;bne original;mov r0,r4;
        {n(functions['raw_preview_query'])}cmp r0,#0;beq original;
        uxth r1,r0;str r1,[sp,#12];lsrs r0,#16;str r0,[sp,#32];
        original:pop {{r0-r3,r5-r7,lr}};
        push {{r4-r6,lr}};add.w r12,sp,#16;ldm.w r12,{{r4-r6}};
        {c('r12',0x103488BD)}bx r12;'''
    plan.edit(PACK_SITE, jump(PACK_SITE, functions['raw_preview_pack'], 10),
              'Per-file custom NEF decoded preview descriptor', bytes.fromhex('70b50df1100c9ce87000'))
    return dict(version=1, query_service='0x103a1fa8',
                file_types=[1, 2], file_shapes=[list(wh) for wh in shapes],
                dimension_callers=[hex(v) for _, values in DIMENSION_SITES.values() for v in values],
                descriptor_callers=[hex(v) for v in PACK_CALLERS],
                uses_current_shooting_profile=False, writes_photo=False,
                physical_playback_verified=False)
