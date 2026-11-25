"""Execute the PTP ReadMemory record lookup and transaction offline.

Lookup: the patched 0x102F8C74 must return the owned record for 0x9FF1 and
the stock result for every other operation code. Transaction: the stock
buffer getters run; the data-phase sender, responder and cache clean are
fixtures that record their arguments.

Usage:
  python tools/verify_ptp_read.py --output build/research/ptp-read-verify.json
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import BASE
from z30_extensions.features.research import ptp_read as pr
from native_runtime import Context, RAM
from build_cmos_dump import build
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_SP

REQUEST, CONTAINER, DDR = RAM + 0x1000, 0x8B000000, 0x8C000000
BUFFER_POINTER = 0xC18CA8D4


class Machine:
    def __init__(self, stock, image):
        self.cx = Context(stock, image)
        self.u = self.cx.u
        self.calls = []
        self.send_result = 1
        self.u.hook_add(UC_HOOK_MEM_UNMAPPED, self.unmapped)
        for address, handler in ((pr.SEND_DATA, self.send), (pr.RESPOND, self.respond), (pr.CACHE_CLEAN, self.clean)):
            self.u.hook_add(UC_HOOK_CODE, handler, begin=address, end=address)

    def unmapped(self, u, access, address, size, value, data):
        if 0xC0000000 <= address < 0xE0000000:
            self.cx.map(address & ~0xFFF, 0x1000); return True
        return False

    def ret(self, u, value):
        u.reg_write(UC_ARM_REG_R0, value); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def send(self, u, *unused):
        request, buffer, length, flag = (u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        self.calls.append(('send', request, buffer, length, flag, bytes(u.mem_read(buffer, length))))
        self.ret(u, self.send_result)

    def respond(self, u, *unused):
        request, code, r2, r3 = (u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        count = struct.unpack('<I', u.mem_read(u.reg_read(UC_ARM_REG_SP), 4))[0]
        self.calls.append(('respond', request, code, r2, r3, count))
        self.ret(u, 0)

    def clean(self, u, *unused):
        self.calls.append(('clean', u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)))
        self.ret(u, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--portrait', action='store_true', help='check the full + portrait-ui live build instead')
    args = parser.parse_args()
    stock = extract_eg(args.source.read_bytes())
    if args.portrait:
        from z30_extensions.build import build as build_presets
        image, report = build_presets(stock, ['full', 'portrait-ui'], ptp_read=True)
    else:
        image, report = build(stock, ptp=True)
    feature = report['features']['ptp_read']
    record = int(feature['record'], 16)
    execute = int(feature['functions']['execute'], 16)
    stock_m, patched = Machine(stock, stock), Machine(stock, image)
    cases = []

    # 1. Lookup: owned record for the new code, stock answer for everything else.
    assert patched.cx.call(pr.LOOKUP, (pr.OPCODE,)) == record
    assert struct.unpack('<7I', patched.u.mem_read(record, 28))[:2] == (pr.OPCODE, 1)
    codes = list(range(0x1000, 0x1100)) + list(range(0x9000, 0xA000)) + [0, 0xFFFF, 0x9FF0, 0x9FF2]
    for code in codes:
        if code == pr.OPCODE:
            continue
        assert patched.cx.call(pr.LOOKUP, (code,)) == stock_m.cx.call(pr.LOOKUP, (code,)), hex(code)
    assert stock_m.cx.call(pr.LOOKUP, (pr.OPCODE,)) == 0
    cases.append(dict(name='lookup_owned_record_and_stock_for_other_codes', passed=True, codes=len(codes)))

    m = patched
    m.cx.map(CONTAINER, 0x200000 + 0x1000)
    m.u.mem_write(BUFFER_POINTER, struct.pack('<I', CONTAINER))
    buffer = CONTAINER + 12
    m.cx.map(DDR, 0x10000)
    ddr = bytes((i * 37 + 11) & 255 for i in range(0x10000))
    m.u.mem_write(DDR, ddr)
    capacity = m.cx.call(pr.DATA_CAPACITY)
    assert capacity == 0x1FFFF8

    def transact(params, send_result=1):
        m.calls.clear(); m.send_result = send_result
        m.cx.map(REQUEST, 0x100)
        m.u.mem_write(REQUEST, struct.pack('<IHHI', 12 + 4 * len(params), 1, pr.OPCODE, 7)
                      + b''.join(struct.pack('<I', p & 0xFFFFFFFF) for p in params) + bytes(20))
        assert m.cx.call(execute, (0, REQUEST)) == 0
        return list(m.calls)

    calls = transact([0x10020000, 64])
    assert calls[0] == ('clean', buffer, 64)
    assert calls[1][:5] == ('send', REQUEST, buffer, 64, 0xFFFFFFFF) and calls[1][5] == image[:64]
    assert calls[2] == ('respond', REQUEST, pr.OK, 0, 0, 0) and len(calls) == 3
    cases.append(dict(name='rom_read_exact_bytes_ok', passed=True))

    calls = transact([DDR + 0x123, 0x800])
    assert calls[1][5] == ddr[0x123:0x923] and calls[2][2] == pr.OK
    cases.append(dict(name='ddr_read_exact_bytes_ok', passed=True))

    calls = transact([DDR, 0])
    assert [c[0] for c in calls] == ['send', 'respond'] and calls[0][3] == 0 and calls[1][2] == pr.OK
    cases.append(dict(name='zero_length_sends_empty_data_phase', passed=True))

    calls = transact([0x10020000, 0x7FFFFFFF])
    assert calls[1][3] == capacity and calls[1][5] == image[:capacity] and calls[2][2] == pr.OK
    cases.append(dict(name='length_truncated_to_stock_buffer_capacity', passed=True, capacity=hex(capacity)))

    for name, params in (('one_parameter', [0x10020000]), ('no_parameters', []),
                         ('below_nor', [0x0FFFFFF0, 0x20]), ('nor_end_crossing', [0x13FFFFF0, 0x20]),
                         ('between_windows', [0x50000000, 4]), ('ddr_end_crossing', [0xDFFFFFF0, 0x20]),
                         ('above_ddr', [0xE0000000, 4]), ('address_wrap', [0xFFFFFFF0, 0x20])):
        calls = transact(params)
        assert calls == [('respond', REQUEST, pr.INVALID_PARAMETER, 0, 0, 0)], (name, calls)
        cases.append(dict(name=f'invalid_{name}_no_data_phase', passed=True))

    # Peripheral windows: aligned 32-bit loads only.
    from unicorn import UC_HOOK_MEM_READ
    sif = 0x20380000
    m.cx.map(sif, 0x1000)
    registers = bytes((i * 13 + 5) & 255 for i in range(0x1000))
    m.u.mem_write(sif, registers)
    sizes = []
    hook = m.u.hook_add(UC_HOOK_MEM_READ, lambda u, a, addr, size, v, d: sizes.append(size),
                        begin=sif, end=sif + 0xFFF)
    calls = transact([sif + 0x40, 0x100])
    m.u.hook_del(hook)
    assert calls[1][5] == registers[0x40:0x140] and calls[2][2] == pr.OK
    assert sizes and set(sizes) == {4} and len(sizes) == 0x40, (set(sizes), len(sizes))
    cases.append(dict(name='mmio_read_exact_words_with_32bit_loads_only', passed=True, loads=len(sizes)))
    for name, params in (('mmio_unaligned_address', [sif + 2, 8]), ('mmio_length_not_word', [sif, 6]),
                         ('mmio_over_limit', [0x20200000, 0x1004]), ('mmio_window_end_crossing', [0x2038FFFC, 8]),
                         ('mmio_between_windows', [0x20390000, 4]), ('mmio_outside_all', [0x20000000, 4])):
        calls = transact(params)
        assert calls == [('respond', REQUEST, pr.INVALID_PARAMETER, 0, 0, 0)], (name, calls)
        cases.append(dict(name=f'invalid_{name}_no_data_phase', passed=True))

    calls = transact([0x10020000, 16], send_result=0)
    assert calls[-1][2] == pr.INCOMPLETE
    cases.append(dict(name='failed_data_phase_reports_incomplete', passed=True))

    result = dict(image_sha256=hashlib.sha256(image).hexdigest(), feature=feature, cases=cases, passed=True,
                  limitations=['USB transport, data-phase sender and responder are fixtures.',
                               'The stock permission checkers are bypassed by design; not executed here.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'{len(cases)} cases passed')


if __name__ == '__main__':
    main()
