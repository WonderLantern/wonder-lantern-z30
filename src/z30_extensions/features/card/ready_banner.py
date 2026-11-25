# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Card builds: a short "Wonder Lantern ready · N features" banner on the photo live view.

Load: the card loader calls `banner_start` once after every edit is written (in
the stock IMU task). It only stores "armed", the time and the UI language, and
returns 1. Drawing never happens there.

Draw: the stock image-draw wrapper (0x103BC43E, the graphics dispatcher's
renderer message 0x4201001A) calls the renderer at 0x103BC4D0 (`bl 0x10477C9C;
add sp,#0x5c; pop {pc}`), the same site battery_percent uses. That call jumps to
`banner_draw`, which lets the stock image draw first, then, in photo live view
on the screen layer (target 2, view 0, UI phase 1/2, no setting page), draws the
banner through the same renderer with a descriptor of its own:
  type 1, class 0 (VG_sABGR_8888, 4 bytes a pixel), +0x08 the pixels, source box
  (0, 0, w, h) at +0x10, screen box (x0, y0, x1, y1) at +0x18, alpha 255, angle
  360, +0x4C/+0x50 the buffer width (a multiple of 16) and height (of 8)
-- the field layout of the stock live-view icons (QEMU, tools/probe_image_descriptors_qemu.py).
The live view draws a few bursts of icons a second, so the banner shows within
about half a second of reaching live view.

States (`STATE` word): 1 armed, 2 showing (since `t0`), 3 erase pending, 4 done.
Shown for SHOW_MS from the first live-view draw after loading, however late (the
camera redraws live-view icons only on a change, e.g. a half-press). Erasing paints
the box with colour 0 in VG_BLEND_SRC (vgSeti(0x1104, 0x2000)), as portrait-ui
does on the camera: the stock clear has no effect from a hook. Leaving live view
while it shows defers the erase to the next live-view draw. The screen layer is
the on-screen display only; photos and videos never contain it.
"""
import struct

from ...assembly import assemble, constant as c, invoke as n, jump
from ...platform.fonts import HANS_FONT, HANT_FONT
from ...platform.z30_120 import BASE
from . import ready_banner_image as art

SITE = 0x103BC4D0
EXPECTED = bytes.fromhex('bbf0e4fb' '17b0' '00bd')  # bl IMAGE; add sp,#0x5c; pop {pc}
IMAGE = 0x10477C9C
RECT = 0x1047743C        # r0 -> x, y-up, w, h, rgba, 0, 0
VG_GETI, VG_SETI = 0x1050A47C, 0x1050A406
VG_BLEND, VG_BLEND_SRC = 0x1104, 0x2000
GET_PROPERTY, LANGUAGE_KEY, SIMPLIFIED, TRADITIONAL = 0x10465984, 0x1400, 26, 27
MS_CLOCK = 0xC18EB098
TARGET, OSD_TARGET = 0xC1808758, 2
VIEW_KIND = 0xC18292AC            # u8, 0: photo live view
UI_SERVICE_PHASE = 0xC181F6FC     # u8, 1 or 2 while the live view is up
OVERLAY_BUSY = (0xC2016904, 0xC20168F8)   # both 0 unless a setting page is over the live view
# No time limit for the first showing: the camera's live view only redraws its icons when
# something changes (a half-press, OK), so that first draw can come long after loading
# (2026-11-25: a 30 s limit hid the banner on the camera).
SHOW_MS = 2000
PIXEL_ALIGN = 256
# Repaint trigger (prepare_hooks): off. On the camera (2026-11-25) it never marked a widget
# (the stock shooting-info refresh hardly runs in an idle live view) and, with aligned
# pixels, the stock redraws after loading showed the banner by themselves.
REPAINT_TRIGGER = False
QEMU_TEST_STATE = 0xC1832D40
TOP = 64                          # y (down) of the banner's top edge, below the top icon row
ARMED, SHOWING, ERASE, DONE = 1, 2, 3, 4
BRAND = 'Wonder Lantern'
TEXTS = {'en': ' ready · {n} features', 'zh-Hans': ' 已就绪 · {n} 项功能', 'zh-Hant': ' 已就緒 · {n} 項功能'}
FONTS = {'en': HANS_FONT, 'zh-Hans': HANS_FONT, 'zh-Hant': HANT_FONT}
LANGUAGES = ('en', 'zh-Hans', 'zh-Hant')    # index stored by banner_start


def images(image, count):
    """{language: (width, height, pixels)}, all the same width (a multiple of 16)."""
    texts = {lang: TEXTS[lang].format(n=count) for lang in LANGUAGES}
    width = max(art.text_width(image, FONTS[lang], BRAND, texts[lang]) for lang in LANGUAGES)
    width = (width + 15) & ~15
    return {lang: art.banner(image, FONTS[lang], BRAND, texts[lang], width) for lang in LANGUAGES}


def descriptor(pixels, width, height, x, y):
    d = bytearray(0x5C)
    struct.pack_into('<3I', d, 0, 1, 0, pixels)                      # type 1, class 0, pixels
    struct.pack_into('<4h', d, 0x10, 0, 0, width, height)             # source box
    struct.pack_into('<4h', d, 0x18, x, y, x + width, y + height)     # screen box (x0, y0, x1, y1), y down
    d[0x20] = 0xFF                                                    # alpha
    struct.pack_into('<h', d, 0x40, 360)                              # angle, as the stock icons
    struct.pack_into('<2I', d, 0x4C, width, height)                   # buffer width (16n) and height (8n)
    return bytes(d)


DEBUG_TEXT = 24                   # characters of the readout, NUL included
# Repaint trigger (card builds with portrait-ui, whose UI hooks call ours): the stock live
# view redraws its icons only on a change, so the banner marks a few widgets the stock
# traversal drew recently (+0x44, as portrait-ui v15-v19 does on the camera) once when it
# should appear and once when it should go; the stock traversal then redraws them.
LIVE_CONTAINER, BACKGROUND_DRAW, DIRTY = 0xC2017AB8, 0x104B45DB, 0x44
PHOTO_BACKEND = 0xC181B278
REFRESH, REFRESH_CHECK = 0x1032427E, 0x1032B7D0     # stock shooting-info refresh (UI task)
REFRESH_EXPECTED = bytes.fromhex('2de9f041' '07f0a5fa' '0028')   # push.w {r4-r8,lr}; bl 0x1032B7D0; cmp r0,#0
RING = 8                          # recorded widgets (not the background-only ones)
MARK_SHOW, MARK_ERASE = 1, 2      # state +24 bits


def prepare_hooks(plan):
    """Allocate the banner state (64 bytes) and its two UI-side functions before
    portrait-ui and the menu framework are built (they call or show them).

    state: +0 phase, +4 t0, +8 language, +12 hook calls, +16 target/view/phase/busy,
    +20 draws, +24 marks done, +28 widgets recorded, +32 ring of RING widget pointers.
    """
    return dict(state=plan.alloc(bytes(80)), tick=plan.alloc(bytes(384)), record=plan.alloc(bytes(160)))


# Gate of the repaint trigger: view kind 0 and no setting page, as the draw hook. The UI
# phase (1/2) and photo backend (1) checks of the first trigger never passed in the UI
# update event on the camera (2026-11-25: 124 ticks, no mark), so they are only recorded.
TICK_FAIL_VIEW, TICK_FAIL_PHASE, TICK_FAIL_BACKEND, TICK_FAIL_BUSY0, TICK_FAIL_BUSY1 = 1, 2, 4, 8, 16
TICK_GATE = TICK_FAIL_VIEW | TICK_FAIL_BUSY0 | TICK_FAIL_BUSY1


def _live_view(label):
    """Record the context at state +68.. (r4 = state) and branch to `label` unless view 0 and no
    setting page. +68 failed checks (TICK_FAIL_*), +69 UI phase, +70 photo backend, +71 view."""
    return f'''movs r2,#0;
        {c('r0', VIEW_KIND)}ldrb r1,[r0];strb r1,[r4,#71];cmp r1,#0;it ne;orrne r2,r2,#{TICK_FAIL_VIEW};
        {c('r0', UI_SERVICE_PHASE)}ldrb r1,[r0];strb r1,[r4,#69];subs r1,#1;cmp r1,#1;it hi;orrhi r2,r2,#{TICK_FAIL_PHASE};
        {c('r0', PHOTO_BACKEND)}ldr r1,[r0];strb r1,[r4,#70];cmp r1,#1;it ne;orrne r2,r2,#{TICK_FAIL_BACKEND};
        {c('r0', OVERLAY_BUSY[0])}ldr r1,[r0];cmp r1,#0;it ne;orrne r2,r2,#{TICK_FAIL_BUSY0};
        {c('r0', OVERLAY_BUSY[1])}ldr r1,[r0];cmp r1,#0;it ne;orrne r2,r2,#{TICK_FAIL_BUSY1};
        strb r2,[r4,#68];tst r2,#{TICK_GATE};bne {label};'''


def _hook_code(hooks):
    state = hooks['state']
    # record(widget r0, list r1): called by portrait-ui for every widget the stock traversal
    # draws. Keeps the latest RING distinct live-view widgets with a non-background draw.
    record = f'''push {{r4,lr}};{c('r2', LIVE_CONTAINER + 0x9C)}cmp r1,r2;bne done;
        ldr r2,[r0,#0x4C];cmp r2,#0;beq done;{c('r3', BACKGROUND_DRAW)}cmp r2,r3;beq done;
        {c('r3', state)}ldr r2,[r3,#28];add r12,r3,#32;movs r1,#0;
        seek:cmp r1,r2;bhs add;cmp r1,#{RING};bhs add;ldr r4,[r12,r1,lsl #2];cmp r4,r0;beq done;adds r1,#1;b seek;
        add:and r1,r2,#{RING - 1};str r0,[r12,r1,lsl #2];adds r2,#1;str r2,[r3,#28];
        done:pop {{r4,pc}};'''
    # tick(): portrait-ui's UI update event (UI task, ~5 a second).
    tick = f'''push {{r4-r6,lr}};{c('r4', state)}ldr r0,[r4,#64];adds r0,#1;str r0,[r4,#64];ldr r0,[r4];ldr r5,[r4,#24];
        cmp r0,#{ARMED};bne erase_check;tst r5,#{MARK_SHOW};bne out;movs r6,#{MARK_SHOW};b live;
        erase_check:cmp r0,#{SHOWING};bne out;tst r5,#{MARK_ERASE};bne out;
        {c('r0', MS_CLOCK)}ldr r0,[r0];ldr r1,[r4,#4];subs r0,r0,r1;movw r1,#{SHOW_MS};cmp r0,r1;blo out;
        movs r6,#{MARK_ERASE};
        live:{_live_view('out')}
        ldr r2,[r4,#28];cmp r2,#0;beq out;cmp r2,#{RING};it hi;movhi r2,#{RING};
        add r1,r4,#32;movs r3,#0;
        mark:cmp r3,r2;bhs marked;ldr r0,[r1,r3,lsl #2];
        lsrs r12,r0,#29;cmp r12,#4;blo next;cmp r12,#7;bhs next;tst r0,#3;bne next;
        movs r12,#1;str r12,[r0,#{DIRTY}];
        next:adds r3,#1;b mark;
        marked:orr r5,r5,r6;str r5,[r4,#24];
        out:pop {{r4-r6,pc}};'''
    return record, tick


def prepare_debug(plan):
    """Allocate the diagnostic readout before the menu framework is built (it shows it).

    Returns {'state', 'text', 'record'}: the banner state (32 bytes with counters),
    the Thumb function that formats the readout and returns its text record in r0,
    and that record (ASCII and UTF-16 buffers, as ui_text.TextResources records).
    """
    ascii_buffer, utf16 = plan.alloc(bytes(DEBUG_TEXT)), plan.alloc(bytes(2 * DEBUG_TEXT))
    state = plan.banner_hooks['state'] if getattr(plan, 'banner_hooks', None) else plan.alloc(bytes(80))
    return dict(state=state, text=plan.alloc(bytes(512)), ascii=ascii_buffer, utf16=utf16,
                record=plan.alloc(struct.pack('<4I', ascii_buffer, utf16, utf16, utf16)))


def _debug_text(debug):
    """'P<phase>M<marks>W<widgets>K<ticks>D<draws>R<failed checks>U<ui phase>X<backend>' (21 characters)."""
    def ch(char):
        return f"movs r0,#{ord(char)};strb r0,[r4],#1;strh r0,[r5],#2;"
    def hexd(load, digits):
        out = load + 'mov r7,r0;'
        for i in reversed(range(digits)):
            out += (f"lsrs r0,r7,#{4 * i};" if i else 'mov r0,r7;') + ("and r0,r0,#15;cmp r0,#10;ite lo;"
                    "addlo r0,#48;addhs r0,#55;strb r0,[r4],#1;strh r0,[r5],#2;")
        return out
    s = debug['state']
    # 19 characters: the status page's value column shows about 21 (camera, 2026-11-25).
    # R: checks the trigger saw failing (TICK_FAIL_*), U its UI phase, X its photo backend.
    body = (ch('P') + hexd('ldr r0,[r6];', 1) + ch('M') + hexd('ldr r0,[r6,#24];', 1)
            + ch('W') + hexd('ldr r0,[r6,#28];', 2) + ch('K') + hexd('ldr r0,[r6,#64];', 2)
            + ch('D') + hexd('ldr r0,[r6,#20];', 2) + ch('R') + hexd('ldrb r0,[r6,#68];', 2)
            + ch('U') + hexd('ldrb r0,[r6,#69];', 1) + ch('X') + hexd('ldrb r0,[r6,#70];', 2))
    return f'''push {{r4-r7,lr}};{c('r6', s)}{c('r4', debug['ascii'])}{c('r5', debug['utf16'])}
        {body}movs r0,#0;strb r0,[r4];strh r0,[r5];{c('r0', debug['record'])}pop {{r4-r7,pc}};'''


def add(plan, count, qemu_test=False, debug=None):
    """qemu_test: a flash build for tools/verify_ready_banner_qemu.py, which calls banner_start itself.
    debug: prepare_debug's allocation; the draw hook then counts calls, draws and the last context."""
    if not getattr(plan, 'card', False) and not qemu_test:
        raise ValueError('The ready banner exists only in SD card builds')
    if any(edit.offset == SITE - BASE for edit in plan.edits):
        raise ValueError('The ready banner and battery-percent use the same draw call')
    pictures = images(plan.image, count)
    width, height = next(iter(pictures.values()))[:2]
    if width % 16 or height % 8:
        raise ValueError('Banner buffers must be 16 x 8 aligned')
    x = (720 - width) // 2
    # The GPU needs aligned image data: on the camera the banner showed only from the build
    # whose pixels happened to sit on a 128-byte boundary (offsets 8, 28 and 40 mod 64 drew
    # nothing, 2026-11-25). The card loader puts the tail on a 64 KiB boundary, so aligning
    # the offset in the tail is enough.
    if getattr(plan, 'card', False) and plan.tail_base % PIXEL_ALIGN:
        raise ValueError('The tail base is not aligned for the banner pixels')
    pixel_tables = []
    for lang in LANGUAGES:
        plan.alloc(bytes((-plan.address) % PIXEL_ALIGN))
        pixel_tables.append(plan.alloc(pictures[lang][2]))
    table = plan.alloc(struct.pack('<3I', *pixel_tables))
    template = plan.alloc(descriptor(0, width, height, x, TOP))
    # phase, t0, language index, pad. The card tail is RAM; a flash test build keeps the
    # state in the unused 16 bytes of stock menu record 0 (STATE_ANCHOR + 0x1C .. +0x2B).
    hooks = getattr(plan, 'banner_hooks', None)
    state = (debug['state'] if debug else hooks['state'] if hooks else
             QEMU_TEST_STATE if qemu_test else plan.alloc(bytes(64)))
    if hooks:
        record, tick = _hook_code(hooks)
        plan.put(hooks['record'], assemble(record, hooks['record'], 160))
        plan.put(hooks['tick'], assemble(tick, hooks['tick'], 384))
        # The tick runs at the start of the stock shooting-info refresh (UI task), before its
        # early return: portrait-ui's UI update event does not run in an idle live view
        # (camera, 2026-11-25). Window: push.w {r4-r8,lr}; bl 0x1032B7D0; cmp r0,#0.
        if plan.image.read(REFRESH, len(REFRESH_EXPECTED)) != REFRESH_EXPECTED:
            raise ValueError('Unexpected shooting-info refresh prologue')
        stub = plan.alloc(bytes(96))
        plan.put(stub, assemble(f'''push {{r0-r3,r12,lr}};mrs r0,apsr;push {{r0,r1}};{n(hooks['tick'])}
            pop {{r0,r1}};msr apsr_nzcvq,r0;pop {{r0-r3,r12,lr}};
            push.w {{r4-r8,lr}};{n(REFRESH_CHECK)}cmp r0,#0;
            ldr.w pc,={(REFRESH + len(REFRESH_EXPECTED)) | 1};''', stub, 96))
        plan.edit(REFRESH, jump(REFRESH, stub, len(REFRESH_EXPECTED)), 'Ready banner: repaint trigger in the shooting-info refresh',
                  REFRESH_EXPECTED)
    if debug:
        plan.put(debug['text'], assemble(_debug_text(debug), debug['text'], 512))
    # Counters in every card build (the camera-verified hook code of 2026-11-25 has them):
    # +12 hook calls, +16 target, view, UI phase, busy bits, +20 draws. Only the readout
    # (debug) is optional. The QEMU test state has 16 bytes, so no counters there.
    count_call = ('' if qemu_test else f'''ldr r0,[r4,#12];adds r0,#1;str r0,[r4,#12];
        {c('r0', TARGET)}ldrb r0,[r0];strb r0,[r4,#16];{c('r0', VIEW_KIND)}ldrb r0,[r0];strb r0,[r4,#17];
        {c('r0', UI_SERVICE_PHASE)}ldrb r0,[r0];strb r0,[r4,#18];movs r1,#0;
        {c('r0', OVERLAY_BUSY[0])}ldr r0,[r0];cmp r0,#0;it ne;orrne r1,r1,#1;
        {c('r0', OVERLAY_BUSY[1])}ldr r0,[r0];cmp r0,#0;it ne;orrne r1,r1,#2;strb r1,[r4,#19];''')
    count_draw = '' if qemu_test else 'ldr r0,[r4,#20];adds r0,#1;str r0,[r4,#20];'
    start = plan.alloc(bytes(96))
    plan.put(start, assemble(f'''
        push {{r4,lr}};
        {c('r4', state)}{c('r0', MS_CLOCK)}ldr r0,[r0];str r0,[r4,#4];
        {c('r0', LANGUAGE_KEY)}{n(GET_PROPERTY)}
        movs r1,#0;cmp r0,#{SIMPLIFIED};it eq;moveq r1,#1;cmp r0,#{TRADITIONAL};it eq;moveq r1,#2;
        str r1,[r4,#8];dmb ish;movs r0,#{ARMED};str r0,[r4];
        movs r0,#1;pop {{r4,pc}};
    ''', start, 96))
    if not qemu_test:
        plan.on_load(start)
    draw = plan.alloc(bytes(640))
    plan.put(draw, assemble(f'''
        {n(IMAGE)}
        push {{r4-r7}};sub sp,#0x60;
        {c('r4', state)}{count_call}ldr r6,[r4];
        cmp r6,#{ARMED};blo out;cmp r6,#{ERASE};bhi out;
        {c('r0', TARGET)}ldrb r0,[r0];cmp r0,#{OSD_TARGET};bne away;
        {c('r0', VIEW_KIND)}ldrb r0,[r0];cmp r0,#0;bne away;
        {c('r0', UI_SERVICE_PHASE)}ldrb r0,[r0];cmp r0,#1;beq phase_ok;cmp r0,#2;bne away;
        phase_ok:
        {c('r0', OVERLAY_BUSY[0])}ldr r0,[r0];cmp r0,#0;bne away;
        {c('r0', OVERLAY_BUSY[1])}ldr r0,[r0];cmp r0,#0;bne away;
        {c('r0', MS_CLOCK)}ldr r5,[r0];ldr r7,[r4,#4];subs r7,r5,r7;
        cmp r6,#{ERASE};beq erase;
        cmp r6,#{SHOWING};beq showing;
        str r5,[r4,#4];movs r7,#0;movs r6,#{SHOWING};str r6,[r4];
        showing:
        movw r0,#{SHOW_MS};cmp r7,r0;bhs erase;
        mov r0,sp;{c('r1', template)}movs r2,#0x5C;{n(0x10199BDC, thumb=False)}
        ldr r1,[r4,#8];cmp r1,#2;it hi;movhi r1,#0;
        {c('r2', table)}ldr r1,[r2,r1,lsl #2];str r1,[sp,#8];
        mov r0,sp;{n(IMAGE)}
        {count_draw}b out;
        erase:
        movw r0,#{VG_BLEND};{n(VG_GETI)}mov r6,r0;
        movw r0,#{VG_BLEND};movw r1,#{VG_BLEND_SRC};{n(VG_SETI)}
        movw r0,#{x};str r0,[sp];movw r0,#{480 - TOP - height};str r0,[sp,#4];
        movw r0,#{width};str r0,[sp,#8];movs r0,#{height};str r0,[sp,#12];
        movs r0,#0;str r0,[sp,#16];str r0,[sp,#20];str r0,[sp,#24];
        mov r0,sp;{n(RECT)}
        movw r0,#{VG_BLEND};mov r1,r6;{n(VG_SETI)}
        expire:
        movs r0,#{DONE};str r0,[r4];b out;
        away:
        cmp r6,#{SHOWING};bne out;movs r0,#{ERASE};str r0,[r4];
        out:
        add sp,#0x60;pop {{r4-r7}};
        add sp,#0x5c;pop {{pc}};
    ''', draw, 640))
    plan.edit(SITE, jump(SITE, draw, len(EXPECTED)), 'Ready banner on the live view', EXPECTED)
    plan.features['ready_banner'] = dict(
        site=hex(SITE), draw=hex(draw | 1), on_load=hex(start | 1), state=hex(state), template=hex(template), table=hex(table),
        pixels={lang: hex(a) for lang, a in zip(LANGUAGES, pixel_tables)}, size=[width, height], box=[x, TOP, width, height],
        features=count, texts={lang: BRAND + TEXTS[lang].format(n=count) for lang in LANGUAGES},
        show_ms=SHOW_MS, live_view='photo live view only (view 0, screen layer)',
        first_draw='the first live-view icon redraw after loading (on the camera: a half-press or OK)',
        pixel_bytes=sum(len(p[2]) for p in pictures.values()), camera_verified=False,
        repaint=(dict(tick=hex(hooks['tick'] | 1), record=hex(hooks['record'] | 1), ring=RING,
                      via='portrait-ui update event and traversal draw hooks') if hooks else None),
        debug=(dict(text=hex(debug['text'] | 1), record=hex(debug['record']),
                    readout='P<phase>M<marks>W<widgets>K<ticks>D<draws>R<failed checks>U<ui phase>X<photo backend>')
               if debug else None))
