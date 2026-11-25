"""Optional native execution of the mtp-fast wrapper. Requires caller's stock EG file and Unicorn.

Enters at the patched builder and stops where the displaced prologue rejoins
the stock builder. Handle conversion, record acquire/release and the builder
body are stubs; the count-only flag and stack RAM are synthetic.
"""
import argparse
import json
import struct
from pathlib import Path

from z30_extensions.features.connect.mtp_fast import BUILDER, EXPECTED_PREFIX, HANDLE_TO_FILE, ACQUIRE_RECORD, RELEASE_RECORD, COUNT_ONLY, FOLDER_KIND, VIRTUAL_KIND
from z30_extensions.build import build
from z30_extensions.platform.z30_120 import BASE
from z30_extensions.container import extract_eg

FILE_ID, RETURN, STACK = 0x77, 0x20000000, 0x30008000


def verify(source):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn import arm_const as reg
    candidate, report = build(source, 'mtp-fast')
    resume = BUILDER + len(EXPECTED_PREFIX)
    stubs = {HANDLE_TO_FILE: 'convert', ACQUIRE_RECORD: 'acquire', RELEASE_RECORD: 'release'}
    pushed = [getattr(reg, f'UC_ARM_REG_R{n}') for n in range(4, 13)]
    saved = {register: 0x1100 + register for register in pushed[:8]}
    count = 0

    def run(handle, count_only, record=0x5555AAAA, result=0x123):
        nonlocal count
        count += 1
        u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        size = (len(candidate) + 0xFFF) & ~0xFFF
        u.mem_map(BASE, size)
        u.mem_write(BASE, candidate)
        u.mem_map(COUNT_ONLY & ~0xFFF, 0x1000)
        u.mem_write(COUNT_ONLY, struct.pack('<I', count_only))
        u.mem_map(STACK - 0x8000, 0x10000)
        u.mem_map(RETURN, 0x1000)
        calls = []

        def hook(u, address, _size, _user):
            if address == RETURN:
                return u.emu_stop()
            if address == resume:
                # Displaced prologue: push {r4-r12,lr}; mov r10,r2; mov r11,r0.
                calls.append(('builder', *(u.reg_read(r) for r in (
                    reg.UC_ARM_REG_R0, reg.UC_ARM_REG_R1, reg.UC_ARM_REG_R2, reg.UC_ARM_REG_R10, reg.UC_ARM_REG_R11))))
                sp = u.reg_read(reg.UC_ARM_REG_SP)
                frame = struct.unpack('<10I', u.mem_read(sp, 40))
                for register, value in zip(pushed, frame):
                    u.reg_write(register, value)
                u.reg_write(reg.UC_ARM_REG_SP, sp + 40)
                u.reg_write(reg.UC_ARM_REG_R0, result)
                u.reg_write(reg.UC_ARM_REG_PC, frame[9])
            elif address in stubs:
                name = stubs[address]
                calls.append((name, u.reg_read(reg.UC_ARM_REG_R0)))
                u.reg_write(reg.UC_ARM_REG_R0, {'convert': FILE_ID, 'acquire': record, 'release': 0}[name])
                for register in (reg.UC_ARM_REG_R1, reg.UC_ARM_REG_R2, reg.UC_ARM_REG_R3, reg.UC_ARM_REG_R12):
                    u.reg_write(register, 0xDEAD0000)
                u.reg_write(reg.UC_ARM_REG_PC, u.reg_read(reg.UC_ARM_REG_LR))

        u.hook_add(UC_HOOK_CODE, hook)
        for register, value in saved.items():
            u.reg_write(register, value)
        u.reg_write(reg.UC_ARM_REG_SP, STACK)
        u.reg_write(reg.UC_ARM_REG_LR, RETURN | 1)
        u.reg_write(reg.UC_ARM_REG_R0, handle)
        u.reg_write(reg.UC_ARM_REG_R1, 0x1000)
        u.reg_write(reg.UC_ARM_REG_R2, 0x2000)
        u.emu_start(BUILDER | 1, 0, count=2000)
        if (u.reg_read(reg.UC_ARM_REG_PC) != RETURN or u.reg_read(reg.UC_ARM_REG_SP) != STACK
                or u.reg_read(reg.UC_ARM_REG_R0) != result
                or any(u.reg_read(register) != value for register, value in saved.items())):
            raise ValueError('Wrapper did not return cleanly')
        return calls

    def expect(actual, expected):
        if actual != expected:
            raise ValueError(f'Unexpected wrapper call sequence: {actual}')

    for kind in range(32):
        handle = kind << 27 | 0x190001
        body = ('builder', handle, 0x1000, 0x2000, 0x2000, handle)
        held = [('convert', handle), ('acquire', FILE_ID), body, ('release', 0x5555AAAA)]
        if kind in (FOLDER_KIND, VIRTUAL_KIND):
            held = [body]
        expect(run(handle, 0), held)
        expect(run(handle, 0, result=0xFFFFFFFF), held)
        expect(run(handle, 0, record=0), held[:3])
        expect(run(handle, 1), [body])
    return {'feature': 'mtp_fast', 'candidate_sha256': report['sha256'],
            'wrapper_executions': count, 'passed': True,
            'scope': '32 handle kinds; value and count-only passes; builder failure; failed acquire; stack/R4-R11.',
            'limitations': 'Record services and the builder body are stubs; no filesystem, USB or concurrency.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--extracted-eg', action='store_true')
    args = parser.parse_args()
    supplied = args.source.read_bytes()
    source = supplied if args.extracted_eg else extract_eg(supplied)
    print(json.dumps(verify(source), indent=2))


if __name__ == '__main__':
    main()
