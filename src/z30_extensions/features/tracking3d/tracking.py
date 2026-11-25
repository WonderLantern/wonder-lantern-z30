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

Revisions: 1 hooked the tracker's frame callback tables and read the switch
without restoring it (no effect on the camera); 2 runs where the research
modules ran on the camera, the stock detection result writer 0x101944B0 (about
10-20 Hz in live view, with or without a subject; the same site as the research
face_hook, so the two never build together), and reads the switch through
trk3d_choice, which restores WL-3DTRK.CFG on first use from any task; 3 limited
the handover to an area around the tracking box; 4 went back to any detected
subject (the stock search only hands over when the subject is on the box,
camera AF000207) and the menu became Disable / Enable; 5 added the return to the
tracking mode after a half-press handover (method B, below).

Revision 6 (2026-11-17, review of PR 36): the handover is only armed with the
AF-area mode "Auto-area AF (people)" or "(animals)". Camera (review): with plain
Auto-area AF a face crossing the tracking box ends the tracking and the camera
falls back to normal AF without focusing on that face. The stock frame-column
code 0x1039CC14 reads the AF-area mode from the AF display block
0xC237C7B4 + 0x44 (0xC237C7F8; 6 = auto area) and the subject detection setting
through the stock settings getter 0x10465984 (key 0xC3C when the block's +0x3FD
is set, else 0x58B; 1 = people, 2 = animals, as it uses them for the wide-area
people / animal columns). The cave reads both the same way.

Revision 7 (2026-11-17, second review of PR 36): revision 6 never handed over
on the camera. 0xC3C / 0x58B are the wide-area L sub-modes; the stock AF-area
setter 0x1042B2D8 stores the area as base * 100 + sub, the base in 0xC2B and the
sub-mode in 0xC3C for base 4 (wide-area L) but in 0xC3D for base 6 (auto area);
the movie setter 0x1042B30E uses 0x56D / 0x58B / 0x58C the same way. The stock
menu 0x10489888 reads 0xC3D for base 6 (0 plain, 1 people, 2 animals). The cave
now reads 0xC3D (0x58C when +0x3FD is 0, as 0x1039CC14 picks the photo / movie
key) and truncates the result with uxtb as 0x1039CC2C does.

Each detection update, with the switch on, tracking (0xC237F688), a live tracker
(0xC23AA024 != 0, cleared at the handover), the flag still 0, a detected subject
(0xC237E3E4 = 1..5), auto area and people / animal detection, it sets the flag.
Then the eight displaced stock bytes run and the writer continues.
"""
from ...assembly import assemble, constant, invoke, jump

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
AF_DISPLAY = 0xC237C7B4         # stock AF display block
AREA_MODE = AF_DISPLAY + 0x44   # 0xC237C7F8: AF-area mode, 6 = auto area
AUTO_AREA = 6
DETECT_SELECT = AF_DISPLAY + 0x3FD   # 0x1039CC18: non-zero -> photo key, else movie key
# auto-area sub-mode (0 plain, 1 people, 2 animals): movie 0x58C, photo 0xC3D
# (setters 0x1042B30E / 0x1042B2D8; 0x58B / 0xC3C are the wide-area L sub-modes)
DETECT_KEYS = (0x58C, 0xC3D)
GET_SETTING = 0x10465984        # stock settings getter (r0 = key)
PEOPLE, ANIMALS = 1, 2


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
        @ only with Auto-area AF (people) / (animals), read as the stock frame-column code does
        {c('r1',AREA_MODE)}ldrb r0,[r1];cmp r0,#{AUTO_AREA};bne out;
        {c('r1',DETECT_SELECT)}ldrb r1,[r1];{c('r0',DETECT_KEYS[0])}cmp r1,#0;beq detect_key;{c('r0',DETECT_KEYS[1])}
        detect_key:{invoke(GET_SETTING)}uxtb r0,r0;subs r0,#{PEOPLE};cmp r0,#{ANIMALS - PEOPLE};bhi out;
        {c('r4',TOUCH_FLAG)}
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
        revision=7, site=hex(SITE), area_mode=hex(AREA_MODE), detection_keys=[hex(k) for k in DETECT_KEYS], requires='Auto-area AF (people) or (animals)', enter_tracking_mode=hex(ENTER_TRACKING_MODE), cave=hex(entry | 1), size=len(code), flag=hex(TOUCH_FLAG),
        settings_file='WL-3DTRK.CFG', default='off', camera_verified=False)
