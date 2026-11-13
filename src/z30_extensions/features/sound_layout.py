# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Private sound page and state contracts; no Camera Status UI is included."""
from ..platform.z30_120 import BASE
from .menu_slots import BLOCKS

STATE_ANCHOR = 0xC1832D24  # Unused page-0 RAM pointer anchors sound state.
SETUP_OBJECT = 0xC1837160
SETUP_META_SOURCE = 0x11FF71AC
PAGE_INDEX = 955
FILES_INDEX = PAGE_INDEX + 1
SWITCH_INDEX = PAGE_INDEX + 2
PAGE_OFFSET = 64
WAV_OFFSET = 128
PCM_OFFSET = 17472
MAX_DATA = 17280
STATE_SIZE = PCM_OFFSET + MAX_DATA * 12
FILES_OFFSET = STATE_SIZE
SWITCH_OFFSET = STATE_SIZE + 44
NEW_SIZE = STATE_SIZE + 88
POPUP_INDEX = 959
POPUP_ID = 0x23F8
POPUP_OFFSET = NEW_SIZE
NEW_SIZE += 44
CONFIG_MAGIC = 0x31534C57  # WLS1, versioned SD configuration.
MAGIC = 0x5A33534E
BUSY, ENABLED, LENGTH, SELECTED, PENDING, RESULT = 32, 36, 40, 44, 48, 52
SETTINGS_FD = 116
SUCCESS = 0x04370080
ROOT_IDS = (0x2222, 0x2224, 0x2226, 0x224E)
FILE_IDS = (0x2250, 0x2265, 0x22B1, 0x1810, 0x1844)
SWITCH_IDS = (0x1846, 0x23F6)
ENTRY_ID = 0x2333
SOUND_GROUP_INDEX = 962
SOUND_GROUP_TITLE_SLOT = BLOCKS['sound_group'][0]
# Row order of the fixed label table; ROOT_LABEL_SLOT..POPUP_OK_SLOT stay contiguous.
ENTRY_SLOT = BLOCKS['sound'][0]
TITLE_SLOT = ENTRY_SLOT + 1
ROOT_LABEL_SLOT = ENTRY_SLOT + 2
SWITCH_LABEL_SLOT = ENTRY_SLOT + 6
FILE_LABEL_SLOT = ENTRY_SLOT + 8
SWITCH_TITLE_SLOT = ENTRY_SLOT + 13
FILES_TITLE_SLOT = ENTRY_SLOT + 14
ENABLED_VALUE_SLOT = ENTRY_SLOT + 15
FILE_VALUE_SLOT = ENTRY_SLOT + 16
EMPTY_SLOT = ENTRY_SLOT + 17
CURRENT_SLOT = ENTRY_SLOT + 18
SWITCH_MARK_SLOT = ENTRY_SLOT + 19
FILE_MARK_SLOT = ENTRY_SLOT + 21
POPUP_BODY_SLOT = ENTRY_SLOT + 26
POPUP_OK_SLOT = ENTRY_SLOT + 27
# Native list geometry: rows start below the title and an eighth row is the last that fits.
ROW_TOP, ROW_PITCH, MAX_ROWS = 62, 52, 8
# Native shooting menus that take direct rows: name, page object, stock rows, feature pages.
NATIVE_MENUS = (('photo', 0xC18333D8, 34, ('aspect', 'zebra', 'portrait', 'stamp')),
                ('movie', 0xC1834FDC, 26, ('bitrate',)))
