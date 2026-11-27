# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Canonical 8 kHz mono 16-bit PCM WAV helpers for the Z30 shutter sound."""
import math
import random
import struct
from pathlib import Path

SAMPLE_RATE = 8000
CHANNELS = 1
SAMPLE_WIDTH = 2
HEADER_SIZE = 44
MAX_DATA_BYTES = 17280
MAX_SAMPLES = MAX_DATA_BYTES // SAMPLE_WIDTH
MAX_FILE_BYTES = HEADER_SIZE + MAX_DATA_BYTES

SOUND_SLOTS = {
    'root': Path('SHUTTER.WAV'),
    '1': Path('SHUTTER') / '1.WAV',
    '2': Path('SHUTTER') / '2.WAV',
    '3': Path('SHUTTER') / '3.WAV',
    '4': Path('SHUTTER') / '4.WAV',
}


def encode_canonical_wav(pcm_bytes):
    """Wrap signed 16-bit little-endian 8 kHz mono PCM bytes in a 44-byte WAV header."""
    if not isinstance(pcm_bytes, (bytes, bytearray, memoryview)):
        raise ValueError('PCM payload must be bytes')
    data = bytes(pcm_bytes)
    size = len(data)
    if size == 0:
        raise ValueError('Audio selection is empty')
    if size % SAMPLE_WIDTH != 0:
        raise ValueError('PCM byte length must be even')
    if size > MAX_DATA_BYTES:
        raise ValueError(f'Audio exceeds 1.08 s limit ({size} > {MAX_DATA_BYTES} bytes)')
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        size + 36,
        b'WAVE',
        b'fmt ',
        16,
        1,
        CHANNELS,
        SAMPLE_RATE,
        SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH,
        CHANNELS * SAMPLE_WIDTH,
        SAMPLE_WIDTH * 8,
        b'data',
        size,
    )
    return header + data


def validate_canonical_wav(wav_bytes):
    """Verify exact camera WAV header and bounds (matches firmware audio.py validate)."""
    size = len(wav_bytes)
    if size < HEADER_SIZE + SAMPLE_WIDTH or size > MAX_FILE_BYTES:
        raise ValueError('WAV file size out of camera bounds (46..17324 bytes)')
    fields = struct.unpack_from('<4sI4s4sIHHIIHH4sI', wav_bytes, 0)
    (riff, riff_size, wave_id, fmt_id, fmt_size, audio_fmt, channels,
     rate, byte_rate, block_align, bits, data_id, data_size) = fields
    if riff != b'RIFF' or riff_size + 8 != size or wave_id != b'WAVE':
        raise ValueError('Invalid RIFF/WAVE header')
    if fmt_id != b'fmt ' or fmt_size != 16 or audio_fmt != 1:
        raise ValueError('WAV must be uncompressed PCM with a 16-byte fmt chunk')
    if (channels, rate, byte_rate, block_align, bits) != (1, 8000, 16000, 2, 16):
        raise ValueError('WAV must be 8000 Hz, mono, 16-bit signed PCM')
    if data_id != b'data' or data_size == 0 or data_size % 2 != 0:
        raise ValueError('Invalid or empty WAV data chunk')
    if data_size > MAX_DATA_BYTES or data_size + HEADER_SIZE != size:
        raise ValueError('WAV data chunk length mismatch or exceeds 1.08 s')
    return data_size


def slot_path(output_folder, slot):
    """Resolve the destination WAV path for SLOT inside OUTPUT_FOLDER."""
    if slot not in SOUND_SLOTS:
        raise ValueError(f'Unknown sound slot: {slot}')
    return Path(output_folder) / SOUND_SLOTS[slot]


def save_canonical_wav(output_folder, slot, wav_bytes, overwrite=False):
    """Validate and write a canonical WAV to the chosen slot inside OUTPUT_FOLDER."""
    validate_canonical_wav(wav_bytes)
    target = slot_path(output_folder, slot)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = 'wb' if overwrite else 'xb'
    with target.open(mode) as handle:
        handle.write(wav_bytes)
    return target


def example_wav(slot):
    """Generate one of the four original, procedurally synthesized example clicks."""
    if slot not in (1, 2, 3, 4):
        raise ValueError('Example slot must be 1..4')
    rng = random.Random(530 + slot)
    duration = (0.12, 0.19, 0.14, 0.16)[slot - 1]
    samples = []
    for i in range(round(duration * SAMPLE_RATE)):
        t = i / SAMPLE_RATE
        noise = rng.uniform(-1, 1)
        if slot == 4:
            value = math.sin(2 * math.pi * 1100 * t) * math.exp(-t / 0.025)
        else:
            decay = 0.009 if slot == 1 else 0.014
            value = (0.55 * noise + 0.3 * math.sin(2 * math.pi * 680 * t)
                     + 0.15 * math.sin(2 * math.pi * 1730 * t)) * math.exp(-t / decay)
            if slot == 2 and t >= 0.065:
                value += 0.7 * (noise + math.sin(2 * math.pi * 900 * t)) * 0.5 * math.exp(-(t - 0.065) / 0.014)
            if slot == 3:
                value *= 0.5
        value *= min(1, i / 4) * min(1, (duration - t) * 400)
        samples.append(round(max(-1, min(1, value)) * 10000))
    pcm = struct.pack('<' + 'h' * len(samples), *samples)
    return encode_canonical_wav(pcm)
