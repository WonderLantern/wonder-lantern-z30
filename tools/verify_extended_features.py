"""Optional native M900 and WAV routine checks using a user-supplied stock file."""
import argparse
import binascii
import json
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from native_runtime import Context, RAM
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R6
from z30_extensions.container import extract_eg
from z30_extensions.build import build
from z30_extensions.features.long_exposure import GETTER, KEY
from z30_extensions.features.sound_layout import MAX_DATA


def verify_m900(stock, image):
    sessions = [Context(stock, data) for data in (stock, image)]
    keys = (KEY, 0x1823, 0x1D0C, 0x1901, 0x205, 0x213, 0x3E05, 0x3100, 0x1818, 0x577, 0x20E)
    for s in sessions:
        for key in keys: s.seed(key)
        s.set(0x1D0C, 1); s.set(0x1901, 0); s.set(KEY, 0)
    reads = []
    for stored in (0, 1, 2, 0xFFFFFFFF):
        for index, s in enumerate(sessions):
            s.set(KEY, stored)
            s.u.mem_write(RAM, b'\xCC' * 12)
            if s.call(GETTER, (KEY, RAM + 4)) != 1:
                raise ValueError('Flag read failed')
            expected = 1 if index else stored
            if int.from_bytes(s.u.mem_read(RAM + 4, 4), 'little') != expected:
                raise ValueError('Extended flag override incorrect')
            if bytes(s.u.mem_read(RAM, 4)) != b'\xCC' * 4 or bytes(s.u.mem_read(RAM + 8, 4)) != b'\xCC' * 4:
                raise ValueError('Flag read overwrote adjacent memory')
            if s.call(0x101993DE, (KEY,)) != expected or s.call(0x10465984, (KEY,)) != expected:
                raise ValueError('Native wrapper does not observe override')
            storage, _ = s.properties[KEY]
            if int.from_bytes(s.u.mem_read(storage, 4), 'little') != stored:
                raise ValueError('Persisted flag RAM was modified')
            reads.append({'stock_or_candidate': index, 'stored': stored, 'observed': expected})
    for key in (0x205, 0x213, 0x3E05, 0x3100, 0x1823):
        for value in (0, 1, 900, 0xFFFFFFFF):
            for s in sessions:
                s.set(key, value)
                if s.call(0x101993DE, (key,)) != value:
                    raise ValueError('Unrelated property changed')
    modes = []
    for name, mode in (('P', 0x1D), ('S', 0x1E), ('A', 0x1F), ('M', 0x20)):
        bounds = []
        for index, s in enumerate(sessions):
            s.set(KEY, 0); s.set(0x1823, mode)
            extended = int(index == 1 and name == 'M')
            if s.call(0x103349DA) != extended:
                raise ValueError('Native M-only gate changed')
            s.block(0x104946BE, 0x104946D8)
            gui = s.u.reg_read(UC_ARM_REG_R0)
            s.u.reg_write(UC_ARM_REG_R0, mode)
            s.block(0x10230AB8, 0x10230AD6)
            capture = s.u.reg_read(UC_ARM_REG_R6)
            if gui != (900 if extended else 32) or capture != gui:
                raise ValueError('GUI/capture bound mismatch')
            bounds.append(gui)
        modes.append({'mode': name, 'stock_and_candidate_bounds': bounds})
    seconds = (60, 90, 120, 180, 240, 300, 480, 600, 720, 900)
    for value in seconds:
        if sessions[1].call(0x10353EF4, (value,)) != value * 10000:
            raise ValueError('Native duration encoding mismatch')
    enumeration, request_decodes = [], []
    for name, mode in (('P', 0x1D), ('S', 0x1E), ('A', 0x1F), ('M', 0x20)):
        for index, s in enumerate(sessions):
            s.set(0x1823, mode); s.set(0x1818, 0); s.set(0x577, 0); s.set(0x20E, 1)
            # Only normal speed/type availability arrays are synthetic. The
            # native enumerator itself decides to append extended entries.
            for key, values in ((0x205, (288, 0, -120)), (0x20E, (1, 2, 3)), (0x213, seconds)):
                if s.call(0x1023744E, (key, RAM + 0x2000)) != 1:
                    raise ValueError('Native option record lookup failed')
                record_index = int.from_bytes(s.u.mem_read(RAM + 0x2000, 4), 'little')
                record = 0xC18AC27C + record_index * 32
                pointer = int.from_bytes(s.u.mem_read(record + 12, 4), 'little')
                s.map(pointer, len(values) * 4)
                s.u.mem_write(pointer, struct.pack('<' + 'I' * len(values), *[v & 0xFFFFFFFF for v in values]))
                s.u.mem_write(record + 8, struct.pack('<I', len(values)))
            buffer = RAM + 0x3000
            s.u.mem_write(buffer - 4, b'\xCC' * 0x20C)
            count = s.call(0x1035348C, (buffer, 0x200))
            values = list(struct.unpack('<' + 'I' * count, s.u.mem_read(buffer, count * 4)))
            extended = [v * 10000 for v in seconds] if index and name == 'M' else []
            if count != 5 + len(extended) or [v for v in values if v in [x * 10000 for x in seconds]] != extended:
                raise ValueError('Native speed enumeration mismatch')
            if bytes(s.u.mem_read(buffer - 4, 4)) != b'\xCC' * 4 or bytes(s.u.mem_read(buffer + 0x200, 8)) != b'\xCC' * 8:
                raise ValueError('Native enumeration overwrote guards')
            enumeration.append({'mode': name, 'stock_or_candidate': index, 'count': count})
            for value in (60, 900, 901):
                s.u.mem_write(RAM + 0x4000, b'\xCC' * 8)
                result = s.call(0x10353616, (value * 10000, RAM + 0x4000, RAM + 0x4004))
                accepted = bool(index and name == 'M' and value <= 900)
                if result != (0x2001 if accepted else 0x201C):
                    raise ValueError('Native speed request acceptance mismatch')
                if accepted and struct.unpack('<2I', s.u.mem_read(RAM + 0x4000, 8)) != (1, value):
                    raise ValueError('Native speed request decode mismatch')
                request_decodes.append({'mode': name, 'stock_or_candidate': index, 'seconds': value, 'accepted': accepted})
    return {'flag_reads': reads, 'unrelated_property_comparisons': 20, 'mode_bounds': modes,
            'duration_conversions': len(seconds), 'speed_enumeration': enumeration,
            'request_decodes': request_decodes, 'physical_exposure_verified': False}


def verify_wav(stock, image, report):
    s = Context(stock, image)
    f = {k: int(v, 16) for k, v in report['features']['menu_framework']['functions'].items()}
    input_address, output_address = RAM + 0x10000, RAM + 0x30000
    def wav(samples):
        data = struct.pack('<' + 'h' * len(samples), *samples)
        return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', len(data) + 36, b'WAVE', b'fmt ',
                           16, 1, 1, 8000, 16000, 2, 16, b'data', len(data)) + data
    rng = random.Random(530)
    vectors = [[0], [-32768], [32767], [-32768, 32767, 0, -1, 1],
               [rng.randint(-32768, 32767) for _ in range(55)],
               [rng.randint(-32768, 32767) for _ in range(MAX_DATA // 2)]]
    for samples in vectors:
        payload = wav(samples)
        s.u.mem_write(input_address, payload)
        if s.call(f['validate'], (input_address, len(payload))) != len(samples) * 2:
            raise ValueError('Valid WAV rejected')
        size = len(samples) * 24
        s.u.mem_write(output_address - 4, b'\xCC' * (size + 8))
        if s.call(f['convert'], (input_address + 44, output_address, len(samples) * 2)) != size:
            raise ValueError('PCM output length incorrect')
        expected = []
        for i, x in enumerate(samples):
            y = samples[min(i + 1, len(samples) - 1)]
            for step in range(6):
                numerator = 6 * x + (y - x) * step
                value = abs(numerator) // 6 * (1 if numerator >= 0 else -1)
                expected.extend((value, value))
        if bytes(s.u.mem_read(output_address, size)) != struct.pack('<' + 'h' * len(expected), *expected):
            raise ValueError('PCM conversion differs from reference')
        if bytes(s.u.mem_read(output_address - 4, 4)) != b'\xCC' * 4 or bytes(s.u.mem_read(output_address + size, 4)) != b'\xCC' * 4:
            raise ValueError('PCM output overwrote guards')
    valid = wav([-32768, 0, 32767])
    invalid = []
    for offset in (0, 4, 8, 12, 16, 20, 22, 24, 28, 32, 34, 36, 40):
        data = bytearray(valid); data[offset] ^= 1; invalid.append(data)
    invalid.extend((b'', valid[:43], valid[:-1], valid + b'X', wav([0] * 8641), wav([])))
    for payload in invalid:
        s.u.mem_write(input_address, bytes(payload))
        if s.call(f['validate'], (input_address, len(payload))) != 0:
            raise ValueError('Invalid WAV accepted')
    return {'pcm_vectors': len(vectors), 'invalid_wavs_rejected': len(invalid),
            'abi_and_buffer_guards': True, 'speaker_or_filesystem_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--extracted-eg', action='store_true')
    parser.add_argument('--preset', choices=['full', 'modular'], default='full')
    args = parser.parse_args()
    supplied = args.source.read_bytes()
    stock = supplied if args.extracted_eg else extract_eg(supplied)
    image, report = build(stock, args.preset)
    if binascii.crc_hqx(image, 0): raise ValueError('Candidate CRC mismatch')
    print(json.dumps({'candidate_sha256': report['sha256'], 'm900': verify_m900(stock, image),
                      'wav': verify_wav(stock, image, report), 'passed': True,
                      'limitations': 'Synthetic property/stack RAM; no full camera, GUI pixels, speaker or sensor exposure.'}, indent=2))


if __name__ == '__main__': main()
