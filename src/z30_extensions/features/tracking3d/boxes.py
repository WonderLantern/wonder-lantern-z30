# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""3D tracking: boxes on every detected eye / face in the tracking mode (screen 1).

Stock draws one subject box at a time and none in screen 1. The AF subject list
0xC259EF18 (camera log AF000061: count at +0x38; 0x40-byte entries from +0x3C,
int16 centre x / y at +6 / +8, size at +0xA / +0xC, eye centre at +0x36 / +0x38
and eye size at +0x3A, 0 without an eye) holds them all.

v49 draws them into the stock UI canvas, in the UI renderer right after its canvas
clear (0x104785D4) and before the element draws (the point the aspect mask
underlay probe proved in QEMU): the boxes refresh with the stock UI, stock icons
and frames draw on top, and the next clear removes them, so nothing is erased by
hand. v41-v48 wrote the live view image surface at the GPU submit boundary; that
layer reached the screen only now and then (camera: low refresh) and reading it
for the erase stalled the live view (v44).

Each pass in the shooting screen (UI phase 1 / 2) the boxes drawn last time are
erased with transparent bars (v50: the renderer does not clear the whole canvas
every pass -- camera photo: trails, and boxes left over in screens 2 / 3). With
the 3D tracking switch on, the tracking box shown (0xC237F684 = 1: screen 1) and
no tracking (0xC237F688 = 0) the renderer's
OpenVG blend mode, scissoring and scissor rectangles are saved, blending is set
to SRC (idempotent if the pass repeats), every subject (at most 8) gets a 2 px
outline made of four filled bars through the stock fill 0x1047743C, and the
state is restored. The eye nearest to the stock selected frame is white, every
other eye / face grey; a subject with an eye gets the eye box.
"""
from ...assembly import constant as c, invoke as n

RENDER_SITE = 0x104785D8
RENDER_EXPECTED = 'bdf912000090bdf91030'   # ldrsh.w r0,[sp,#0x12]; str r0,[sp]; ldrsh.w r3,[sp,#0x10]
RENDER_SIZE = 10
RENDER_CONTINUE = RENDER_SITE + RENDER_SIZE

VG_GETI, VG_SETI, VG_GETIV, VG_SETIV = 0x1050A47C, 0x1050A406, 0x1050A4D0, 0x1050A442
VG_BLEND_MODE, VG_SCISSORING, VG_SCISSOR_RECTS, VG_BLEND_SRC = 0x1104, 0x1131, 0x1106, 0x2000
CLIP_RESET = 0x10476F92      # as the QEMU underlay probe, before its fills
FILL = 0x1047743C            # shape: x, y, w, h (OpenVG, y from the bottom), fill RGBA, stroke RGBA, 2 bytes

UI_SERVICE_PHASE = 0xC181F6FC
SUBJECT_COUNT = 0xC259EF18 + 0x38
SUBJECT_ENTRIES = 0xC259EF18 + 0x3C
ENTRY_BYTES = 0x40
MAX_BOXES = 8
BOX_SHOWN = 0xC237F684
TRACKING = 0xC237F688
GREY = 0xA0A0A0E0            # RGBA
WHITE = 0xFFFFFFE0
THICK = 2
MIN_SIZE = 8
STOCK_FRAME = 0xC237C7B4 + 0xC0   # stock selected frame (x0 y0 x1 y1 int16, tracking units; AF000123)
FRAME_OFFSET, FRAME_TO_LIST = 140, 19   # list = (t - 140) * 19 / 256 ~ (t - 140) / 13.5
EYE_RADIUS = 15              # list units
# list units -> screen pixels. x fitted on the stock frames (AF000123): 13.6 tracking units
# per list unit, tracking -> screen 720 / 5568 (the stock source window); y over the full
# height (AF000061: 0.1733 face cells per list unit, 72 tracking units per cell) = 12.5
# tracking units per list unit, i.e. 1.614 px, anchored where v46/v47 matched (centre).
X_MUL, X_ADD = 447, 15       # px = 1.747 * u + 15
Y_MUL, Y_ADD = 413, 33       # px = 1.614 * u + 33
FACE_HALF_MUL, FACE_HALF_ADD = 182, 9
EYE_HALF = 8
HIST = 24                    # state +24: boxes on the canvas (active, count, 8 x x0 y0 x1 y1 int16)
STATE_BYTES = 8 + 8 + 8 * MAX_BOXES   # after the 16-byte 3D tracking state: passes, boxes, then HIST
SIZE = 1024
HOOK_SIZE = 64
# stack frame of the drawer
SHAPE, TARGET_X, TARGET_Y, TARGET_ON, CLIP, SAVED, NEED, FRAME = 0, 28, 32, 36, 40, 56, 72, 76   # 36 + 76 = 112: 8-byte aligned
# OpenVG state the stock fill changes and the later stock element draws rely on (QEMU:
# without restoring them the info / card icons drew as black blocks)
VG_MATRIX_MODE, VG_IMAGE_MODE, VG_VENDOR_C1A0 = 0x1100, 0x1105, 0xC1A0
# the stock fill ends with vgSetPaint(0, VG_FILL_PATH): keep the renderer's fill paint
VG_GET_PAINT, VG_SET_PAINT, VG_FILL_PATH = 0x1050AD82, 0x1050AD66, 2


def source(f):
    """trk3d_boxes(): called from the renderer hook; preserves r4-r11."""
    return f'''push {{r4-r11,lr}};sub sp,#{FRAME};mov r11,sp;
        {n(f['trk3d_get'])}cmp r0,#0;beq out;mov r4,r0;
        @ the stock canvas is not cleared everywhere every pass (camera, v49: trails, and boxes
        @ left over in screens 2 / 3), so the boxes drawn last time are erased here first.
        @ Outside the shooting screen the canvas belongs to others: forget, never touch.
        {c('r1',UI_SERVICE_PHASE)}ldrb r1,[r1];cmp r1,#1;beq phase_ok;cmp r1,#2;beq phase_ok;
        movs r0,#0;str r0,[r4,#{HIST}];str r0,[r4,#{HIST + 4}];b out;
        phase_ok:
        movs r6,#0;
        ldr r0,[r4];cmp r0,#1;bne need_done;
        {c('r1',BOX_SHOWN)}ldr r1,[r1];cmp r1,#1;bne need_done;
        {c('r1',TRACKING)}ldr r1,[r1];cmp r1,#0;bne need_done;
        {c('r0',SUBJECT_COUNT)}ldr r6,[r0];cmp r6,#{MAX_BOXES};it hi;movhi r6,#{MAX_BOXES};
        need_done:str r6,[sp,#{NEED}];
        cmp r6,#0;bne go;ldr r0,[r4,#{HIST}];cmp r0,#0;beq out;
        go:
        ldr r0,[r4,#16];adds r0,#1;str r0,[r4,#16];
        @ the focused eye target from the stock selected frame
        movs r0,#0;str r0,[sp,#{TARGET_ON}];
        {c('r0',STOCK_FRAME)}ldrsh r1,[r0];ldrsh r2,[r0,#4];cmp r2,r1;ble no_target;
        add r1,r1,r2;asrs r1,r1,#1;subs r1,#{FRAME_OFFSET};movs r12,#{FRAME_TO_LIST};mul r1,r1,r12;asrs r1,r1,#8;
        str r1,[sp,#{TARGET_X}];
        ldrsh r1,[r0,#2];ldrsh r2,[r0,#6];add r1,r1,r2;asrs r1,r1,#1;subs r1,#{FRAME_OFFSET};mul r1,r1,r12;asrs r1,r1,#8;
        str r1,[sp,#{TARGET_Y}];movs r1,#1;str r1,[sp,#{TARGET_ON}];
        no_target:
        @ save the renderer's OpenVG state, replace-blend our bars
        {c('r0',VG_BLEND_MODE)}{n(VG_GETI)}mov r9,r0;
        {c('r0',VG_SCISSORING)}{n(VG_GETI)}mov r10,r0;
        {c('r0',VG_SCISSOR_RECTS)}movs r1,#4;add r2,sp,#{CLIP};{n(VG_GETIV)}
        {c('r0',VG_MATRIX_MODE)}{n(VG_GETI)}str r0,[sp,#{SAVED}];
        {c('r0',VG_IMAGE_MODE)}{n(VG_GETI)}str r0,[sp,#{SAVED + 4}];
        {c('r0',VG_VENDOR_C1A0)}{n(VG_GETI)}str r0,[sp,#{SAVED + 8}];
        movs r0,#{VG_FILL_PATH};{n(VG_GET_PAINT)}str r0,[sp,#{SAVED + 12}];
        movs r0,#0;{n(CLIP_RESET)}
        {c('r0',VG_BLEND_MODE)}{c('r1',VG_BLEND_SRC)}{n(VG_SETI)}
        @ erase the previous boxes: transparent bars (SRC), as the QEMU underlay probe erased
        ldr r0,[r4,#{HIST}];cmp r0,#0;beq forget;      @ no boxes: also drop any stale count
        add r5,r4,#{HIST + 8};ldr r6,[r4,#{HIST + 4}];cmp r6,#{MAX_BOXES};bhi forget;movs r8,#0;
        erase_loop:cmp r6,#0;beq forget;
        ldrsh r0,[r5];ldrsh r1,[r5,#2];ldrsh r2,[r5,#4];ldrsh r3,[r5,#6];bl outline;
        add r5,#8;subs r6,#1;b erase_loop;
        forget:movs r0,#0;str r0,[r4,#{HIST}];str r0,[r4,#{HIST + 4}];
        erased:
        ldr r6,[sp,#{NEED}];cmp r6,#0;beq restore;
        {c('r5',SUBJECT_ENTRIES)}
        box_loop:
        @ white only on the focused eye, everything else grey
        {c('r8',GREY)}ldr r0,[sp,#{TARGET_ON}];cmp r0,#0;beq colour_ok;
        ldrsh r0,[r5,#0x3a];cmp r0,#0;ble colour_ok;
        ldrsh r0,[r5,#0x36];ldr r1,[sp,#{TARGET_X}];subs r0,r0,r1;it lt;rsblt r0,r0,#0;cmp r0,#{EYE_RADIUS};bgt colour_ok;
        ldrsh r0,[r5,#0x38];ldr r1,[sp,#{TARGET_Y}];subs r0,r0,r1;it lt;rsblt r0,r0,#0;cmp r0,#{EYE_RADIUS};bgt colour_ok;
        {c('r8',WHITE)}
        colour_ok:
        @ the eye box when the subject has one, else the face / body box
        ldrsh r0,[r5,#0x3a];cmp r0,#0;ble face_src;
        add r7,r5,#0x36;movs r3,#{EYE_HALF};b src_ok;
        face_src:add r7,r5,#6;ldrsh r0,[r7,#4];movs r3,#{FACE_HALF_MUL};mul r3,r0,r3;asrs r3,r3,#8;adds r3,#{FACE_HALF_ADD};
        src_ok:
        ldrsh r0,[r7];movw r12,#{X_MUL};mul r0,r0,r12;asrs r0,r0,#8;adds r0,#{X_ADD};
        subs r1,r0,r3;add r2,r0,r3;
        ldrsh r0,[r7,#2];movw r12,#{Y_MUL};mul r0,r0,r12;asrs r0,r0,#8;adds r0,#{Y_ADD};
        sub r12,r0,r3;add r3,r0,r3;mov r0,r1;mov r1,r12;
        @ r0 x0, r1 y0, r2 x1, r3 y1 (screen); clip to 720 x 480
        cmp r0,#0;it lt;movlt r0,#0;cmp r1,#0;it lt;movlt r1,#0;
        cmp r2,#720;it gt;movgt r2,#720;cmp r3,#480;it gt;movgt r3,#480;
        sub r12,r2,r0;cmp r12,#{MIN_SIZE};blt box_next;
        sub r12,r3,r1;cmp r12,#{MIN_SIZE};blt box_next;
        @ the record holds MAX_BOXES boxes: never draw one it cannot remember (and erase)
        ldr r12,[r4,#{HIST + 4}];cmp r12,#{MAX_BOXES};bhs box_next;
        bl outline;
        @ remember it for the next erase (outline keeps r0-r3)
        ldr r12,[r4,#{HIST + 4}];add r7,r4,#{HIST + 8};add r7,r7,r12,lsl #3;
        strh r0,[r7];strh r1,[r7,#2];strh r2,[r7,#4];strh r3,[r7,#6];
        adds r12,#1;str r12,[r4,#{HIST + 4}];movs r12,#1;str r12,[r4,#{HIST}];
        ldr r0,[r4,#20];adds r0,#1;str r0,[r4,#20];
        box_next:add r5,#{ENTRY_BYTES};subs r6,#1;bne box_loop;
        restore:
        @ restore the renderer's OpenVG state
        ldr r0,[sp,#{SAVED + 12}];movs r1,#{VG_FILL_PATH};{n(VG_SET_PAINT)}
        {c('r0',VG_VENDOR_C1A0)}ldr r1,[sp,#{SAVED + 8}];{n(VG_SETI)}
        {c('r0',VG_IMAGE_MODE)}ldr r1,[sp,#{SAVED + 4}];{n(VG_SETI)}
        {c('r0',VG_MATRIX_MODE)}ldr r1,[sp,#{SAVED}];{n(VG_SETI)}
        {c('r0',VG_BLEND_MODE)}mov r1,r9;{n(VG_SETI)}
        {c('r0',VG_SCISSOR_RECTS)}movs r1,#4;add r2,sp,#{CLIP};{n(VG_SETIV)}
        {c('r0',VG_SCISSORING)}mov r1,r10;{n(VG_SETI)}
        out:add sp,#{FRAME};pop {{r4-r11,pc}};

        @ outline(r0 x0, r1 y0, r2 x1, r3 y1): four bars of THICK px in colour r8
        outline:push {{r0-r4,lr}};   @ 24 bytes: keeps the stack 8-byte aligned for the stock fill
        ldr r0,[sp];ldr r1,[sp,#4];ldr r2,[sp,#8];adds r3,r1,#{THICK};bl bar;
        ldr r0,[sp];ldr r3,[sp,#12];subs r1,r3,#{THICK};ldr r2,[sp,#8];bl bar;
        ldr r0,[sp];ldr r1,[sp,#4];adds r1,#{THICK};ldr r3,[sp,#12];subs r3,#{THICK};adds r2,r0,#{THICK};bl bar;
        ldr r2,[sp,#8];subs r0,r2,#{THICK};ldr r1,[sp,#4];adds r1,#{THICK};ldr r3,[sp,#12];subs r3,#{THICK};bl bar;
        pop {{r0-r4,pc}};

        @ bar(r0 x0, r1 y0, r2 x1, r3 y1): one filled rectangle; OpenVG y = 480 - y1
        bar:push {{r4,lr}};
        cmp r0,r2;bge bar_done;cmp r1,r3;bge bar_done;
        str r0,[r11,#0];movw r4,#480;sub r4,r4,r3;str r4,[r11,#4];
        sub r4,r2,r0;str r4,[r11,#8];sub r4,r3,r1;str r4,[r11,#12];
        str r8,[r11,#16];movs r4,#0;str r4,[r11,#20];str r4,[r11,#24];
        mov r0,r11;{n(FILL)}
        bar_done:pop {{r4,pc}};'''


def hook(f):
    """Renderer hook at RENDER_SITE: draw, then the displaced stock instructions."""
    return f'''push {{r0-r12,lr}};{n(f['trk3d_boxes'])}pop {{r0-r12,lr}};
        ldrsh r0,[sp,#0x12];str r0,[sp];ldrsh r3,[sp,#0x10];
        {c('r12', RENDER_CONTINUE | 1)}bx r12;'''
