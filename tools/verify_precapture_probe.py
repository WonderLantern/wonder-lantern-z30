"""Build and execute a single-JPG save-boundary experiment, never a camera.

Stock extractors/order and patched call-site continuations execute. Allocation,
file services and selected downstream capture services are explicit fixtures.
"""
import argparse
import io
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.container import extract_eg
from z30_extensions.plan import Plan
from z30_extensions.platform.z30_120 import sha256
from z30_extensions.features.sound.shutter_sound import add
from z30_extensions.features.research.precapture_probe import MAGIC, MAX_FRAME, STATE_BYTES, FILLER, MAX_PADDING
from z30_extensions.features.research import precapture_capture as capture
from z30_extensions.assembly import assemble, constant, invoke
from z30_extensions.features.menu.layout import STATE_ANCHOR, MAGIC as SOUND_MAGIC, SUCCESS
from native_runtime import Context, RAM
from probe_precapture_writer import (WriterFixture, OBJ, BANKS, HEADER, PAYLOAD,
                                    PRIMARY, SECONDARY, FILE_BOUNDARY)
from probe_precapture_lifecycle import probe as probe_caller
from verify_precapture_core import make_jpeg
from read_precapture_probe import decode
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                              UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR,
                              UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R12,
                              UC_ARM_REG_SP, UC_ARM_REG_CPSR)

ARENA, SOUND_STATE = 0x53000000, 0x54000000


class Fixture(WriterFixture):
    def __init__(self, stock, image, report):
        self.context = Context(stock, image)
        self.u = self.context.u
        self.feature = report['features']['jpg_capture_probe']
        self.functions = {k: int(v, 16) for k, v in self.feature['functions'].items()}
        self.state = SOUND_STATE + self.feature['state_offset']
        total = self.feature['allocation_size'] + 44
        self.context.map(SOUND_STATE, total)
        self.u.mem_write(SOUND_STATE, bytes(total))
        self.word(SOUND_STATE+56, SOUND_MAGIC)
        self.word(STATE_ANCHOR+16, SOUND_STATE)
        self.word(self.state+4, MAGIC)
        self.context.map(ARENA, MAX_FRAME)
        self.context.map(FILLER, MAX_PADDING)
        self.u.mem_write(FILLER, bytes(MAX_PADDING))
        self.output = bytearray()
        self.calls = []
        self.u.hook_add(UC_HOOK_CODE, self.file_write, begin=FILE_BOUNDARY, end=FILE_BOUNDARY)
        self.allocations = 0
        self.allocation_failure = False
        self.short_write = False
        self.close_error = False
        self.event_create_result, self.task_create_result = 17, 21
        self.task_config_result, self.task_start_result, self.event_post_result = 0, 0, 0
        self.task_query_result = 0
        self.silent_photo, self.capture_result = 1, 0
        self.native_capture_phase = 0
        self.capture_sync_complete = False
        self.task_creations, self.event_posts, self.capture_requests, self.delays = [], [], [], []
        # v10 admission getters: static values plus N busy rounds of 0x1006316E.
        self.gates = {0x1b2e: 0, 0x1020BBC4: 0, 0x1020BC2E: 0, 0x1020BAEC: 0, 0x10065964: 0,
                      0x1006316E: 0, 0x10063166: 0, 0x101F5204: 0}
        self.busy_rounds = 0
        self.on_delay = None
        self.context.map(0xC1F87A70, 0x20)
        self.context.map(0xC23A5504, 0x400)
        self.u.mem_write(0xC23A5504, bytes(0x400))
        self.files = {}
        self.opened = {}
        self.fd = 10
        for address in (0x10211F4C, 0x103382A2, 0x102C8A6A, 0x1033832E, 0x101993DE,
                        0x10035F04, 0x10035FDC, 0x10035E6C, 0x10036440, 0x10036334,
                        0x1021994A, 0x10188776, 0x1020BB8A, 0x1020A630, 0x10036254,
                        0x1020BBC4, 0x1020BC2E, 0x1020BAEC, 0x10065964, 0x1006316E,
                        0x10063166, 0x101F5204):
            self.u.hook_add(UC_HOOK_CODE, self.service, begin=address, end=address)

    def ret(self, value):
        self.u.reg_write(UC_ARM_REG_R0, value)
        self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def service(self, u, address, size, data):
        r0, r1, r2, r3 = [u.reg_read(r) for r in
                          (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        if address == 0x10036254:
            raise AssertionError('Withdrawn task query must never execute')
        if address == 0x10035F04:
            return self.ret(self.event_create_result & 0xffffffff)
        if address == 0x10035FDC:
            self.task_creations.append(struct.unpack('<14I', u.mem_read(r0, 56)))
            return self.ret(self.task_create_result & 0xffffffff)
        if address == 0x10035E6C:
            assert r0 == (self.task_create_result & 0xffffffff) and r1 == 1
            return self.ret(self.task_config_result & 0xffffffff)
        if address == 0x10036440:
            assert r0 == (self.task_create_result & 0xffffffff) and r1 == self.state+capture.TASK
            return self.ret(self.task_start_result & 0xffffffff)
        if address == 0x10036334:
            self.event_posts.append((r0, r1))
            return self.ret(self.event_post_result & 0xffffffff)
        if address == 0x1021994A:
            self.delays.append(r0)
            if self.on_delay:
                self.on_delay(self, r0)
            return self.ret(0)
        if address == 0x10188776:
            if r0 == 0x1b2e:
                return self.ret(self.gates[0x1b2e])
            assert r0 == 0x57b
            return self.ret(self.silent_photo)
        if address == 0x101F5204:
            assert r0 == 0x11b
            return self.ret(self.gates[address])
        if address == 0x1006316E and self.busy_rounds:
            self.busy_rounds -= 1
            return self.ret(1)
        if address in self.gates:
            return self.ret(self.gates[address])
        if address == 0x1020BB8A:
            return self.ret(self.native_capture_phase)
        if address == 0x1020A630:
            request = bytes(u.mem_read(r0, 20))
            assert request == bytes(16)+struct.pack('<I', 1)
            assert r1 == self.functions['pre_complete']
            self.capture_requests.append((request.hex(), r1))
            if self.capture_result == 0:
                self.word(0xC23A5504+0x46*4, r1)
            if self.capture_sync_complete:
                self.word(0xC23A5504+0x46*4, 0)
                thunk = RAM+0x98000
                code = ('push {r4,lr};'+constant('r0', 0x04100046)+'movs r1,#0;'+
                        invoke(r1)+'movs r0,#0;pop {r4,pc};')
                u.mem_write(thunk, assemble(code, thunk, 96))
                u.reg_write(UC_ARM_REG_PC, thunk|1)
                return
            return self.ret(self.capture_result)
        if address == 0x10211F4C:
            assert r0 == MAX_FRAME and r1 == 0
            self.allocations += 1
            return self.ret(0 if self.allocation_failure else ARENA)
        if address == 0x101993DE:
            assert r0 == 0x188C
            return self.ret(0)
        if address == 0x103382A2:
            pointer = struct.unpack('<I', u.mem_read(r0, 4))[0]
            path = bytes(u.mem_read(pointer, 64)).split(b'\0')[0].decode()
            assert r1 == 0x301 and path in ('A:\\WL-PRE.JPG', 'A:\\WL-PRE.DBG')
            self.fd += 1
            self.files[path] = b''
            self.opened[self.fd] = path
            self.word(r0+8, self.fd)
            return self.ret(SUCCESS)
        if address == 0x102C8A6A:
            path = self.opened[r0]
            count = r2-1 if self.short_write else r2
            self.files[path] += bytes(u.mem_read(r1, count))
            self.word(r3, count)
            return self.ret(0)
        assert address == 0x1033832E
        if self.close_error:
            return self.ret(SUCCESS+1)
        fd = struct.unpack('<I', u.mem_read(r0+8, 4))[0]
        self.opened.pop(fd, None)
        self.word(r0+8, 0xFFFFFFFF)
        return self.ret(SUCCESS)

    def state_word(self, offset):
        return struct.unpack('<I', self.u.mem_read(self.state+offset, 4))[0]

    def call(self, name, *args):
        return self.context.call(self.functions[name], (self.state, *args), limit=50000000)

    def copy(self, jpeg, mode=1):
        pointers = self.configure([jpeg[:2], jpeg[2:-2], jpeg[-2:], b''])
        self.call('pre_copy', mode, OBJ)
        return pointers


def configure_mode0(f, header, body, tail, padding, filler=bytes(MAX_PADDING)):
    """Artificial mode-0 subset for differential stock-writer replay.

    It places a filler-backed primary segment before a direct tail section.
    Remaining real-camera descriptor nodes are not reconstructed or claimed.
    """
    assert len(filler) == MAX_PADDING
    f.configure([b'', b'', b'', b''])
    address = PAYLOAD
    pointers = []
    for part in (header, b'\0\0'+body, tail):
        f.context.map(address, max(1, len(part)))
        if part:
            f.u.mem_write(address, part)
        pointers.append(address)
        address += len(part)+64
    f.word(BANKS, PRIMARY)
    f.word(BANKS+12, SECONDARY)
    f.word(BANKS+16, 0)
    f.word(HEADER, pointers[0])
    f.word(HEADER+8, len(header))
    f.word(PRIMARY+8, pointers[1])
    f.word(PRIMARY+12, len(body)+2)
    f.word(OBJ+0x2C, len(body)+padding)
    f.word(HEADER+12, pointers[2])
    f.word(HEADER+20, len(tail))
    f.word(SECONDARY+8, pointers[2])
    f.word(SECONDARY+12, 2)  # Empty kind 4, including its prefix convention.
    f.word(OBJ+0x20, 0)
    total = len(header)+len(body)+padding+len(tail)
    f.u.mem_write(OBJ+24, struct.pack('<Q', total))
    f.u.mem_write(FILLER, filler)
    return total


def verify(stock, image, report, camera_log=None, camera_jpg=None):
    from PIL import Image
    cases = []
    f = Fixture(stock, image, report)
    jpeg = make_jpeg('#294a60', (5568, 3712))
    f.copy(jpeg)
    assert f.state_word(24) == 0 and f.allocations == 0
    cases.append(dict(name='default_off_does_not_allocate_or_copy', passed=True))
    f.call('pre_arm', 1)
    assert f.allocations == 1 and f.state_word(12) == 1
    pointers = f.copy(jpeg)
    assert f.state_word(24) == len(jpeg) and f.state_word(36) == 2
    assert bytes(f.u.mem_read(ARENA, len(jpeg))) == jpeg
    # The producer can be overwritten after the synchronous callback returns.
    for pointer, part in zip(pointers, (jpeg[:2], jpeg[2:-2], jpeg[-2:], b'')):
        if part:
            f.u.mem_write(pointer, bytes(len(part)))
    f.call('pre_export')
    assert f.state_word(36) == 3 and not f.opened
    assert f.files['A:\\WL-PRE.JPG'] == jpeg
    assert len(f.files['A:\\WL-PRE.DBG']) == 64+64*24+6*16+188
    diagnostic = decode(f.files['A:\\WL-PRE.DBG'])
    assert diagnostic['header']['copied_bytes'] == len(jpeg)
    assert sum(s['logical_length'] for s in diagnostic['segments']) == len(jpeg)
    with Image.open(io.BytesIO(f.files['A:\\WL-PRE.JPG'])) as decoded:
        decoded.load()
        assert decoded.size == (5568, 3712)
    cases.append(dict(name='stock_segment_extraction_owned_copy_delayed_file_export',
                      size=len(jpeg), sha256=sha256(jpeg), passed=True))
    for kind in ('bad_markers', 'unsupported_mode', 'oversize', 'inconsistent_total', 'padding'):
        f.call('pre_arm', 1)
        f.configure([jpeg[:2], jpeg[2:-2], jpeg[-2:], b''])
        mode = 1
        expected = 6
        if kind == 'bad_markers':
            f.u.mem_write(PAYLOAD, b'xx')
            expected = 7
        elif kind == 'unsupported_mode':
            mode = 4
        elif kind == 'oversize':
            f.word(OBJ+24, MAX_FRAME+1)
            expected = 5
        elif kind == 'inconsistent_total':
            f.word(OBJ+24, len(jpeg)+1)
        else:
            f.word(OBJ+0x24, len(jpeg)-3)  # Primary payload differs from declared segment.
        f.call('pre_copy', mode, OBJ)
        assert f.state_word(24) == 0 and f.state_word(36) == expected, (kind, f.state_word(36))
    assert f.allocations == 1
    cases.append(dict(name='invalid_input_refused_and_rearm_reuses_allocation', variants=5, passed=True))
    previous_export = f.files['A:\\WL-PRE.JPG']
    f.call('pre_export')
    assert f.state_word(36) == 10 and f.files['A:\\WL-PRE.JPG'] == previous_export
    assert decode(f.files['A:\\WL-PRE.DBG'])['header']['copied_bytes'] == 0
    cases.append(dict(name='no_copy_exports_log_without_claiming_a_new_jpg', passed=True))
    f.call('pre_arm', 1)
    f.copy(jpeg)
    f.short_write = True
    f.call('pre_export')
    assert f.state_word(36) == 8 and f.state_word(24) == len(jpeg) and not f.opened
    f.short_write = False
    f.close_error = True
    f.call('pre_export')
    assert f.state_word(36) == 8 and f.state_word(56) == 1 and f.opened
    f.close_error = False
    f.call('pre_export')
    assert f.state_word(36) == 3 and not f.opened
    assert f.files['A:\\WL-PRE.JPG'] == jpeg
    cases.append(dict(name='short_write_and_close_failure_preserve_copy_and_file_lease', passed=True))
    large = b'chunk-contract' * 161320
    f.context.map(PAYLOAD, len(large))
    f.u.mem_write(PAYLOAD, large)
    f.opened[999] = 'A:\\WL-PRE.JPG'
    f.files['A:\\WL-PRE.JPG'] = b''
    assert f.context.call(f.functions['pre_write'], (999, PAYLOAD, len(large))) == 1
    assert f.files['A:\\WL-PRE.JPG'] == large
    f.opened.pop(999)
    cases.append(dict(name='multi_chunk_file_write_preserves_order_and_length', bytes=len(large), passed=True))
    g = Fixture(stock, image, report)
    g.allocation_failure = True
    g.call('pre_arm', 1)
    assert g.state_word(36) == 4 and g.state_word(20) == 0
    cases.append(dict(name='allocation_failure_leaves_original_photo_path_available', passed=True))

    # Exercise stock mode-0 padding semantics, including the actual 376-byte
    # count found in the first camera log and a nonzero filler negative control.
    padded = Fixture(stock, image, report)
    for count in (1, 376, 512):
        for fill_byte in (0, 0xA5):
            padded.call('pre_arm', 1)
            filler = bytes([fill_byte])*MAX_PADDING
            body = b'A'*17
            header = jpeg[:2]+b'\xff\xfe'+struct.pack('>H', len(body)+count+2)
            tail = jpeg[2:]
            expected = header+body+filler[:count]+tail
            configure_mode0(padded, header, body, tail, count, filler)
            result, original_output, _ = padded.write(mode=0)
            assert result == 0 and original_output == expected
            padded.call('pre_copy', 0, OBJ)
            assert padded.state_word(36) == 2 and padded.state_word(24) == len(expected)
            copied = bytes(padded.u.mem_read(ARENA, len(expected)))
            assert copied == original_output
            with Image.open(io.BytesIO(copied)) as decoded:
                decoded.load()
                assert decoded.size == (5568, 3712)
    for count, body_length in ((513, 17), (376, -1)):
        padded.call('pre_arm', 1)
        configure_mode0(padded, jpeg[:2], b'A'*17, jpeg[2:], count)
        if body_length == -1:
            padded.word(OBJ+0x2C, 10)  # Extracted padding arithmetic wraps beyond bounds.
        padded.call('pre_copy', 0, OBJ)
        assert padded.state_word(36) == 6 and padded.state_word(24) == 0
    cases.append(dict(name='stock_mode0_padding_matches_native_writer',
                      padding_counts=[1, 376, 512], filler_patterns=[0, 165],
                      invalid_padding_refused=2, passed=True))

    if camera_log is not None:
        evidence = json.loads(camera_log.read_text())
        original = camera_jpg.read_bytes()
        first, primary = evidence['segments'][:2]
        assert evidence['header']['stock_mode'] == 0
        assert evidence['header']['declared_bytes'] == len(original)
        assert first['kind'] == 0 and first['padding'] == 0 and primary['kind'] == 3
        count = primary['padding']
        assert 0 < count <= MAX_PADDING
        start = first['logical_length']
        end = start+primary['logical_length']-count
        actual_filler = original[end:end+count]
        configure_mode0(padded, original[:start], original[start:end], original[end+count:],
                        count, actual_filler+bytes(MAX_PADDING-count))
        padded.call('pre_arm', 1)
        result, replay, _ = padded.write(mode=0)
        assert result == 0 and replay == original
        padded.call('pre_copy', 0, OBJ)
        assert padded.state_word(36) == 2 and padded.state_word(24) == len(original)
        assert bytes(padded.u.mem_read(ARENA, len(original))) == original
        padded.context.u.mem_write(PAYLOAD, bytes(start+primary['logical_length']+1024))
        padded.call('pre_export')
        assert padded.files['A:\\WL-PRE.JPG'] == original
        with Image.open(io.BytesIO(padded.files['A:\\WL-PRE.JPG'])) as decoded:
            decoded.load()
            dimensions = list(decoded.size)
        cases.append(dict(name='camera_sample_bytes_with_reconstructed_mode0_descriptor',
                          bytes=len(original), source_sha256=sha256(original), padding=count,
                          decoded_size=dimensions, original_object_reconstructed=False, passed=True))

    # Execute the actual patched original consumer, including both literal
    # hook windows and continuation to the unchanged stock save BL.
    for result in (0, 2, 3):
        probe_caller(stock, result, image=image)
    cases.append(dict(name='patched_selector_1_caller_preserves_all_three_return_branches', variants=3, passed=True))
    # Execute selector 0 with an armed callback and the original low-nibble
    # classifier. Only the save dispatcher and terminal services are replaced.
    for save_status in (0, 2, 3):
        h = Fixture(stock, image, report)
        h.call('pre_arm', 1)
        h.configure([jpeg[:2], jpeg[2:-2], jpeg[-2:], b''])
        owner, manager, info = RAM+0x60000, RAM+0x70000, RAM+0x80000
        entry = owner+0x80
        photo = entry+0x30
        h.u.mem_write(photo, bytes(h.u.mem_read(OBJ, 0x60)))
        h.word(photo, 1)  # Native classifier value; not a camera format name.
        h.word(owner+0xC, manager)
        h.word(manager+0x3A4, info)
        h.word(manager+0x320, 1)
        h.word(owner+0x7C, 1)
        h.word(manager+0x324, 0x2468)
        glob = h.context.stock.words(0x1009FD08, 1)[0]
        h.context.map(glob, 0x30)
        h.u.mem_write(glob, bytes(0x30))
        callback = RAM+0xF0000
        h.word(glob+0x14, callback|1)
        events = []
        def services(u, address, size, unused):
            args = tuple(u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            if address == 0x10461EC2:
                return h.ret(1)
            if address == 0x1033DF28:
                events.append(('save', args))
                return h.ret(save_status)
            if address == 0x10146DE6:
                events.append(('release', args[:2]))
            return h.ret(0)
        for address in (0x10461EC2, 0x1033DF28, 0x10146DE6, callback):
            h.u.hook_add(UC_HOOK_CODE, services, begin=address, end=address)
        assert h.context.call(0x1009FBB8, (owner,), limit=50000000) == 0
        assert events == [('save', (0, 1, photo)), ('release', (0x2468, 14))], events
        assert h.state_word(24) == len(jpeg) and h.state_word(36) == 2
        assert bytes(h.u.mem_read(ARENA, len(jpeg))) == jpeg
        assert struct.unpack('<I', h.u.mem_read(owner+0x7C, 4))[0] == 0
    cases.append(dict(name='armed_selector_0_copies_then_preserves_save_and_release', variants=3, passed=True))
    # Prove stock 14/15 pool forwarding instead of calling them opaque notices.
    pool_context = Context(stock, stock)
    pool_context.map(0xC2664734, 0x180)
    pool_context.u.mem_write(0xC2664734, bytes(0x180))
    observed = []
    def release(u, address, size, unused):
        observed.append((u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)))
        u.reg_write(UC_ARM_REG_R0, 1)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    pool_context.u.hook_add(UC_HOOK_CODE, release, begin=0x100CC588, end=0x100CC588)
    for pool in (14, 15):
        assert pool_context.call(0x10146DE6, (0x50010000, pool)) == 1
        assert observed[-1] == (0x50010000, pool)
    cases.append(dict(name='stock_save_resource_return_forwards_pool_14_and_15', passed=True))

    # Input observer must preserve routing, payload pointer and branch flags.
    route_ids = (0x0410004E, 0x0410004F, 0x04260012, 0x04260015, 0x04260017,
                 0x04260018, 0x04260019, 0x0426001A, 0x0426001B, 0)
    def routed(candidate, enabled):
        k = Fixture(stock, candidate, report)
        k.word(k.state+8, enabled)
        events = []
        def dispatch(u, address, size, unused):
            events.append(tuple(u.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)))
            k.ret(0)
        k.u.hook_add(UC_HOOK_CODE, dispatch, begin=0x10061AA6, end=0x10061AA6)
        # Avoid the one route that directly executes a downstream action by
        # using IDs which dispatch or fall through without a payload read.
        for ident in route_ids:
            k.context.call(0x100636F0, (ident, RAM+0x8000))
        return events, k
    baseline, _ = routed(stock, 0)
    actual, k = routed(image, 1)
    assert actual == baseline
    assert k.state_word(48) == len(route_ids)
    cases.append(dict(name='release_input_observer_preserves_stock_routes', variants=len(route_ids), passed=True))
    # Execute the original SW0 constructor at its real call site and stop
    # exactly after the displaced instructions. No key-input service fixture.
    transitions = ((0, 0x80000000), (0x80000000, 0xc0000000),
                   (0xc0000000, 0x80000000), (0x80000000, 0),
                   (0, 0x40000000), (0x40000000, 0), (0x1234, 0x5678),
                   (0xc0000000, 0xc0000000))
    # Unicorn's register enum need not be contiguous in future versions.
    from unicorn import arm_const as ac
    registers = [getattr(ac, 'UC_ARM_REG_R'+str(i)) for i in range(13)]
    registers += [UC_ARM_REG_LR, UC_ARM_REG_SP]
    original_keys, patched_keys = Fixture(stock, stock, report), Fixture(stock, image, report)
    for fixture in (original_keys, patched_keys):
        fixture.context.map(0xC1F87A70, 0x300)
    def physical(fixture, enabled, previous, current, flags):
        fixture.word(fixture.state+8, enabled)
        fixture.word(fixture.state+48, 0)
        fixture.u.mem_write(fixture.state+112, bytes(24))
        fixture.word(0xC1F87C3C, 0x13579bdf)
        for index, reg in enumerate(registers[:13]):
            fixture.u.reg_write(reg, 0x12340000+index)
        for reg, value in ((UC_ARM_REG_R0, 0xC1F87A74),
                           (UC_ARM_REG_R1, current), (UC_ARM_REG_R2, previous),
                           (UC_ARM_REG_R4, 0xC1F87BFC), (UC_ARM_REG_R5, 0xC1F87C38)):
            fixture.u.reg_write(reg, value)
        cpsr = fixture.u.reg_read(UC_ARM_REG_CPSR)
        fixture.u.reg_write(UC_ARM_REG_CPSR, (cpsr & ~0xf80f0000) | flags)
        fixture.context.block(0x101E3392, 0x101E339C)
        result = ([fixture.u.reg_read(reg) for reg in registers],
                  fixture.u.reg_read(UC_ARM_REG_CPSR) & 0xf80f0000,
                  bytes(fixture.u.mem_read(0xC1F87A74, 20)))
        return result, fixture
    for enabled in (0, 1):
        for previous, current in transitions:
            for flags in (0, 0xf80f0000):
                original, _ = physical(original_keys, 0, previous, current, flags)
                observed, fixture = physical(patched_keys, enabled, previous, current, flags)
                assert observed == original
                count = int(bool(enabled and ((previous ^ current) & 0xc0000000)))
                assert fixture.state_word(48) == count
                assert fixture.allocations == 0 and not fixture.files
                if count:
                    record = struct.unpack('<6I', fixture.u.mem_read(fixture.state+112, 24))
                    assert record[1:4] == (4, current & 0xc0000000, previous & 0xc0000000)
    cases.append(dict(name='physical_sw0_edges_preserve_stock_registers_flags_and_memory',
                      variants=len(transitions)*4, passed=True))
    cases.extend(verify_capture_worker(stock, image, report))
    return cases


def verify_capture_worker(stock, image, report):
    """Native task/config wrappers run; kernel, delay and capture are fixtures."""
    cases = []
    k = Fixture(stock, image, report)
    k.call('pre_arm', 1)
    assert k.state_word(capture.READY) == 1 and len(k.task_creations) == 1
    task = k.task_creations[0]
    assert task[0] == k.state+capture.TASK and task[2] == 0x1021986d
    assert task[3:5] == (32, 16384)
    assert k.state_word(capture.TASK+16) & 255 == 1
    assert k.state_word(capture.TASK+4) == k.functions['pre_entry']
    assert k.state_word(capture.TASK+44) == k.functions['pre_worker']
    assert k.state_word(capture.TASK+48) == 1
    assert bytes(k.u.mem_read(k.state+capture.CONFIG+16, 8)) == b'WL-PRE\0\0'
    assert not k.capture_requests and not k.event_posts
    k.context.call(k.functions['pre_wake'], (k.state, 0x80000000, 0))
    k.context.call(k.functions['pre_wake'], (k.state, 0x80000000, 0))
    assert k.event_posts == [(17, 1)] and not k.capture_requests
    assert k.state_word(capture.PHASE) == 1
    k.word(0xC1F87A74, 0x80000000)
    k.context.call(k.functions['pre_worker'], (1,))
    assert k.delays == [80] and len(k.capture_requests) == 1
    assert k.state_word(capture.PHASE) == 3 and k.state_word(36) == 14
    k.call('pre_arm', 1)
    assert len(k.task_creations) == 1 and k.state_word(36) == 9
    assert k.state_word(capture.PHASE) == 3
    callback = k.context.call(0x10061054, (0x46,))
    assert callback == k.functions['pre_complete']
    k.context.call(callback, (0x04100046, 0))
    assert k.state_word(capture.PHASE) == 4
    k.call('pre_arm', 1)
    assert len(k.task_creations) == 1 and k.state_word(capture.TICKET) == 1
    cases.append(dict(name='native_worker_creation_half_wake_one_request_and_callback', passed=True))

    # Disable, release, full-press and non-silent settings never start a request.
    for reason in ('disabled', 'released', 'full_pressed', 'silent_off', 'native_active',
                   'callback_47_occupied', 'callback_occupied'):
        k.call('pre_arm', 1)
        k.context.call(k.functions['pre_wake'], (k.state, 0x80000000, 0))
        k.word(0xC1F87A74, 0x80000000)
        k.silent_photo = int(reason != 'silent_off')
        k.native_capture_phase = 2 if reason == 'native_active' else 0
        k.word(0xC23A5504+0x46*4, 0x12345679 if reason == 'callback_occupied' else 0)
        k.word(0xC23A5504+0x47*4, 0x12345679 if reason == 'callback_47_occupied' else 0)
        if reason == 'disabled':
            k.call('pre_arm', 0)
        elif reason == 'released':
            k.word(0xC1F87A74, 0)
        elif reason == 'full_pressed':
            k.word(0xC1F87A74, 0xc0000000)
        before = len(k.capture_requests)
        k.context.call(k.functions['pre_worker'], (1,))
        assert len(k.capture_requests) == before and k.state_word(capture.PHASE) == 5
        if reason == 'disabled':
            assert k.state_word(36) == 0
        if reason == 'callback_occupied':
            assert k.context.call(0x10061064, (0x46,)) == 0x12345679
    cases.append(dict(name='worker_rechecks_input_silent_setting_and_callback_ownership', variants=7, passed=True))
    k.silent_photo = 1
    k.word(0xC23A5504+0x46*4, 0)
    k.word(0xC23A5504+0x47*4, 0)
    k.word(0xC1F87A74, 0x80000000)
    for result, sync in ((0x1d, False), (0, True)):
        k.call('pre_arm', 1)
        k.context.call(k.functions['pre_wake'], (k.state, 0x80000000, 0))
        k.capture_result, k.capture_sync_complete = result, sync
        k.context.call(k.functions['pre_worker'], (1,))
        assert k.state_word(capture.PHASE) == (4 if sync else 5)
        assert k.state_word(capture.RESULT) == result
    cases.append(dict(name='capture_rejection_and_early_callback_do_not_hang_phase', variants=2, passed=True))

    # v10 admission wait. Getters are fixtures; this proves the worker's
    # bounded polling, logging and single-submission rules only.
    def events(fixture):
        snapshot = bytes(fixture.u.mem_read(fixture.state, 64))+bytes(fixture.u.mem_read(fixture.state+112, 1632))
        return decode(snapshot)['events']
    def armed_worker(busy_rounds, gates=None):
        g = Fixture(stock, image, report)
        g.call('pre_arm', 1)
        g.context.call(g.functions['pre_wake'], (g.state, 0x80000000, 0))
        g.word(0xC1F87A74, 0x80000000)
        g.busy_rounds = busy_rounds
        g.gates.update(gates or {})
        return g
    g = armed_worker(3)
    g.context.call(g.functions['pre_worker'], (1,))
    rows = events(g)
    assert g.delays == [80]+[capture.GATE_DELAY_MS]*3 and len(g.capture_requests) == 1
    assert [r['arg1'] for r in rows if r['kind'] == 5 and r['arg0'] == 15] == [3]
    assert sum(r['kind'] == 9 for r in rows) == 2 and sum(r['kind'] == 10 for r in rows) == 2
    assert [r['arg0'] for r in rows if r['kind'] == 9] == [1, 0]
    assert g.state_word(capture.PHASE) == 3
    g = armed_worker(10**6)
    g.context.call(g.functions['pre_worker'], (1,))
    rows = events(g)
    assert g.delays == [80]+[capture.GATE_DELAY_MS]*(capture.GATE_POLLS-1)
    assert len(g.capture_requests) == 1
    assert [r['arg1'] for r in rows if r['kind'] == 5 and r['arg0'] == 14] == [capture.GATE_POLLS]
    g = armed_worker(10**6)
    g.on_delay = lambda fx, ms: fx.word(0xC1F87A74, 0) if ms == capture.GATE_DELAY_MS else None
    g.context.call(g.functions['pre_worker'], (1,))
    assert not g.capture_requests and g.state_word(capture.PHASE) == 5
    assert any(r['kind'] == 5 and r['arg0'] == 8 for r in events(g))
    g = armed_worker(0, {0x1020BBC4: 0x12, 0x1020BAEC: 1, 0x10065964: 2, 0x101F5204: 1})
    g.on_delay = lambda fx, ms: fx.gates.update({0x1020BBC4: 3, 0x1020BAEC: 0, 0x10065964: 0}) if ms == capture.GATE_DELAY_MS else None
    g.context.call(g.functions['pre_worker'], (1,))
    rows = events(g)
    packed = [r['arg0'] for r in rows if r['kind'] == 8]
    assert packed == [0x12 | 1 << 16 | 2 << 24, 3], [hex(v) for v in packed]
    assert [r['arg0'] for r in rows if r['kind'] == 10] == [1, 1]
    assert len(g.capture_requests) == 1
    cases.append(dict(name='v10_admission_wait_polls_getters_logs_and_submits_once', variants=4, passed=True))

    # One failed create attempt cannot spawn an event-wait loop or retry leak.
    for event_error, task_error, start_error in ((-5, 21, 0), (17, -5, -5), (17, 21, -5)):
        failed = Fixture(stock, image, report)
        failed.event_create_result = event_error
        failed.task_create_result = task_error
        failed.task_start_result = start_error
        failed.call('pre_arm', 1)
        assert failed.state_word(capture.READY) == 2 and failed.state_word(36) == 12
        count = len(failed.task_creations)
        failed.call('pre_arm', 1)
        assert len(failed.task_creations) == count and failed.state_word(36) == 12
        failed.context.call(failed.functions['pre_wake'], (failed.state, 0x80000000, 0))
        assert not failed.event_posts and not failed.capture_requests
    cases.append(dict(name='event_task_and_start_failure_are_bounded_until_shutdown', variants=3, passed=True))
    # Run one iteration of the ORIGINAL native event-task dispatcher. The OS
    # wait and capture entry remain explicit fixtures; the dispatcher and
    # worker execute their real instructions and pass the observed event mask.
    from native_runtime import STACK, STOP
    dispatched = Fixture(stock, image, report)
    dispatched.call('pre_arm', 1)
    dispatched.context.call(dispatched.functions['pre_wake'], (dispatched.state, 0x80000000, 0))
    dispatched.word(0xC1F87A74, 0x80000000)
    waits = []
    def wait_event(u, address, size, unused):
        waits.append((u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)))
        if len(waits) == 2:
            u.reg_write(UC_ARM_REG_PC, STOP)
        else:
            dispatched.word(u.reg_read(UC_ARM_REG_R2), 1)
            dispatched.ret(1)
    dispatched.u.hook_add(UC_HOOK_CODE, wait_event, begin=0x10199E30, end=0x10199E30)
    dispatched.u.reg_write(UC_ARM_REG_R0, dispatched.state+capture.TASK)
    dispatched.u.reg_write(UC_ARM_REG_SP, STACK+0xf0000)
    dispatched.u.reg_write(UC_ARM_REG_LR, STOP|1)
    dispatched.u.emu_start(0x10199C91, STOP, timeout=30000000, count=3000000)
    assert dispatched.u.reg_read(UC_ARM_REG_PC) == STOP and len(waits) == 2
    assert waits == [(dispatched.state+capture.TASK+32, 1)]*2
    assert len(dispatched.capture_requests) == 1 and dispatched.state_word(capture.PHASE) == 3
    assert dispatched.u.reg_read(UC_ARM_REG_SP) == STACK+0xf0000-8
    cases.append(dict(name='original_event_task_dispatches_one_half_press_request_and_waits_again', passed=True))

    # Execute the stock task trampoline as well. Exercise both a start-code
    # r0 and object r0; the shim must explicitly give the dispatcher its object.
    for entry_r0 in (1, dispatched.state+capture.TASK):
        started = Fixture(stock, image, report)
        started.call('pre_arm', 1)
        started.context.call(started.functions['pre_wake'], (started.state, 0x80000000, 0))
        started.word(0xC1F87A74, 0x80000000)
        entry_waits = []
        def entry_wait(u, address, size, unused):
            entry_waits.append((u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)))
            if len(entry_waits) == 2:
                u.reg_write(UC_ARM_REG_PC, STOP)
            else:
                started.word(u.reg_read(UC_ARM_REG_R2), 1)
                started.ret(1)
        started.u.hook_add(UC_HOOK_CODE, entry_wait, begin=0x10199E30, end=0x10199E30)
        started.u.reg_write(UC_ARM_REG_R0, entry_r0)
        started.u.reg_write(UC_ARM_REG_R1, started.state+capture.TASK)
        started.u.reg_write(UC_ARM_REG_SP, STACK+0xf0000)
        started.u.reg_write(UC_ARM_REG_LR, STOP|1)
        started.u.emu_start(0x1021986D, STOP, timeout=30000000, count=3000000)
        assert started.u.reg_read(UC_ARM_REG_PC) == STOP
        assert entry_waits == [(started.state+capture.TASK+32, 1)]*2
        assert len(started.capture_requests) == 1
        assert started.u.reg_read(UC_ARM_REG_SP) == STACK+0xf0000-24
        snapshot = bytes(started.u.mem_read(started.state, 64))+bytes(started.u.mem_read(started.state+112, 1632))
        entries = [r for r in decode(snapshot)['events'] if r['kind'] == 7]
        assert len(entries) == 1 and entries[0]['arg0'] == entry_r0
    cases.append(dict(name='stock_task_trampoline_and_entry_shim_dispatch_with_both_r0_conventions', variants=2, passed=True))

    for query_result in (0, -18):
        queried = Fixture(stock, image, report)
        queried.call('pre_arm', 1)
        queried.task_query_result = query_result
        queried.u.mem_write(queried.state+capture.TASK_INFO, bytes([0xa5])*64)
        queried.call('pre_export')
        snapshot = decode(queried.files['A:\\WL-PRE.DBG'])['native_worker_snapshot']
        assert snapshot['task_query_result'] == 0xfffffffe
        assert snapshot['entry_hit'] == 0
        expected = [0]*16
        assert snapshot['task_info_words'] == expected
        assert snapshot['core_field'] == 1 and snapshot['ready'] == 1
    cases.append(dict(name='export_never_calls_withdrawn_task_query_and_marks_unavailable', variants=2, passed=True))

    # Original count-selection helper consumes word +0x10. Available-buffer
    # count and scalar property reads are fixtures, not physical pool evidence.
    counts = Context(stock, stock)
    counts.map(0xC18A6FE8, 0x80)
    counts.u.mem_write(0xC18A6FE8, bytes(0x80))
    request = RAM+0x1000
    counts.u.mem_write(request, bytes(16)+struct.pack('<I', 1))
    available = [16]
    def count_services(u, address, size, unused):
        u.reg_write(UC_ARM_REG_R0, available[0] if address == 0x10209EE4 else 0)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    for address in (0x10209EE4, 0x10188776):
        counts.u.hook_add(UC_HOOK_CODE, count_services, begin=address, end=address)
    assert counts.call(0x10209F1A, (request,)) == 1
    available[0] = 0
    assert counts.call(0x10209F1A, (request,)) == 0
    cases.append(dict(name='original_count_selection_accepts_one_frame_and_refuses_zero_available', variants=2, passed=True))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    parser.add_argument('--output-dir', type=Path, default=Path('build/research/jpg-capture-probe-v1'))
    parser.add_argument('--camera-log', type=Path)
    parser.add_argument('--camera-jpg', type=Path)
    args = parser.parse_args()
    if bool(args.camera_log) != bool(args.camera_jpg):
        parser.error('Use camera-log and camera-jpg together')
    output = args.output_dir.resolve()
    outputs = [output / name for name in ('EG209020.BIN', 'EG209020.json', 'native.json')]
    if args.source.resolve() in outputs or any(p.exists() for p in outputs):
        parser.error('Choose a fresh output directory')
    stock = extract_eg(args.source.read_bytes())
    plan = Plan(stock)
    add(plan, precapture_probe=True)
    image, report = plan.finish()
    cases = verify(stock, image, report, args.camera_log, args.camera_jpg)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'EG209020.BIN').write_bytes(image)
    report.update(preset='jpg-capture-probe-v'+str(report['features']['jpg_capture_probe']['version']),
                  research=True, includes_full_features=False)
    (output / 'EG209020.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    (output / 'native.json').write_text(json.dumps(dict(cases=cases, passed=len(cases),
        candidate_sha256=sha256(image), camera_verified=False,
        substitutions=['General allocation', 'file system', 'selected downstream stock services',
                       'Kernel event/task create, start and post', 'Worker delay',
                       'Capture entry 0x1020A630, phase and silent-setting read in worker tests',
                       'v10 admission getters 0x1020BBC4/BC2E/BAEC, 0x10065964, 0x1006316E/66, 0x101F5204, attr 0x1B2E'],
        limitations=['No sensor, ISP, DMA, physical allocation, SD or thermal execution.',
                     'Physical SW0 hook/polarity have v3 user log evidence; the new worker does not yet.',
                     'One opt-in half-press request is implemented; native capture outcome requires camera testing.',
                     'No continuous acquisition, save suppression or electronic/mechanical switching.']),
        indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(passed=len(cases), output=str(output), sha256=sha256(image)), indent=2))


if __name__ == '__main__':
    main()
