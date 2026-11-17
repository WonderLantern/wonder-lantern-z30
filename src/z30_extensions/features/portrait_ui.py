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

# The i menu (camera, 2026-11-15, USB readout and RAM diffs): view kind 3, the
# same context words as live view otherwise. Landscape it is a 6x2 grid of 120x72
# cells (x 0..720, y 280..424) on a backdrop 0,280,720,146; the title above it
# (clear 88,228,544,44, glyph baseline y 264) and the bottom bar right of x 463
# (y 430..478). Portrait it becomes 4x3 by slot number, so the order reads the
# same: slot i at column i & 3, row i >> 2. The title and the bar keep their size.
MENU_VIEW = 3
MENU_TOP, MENU_CELL_W, MENU_CELL_H, MENU_SPLIT = 280, 120, 72, 352  # landscape grid, row 1 from y 352
MENU_BACKDROP = (0, 280, 720, 146)
MENU_GRID_Y = 440                          # portrait grid top: rows 440, 512, 584, bottom 656
MENU_TITLE = (200, 280, -120, 150)         # centre y range, dx, dy
MENU_BAR = (428, 400, -340, 240)           # centre y from, x from, dx, dy
MENU_MODE = 4                              # R_MODE bit: the portrait layout is the i menu
# A setting page opened from the i menu (OK on a cell) is view kind 3 too; there the
# navigation-table state (C182D8C4) is 3 and the navigation block counts its own
# options (camera: image quality, 7). Only state 0 is the grid; a setting page
# stays landscape, handled like a level camera (one full clear, stock layout).
# Direction keys in the i menu: the action handlers (e.g. right 103DF5E3) call
# 1051B654(direction: 0 up, 1 down, 2 right, 3 left), which in table state 0
# (C182D8C4) is 1051B7AC: current slot 1042B52A(), the 6x2 target into the
# navigation block C201D198 (+0 count 12, +4 slot), then the cursor setter
# 1042BBFC(slot), which moves the highlight. Portrait, the hook turns the key with
# the screen, moves in 4x3 (wrapping inside the row or column) and does the same
# store and setter call, skipping the stock 6x2 arithmetic.
NAV_MOVE, NAV_MOVE_EXPECTED = 0x1051B654, bytes.fromhex('ec49' '0a78' '091d' '01ebc201')  # ldr r1,=C182D8C4; ldrb r2,[r1]; adds r1,r1,#4; add.w r1,r1,r2,lsl #3
CURSOR_GET_EXPECTED, CURSOR_SET_EXPECTED = bytes.fromhex('5e485b4990300078'), bytes.fromhex('2de9fc41c84c0546')
NAV_MOVE_RESUME = NAV_MOVE + len(NAV_MOVE_EXPECTED)
NAV_TABLE, NAV_BLOCK = 0xC182D8C4, 0xC201D198
CURSOR_GET, CURSOR_SET = 0x1042B52A, 0x1042BBFC
# Physical direction (0 up, 1 down, 2 right, 3 left) to the direction on the
# portrait screen, one nibble each, per orientation (as panel_box turns the screen).
NAV_TURN = {1: 0x1023, 2: 0x0132}

# Setting pages opened from the i menu (view kind 3, navigation-table state not 0).
# Their layouts were not read on the camera yet, so v22 does not lay them out item by
# item: the whole landscape page is drawn at 2/3 size, upright, at the bottom of the
# portrait screen (480x320, y 400..720), as the Z 5 does for its third-level pages.
# Touch goes back by the exact 3/2; the direction keys turn with the screen.
MINI_MODE = 8                     # R_MODE bit: the 2/3 setting page
MINI_Y = 720 - 320                # portrait top of the page
MINI_NUM, MINI_SHIFT = 683, 10    # 2/3 as (v * 683) >> 10, no divide
# Direction action codes at the UI action entry (10310BF8; the i-menu handler 103DEC33
# reads them): right 2, left 3, up 4, down 5. Physical code -> screen code per grip,
# one nibble per code 2..5, from NAV_TURN (camera-verified for the i-menu grid).
# Slider pages: touching shutter speed, aperture or ISO in live view opens a value
# slider over the live-view page (camera USB draw log, 2026-11-17): view kind 12
# shutter, 11 aperture, 13 ISO (the bubble tail points at x 225 / 314 / 470). The
# same context words as live view; the bottom bar stays, the top bar is hidden. The
# slider row is y 358..406: ◀◀ 93..173, the value backdrop 203..517 (holds ◀, the
# value and ▶), ▶▶ 547..627; the tail under the value y 406..430; the exposure scale
# x 680..718, y 238..426. Portrait: the row loses its two 30-px gaps (474 of 480 px)
# and sits just above the two bottom rows; the tail follows its item's column.
SLIDER_VIEWS = (11, 12, 13)
SLIDER_MODE = 16                  # R_MODE bit: a slider page
SLIDER_DY = 188                   # row y 546..594, tail 594..618; bottom rows start at 618
SLIDER_LAYOUT = (
    # cx0, cx1, cy0, cy1,  dx,  dy, status
    (80, 190, 340, 406, -92, SLIDER_DY, 1),    # ◀◀ (x 1..81)
    (190, 530, 340, 406, -120, SLIDER_DY, 1),  # value backdrop with ◀ and ▶ (x 83..397)
    (530, 640, 340, 406, -148, SLIDER_DY, 1),  # ▶▶ (x 399..479)
    (100, 262, 406, 430, -127, SLIDER_DY, 1),  # tail under the shutter speed
    (262, 380, 406, 430, -127, SLIDER_DY, 1),  # tail under the aperture
    (430, 592, 406, 430, -185, SLIDER_DY, 1),  # tail under the ISO
    (600, 720, 0, 60, -236, 0, 1),             # top-right button (636,4,80,48), to the top right
)
SLIDER_BUTTONS = (                # landscape box, portrait dx (dy SLIDER_DY)
    ((93, 358, 80, 48), -92),
    ((203, 358, 314, 48), -120),
    ((547, 358, 80, 48), -148),
)
SLIDER_ROW_BOX = (93, 358, 534, 48)
SLIDER_CORNER = (636, 4, 80, 48)

# Histogram (camera USB readout, 2026-11-17). The stock histogram is not drawn through
# the drawing entries (as on the Z 5), so upright on the histogram page (DISP 2) the
# stock one is hidden (its 9 visibility masks cleared; the sampling gate reads the DISP
# state, not the masks) and the counts are drawn with the stock rectangle and clear
# functions, called past the hooks. 256 counts (u32) at HIST_BINS, total 92928, fresh
# only on DISP 2/3/5; the same addresses after a restart. MS_CLOCK: the kernel's 64-bit
# millisecond time (low word), read by the scheduler with ldrd.
HIST_BINS = 0xC201768C
HIST_MASKS, HIST_MASK_COUNT = 0xC1826718, 9   # descriptor 0xC1826714 + 4, RAM
HIST_DISP, HIST_PAGE = 0xC2016A68 + 0x196, 2  # liveview_dual's DISP state byte; the histogram page
# With liveview-dual the histogram also shows on the level page (3) and the full-information
# page (5); v24 only handled 2 and the other two kept the stock landscape histogram (camera).
HIST_DUAL_PAGES = (2, 3, 5)
MS_CLOCK = 0xC18EB098
# Bottom right, in the empty space right of the "OK: focus point" hint, under the exposure
# scale (x 440.., y ..546) and above the shots remaining (y 627..), left of the AF frame's
# bottom-right corner line (x ~432) -- camera screenshot 2026-11-17 (v24 sat top middle).
# v25's 128 x 64 looked small on the camera; v26 uses the whole gap: x 288..480 (the hint
# frame ends at 292, so 4 px overlap), y 550..622.
HIST_X, HIST_Y, HIST_CW, HIST_H = 288, 550, 3, 72   # portrait box: 64 bars of 3 px, 192 x 72
HIST_LW = 1                                         # divider and frame lines
HIST_W = 64 * HIST_CW
HIST_INTERVAL = 80                                  # ms between redraws, as on the Z 5
HIST_BG, HIST_BAR, HIST_LINE = 0x00000080, 0xE6E6E6FF, 0xFFFFFFFF
# Offsets in the histogram block (portrait_menu.HST).
H_ON, H_DIRTY, H_MS, H_SAVED, H_ORI, H_COLOR, H_FN = 0, 4, 8, 12, 16, 20, 24
H_FULL, H_PART, H_OVERLAP, H_MASKS, H_BLEND, H_PREV = 28, 32, 36, 40, 76, 80
# v24 on the camera: the stock clear (vgClear, descriptor +0x14 = 0) called from here left
# the old bars and stacked the translucent backdrop; rectangles drew. The stock clear's
# other path (+0x14 set) draws a rectangle with the blend mode set to VG_BLEND_SRC, which
# replaces the pixels. v25 does the same around all its drawing: the backdrop replaces
# whatever was there, and erasing is a rectangle of colour 0.
VG_SETI, VG_GETI, VG_BLEND_MODE, VG_BLEND_SRC = 0x1050A406, 0x1050A47C, 0x1104, 0x2000
LAYOUT_BITS = 4 | 8 | 16   # MENU_MODE | MINI_MODE | SLIDER_MODE


def hist_bars(bins):
    """Python model: 64 bar heights; the peak is the smaller of the largest count and 4x the mean."""
    peak = min(max(bins), sum(bins) >> 6)
    d = peak * 4
    out = []
    for i in range(64):
        s = sum(bins[4 * i:4 * i + 4]) * HIST_H
        out.append(0 if not d else min(HIST_H, s // d))
    return out


def hist_box(ori, px, py, w, h):
    """Portrait box -> physical (x, y-up, w, h), as panel_box."""
    return panel_box(ori, px, py, w, h)


def hist_prims(ori, bars, prev, full):
    """Python model of portrait_hist's drawing: [(fn, (x, y-up, w, h), colour)], all 'rect' with the blend mode SRC."""
    out = []
    def prim(fn, px, py, w, h, colour):
        out.append((fn, hist_box(ori, px, py, w, h), colour))
    changed = bool(full)
    if full:
        prim('rect', HIST_X, HIST_Y, HIST_W, HIST_H, HIST_BG)
    for i, h in enumerate(bars):
        cx = HIST_X + i * HIST_CW
        if not full:
            if h == prev[i]:
                continue
            prim('rect', cx, HIST_Y, HIST_CW, HIST_H, HIST_BG)
            changed = True
        if h:
            prim('rect', cx, HIST_Y + HIST_H - h, HIST_CW, h, HIST_BAR)
    if changed:
        for i in (1, 2, 3):
            prim('rect', HIST_X + i * (HIST_W // 4), HIST_Y, HIST_LW, HIST_H, HIST_LINE)
        prim('rect', HIST_X, HIST_Y, HIST_W, HIST_LW, HIST_LINE)
        prim('rect', HIST_X, HIST_Y + HIST_H - HIST_LW, HIST_W, HIST_LW, HIST_LINE)
        prim('rect', HIST_X, HIST_Y, HIST_LW, HIST_H, HIST_LINE)
        prim('rect', HIST_X + HIST_W - HIST_LW, HIST_Y, HIST_LW, HIST_H, HIST_LINE)
    return out


CODE_DIR = {4: 0, 5: 1, 2: 2, 3: 3}
DIR_CODE = {v: k for k, v in CODE_DIR.items()}
CODE_TURN = {ori: sum(DIR_CODE[(turn >> (CODE_DIR[code] * 4)) & 15] << ((code - 2) * 4) for code in (2, 3, 4, 5))
             for ori, turn in NAV_TURN.items()}

# Touch. The shooting application's loop (102F7678, event 0x56) hands every touch event
# of a view kind below 0x11 to 104F1AD4 (entry C on the Z 5; live view is 0, the i menu
# 3); 1039DA3C takes the others. 104F1AD4 copies the 60-byte event and passes it on to
# the widgets, so the event is rewritten in place at its entry and every later reader,
# including the copy in C2006558 the i-menu hit test reads, sees the landscape point.
# Event (as on the Z 5, and as QEMU's touch input 103CE7E6 builds it): +8 contact
# (1 down, 0 up), +9 type (1 press/release, 2 move, 3 drag end, 4 hold, 9 cancel),
# +0x12/+0x14 the landscape panel point (y down). The GUI's own touch entry 104759DC
# (menus) is not changed.
TOUCH, TOUCH_EXPECTED = 0x104F1AD4, bytes.fromhex('2de9f043' '93b0' '0646')  # push.w {r4-r9,lr}; sub sp,#0x4c; mov r6,r0
TOUCH_CALL, TOUCH_VIEW_LIMIT = 0x102F7688, 0x11
TOUCH_EVENT_COPY = 0x10456220   # memcpy(C2006558, event, 0x3C), reached from the widgets
# Touch states (header +8): 0 none, 1 mapped by a control's offset, 2 cancelled,
# 3 the 2/3 setting page (exact inverse).
T_NONE, T_MAPPED, T_CANCEL, T_MINI = 0, 1, 2, 3
# Live-view controls, landscape boxes read on the camera over USB (2026-11-11 log, the
# same places as on the Z 5): each is placed where portrait_locate draws it. A press
# on a placed control goes to that control's landscape place; a press on a landscape
# place (empty in portrait) is cancelled; anything else is the picture (touch AF).
LIVE_CONTROLS = (
    ('touch_shutter', (2, 128, 80, 48)),
    ('i_button', (660, 128, 58, 48)),
    ('hint_button', (306, 372, 106, 48)),
    ('shutter', (128, 430, 124, 48)),
    ('aperture', (266, 430, 104, 48)),
    ('iso', (436, 430, 153, 48)),
)
LIVE_CANCEL = tuple(box for _, box in LIVE_CONTROLS if _ != 'hint_button') + ((195, 372, 328, 48),)  # the whole hint row
MENU_BAR_BOX = (400, 428, 320, 52)   # the i-menu bottom bar (moved by MENU_BAR)
TOUCH_END = 0x7FFF
TCH_MAGIC, TCH_CAPACITY = 0x31485657, 64  # WVH1; the ring lives in the page's state block (portrait_menu.TCH)


def scale_mini(v):
    return (v * MINI_NUM) >> MINI_SHIFT


def locate_mini(x, y, w, h):
    """Python model of the setting-page branch of portrait_locate: the page at 2/3, bottom of the portrait screen."""
    if w >= FULL_W and h >= FULL_H:
        return 0, x, y, w, h
    return 4, scale_mini(x), scale_mini(y) + MINI_Y, max(1, scale_mini(w)), max(1, scale_mini(h))


def touch_tables():
    """(live, menu, slider): rows (kind, a0, a1, a2, a3, ox, oy). Kind 1: portrait box -> offset; kind 2: landscape box -> cancel."""
    live, menu, slider = [], [], []
    for (x, y, w, h), dx in SLIDER_BUTTONS:
        px, py = x + dx, y + SLIDER_DY
        slider.append((T_MAPPED, px, py, px + w, py + h, -dx, -SLIDER_DY))
    x, y, w, h = SLIDER_CORNER
    status, px, py, _, _ = locate_slider(x, y, w, h)
    slider.append((T_MAPPED, px, py, px + w, py + h, x - px, y - py))
    for name, (x, y, w, h) in LIVE_CONTROLS:
        if name in ('shutter', 'aperture', 'iso'):
            status, px, py, pw, ph = locate(x, y, w, h)
            slider.append((T_MAPPED, px, py, px + pw, py + ph, x - px, y - py))
    for x, y, w, h in (SLIDER_ROW_BOX, SLIDER_CORNER) + LIVE_CANCEL:
        slider.append((T_CANCEL, x, y, x + w, y + h, 0, 0))
    for _, (x, y, w, h) in LIVE_CONTROLS:
        status, px, py, pw, ph = locate(x, y, w, h)
        live.append((T_MAPPED, px, py, px + pw, py + ph, x - px, y - py))
    for x, y, w, h in LIVE_CANCEL:
        live.append((T_CANCEL, x, y, x + w, y + h, 0, 0))
    for slot in range(12):
        x, y = MENU_CELL_W * (slot % 6), MENU_TOP + MENU_CELL_H * (slot // 6)
        px, py = MENU_CELL_W * (slot & 3), MENU_GRID_Y + MENU_CELL_H * (slot >> 2)
        menu.append((T_MAPPED, px, py, px + MENU_CELL_W, py + MENU_CELL_H, x - px, y - py))
    x, y, w, h = MENU_BAR_BOX
    menu.append((T_MAPPED, x + MENU_BAR[2], y + MENU_BAR[3], x + MENU_BAR[2] + w, y + MENU_BAR[3] + h, -MENU_BAR[2], -MENU_BAR[3]))
    return live, menu, slider


def portrait_point(ori, X, Y):
    """Landscape panel point (y down) -> the point on the upright portrait screen."""
    return (480 - Y, X) if ori == 2 else (Y, 720 - X)


def touch_press(mode, X, Y):
    """Python model of the press decision: (state, ox, oy)."""
    ori = mode & 3
    px, py = portrait_point(ori, X, Y)
    if mode & MINI_MODE:
        return (T_MINI, 0, 0) if py >= MINI_Y else (T_CANCEL, 0, 0)
    live, menu, slider = touch_tables()
    for kind, a0, a1, a2, a3, ox, oy in (menu if mode & MENU_MODE else slider if mode & SLIDER_MODE else live):
        if kind == T_MAPPED and a0 <= px < a2 and a1 <= py < a3:
            return T_MAPPED, ox, oy
        if kind == T_CANCEL and a0 <= X < a2 and a1 <= Y < a3:
            return T_CANCEL, 0, 0
    return (T_CANCEL if mode & MENU_MODE else T_NONE), 0, 0


def touch_map(mode, state, ox, oy, event):
    """Python model of portrait_touch for one event: (new event bytes, new state, ox, oy)."""
    e = bytearray(event)
    flag, kind = e[8], e[9]
    X, Y = struct.unpack_from('<2H', e, 0x12)
    ori = mode & 3
    if kind == 9 or not ori:
        return bytes(e), T_NONE, ox, oy
    if kind == 1 and flag == 1:
        state, ox, oy = touch_press(mode, X, Y)
    if state == T_CANCEL:
        e[8], e[9] = 0, 9
    elif state in (T_MAPPED, T_MINI):
        px, py = portrait_point(ori, X, Y)
        if state == T_MAPPED:
            nx, ny = px + ox, py + oy
        else:
            nx, ny = (px * 3) >> 1, ((py - MINI_Y) * 3) >> 1
        struct.pack_into('<2H', e, 0x12, max(0, min(719, nx)), max(0, min(479, ny)))
    if flag == 0 or kind == 3:
        state = T_NONE
    return bytes(e), state, ox, oy


def turn_code(ori, code):
    """Python model of the setting-page key turn at the action entry."""
    if 2 <= code <= 5:
        return (CODE_TURN[ori] >> ((code - 2) * 4)) & 15
    return code

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

SIZES = dict(portrait_f2fix=96, portrait_fix2f=96, portrait_focus=96, portrait_locate=768,
             portrait_rect=320, portrait_image=320, portrait_image3=320, portrait_text=448,
             portrait_hook=768, portrait_log=512, portrait_touch=640,
             portrait_hprim=128, portrait_hist=1536, portrait_hmask=192)
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


def locate_slider(x, y, w, h):
    """Python model of portrait_locate on a slider page: SLIDER_LAYOUT first, then the live-view rules."""
    return locate(x, y, w, h, SLIDER_LAYOUT + LAYOUT)


def locate(x, y, w, h, rules=LAYOUT):
    """Python model of portrait_locate: (status, px, py, pw, ph); status 0 keeps the command, 2 is a bar."""
    if w >= FULL_W and h >= FULL_H:
        return 0, x, y, w, h
    cy = y + (h >> 1)
    cx = x + (w >> 1)
    for cx0, cx1, cy0, cy1, dx, dy, status in rules:
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


def locate_menu(x, y, w, h):
    """Python model of the i-menu branch of portrait_locate; status 3: not part of the menu (clears are dropped)."""
    if w >= FULL_W and h >= FULL_H:
        return 0, x, y, w, h
    bx, by, bw, bh = MENU_BACKDROP
    if x == bx and w >= 700 and 260 <= y <= 300:
        return 1, 0, MENU_GRID_Y, 480, 3 * MENU_CELL_H
    cy = y + (h >> 1)
    if MENU_TOP <= cy < MENU_BAR[0]:
        row = 0 if cy < MENU_SPLIT else 1
        if w <= 2:   # a vertical line is the right edge of its cell (x 0: the left edge of the first)
            col = 0 if x < 2 else (x + 2) // MENU_CELL_W - 1
        else:
            col = (x + (w >> 1)) // MENU_CELL_W if x + (w >> 1) >= 0 else 0
        col = max(0, min(5, col))
        slot = row * 6 + col
        return (1, x + MENU_CELL_W * ((slot & 3) - col),
                y + (MENU_GRID_Y - MENU_TOP) + MENU_CELL_H * ((slot >> 2) - row), w, h)
    if MENU_TITLE[0] <= cy < MENU_TITLE[1]:
        return 1, x + MENU_TITLE[2], y + MENU_TITLE[3], w, h
    if cy >= MENU_BAR[0] and x >= MENU_BAR[1]:
        return 1, x + MENU_BAR[2], y + MENU_BAR[3], w, h
    return 3, x, y, w, h


def nav_target(ori, direction, slot):
    """Python model of the i-menu key hook: the slot a physical direction moves to in the portrait 4x3 grid."""
    turned = (NAV_TURN[ori] >> (direction * 4)) & 15
    col, row = slot & 3, slot >> 2
    if turned == 0:
        row = (row + 2) % 3
    elif turned == 1:
        row = (row + 1) % 3
    elif turned == 2:
        col = (col + 1) & 3
    else:
        col = (col + 3) & 3
    return row * 4 + col


def panel_box(ori, px, py, pw, ph):
    """Portrait box to the physical landscape rectangle (x, y-up, w, h)."""
    if ori == 2:
        return py, px, ph, pw
    return 720 - py - ph, 480 - px - pw, ph, pw


def hist_page_test(reg, pages, yes):
    """Branch to yes when the DISP byte in reg is one of pages."""
    return ''.join(f'cmp {reg},#{page};beq {yes};' for page in pages)


def hist_sources(f, hst, pages=(HIST_PAGE,)):
    """The portrait histogram (modelled by hist_bars / hist_prims); hst: the block's offset in the state block."""
    code = {}
    bx = {ori: hist_box(ori, HIST_X, HIST_Y, HIST_W, HIST_H) for ori in (1, 2)}
    # r0 px, r1 py, r2 w, r3 h, r4 (the caller's) -> histogram block (H_ORI, H_COLOR, H_FN): one stock
    # rectangle or clear, called past the hooks, with the box turned to the panel.
    code['portrait_hprim'] = f"""push {{r4-r6,lr}};sub sp,#32;ldr r5,[r4,#{H_ORI}];cmp r5,#2;bne grip1;
        str r1,[sp];str r0,[sp,#4];str r3,[sp,#8];str r2,[sp,#12];b colour;
        grip1:rsb r6,r1,#720;sub r6,r6,r3;str r6,[sp];rsb r6,r0,#480;sub r6,r6,r2;str r6,[sp,#4];str r3,[sp,#8];str r2,[sp,#12];
        colour:ldr r6,[r4,#{H_COLOR}];str r6,[sp,#16];movs r6,#0;str r6,[sp,#20];str r6,[sp,#24];str r6,[sp,#28];
        ldr r6,[r4,#{H_FN}];mov r0,sp;blx r6;add sp,#32;pop {{r4-r6,pc}};"""

    def prim(fn, x, y, w, h, colour):
        """x, y, w, h: registers or constants."""
        def load(reg, v):
            return f'movw {reg},#{v};' if isinstance(v, int) else f'mov {reg},{v};'
        return (f"{c('r0', colour)}str r0,[r4,#{H_COLOR}];{c('r0', f[fn] | 1)}str r0,[r4,#{H_FN}];"
                + load('r0', x) + load('r1', y) + load('r2', w) + load('r3', h) + n(f['portrait_hprim']))
    lw = HIST_LW
    lines = ''.join(prim('raw_rect', HIST_X + i * (HIST_W // 4), HIST_Y, lw, HIST_H, HIST_LINE) for i in (1, 2, 3))
    lines += prim('raw_rect', HIST_X, HIST_Y, HIST_W, lw, HIST_LINE) + prim('raw_rect', HIST_X, HIST_Y + HIST_H - lw, HIST_W, lw, HIST_LINE)
    lines += prim('raw_rect', HIST_X, HIST_Y, lw, HIST_H, HIST_LINE) + prim('raw_rect', HIST_X + HIST_W - lw, HIST_Y, lw, HIST_H, HIST_LINE)
    # r0 state block, r1 grip (1/2), r2 -> the current clear's panel box (x, y-up, w, h), already
    # turned. Called on screen-layer clears while upright. r4 block, r5 bar index, r6 height,
    # r8 changed, r9 full, r10 grip, r11 box (then the column x); the bars on the stack.
    code['portrait_hist'] = f"""push {{r4-r11,lr}};sub sp,#72;ldr r9,[r0,#{R_MODE}];{c('r3',hst)}add r4,r0,r3;mov r10,r1;mov r11,r2;
        movs r5,#0;tst r9,#{LAYOUT_BITS};bne decided;ldr r0,[r4,#{H_SAVED}];cmp r0,#0;beq decided;
        {c('r0',HIST_DISP)}ldrb r0,[r0];{hist_page_test('r0', pages, 'on_page')}b decided;on_page:movs r5,#1;
        decided:ldr r6,[r4,#{H_ON}];cmp r6,#0;beq fresh;cmp r5,#0;beq erase;cmp r6,r10;beq fresh;
        erase:str r6,[r4,#{H_ORI}];{c('r0',VG_BLEND_MODE)}{n(VG_GETI)}str r0,[r4,#{H_BLEND}];movw r0,#{VG_BLEND_MODE};movw r1,#{VG_BLEND_SRC};{n(VG_SETI)}{prim('raw_rect', HIST_X, HIST_Y, HIST_W, HIST_H, 0)}movw r0,#{VG_BLEND_MODE};ldr r1,[r4,#{H_BLEND}];{n(VG_SETI)}movs r0,#0;str r0,[r4,#{H_ON}];
        fresh:cmp r5,#0;beq done;
        cmp r10,#2;bne own1;movw r0,#{bx[2][0]};movw r1,#{bx[2][1]};b own;own1:movw r0,#{bx[1][0]};movw r1,#{bx[1][1]};
        own:ldm r11,{{r2,r3,r6,r7}};cmp r6,#0;ble clear_ok;cmp r7,#0;ble clear_ok;
        add r8,r0,#{HIST_H};cmp r2,r8;bge clear_ok;add r8,r2,r6;cmp r0,r8;bge clear_ok;
        add r8,r1,#{HIST_W};cmp r3,r8;bge clear_ok;add r8,r3,r7;cmp r1,r8;bge clear_ok;
        movs r0,#1;str r0,[r4,#{H_DIRTY}];ldr r0,[r4,#{H_OVERLAP}];adds r0,#1;str r0,[r4,#{H_OVERLAP}];b done;
        clear_ok:{c('r0',MS_CLOCK)}ldr r8,[r0];
        ldr r0,[r4,#{H_ON}];cmp r0,#0;beq go;ldr r0,[r4,#{H_DIRTY}];cmp r0,#0;bne go;
        ldr r0,[r4,#{H_MS}];sub r0,r8,r0;cmp r0,#{HIST_INTERVAL};blo done;
        go:str r8,[r4,#{H_MS}];
        {c('r0',HIST_BINS)}movs r1,#0;movs r2,#0;movs r3,#0;
        total:ldr r6,[r0,r3,lsl #2];add r1,r1,r6;cmp r6,r2;it hi;movhi r2,r6;adds r3,#1;cmp r3,#256;blo total;
        lsr r1,r1,#6;cmp r2,r1;it hi;movhi r2,r1;lsl r7,r2,#2;
        movs r5,#0;
        bar:add r1,r0,r5,lsl #4;ldm r1,{{r1,r2,r3,r6}};add r1,r1,r2;add r1,r1,r3;add r1,r1,r6;
        movs r2,#{HIST_H};mul r1,r1,r2;movs r6,#0;cmp r7,#0;beq stored;
        divide:cmp r6,#{HIST_H};bhs stored;cmp r1,r7;blo stored;sub r1,r1,r7;adds r6,#1;b divide;
        stored:strb r6,[sp,r5];adds r5,#1;cmp r5,#64;blo bar;
        ldr r0,[r4,#{H_ON}];movs r9,#1;cmp r0,#0;beq full_set;ldr r9,[r4,#{H_DIRTY}];
        full_set:str r10,[r4,#{H_ORI}];{c('r0',VG_BLEND_MODE)}{n(VG_GETI)}str r0,[r4,#{H_BLEND}];movw r0,#{VG_BLEND_MODE};movw r1,#{VG_BLEND_SRC};{n(VG_SETI)}movs r8,#0;cmp r9,#0;beq columns;movs r8,#1;
        ldr r0,[r4,#{H_FULL}];adds r0,#1;str r0,[r4,#{H_FULL}];
        {prim('raw_rect', HIST_X, HIST_Y, HIST_W, HIST_H, HIST_BG)}
        columns:movs r5,#0;
        column:ldrb r6,[sp,r5];cmp r9,#0;bne draw_bar;add r0,r4,#{H_PREV};ldrb r0,[r0,r5];cmp r0,r6;beq next_bar;
        movs r8,#1;movs r0,#{HIST_CW};mul r11,r5,r0;add r11,r11,#{HIST_X};
        {prim('raw_rect', 'r11', HIST_Y, HIST_CW, HIST_H, HIST_BG)}
        draw_bar:ldrb r6,[sp,r5];cmp r6,#0;beq next_bar;movs r0,#{HIST_CW};mul r11,r5,r0;add r11,r11,#{HIST_X};movw r7,#{HIST_Y + HIST_H};sub r7,r7,r6;
        {prim('raw_rect', 'r11', 'r7', HIST_CW, 'r6', HIST_BAR)}
        next_bar:ldrb r6,[sp,r5];add r0,r4,#{H_PREV};strb r6,[r0,r5];adds r5,#1;cmp r5,#64;blo column;
        cmp r8,#0;beq settled;cmp r9,#0;bne lines;ldr r0,[r4,#{H_PART}];adds r0,#1;str r0,[r4,#{H_PART}];
        lines:{lines}
        settled:movw r0,#{VG_BLEND_MODE};ldr r1,[r4,#{H_BLEND}];{n(VG_SETI)}str r10,[r4,#{H_ON}];movs r0,#0;str r0,[r4,#{H_DIRTY}];
        done:add sp,#72;pop {{r4-r11,pc}};"""
    # r0 state block: hide the stock histogram (save and clear its 9 masks) while the
    # switch is on, upright, the live-view layout (no menu, setting or slider page) and DISP 2;
    # restore them otherwise.
    code['portrait_hmask'] = f"""push {{r4-r6,lr}};{c('r1',hst)}add r4,r0,r1;movs r5,#0;
        ldr r1,[r0];cmp r1,#1;bne decided;ldr r1,[r0,#{R_MODE}];tst r1,#3;beq decided;tst r1,#{LAYOUT_BITS};bne decided;
        {c('r1',HIST_DISP)}ldrb r1,[r1];{hist_page_test('r1', pages, 'on_page')}b decided;on_page:movs r5,#1;
        decided:ldr r1,[r4,#{H_SAVED}];{c('r2',HIST_MASKS)}add r12,r4,#{H_MASKS};movs r3,#0;
        cmp r5,#0;beq restore;cmp r1,#0;bne out;
        save:ldr r6,[r2,r3,lsl #2];str r6,[r12,r3,lsl #2];movs r6,#0;str r6,[r2,r3,lsl #2];adds r3,#1;cmp r3,#{HIST_MASK_COUNT};blo save;
        movs r1,#1;str r1,[r4,#{H_SAVED}];b out;
        restore:cmp r1,#0;beq out;
        back:ldr r6,[r12,r3,lsl #2];str r6,[r2,r3,lsl #2];adds r3,#1;cmp r3,#{HIST_MASK_COUNT};blo back;
        movs r1,#0;str r1,[r4,#{H_SAVED}];
        out:pop {{r4-r6,pc}};"""
    return code


def sources(f, state_offset, runtime, layout, touch=None, slider_layout=None, hst=None, hist_pages=(HIST_PAGE,)):
    if slider_layout is None:
        slider_layout = layout
    if runtime != R_MODE:
        raise ValueError('Runtime words moved')
    k = KINDS
    code = {}
    if touch is not None:
        code['portrait_touch'] = touch_source(f, state_offset, **touch)
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
    # The i menu (view kind 3) has its own branch, modelled by locate_menu; 3 there
    # means the command is not part of the menu.
    code['portrait_locate'] = f'''push {{r4-r8,lr}};mov r12,r0;ldm r0,{{r0-r3}};
        movw r4,#{FULL_W};cmp r2,r4;blt sized;movw r4,#{FULL_H};cmp r3,r4;bge keep;
        sized:{c('r4',VIEW_KIND)}ldrb r4,[r4];cmp r4,#{MENU_VIEW};beq menu;
        {c('r6',layout)}sub r4,r4,#{SLIDER_VIEWS[0]};cmp r4,#{len(SLIDER_VIEWS) - 1};bhi rules;{c('r6',slider_layout)}
        rules:add r4,r0,r2,asr #1;add r5,r1,r3,asr #1;
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
        keep:movs r0,#0;pop {{r4-r8,pc}};
        mini:movw r4,#{MINI_NUM};mul r0,r0,r4;asr r0,r0,#{MINI_SHIFT};mul r1,r1,r4;asr r1,r1,#{MINI_SHIFT};add r1,r1,#{MINI_Y};
        mul r2,r2,r4;asr r2,r2,#{MINI_SHIFT};cmp r2,#1;it lt;movlt r2,#1;
        mul r3,r3,r4;asr r3,r3,#{MINI_SHIFT};cmp r3,#1;it lt;movlt r3,#1;
        stm r12,{{r0-r3}};movs r0,#4;pop {{r4-r8,pc}};
        menu:{c('r4',NAV_TABLE)}ldrb r4,[r4];cmp r4,#0;bne mini;
        cmp r0,#{MENU_BACKDROP[0]};bne m_cy;movw r4,#700;cmp r2,r4;blt m_cy;
        movw r4,#260;cmp r1,r4;blt m_cy;movw r4,#300;cmp r1,r4;bgt m_cy;
        movs r0,#0;movw r1,#{MENU_GRID_Y};movw r2,#480;movw r3,#{3 * MENU_CELL_H};b m_store;
        m_cy:add r5,r1,r3,asr #1;
        movw r4,#{MENU_TOP};cmp r5,r4;blt m_title;movw r4,#{MENU_BAR[0]};cmp r5,r4;bge m_bar;
        movs r6,#0;movw r4,#{MENU_SPLIT};cmp r5,r4;it ge;movge r6,#1;
        cmp r2,#2;bgt m_cx;cmp r0,#2;blt m_col0;adds r4,r0,#2;mvn r8,#0;b m_div;
        m_cx:add r4,r0,r2,asr #1;movs r8,#0;
        m_div:movs r7,#0;
        m_loop:cmp r4,#{MENU_CELL_W};blt m_q;subs r4,#{MENU_CELL_W};adds r7,#1;b m_loop;
        m_q:add r7,r7,r8;cmp r7,#0;bge m_hi;
        m_col0:movs r7,#0;
        m_hi:cmp r7,#5;it gt;movgt r7,#5;
        add r4,r6,r6,lsl #1;add r4,r7,r4,lsl #1;
        and r5,r4,#3;sub r5,r5,r7;movs r8,#{MENU_CELL_W};mla r0,r5,r8,r0;
        lsr r5,r4,#2;sub r5,r5,r6;movs r8,#{MENU_CELL_H};mla r1,r5,r8,r1;add r1,r1,#{MENU_GRID_Y - MENU_TOP};b m_store;
        m_title:cmp r5,#{MENU_TITLE[0]};blt m_none;sub r0,r0,#{-MENU_TITLE[2]};add r1,r1,#{MENU_TITLE[3]};b m_store;
        m_bar:movw r4,#{MENU_BAR[1]};cmp r0,r4;blt m_none;movw r4,#{-MENU_BAR[2]};sub r0,r0,r4;add r1,r1,#{MENU_BAR[3]};b m_store;
        m_none:movs r0,#3;pop {{r4-r8,pc}};
        m_store:stm r12,{{r0-r3}};movs r0,#1;pop {{r4-r8,pc}};'''
    # r0 -> x, y-up, w, h (+16 rgba for rectangles); r1 orientation; r2 kind.
    # Thin clears (w or h <= THIN) stay where they are: on the live-view page they are
    # only the eight 2-px lines that erase the AF-area corner frame (camera log,
    # 2026-11-11, the same as the Z 5 found). Moving them left the frame behind.
    # In the i menu the grid lines are 2-px clears and rectangles: no thin-clear rule
    # there, and a clear that is not part of the menu (status 3) is dropped, as the
    # live-view icons under the menu must not punch holes into it.
    code['portrait_rect'] = f'''push {{r4-r6,lr}};sub sp,#24;mov r4,r0;mov r5,r1;str r2,[sp,#16];
        cmp r2,#{k['clear']};bne not_thin;{c('r0',VIEW_KIND)}ldrb r0,[r0];cmp r0,#{MENU_VIEW};beq not_thin;
        ldr r0,[r4,#8];cmp r0,#{THIN};ble done;ldr r0,[r4,#12];cmp r0,#{THIN};ble done;
        not_thin:ldr r2,[sp,#16];cmp r2,#{k['rect']};bne geometry;ldr r0,[r4,#16];{n(f['portrait_focus'])}cmp r0,#0;bne done;
        geometry:ldm r4,{{r0-r3}};rsb r1,r1,#480;sub r1,r1,r3;
        stm sp,{{r0-r3}};mov r0,sp;{n(f['portrait_locate'])}cmp r0,#0;beq done;
        cmp r0,#3;bne moved;ldr r0,[sp,#16];cmp r0,#{k['clear']};bne done;movs r0,#0;str r0,[r4,#8];str r0,[r4,#12];b done;
        moved:mov r12,r0;
        ldm sp,{{r0-r3}};cmp r2,#480;ble placed;movs r0,#0;movw r2,#480;
        cmp r12,#2;bne placed;add r3,r3,#{ROW};
        placed:cmp r5,#2;bne other;str r1,[r4];str r0,[r4,#4];b sizes;
        other:rsb r6,r1,#720;sub r6,r6,r3;str r6,[r4];rsb r6,r0,#480;sub r6,r6,r2;str r6,[r4,#4];
        sizes:str r3,[r4,#8];str r2,[r4,#12];
        done:add sp,#24;pop {{r4-r6,pc}};'''
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
        stm sp,{{r0-r3}};mov r0,sp;{n(f['portrait_locate'])}cmp r0,#0;beq done;cmp r0,#3;beq done;
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
        stm sp,{{r0-r3}};mov r0,sp;{n(f['portrait_locate'])}cmp r0,#0;beq done;cmp r0,#3;beq done;
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
        stm sp,{{r0-r3}};mov r0,sp;{n(f['portrait_locate'])}cmp r0,#0;beq done;cmp r0,#3;beq done;str r0,[sp,#16];
        cmp r0,#4;bne sized;movw r8,#{MINI_NUM};
        mov r0,r6;{n(f['portrait_f2fix'])}mul r0,r0,r8;asr r0,r0,#{MINI_SHIFT};{n(f['portrait_fix2f'])}mov r6,r0;
        mov r0,r7;{n(f['portrait_f2fix'])}mul r0,r0,r8;asr r0,r0,#{MINI_SHIFT};{n(f['portrait_fix2f'])}mov r7,r0;
        sized:ldm sp,{{r0-r3}};and r12,r10,#255;add r8,r12,r0,lsl #8;add r1,r1,r3;lsl r9,r1,#8;
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
    if hst is None:
        hmask = hist_off = hist_draw = ''
    else:
        code.update(hist_sources(f, hst, hist_pages))
        hmask = f"mov r0,r6;{n(f['portrait_hmask'])}"
        hist_off = f"{c('r1',hst + H_ON)}str r0,[r6,r1];"
        hist_draw = f"mov r0,r6;mov r1,r7;ldr r2,[r5];{n(f['portrait_hist'])}"
    code['portrait_hook'] = f'''push {{r4-r8,lr}};mov r4,r0;mov r5,r1;
        {n(f['get'])}cmp r0,#0;beq out;{c('r1',state_offset)}add r6,r0,r1;
        {hmask}ldr r0,[r6];cmp r0,#1;bne out;
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
        phase:{c('r1',VIEW_KIND)}ldrb r0,[r1];cmp r0,#0;beq view_ok;cmp r0,#{MENU_VIEW};beq view_ok;
        sub r0,r0,#{SLIDER_VIEWS[0]};cmp r0,#{len(SLIDER_VIEWS) - 1};bhi context;
        view_ok:{c('r1',PHOTO_BACKEND)}ldr r0,[r1];cmp r0,#1;bne context;
        {c('r1',OVERLAY_BUSY[0])}ldr r0,[r1];cmp r0,#0;bne context;
        {c('r1',OVERLAY_BUSY[1])}ldr r0,[r1];cmp r0,#0;bne context;movs r8,#1;
        context:movs r0,#0;cmp r8,#0;beq mode;cmp r7,#1;beq portrait;cmp r7,#2;bne mode;
        portrait:mov r0,r7;{c('r1',VIEW_KIND)}ldrb r1,[r1];cmp r1,#{MENU_VIEW};beq menu_bits;
        sub r1,r1,#{SLIDER_VIEWS[0]};cmp r1,#{len(SLIDER_VIEWS) - 1};it ls;orrls r0,r0,#{SLIDER_MODE};b mode;
        menu_bits:{c('r1',NAV_TABLE)}ldrb r1,[r1];cmp r1,#0;ite eq;orreq r0,r0,#{MENU_MODE};orrne r0,r0,#{MINI_MODE};
        mode:ldr r2,[r6,#{R_MODE}];cmp r0,r2;beq same;str r0,[r6,#{R_MODE}];
        movs r2,#1;str r2,[r6,#{R_PENDING}];str r2,[r6,#{R_REPAINT}];
        same:and r7,r0,#3;cmp r8,#0;bne live;movs r2,#0;str r2,[r6,#{R_PENDING}];b out;
        live:{c('r1',TARGET)}ldrb r0,[r1];cmp r0,#{OSD_TARGET};bne out;
        cmp r4,#{k['clear']};bne draw;ldr r0,[r6,#{R_PENDING}];cmp r0,#0;beq draw;
        movs r0,#0;str r0,[r6,#{R_PENDING}];{hist_off}ldr r1,[r5];str r0,[r1];str r0,[r1,#4];
        movw r0,#720;str r0,[r1,#8];movw r0,#480;str r0,[r1,#12];b out;
        draw:cmp r7,#0;beq out;
        cmp r4,#{k['rect']};bne not_rect;ldr r0,[r5,#20];{c('r1',CLEAR_INNER_RECT_RETURN)}cmp r0,r1;beq out;
        ldr r0,[r5];mov r1,r7;mov r2,r4;{n(f['portrait_rect'])}b out;
        not_rect:cmp r4,#{k['clear']};bne not_clear;ldr r0,[r5];mov r1,r7;mov r2,r4;{n(f['portrait_rect'])}{hist_draw}b out;
        not_clear:cmp r4,#{k['frame']};bne not_frame;mov r0,r5;mov r1,r7;mov r2,r4;{n(f['portrait_rect'])}b out;
        not_frame:cmp r4,#{k['image3']};bne not_image3;mov r0,r5;mov r1,r7;{n(f['portrait_image3'])}b out;
        not_image3:cmp r4,#{k['text']};bne image;ldr r0,[r5];mov r1,r7;{n(f['portrait_text'])}b out;
        image:ldr r0,[r5];mov r1,r7;mov r2,r4;{n(f['portrait_image'])}
        out:pop {{r4-r8,pc}};'''
    return code


def touch_source(f, state_offset, offset, magic, capacity, live, menu, slider):
    """r0 -> the touch event at 104F1AD4; rewritten in place (modelled by touch_map), recorded while upright.

    r4 event, r5 state block, r6 touch header (+8 state, +12/+14 offset), r7 state,
    r8 contact, r9 type, r10 grip, r11 layout mode, r2/r3 the portrait point."""
    if capacity & (capacity - 1):
        raise ValueError('Touch ring capacity must be a power of two')
    return f'''push {{r4-r11,lr}};mov r4,r0;
        {n(f['get'])}cmp r0,#0;beq out;{c('r1',state_offset)}add r5,r0,r1;ldr r0,[r5];cmp r0,#1;bne out;
        {c('r1',offset)}add r6,r5,r1;
        ldr r11,[r5,#{R_MODE}];ands r10,r11,#3;bne upright;movs r0,#0;str r0,[r6,#8];b out;
        upright:sub sp,#8;ldrb r8,[r4,#8];ldrb r9,[r4,#9];
        ldrh r0,[r4,#0x12];strh r0,[sp];ldrh r1,[r4,#0x14];strh r1,[sp,#2];
        cmp r10,#2;bne grip1;rsb r2,r1,#480;mov r3,r0;b point;
        grip1:mov r2,r1;rsb r3,r0,#720;
        point:cmp r9,#9;beq reset;cmp r9,#1;bne follow;cmp r8,#1;bne follow;
        tst r11,#{MINI_MODE};beq tables;movs r7,#{T_MINI};cmp r3,#{MINI_Y};it lt;movlt r7,#{T_CANCEL};b decided;
        tables:tst r11,#{MENU_MODE};bne menu;tst r11,#{SLIDER_MODE};bne slider;{c('r12',live)}movs r7,#{T_NONE};b scan;
        slider:{c('r12',slider)}movs r7,#{T_NONE};b scan;
        menu:{c('r12',menu)}movs r7,#{T_CANCEL};
        scan:ldrsh lr,[r12];movw r0,#{TOUCH_END};cmp lr,r0;beq decided;
        cmp lr,#{T_MAPPED};bne landscape;
        ldrsh r0,[r12,#2];cmp r2,r0;blt next;ldrsh r0,[r12,#6];cmp r2,r0;bge next;
        ldrsh r0,[r12,#4];cmp r3,r0;blt next;ldrsh r0,[r12,#8];cmp r3,r0;bge next;
        ldrsh r0,[r12,#10];strh r0,[r6,#12];ldrsh r0,[r12,#12];strh r0,[r6,#14];movs r7,#{T_MAPPED};b decided;
        landscape:ldrh r1,[sp];ldrh lr,[sp,#2];
        ldrsh r0,[r12,#2];cmp r1,r0;blt next;ldrsh r0,[r12,#6];cmp r1,r0;bge next;
        ldrsh r0,[r12,#4];cmp lr,r0;blt next;ldrsh r0,[r12,#8];cmp lr,r0;bge next;
        movs r7,#{T_CANCEL};b decided;
        next:adds r12,#16;b scan;
        decided:str r7,[r6,#8];
        follow:ldr r7,[r6,#8];
        cmp r7,#{T_CANCEL};bne not_cancel;movs r0,#0;strb r0,[r4,#8];movs r0,#9;strb r0,[r4,#9];b finish;
        not_cancel:cmp r7,#{T_MAPPED};bne not_mapped;ldrsh r0,[r6,#12];add r0,r2,r0;ldrsh r1,[r6,#14];add r1,r3,r1;b store;
        not_mapped:cmp r7,#{T_MINI};bne finish;
        add r0,r2,r2,lsl #1;asr r0,r0,#1;sub r1,r3,#{MINI_Y};add r1,r1,r1,lsl #1;asr r1,r1,#1;
        store:cmp r0,#0;it lt;movlt r0,#0;movw r12,#719;cmp r0,r12;it gt;movgt r0,r12;
        cmp r1,#0;it lt;movlt r1,#0;movw r12,#479;cmp r1,r12;it gt;movgt r1,r12;
        strh r0,[r4,#0x12];strh r1,[r4,#0x14];
        finish:cmp r8,#0;beq reset;cmp r9,#3;bne record;
        reset:movs r0,#0;str r0,[r6,#8];
        record:{c('r0',magic)}str r0,[r6];ldr r0,[r6,#4];and r1,r0,#{capacity - 1};adds r0,#1;str r0,[r6,#4];
        add r1,r6,r1,lsl #4;adds r1,#16;
        ldrh r0,[sp];strh r0,[r1];ldrh r0,[sp,#2];strh r0,[r1,#2];
        ldrh r0,[r4,#0x12];strh r0,[r1,#4];ldrh r0,[r4,#0x14];strh r0,[r1,#6];
        strb r9,[r1,#8];strb r8,[r1,#9];{c('r0',VIEW_KIND)}ldrb r0,[r0];strb r0,[r1,#10];
        ldr r0,[r6,#8];strb r0,[r1,#11];str r11,[r1,#12];
        add sp,#8;
        out:pop {{r4-r11,pc}};'''


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
    if (control['touch_capacity'], TCH_CAPACITY) != (TCH_CAPACITY, TCH_CAPACITY):
        raise ValueError('Touch ring layout differs between the page and the hooks')
    # Entry C: the shooting loop calls it for view kinds below 0x11 (cmp r0,#0x11; bhs to entry D).
    if (im.read(TOUCH, len(TOUCH_EXPECTED)) != TOUCH_EXPECTED
            or im.read(TOUCH_CALL - 6, 10) != bytes.fromhex('1128' '03a8' '02d2' 'faf124fa')  # ...; bl 104F1AD4
            or im.read(TOUCH_EVENT_COPY, 10) != bytes.fromhex('10b50c46014685483c22')):
        raise ValueError('Unexpected shooting touch entry')
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    # Stock rectangle and clear past the hooks (the displaced window replayed, then the
    # stock body): the portrait histogram draws with these.
    for name, kind in (('raw_rect', 'rect'), ('raw_clear', 'clear')):
        _, site, expected, replay = next(h for h in HOOKS if h[0] == kind)
        f[name] = plan.address
        plan.alloc(assemble(f"{replay}ldr.w pc,={(site + len(bytes.fromhex(expected))) | 1};", f[name], 32))
    if (control['histogram_size'] < H_PREV + 64 or im.words(0x1073F830 + 31 * 4, 1)[0] != HIST_MASKS - 4
            or im.words(0x104501A8, 1)[0] != 0xC2016A68):
        raise ValueError('Unexpected histogram widget or state layout')
    def rules(table):
        return b''.join(struct.pack('<8h', *rule, 0) for rule in table) + struct.pack('<8h', 0x7FFF, *[0] * 7)
    layout = plan.alloc(rules(LAYOUT))
    slider_layout = plan.alloc(rules(SLIDER_LAYOUT + LAYOUT))
    live_rows, menu_rows, slider_rows = touch_tables()
    def rows(table):
        return b''.join(struct.pack('<8h', *row, 0) for row in table) + struct.pack('<8h', TOUCH_END, *[0] * 7)
    touch = dict(offset=control['touch_offset'], magic=TCH_MAGIC, capacity=TCH_CAPACITY,
                 live=plan.alloc(rows(live_rows)), menu=plan.alloc(rows(menu_rows)), slider=plan.alloc(rows(slider_rows)))
    # liveview-dual shows the histogram on DISP 2, 3 and 5; without it only on 2.
    hist_pages = HIST_DUAL_PAGES if 'liveview_dual' in plan.features else (HIST_PAGE,)
    for name, source in sources(f, control['state_offset'], control['runtime_offset'], layout, touch, slider_layout,
                                control['histogram_offset'], hist_pages).items():
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
            # The 2/3 setting page is upright: a direction code (2..5) turns with the screen
            # (CODE_TURN, modelled by turn_code) before the stock handler sees it.
            act = f'''
            ldr r1,[r2,#{R_MODE}];tst r1,#{MINI_MODE};bne mini_keys;tst r1,#{SLIDER_MODE};beq no_turn;
            {c('r0',VIEW_KIND)}ldrb r0,[r0];sub r0,r0,#{SLIDER_VIEWS[0]};cmp r0,#{len(SLIDER_VIEWS) - 1};bhi no_turn;b turn_keys;
            mini_keys:{c('r0',VIEW_KIND)}ldrb r0,[r0];cmp r0,#{MENU_VIEW};bne no_turn;
            {c('r0',NAV_TABLE)}ldrb r0,[r0];cmp r0,#0;beq no_turn;
            turn_keys:ldr r0,[sp,#8];subs r3,r0,#2;cmp r3,#3;bhi no_turn;
            and r1,r1,#3;movw r0,#{CODE_TURN[1]};cmp r1,#2;bne turn;movw r0,#{CODE_TURN[2]};
            turn:lsl r3,r3,#2;lsr r0,r0,r3;and r0,r0,#15;str r0,[sp,#8];
            no_turn:
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
    # i-menu direction keys: portrait, the slot moves in the 4x3 layout (nav_target).
    if (im.read(NAV_MOVE, len(NAV_MOVE_EXPECTED)) != NAV_MOVE_EXPECTED
            or im.words(((NAV_MOVE + 4) & ~3) + 0x3B0, 1)[0] != NAV_TABLE
            or im.read(CURSOR_GET, len(CURSOR_GET_EXPECTED)) != CURSOR_GET_EXPECTED
            or im.read(CURSOR_SET, len(CURSOR_SET_EXPECTED)) != CURSOR_SET_EXPECTED):
        raise ValueError('Unexpected i-menu cursor functions')
    state = control['state_offset']
    entry = plan.address
    code = assemble(f'''push {{r1-r7,lr}};mov r4,r0;cmp r4,#3;bhi stock;
        {n(f['get'])}cmp r0,#0;beq stock;{c('r1',state)}add r5,r0,r1;ldr r0,[r5];cmp r0,#1;bne stock;
        ldr r6,[r5,#{R_MODE}];tst r6,#{MENU_MODE};beq stock;and r6,r6,#3;
        {c('r0',VIEW_KIND)}ldrb r0,[r0];cmp r0,#{MENU_VIEW};bne stock;
        {c('r0',NAV_TABLE)}ldrb r0,[r0];cmp r0,#0;bne stock;
        {c('r7',NAV_BLOCK)}ldr r0,[r7];cmp r0,#12;bne stock;
        {n(CURSOR_GET)}cmp r0,#11;bhi stock;
        movw r1,#{NAV_TURN[1]};cmp r6,#2;bne turned;movw r1,#{NAV_TURN[2]};
        turned:lsl r2,r4,#2;lsr r1,r1,r2;and r1,r1,#15;
        and r2,r0,#3;lsr r3,r0,#2;
        cmp r1,#0;bne not_up;cmp r3,#0;ite eq;moveq r3,#2;subne r3,r3,#1;b place;
        not_up:cmp r1,#1;bne not_down;cmp r3,#2;ite eq;moveq r3,#0;addne r3,r3,#1;b place;
        not_down:cmp r1,#2;bne left;adds r2,#1;and r2,r2,#3;b place;
        left:adds r2,#3;and r2,r2,#3;
        place:add r0,r2,r3,lsl #2;str r0,[r7,#4];{n(CURSOR_SET)}
        movs r0,#0;pop {{r1-r7,pc}};
        stock:mov r0,r4;pop {{r1-r7,lr}};
        {c('r1',NAV_TABLE)}ldrb r2,[r1];adds r1,r1,#4;add.w r1,r1,r2,lsl #3;
        ldr.w pc,={NAV_MOVE_RESUME | 1};''', entry, 320)
    plan.alloc(code)
    plan.edit(NAV_MOVE, jump(NAV_MOVE, entry, len(NAV_MOVE_EXPECTED)), 'Portrait UI: i-menu keys follow the screen', NAV_MOVE_EXPECTED)
    hooks.append(dict(name='menu_keys', site=hex(NAV_MOVE), entry=hex(entry | 1), window=len(NAV_MOVE_EXPECTED)))
    # Touch entry C: the event is rewritten in place, then the stock window runs as it was.
    entry = plan.address
    code = assemble(f'''push {{r0-r3,r12,lr}};mrs r0,apsr;push {{r0,r1}};ldr r0,[sp,#8];{n(f['portrait_touch'])}
        pop {{r0,r1}};msr apsr_nzcvq,r0;pop {{r0-r3,r12,lr}};
        push.w {{r4-r9,lr}};sub sp,#0x4c;mov r6,r0;ldr.w pc,={(TOUCH + len(TOUCH_EXPECTED)) | 1};''', entry, STUB_SIZE)
    plan.alloc(code)
    plan.edit(TOUCH, jump(TOUCH, entry, len(TOUCH_EXPECTED)), 'Portrait UI: touch follows the portrait layout', TOUCH_EXPECTED)
    hooks.append(dict(name='touch', site=hex(TOUCH), entry=hex(entry | 1), window=len(TOUCH_EXPECTED)))
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
        menu=dict(view_kind=MENU_VIEW, backdrop=list(MENU_BACKDROP), grid_top=MENU_TOP, cell=[MENU_CELL_W, MENU_CELL_H],
                  portrait_grid_top=MENU_GRID_Y, title=list(MENU_TITLE), bar=list(MENU_BAR), mode_bit=MENU_MODE,
                  keys=dict(site=hex(NAV_MOVE), navigation=hex(NAV_BLOCK), cursor_get=hex(CURSOR_GET), cursor_set=hex(CURSOR_SET),
                            turn={str(k): hex(v) for k, v in NAV_TURN.items()}),
                  grid_state=dict(address=hex(NAV_TABLE), value=0),
                  source='camera USB readout and RAM diffs, 2026-11-15',
                  camera_verified='2026-11-15: grid, title, bar, keys and wrap in both grips; level stock'),
        setting_pages=dict(mode_bit=MINI_MODE, scale='2/3 as (v*683)>>10', portrait_top=MINI_Y,
                           keys=dict(site=hex(EVENT_HOOKS[0][1]), codes='right 2, left 3, up 4, down 5',
                                     turn={str(k): hex(v) for k, v in CODE_TURN.items()}),
                           camera_verified=False),
        slider_pages=dict(view_kinds=list(SLIDER_VIEWS), mode_bit=SLIDER_MODE, rules=[list(r) for r in SLIDER_LAYOUT],
                          buttons=[[list(box), dx] for box, dx in SLIDER_BUTTONS], row_dy=SLIDER_DY,
                          keys='direction codes turn with the screen, as on the 2/3 setting pages',
                          source='camera USB draw log, 2026-11-17 (shutter 12, aperture 11, ISO 13)',
                          camera_verified='2026-11-17: layout, buttons, bottom-bar switch, corner button and keys in both grips'),
        histogram=dict(bins=hex(HIST_BINS), masks=hex(HIST_MASKS), mask_count=HIST_MASK_COUNT, disp=hex(HIST_DISP),
                       pages=list(hist_pages), clock=hex(MS_CLOCK), box=[HIST_X, HIST_Y, HIST_W, HIST_H], interval_ms=HIST_INTERVAL,
                       raw_rect=hex(f['raw_rect'] | 1), raw_clear=hex(f['raw_clear'] | 1), block_offset=control['histogram_offset'],
                       blend=dict(mode=hex(VG_BLEND_MODE), value=hex(VG_BLEND_SRC), set=hex(VG_SETI), get=hex(VG_GETI),
                                  why='v24 camera: vgClear from the hook left old bars; the stock SRC-blend rectangle path replaces pixels'),
                       source='camera USB RAM readout, 2026-11-17; drawing after the Z 5 (wonder-lantern-z5 PORTRAIT_UI 5)',
                       camera_verified='2026-11-17 (v26): DISP 2, 3 and 5, one upright histogram, no trails'),
        touch=dict(site=hex(TOUCH), caller=hex(TOUCH_CALL), view_limit=hex(TOUCH_VIEW_LIMIT),
                   event='+8 contact, +9 type, +0x12/+0x14 landscape point',
                   live_controls={name: list(box) for name, box in LIVE_CONTROLS}, live_cancel=[list(b) for b in LIVE_CANCEL],
                   menu_bar=list(MENU_BAR_BOX), ring=dict(magic=hex(TCH_MAGIC), capacity=TCH_CAPACITY),
                   source='static analysis of 102F7678/104F1AD4, QEMU (menu path only: QEMU live view does not deliver touch)',
                   camera_verified=False),
        scope='photo live view, its i menu (4x3), the i-menu setting pages (2/3) and the shutter/aperture/ISO slider pages on the rear screen, with touch; menus, playback and movie stay landscape',
        default=0, inert_when_off=True,
        camera_verified=False, osd_target_camera_verified=False, orientation_side_camera_verified=False)
