"""Differential execution of sixth-page adapters, not a camera display test."""
import argparse
import json
import struct
from pathlib import Path

from z30_extensions.build import build
from z30_extensions.container import extract_eg
from z30_extensions.features.liveview_dual import SNAPSHOT
from z30_extensions.features.liveview_full_info import HISTOGRAM_MASK
from z30_extensions.features.liveview_full_info_windows import WINDOWS, PREFIXES
from z30_extensions.platform.z30_120 import BASE


def verify(source):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_SP,
                                   UC_ARM_REG_LR, UC_ARM_REG_CPSR,
                                   UC_CPU_ARM_CORTEX_A15, UC_ARM_REG_PC)
    candidate, report = build(source, 'liveview-dual')
    results = []
    scratch, stack = 0x50000000, 0x50010000
    registers = list(range(UC_ARM_REG_R0, UC_ARM_REG_R0 + 13)) + [UC_ARM_REG_SP, UC_ARM_REG_LR]
    for site, expected, dest, mode, classifier, replay in WINDOWS:
        size = len(bytes.fromhex(expected))
        machines = []
        for image in (source, candidate):
            u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
            u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A15)
            u.mem_map(BASE, (len(image) + 4095) & ~4095)
            u.mem_write(BASE, image)
            u.mem_map(SNAPSHOT & ~4095, 4096)
            u.mem_map(HISTOGRAM_MASK & ~4095, 4096)
            u.mem_map(scratch, 0x20000)
            u.mem_write(scratch, struct.pack('<I', scratch + 0x8000) * (0x10000 // 4))
            machines.append(u)
        observations = [[], []]
        for index, u in enumerate(machines):
            def stop(emulator, pc, length, unused):
                if not (site <= pc < site + size or BASE + len(source) - 2 <= pc < BASE + len(candidate) - 2):
                    emulator.emu_stop()
            u.hook_add(UC_HOOK_CODE, stop)
            for disp, hidden in [(d, False) for d in range(256)] + [(d, True) for d in range(256)]:
                # hidden: portrait-ui cleared the histogram masks; page 5 must not add its bit then.
                u.mem_write(HISTOGRAM_MASK, struct.pack('<I', 0 if hidden else 0xCCC))
                for flags in (0, 0xF8000000):
                    # For state 5, compare with the native presentation state.
                    reference_disp = mode if index == 0 and disp == 5 else disp
                    seed = [scratch + 0x100 + r * 0x100 for r in range(13)] + [stack + 0x800, 0x12345679]
                    if site in (0x1036C17C, 0x1036C3A8):
                        seed[1] = 2
                    if site == 0x1036EDC8:
                        seed[0] = 0
                    load_offset = PREFIXES.get(site, (0, ''))[0]
                    base_reg = bytes.fromhex(expected)[load_offset] & 15
                    seed[base_reg] = SNAPSHOT
                    if classifier is not None:
                        seed[classifier] = 31
                        if index == 0 and disp == 5 and not hidden:
                            seed[4] |= 0x111
                    for register, value in zip(registers, seed):
                        u.reg_write(register, value)
                    u.reg_write(UC_ARM_REG_CPSR, (u.reg_read(UC_ARM_REG_CPSR) & ~0xF8000000) | flags)
                    u.mem_write(SNAPSHOT + 0x196, bytes([reference_disp]))
                    u.emu_start(site | 1, 0, count=100)
                    state = tuple(u.reg_read(r) for r in registers) + (u.reg_read(UC_ARM_REG_CPSR) & 0xF8000000, u.reg_read(UC_ARM_REG_PC))
                    observations[index].append(state)
        for case, (stock, patched) in enumerate(zip(*observations)):
            if stock != patched:
                raise AssertionError(f'Adapter {site:#x}, DISP {case // 2 % 256}, hidden {case >= 512}, flags {case % 2}: {stock} != {patched}')
        results.append(dict(site=hex(site), cases=len(observations[0]), passed=True))
    return dict(passed=True, sha256=report['sha256'], adapters=results,
                adapter_executions=sum(r['cases'] * 2 for r in results),
                limitations='Synthetic register/RAM inputs; no GPU, sensor or physical camera validation.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    report = verify(extract_eg(a.source.read_bytes()))
    text = json.dumps(report, indent=2)
    if a.output:
        a.output.write_text(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
