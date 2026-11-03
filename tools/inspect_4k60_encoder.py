"""Offline Z30 C1.20 4K 50p/60p encoder capability probe; never accesses a camera.

Runs the stock encoder parameter constructor with synthetic 4K 50p/60p inputs,
sweeps the stock level-to-macroblocks-per-second helper, and records whether the
configuration path can express a Level 5.2 (4K 60p) recording. The conclusions
below concern the encoder parameter stack only; the capture pipeline and the
accelerator itself are not modeled.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE, SCATTER, sha256

CONSTRUCTOR = 0x102DFBFE
SERIALIZER = 0x102DFF5C
LEVEL_HELPER = 0x102E9DE8
SUBSTITUTED = (0x102E9CC8, 0x103967BC)
SCRATCH = 0x50000000
OUTPUT = SCRATCH + 0x1000
STOP = SCRATCH + 0xF000
STACK = SCRATCH + 0x18000
CONFIG_SIZE = 0x1A0
PROFILE_HIGH = 100


class Session:
    def __init__(self, data):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_PC, UC_ARM_REG_LR
        self._regs = (UC_ARM_REG_R0, UC_ARM_REG_PC, UC_ARM_REG_LR)
        u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.mem_map(BASE, (len(data) + 4095) & ~4095)
        u.mem_write(BASE, data)
        source, destination, length, _ = SCATTER
        u.mem_map(destination, (length + 4095) & ~4095)
        u.mem_write(destination, data[source - BASE:source - BASE + length])
        u.mem_map(SCRATCH, 0x20000)
        u.mem_map(0x24070000, 0x2000)
        self.uc = u
        self.returned = False

        def code(uc, address, size, _):
            if address == STOP:
                self.returned = True
                uc.emu_stop()
            elif address in SUBSTITUTED:
                uc.reg_write(UC_ARM_REG_R0, SCRATCH)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

        u.hook_add(UC_HOOK_CODE, code)

    def call(self, address, args, stack_words=()):
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                       UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR)
        self.returned = False
        if stack_words:
            self.uc.mem_write(STACK, struct.pack('<%dI' % len(stack_words), *stack_words))
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        self.uc.reg_write(UC_ARM_REG_LR, STOP | 1)
        for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            self.uc.reg_write(reg, value)
        self.uc.emu_start(address | 1, 0, count=100000)
        assert self.returned, 'Instruction bound reached before stock return'
        assert self.uc.reg_read(UC_ARM_REG_SP) == STACK, 'Unbalanced stack'
        return self.uc.reg_read(UC_ARM_REG_R0)

    def construct(self, width, height, fps, bitrate_bps=144000000):
        """Synthetic caller context modeled on the stock caller at 0x102C9C28."""
        stack = (bitrate_bps >> 10, bitrate_bps >> 10, fps, 1, 20, 1, 0, 0, 0, 5, 1, 1, 1, 1, OUTPUT)
        assert self.call(CONSTRUCTOR, [15, 1, width, height], stack) == 0
        assert struct.unpack('<I', self.uc.mem_read(OUTPUT, 4))[0] == SCRATCH
        return bytes(self.uc.mem_read(SCRATCH, CONFIG_SIZE))


def inspect(data):
    session = Session(data)
    rows = []
    for width, height, fps, label in (
        (3840, 2160, 29, '4K 30p stock reference'),
        (1920, 1080, 59, '1080 60p stock reference'),
        (1920, 1080, 29, '1080 30p stock reference'),
        (1920, 1080, 119, '1080 120p analog'),
        (3840, 2160, 49, '4K 50p analog'),
        (3840, 2160, 59, '4K 59.94 analog'),
        (3840, 2160, 31, '4K above 5.1 macroblock rate'),
        (3840, 2160, 120, '4K 120 analog'),
    ):
        blob = session.construct(width, height, fps)
        level = blob[0x61]
        profile = blob[0x60]
        limit = session.call(LEVEL_HELPER, [level, 0, 0, 0], ())
        rows.append(dict(label=label, width=width, height=height, synthetic_fps=fps,
                         constructed_profile_byte=profile, constructed_level_byte=level,
                         native_level_macroblocks_per_second=limit,
                         native_level_megapixels_per_second=round(limit * 256 / 1e6, 2)))
    stock_rows = [row for row in rows if 'stock reference' in row['label']]
    assert all(row['constructed_profile_byte'] == PROFILE_HIGH for row in rows)
    assert [row['constructed_level_byte'] for row in stock_rows] == [51, 50, 41]
    # The constructor never reaches level 52, even for inputs whose macroblock
    # rate exceeds the Level 5.1 limit (4K 31p needs 1,004,400 MB/s > 983,040).
    assert all(row['constructed_level_byte'] == 51
               for row in rows if row['width'] == 3840), '4K levels must stay capped at 5.1'

    ladder = {}
    for level in range(1, 67):
        ladder[level] = session.call(LEVEL_HELPER, [level, 0, 0, 0], ())
    changes = {}
    previous = None
    for level in sorted(ladder):
        if ladder[level] != previous:
            changes[level] = ladder[level]
        previous = ladder[level]
    # The helper knows Level 5.2 (2,073,600 MB/s) although no constructed
    # configuration uses it: the parameter stack tops out at Level 5.1.
    assert changes.get(52) == 2073600, changes
    assert changes.get(51) == 983040
    return dict(stock_eg_sha256=sha256(data), camera_access=False,
                method='Stock ARM parameter construction and level helper in Unicorn',
                level_ladder_macroblocks_per_second={str(k): v for k, v in changes.items()},
                limitations=['Allocation/lookup calls return synthetic buffers.',
                             'Inputs are synthetic; no bitstream is produced.',
                             'The capture pipeline and accelerator are not modeled.',
                             'A Level 5.2 ladder entry does not prove the silicon sustains its rate.'],
                scenarios=rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = inspect(extract_eg(args.source.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    for row in result['scenarios']:
        print(f"{row['width']}x{row['height']} fps {row['synthetic_fps']:3d} ({row['label']}): "
              f"profile {row['constructed_profile_byte']} level {row['constructed_level_byte']} "
              f"MaxMBPS {row['native_level_macroblocks_per_second']}")
    print('level ladder changes:', json.dumps(result['level_ladder_macroblocks_per_second']))


if __name__ == '__main__':
    main()
