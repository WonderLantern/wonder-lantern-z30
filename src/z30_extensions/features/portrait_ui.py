# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Portrait layout for the photo live-view information, drawn by the stock renderer.

The GUI task turns every widget into a few OpenVG commands: images, rectangles,
clears, rounded outlines and text. With the camera held upright, these entries
move each element to a portrait position and turn it by 90 or 270 degrees onto
the physical landscape panel. The stock layout code keeps running; only where
and in which direction things are drawn changes.

The approach and the per-command rewrites follow the camera-verified Z 5
implementation (honokaoshi83-max/z5-extensions, docs/PORTRAIT_UI.md). The Z30
renderer sits at the same relative offsets as the Z 5 one; every address here
was located again in the Z30 C 1.20 module and is checked against stock bytes.

Off by default and inert while off. The live-view mask, grid, zebra and the
42-record rectangle channel do not pass these entries, so they stay where they
belong: in picture coordinates.
"""
import struct
from ..assembly import assemble, constant as c, invoke as n, jump

# Renderer entries (descriptor formats as on the Z 5).
RECT = 0x1047743C     # rectangle; r0 -> x, y-up, w, h, rgba
CLEAR = 0x1047797E    # clear; r0 -> x, y-up, w, h
FRAME = 0x104777FC    # rounded outline; r0-r3 = x, y-up, w, h
IMAGE = 0x10477C9C    # image 1: reads the angle at +0x40
IMAGE2 = 0x104787F2   # image 2: also reads the angle; full-layer copies come here
IMAGE3 = 0x10479022   # image 3: no angle; redrawn through image 1
TEXT_CALL = 0x1054E652  # bl vgLoadMatrix with the text object's 3x3 matrix
LOAD_MATRIX = 0x1050A336
# The clear entry draws some clears with the rectangle entry and the same
# descriptor; that inner call must not be moved a second time.
CLEAR_INNER_RECT_RETURN = 0x104779CF

ORIENTATION = 0x1021B5F6  # 0 level, 1 and 2 upright (side to be confirmed), 3 inverted; also used by video-rot
TARGET = 0xC1808758       # u8, current render target; read by 103BD30A for the viewport table
OSD_TARGET = 2            # the on-screen layer on the Z 5; Z30 value not yet read on the camera
# The photo live-view context the aspect mask already relies on (camera-verified in v42).
VIEW_KIND = 0xC18292AC    # u8; 0 is photo live view (indexes the UI handler table at 10310BF8)
UI_SERVICE_PHASE = 0xC181F6FC
PHOTO_BACKEND = 0xC181B278
# Native UI properties 2d and 2a; both zero in plain photo live view (the aspect
# mask needs this on the camera). A setting page opened from live view must
# stay landscape: candidate signal, confirmed by the readout on the camera.
OVERLAY_BUSY = (0xC2016904, 0xC20168F8)

# GUI-task repaint. 1032427E is the stock shooting-info refresh; at 1032431C
# it asks 10453FAC(10453AC3, 3, mask) to redraw group 3, with mask 7 for
# everything. After the layout changed, the next pass asks for 7. The request
# takes a GUI lock, so it is never made from the render thread.
REFRESH_REQUEST = 0x1032431C
REFRESH_EXPECTED = bytes.fromhex('ca4803212ff144fe')
REDRAW = 0x10453FAC
REDRAW_PREDICATE = 0x10453AC3
# 10453FAC(predicate, what, screens): 10453D14 reads five content bits from
# "what" (stock asks 3 here) and the screens from the third argument. v2 forced
# only the screens (7) and the camera still needed a half press; v3 asks for
# all five content bits once after the layout changed.
REDRAW_ALL = 0x1F
REDRAW_EXPECTED = bytes.fromhex('70b5c84d0e461446')  # push {r4-r6,lr}; ldr r5,[pc,#0x320]; mov r6,r1; mov r4,r2
REDRAW_LITERAL, REDRAW_R5 = ((0x10453FAE + 4) & ~3) + 0x320, 0xC182012C
REQ_MAGIC, REQ_CAPACITY = 0x31525657, 64
# The stock shooting-info refresh asks about 8 times a second; it is only counted
# (header +8) so the ring keeps the other requests, e.g. the one a half press makes.
PERIODIC_CALLER = 0x10324325
# UI event dispatch wrappers: each indexes the handler table C201A36C with the UI
# state byte and tail-calls a handler slot. Record-only, to see how a half press
# reaches the shooting page. name, site, stock window, replay, resume.
UI_STATE, UI_TABLE = 0xC18292AC, 0xC201A36C
EVT_MAGIC, EVT_CAPACITY = 0x31455657, 128
POSE_BYTE = 0xC1D41318 + 0x90  # what the orientation getter returns on the camera
# Action codes read on the camera at 10310BF8 (2026-11-11): 0x13 half press,
# 0x00 release. The stock page repaints everything on 0x13. 0x49 once arrived
# after a turn but not reliably (v9 on the camera), so v10 triggers from the
# periodic update event instead (see the event hooks in add()).
ACTION_HALF, ACTION_RELEASE, ACTION_TURNED = 0x13, 0x00, 0x49
HOLD_UPDATES = 5  # update events (~5 a second) between the injected press and release
R_HOLD = 32       # runtime word (was the v3 forced-repaint count)
# v11's injected half press reached the page (held ~0.6 s, no AF) but did not
# repaint it on the camera, so it is off; the update entry only records again.
INJECT_HALF_PRESS = False
# Redraw traversal 102BD3BA(container, callback): walks container+0x9C and calls
# the draw function (+0x4C) of every widget whose flag (+0x44) is set; 102BD4C4
# (entry 10454226) sets that flag on every widget. Record-only here, to find the
# live-view containers so a later build can mark them all after a turn.
TRAVERSE, TRAVERSE_EXPECTED = 0x102BD3BA, bytes.fromhex('2de9f843' '9c30' '8946' '0027')
TRV_MAGIC, TRV_CAPACITY = 0x31545657, 64
# The live-view info container, read over USB on the camera (v12 traversal trace,
# 2026-11-13): every traversal in photo live view used 0xC2017AB8 (465 widgets) with
# callback 10453AFD; six stock literals at 10325074..10327314 point at it. After a
# turn, the update entry does what the stock page refresh (10457274) does: mark
# every widget (10454226) and redraw them now (10454218).
LIVE_CONTAINER, REDRAW_CALLBACK = 0xC2017AB8, 0x10453AFD
MARK_ALL, REDRAW_NOW = 0x10454226, 0x10454218
MARK_ALL_EXPECTED = bytes.fromhex('69f64db9')                    # b.w 102BD4C4
REDRAW_NOW_EXPECTED = bytes.fromhex('2d4a743a126a02b1002101f0bebe')
REPAINT_BY_MARKING = True
REDRAW_IMMEDIATELY = False  # True hung the camera (v13)
# v14 marked all 465 widgets of the container: QEMU stopped on a prefetch abort,
# so some of them cannot be drawn in the current state. v15 records the widgets the
# stock traversal really draws in that container (call site 102BD3F8) and, after a
# turn, sets the flag (+0x44, as 102BD4C4 does) on those only. Leaving live view
# empties the list; the stock redraw on return fills it again.
MARK_RECORDED = True
# Draw function of the live-view widgets that only clear their box (camera, v17 snapshots).
BACKGROUND_DRAW = 0x104B45DB
PASS_GAP = 8  # most update events (~5 a second) between the passes; normally the traversal (~3 a second) ends the wait first
DRAW_SITE, DRAW_EXPECTED = 0x102BD3F0, bytes.fromhex('e26c12b149462046')  # ldr r2,[r4,#0x4c]; cbz r2; mov r1,r9; mov r0,r4
DRAW_CALL, DRAW_SKIP = 0x102BD3F8, 0x102BD3FA
WID_CAPACITY = 256  # 234 widgets of the container have a draw function (camera, 2026-11-13); 128 filled up
EVENT_HOOKS = (
    ('action', 0x10310BF8, '014678480278784850f82200'[:16], f'mov r1,r0;ldr r0,={UI_STATE:#x};ldrb r2,[r0];ldr r0,={UI_TABLE:#x};'),
    ('update', 0x10310C08, '10b5f4f7c9fa0028', 'push {r4,lr};CALL103051A0cmp r0,#0;'),
    ('pair', 0x10310C26, '03466c4802786c4850f82200', f'mov r3,r0;ldr r0,={UI_STATE:#x};ldrb r2,[r0];ldr r0,={UI_TABLE:#x};ldr.w r0,[r0,r2,lsl #2];'),
    ('slot20', 0x10310C3A, '0146674802786748' + '50f82200', f'mov r1,r0;ldr r0,={UI_STATE:#x};ldrb r2,[r0];ldr r0,={UI_TABLE:#x};ldr.w r0,[r0,r2,lsl #2];'),
)
REPAINT_HOOK = False  # see add(): disabled after the v3 camera freeze
# Draw log (see portrait_menu.LOG): one 16-byte record per command while upright.
LOG, LOG_MAGIC, LOG_CAPACITY = 112, 0x314C5657, 256

KINDS = dict(image=0, rect=1, clear=2, frame=3, text=5, image2=6, image3=7)

# name, site, stock bytes of the displaced window, replayed instructions, resume
HOOKS = (
    ('rect', RECT, '2de9f041044688b0','push.w {r4-r8,lr};mov r4,r0;sub sp,#0x20;'),
    ('clear', CLEAR, 'f0b589b00446002041f22116','push {r4-r7,lr};sub sp,#0x24;mov r4,r0;movs r0,#0;movw r6,#0x1121;'),
    ('frame', FRAME, '2de9f04f87b08a46', 'push.w {r4-r11,lr};sub sp,#0x1c;mov r10,r1;'),
    ('image', IMAGE, '2de9f04fb7b04ff0000a', 'push.w {r4-r11,lr};sub sp,#0xdc;mov.w r10,#0;'),
    ('image2', IMAGE2, '2de9f04fbdb00446406a', 'push.w {r4-r11,lr};sub sp,#0xf4;mov r4,r0;ldr r0,[r0,#0x24];'),
    ('image3', IMAGE3, '2de9f04faa4904464068', None),
)
TEXT_EXPECTED = bytes.fromhex('bbf770fe022141f22210')

SIZES = dict(portrait_f2fix=96, portrait_fix2f=96, portrait_focus=96, portrait_locate=320,
             portrait_rect=224, portrait_image=320, portrait_image3=320, portrait_text=384,
             portrait_hook=768, portrait_log=512)
# Runtime words after the switch word (see portrait_menu): mode, pending full
# clear, repaint wanted, refresh passes, forced repaints, then the readout.
R_MODE, R_PENDING, R_REPAINT, R_PASSES, R_FORCED = 16, 20, 24, 28, 32
R_KINDS, R_TARGETS, R_PHASES, R_BUSY, R_COPIES = 36, 40, 44, 48, 52
STUB_SIZE = 96

# Portrait layout, in a 480x720 y-down frame. Bands and columns follow the
# element's centre, so an icon, its outline and its clear move together.
TOP_BAND, BOTTOM_BAND = 52, 428        # centre above / at or below: the info bars
LEFT_COLUMN, RIGHT_COLUMN = 120, 600   # centre left of / at or right of
SHIFT_Y = 120                          # middle band moves to the middle of the tall screen
FULL_W, FULL_H = 600, 400              # full-screen commands stay as they are
THIN = 3                               # clears this thin stay in place (AF frame lines)
# Each 720-wide bar becomes two full-size rows: elements whose left edge is left
# of SPLIT_X keep their x, the rest move 240 left into the second row. Text is
# placed by where it starts, which is why the left edge decides, not the centre.
SPLIT_X, ROW = 360, 52


# Per-element rules first, from the live-view boxes read on the camera over USB
# (tools/portrait_live.py, 2026-11-11; landscape centre cx, cy). Each moves
# every element whose centre falls inside by dx, dy. Status 2 marks bar rows.
# Elements no rule matches use the zones below.
LAYOUT = (
    # cx0, cx1, cy0, cy1,  dx,  dy, status
    (0, 200, 0, 52, 0, 0, 2),            # top row 1: shooting mode
    (200, 490, 0, 52, -166, 0, 2),       # top row 1: release, AF mode, AF area, next icon, packed left
    (490, 720, 0, 52, -506, 44, 2),      # top row 2: the three right-hand icons, left aligned
    (600, 720, 40, 90, -238, -44, 1),    # image quality (FINE L) to the right end of top row 1
    (300, 420, 360, 428, -120, 187, 1),  # "OK: focus point" hint, lower: just above the bottom rows
    # Bottom row: shutter, aperture, ISO. A long shutter value (1/1000) brings its own box
    # (x 129..254), so the four groups (482 px) no longer fit one row: shots remaining
    # moves to the row above, right of the metering and exposure-compensation icons.
    (0, 100, 428, 480, 0, 188, 2),       # metering icon
    (100, 262, 428, 480, -127, 240, 2),  # shutter speed and its box (x 129..254)
    (262, 380, 428, 480, -127, 240, 2),  # aperture box (x 267..369)
    (380, 430, 428, 480, -238, 188, 2),  # exposure-compensation icon, row above
    (430, 592, 428, 480, -185, 240, 2),  # ISO box (x 437..588)
    (592, 720, 428, 480, -224, 188, 2),  # shots remaining (x 594..698), row above, right
)


def locate(x, y, w, h):
    """Python model of portrait_locate: (status, px, py, pw, ph); status 0 keeps the command, 2 is a bar."""
    if w >= FULL_W and h >= FULL_H:
        return 0, x, y, w, h
    cy = y + (h >> 1)
    cx = x + (w >> 1)
    for cx0, cx1, cy0, cy1, dx, dy, status in LAYOUT:
        if cx0 <= cx < cx1 and cy0 <= cy < cy1:
            return status, x + dx, y + dy, w, h
    if cy < TOP_BAND:
        return (2, x, y, w, h) if x < SPLIT_X else (2, x - 240, y + ROW, w, h)
    if cy >= BOTTOM_BAND:
        return (2, x, y + 240 - ROW, w, h) if x < SPLIT_X else (2, x - 240, y + 240, w, h)
    cx = x + (w >> 1)
    if cx < LEFT_COLUMN:
        return 1, x, y + SHIFT_Y, w, h
    if cx >= RIGHT_COLUMN:
        return 1, x - 240, y + SHIFT_Y, w, h
    return 1, x - 120, y + SHIFT_Y, w, h


def panel_box(ori, px, py, pw, ph):
    """Portrait box to the physical landscape rectangle (x, y-up, w, h)."""
    if ori == 2:
        return py, px, ph, pw
    return 720 - py - ph, 480 - px - pw, ph, pw


def sources(f, state_offset, runtime, layout):
    if runtime != R_MODE:
        raise ValueError('Runtime words moved')
    k = KINDS
    code = {}
    # IEEE single <-> signed 24.8, for the glyph matrix only. No VFP in a GUI hook.
    code['portrait_f2fix'] = '''
        ubfx r1,r0,#23,#8;sub r1,r1,#127;cmn r1,#8;blt zero;
        ubfx r2,r0,#0,#23;orr r2,r2,#0x800000;
        subs r3,r1,#15;bge left;rsbs r3,r3,#0;lsr r2,r2,r3;b sign;
        left:cmp r3,#8;bge zero;lsl r2,r2,r3;
        sign:cmp r0,#0;it lt;rsblt r2,r2,#0;mov r0,r2;bx lr;
        zero:movs r0,#0;bx lr;'''
    code['portrait_fix2f'] = '''
        cmp r0,#0;beq done;
        ands r3,r0,#0x80000000;it ne;rsbne r0,r0,#0;
        clz r1,r0;rsb r1,r1,#31;subs r2,r1,#23;blt small;
        lsr r0,r0,r2;b pack;
        small:rsbs r2,r2,#0;lsl r0,r0,r2;
        pack:bfc r0,#23,#9;add r1,r1,#119;orr r0,r0,r1,lsl #23;orr r0,r0,r3;
        done:bx lr;'''
    # Focus frames are red or green and belong to the picture.
    code['portrait_focus'] = '''
        lsrs r1,r0,#24;ubfx r2,r0,#16,#8;ubfx r3,r0,#8,#8;
        cmp r1,#0xc0;blo green;cmp r2,#0x80;bhs green;cmp r3,#0x60;blo yes;
        green:cmp r2,#0xc0;blo no;cmp r1,#0x40;bhs no;cmp r3,#0x40;bhs no;
        yes:movs r0,#1;bx lr;no:movs r0,#0;bx lr;'''
    # r0 -> {x, y-down, w, h}, rewritten to the portrait box; returns 0 keep, 1 moved, 2 scaled.
    code['portrait_locate'] = f'''push {{r4-r8,lr}};mov r12,r0;ldm r0,{{r0-r3}};
        movw r4,#{FULL_W};cmp r2,r4;blt sized;movw r4,#{FULL_H};cmp r3,r4;bge keep;
        sized:add r4,r0,r2,asr #1;add r5,r1,r3,asr #1;{c('r6',layout)}
        rule:ldrsh r7,[r6];movw r8,#0x7fff;cmp r7,r8;beq zones;
        cmp r4,r7;blt next;ldrsh r7,[r6,#2];cmp r4,r7;bge next;
        ldrsh r7,[r6,#4];cmp r5,r7;blt next;ldrsh r7,[r6,#6];cmp r5,r7;bge next;
        ldrsh r7,[r6,#8];add r0,r0,r7;ldrsh r7,[r6,#10];add r1,r1,r7;
        stm r12,{{r0-r3}};ldrsh r0,[r6,#12];pop {{r4-r8,pc}};
        next:adds r6,#16;b rule;
        zones:add r4,r1,r3,asr #1;cmp r4,#{TOP_BAND};blt top;
        movw r5,#{BOTTOM_BAND};cmp r4,r5;bge bottom;
        add r4,r0,r2,asr #1;cmp r4,#{LEFT_COLUMN};blt middle;
        movw r5,#{RIGHT_COLUMN};cmp r4,r5;bge right;
        sub r0,r0,#120;b middle;
        right:sub r0,r0,#240;
        middle:add r1,r1,#{SHIFT_Y};stm r12,{{r0-r3}};movs r0,#1;pop {{r4-r8,pc}};
        top:movw r5,#{SPLIT_X};cmp r0,r5;blt bar;sub r0,r0,#240;add r1,r1,#{ROW};b bar;
        bottom:movw r5,#{SPLIT_X};cmp r0,r5;blt lower;sub r0,r0,#240;add r1,r1,#240;b bar;
        lower:add r1,r1,#{240 - ROW};
        bar:stm r12,{{r0-r3}};movs r0,#2;pop {{r4-r8,pc}};
        keep:movs r0,#0;pop {{r4-r8,pc}};'''
    # r0 -> x, y-up, w, h (+16 rgba for rectangles); r1 orientation; r2 kind.
    # Thin clears (w or h <= THIN) stay where they are: on the live-view page they are
    # only the eight 2-px lines that erase the AF-area corner frame (camera log,
    # 2026-11-11, the same as the Z 5 found). Moving them left the frame behind.
    code['portrait_rect'] = f'''push {{r4-r6,lr}};sub sp,#16;mov r4,r0;mov r5,r1;
        cmp r2,#{k['clear']};bne not_thin;ldr r0,[r4,#8];cmp r0,#{THIN};ble done;ldr r0,[r4,#12];cmp r0,#{THIN};ble done;
        not_thin:cmp r2,#{k['rect']};bne geometry;ldr r0,[r4,#16];{n(f['portrait_focus'])}cmp r0,#0;bne done;
        geometry:ldm r4,{{r0-r3}};rsb r1,r1,#480;sub r1,r1,r3;
        stm sp,{{r0-r3}};mov r0,sp;{n(f['portrait_locate'])}cmp r0,#0;beq done;mov r12,r0;
        ldm sp,{{r0-r3}};cmp r2,#480;ble placed;movs r0,#0;movw r2,#480;
        cmp r12,#2;bne placed;add r3,r3,#{ROW};
        placed:cmp r5,#2;bne other;str r1,[r4];str r0,[r4,#4];b sizes;
        other:rsb r6,r1,#720;sub r6,r6,r3;str r6,[r4];rsb r6,r0,#480;sub r6,r6,r2;str r6,[r4,#4];
        sizes:str r3,[r4,#8];str r2,[r4,#12];
        done:add sp,#16;pop {{r4-r6,pc}};'''
    # r0 -> image 1/2 descriptor; r1 orientation; r2 kind.
    code['portrait_image'] = f'''push {{r4-r7,lr}};sub sp,#20;mov r4,r0;mov r5,r1;
        ldrh r0,[r4,#0x40];cmp r0,#0;beq upright_source;movw r1,#360;cmp r0,r1;bne done;
        upright_source:
        ldrsh r0,[r4,#0x14];ldrsh r1,[r4,#0x10];sub r6,r0,r1;
        ldrsh r0,[r4,#0x16];ldrsh r1,[r4,#0x12];sub r7,r0,r1;
        cmp r6,#0;ble done;cmp r7,#0;ble done;
        cmp r2,#{k['image2']};bne tint;movw r0,#700;cmp r6,r0;blt tint;movw r0,#400;cmp r7,r0;bge done;
        tint:ldr r0,[r4,#0x20];{n(f['portrait_focus'])}cmp r0,#0;bne done;
        ldr r0,[r4];cmp r0,#1;beq box;
        ldrsh r0,[r4,#0x1c];ldrsh r1,[r4,#0x18];sub r6,r0,r1;
        ldrsh r0,[r4,#0x1e];ldrsh r1,[r4,#0x1a];sub r7,r0,r1;
        cmp r6,#0;ble done;cmp r7,#0;ble done;
        box:ldrsh r0,[r4,#0x18];ldrsh r1,[r4,#0x1a];mov r2,r6;mov r3,r7;
        stm sp,{{r0-r3}};mov r0,sp;{n(f['portrait_locate'])}cmp r0,#0;beq done;
        ldm sp,{{r0-r3}};movs r6,#0;str r6,[r4];
        cmp r5,#2;bne other;
        strh r1,[r4,#0x18];rsb r6,r0,#480;sub r7,r6,r2;strh r7,[r4,#0x1a];
        add r7,r1,r3;strh r7,[r4,#0x1c];strh r6,[r4,#0x1e];movs r6,#90;b angle;
        other:rsb r6,r1,#720;sub r7,r6,r3;strh r7,[r4,#0x18];strh r0,[r4,#0x1a];
        strh r6,[r4,#0x1c];add r7,r0,r2;strh r7,[r4,#0x1e];movw r6,#270;
        angle:strh r6,[r4,#0x40];
        done:add sp,#20;pop {{r4-r7,pc}};'''
    # r0 -> the stub's saved registers. Renderer 3 has no angle: its descriptor
    # is rewritten for renderer 1, which is called (through its own hook) instead.
    code['portrait_image3'] = f'''push {{r4-r6,lr}};sub sp,#0x60;mov r6,r0;ldr r4,[r6];cmp r4,#0;beq done;
        ldr r0,[r4,#0x18];asr r0,r0,#16;ldr r1,[r4,#0x1c];asr r1,r1,#16;
        ldr r2,[r4,#0x20];asr r2,r2,#16;sub r2,r2,r0;ldr r3,[r4,#0x24];asr r3,r3,#16;sub r3,r3,r1;
        cmp r2,#0;ble done;cmp r3,#0;ble done;
        stm sp,{{r0-r3}};mov r0,sp;{n(f['portrait_locate'])}cmp r0,#0;beq done;
        movs r0,#0;mov r1,sp;movs r2,#23;
        wipe:str r0,[r1],#4;subs r2,#1;bne wipe;
        ldm r4,{{r0-r3}};stm sp,{{r0-r3}};ldr r0,[r4,#16];str r0,[sp,#16];ldr r0,[r4,#20];str r0,[sp,#20];
        mvn r0,#0;str r0,[sp,#0x20];
        ldr r0,[r4,#0x18];asr r0,r0,#16;strh r0,[sp,#0x18];ldr r0,[r4,#0x1c];asr r0,r0,#16;strh r0,[sp,#0x1a];
        ldr r0,[r4,#0x20];asr r0,r0,#16;strh r0,[sp,#0x1c];ldr r0,[r4,#0x24];asr r0,r0,#16;strh r0,[sp,#0x1e];
        ldr r0,[r4,#0x2c];str r0,[sp,#0x24];ldr r0,[r4,#0x28];str r0,[sp,#0x2c];
        ldrb r0,[r4,#0x30];strb r0,[sp,#0x44];ldrb r0,[r4,#0x31];strb r0,[sp,#0x45];
        ldr r0,[r4,#0x34];str r0,[sp,#0x48];ldr r0,[r4,#0x38];str r0,[sp,#0x4c];ldr r0,[r4,#0x3c];str r0,[sp,#0x50];
        mov r0,sp;{n(IMAGE)}movs r0,#0;str r0,[r6];
        done:add sp,#0x60;pop {{r4-r6,pc}};'''
    # r0 -> glyph matrix {sx, shy, w0, shx, sy, w1, tx, ty, w2}; r1 orientation.
    code['portrait_text'] = f'''push {{r4-r11,lr}};sub sp,#20;mov r4,r0;mov r5,r1;
        ldr r0,[r4,#4];lsls r0,r0,#1;bne done;ldr r0,[r4,#12];lsls r0,r0,#1;bne done;
        ldr r6,[r4];ldr r7,[r4,#16];
        mov r0,r6;{n(f['portrait_f2fix'])}asr r8,r0,#8;
        mov r0,r7;{n(f['portrait_f2fix'])}asr r9,r0,#8;cmp r9,#0;ble done;
        ldr r0,[r4,#24];{n(f['portrait_f2fix'])}mov r10,r0;
        ldr r0,[r4,#28];{n(f['portrait_f2fix'])}mov r11,r0;
        asr r0,r10,#8;asr r1,r11,#8;rsb r1,r1,#480;sub r1,r1,r9;mov r2,r8;mov r3,r9;
        stm sp,{{r0-r3}};mov r0,sp;{n(f['portrait_locate'])}cmp r0,#0;beq done;str r0,[sp,#16];
        ldm sp,{{r0-r3}};and r12,r10,#255;add r8,r12,r0,lsl #8;add r1,r1,r3;lsl r9,r1,#8;
        cmp r5,#2;beq upright;
        eor r6,r6,#0x80000000;movw r0,#{720 << 8 & 0xFFFF};movt r0,#{720 << 8 >> 16};sub r9,r0,r9;
        movw r0,#{480 << 8 & 0xFFFF};movt r0,#{480 << 8 >> 16};sub r8,r0,r8;b store;
        upright:eor r7,r7,#0x80000000;
        store:mov r0,r9;{n(f['portrait_fix2f'])}mov r10,r0;mov r0,r8;{n(f['portrait_fix2f'])}mov r11,r0;
        movs r0,#0;str r0,[r4];str r6,[r4,#4];str r0,[r4,#8];str r7,[r4,#12];str r0,[r4,#16];str r0,[r4,#20];
        str r10,[r4,#24];str r11,[r4,#28];mov r0,#0x3f800000;str r0,[r4,#32];
        done:add sp,#20;pop {{r4-r11,pc}};'''
    # r0 switch word, r1 kind, r2 -> stub frame. Record: kind, target, view kind,
    # phase (bytes), the landscape box x, y-down, w, h (halfwords), caller.
    # Text records the glyph origin and the glyph scale as w, h.
    f2 = n(f['portrait_f2fix'])
    code['portrait_log'] = f'''push {{r4-r8,lr}};sub sp,#16;mov r4,r0;mov r5,r1;mov r6,r2;
        add r7,r4,#{LOG};ldr r0,[r7,#8];cmp r0,#0;bne done;
        ldr r0,[r7];{c('r1',LOG_MAGIC)}cmp r0,r1;beq ready;str r1,[r7];movw r0,#{LOG_CAPACITY};str r0,[r7,#12];
        ready:cmp r5,#{k['rect']};beq rect;cmp r5,#{k['clear']};beq rect;cmp r5,#{k['frame']};beq frame;
        cmp r5,#{k['image3']};beq image3;cmp r5,#{k['text']};beq text;
        ldr r8,[r6];ldrsh r0,[r8,#0x18];ldrsh r1,[r8,#0x1a];ldrsh r2,[r8,#0x1c];sub r2,r2,r0;
        ldrsh r3,[r8,#0x1e];sub r3,r3,r1;b store;
        rect:ldr r8,[r6];ldm r8,{{r0-r3}};b flip;
        frame:ldm r6,{{r0-r3}};
        flip:rsb r1,r1,#480;sub r1,r1,r3;b store;
        image3:ldr r8,[r6];ldr r0,[r8,#0x18];asr r0,r0,#16;ldr r1,[r8,#0x1c];asr r1,r1,#16;
        ldr r2,[r8,#0x20];asr r2,r2,#16;sub r2,r2,r0;ldr r3,[r8,#0x24];asr r3,r3,#16;sub r3,r3,r1;b store;
        text:ldr r8,[r6];ldr r0,[r8,#24];{f2}asr r0,r0,#8;str r0,[sp];
        ldr r0,[r8,#28];{f2}asr r0,r0,#8;rsb r0,r0,#480;str r0,[sp,#4];
        ldr r0,[r8];{f2}asr r0,r0,#8;str r0,[sp,#8];ldr r0,[r8,#16];{f2}asr r0,r0,#8;str r0,[sp,#12];
        ldm sp,{{r0-r3}};
        store:ldr r12,[r7,#4];and lr,r12,#{LOG_CAPACITY - 1};add r12,r12,#1;str r12,[r7,#4];
        add lr,r7,lr,lsl #4;add lr,lr,#16;
        strb r5,[lr];{c('r12',TARGET)}ldrb r12,[r12];strb r12,[lr,#1];
        {c('r12',VIEW_KIND)}ldrb r12,[r12];strb r12,[lr,#2];{c('r12',UI_SERVICE_PHASE)}ldrb r12,[r12];strb r12,[lr,#3];
        strh r0,[lr,#4];strh r1,[lr,#6];strh r2,[lr,#8];strh r3,[lr,#10];ldr r8,[r6,#20];str r8,[lr,#12];
        done:add sp,#16;pop {{r4-r8,pc}};'''
    # r0 kind, r1 -> saved r0-r3, r12, lr of the stub (then the caller's stack).
    # Readout while upright (shown on the menu page; cleared when the switch is set):
    # view kinds, render targets and phases seen, the overlay words seen nonzero
    # (bits 0/1 in live view, 2/3 elsewhere) and the targets of full-layer copies.
    def bit(source, slot):
        return (f"{c('r1', source)}ldrb r0,[r1];cmp r0,#31;it hi;movhi r0,#31;movs r2,#1;lsl r2,r2,r0;"
                f"ldr r3,[r6,#{slot}];orr r3,r3,r2;str r3,[r6,#{slot}];")
    record = bit(VIEW_KIND, R_KINDS) + bit(TARGET, R_TARGETS) + bit(UI_SERVICE_PHASE, R_PHASES)
    code['portrait_hook'] = f'''push {{r4-r8,lr}};mov r4,r0;mov r5,r1;
        {n(f['get'])}cmp r0,#0;beq out;{c('r1',state_offset)}add r6,r0,r1;
        ldr r0,[r6];cmp r0,#1;bne out;
        {n(ORIENTATION)}mov r7,r0;movs r8,#0;
        cmp r7,#1;beq record;cmp r7,#2;bne gate;
        record:{record}
        {c('r1',OVERLAY_BUSY[0])}ldr r0,[r1];movs r2,#0;cmp r0,#0;it ne;movne r2,#1;
        {c('r1',OVERLAY_BUSY[1])}ldr r0,[r1];cmp r0,#0;it ne;orrne r2,r2,#2;
        {c('r1',VIEW_KIND)}ldrb r0,[r1];cmp r0,#0;it ne;lslne r2,r2,#2;
        ldr r3,[r6,#{R_BUSY}];orr r3,r3,r2;str r3,[r6,#{R_BUSY}];
        mov r0,r6;mov r1,r4;mov r2,r5;{n(f['portrait_log'])}
        cmp r4,#{k['image2']};bne gate;ldr r0,[r5];ldrh r1,[r0,#0x14];movw r2,#700;cmp r1,r2;blo gate;
        {c('r1',TARGET)}ldrb r0,[r1];cmp r0,#31;it hi;movhi r0,#31;movs r2,#1;lsl r2,r2,r0;
        ldr r3,[r6,#{R_COPIES}];orr r3,r3,r2;str r3,[r6,#{R_COPIES}];
        gate:{c('r1',UI_SERVICE_PHASE)}ldrb r0,[r1];cmp r0,#1;beq phase;cmp r0,#2;bne context;
        phase:{c('r1',VIEW_KIND)}ldrb r0,[r1];cmp r0,#0;bne context;
        {c('r1',PHOTO_BACKEND)}ldr r0,[r1];cmp r0,#1;bne context;
        {c('r1',OVERLAY_BUSY[0])}ldr r0,[r1];cmp r0,#0;bne context;
        {c('r1',OVERLAY_BUSY[1])}ldr r0,[r1];cmp r0,#0;bne context;movs r8,#1;
        context:movs r0,#0;cmp r8,#0;beq mode;cmp r7,#1;beq portrait;cmp r7,#2;bne mode;
        portrait:mov r0,r7;
        mode:ldr r2,[r6,#{R_MODE}];cmp r0,r2;beq same;str r0,[r6,#{R_MODE}];
        movs r2,#1;str r2,[r6,#{R_PENDING}];str r2,[r6,#{R_REPAINT}];
        same:mov r7,r0;cmp r8,#0;bne live;movs r2,#0;str r2,[r6,#{R_PENDING}];b out;
        live:{c('r1',TARGET)}ldrb r0,[r1];cmp r0,#{OSD_TARGET};bne out;
        cmp r4,#{k['clear']};bne draw;ldr r0,[r6,#{R_PENDING}];cmp r0,#0;beq draw;
        movs r0,#0;str r0,[r6,#{R_PENDING}];ldr r1,[r5];str r0,[r1];str r0,[r1,#4];
        movw r0,#720;str r0,[r1,#8];movw r0,#480;str r0,[r1,#12];b out;
        draw:cmp r7,#0;beq out;
        cmp r4,#{k['rect']};bne not_rect;ldr r0,[r5,#20];{c('r1',CLEAR_INNER_RECT_RETURN)}cmp r0,r1;beq out;
        ldr r0,[r5];mov r1,r7;mov r2,r4;{n(f['portrait_rect'])}b out;
        not_rect:cmp r4,#{k['clear']};bne not_clear;ldr r0,[r5];mov r1,r7;mov r2,r4;{n(f['portrait_rect'])}b out;
        not_clear:cmp r4,#{k['frame']};bne not_frame;mov r0,r5;mov r1,r7;mov r2,r4;{n(f['portrait_rect'])}b out;
        not_frame:cmp r4,#{k['image3']};bne not_image3;mov r0,r5;mov r1,r7;{n(f['portrait_image3'])}b out;
        not_image3:cmp r4,#{k['text']};bne image;ldr r0,[r5];mov r1,r7;{n(f['portrait_text'])}b out;
        image:ldr r0,[r5];mov r1,r7;mov r2,r4;{n(f['portrait_image'])}
        out:pop {{r4-r8,pc}};'''
    return code


def call_hook(f, kind):
    """Save r0-r3, r12, lr and the flags; the hook sees the registers and may rewrite them."""
    return f'''push {{r0-r3,r12,lr}};mrs r0,apsr;push {{r0,r1}};movs r0,#{kind};add r1,sp,#8;
        {n(f['portrait_hook'])}pop {{r0,r1}};msr apsr_nzcvq,r0;pop {{r0-r3,r12,lr}};'''


def stub(f, kind, replay, resume, skip=False):
    # A cleared r0 means the hook drew this command itself (image 3): return to the caller.
    tail = 'cbnz r0,stock;bx lr;stock:' if skip else ''
    return f'''{call_hook(f, kind)}{tail}{replay}ldr.w pc,={resume | 1};'''


def add(plan):
    control = plan.features['portrait_ui_control']
    if (control['log_offset'], control['log_capacity']) != (LOG, LOG_CAPACITY):
        raise ValueError('Draw log layout differs between the page and the hooks')
    if control['request_capacity'] != REQ_CAPACITY:
        raise ValueError('Request trace layout differs between the page and the hooks')
    f = {'get': int(plan.features['shutter_sound']['functions']['get'], 16) & ~1}
    im = plan.image
    if im.read(ORIENTATION, 8) != bytes.fromhex('ec48c16804200129'):
        raise ValueError('Unexpected native orientation getter')
    if im.read(TEXT_CALL, len(TEXT_EXPECTED)) != TEXT_EXPECTED:
        raise ValueError('Unexpected text matrix call')
    # 103BD30A: ldr r1,=TARGET; ...; ldrb r1,[r1] -- the target the viewport table is searched for.
    if im.words(0x103BD3A8, 1)[0] != TARGET or im.read(0x103BD30A, 8) != bytes.fromhex('274900204a680978'):
        raise ValueError('Unexpected render target reader')
    # 10310BF8: the UI dispatch indexes its handler table with VIEW_KIND.
    if im.words(0x10310DDC, 1)[0] != VIEW_KIND:
        raise ValueError('Unexpected UI state byte')
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    layout = plan.alloc(b''.join(struct.pack('<8h', *rule, 0) for rule in LAYOUT) + struct.pack('<8h', 0x7FFF, *[0] * 7))
    for name, source in sources(f, control['state_offset'], control['runtime_offset'], layout).items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    hooks = []
    for name, site, expected, replay in HOOKS:
        expected = bytes.fromhex(expected)
        if replay is None:  # image 3: its window holds a PC-relative load
            literal = im.words(((site + 6) & ~3) + 0x2A8, 1)[0]
            replay = f"push.w {{r4-r11,lr}};{c('r1', literal)}mov r4,r0;ldr r0,[r0,#4];"
        entry = plan.address
        code = assemble(stub(f, KINDS[name], replay, site + len(expected), skip=name == 'image3'),
                        entry, STUB_SIZE)
        plan.alloc(code)
        plan.edit(site, jump(site, entry, len(expected)), f'Portrait UI: {name} entry', expected)
        hooks.append(dict(name=name, site=hex(site), entry=hex(entry | 1), window=len(expected)))
    entry = plan.address
    code = assemble(f'''{call_hook(f, KINDS['text'])}{n(LOAD_MATRIX)}movs r1,#2;movw r0,#0x1122;
        ldr.w pc,={(TEXT_CALL + len(TEXT_EXPECTED)) | 1};''', entry, STUB_SIZE)
    plan.alloc(code)
    plan.edit(TEXT_CALL, jump(TEXT_CALL, entry, len(TEXT_EXPECTED)), 'Portrait UI: text matrix', TEXT_EXPECTED)
    hooks.append(dict(name='text', site=hex(TEXT_CALL), entry=hex(entry | 1), window=len(TEXT_EXPECTED)))
    # v3 forced 10453FAC(.., 0x1F, 7) here after a layout change. On the camera a
    # half press right after that left the info layer frozen (GUI alive, screen
    # unchanged, DISP ignored), so v4 leaves the stock refresh request untouched.
    # Record-only trace of 10453FAC(predicate, what, screens) while the switch is on:
    # caller and the three arguments, nothing changed. Finds what a half press
    # asks for, so a later build can ask the same after the layout changes.
    if im.read(REDRAW, len(REDRAW_EXPECTED)) != REDRAW_EXPECTED or im.words(REDRAW_LITERAL, 1)[0] != REDRAW_R5:
        raise ValueError('Unexpected redraw request prologue')
    requests = control['state_offset'] + control['request_offset']
    entry = plan.address
    code = assemble(f'''push {{r0-r3,r12,lr}};mrs r0,apsr;push {{r0,r1}};
        {n(f['get'])}cmp r0,#0;beq skip;{c('r1',control['state_offset'])}ldr r1,[r0,r1];cmp r1,#1;bne skip;
        {c('r1',requests)}add r12,r0,r1;{c('r3',REQ_MAGIC)}str r3,[r12];
        ldr r0,[sp,#28];{c('r1',PERIODIC_CALLER)}cmp r0,r1;bne keep;
        ldr r3,[r12,#8];adds r3,#1;str r3,[r12,#8];b skip;
        keep:
        ldr r3,[r12,#4];and r1,r3,#{REQ_CAPACITY - 1};adds r3,#1;str r3,[r12,#4];
        add r12,r12,r1,lsl #4;add r12,r12,#16;
        ldr r0,[sp,#28];str r0,[r12];ldr r0,[sp,#8];str r0,[r12,#4];
        ldr r0,[sp,#12];str r0,[r12,#8];ldr r0,[sp,#16];str r0,[r12,#12];
        skip:pop {{r0,r1}};msr apsr_nzcvq,r0;pop {{r0-r3,r12,lr}};
        push {{r4-r6,lr}};{c('r5',REDRAW_R5)}mov r6,r1;mov r4,r2;
        ldr.w pc,={(REDRAW + len(REDRAW_EXPECTED)) | 1};''', entry, 192)
    plan.alloc(code)
    plan.edit(REDRAW, jump(REDRAW, entry, len(REDRAW_EXPECTED)), 'Portrait UI: record redraw requests (no change)', REDRAW_EXPECTED)
    hooks.append(dict(name='request_trace', site=hex(REDRAW), entry=hex(entry | 1), window=len(REDRAW_EXPECTED)))
    events = control['state_offset'] + control['event_offset']
    if im.read(MARK_ALL, 4) != MARK_ALL_EXPECTED or im.read(REDRAW_NOW, len(REDRAW_NOW_EXPECTED)) != REDRAW_NOW_EXPECTED:
        raise ValueError('Unexpected widget refresh entries')
    if sum(1 for a in (0x10325074, 0x103254C0, 0x1032620C, 0x10326A88, 0x10326E88, 0x10327314)
           if im.words(a, 1)[0] == LIVE_CONTAINER) != 6:
        raise ValueError('Live-view container literals moved')
    if control['event_capacity'] != EVT_CAPACITY:
        raise ValueError('Event trace layout differs between the page and the hooks')
    for number, (name, site, expected, replay) in enumerate(EVENT_HOOKS):
        expected = bytes.fromhex(expected)
        replay = replay.replace('CALL103051A0', n(0x103051A0))
        entry = plan.address
        # Header words: +8 the user is half-pressing (seen ACTION_HALF, not yet ACTION_RELEASE),
        # +12 injected repaints. Only the action entry acts; the others only record.
        # Action entry: track whether the user is half-pressing (header +8).
        # Update entry (GUI task, ~5 a second): once the drawing hooks saw the layout
        # change (R_REPAINT) and the full clear is done (R_PENDING 0), dispatch a UI
        # half press and release through the action entry; the page repaints on it.
        if name == 'action':
            act = f'''
            ldr r0,[sp,#8];cmp r0,#{ACTION_HALF};bne not_half;movs r1,#1;str r1,[r12,#8];b skip;
            not_half:cmp r0,#{ACTION_RELEASE};bne skip;str r0,[r12,#8];'''
            inject = ''
        elif name == 'update' and REPAINT_BY_MARKING:
            # v13 also called 10454218 (redraw now) here; it waits up to 10 s for the
            # drawing flag (10453CCE) inside an event the GUI is already handling, and
            # the camera hung on the first turn. Marking alone leaves the drawing to the
            # stock traversal, which runs on its own about three times a second.
            redraw_now = (f"{c('r0',LIVE_CONTAINER)}{c('r1',REDRAW_CALLBACK)}{n(REDRAW_NOW)}"
                          if REDRAW_IMMEDIATELY else '')
            widgets = control['state_offset'] + control['widget_offset']
            # Several widgets can share one box (one per mode); only the stock's latest
            # drawn one there is current, the others only clear it. So for each recorded
            # widget: skip it when another with the same box (+0x10, 8 bytes) was drawn later.
            # Pass 1 marks the background widgets (draw function BACKGROUND_DRAW), which
            # only clear their box; pass 2, a few update events later, marks the rest.
            # In one pass the stock traversal (list order) drew some backgrounds after
            # the icons over them and AF-A, RAW and DX stayed blank (v17, camera); a
            # half press draws them in this order too. r9 holds the pass (1 or 2).
            mark = (f'''push {{r4-r9}};mov r9,r1;{n(f['get'])}cmp r0,#0;beq mark_done;{c('r1',widgets)}add r0,r0,r1;ldr r2,[r0];
                cmp r2,#{WID_CAPACITY};it hi;movhi r2,#{WID_CAPACITY};add r1,r0,#16;
                movs r3,#0;
                outer:cmp r3,r2;bhs mark_done;
                add lr,r1,r3,lsl #3;ldr r4,[lr];ldr r5,[lr,#4];
                lsrs r0,r4,#29;cmp r0,#4;blo next_i;cmp r0,#7;bhs next_i;tst r4,#3;bne next_i;
                ldr r0,[r4,#0x4C];{c('r12',BACKGROUND_DRAW)}cmp r0,r12;ite eq;moveq r0,#1;movne r0,#2;cmp r0,r9;bne next_i;
                ldr r6,[r4,#0x10];ldr r7,[r4,#0x14];movs r8,#0;
                inner:cmp r8,r2;bhs do_mark;cmp r8,r3;beq next_j;
                add lr,r1,r8,lsl #3;ldr r12,[lr];ldr lr,[lr,#4];cmp lr,r5;bls next_j;
                lsrs r0,r12,#29;cmp r0,#4;blo next_j;cmp r0,#7;bhs next_j;tst r12,#3;bne next_j;
                ldr r0,[r12,#0x10];cmp r0,r6;bne next_j;ldr r0,[r12,#0x14];cmp r0,r7;beq next_i;
                next_j:adds r8,#1;b inner;
                do_mark:movs r0,#1;str r0,[r4,#0x44];
                next_i:adds r3,#1;b outer;
                mark_done:pop {{r4-r9}};'''
                    if MARK_RECORDED else f"{c('r0',LIVE_CONTAINER)}{n(MARK_ALL)}")
            # R_REPAINT: 1 after a layout change -> background pass. Then, once per update
            # event, it counts down from 2 + PASS_GAP; the foreground pass runs as soon as
            # the stock traversal has drawn every background widget (no +0x44 flag left
            # on one), or when the count reaches 2 (v18 always waited the full count and
            # the camera took a moment to fill in).
            act = f'''
            ldr r1,[r2,#{R_REPAINT}];cmp r1,#0;beq skip;ldr r1,[r2,#{R_PENDING}];cmp r1,#0;bne skip;
            ldr r1,[r12,#8];cmp r1,#0;bne skip;
            {c('r1',UI_SERVICE_PHASE)}ldrb r1,[r1];cmp r1,#1;beq phase_ok;cmp r1,#2;bne skip;
            phase_ok:{c('r1',VIEW_KIND)}ldrb r1,[r1];cmp r1,#0;bne skip;
            {c('r1',PHOTO_BACKEND)}ldr r1,[r1];cmp r1,#1;bne skip;
            ldr r1,[r2,#{R_REPAINT}];cmp r1,#2;bhi pass_wait;beq pass_fg;
            movs r1,#{2 + PASS_GAP};str r1,[r2,#{R_REPAINT}];movs r1,#1;b pass_go;
            pass_wait:subs r1,#1;str r1,[r2,#{R_REPAINT}];cmp r1,#2;beq skip;
            push {{r4-r6}};{c('r0',control['widget_offset'])}add r0,r2,r0;ldr r3,[r0];
            cmp r3,#{WID_CAPACITY};it hi;movhi r3,#{WID_CAPACITY};add r0,#16;{c('r6',BACKGROUND_DRAW)}
            scan:cmp r3,#0;beq scan_clean;subs r3,#1;ldr r4,[r0,r3,lsl #3];
            lsrs r5,r4,#29;cmp r5,#4;blo scan;cmp r5,#7;bhs scan;tst r4,#3;bne scan;
            ldr r5,[r4,#0x4C];cmp r5,r6;bne scan;ldr r5,[r4,#0x44];cmp r5,#0;beq scan;
            pop {{r4-r6}};b skip;
            scan_clean:pop {{r4-r6}};
            pass_fg:movs r1,#0;str r1,[r2,#{R_REPAINT}];movs r1,#2;
            pass_go:str r1,[sp,#4];ldr r1,[r12,#12];adds r1,#1;str r1,[r12,#12];'''
            if MARK_RECORDED:  # outside photo live view: forget the recorded widgets
                act = (f'''{c('r1',VIEW_KIND)}ldrb r1,[r1];cmp r1,#0;beq in_live;
                    {c('r1',control['widget_offset'])}movs r3,#0;str r3,[r2,r1];b skip;in_live:''' + act)
            inject = f'''cmp r1,#0;beq plain;
            {mark}{redraw_now}
            plain:'''
        elif name == 'update' and INJECT_HALF_PRESS:
            # R_HOLD counts update events while the injected half press is held; the
            # release goes out when it reaches zero (a real half press held ~0.9 s on
            # the camera, and press+release back to back did not repaint: v10).
            act = f'''
            ldr r1,[r2,#{R_HOLD}];cmp r1,#0;beq trigger;subs r1,#1;str r1,[r2,#{R_HOLD}];cmp r1,#0;bne skip;
            movs r1,#2;str r1,[sp,#4];b skip;
            trigger:ldr r1,[r2,#{R_REPAINT}];cmp r1,#0;beq skip;ldr r1,[r2,#{R_PENDING}];cmp r1,#0;bne skip;
            ldr r1,[r12,#8];cmp r1,#0;bne skip;
            {c('r1',UI_SERVICE_PHASE)}ldrb r1,[r1];cmp r1,#1;beq phase_ok;cmp r1,#2;bne skip;
            phase_ok:{c('r1',VIEW_KIND)}ldrb r1,[r1];cmp r1,#0;bne skip;
            {c('r1',PHOTO_BACKEND)}ldr r1,[r1];cmp r1,#1;bne skip;
            movs r1,#0;str r1,[r2,#{R_REPAINT}];movs r1,#{HOLD_UPDATES};str r1,[r2,#{R_HOLD}];
            ldr r1,[r12,#12];adds r1,#1;str r1,[r12,#12];movs r1,#1;str r1,[sp,#4];'''
            action = EVENT_HOOKS[0][1]
            inject = f'''cmp r1,#0;beq plain;cmp r1,#2;
            pop {{r0-r3,r12,lr}};push {{r0-r3,r12,lr}};beq release;
            movs r0,#{ACTION_HALF};{n(action)}b injected;
            release:movs r0,#{ACTION_RELEASE};{n(action)}
            injected:
            plain:'''
        else:
            act = inject = ''
        code = assemble(f'''push {{r0-r3,r12,lr}};mrs r0,apsr;movs r1,#0;push {{r0,r1}};
            {n(f['get'])}cmp r0,#0;beq skip;{c('r1',control['state_offset'])}add r2,r0,r1;ldr r1,[r2];cmp r1,#1;bne skip;
            {c('r1',control['event_offset'])}add r12,r2,r1;{c('r3',EVT_MAGIC)}str r3,[r12];
            ldr r3,[r12,#4];and r1,r3,#{EVT_CAPACITY - 1};adds r3,#1;str r3,[r12,#4];
            add r3,r12,r1,lsl #4;add r3,r3,#16;
            {c('r0',POSE_BYTE)}ldrb r0,[r0];lsl r0,r0,#8;orr r0,r0,#{number};str r0,[r3];
            ldr r0,[sp,#28];str r0,[r3,#4];ldr r0,[sp,#8];str r0,[r3,#8];ldr r0,[sp,#12];str r0,[r3,#12];
            {act}
            skip:pop {{r0,r1}};msr apsr_nzcvq,r0;{inject}pop {{r0-r3,r12,lr}};
            {replay}ldr.w pc,={(site + len(expected)) | 1};''', entry, 768)
        plan.alloc(code)
        plan.edit(site, jump(site, entry, len(expected)), f'Portrait UI: record UI event {name} (no change)', expected)
        hooks.append(dict(name=f'event_{name}', site=hex(site), entry=hex(entry | 1), window=len(expected)))
    if control['traversal_capacity'] != TRV_CAPACITY:
        raise ValueError('Traversal trace layout differs between the page and the hooks')
    if im.read(TRAVERSE, len(TRAVERSE_EXPECTED)) != TRAVERSE_EXPECTED:
        raise ValueError('Unexpected redraw traversal prologue')
    entry = plan.address
    code = assemble(f'''push {{r0-r3,r12,lr}};mrs r0,apsr;push {{r0,r1}};
        {n(f['get'])}cmp r0,#0;beq skip;{c('r1',control['state_offset'])}add r2,r0,r1;ldr r1,[r2];cmp r1,#1;bne skip;
        {c('r1',control['traversal_offset'])}add r12,r2,r1;{c('r3',TRV_MAGIC)}str r3,[r12];
        ldr r3,[r12,#4];and r1,r3,#{TRV_CAPACITY - 1};adds r3,#1;str r3,[r12,#4];
        add r3,r12,r1,lsl #4;add r3,r3,#16;
        ldr r0,[sp,#8];str r0,[r3];ldr r0,[sp,#12];str r0,[r3,#4];ldr r0,[sp,#28];str r0,[r3,#8];
        {c('r0',VIEW_KIND)}ldrb r1,[r0];{c('r0',POSE_BYTE)}ldrb r0,[r0];orr r1,r1,r0,lsl #8;str r1,[r3,#12];
        skip:pop {{r0,r1}};msr apsr_nzcvq,r0;pop {{r0-r3,r12,lr}};
        push.w {{r3-r9,lr}};adds r0,#0x9c;mov r9,r1;movs r7,#0;
        ldr.w pc,={(TRAVERSE + len(TRAVERSE_EXPECTED)) | 1};''', entry, 256)
    plan.alloc(code)
    plan.edit(TRAVERSE, jump(TRAVERSE, entry, len(TRAVERSE_EXPECTED)), 'Portrait UI: record redraw traversals (no change)', TRAVERSE_EXPECTED)
    hooks.append(dict(name='traversal_trace', site=hex(TRAVERSE), entry=hex(entry | 1), window=len(TRAVERSE_EXPECTED)))
    if MARK_RECORDED:
        if im.read(DRAW_SITE, len(DRAW_EXPECTED)) != DRAW_EXPECTED or control['widget_capacity'] != WID_CAPACITY:
            raise ValueError('Unexpected traversal draw call')
        entry = plan.address
        # r4 widget, r8 = container + 0x9C. Add r4 once to the list when the container is the
        # live-view one and the widget has a draw function; then the stock window, with its cbz.
        code = assemble(f'''push {{r0-r3,r12,lr}};mrs r0,apsr;push {{r0,r1}};
            ldr r0,[r4,#0x4c];cmp r0,#0;beq skip;{c('r0',LIVE_CONTAINER + 0x9C)}cmp r8,r0;bne skip;
            {n(f['get'])}cmp r0,#0;beq skip;{c('r1',control['state_offset'])}ldr r2,[r0,r1];cmp r2,#1;bne skip;
            {c('r1',control['state_offset'] + control['widget_offset'])}add r0,r0,r1;ldr r2,[r0];
            add r1,r0,#16;movs r3,#0;
            seek:cmp r3,r2;beq add;ldr r12,[r1,r3,lsl #3];cmp r12,r4;beq found;adds r3,#1;b seek;
            found:add r1,r1,r3,lsl #3;b stamp;
            add:cmp r2,#{WID_CAPACITY};bhs skip;add r1,r1,r2,lsl #3;str r4,[r1];adds r2,#1;str r2,[r0];
            stamp:ldr r3,[r0,#4];adds r3,#1;str r3,[r0,#4];str r3,[r1,#4];
            skip:pop {{r0,r1}};msr apsr_nzcvq,r0;pop {{r0-r3,r12,lr}};
            ldr r2,[r4,#0x4c];cbz r2,none;mov r1,r9;mov r0,r4;ldr.w pc,={DRAW_CALL | 1};
            none:ldr.w pc,={DRAW_SKIP | 1};''', entry, 256)
        plan.alloc(code)
        plan.edit(DRAW_SITE, jump(DRAW_SITE, entry, len(DRAW_EXPECTED)), 'Portrait UI: remember drawn live-view widgets', DRAW_EXPECTED)
        hooks.append(dict(name='draw_record', site=hex(DRAW_SITE), entry=hex(entry | 1), window=len(DRAW_EXPECTED)))
    if REPAINT_HOOK:
        if (im.read(REFRESH_REQUEST, len(REFRESH_EXPECTED)) != REFRESH_EXPECTED
                or im.words(((REFRESH_REQUEST + 4) & ~3) + 0x328, 1)[0] != REDRAW_PREDICATE):
            raise ValueError('Unexpected shooting-info refresh request')
        state = control['state_offset']
        entry = plan.address
        code = assemble(f'''push {{r2,r4}};{n(f['get'])}cmp r0,#0;beq plain;{c('r1',state)}add r4,r0,r1;
            ldr r0,[r4];cmp r0,#1;bne plain;
            ldr r0,[r4,#{R_PASSES}];adds r0,#1;str r0,[r4,#{R_PASSES}];
            ldr r0,[r4,#{R_REPAINT}];cmp r0,#0;beq plain;movs r0,#0;str r0,[r4,#{R_REPAINT}];
            ldr r0,[r4,#{R_FORCED}];adds r0,#1;str r0,[r4,#{R_FORCED}];
            pop {{r2,r4}};movs r2,#7;movs r1,#{REDRAW_ALL};b request;
            plain:pop {{r2,r4}};movs r1,#3;
            request:{c('r0',REDRAW_PREDICATE)}{n(REDRAW)}
            ldr.w pc,={(REFRESH_REQUEST + len(REFRESH_EXPECTED)) | 1};''', entry, STUB_SIZE)
        plan.alloc(code)
        plan.edit(REFRESH_REQUEST, jump(REFRESH_REQUEST, entry, len(REFRESH_EXPECTED)),
                  'Portrait UI: repaint everything once after the layout changed', REFRESH_EXPECTED)
        hooks.append(dict(name='repaint', site=hex(REFRESH_REQUEST), entry=hex(entry | 1), window=len(REFRESH_EXPECTED)))
    plan.features['portrait_ui'] = dict(
        hooks=hooks, functions={k: hex(v | 1) for k, v in f.items()},
        orientation_getter=hex(ORIENTATION), render_target=hex(TARGET), osd_target=OSD_TARGET,
        context=dict(view_kind=hex(VIEW_KIND), view_kind_value=0, ui_service_phase=hex(UI_SERVICE_PHASE),
                     phases=[1, 2], photo_backend=hex(PHOTO_BACKEND), overlay_busy_zero=[hex(v) for v in OVERLAY_BUSY]),
        repaint=dict(enabled=REPAINT_HOOK, site=hex(REFRESH_REQUEST), request=hex(REDRAW), what=REDRAW_ALL, screens=7, thread='GUI task (stock refresh path)'),
        layout=dict(top_band=TOP_BAND, bottom_band=BOTTOM_BAND, left_column=LEFT_COLUMN,
                    right_column=RIGHT_COLUMN, shift_y=SHIFT_Y, bar_split_x=SPLIT_X, bar_row=ROW, rules=[list(r) for r in LAYOUT],
                    rules_source='live-view boxes read on the camera over USB, 2026-11-11'),
        scope='photo live view on the rear screen; menus, i menu, playback and movie stay landscape',
        default=0, inert_when_off=True,
        camera_verified=False, osd_target_camera_verified=False, orientation_side_camera_verified=False)
