"""Execute the drive-selector observer and mode-log export offline; never a camera.

The stock selector runs in Unicorn on the stock image and on the patched image
over real drive data from a camera dump (see read_cmos_dump.py); its CMOS
packets and return values must be identical. The observer's records, its
behaviour without page state, ring wrap and the WL-MODE.BIN writer are checked
with explicit allocator and file-service fixtures.

Usage:
  python tools/verify_cmos_trace.py --dump-dir build/research/cmos-ab --output build/research/cmos-trace-verify.json
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
from z30_extensions.features import cmos_dump as cd, cmos_trace as ct
from z30_extensions.features.sound_layout import STATE_ANCHOR, MAGIC as SOUND_MAGIC
from native_runtime import RAM, Context
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED
from build_cmos_dump import build
from decode_cmos_modes import Emulator, SELECTOR, SEQ_WRITER, COMMANDS, TYPES
from verify_cmos_dump import Fixture as WriterFixture, BOUNCE, FD
from unicorn.arm_const import UC_ARM_REG_R0

SOUND_STATE = 0x54000000
PARAMETER_PATTERN = bytes(range(0x40, 0x40 + 40))


class Traced(Emulator):
    def __init__(self, image_stock, image, blob, context, pointer, feature):
        self.cx = Context(image_stock, image)  # patched EG and tail, stock scatter
        self.u = self.cx.u
        self.cx.map(pointer, len(blob))
        self.u.mem_write(pointer, blob)
        self.u.mem_write(cd.CTX, context)
        self.packets = []
        self.u.hook_add(UC_HOOK_MEM_UNMAPPED, self.unmapped)
        self.u.hook_add(UC_HOOK_CODE, self.writer, begin=SEQ_WRITER, end=SEQ_WRITER)
        self.feature = feature
        self.state = None
        self.cx.map(ct.PARAMETERS, 64)
        self.u.mem_write(ct.PARAMETERS, PARAMETER_PATTERN)

    def install_state(self, state_offset, allocation):
        self.cx.map(SOUND_STATE, allocation + 64)
        self.u.mem_write(SOUND_STATE, bytes(allocation + 64))
        self.u.mem_write(SOUND_STATE + 56, struct.pack('<I', SOUND_MAGIC))
        self.u.mem_write(STATE_ANCHOR + 16, struct.pack('<I', SOUND_STATE))
        self.state = SOUND_STATE + state_offset

    def remove_state(self):
        self.u.mem_write(STATE_ANCHOR + 16, struct.pack('<I', 0))

    def select_global(self, command, drive_type):
        request = bytearray(0x40)
        request[0] = drive_type
        request[1:16] = bytes(range(1, 16))
        self.u.mem_write(ct.REQUEST, bytes(request))
        self.packets = []
        status = self.cx.call(SELECTOR, (command, ct.REQUEST), limit=2_000_000)
        return status, list(self.packets), bytes(request[:16])

    def word(self, offset):
        return struct.unpack('<I', self.u.mem_read(self.state + offset, 4))[0]

    def record(self, slot):
        raw = bytes(self.u.mem_read(self.state + ct.LOG_BASE + slot * ct.RECORD_BYTES, ct.RECORD_BYTES))
        sequence, command = struct.unpack_from('<2I', raw)
        return sequence, command, raw[8:24], raw[24:64]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--dump-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    stock = extract_eg(args.source.read_bytes())
    image, report = build(stock, trace=True)
    feature = report['features']['cmos_trace']
    header = json.loads((args.dump_dir / 'header.json').read_text(encoding='utf-8'))
    assert header['sum_matches']
    blob = (args.dump_dir / 'drive-data.bin').read_bytes()
    context = (args.dump_dir / 'context.bin').read_bytes()
    pointer = int(header['pointer'], 16)
    cases = []

    reference = Emulator(stock, blob, context, pointer)
    reference.cx.map(ct.REQUEST & ~0xFFF, 0x1000)
    traced = Traced(stock, image, blob, context, pointer, feature)
    traced.cx.map(ct.REQUEST & ~0xFFF, 0x1000)
    assert bytes(traced.u.mem_read(SELECTOR, 8)) != ct.SELECTOR_PROLOGUE

    def stock_select(command, drive_type):
        request = bytearray(0x40); request[0] = drive_type; request[1:16] = bytes(range(1, 16))
        reference.u.mem_write(ct.REQUEST, bytes(request)); reference.packets = []
        status = reference.cx.call(SELECTOR, (command, ct.REQUEST), limit=2_000_000)
        return status, list(reference.packets)

    # 1. Without page state: identical output, nothing recorded anywhere.
    for command in COMMANDS:
        for drive_type in TYPES:
            assert traced.select_global(command, drive_type)[:2] == stock_select(command, drive_type), (command, drive_type)
    cases.append(dict(name='no_state_identical_to_stock_all_commands_and_types', passed=True,
                      calls=len(COMMANDS) * len(TYPES)))

    # 2. With page state: identical output; only command 4 is recorded, in order.
    state_offset = feature['state_offset']
    traced.install_state(state_offset, state_offset + ct.STATE_BYTES)
    expected = []
    for command in COMMANDS:
        for drive_type in TYPES:
            status, packets, request = traced.select_global(command, drive_type)
            assert (status, packets) == stock_select(command, drive_type), (command, drive_type)
            if command == ct.COMMAND:
                expected.append(request)
    assert traced.word(ct.LOG_INDEX) == len(expected)
    for slot, request in enumerate(expected):
        assert traced.record(slot) == (slot + 1, ct.COMMAND, request, PARAMETER_PATTERN), slot
    assert traced.record(len(expected))[0] == 0
    cases.append(dict(name='state_present_identical_output_and_command4_records', passed=True,
                      records=len(expected)))

    # 3. Ring wrap: sequence keeps counting, slot = (sequence - 1) mod RECORDS.
    for index in range(ct.RECORDS + 5):
        traced.select_global(ct.COMMAND, index % 13)
    total = len(expected) + ct.RECORDS + 5
    assert traced.word(ct.LOG_INDEX) == total
    sequences = sorted(traced.record(slot)[0] for slot in range(ct.RECORDS))
    assert sequences == list(range(total - ct.RECORDS + 1, total + 1))
    cases.append(dict(name='ring_wraps_keeping_latest_records', passed=True, total=total))

    # 4. Export writes header + ring through the cache-cleaned writer.
    writer = WriterFixture(stock, image, dict(functions=dict(cmos_export=feature['functions']['cmos_log_export'])))
    ring = bytes(traced.u.mem_read(traced.state + ct.LOG_BASE, ct.RECORDS * ct.RECORD_BYTES))
    from verify_cmos_dump import STATE
    writer.reset()
    writer.u.mem_write(STATE, bytes(ct.STATE_BYTES))
    writer.word(STATE + cd.FD_OFF, -1)
    writer.word(STATE + ct.LOG_INDEX, total)
    writer.u.mem_write(STATE + ct.LOG_BASE, ring)
    assert writer.run() == len(ct.STATUS) - 1
    head = struct.pack('<7I', ct.LOG_MAGIC, ct.LOG_VERSION, total, ct.RECORDS, ct.RECORD_BYTES,
                       ct.REQUEST, ct.PARAMETERS) + bytes(4)
    assert bytes(writer.output) == head + ring
    assert writer.paths == [ct.LOG_PATH]
    cleans = [call for call in writer.calls if call[0] == 'clean']
    assert cleans == [('clean', BOUNCE, ct.LOG_HEADER + ct.RECORDS * ct.RECORD_BYTES)]
    cases.append(dict(name='log_export_layout_and_cache_clean', passed=True, size=len(writer.output)))

    writer.reset(open_ok=False)
    writer.u.mem_write(STATE, bytes(ct.STATE_BYTES)); writer.word(STATE + cd.FD_OFF, -1)
    assert writer.run() == 4 and not any(call[0] == 'write' for call in writer.calls)
    writer.reset(fail_write=1)
    writer.u.mem_write(STATE, bytes(ct.STATE_BYTES)); writer.word(STATE + cd.FD_OFF, -1)
    assert writer.run() == 5 and writer.calls[-1][0] == 'close'
    cases.append(dict(name='log_export_failures_reported_and_closed', passed=True))

    result = dict(image_sha256=hashlib.sha256(image).hexdigest(), dump_sha256=header['blob_sha256'],
                  observer=feature['observer'], cases=cases, passed=True,
                  limitations=['Request bytes beyond the type are synthetic; real requests come from the camera.',
                               'Concurrency with the menu task is not modelled.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'{len(cases)} cases passed')


if __name__ == '__main__':
    main()
