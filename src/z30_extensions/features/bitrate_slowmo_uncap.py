"""Raise movie bitrates and lift the slow-motion limits by in-place edits.

Add or change rows in BITRATES_MBPS to customize a build; the mode names are
the keys of STOCK_MBPS.
"""
import struct

# (maximum, average) in Mbps for a movie mode and Movie quality setting.
# 4K rows stay inside the H.264 Level 5.1 ceiling (240 Mbps) the encoder
# configures for 4K, and inside the proven range of the uncap mechanism.
BITRATES_MBPS = {
    '4K 30p HIGH': (200, 168),
    '4K 25p HIGH': (200, 168),
    '4K 24p HIGH': (200, 168),
    '1080 60p HIGH': (108, 90),
    '1080 50p HIGH': (108, 90),
    '1080 30p HIGH': (72, 60),
    '1080 25p HIGH': (72, 60),
    '1080 24p HIGH': (72, 60),
}
STOCK_MBPS = {
    '4K 30p HIGH': (144, 120), '4K 30p NORM': (72, 60),
    '4K 25p HIGH': (144, 120), '4K 25p NORM': (72, 60),
    '4K 24p HIGH': (144, 120), '4K 24p NORM': (72, 60),
    '1080 60p HIGH': (56, 48), '1080 60p NORM': (28, 24),
    '1080 50p HIGH': (56, 48), '1080 50p NORM': (28, 24),
    '1080 30p HIGH': (28, 24), '1080 30p NORM': (14, 12),
    '1080 25p HIGH': (28, 24), '1080 25p NORM': (14, 12),
    '1080 24p HIGH': (28, 24), '1080 24p NORM': (14, 12),
    '1080 120p HIGH': (144, 120), '1080 120p NORM': (72, 60),
    '1080 100p HIGH': (144, 120), '1080 100p NORM': (72, 60),
    # Slow motion is encoded at 120/100 fps; the played-back rate is 1/4 or 1/5.
    '1080 30p x4 slow HIGH': (144, 120), '1080 30p x4 slow NORM': (72, 60),
    '1080 25p x4 slow HIGH': (144, 120), '1080 25p x4 slow NORM': (72, 60),
    '1080 24p x5 slow HIGH': (144, 120), '1080 24p x5 slow NORM': (72, 60),
}
# Record index of each mode in the firmware table; 20-23 are the unused 720p rows.
RECORDS = dict(zip(STOCK_MBPS, list(range(20)) + list(range(24, 30))))
MODE_TABLE = 0x10713154
RECORD = 0x1C
RATES = 0x14
# The encoder keeps maximum kbit / 8 in 16 bits, so stay well below 536 Mbps.
CEILING_MBPS = 400

# Stock slow motion stops at 3 minutes and at one 4 GB file. These edits raise the length
# to 25 minutes and give it the file limits of the other modes: up to 8 files on FAT32, one
# large file on exFAT.
#
# 25 minutes is as far as the length can go. It counts recording time, while the muxer's
# index tables (see below) fill with the played-back footage, 4 or 5 times as fast: they
# overflow after 32.8 minutes of 30p x4, 31.5 of 25p x4 and 26.3 of 24p x5, and the one
# constant serves all three. With the constant at 125 minutes, a 45 minute 30p x4 recording
# on camera (exFAT) gave a 182 minute file the camera hung on: the key frame table had
# overflowed into the sample description. The muxer's own limit did not stop it.
#
# The 125 minute limit of the other modes is kept. Raising it was tried on camera (1080 30p
# MOV, exFAT) and lost footage both ways:
# - Length table 0x115C737C only: recording ran for over 4 hours, but the muxer drops every
#   frame past its own 125 minute limit (literal 0x10290408), so the file ended there.
# - Muxer literal as well: a 162 minute file was not playable. The muxer's index tables
#   are fixed-size; the chunk offset and key frame tables hold 15,750 entries, one per
#   GOP, about 131 minutes of footage, and later entries overwrote the neighbouring tables.
# Going further means enlarging those tables inside the muxer library
# (0x102CB000-0x102EF000), which has not been reverse engineered. MP4 uses the same muxer.
SLOW_LIMIT_MS = 1500000
NOP = bytes.fromhex('00bf')
SLOW_EDITS = (
    # Constant the limit getter 0x10290398 returns for slow motion instead of a table row.
    # Shooting mode 0x12 gets it too; the Z30 does not offer that mode, and it keeps its
    # single 4 GB file either way.
    (0x10290428, struct.pack('<I', SLOW_LIMIT_MS), 'Slow-motion length limit', struct.pack('<I', 180000)),
    # FAT32: file-count getter 0x10290226 returns 1 for slow motion; without this branch
    # it reads the same per-mode table as 120p/100p (8 files).
    (0x1029023A, NOP, 'Slow-motion file count', bytes.fromhex('1ad0')),
    # exFAT: recorder setup 0x102A1F56 skips the large-file flag for slow motion.
    (0x102A1FE6, NOP, 'Slow-motion exFAT single file', bytes.fromhex('59b9')),
)


def rates(pair):
    return struct.pack('<2I', *(round(value * 1000000) for value in pair))


def add(plan):
    if set(BITRATES_MBPS) - set(STOCK_MBPS):
        raise ValueError('Unknown movie mode in bitrate table')
    changed = {}
    for mode, pair in BITRATES_MBPS.items():
        maximum, average = pair
        if not 0 < average <= maximum <= CEILING_MBPS:
            raise ValueError(f'Movie bitrate for {mode} must satisfy 0 < average <= maximum <= {CEILING_MBPS} Mbps')
        if tuple(pair) == STOCK_MBPS[mode]:
            continue
        plan.edit(MODE_TABLE + RECORDS[mode] * RECORD + RATES, rates(pair),
                  f'Movie bitrate {mode}', rates(STOCK_MBPS[mode]))
        changed[mode] = {'maximum_mbps': maximum, 'average_mbps': average}
    for edit in SLOW_EDITS:
        plan.edit(*edit)
    plan.features['bitrate_slowmo_uncap'] = {'changed_bitrates': changed, 'slow_motion_length_limit_ms': SLOW_LIMIT_MS}
