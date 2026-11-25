"""Optional native gate execution. Requires caller's stock EG file and Unicorn.

Stops at the native accept/reject continuation, before downstream drawing or
eligibility services. Snapshot and stack RAM are synthetic; no hardware I/O.
"""
import argparse
import json
import struct
from pathlib import Path

from z30_extensions.features.liveview.dual import HOOKS, SNAPSHOT
from z30_extensions.build import build
from z30_extensions.platform.z30_120 import BASE
from z30_extensions.container import extract_eg


def verify(source, preset='liveview-dual'):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_CPU_ARM_CORTEX_A15, UC_ARM_REG_R0,
                                   UC_ARM_REG_R4, UC_ARM_REG_SP, UC_ARM_REG_LR)
    candidate, report = build(source, preset)
    count = 0
    stack = 0x50000000
    initial_sp = stack + 0x800
    saved_r4, saved_lr = 0x12345678, stack + 0x101
    for site, _, prologue, yes, no in HOOKS:
        results = []
        for image in (source, candidate):
            u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
            u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A15)
            u.mem_map(BASE, (len(image) + 4095) & ~4095)
            u.mem_write(BASE, image)
            u.mem_map(SNAPSHOT & ~4095, 4096)
            u.mem_map(stack, 4096)
            hit = []

            def stop(emulator, address, size, unused):
                if address in (yes, no):
                    hit.append(address)
                    emulator.emu_stop()

            u.hook_add(UC_HOOK_CODE, stop)
            image_results = []
            for disp in range(256):
                hit.clear()
                u.mem_write(SNAPSHOT + 0x196, bytes([disp]))
                u.reg_write(UC_ARM_REG_R0, SNAPSHOT)
                u.reg_write(UC_ARM_REG_R4, saved_r4)
                u.reg_write(UC_ARM_REG_SP, initial_sp)
                u.reg_write(UC_ARM_REG_LR, saved_lr)
                u.emu_start(site | 1, 0, count=100)
                if len(hit) != 1:
                    raise ValueError('Gate failed to reach exactly one continuation')
                expected_sp = initial_sp - 8 if prologue else initial_sp
                if u.reg_read(UC_ARM_REG_SP) != expected_sp:
                    raise ValueError('Unexpected gate stack adjustment')
                if u.reg_read(UC_ARM_REG_R4) != (0 if prologue else saved_r4):
                    raise ValueError('Unexpected R4 preservation')
                if u.reg_read(UC_ARM_REG_LR) != saved_lr:
                    raise ValueError('Unexpected LR preservation')
                if prologue and bytes(u.mem_read(expected_sp, 8)) != struct.pack('<II', saved_r4, saved_lr):
                    raise ValueError('Unexpected saved register frame')
                image_results.append(hit[0])
                count += 1
            results.append(image_results)
        for disp in range(256):
            if results[0][disp] != (yes if disp == 2 else no):
                raise ValueError('Unexpected original gate behavior')
            if results[1][disp] != (yes if disp in (2, 3, 5) else no):
                raise ValueError('Unexpected patched gate behavior')
    return {'feature': 'liveview_dual', 'candidate_sha256': report['sha256'],
            'gate_executions': count, 'passed': True,
            'scope': 'Two stock and patched gate windows, 256 DISP values, stack/R4/LR.',
            'limitations': 'Synthetic RAM; stops before native downstream eligibility, drawing and hardware.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--extracted-eg', action='store_true')
    parser.add_argument('--preset', choices=['liveview-dual', 'full', 'modular'], default='liveview-dual')
    args = parser.parse_args()
    supplied = args.source.read_bytes()
    source = supplied if args.extracted_eg else extract_eg(supplied)
    print(json.dumps(verify(source, args.preset), indent=2))


if __name__ == '__main__':
    main()
