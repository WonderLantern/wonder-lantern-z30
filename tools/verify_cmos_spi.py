"""Execute the CMOS serial-write observer, Arm and SPI log export offline.

The stock selector, sequence writer and serial writer 0x1019D868 run in
Unicorn over real drive data from a camera dump; only the SIO transfer
0x100C611E and GPIO 0x101D42F0 are stubs. At 0x1019D870 (after the displaced
prologue) the patched image must see exactly the stock image's buffer/length
for every write, and the log must hold those packets only while armed.

Usage:
  python tools/verify_cmos_spi.py --dump-dir build/research/cmos-ab --output build/research/cmos-spi-verify.json
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
from z30_extensions.features import cmos_dump as cd, cmos_spi as cs
from z30_extensions.features.sound_layout import STATE_ANCHOR, MAGIC as SOUND_MAGIC
from native_runtime import Context
from build_cmos_dump import build
from decode_cmos_modes import SELECTOR, COMMANDS, TYPES
from verify_cmos_dump import Fixture as WriterFixture, BOUNCE, STATE
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_PC, UC_ARM_REG_LR

SIO, GPIO, AFTER_PROLOGUE = 0x100C611E, 0x101D42F0, cs.WRITER + 8
REQUEST = 0xC18E4EBC
SOUND_STATE, LOG = 0x54000000, 0x56000000


class Machine:
    def __init__(self, stock, image, blob, context, pointer):
        self.cx = Context(stock, image)
        self.u = self.cx.u
        self.cx.map(pointer, len(blob)); self.u.mem_write(pointer, blob)
        self.u.mem_write(cd.CTX, context)
        self.cx.map(REQUEST & ~0xFFF, 0x1000)
        self.seen = []
        self.u.hook_add(UC_HOOK_MEM_UNMAPPED, self.unmapped)
        self.u.hook_add(UC_HOOK_CODE, self.observe, begin=AFTER_PROLOGUE, end=AFTER_PROLOGUE)
        for stub in (SIO, GPIO):
            self.u.hook_add(UC_HOOK_CODE, self.stub, begin=stub, end=stub)

    def unmapped(self, u, access, address, size, value, data):
        if 0x80000000 <= address < 0xE0000000:
            self.cx.map(address & ~0xFFF, 0x1000); return True
        return False

    def observe(self, u, *unused):
        buffer, length = u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)
        self.seen.append(bytes(u.mem_read(buffer, length)))

    def stub(self, u, *unused):
        u.reg_write(UC_ARM_REG_R0, 0); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def select(self, command, drive_type):
        request = bytearray(0x40); request[0] = drive_type
        self.u.mem_write(REQUEST, bytes(request))
        self.seen = []
        status = self.cx.call(SELECTOR, (command, REQUEST), limit=4_000_000)
        return status, list(self.seen)

    def word(self, address):
        return struct.unpack('<I', self.u.mem_read(address, 4))[0]

    def put(self, address, value):
        self.u.mem_write(address, struct.pack('<I', value & 0xFFFFFFFF))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--dump-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    stock = extract_eg(args.source.read_bytes())
    image, report = build(stock, spi=True)
    feature = report['features']['cmos_spi']
    header = json.loads((args.dump_dir / 'header.json').read_text(encoding='utf-8'))
    assert header['sum_matches']
    blob = (args.dump_dir / 'drive-data.bin').read_bytes()
    context = (args.dump_dir / 'context.bin').read_bytes()
    pointer = int(header['pointer'], 16)
    reference = Machine(stock, stock, blob, context, pointer)
    patched = Machine(stock, image, blob, context, pointer)
    state = SOUND_STATE + feature['state_offset']
    cases = []

    def run_all(machine):
        return [machine.select(command, drive_type) for command in COMMANDS for drive_type in TYPES]

    expected = run_all(reference)
    packets = [p for _, seen in expected for p in seen]
    assert packets, 'stock run produced no serial writes'

    # 1. No page state: identical serial writes.
    assert run_all(patched) == expected
    cases.append(dict(name='no_state_identical_serial_writes', passed=True, writes=len(packets)))

    # 2. State present, not armed: identical, nothing logged.
    patched.cx.map(SOUND_STATE, feature['state_offset'] + cs.STATE_BYTES + 64)
    patched.u.mem_write(SOUND_STATE, bytes(feature['state_offset'] + cs.STATE_BYTES + 64))
    patched.put(SOUND_STATE + 56, SOUND_MAGIC); patched.put(STATE_ANCHOR + 16, SOUND_STATE)
    patched.cx.map(LOG, cs.LOG_BYTES); patched.u.mem_write(LOG, bytes(cs.LOG_BYTES))
    patched.put(state + cs.SPI_BUFFER, LOG)
    assert run_all(patched) == expected and patched.word(state + cs.SPI_INDEX) == 0
    cases.append(dict(name='not_armed_identical_and_silent', passed=True))

    # 3. Armed, segment 3: identical output; the log equals the de-duplication model.
    def dedupe(stream, table):
        kept = []
        for packet in stream:
            register = packet[1] << 8 | packet[2] if len(packet) >= 3 else 0
            key = struct.pack('<I', register | len(packet) << 16) + packet[:24].ljust(24, bytes(1))
            slot = (register ^ register >> 6) & (cs.DEDUPE_SLOTS - 1)
            history = table.setdefault(slot, [None, None])
            if key in history:
                continue
            table[slot] = [key, history[0]]
            kept.append(packet)
        return kept
    table = {}
    patched.put(state + cs.SPI_SEGMENT, 3); patched.put(state + cs.SPI_ARMED, 1)
    assert run_all(patched) == expected
    logged = dedupe(packets, table)
    used = patched.word(state + cs.SPI_INDEX)
    assert used == len(logged) and 0 < used < len(packets), (used, len(logged), len(packets))
    log = bytes(patched.u.mem_read(LOG, cs.LOG_BYTES))
    for index, packet in enumerate(logged):
        number, meta = struct.unpack_from('<2I', log, index * 32)
        assert number == index + 1 and meta == 3 | len(packet) << 16, index
        assert log[index * 32 + 8:index * 32 + 8 + min(len(packet), 24)] == packet[:24], index
    cases.append(dict(name='armed_logs_only_new_values_per_register', passed=True,
                      writes=len(packets), logged=used))

    # 4. Steady repeats are silent; a changed value is logged again; the ring index keeps counting.
    for _ in range(3):
        assert run_all(patched) == expected
    again = dedupe(packets * 3, table)
    assert patched.word(state + cs.SPI_INDEX) == used + len(again)
    total = patched.word(state + cs.SPI_INDEX)
    from read_cmos_spi_log import decode_ring
    ring = decode_ring(bytes(patched.u.mem_read(LOG, cs.LOG_BYTES)), total)
    stream = logged + again
    assert [e['index'] for e in ring] == list(range(max(1, total - cs.CAPACITY + 1), total + 1))
    assert all(e['packet'] == stream[e['index'] - 1][:24] for e in ring)
    cases.append(dict(name='repeated_rounds_add_only_model_entries', passed=True, repeats_logged=len(again)))

    # 4b. Real steady 4K30 live-view stream from a USB capture: almost everything is repeated.
    capture = Path('build/research/live-1.json')
    if capture.exists():
        live = json.loads(capture.read_text(encoding='utf-8'))['entries'][5600:7600]
        real = [bytes.fromhex(e['packet']) for e in live]
        before = patched.word(state + cs.SPI_INDEX)
        SCRATCH = 0x57000000
        patched.cx.map(SCRATCH, 0x1000)
        for packet in real:
            patched.u.mem_write(SCRATCH, packet)
            patched.cx.call(cs.WRITER, (SCRATCH, len(packet)), limit=200_000)
        kept = dedupe(real, table)
        assert patched.word(state + cs.SPI_INDEX) - before == len(kept)
        assert len(kept) <= 40, len(kept)
        cases.append(dict(name='real_4k30_stream_mostly_suppressed', passed=True, packets=len(real), logged=len(kept)))

    # 5. Arm: allocate once, zero, bump segment, arm; second Arm keeps buffer.
    writer = WriterFixture(stock, image, dict(functions=dict(cmos_export=feature['functions']['spi_arm'])))
    writer.reset(); writer.u.mem_write(STATE, bytes(cs.STATE_BYTES)); writer.word(STATE + cd.FD_OFF, -1)
    writer.context.map(BOUNCE, cs.LOG_BYTES); writer.u.mem_write(BOUNCE, b'\xAA' * cs.LOG_BYTES)
    writer.u.mem_write(STATE + cs.DEDUPE, b'Z' * (cs.DEDUPE_SLOTS * cs.DEDUPE_SLOT_BYTES))
    status = writer.run()
    read = lambda offset: struct.unpack('<I', writer.u.mem_read(STATE + offset, 4))[0]
    assert bytes(writer.u.mem_read(STATE + cs.DEDUPE, cs.DEDUPE_SLOTS * cs.DEDUPE_SLOT_BYTES)) == bytes(cs.DEDUPE_SLOTS * cs.DEDUPE_SLOT_BYTES)
    assert status == cs.ARMED_STATUS and writer.calls[0] == ('alloc', cs.LOG_BYTES, 0)
    assert (read(cs.SPI_BUFFER), read(cs.SPI_ARMED), read(cs.SPI_SEGMENT), read(cs.SPI_INDEX)) == (BOUNCE, 1, 1, 0)
    assert bytes(writer.u.mem_read(BOUNCE, cs.LOG_BYTES)) == bytes(cs.LOG_BYTES)
    writer.calls.clear(); writer.u.mem_write(STATE + cd.STATUS_OFF, bytes(4))
    assert writer.run() == cs.ARMED_STATUS and not writer.calls and read(cs.SPI_SEGMENT) == 2
    cases.append(dict(name='arm_allocates_once_zeroes_log_and_history_counts_segments', passed=True))

    # 6. Export: header + whole log, cleaned in CHUNK pieces; refuses when never armed.
    exporter = WriterFixture(stock, image, dict(functions=dict(cmos_export=feature['functions']['spi_export'])))
    exporter.reset(); exporter.u.mem_write(STATE, bytes(cs.STATE_BYTES)); exporter.word(STATE + cd.FD_OFF, -1)
    assert exporter.run() == cs.NOT_ARMED_STATUS and not exporter.calls
    exporter.reset(); exporter.u.mem_write(STATE, bytes(cs.STATE_BYTES)); exporter.word(STATE + cd.FD_OFF, -1)
    exporter.context.map(LOG, cs.LOG_BYTES); exporter.u.mem_write(LOG, log)
    for offset, value in ((cs.SPI_BUFFER, LOG), (cs.SPI_ARMED, 1), (cs.SPI_SEGMENT, 3), (cs.SPI_INDEX, cs.CAPACITY)):
        exporter.word(STATE + offset, value)
    assert exporter.run() == cs.SAVED_STATUS
    head = struct.pack('<6I', cs.SPI_MAGIC, cs.SPI_VERSION, cs.CAPACITY, cs.CAPACITY, cs.ENTRY_BYTES, 3) + bytes(8)
    assert bytes(exporter.output) == head + log and exporter.paths == [cs.SPI_PATH]
    cleans = [call for call in exporter.calls if call[0] == 'clean']
    assert cleans == [('clean', BOUNCE, cs.SPI_HEADER)] + [('clean', BOUNCE, cd.CHUNK)] * (cs.LOG_BYTES // cd.CHUNK)
    cases.append(dict(name='export_layout_cache_clean_and_not_armed_refusal', passed=True, size=len(exporter.output)))

    result = dict(image_sha256=hashlib.sha256(image).hexdigest(), dump_sha256=header['blob_sha256'],
                  observer=feature['spi_observer'], cases=cases, passed=True,
                  limitations=['SIO transfer and GPIO are stubs; real bus timing is not modelled.',
                               'Request fields other than the type are zero.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f'{len(cases)} cases passed')


if __name__ == '__main__':
    main()
