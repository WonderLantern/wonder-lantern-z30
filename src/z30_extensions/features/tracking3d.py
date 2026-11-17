# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""3D tracking: tracking started with OK or a half-press hands over to the
detected person / animal (the fine subject box), as a tracking started by touch
does. See docs/3d-tracking.md and docs/af-tracking-research.md.

Stock behaviour (camera logs AF000272, TF000093):
- a touch start runs the subject search 0x10073C76; with no subject at the
  touched point it sets 0xC18C7240 = 1 and tracks. When a person or animal later
  comes into range, the AF mode chooser switches to 0x203 (subject AF), the
  tracker is cleared (0x100ADE54) and the flag returns to 0;
- an OK / half-press start never sets the flag and never hands over; setting it
  (research module af_touchflag) makes those hand over too.

Revision 2 (2026-11-14): revision 1 hooked the tracker's frame callback tables
and read the switch without restoring it; on the camera it had no effect. This
one runs where the research modules ran on the camera: the stock detection
result writer 0x101944B0 (about 10-20 Hz in live view, with or without a
subject; the same site as the research face_hook, so the two never build
together), and reads the switch through trk3d_choice, which restores
WL-3DTRK.CFG on first use from any task (as gyro_choice does from the IMU task).

Each detection update, with the switch on, tracking (0xC237F688), a live tracker
(0xC23AA024 != 0, cleared at the handover), the flag still 0 and a detected
subject (0xC237E3E4 = 1..5) whose box centre (this update's arguments, coarse
cells) lies inside the handover area around the tracking box centre, it sets
the flag. The areas follow the user's drawing on the camera screen (2026-11-14,
box 552 units = 186 px of a 1880 px wide screen): Standard about 4.3 x 3.4
boxes, Close about 2.7 x 2.0 boxes. Then the eight displaced stock bytes run and
the writer continues.
"""
from ..assembly import assemble, constant, invoke, jump

# Back to the tracking mode after a half-press handover (method B, 2026-11-14):
# the live-view OK handler 0x10368BA0 calls 0x1045A1AE(&byte 0) in tracking state
# 0 / 1 (enter the tracking mode, screen 1), 0x1045A1E6(0) in state 2 (start) and
# 0x1045A21C() in state 3 (leave); 0x10458DC0 re-enters with 0x1045A1AE after a
# 0x1045A21C. 0x1045A1AE -> 0x10230832 -> 0x100774F4 sets the AF tracking state
# 0xC259FF1C to 1 under its own mutex.
ENTER_TRACKING_MODE = 0x1045A1AE
AF_WORKING = 0xC237F680        # 1 while half-pressed / AF-ON
BOX_SHOWN = 0xC237F684         # 1 in the tracking mode (screen 1)
ARMED_OFFSET = 12              # spare word of the 3D tracking state block

SITE = 0x101944B0
RETURN = 0x101944B8
STOCK = bytes.fromhex('70b5 0d46 1446 f022')
TRACKING = 0xC237F688
TRACKER_LIVE = 0xC23AA024
SUBJECTS = 0xC237E3E4
TOUCH_FLAG = 0xC18C7240


def cave_source(choice, get):
    """r8 = state block pointer (3D tracking selection at +0, ARMED at +12); sp+0 = a zero byte for 0x1045A1AE."""
    c = constant
    return f'''
        push {{r0-r8,r12,lr}};sub sp,#12;mov r6,r1;mov r7,r2;
        {invoke(choice)}mov r5,r0;cmp r5,#1;bne out;
        {invoke(get)}movs r8,r0;beq out;

        @ back to the tracking mode: armed by a handover during a half-press
        ldr r1,[r8,#{ARMED_OFFSET}];cmp r1,#0;beq handover;
        {c('r1',BOX_SHOWN)}ldr r1,[r1];cmp r1,#0;bne disarm;       @ stock is back in it already
        {c('r1',AF_WORKING)}ldr r1,[r1];cmp r1,#0;bne handover;    @ still half-pressed
        {c('r1',TRACKING)}ldr r1,[r1];cmp r1,#0;bne handover;      @ still tracking
        movs r1,#0;str r1,[r8,#{ARMED_OFFSET}];str r1,[sp];mov r0,sp;{invoke(ENTER_TRACKING_MODE)}b out;
        disarm:movs r1,#0;str r1,[r8,#{ARMED_OFFSET}];

        handover:
        {c('r1',TRACKING)}ldr r0,[r1];cmp r0,#1;bne out;
        {c('r1',TRACKER_LIVE)}ldr r0,[r1];cmp r0,#0;beq out;
        {c('r4',TOUCH_FLAG)}ldr r0,[r4];cmp r0,#0;bne out;
        {c('r1',SUBJECTS)}ldr r0,[r1];subs r0,#1;cmp r0,#4;bhi out;
        movs r0,#1;str r0,[r4];dmb ish;
        {c('r1',AF_WORKING)}ldr r1,[r1];cmp r1,#0;beq out;
        movs r1,#1;str r1,[r8,#{ARMED_OFFSET}];
        out:add sp,#12;pop {{r0-r8,r12,lr}};
        push {{r4,r5,r6,lr}};mov r5,r1;mov r4,r2;movs r2,#0xF0;
        ldr.w pc,={RETURN | 1};
    '''


def add(plan):
    control = plan.features['tracking3d_control']
    choice = int(control['choice'], 16)
    get = int(control['functions']['trk3d_get'], 16)
    entry = plan.address
    code = assemble(cave_source(choice, get), entry, 384)
    plan.alloc(code)
    plan.edit(SITE, jump(SITE, entry, len(STOCK)), '3D tracking: detection result writer', STOCK)
    plan.features['tracking3d'] = dict(
        revision=5, site=hex(SITE), enter_tracking_mode=hex(ENTER_TRACKING_MODE), cave=hex(entry | 1), size=len(code), flag=hex(TOUCH_FLAG),
        settings_file='WL-3DTRK.CFG', default='off', camera_verified=False)
