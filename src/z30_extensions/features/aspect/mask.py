# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
"""Soft-mask geometry and the opt-in firmware drawing hook.

Dynamic geometry reads the selected profile and the native viewport snapshot,
then executes the same source-window projection as the existing frame code.
The mask does not depend on the stock grid channel being enabled. The GPU
submit hook writes only the separate live-view RGBA image surface below the UI.
This is experimental; cache ownership and physical lifecycle remain unverified.
"""
from ...assembly import constant as c, invoke as n
from .frame import projection_instructions

DEFAULT_ALPHA = 128  # 50% black; user requested a darker mask after the 38% proposal.

# Display state word holding the 720x480 RGBA live-view image surface address.
# Derived from the QEMU display model and confirmed by runtime memory reads.
IMAGE_SLOT = 0xC18200C0
# Stock grid-channel record (RAM). Geometry written by the aspect grid hook via
# aspect_frame_prepare: +0 x0 u16, +4 x_width u32, +14 y0 u16, +16 y_height u32.
GRID_RECORD = 0xC201A7F4  # Historical diagnostic only, not a mask input.
SNAPSHOT = 0xC237C7B4  # Stock 101925ce memcpy source (literal 1019272c).
SOURCE_WINDOW = 0xC237C3D2
VIEWPORT = (0, 0, 720, 480)
# Read-only inputs of the native photo-overlay predicate 104be4a0 and its
# readiness helper 10306328. Display-source flags c18200dc are not an app gate.
VIEW_KIND = 0xC18292AC
OVERLAY_BUSY = (0xC2016904, 0xC20168F8)  # Native UI properties 2d and 2a.
PHOTO_BACKEND = 0xC181B278
# Native 10457db2. Snapshot waiters 1044e4c8/1044e540 reject stages 0/3/4;
# the observed camera uses 2 while ready and repeatedly enters 4. This is a
# renderer lifecycle gate, not a complete enumeration of playback states.
UI_SERVICE_PHASE = 0xC181F6FC

SUBMIT_SITE = 0x1055D112
SUBMIT_CONTINUE = SUBMIT_SITE + 10
SUBMIT_EXPECTED = '70b50c46054625f031f8'
SUBMIT_SIZE = 10

STATE_SIZE = 128
STATE_FLAGS = 0x00
STATE_IMAGE = 0x04
STATE_ALPHA = 0x08
STATE_COUNT = 0x0C
STATE_RECTS = 0x10
STATE_COLOR = 0x50
STATE_LOCK = 0x54
HISTORY_OFFSET = 16  # After the existing 16-byte aspect settings state.
FLAG_ACTIVE = 1

SIZES = dict(aspect_mask_fill=512, aspect_mask_submit=1024, aspect_mask_project=768,
             aspect_mask_photo_context=192, aspect_mask_erase=384, aspect_mask_update=512,
             aspect_mask_retire=256)

# Static verification bands for the 2.35:1 sample profile (screen coordinates).
TRIAL_RECTS = ((0, 0, 720, 87), (0, 393, 720, 480))


def trial_state(alpha=DEFAULT_ALPHA):
    """Initial 128-byte trial state: mask enabled with the QEMU-verified bands."""
    if not 0 < alpha <= 255:
        raise ValueError('mask alpha must be within 1..255')
    state = bytearray(STATE_SIZE)
    import struct
    struct.pack_into('<I', state, STATE_FLAGS, FLAG_ACTIVE)
    struct.pack_into('<I', state, STATE_IMAGE, 0)
    struct.pack_into('<I', state, STATE_ALPHA, alpha)
    struct.pack_into('<I', state, STATE_COUNT, len(TRIAL_RECTS))
    for index, rect in enumerate(TRIAL_RECTS):
        struct.pack_into('<4I', state, STATE_RECTS + index * 16, *rect)
    struct.pack_into('<I', state, STATE_COLOR, alpha << 24)
    return bytes(state)


def grid_regions(x0, x_width, y0, y_height, viewport=VIEWPORT):
    """Viewport outside the grid-record photo, in screen coordinates.

    Mirrors the stock record semantics used by the firmware hook: the projected
    photo rectangle is (x0, y0, x0+x_width, y0+y_height), already clipped to the
    viewport by the axis code. Invalid or full-frame geometry returns an empty
    tuple (draw nothing).
    """
    if (not isinstance(viewport, (tuple, list)) or len(viewport) != 4):
        return ()
    if any(type(v) is not int for v in (x0, x_width, y0, y_height, *viewport)):
        return ()
    l, t, r, b = viewport
    if x_width <= 0 or y_height <= 0:
        return ()
    x1, y1 = x0 + x_width, y0 + y_height
    if x0 < l or y0 < t or x1 > r or y1 > b or x1 <= l or y1 <= t:
        return ()
    return tuple(q for q in ((l, t, r, y0), (l, y1, r, b),
                             (l, y0, x0, y1), (x1, y0, r, y1))
                 if q[0] < q[2] and q[1] < q[3])


def source_codes(f, state_address=None, *, trial=False, geometry_table=None):
    """Append-only code for the opt-in soft-mask drawing hook.

    The fill runs at the GPU submit boundary so it lands between the display
    pipeline's image-surface refresh and the overlay composition that consumes
    it. Dynamic mode only fills at the guarded overlay-bank clear boundary,
    after native drawing has completed. Fill the lower image surface uniformly;
    skipping opaque residual pixels here leaves visible holes. The UI canvas
    is composed above it. Historical ungated trial fills retain pixel guards.
    """
    code = {}
    own_color = f'movs r2,#0;movt r2,#{DEFAULT_ALPHA << 8};' if trial else ''
    pixel_guard = '''ldr lr,[r0];cmp lr,r2;beq mask_pixel_write;
        lsrs lr,lr,#24;bne mask_pixel_next;''' if trial else ''
    image_source = f"{c('r5', IMAGE_SLOT)}ldr r5,[r5];" if trial else f'ldr r5,[r4,#{STATE_IMAGE}];'
    code['aspect_mask_fill'] = f'''push {{r4-r11,lr}};
        mov r4,r0;mov r6,r1;
        {image_source}
        lsrs r0,r5,#28;cmp r0,#0xd;bne mask_done;
        ldr r7,[r4,#{STATE_COUNT}];cmp r7,#0;beq mask_done;cmp r7,#4;bhi mask_done;
        add r8,r4,#{STATE_RECTS};
        mask_rect:
        ldr r0,[r8];ldr r1,[r8,#4];ldr r2,[r8,#8];ldr r3,[r8,#0xc];
        cmp r2,#720;bhi mask_next;cmp r3,#480;bhi mask_next;
        cmp r0,r2;bhs mask_next;cmp r1,r3;bhs mask_next;
        movs r9,#480;subs r9,r9,r3;
        movs r10,#480;subs r10,r10,r1;
        cmp r9,r10;bhs mask_next;
        subs r11,r2,r0;cmp r11,#1;blt mask_next;
        movw r12,#2880;mul r12,r9,r12;lsls r1,r0,#2;add r12,r5,r12;add r12,r12,r1;
        {own_color}
        mask_write:
        mov r3,r9;
        mask_row:
        cmp r3,r10;bhs mask_next;
        mov r0,r12;mov r1,r11;
        mask_column:
        {pixel_guard}
        mask_pixel_write:str r6,[r0];
        mask_pixel_next:adds r0,#4;subs r1,#1;bne mask_column;
        movw r0,#2880;add r12,r12,r0;adds r3,#1;
        b mask_row;
        mask_next:
        add r8,#0x10;subs r7,#1;bne mask_rect;
        mask_done:
        dsb sy;
        pop {{r4-r11,pc}};'''
    if trial:
        code['aspect_mask_submit'] = f'''push {{r0-r3,lr}};
        {c('r0', state_address)}
        ldr r1,[r0,#{STATE_COLOR}];
        {n(f['aspect_mask_fill'])}
        pop {{r0-r3,lr}};
        push {{r4,r5,r6,lr}};mov r4,r1;mov r5,r0;
        {n(0x1058217E)}
        {c('r12', SUBMIT_CONTINUE | 1)}
        bx r12;'''
        return code
    if geometry_table is None:
        raise ValueError('Dynamic mask needs the existing photo geometry table')
    # Mirror the photo subset of native overlay eligibility without invoking
    # the snapshot API/locks in the GPU worker. No grid preference is read.
    code['aspect_mask_photo_context'] = f'''
        {c('r1',UI_SERVICE_PHASE)}ldrb r0,[r1];cmp r0,#1;beq stage_ok;cmp r0,#2;bne not_photo;
        stage_ok:
        {c('r1',VIEW_KIND)}ldrb r0,[r1];cmp r0,#0;beq kind_ok;cmp r0,#6;bne not_photo;
        kind_ok:{c('r1',OVERLAY_BUSY[0])}ldr r0,[r1];cmp r0,#0;bne not_photo;
        {c('r1',OVERLAY_BUSY[1])}ldr r0,[r1];cmp r0,#0;bne not_photo;
        {c('r1',PHOTO_BACKEND)}ldr r0,[r1];cmp r0,#1;bne not_photo;
        {c('r1',SNAPSHOT)}ldr r0,[r1];tst r0,#1;beq not_photo;
        movs r0,#1;bx lr;not_photo:movs r0,#0;bx lr;'''
    # History is private RAM appended to the aspect state, protected by a
    # bounded try-lock in submit. Erase only our exact committed black word
    # inside the last owned rectangles, and only on the same current surface.
    # A changed image pointer is never dereferenced: it may have been freed.
    code['aspect_mask_erase'] = f'''push {{r4-r11,lr}};mov r4,r0;
        ldr r0,[r4];cmp r0,#1;bne erase_done;
        ldr r5,[r4,#{STATE_IMAGE}];lsrs r0,r5,#28;cmp r0,#0xd;bne erase_done;
        {c('r0',IMAGE_SLOT)}ldr r0,[r0];cmp r0,r5;bne erase_done;
        ldr r6,[r4,#{STATE_COLOR}];movs r0,#0;movt r0,#{DEFAULT_ALPHA << 8};cmp r6,r0;bne erase_done;
        ldr r7,[r4,#{STATE_COUNT}];cmp r7,#1;blo erase_done;cmp r7,#4;bhi erase_done;
        add r8,r4,#{STATE_RECTS};
        erase_rect:ldm r8,{{r0-r3}};cmp r2,#720;bhi erase_next;cmp r3,#480;bhi erase_next;
        cmp r0,r2;bhs erase_next;cmp r1,r3;bhs erase_next;
        movw r9,#480;sub r9,r9,r3;movw r10,#480;sub r10,r10,r1;sub r11,r2,r0;
        movw r12,#2880;mul r12,r9,r12;add r12,r5,r12;add r12,r12,r0,lsl #2;
        erase_row:mov r0,r12;mov r1,r11;
        erase_column:ldr r2,[r0];cmp r2,r6;it eq;moveq r2,#0;it eq;streq r2,[r0];
        adds r0,#4;subs r1,#1;bne erase_column;
        movw r0,#2880;add r12,r12,r0;adds r9,#1;cmp r9,r10;blo erase_row;
        erase_next:add r8,#16;subs r7,#1;bne erase_rect;
        erase_done:movs r0,#0;str r0,[r4];dsb sy;pop {{r4-r11,pc}};'''
    # Lifecycle retirement must not depend on the live-view A03 descriptor:
    # another screen can submit entirely different commands to the same GPU.
    # Preserve the original arguments and never wait for a competing writer.
    code['aspect_mask_retire'] = f'''push {{r4-r6,lr}};
        {n(f['aspect_get'])}cmp r0,#0;beq retire_done;add r4,r0,#{HISTORY_OFFSET};
        add r5,r4,#{STATE_LOCK};movs r6,#4;
        retire_claim:ldrex r1,[r5];cmp r1,#0;bne retire_busy;
        movs r1,#1;strex r0,r1,[r5];cmp r0,#0;beq retire_owned;
        subs r6,#1;bne retire_claim;clrex;b retire_done;
        retire_busy:clrex;b retire_done;
        retire_owned:dmb ish;mov r0,r4;{n(f['aspect_mask_erase'])}
        movs r0,#0;dmb ish;str r0,[r5];
        retire_done:pop {{r4-r6,pc}};'''
    code['aspect_mask_update'] = f'''push {{r4-r8,lr}};mov r4,r0;mov r5,r1;
        ldr r0,[r5];cmp r0,#1;bne update_fill;
        ldr r0,[r4,#{STATE_IMAGE}];ldr r1,[r5,#{STATE_IMAGE}];cmp r0,r1;bne update_erase;
        ldr r0,[r4,#{STATE_COUNT}];ldr r1,[r5,#{STATE_COUNT}];cmp r0,r1;bne update_erase;
        lsls r2,r0,#2;add r0,r4,#{STATE_RECTS};add r1,r5,#{STATE_RECTS};
        update_compare:ldr r3,[r0],#4;ldr r6,[r1],#4;cmp r3,r6;bne update_erase;
        subs r2,#1;bne update_compare;b update_fill;
        update_erase:mov r0,r5;{n(f['aspect_mask_erase'])}
        update_fill:mov r0,r4;movs r1,#0;movt r1,#{DEFAULT_ALPHA << 8};{n(f['aspect_mask_fill'])}
        ldr r6,[r4,#{STATE_IMAGE}];{c('r0',IMAGE_SLOT)}ldr r0,[r0];cmp r0,r6;bne update_abandon;
        movs r0,#0;str r0,[r5];str r6,[r5,#{STATE_IMAGE}];
        ldr r7,[r4,#{STATE_COUNT}];str r7,[r5,#{STATE_COUNT}];lsls r7,r7,#2;
        add r0,r4,#{STATE_RECTS};add r1,r5,#{STATE_RECTS};
        update_copy:ldr r2,[r0],#4;str r2,[r1],#4;subs r7,#1;bne update_copy;
        movs r0,#0;movt r0,#{DEFAULT_ALPHA << 8};str r0,[r5,#{STATE_COLOR}];
        dmb ish;movs r0,#1;str r0,[r5];b update_done;
        update_abandon:movs r0,#0;str r0,[r5];
        update_done:pop {{r4-r8,pc}};'''
    code['aspect_mask_project'] = f'''push {{r4-r11,lr}};sub sp,#132;
        mov r4,r0;mov r5,r1;mov r6,r2;mov r7,r3;
        {projection_instructions()}
        axes:ldr r0,[sp,#16];ldr r1,[sp,#20];ldr r2,[sp,#24];ldr r3,[sp,#28];
        adds r0,#1;bic r0,r0,#1;adds r1,#1;bic r1,r1,#1;
        bic r2,r2,#1;bic r3,r3,#1;
        ldr r5,[sp];cmp r0,r5;it lt;movlt r0,r5;
        ldr r5,[sp,#4];cmp r1,r5;it lt;movlt r1,r5;
        ldr r5,[sp,#8];cmp r2,r5;it gt;movgt r2,r5;
        ldr r5,[sp,#12];cmp r3,r5;it gt;movgt r3,r5;
        cmp r0,r2;bge bad;cmp r1,r3;bge bad;
        stm r4,{{r0-r3}};movs r0,#1;b done;
        bad:movs r0,#0;done:add sp,#132;pop {{r4-r11,pc}};'''
    # Current geometry stays on the private stack. Owned pixels are tracked in
    # private RAM, never ROM. A bounded try-lock avoids overlapping ownership
    # updates without calling native snapshot locks from the GPU worker.
    submit_prefix = f'''push {{r0-r11,lr}};sub sp,#0x94;
        {c('r1',UI_SERVICE_PHASE)}ldrb r1,[r1];cmp r1,#1;beq mask_descriptor;
        cmp r1,#2;beq mask_descriptor;
        {n(f['aspect_mask_retire'])}b mask_dynamic_done;
        mask_descriptor:
        lsrs r1,r0,#28;cmp r1,#0xc;bne mask_dynamic_done;
        ldr r1,[r0];movw r2,#0xa03;cmp r1,r2;bne mask_dynamic_done;
        ldr r0,[r0,#0x14];lsrs r1,r0,#28;cmp r1,#0xc;bne mask_dynamic_done;
        ldr r1,[r0,#0x88];cmp r1,#720;bne mask_dynamic_done;
        ldr r1,[r0,#0x8c];cmp r1,#480;bne mask_dynamic_done;
        ldr r1,[r0,#0xb0];lsrs r1,r1,#28;cmp r1,#0xe;bne mask_dynamic_done;
        {n(f['aspect_get'])}cmp r0,#0;beq mask_dynamic_done;
        str r0,[sp,#0x90];add r6,r0,#{HISTORY_OFFSET};str r6,[sp,#0x80];
        add r2,r6,#{STATE_LOCK};movs r3,#4;
        mask_claim:ldrex r1,[r2];cmp r1,#0;bne mask_claim_busy;
        movs r1,#1;strex r0,r1,[r2];cmp r0,#0;beq mask_claimed;
        subs r3,#1;bne mask_claim;clrex;b mask_dynamic_done;
        mask_claim_busy:clrex;b mask_dynamic_done;
        mask_claimed:dmb ish;'''
    submit_body = f'''
        {n(f['aspect_mask_photo_context'])}cmp r0,#1;bne mask_drop;
        {c('r0',IMAGE_SLOT)}ldr r0,[r0];lsrs r1,r0,#28;cmp r1,#0xd;bne mask_drop;
        str r0,[sp,#{STATE_IMAGE}];
        ldr r0,[sp,#0x90];ldr r5,[r0];cmp r5,#1;blo mask_dynamic_done;
        cmp r5,#6;bhi mask_dynamic_done;
        {c('r4', SNAPSHOT+8)}ldr r0,[r4];ldr r1,[r4,#4];
        str r0,[sp,#0x60];str r1,[sp,#0x64];
        ldrsh r0,[sp,#0x60];cmp r0,#0;blt mask_dynamic_done;
        ldrsh r1,[sp,#0x62];cmp r1,#0;blt mask_dynamic_done;
        ldrsh r2,[sp,#0x64];cmp r2,#720;bgt mask_dynamic_done;cmp r0,r2;bge mask_dynamic_done;
        ldrsh r3,[sp,#0x66];cmp r3,#480;bgt mask_dynamic_done;cmp r1,r3;bge mask_dynamic_done;
        {c('r4', SOURCE_WINDOW)}
        ldrh r0,[r4];strh r0,[sp,#0x68];ldrh r0,[r4,#2];strh r0,[sp,#0x6a];
        ldrh r0,[r4,#4];strh r0,[sp,#0x6c];ldrh r0,[r4,#6];strh r0,[sp,#0x6e];
        add r0,sp,#0x70;add r1,sp,#0x60;
        {c('r2',geometry_table)}add r2,r2,r5,lsl #4;mov r3,r5;
        {n(f['aspect_mask_project'])}cmp r0,#0;beq mask_dynamic_done;
        {n(f['aspect_get'])}ldr r1,[sp,#0x90];cmp r0,r1;bne mask_dynamic_done;
        ldr r0,[r0];cmp r0,r5;bne mask_dynamic_done;
        {c('r4',SNAPSHOT+8)}ldr r0,[r4];ldr r1,[sp,#0x60];cmp r0,r1;bne mask_dynamic_done;
        ldr r0,[r4,#4];ldr r1,[sp,#0x64];cmp r0,r1;bne mask_dynamic_done;
        {c('r4',SOURCE_WINDOW)}
        ldrh r0,[r4];ldrh r1,[sp,#0x68];cmp r0,r1;bne mask_dynamic_done;
        ldrh r0,[r4,#2];ldrh r1,[sp,#0x6a];cmp r0,r1;bne mask_dynamic_done;
        ldrh r0,[r4,#4];ldrh r1,[sp,#0x6c];cmp r0,r1;bne mask_dynamic_done;
        ldrh r0,[r4,#6];ldrh r1,[sp,#0x6e];cmp r0,r1;bne mask_dynamic_done;
        {n(f['aspect_mask_photo_context'])}cmp r0,#1;bne mask_dynamic_done;
        ldrsh r4,[sp,#0x60];ldrsh r5,[sp,#0x62];ldrsh r6,[sp,#0x64];ldrsh r7,[sp,#0x66];
        add r0,sp,#0x70;ldm r0,{{r8-r11}};
        cmp r8,r4;it eq;moveq r8,#0;
        cmp r9,r5;it eq;moveq r9,#0;
        cmp r10,r6;it eq;movweq r10,#720;
        cmp r11,r7;it eq;movweq r11,#480;
        movs r4,#0;movs r5,#0;movw r6,#720;movw r7,#480;
        movs r0,#0;str r0,[sp,#{STATE_COUNT}];add r12,sp,#{STATE_RECTS};
        mov r0,r4;mov r1,r5;mov r2,r6;mov r3,r9;bl mask_append;
        mov r0,r4;mov r1,r11;mov r2,r6;mov r3,r7;bl mask_append;
        mov r0,r4;mov r1,r9;mov r2,r8;mov r3,r11;bl mask_append;
        mov r0,r10;mov r1,r9;mov r2,r6;mov r3,r11;bl mask_append;
        ldr r0,[sp,#{STATE_COUNT}];cmp r0,#0;beq mask_dynamic_done;
        {c('r0',IMAGE_SLOT)}ldr r0,[r0];ldr r1,[sp,#{STATE_IMAGE}];cmp r0,r1;bne mask_drop;
        mov r0,sp;ldr r1,[sp,#0x80];{n(f['aspect_mask_update'])}
        b mask_unlock;
        mask_append:cmp r0,r2;bge mask_append_done;cmp r1,r3;bge mask_append_done;
        stm r12!,{{r0-r3}};ldr r0,[sp,#{STATE_COUNT}];adds r0,#1;str r0,[sp,#{STATE_COUNT}];
        mask_append_done:bx lr;'''
    # Every decline after acquiring ownership must retire old pixels and
    # release the try-lock; descriptor/target declines above never take it.
    submit_body = submit_body.replace('mask_dynamic_done', 'mask_drop')
    code['aspect_mask_submit'] = submit_prefix + submit_body + f'''
        mask_drop:ldr r0,[sp,#0x80];{n(f['aspect_mask_erase'])}
        mask_unlock:ldr r1,[sp,#0x80];movs r0,#0;dmb ish;str r0,[r1,#{STATE_LOCK}];
        mask_dynamic_done:add sp,#0x94;pop {{r0-r11,lr}};
        push {{r4,r5,r6,lr}};mov r4,r1;mov r5,r0;{n(0x1058217E)}
        {c('r12',SUBMIT_CONTINUE | 1)}bx r12;'''
    return code


def display_regions(photo, viewport, canvas=VIEWPORT):
    """Continue exterior shading to the LCD edge without moving a crop edge.

    A photo edge clipped by the native viewport is not an actual crop border:
    continue it to the corresponding canvas edge. The remaining crop borders
    retain their verified projected coordinates. This prevents bright strips
    above/below a wide mask and unshaded end segments of a narrow side mask.
    Native graphics are protected separately by the pixel fill contract.
    """
    if regions(photo, viewport) is None or regions(viewport, canvas) is None:
        return None
    l,t,r,b=viewport;cl,ct,cr,cb=canvas
    if not cl<=l<r<=cr or not ct<=t<b<=cb:
        return None
    x0,y0,x1,y1=photo
    extended=(cl if x0<=l else x0,ct if y0<=t else y0,
              cr if x1>=r else x1,cb if y1>=b else y1)
    return regions(extended,canvas)


def regions(photo, viewport):
    """Viewport outside the projected photo, partitioned without overlap.

    Invalid inputs or a disjoint photo return None (do not draw). An empty tuple
    means the photo covers the entire viewport. Alignment belongs to the chosen
    drawing backend; do not quantize a second time and shift confirmed edges.
    """
    if (not isinstance(photo,(tuple,list)) or not isinstance(viewport,(tuple,list))
            or len(photo) != 4 or len(viewport) != 4):
        return None
    if any(type(v) is not int for v in (*photo, *viewport)):
        return None
    l, t, r, b = viewport
    x0, y0, x1, y1 = photo
    if not (0 <= l < r <= 8192 and 0 <= t < b <= 8192
            and -32768 <= x0 < x1 <= 32767 and -32768 <= y0 < y1 <= 32767):
        return None
    x0, y0, x1, y1 = max(l,x0), max(t,y0), min(r,x1), min(b,y1)
    if x0 >= x1 or y0 >= y1:
        return None
    # Full-width top/bottom; side masks only between them. No double alpha
    # application at corners, and no coverage of the retained photo.
    return tuple(q for q in ((l,t,r,y0), (l,y1,r,b),
                             (l,y0,x0,y1), (x1,y0,r,y1))
                 if q[0] < q[2] and q[1] < q[3])
