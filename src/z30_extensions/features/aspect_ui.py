# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Experimental native photo-aspect selector and grid reference-frame hook."""
import struct
from ..assembly import constant as c, invoke as n, assemble, jump
from .aspect_geometry import PROFILES, build_trial, preview_dimensions
from .menu_slots import page_slots

PAGE_INDEX = 961
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = page_slots('aspect', 7)
MAGIC = 0x32414C57  # WLA2: previous-area field now refers to the user setting.
AREA_KEY = 0x501
CAPTURE_AREA_KEY = 0x189E  # Derived state; never substitute for the user setting.
NAMES = ('OFF (stock)', '4:3', '5:4', '2:1', '2.35:1', '2.39:1', '65:24 XPAN')
SPANS = ((0x11571DDC, 120), (0x115A4D4C, 0x1E0))
SIZES = dict(aspect_get=96, aspect_labels=384, aspect_open=160, aspect_action=512,
             aspect_save=640, aspect_restore=640, aspect_close=128,
             aspect_translate=256, aspect_file_guard=128, aspect_index=160, aspect_trim=160, aspect_trim_alternate=192,
             aspect_grid=384, aspect_grid_original=64, aspect_sync=256, aspect_output_data=160,
             aspect_frame_axis=384, aspect_frame_prepare=1024)

# Independent native view rectangle calculations query area geometry.
# Guard exact callsites, not an address range or a guessed sleep/wake refresh.
LIVEVIEW_WINDOW_CALLER = 0x10197D9D
LIVEVIEW_OUTPUT_CALLERS = (0x1018AB41, LIVEVIEW_WINDOW_CALLER, 0x101985E5, 0x1018AE57)

# Return addresses of the stock still-image buffer/geometry consumers. Generic
# file playback/editing callers are deliberately excluded: an old photo must
# never acquire the currently selected shooting ratio.
PREVIEW_CALLERS = {
    'small': (0x100A4F65, 0x100A51AD, 0x101434FD, 0x10143641,
              0x10146737),
    'large': (0x10143545, 0x10143671, 0x101438DB, 0x1014676D),
}

# These native file/metadata/layout consumers describe an existing image,
# rather than the next capture. In particular 1038fa8c scales a preview using
# TWO getters; guarding only the numerator or denominator still distorts it.
# Keep their original table pointers together. The shared 10221ae2 metadata
# service is included: its input record is not the current shooting setting.
FILE_GEOMETRY_CALLERS = (
    0x10221B45,
    0x103409A7, 0x103409B9, 0x10340EDB, 0x10341023,
    0x103419EB, 0x10341A6B, 0x10341A7D, 0x10341A97,
    0x10341E7F, 0x10342357, 0x10342669, 0x103426A9,
    0x1034423D, 0x10347557, 0x10348323, 0x10348659,
    0x1034B1F7, 0x10351BD9, 0x103544DB,
    0x1038FAEB, 0x1038FB03, 0x103A8C9F, 0x103A8D7B, 0x103AF625,
)

PREVIEW_DIRECT_SPAN = (0x1157223C, 80)
# Literal reads in the independent preview pixel-buffer producers. Strides
# stay stock; the immutable copies only replace index-1 output dimensions.
PREVIEW_DIRECT_HOOKS = (
    (0x10142DE4, 10, 'r3', 0x11572250,
     'movs r2,#1;strb r2,[r4,#0x14];addw r1,r1,#0x1ff;',
     'ff4b0122227501f2ff11'),
    (0x10142E46, 12, 'r3', 0x11572250,
     'strb.w r2,[r4,#0x30];adds r3,#0x14;add.w r3,r3,r1,lsl #2;',
     'e74b84f83020143303eb8103'),
    (0x101437A2, 10, 'r0', 0x11572264,
     'add.w r1,r0,r1,lsl #2;ldrsh.w r0,[r1];',
     'f74800eb8101b1f90000'),
    (0x1014651C, 8, 'r0', 0x11572250,
     'strh.w r8,[r4,#4];subs r0,#0x14;',
     'f548a4f804801438'),
    (0x10146552, 12, 'r1', 0x11572250,
     'strb.w lr,[r4,#0x1c];adds r1,#0x14;add.w r1,r1,r2,lsl #2;',
     'e84984f81ce0143101eb8201'),
)


def prepare(plan, f, resources, allocation_size, *, trace=False, border=False, border_diagnostic=False,
            border_lease=False, mask=False, mask_trial=False):
    sizes = dict(SIZES)
    for key, size in SIZES.items():
        f[key] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    strings = {k: resources.alloc(v) for k, v in
               dict(title='Photo aspect', root='Photo aspect', empty='', mark='*').items()}
    labels = plan.alloc(struct.pack('<7I', *(resources.alloc(v) for v in NAMES)))
    copies = []
    geometries = [(0, 0, 5568, 3712)]
    profiles = []
    for profile in PROFILES:
        image, report = build_trial(plan.source, profile)
        copies.append([plan.alloc(image[a-0x10020000:a-0x10020000+length]) for a, length in SPANS])
        geometries.append(tuple(report['target_large_in_dx_xywh']))
        profiles.append(dict(name=profile,ratio=report['ratio'],jpeg_wh=report['target_jpeg_wh'],
                             large_xywh=report['target_large_in_dx_xywh'],
                             preview_wh=[preview_dimensions(*report['target_jpeg_wh'][0], box)
                                         for box in ((640,360),(1920,1080))]))
    pointer_table = plan.alloc(struct.pack('<12I', *(v for row in copies for v in row)))
    geometry_table = plan.alloc(b''.join(struct.pack('<4I', *v) for v in geometries))
    path = plan.alloc(b'A:\\WL-ASPECT.CFG\0')
    call = lambda key: n(f[key])
    from .sound_settings import settings_code
    settings = settings_code(f, path, state_get='aspect_get', close='aspect_close',
        magic=MAGIC, enabled_offset=8, selected_offset=0, fd_offset=4,
        enabled_max=4, selected_max=6, load_sound=False, restore_enabled=True)
    codes = {new: settings[old] for old, new in
             (('save_settings', 'aspect_save'), ('restore_settings', 'aspect_restore'),
              ('settings_close', 'aspect_close'))}
    codes['aspect_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r1;done:pop {{r4,pc}};'''
    codes['aspect_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['root'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('aspect_get')}movs r4,#0;cmp r0,#0;beq ready;ldr r4,[r0];cmp r4,#6;it hi;movhi r4,#0;
        ready:movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r4,r5;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#7;bne loop;
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['aspect_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;{call('aspect_sync')}
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('aspect_get')}ldr r4,[r0];cmp r4,#6;it hi;movhi r4,#0;
        {call('get')}{c('r1',page_offset)}add r0,r1;
        ASPECT_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    # Restore the native backend together with the profile. A persisted choice
    # alone must never apply custom area-2 geometry while the camera stays DX.
    codes['aspect_sync'] = f'''push {{r4-r6,lr}};{call('aspect_get')}cmp r0,#0;beq done;
        ldr r4,[r0];subs r4,#1;cmp r4,#5;bhi done;
        {c('r0',0x188c)}{n(0x101993de)}cmp r0,#0;bne done;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;beq done;
        {c('r0',AREA_KEY)}movs r1,#2;{n(0x10465990)}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    # Keep the original area when enabling, and restore it when disabling.
    # Publish before the native setter can notify geometry consumers. The area
    # guard still rejects custom tables until area 2 is visible. Restore both
    # selection fields if the setter or immediate readback fails; save only
    # after success. This also matches restore -> sync initialization ordering.
    codes['aspect_action'] = f'''push {{r4-r9,lr}};sub sp,#4;mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r0;cmp r5,#6;bhi done;
        {c('r0',0x188c)}{n(0x101993de)}cmp r0,#0;bne done;
        {call('aspect_get')}cmp r0,#0;beq done;mov r6,r0;ldr r7,[r6];ldr r9,[r6,#8];
        cmp r5,#0;beq disable;cmp r7,#0;bne area;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#4;bhi zero;cmp r0,#1;beq zero;cmp r0,#3;bne prior;
        zero:movs r0,#0;prior:str r0,[r6,#8];
        area:movs r8,#2;b set_area;
        disable:cmp r7,#0;beq root;ldr r8,[r6,#8];cmp r8,#4;bhi fallback;cmp r8,#1;beq fallback;cmp r8,#3;bne set_area;
        fallback:movs r8,#0;
        set_area:str r5,[r6];dmb ish;
        {c('r0',AREA_KEY)}mov r1,r8;{n(0x10465990)}cmp r0,#1;bne rollback;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,r8;bne rollback;
        {call('aspect_save')}cmp r0,#1;beq root;
        mov r0,r4;{call('show_error')}b done;
        rollback:str r7,[r6];str r9,[r6,#8];dmb ish;b done;
        root:mov r0,r4;{call('page_return')}
        done:movs r0,#0;add sp,#4;pop {{r4-r9,pc}};'''
    # Pure address translation to immutable per-profile tables. No ROM/RAM
    # geometry table is edited when the user changes the selected profile.
    codes['aspect_translate'] = f'''push {{r4-r6,lr}};mov r4,r0;{call('aspect_get')}
        cmp r0,#0;beq stock;ldr r5,[r0];subs r5,#1;cmp r5,#5;bhi stock;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;bne stock;
        {c('r1',SPANS[0][0])}subs r6,r4,r1;cmp r6,#{SPANS[0][1]};blo first;
        {c('r1',SPANS[1][0])}subs r6,r4,r1;{c('r2',SPANS[1][1])}cmp r6,r2;bhs stock;
        lsls r5,r5,#3;adds r5,#4;b translate;
        first:lsls r5,r5,#3;
        translate:{c('r1',pointer_table)}ldr r0,[r1,r5];add r0,r6;pop {{r4-r6,pc}};
        stock:mov r0,r4;pop {{r4-r6,pc}};'''
    file_callers = plan.alloc(struct.pack('<'+'I'*len(FILE_GEOMETRY_CALLERS), *FILE_GEOMETRY_CALLERS))
    # r0 = original pointer, r1 = original native caller. No app-state guess,
    # lock, table mutation or per-file write is needed for this isolation.
    codes['aspect_file_guard'] = f'''push {{r4,lr}};mov r4,r0;
        {c('r2',file_callers)}movs r3,#{len(FILE_GEOMETRY_CALLERS)};
        next_file:ldr r0,[r2],#4;cmp r0,r1;beq original;
        subs r3,#1;bne next_file;
        mov r0,r4;{call('aspect_translate')}pop {{r4,pc}};
        original:mov r0,r4;pop {{r4,pc}};'''
    codes['aspect_index'] = '''cmp r0,#0;bne one;cmp r1,#0;beq i0;cmp r1,#1;beq i1;cmp r1,#2;beq i2;b bad;
        one:cmp r0,#1;bne two;cmp r1,#1;beq i3;cmp r1,#2;beq i4;b bad;
        two:cmp r0,#2;bne bad;cmp r1,#2;beq i5;
        bad:movs r0,#6;bx lr;i0:movs r0,#0;bx lr;i1:movs r0,#1;bx lr;
        i2:movs r0,#2;bx lr;i3:movs r0,#3;bx lr;i4:movs r0,#4;bx lr;i5:movs r0,#5;bx lr;'''
    view_callers = plan.alloc(struct.pack('<' + 'I' * len(LIVEVIEW_OUTPUT_CALLERS), *LIVEVIEW_OUTPUT_CALLERS))
    codes['aspect_output_data'] = f'''push {{r4,lr}};mov r4,r0;
        {c('r2',view_callers)}movs r3,#{len(LIVEVIEW_OUTPUT_CALLERS)};
        next_view:ldr r0,[r2],#4;cmp r1,r0;beq view;
        subs r3,#1;bne next_view;b custom;
        view:
        {call('aspect_get')}cmp r0,#0;beq original;ldr r0,[r0];subs r0,#1;cmp r0,#1;bls original;
        custom:mov r0,r4;{call('aspect_file_guard')}pop {{r4,pc}};
        original:mov r0,r4;pop {{r4,pc}};'''
    selectors = []
    def selector(name, address, size, source):
        sizes[name] = size
        f[name] = plan.alloc(bytes(size))
        codes[name] = source
        length = 8 if address % 4 == 0 else 10
        plan.edit(address, jump(address, f[name], length), 'Experimental aspect selector: ' + name)
        selectors.append(address)
    # Independent still-preview output dimensions. Keep the primary geometry
    # tables identical to v2; only capture consumers with preview aspect index
    # 1 (stock 16:9) can see these immutable per-profile pairs. Excluded callers
    # retain the exact stock pointer, even while a custom ratio is selected.
    for kind,address,base in (('small',0x1020C02E,0x115A4CFC),
                              ('large',0x1020C038,0x115A4D10)):
        column = 0 if kind == 'small' else 1
        table = plan.alloc(b''.join(struct.pack('<2H',*p['preview_wh'][column]) for p in profiles))
        callers = PREVIEW_CALLERS[kind]
        caller_table = plan.alloc(struct.pack('<'+'I'*len(callers),*callers))
        selector('aspect_preview_'+kind,address,320,f'''push {{r4-r8,lr}};
            mov r4,r0;mov r5,lr;{c('r6',base)}add r6,r6,r4,lsl #2;
            cmp r4,#1;bne original;
            {c('r1',caller_table)}movs r2,#{len(callers)};
            next_caller:ldr r3,[r1],#4;cmp r5,r3;beq allowed;
            subs r2,#1;bne next_caller;b original;
            allowed:{call('aspect_get')}cmp r0,#0;beq original;
            ldr r7,[r0];subs r7,#1;cmp r7,#5;bhi original;
            {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;bne original;
            {c('r0',table)}add r0,r0,r7,lsl #2;pop {{r4-r8,pc}};
            original:mov r0,r6;pop {{r4-r8,pc}};''')
    # The camera sample from v3 still contained 16:9 previews. These separate
    # pixel-buffer producers read dimensions directly, bypassing the getters.
    preview_shadows=[]
    preview_base,preview_length=PREVIEW_DIRECT_SPAN
    for profile in profiles:
        data=bytearray(plan.source[preview_base-0x10020000:preview_base-0x10020000+preview_length])
        struct.pack_into('<2H',data,4,*profile['preview_wh'][0])
        struct.pack_into('<2H',data,44,*profile['preview_wh'][1])
        preview_shadows.append(plan.alloc(data))
    preview_pointers=plan.alloc(struct.pack('<6I',*preview_shadows))
    sizes['aspect_preview_data']=192
    f['aspect_preview_data']=plan.alloc(bytes(192))
    codes['aspect_preview_data']=f'''push {{r4-r6,lr}};mov r4,r0;
        {call('aspect_get')}cmp r0,#0;beq unchanged;ldr r5,[r0];subs r5,#1;cmp r5,#5;bhi unchanged;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;bne unchanged;
        {c('r0',preview_base)}subs r6,r4,r0;cmp r6,#{preview_length};bhs unchanged;
        {c('r0',preview_pointers)}ldr r0,[r0,r5,lsl #2];add r0,r6;pop {{r4-r6,pc}};
        unchanged:mov r0,r4;pop {{r4-r6,pc}};'''
    for address,length,register,base,replay,expected in PREVIEW_DIRECT_HOOKS:
        name=f'aspect_preview_direct_{address:x}'
        sizes[name]=192;f[name]=plan.alloc(bytes(192))
        slot={'r0':0,'r1':4,'r3':12}[register]
        # Preserve the live lr value too: one producer uses it as a data
        # register. The stack return avoids clobbering a live r12 at continuation.
        codes[name]=f'''push {{r0-r3,r12,lr}};{c('r0',base)}
            {call('aspect_preview_data')}str r0,[sp,#{slot}];pop {{r0-r3,r12,lr}};
            {replay}push {{r0,r1}};{c('r0',(address+length)|1)}
            str r0,[sp,#4];pop {{r0,pc}};'''
        plan.edit(address,jump(address,f[name],length),'Aspect independent preview producer',bytes.fromhex(expected))
    selector('aspect_output', 0x1020BFDA, 768 if trace else 128, f'''push {{r4,lr}};mov r4,lr;{c('r2',0xc18c7dfc)}
        ldr r0,[r2,r0,lsl #2];add r0,r0,r1,lsl #2;mov r1,r4;{call('aspect_output_data')}pop {{r4,pc}};''')
    for name, address, offset, fallback in (('aspect_internal',0x1020BFC8,0,0),
                                           ('aspect_reduced',0x1020C05C,0x28,8)):
        selector(name,address,160,f'''push {{r4,lr}};mov r4,lr;cmp r0,#0;bne fixed;
            {c('r0',0xc18c7de8+offset)}ldr r0,[r0,r1,lsl #2];add r0,r0,r2,lsl #2;
            mov r1,r4;{call('aspect_file_guard')}pop {{r4,pc}};
            fixed:{c('r0',0x115a4c80+fallback)}pop {{r4,pc}};''')
    selector('aspect_offset',0x1020C072,128,f'''push {{r4,lr}};mov r4,lr;
        {c('r1',0x115a4d4c)}add r0,r1,r0,lsl #2;mov r1,r4;{call('aspect_file_guard')}pop {{r4,pc}};''')
    for name,address,offset,fallback in (('aspect_crop',0x1020C07C,0x3c,0xc),
                                        ('aspect_edges',0x1020C0D2,0x50,0x10),
                                        ('aspect_desc_edges',0x1020C128,0x64,0x10)):
        selector(name,address,224,f'''push {{r4-r6,lr}};mov r6,lr;cmp r0,#0;bne fixed;
            mov r4,r1;mov r0,r2;mov r1,r3;{call('aspect_index')}mov r5,r0;
            {c('r0',0xc18c7de8+offset)}ldr r0,[r0,r4,lsl #2];add r0,r0,r5,lsl #2;
            mov r1,r6;{call('aspect_file_guard')}pop {{r4-r6,pc}};
            fixed:{c('r0',0x115a4c80+fallback)}pop {{r4-r6,pc}};''')
    # Horizontal trim comes from a direct PC-relative table reference.
    codes['aspect_trim'] = f'''push {{r0,r1,r3,lr}};{c('r0',0x11571e18)}
        {call('aspect_translate')}mov r2,r0;pop {{r0,r1,r3,lr}};
        ldrb r0,[r5,#0x229];add r1,r1,r1,lsl #1;
        {c('r12',0x101428b5)}bx r12;'''
    plan.edit(0x101428AA,jump(0x101428AA,f['aspect_trim'],10),'Aspect horizontal trim table',
              bytes.fromhex('f94a95f8290201eb4101'))
    # Camera trace: info+0x228 == 3 skips the default table reference above
    # and reads 0x11571DDC instead. Translate that distinct reference too.
    codes['aspect_trim_alternate'] = f'''push {{r0,r2,r3,lr}};{c('r0',0x11571ddc)}
        {call('aspect_translate')}mov r1,r0;pop {{r0,r2,r3,lr}};
        add.w r0,r0,r0,lsl #1;add.w r1,r1,r0,lsl #2;
        push {{r0,r1}};{c('r0',0x101427fb)}str r0,[sp,#4];pop {{r0,pc}};'''
    plan.edit(0x101427F0,jump(0x101427F0,f['aspect_trim_alternate'],10),
              'Aspect alternate horizontal trim table', bytes.fromhex('2a4900eb400001eb8001'))
    # v5's speculative narrow-source rewrite failed on camera. Keep native
    # configuration unchanged; an opt-in diagnostic can still snapshot it.
    if trace:
        sizes['aspect_narrow_source']=192
        f['aspect_narrow_source']=plan.alloc(bytes(192))
        codes['aspect_narrow_source']=f'''strh.w r3,[r4,#0x4e];strh.w r3,[r4,#0x50];ldrb r0,[r5];
        push {{r0,r1}};{c('r0',0x10142a2d)}str r0,[sp,#4];pop {{r0,pc}};'''
        plan.edit(0x10142A22,jump(0x10142A22,f['aspect_narrow_source'],10),
                  'Aspect diagnostic normal configuration snapshot',bytes.fromhex('a4f84e30a4f850302878'))
    # The native normalizer rewrites lengths and counts, so override AFTER
    # it, restricted to its original mode-0 updater callsite. Other callers
    # and outputs retain the exact original normalization path.
    codes['aspect_grid_original'] = f'''push.w {{r4-r9,lr}};sub sp,#0x24;mov r6,r1;movs r2,#32;
        {c('r12',0x10454d51)}bx r12;'''
    from .aspect_frame import source_codes
    codes.update(source_codes(f, include_borders=not (mask or mask_trial)))
    codes['aspect_grid'] = f'''push {{r4-r6,lr}};mov r4,lr;
        {call('aspect_grid_original')}mov r6,r0;{c('r1',0x104be455)}cmp r4,r1;bne done;
        {call('aspect_get')}cmp r0,#0;beq done;ldr r5,[r0];cmp r5,#0;beq done;cmp r5,#6;bhi done;
        {c('r0',AREA_KEY)}{n(0x10465984)}cmp r0,#2;bne done;
        subw sp,sp,#0x468;mov r0,sp;{n(0x104b9348)}
        {c('r0',0xc201a7f4)}add r1,sp,#8;
        {c('r2',geometry_table)}add r2,r2,r5,lsl #4;mov r3,r5;
        {call('aspect_frame_prepare')}addw sp,sp,#0x468;
        done:mov r0,r6;pop {{r4-r6,pc}};'''
    plan.edit(0x10454D46,jump(0x10454D46,f['aspect_grid'],10),'Experimental aspect grid frame after normalization',
              bytes.fromhex('2de9f04389b00e462022'))
    mask_state = None
    mask_offset = None
    if mask or mask_trial:
        from .aspect_mask import (SIZES as mask_sizes, STATE_SIZE, SUBMIT_SITE,
                                  SUBMIT_EXPECTED, SUBMIT_SIZE, trial_state,
                                  source_codes as mask_source_codes)
        if mask and mask_trial:
            raise ValueError('Soft mask dynamic and trial modes are mutually exclusive')
        if mask and (border or border_diagnostic or border_lease):
            raise ValueError('Soft mask dynamic mode is built without border extras')
        for key, size in mask_sizes.items():
            sizes[key] = size
            f[key] = plan.alloc(bytes(size))
        if mask_trial:
            # Trial mode keeps a fixed ROM-side state for the QEMU replay only.
            mask_state = plan.alloc(trial_state())
        codes.update(mask_source_codes(f, mask_state, trial=bool(mask_trial), geometry_table=geometry_table))
        plan.edit(SUBMIT_SITE, jump(SUBMIT_SITE, f['aspect_mask_submit'], SUBMIT_SIZE),
                  'Soft aspect mask: dim the live-view image surface at the GPU submit boundary',
                  bytes.fromhex(SUBMIT_EXPECTED))
    if border:
        from .aspect_border import SIZES as border_sizes, source_codes as border_codes
        border_sizes = dict(border_sizes)
        if border_diagnostic:
            border_sizes.update(aspect_border_note=128, aspect_border_snapshot=256,
                                aspect_border_apply=1280, aspect_border_publish=1280,
                                aspect_border_monitor=160, aspect_border_pack=192)
        if border_lease:
            from .aspect_border_lease import SIZES as lease_sizes
            border_sizes.update(lease_sizes)
            border_sizes['aspect_border_publish']=2048 if border_diagnostic else 1024
            if border_diagnostic:border_sizes['aspect_border_snapshot']=384
        for key, size in border_sizes.items():
            sizes[key] = size
            f[key] = plan.alloc(bytes(size))
        from .aspect_trace import TRACE_BYTES
        border_cache_offset = 16 + (TRACE_BYTES if trace else 0)
        codes.update(border_codes(f, geometry_table, border_cache_offset, diagnostic=border_diagnostic,lease=border_lease))
        if border_lease:
            plan.edit(0x104B6B0C,jump(0x104B6B0C,f['aspect_border_visibility'],8),
                      'Independent border yields channel ownership to native visibility writes',
                      bytes.fromhex('2de9f04103281446'))
            plan.edit(0x104B6BDC,jump(0x104B6BDC,f['aspect_border_visibility_batch'],8),
                      'Independent border yields channel ownership to native batch visibility writes',
                      bytes.fromhex('2de9f04116000d46'))
        if border_diagnostic:
            marker="cmp r4,r1;bne done;"
            assert codes['aspect_grid'].count(marker)==1
            codes['aspect_grid']=codes['aspect_grid'].replace(marker,
                marker+"push {r0-r3,r12,lr};movs r0,#3;"+call('aspect_border_note')+
                "pop {r0-r3,r12,lr};",1)
        plan.edit(0x103C0568, jump(0x103C0568, f['aspect_border_pack'], 12),
                  'Opt-in independent aspect border after native rectangle packing',
                  bytes.fromhex('2de9f04116468046f7f025fe'))
        plan.edit(0x104BE73A, jump(0x104BE73A, f['aspect_border_monitor'], 10),
                  'Opt-in aspect border refresh after native grid monitor',
                  bytes.fromhex('70b541f60720a7f720f9'))
    trace_info=None
    if trace:
        if mask:
            from .aspect_mask_trace import install
        else:
            from .aspect_trace import install
        trace_info=install(plan,f,codes,sizes)
    from .aspect_raw_playback import install as install_raw_playback
    raw_playback = install_raw_playback(plan, f, codes, sizes, profiles)
    from .aspect_mask import STATE_SIZE as MASK_STATE_SIZE
    mask_extra = MASK_STATE_SIZE if mask else 0
    if mask:mask_offset = state_offset+16
    return dict(page_offset=page_offset,state_offset=state_offset,
                allocation_size=state_offset+16+(trace_info['bytes'] if trace_info else 0)+(12 if border else 0)+(64 if border_diagnostic else 0)+(4 if border_lease else 0)+mask_extra,
                mask_offset=mask_offset,
                trace=trace_info, border=border, border_cache_offset=border_cache_offset if border else None,
                border_diagnostic=border_diagnostic,
                border_lease=border_lease, mask=mask, mask_trial=mask_trial, mask_state=mask_state,
                raw_playback=raw_playback,
                codes=codes,geometry_table=geometry_table,selectors=selectors,
                function_names=list(codes),shadows=copies,sizes=sizes,profiles=profiles)


def finish(plan, f, extension, identifiers):
    ids=plan.alloc(struct.pack('<7I',*identifiers))
    extension['codes']['aspect_open']=extension['codes']['aspect_open'].replace('ASPECT_IDS;',c('r1',ids))
    for name,source in extension['codes'].items():
        plan.put(f[name],assemble(source,f[name],extension['sizes'][name]))
    plan.features['aspect_profiles']=dict(
        names=list(NAMES),default=0,functions={k:hex(f[k]|1) for k in extension['function_names']},
        page_index=PAGE_INDEX,page_offset=extension['page_offset'],state_offset=extension['state_offset'],
        geometry_table=hex(extension['geometry_table']),settings_file='WL-ASPECT.CFG',
        area_key=hex(AREA_KEY),capture_area_key=hex(CAPTURE_AREA_KEY),profile_area=2,shadows=extension['shadows'],
        config_magic=hex(MAGIC),config_version=2,
        preview_geometry_version=2,
        playback_isolation_version=1,
        raw_preview_playback=extension['raw_playback'],
        file_geometry_callers=[hex(v) for v in FILE_GEOMETRY_CALLERS],
        narrow_source_window_version=0,
        native_area_sync_version=1,
        selection_publication_version=2,
        alternate_trim_table_version=1,
        liveview_window_guard_version=3,
        liveview_native_output_callers=[hex(v) for v in LIVEVIEW_OUTPUT_CALLERS],
        liveview_native_output_profiles=[1,2],
        diagnostic_trace=extension['trace'],
        preview_direct_hooks=[hex(v[0]) for v in PREVIEW_DIRECT_HOOKS],
        preview_capture_callers={k:[hex(v) for v in values] for k,values in PREVIEW_CALLERS.items()},
        preview_policy='Fit actual L aspect in stock preview allocation; four-pixel alignment; capture callers only',
        profiles=extension['profiles'],restore_policy='First custom-menu initialization; default OFF before initialization',
        reference_frame_version=2,
        reference_frame_borders=not (extension['mask'] or extension['mask_trial']),
        preview='Frame and selected thirds/quarters lines share the native grid; narrow bounds project DX coordinates',
        status='experimental_menu_and_geometry_not_camera_validated',physical_verified=False,
        limitations=['Native grid must be enabled; selected grid style is drawn within the visible photo bounds.',
                     'Narrow photo borders outside the native visible source window are clipped, not fitted inside it.',
                     'Mode 0 physical LCD identity, viewport semantics and pixel clearing require hardware validation.',
                     'Preview output dimensions are experimental; no physical playback verification of this image.',
                     'Fixed 160x120 thumbnail path remains stock.',
                     'No filled mask, live sensor/JPEG/NEF/HDMI or concurrent capture validation.',
                     'Alternate RAW/processing branches remain experimental; not a camera-ready update.'])
    if extension['mask'] or extension['mask_trial']:
        from .aspect_mask import (DEFAULT_ALPHA, IMAGE_SLOT, SNAPSHOT, SOURCE_WINDOW, SUBMIT_SITE,
                                  STATE_SIZE, TRIAL_RECTS, VIEW_KIND, OVERLAY_BUSY, PHOTO_BACKEND, UI_SERVICE_PHASE)
        soft = dict(
            version=12, opt_in=True, physically_verified=False,
            native_photo_context_guard=bool(extension['mask']),
            photo_context_fields=dict(view_kind=hex(VIEW_KIND),busy=[hex(v) for v in OVERLAY_BUSY],
                                      backend=hex(PHOTO_BACKEND),ready_flags=hex(SNAPSHOT),
                                      ui_service_phase=hex(UI_SERVICE_PHASE)),
            allowed_ui_service_phases=[1,2],
            retire_before_descriptor_filter=bool(extension['mask']),
            dynamic=bool(extension['mask']), trial=bool(extension['mask_trial']),
            state=(hex(extension['mask_state']) if extension['mask_trial']
                   else 'aspect_get+16:owned-surface-and-rectangles;native-photo-context+projection'),
            state_bytes=STATE_SIZE,
            state_anchor=None, viewport_snapshot=hex(SNAPSHOT), source_window=hex(SOURCE_WINDOW),
            image_slot=hex(IMAGE_SLOT), submit_site=hex(SUBMIT_SITE),
            alpha=DEFAULT_ALPHA, alpha_offset=8, flags_offset=0, count_offset=12,
            rects_offset=16, color_offset=0x50,
            fill_function=hex(f['aspect_mask_fill'] | 1),
            submit_function=hex(f['aspect_mask_submit'] | 1),
            clear_function=hex(f['aspect_mask_erase']|1) if extension['mask'] else None,
            layering='Uniformly fills the lower RGBA live-view image surface at the guarded '
                     'bank-clear boundary; the native UI canvas is composed above it.',
            geometry='Reads the selected profile and stable native viewport/source snapshots; '
                     'reuses the existing photo projection, without depending on grid visibility.',
            coverage='Actual crop edges retain their projection; viewport-clipped photo edges '
                     'continue to the LCD edge so exterior shading has no viewport-end gaps.',
            submit_policy='Dynamic: retire old owned pixels before descriptor filtering when native UI stage is not 1/2; '
                          'native photo-overlay eligibility subset checked before projection and fill; '
                          'no grid preference or snapshot locking API in the worker. '
                          'Only type-0xa03 clears of native 720x480 E-region overlay banks; '
                          'validates bounds and replaces every outside-photo image pixel; '
                          'does not skip opaque residual pixels, which would leave mask holes.',
            erase_policy='Dynamic: bounded try-lock protects private RAM ownership history; '
                         'retire prior rectangles on context/profile/geometry decline or rectangle change. '
                         'Clear only the exact committed mask word on the same current image surface; '
                         'do not dereference a replaced surface or erase rewritten native pixels.',
            qemu_evidence=None,
            limitations=['QEMU display model only; on-camera layering, cache coherency and refresh '
                         'lifecycle are unverified.',
                         'Physical camera validation is still required; QEMU runs the rebuilt image '
                         'through a ROM replay because the machine hash-gates the stock -bios.',
                         f'Alpha {DEFAULT_ALPHA}/255 follows camera feedback; this opacity still '
                         'requires on-camera appearance confirmation.'])
        if extension['mask_trial']:
            soft['trial_rects'] = [list(rect) for rect in TRIAL_RECTS]
            soft['trial_geometry'] = 'Static 2.35:1 sample bands for the QEMU end-to-end trial'
        plan.features['aspect_profiles']['soft_mask'] = soft
        plan.features['aspect_profiles']['limitations'][5] = (
            'Soft mask is an experimental opt-in: QEMU layering is verified, on-camera alpha, '
            'cache coherency and live-view exit/enter lifecycle remain unverified.')
    if extension['border']:
        plan.features['aspect_profiles']['independent_border'] = dict(
            version=1, opt_in=True, physically_verified=False,
            producer='0x103c0568', refresh_monitor='0x104be73a',
            component=3, mask=1, colour_rgb=[255,255,0], strip_pixels=8,
            long_side_stroke_pixels=8, end_cap_stroke_pixels=2,
            stroke_coverage_camera_verified=False,
            dirty_state_word_offset=12,
            cache_state_word_offset=extension['border_cache_offset'],
            diagnostic=extension['border_diagnostic'],
            channel_lease=extension['border_lease'],
            requires_native_rectangle_component_enabled=True,
            free_records_required=4, occupied_records_preserved=True,
            stock_grid_preference_unchanged=True,
            cleanup='Repack stock rectangle bank; no persistent additions to the bank',
            limitations=['Prototype: camera output and refresh lifecycle unverified.',
                         'If four records are not free, skip the additional border.',
                         'Original component visibility and photo readiness gates remain.'])
        plan.features['aspect_profiles']['limitations'][0] = (
            'Independent border requires the native rectangle component and four free records; '
            'the stock grid setting controls the white grid, not the additional yellow strips.')
        if extension['border_diagnostic']:
            from .aspect_border import DIAGNOSTIC_NAMES
            plan.features['aspect_profiles']['independent_border']['diagnostic_counters'] = list(DIAGNOSTIC_NAMES)
        if extension['border_lease']:
            plan.features['aspect_profiles']['independent_border'].update(
                version=2, requires_native_rectangle_component_enabled=False,
                lease_state_word_offset=extension['border_cache_offset']+12+(64 if extension['border_diagnostic'] else 0),
                lease_policy='Enable only disabled empty bank; native visibility writes relinquish ownership; release never hides active native records')
