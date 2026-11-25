# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Custom electronic shutter sound from SD WAV files, as a part of the menu framework.

The sound brings its own pages (enable switch, file choice, preview, reload),
its state (the WAV and PCM buffers), WL-SOUND.CFG and the stock audio hooks
(shot, config, data). menu_framework.add(..., sound=True) plugs it in; the
framework itself no longer depends on it.
"""
import struct

from .sound_layout import *
from . import menu_framework
from .menu_framework import ROOT_ORDER, seed_record  # noqa: F401  (kept for existing importers)

# Function slots the sound owns (the framework allocates them in SLOT_ORDER).
SIZES = {'validate': 384, 'convert': 192, 'load': 1024, 'start': 448, 'stop': 192, 'trigger': 384,
         'complete': 384, 'config': 320, 'data': 320, 'shot': 192,
         'save_settings': 640, 'restore_settings': 640, 'settings_close': 128}
TEXTS = dict(entry='Shutter sound', title='Shutter sound', files='Select sound', switch='Sound enabled',
             enable='Enabled', file='Sound file', preview='Preview sound', reload='Reload sound',
             off='OFF', on='ON', none='None', root='SHUTTER.WAV',
             one='1.WAV', two='2.WAV', three='3.WAV', four='4.WAV',
             missing='File not found', read='Read failed', invalid='Invalid WAV', close='Close failed',
             busy='Sound busy', queue='Play failed', audio='Audio failed')
# The file and switch pages: index, rows, label, value/mark and title slots, control IDs.
PAGE_SPECS = [(FILES_INDEX, 5, FILE_LABEL_SLOT, FILE_MARK_SLOT, FILES_TITLE_SLOT, FILE_IDS),
              (SWITCH_INDEX, 2, SWITCH_LABEL_SLOT, SWITCH_MARK_SLOT, SWITCH_TITLE_SLOT, SWITCH_IDS)]
CARD_PATHS = ['SHUTTER.WAV'] + [f'SHUTTER/{i}.WAV' for i in range(1, 5)]


def prepare(plan, text_resources, strings, grouped):
    """Sound tables allocated right after the texts."""
    if grouped:
        strings['sound_title'] = text_resources.alloc('Shutter sound')
    paths = [plan.alloc(path.encode() + b'\0') for path in ['A:\\SHUTTER.WAV'] + [f'A:\\SHUTTER\\{i}.WAV' for i in range(1, 5)]]
    path_table = plan.alloc(struct.pack('<5I', *paths))
    file_names = plan.alloc(struct.pack('<5I', *[strings[k] for k in ('root', 'one', 'two', 'three', 'four')]))
    fixed = plan.alloc(struct.pack('<15I', *[strings[k] for k in
        ('entry', 'title', 'enable', 'file', 'preview', 'reload', 'off', 'on', 'root', 'one', 'two', 'three', 'four', 'switch', 'files')]))
    return dict(path_table=path_table, file_names=file_names, fixed=fixed)


def prepare_ids(plan):
    return dict(file_ids=plan.alloc(struct.pack('<5I', *FILE_IDS)),
                switch_ids=plan.alloc(struct.pack('<2I', *SWITCH_IDS)))


def code(plan, f, parts, anchor_table):
    from .sound_audio import audio_code
    from .sound_settings import settings_code
    codes = audio_code(f, parts['path_table'], anchor_table)
    config_path = plan.alloc(b'A:\\WL-SOUND.CFG\0')
    codes.update(settings_code(f, config_path))
    return codes


def fragments(f, strings, parts, child_templates, group):
    from .sound_ui import fragments as sound_fragments
    return sound_fragments(f, strings, parts['fixed'], parts['file_names'], parts['file_ids'],
                           parts['switch_ids'], child_templates, group)


def report(grouped):
    return {'sound_settings_page': SOUND_GROUP_INDEX if grouped else PAGE_INDEX,
            'default_enabled': False,
            'settings_persistence': 'SD WL-SOUND.CFG; restore on first menu entry or photo trigger',
            'sample_format': '8 kHz, mono, signed 16-bit, canonical 44-byte WAV, <=1.08 seconds',
            'card_paths': CARD_PATHS, 'physical_menu_audio_verified': False}


def add(plan, **options):
    """The menu framework with the shutter sound (the historical entry point)."""
    return menu_framework.add(plan, sound=True, **options)
