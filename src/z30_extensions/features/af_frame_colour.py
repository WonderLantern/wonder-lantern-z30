# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""AF frame colour: green in focus / red not in focus / white otherwise for the
tracking box and the subject (person / animal) box, in AF-S and AF-C alike; the
plain auto-area AF-C frames turn green in focus. See docs/af-frame-colour.md.

Stock (static analysis, camera snapshot SN000119): 0x1039C930 picks the live view
AF frame style from a table at 0x107277B0, 29 rows x 11 columns of u16:
- row = state code, five per focus mode (0xC237C7F9: 1 AF-S rows 0..4, 2 AF-C
  rows 5..9, 3 AF-A, 4 ...): +0 AF not working, +1 a special state, +2 AF
  working with no result yet, +3 in focus, +4 not in focus (focus result
  0xC25A1E70: 2 / 1);
- column = frame kind (0x1039CC14, from AF-area mode 0xC237C7F8 and the tracking
  bytes 0xC237C815 / 0xC237C816): 0..6 the fixed AF-area modes, 7 plain
  auto-area, 8 auto-area with a detected subject (face / eye / animal box),
  9 tracking mode screen 1 (box shown, not tracking), 10 tracking;
- entry: low byte the colour code (2 = in focus, also what the in-focus indicator
  uses), high byte the display mode (0 hidden, 1 shown, 2 blinking).
Colour codes as the stock rows use them: 0 the subject / tracking box while
idle, 1 the AF point idle, 2 green, 3 the white screen-1 tracking box, 4 red
(not in focus, blinking), 5 the special state of columns 8..10 (yellow on the
camera at the start of a half-press, PR 36 review).

The edits only change table entries; no code runs.
"""
TABLE = 0x107277B0
COLUMNS = 11
AF_S, AF_C, AF_F = 0, 5, 10     # first row of each focus mode (AF-F: movie, focus mode byte 4)
IDLE, SPECIAL, WORKING, IN_FOCUS = 0, 1, 2, 3
PLAIN_AUTO, SUBJECT, SCREEN1, TRACKING = 7, 8, 9, 10
WHITE, GREEN = 0x0103, 0x0102    # shown, colour 3 / 2

# (row, column, stock, new)
EDITS = (
    # AF-S: subject and tracking boxes white while AF is idle or has no result yet
    (AF_S + IDLE, SUBJECT, 0x0100, WHITE), (AF_S + IDLE, TRACKING, 0x0100, WHITE),
    (AF_S + WORKING, SUBJECT, 0x0100, WHITE), (AF_S + WORKING, SCREEN1, 0x0100, WHITE),
    (AF_S + WORKING, TRACKING, 0x0100, WHITE),
    # AF-C: the same while idle
    (AF_C + IDLE, SUBJECT, 0x0100, WHITE), (AF_C + IDLE, TRACKING, 0x0100, WHITE),
    # AF-C in focus: green (stock: colour 0 / 1). AF-C refocuses every cycle and
    # the result reads 0 between cycles; as stock does, that state looks like the
    # in-focus one so the box does not flicker.
    (AF_C + WORKING, SUBJECT, 0x0100, GREEN), (AF_C + WORKING, SCREEN1, 0x0100, GREEN),
    (AF_C + WORKING, TRACKING, 0x0100, GREEN),
    (AF_C + IN_FOCUS, PLAIN_AUTO, 0x0101, GREEN),
    (AF_C + IN_FOCUS, SUBJECT, 0x0100, GREEN), (AF_C + IN_FOCUS, SCREEN1, 0x0100, GREEN),
    (AF_C + IN_FOCUS, TRACKING, 0x0100, GREEN),
    # Movie (AF-F; camera log AF000161: focus mode 4, state code 10 while not
    # half-pressed): the subject box was yellow (colour 0); white like the photo
    # boxes. In focus stays green (0x0102), not in focus red, as stock.
    (AF_F + IDLE, SUBJECT, 0x0100, WHITE), (AF_F + IDLE, TRACKING, 0x0100, WHITE),
    (AF_F + WORKING, SUBJECT, 0x0100, WHITE), (AF_F + WORKING, SCREEN1, 0x0100, WHITE),
    (AF_F + WORKING, TRACKING, 0x0100, WHITE),
    # Movie AF-F focuses all the time without a half-press, so the subject box
    # turned green / red on its own; white in every AF-F state (v44). A
    # half-press in movie mode switches to the AF-S rows (white / green / red).
    (AF_F + IN_FOCUS, SUBJECT, 0x0102, WHITE), (AF_F + 4, SUBJECT, 0x0204, WHITE),
    # PR 36 review (camera): the subject / tracking boxes flashed yellow at the
    # start of a half-press before turning green / red. The special state (+1)
    # of every mode still had the stock colour 5 (0x0105) in columns 8..10;
    # white like the idle state.
    (AF_S + SPECIAL, SUBJECT, 0x0105, WHITE), (AF_S + SPECIAL, SCREEN1, 0x0105, WHITE),
    (AF_S + SPECIAL, TRACKING, 0x0105, WHITE),
    (AF_C + SPECIAL, SUBJECT, 0x0105, WHITE), (AF_C + SPECIAL, SCREEN1, 0x0105, WHITE),
    (AF_C + SPECIAL, TRACKING, 0x0105, WHITE),
    (AF_F + SPECIAL, SUBJECT, 0x0105, WHITE), (AF_F + SPECIAL, SCREEN1, 0x0105, WHITE),
    (AF_F + SPECIAL, TRACKING, 0x0105, WHITE),
)
# v42 kept the AF-C subject box (column 8) at its stock colour while half-pressed
# (with colour 2 its eye-selection arrows looked jagged, v41); v44 restores green
# at the user's request.
# not in focus is already red (0x0204) in both modes for these columns


def entry(row, column):
    return TABLE + (row * COLUMNS + column) * 2


def add(plan):
    # v51: the stock table stays as it is; af_frame_colour_ui holds the edited copy
    # and switches the lookup between the two (photo menu row, Enable by default).
    control = plan.features['af_frame_colour_control']
    plan.features['af_frame_colour'] = dict(
        table=hex(TABLE), edited_copy=control['table'], lookup='0x1039c930', edits=len(EDITS),
        entries=[dict(row=r, column=c, stock=hex(s), new=hex(n)) for r, c, s, n in EDITS],
        camera_verified=False)
