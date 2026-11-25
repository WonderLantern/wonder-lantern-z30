"""Execute built SD LUT menu dispatch, RAM session and patched pixel gate.

Heap, filesystem, cache and display endpoints are fixtures. This does not
establish camera boot, rendered menus, real SD or JPEG save behavior.
"""
import argparse
import binascii
import json
import struct
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'), str(Path(__file__).resolve().parent)]
from z30_extensions.host import lut
from z30_extensions.assembly import assemble, invoke
from z30_extensions.container import extract_eg
from z30_extensions.platform.z30_120 import sha256
from z30_extensions.features.lut import core as lut_core, sdload as lut_sdload, session as lut_session, ui as lut_ui
from z30_extensions.features.menu.layout import SUCCESS
from native_runtime import Context, RAM
from trace_lut_retouch import word, put, returning
from trace_lut_buffers import registers
from trace_lut_save import effect_to_main_encode
from build_lut_trial import build
from verify_lut_sdload import Fixture as LoaderFixture, table_text, payload
from verify_lut_core_native import image_descriptor, gradient_image, PARAMS, DESC_IN, DESC_OUT, Y_IN, C_IN, Y_OUT, C_OUT
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_R7, UC_ARM_REG_R8


class Fixture(Context):
    def __init__(self, stock, image, report):
        super().__init__(stock, image)
        self.f = {key: int(value, 16) for key, value in report['features']['menu_framework']['functions'].items()}
        self.feature = report['features']['lut_sd_menu']
        self.host_bytes = report['features']['menu_framework']['persistent_state_size']
        self.state = self.host = self.page = None
        self.next_heap, self.live = 0x53000000, {}
        self.heap_error = False
        self.events, self.labels, self.label_records, self.display = [], {}, {}, []
        self.content, self.offset, self.chunk, self.error, self.handle = table_text().encode(), 0, 19, None, 17
        self.file_live, self.in_pixels, self.validator_hits, self.cache_calls = False, False, 0, []
        self.reads = 0
        self.no_seek = False
        self.seed(0x1129, 2)
        endpoints = (lut_session.ALLOCATE, lut_session.RELEASE, lut_sdload.FS_OPEN,
                     lut_sdload.FS_READ, lut_sdload.FS_CLOSE, lut_sdload.FS_SEEK, 0x103382A2,
                     lut_core.CACHE_INVALIDATE, lut_core.CACHE_CLEAN, lut_core.DISPLACED_CALL,
                     self.f['text'] & ~1, self.f['text_entry'] & ~1, self.f['render'] & ~1, 0x1045F732)
        for address in set(endpoints):
            self.u.hook_add(UC_HOOK_CODE, self.service, begin=address, end=address)

    def string(self, address):
        return bytes(self.u.mem_read(address, 64)).split(b'\0')[0].decode('ascii')

    def service(self, u, address, size, unused):
        args = registers(u, 4)
        if address in (lut_session.ALLOCATE, lut_session.RELEASE,
                       lut_sdload.FS_OPEN, lut_sdload.FS_READ, lut_sdload.FS_CLOSE,
                       lut_sdload.FS_SEEK):
            assert not self.in_pixels, 'pixel worker reached heap/filesystem boundary'
        if address == lut_session.ALLOCATE:
            assert args[1] == 0
            if self.heap_error:
                returning(u); return
            pointer = self.next_heap
            self.next_heap += (args[0] + 63) & ~31
            self.map(pointer, args[0]); self.live[pointer] = args[0]
            u.mem_write(pointer, b'\xA5' * args[0])
            returning(u, pointer)
        elif address == lut_session.RELEASE:
            assert args[0] in self.live
            del self.live[args[0]]; returning(u)
        elif address in (lut_sdload.FS_OPEN, lut_sdload.FS_READ, lut_sdload.FS_CLOSE,
                       lut_sdload.FS_SEEK):
            if address == lut_sdload.FS_OPEN:
                assert not self.file_live
            else:
                assert self.file_live
            LoaderFixture.boundary(self, u, address, size, unused)
            if address == lut_sdload.FS_OPEN and self.error != 'open':
                self.file_live = True
            elif address == lut_sdload.FS_CLOSE and self.error != 'close':
                self.file_live = False
        elif address == 0x103382A2:
            path = word(u, args[0])
            assert self.string(path) == 'A:\\WL-SOUND.CFG'
            put(u, args[0] + 8, 0xFFFFFFFF); returning(u, SUCCESS + 1)
        elif address in (lut_core.CACHE_INVALIDATE, lut_core.CACHE_CLEAN):
            assert self.in_pixels and self.state is not None
            assert word(u, self.state + lut_session.LOCK) == 1
            self.cache_calls.append(address); returning(u)
        elif address == lut_core.DISPLACED_CALL:
            self.validator_hits += 1; returning(u)
        elif address in (self.f['text'] & ~1, self.f['text_entry'] & ~1):
            self.label_records[args[1]] = args[0]
            self.labels[args[1]] = self.string(word(u, args[0])); returning(u)
        else:
            self.display.append(dict(address=hex(address), arguments=args[:2])); returning(u)

    def open(self):
        assert self.call(self.f['bitrate_open']) == 0
        self.host = self.call(self.f['get'])
        assert self.host and self.live[self.host] == self.host_bytes
        self.state = self.call(self.f['bitrate_get'])
        assert self.state == self.host + self.feature['state_offset']
        self.page = self.call(0x104571FE, (2, self.feature['page_index']))
        assert self.page == self.host + self.feature['page_offset']
        assert word(self.u, self.state) == 0
        assert word(self.u, self.state + 4) == int(self.feature['fallback'], 16)
        assert word(self.u, self.state + lut_session.PENDING_FD) == 0xFFFFFFFF

    def action(self, row):
        self.u.mem_write(self.page + 22, struct.pack('<2H', row, 0))
        assert self.call(0x103CE804, (self.page, 0x256)) == 0x3BB
        result = word(self.u, self.state + 48)
        assert self.file_live == (word(self.u, self.state + lut_session.PENDING_FD) != 0xFFFFFFFF)
        return result

    def process(self, binary=None, expected_stock=False, busy=False):
        width, height, stride = 8, 3, 16
        yin, cin = gradient_image(width, height, stride, stride)
        self.u.mem_write(Y_IN, yin); self.u.mem_write(C_IN, cin)
        self.u.mem_write(Y_OUT, b'\xCD' * len(yin)); self.u.mem_write(C_OUT, b'\xCD' * len(cin))
        image_descriptor(self.u, DESC_IN, stride, stride, width, height, Y_IN, C_IN)
        image_descriptor(self.u, DESC_OUT, stride, stride, width, height, Y_OUT, C_OUT)
        self.u.mem_write(PARAMS, bytes(24)); put(self.u, PARAMS + 4, DESC_IN); put(self.u, PARAMS + 16, DESC_OUT)
        self.cache_calls.clear()
        validator_before = self.validator_hits
        self.in_pixels = True
        try:
            result = self.call(0x1025FB8E, (PARAMS,))
        finally:
            self.in_pixels = False
        if expected_stock or busy:
            assert result == 0 and not self.cache_calls
            assert self.validator_hits - validator_before == int(expected_stock)
            assert bytes(self.u.mem_read(Y_OUT, len(yin))) == b'\xCD' * len(yin)
        else:
            assert result == 1 and len(self.cache_calls) == 4
            assert self.validator_hits == validator_before
            want_y, want_c = lut.apply_frame_int(binary, yin, cin, width, height, stride, stride)
            want_y, want_c = bytearray(want_y), bytearray(want_c)
            for row in range(height):
                want_y[row * stride + width:(row + 1) * stride] = b'\xCD' * (stride - width)
                want_c[row * stride + width:(row + 1) * stride] = b'\xCD' * (stride - width)
            assert bytes(self.u.mem_read(Y_OUT, len(yin))) == want_y
            assert bytes(self.u.mem_read(C_OUT, len(cin))) == want_c


def verify(stock, image, report):
    # Run existing caller-to-main-encoder fixtures against the composed image's
    # actual guarded tables too. Effect pixels and codec remain substituted.
    success_chain = effect_to_main_encode(stock, image)
    f = Fixture(stock, image, report)
    rows = [dict(case='guarded_effect_to_main_encode', **case) for case in success_chain]
    f.process(expected_stock=True)
    assert not f.live and not f.events
    rows.append(dict(case='unopened_menu_uses_stock', no_allocation_or_io=True))
    f.heap_error = True
    assert f.call(f.f['bitrate_open']) == 0 and f.call(f.f['get']) == 0
    f.process(expected_stock=True)
    assert not f.live and not f.events
    rows.append(dict(case='host_allocation_failure_uses_stock', no_partial_session=True))
    f.heap_error = False
    f.open()
    assert len(f.live) == 1 and f.labels[lut_ui.CURRENT_SLOT] == 'Disabled'
    assert any(e['address'] == hex(0x1045F732) and e['arguments'] == [2, 960] for e in f.display)
    rows.append(dict(case='owned_menu_initializes_disabled_ram_session', host_bytes=f.host_bytes))
    title_record = f.label_records[lut_ui.TITLE_SLOT]
    hans = bytes(f.u.mem_read(word(f.u, title_record + 4), 48)).decode('utf-16-le').split('\0')[0]
    assert hans == '照片 LUT'
    rows.append(dict(case='built_menu_translation_record', simplified_chinese=hans))
    f.process(expected_stock=True)
    rows.append(dict(case='disabled_session_uses_stock', no_io=True))
    assert f.action(0) == 0
    active = word(f.u, f.state + 4)
    assert word(f.u, f.state) == lut_core.LUT_FLAG
    from z30_extensions.features.menu.layout import STATE_ANCHOR
    assert word(f.u, STATE_ANCHOR + 16) == f.host  # the gate's getter anchor
    assert len(f.live) == 2 and not f.file_live
    assert f.labels[lut_ui.CURRENT_SLOT] == 'SD LUT ready'
    binary = payload(table_text())
    assert bytes(f.u.mem_read(active, len(binary))) == binary
    f.process(binary)
    rows.append(dict(case='native_route_load_then_patched_gate', pixel_parity=True, retained_allocations=2))
    f.error = 'open'
    assert f.action(0) == 1 and word(f.u, f.state + 4) == active
    assert f.labels[lut_ui.CURRENT_SLOT] == 'File not found'
    f.process(binary)
    rows.append(dict(case='missing_file_keeps_previous_sd_lut', pixel_parity=True))
    f.error = None; f.content += b'0 0 0\n'
    assert f.action(0) == 3 and word(f.u, f.state + 4) == active
    assert len(f.live) == 2 and f.labels[lut_ui.CURRENT_SLOT] == 'Invalid LUT'
    rows.append(dict(case='invalid_reload_preserves_previous', retained_allocations=2))
    f.content = table_text().encode(); f.error = 'close'
    assert f.action(0) == 2 and f.file_live
    assert f.action(0) == 7 and f.file_live
    rows.append(dict(case='close_failure_retained_and_blocks_reload', pending_fd=word(f.u, f.state + 44)))
    f.error = None
    assert f.action(0) == 0 and not f.file_live and active not in f.live
    rows.append(dict(case='close_retry_and_reload_recover', retained_allocations=len(f.live)))
    put(f.u, f.state + lut_session.LOCK, 1)
    held = bytes(f.u.mem_read(f.state, 48))
    assert f.action(0) == 6 and bytes(f.u.mem_read(f.state, 48)) == held
    f.process(busy=True)
    assert word(f.u, f.state + lut_session.LOCK) == 1
    put(f.u, f.state + lut_session.LOCK, 0)
    rows.append(dict(case='busy_menu_and_worker_preserve_owner', no_table_replacement=True))
    assert f.action(1) == 0 and len(f.live) == 1
    fallback = bytes(f.u.mem_read(int(f.feature['fallback'], 16), f.feature['fallback_bytes']))
    f.process(fallback)
    assert f.labels[lut_ui.CURRENT_SLOT] == 'Embedded LUT'
    rows.append(dict(case='restore_embedded_then_process', pixel_parity=True))
    assert f.action(2) == 0 and word(f.u, f.state) == 0 and len(f.live) == 1
    assert word(f.u, STATE_ANCHOR + 16) == f.host  # anchor untouched by disable
    assert f.labels[lut_ui.CURRENT_SLOT] == 'Disabled'
    f.process(expected_stock=True)
    rows.append(dict(case='disable_release_and_stock_fallback', host_only=True))
    f.heap_error = True
    assert f.action(0) == 5 and word(f.u, f.state) == 0 and len(f.live) == 1
    assert f.labels[lut_ui.CURRENT_SLOT] == 'No memory'
    rows.append(dict(case='heap_failure_visible_in_menu', host_only=True))
    f.heap_error = False
    assert f.action(3) == 5 and len(f.live) == 1  # status row does not attempt a load
    rows.append(dict(case='status_row_has_no_load_side_effect', retained_status=5))
    if 'lut_failure' in report['features']:
        rows += failure_paths(f, report)
    return rows


def failure_paths(f, report):
    """Original post-submit caller + guarded phase tables + native completion."""
    u = f.u
    job, context, metadata, source, params, obj, service, work, control = [RAM + x for x in
        (0x60000, 0x65000, 0x66000, 0x6A000, 0x6A100, 0x6A200, 0x6B000, 0x6B400, 0x6C000)]
    rows = []
    # Guard the original continuation's loaded image pointer during unit windows.
    u.mem_write(job, bytes(0x4740)); u.mem_write(context, bytes(0x400))
    put(u, context + 0x30C, obj)
    for name, effect, accepted, result, asynchronous, expected in [
        ('valid_sync_result', 6, 1, 1, False, 0),
        ('async_not_yet_completed', 6, 1, 0, True, 0),
        ('other_effect_unchanged', 0, 1, 0, False, 0),
        ('submission_rejected', 6, 0, 1, False, 1),
    ]:
        put(u, job + 4, 0)
        u.mem_write(job + 0x4694, bytes([effect])); put(u, job + 0x4698, params)
        u.mem_write(params, bytes(24)); put(u, params, int(asynchronous)); put(u, params + 20, result)
        for register, value in ((UC_ARM_REG_R4, job + 0x4690), (UC_ARM_REG_R7, context),
                                 (UC_ARM_REG_R8, job), (UC_ARM_REG_R0, accepted)):
            u.reg_write(register, value)
        f.block(0x100A5552, 0x100A555E)
        assert word(u, job + 4) == expected
        assert word(u, job + 0x472C) == (0 if expected else accepted)
        rows.append(dict(case=name, job_error=expected, scope='post-submit window only'))
    assert f.action(0) == 0
    f.map(0xC23513A8, 0x80)
    trampoline = RAM + 0x70000
    u.mem_write(trampoline, assemble('push {r4, lr}; mov r0, r1;' + invoke(0x1025FB8E) +
                                    'movs r0, #1; pop {r4, pc};', trampoline, 128))
    events, forbidden = [], []
    forbidden_entries = {0x100A578C, 0x100A5BD2, 0x100A42C8, 0x100A3FDC, 0x100A4448,
                         0x100A2B92, 0x100A27E4, 0x100A60F2, 0x10096EE6, 0x10219468,
                         0x100A6A92, 0x102B6EC0, 0x102C8DC6}
    def boundary(uc, address, size, unused):
        if address in forbidden_entries:
            forbidden.append(address)
            raise AssertionError(f'failed job entered image/encode/write step {address:#x}')
        if address == 0x10142ACA:
            returning(uc, obj)
        elif address == 0x100CCAD4:
            returning(uc)
        elif address == 0x100CCA42:
            assert registers(uc, 2) == [6, params]
            # Replace queue/wait, but execute the actual patched pixel gate.
            # The trampoline returns submission acceptance=1 even on core failure.
            uc.reg_write(UC_ARM_REG_PC, trampoline | 1)
        elif address in (0x10199BB0, 0x10199BAA):
            returning(uc)
        elif address == 0x10199E04:
            events.append(registers(uc, 2)); returning(uc)
    hooks = [u.hook_add(UC_HOOK_CODE, boundary, begin=a, end=a) for a in
             forbidden_entries | {0x10142ACA, 0x100CCAD4, 0x100CCA42, 0x10199BB0, 0x10199BAA, 0x10199E04}]
    save_node = 0xC1888EB4
    phase4, phase5 = word(u, save_node + 0x14), 0xC1888EDC
    try:
        for name, fmt, busy in [('core_format_failure_stops_save', 2, False),
                                ('busy_core_stops_save', 1, True)]:
            for address, length in ((job, 0x4740), (context, 0x400), (metadata, 0x3200),
                                    (params, 24), (obj, 0x80), (service, 0x300), (work, 44), (control, 0x2CC)):
                u.mem_write(address, bytes(length))
            put(u, job + 8, service); put(u, job + 12, context)
            u.mem_write(job + 0x4690, b'\x01'); u.mem_write(job + 0x4694, b'\x06')
            put(u, job + 0x4698, params); put(u, context + 0x3A4, metadata)
            u.mem_write(metadata, b'\x01'); u.mem_write(metadata + 0x2658, b'\x05')
            image_descriptor(u, source, 16, 16, 8, 3, Y_IN, C_IN)
            image_descriptor(u, obj + 0x18, 16, 16, 8, 3, Y_OUT, C_OUT)
            u.mem_write(source, bytes([fmt])); put(u, params + 4, source)
            put(u, params + 8, RAM + 0x6A800); put(u, RAM + 0x6A800, 0)
            put(u, work, job); put(u, work + 4, phase4)
            put(u, f.state + lut_session.LOCK, int(busy))
            events.clear(); forbidden.clear()
            before_files = len(f.events)
            assert f.call(0x100CCDC4, (0xC189F2F8, work)) == 0
            assert word(u, params + 20) == 0 and word(u, job + 4) == 1
            assert word(u, job + 0x472C) == 0 and not forbidden
            assert word(u, context + 0x30C) == obj  # Still stock-owned, not silently freed.
            put(u, work + 4, phase5); put(u, work + 0x24, 0)
            assert f.call(0x100CCDC4, (0xC189F334, work)) == 0 and not forbidden
            # Final stock callback must report failure instead of acceptance.
            put(u, control, job); put(u, 0xC23513B4, 0x1034001D)
            f.live[job], f.live[control] = 0x4740, 0x2CC
            assert f.call(0x1021ECD4, (0x43080001, control)) == 0  # final free returns 0
            assert events == [[0xC295C6B4, 0x200]]
            assert len(f.events) == before_files and job not in f.live and control not in f.live
            put(u, f.state + lut_session.LOCK, 0)
            rows.append(dict(case=name, job_error=1, completion_event='0x200',
                effect_queue_replaced=True, actual_core_executed=True,
                subsequent_image_encode_write_skipped=True, stock_image_reference_retained=True,
                limitation='Upper stock owner release/rollback is not executed.'))
    finally:
        for hook in hooks:
            u.hook_del(hook)
    assert f.action(2) == 0 and len(f.live) == 1
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path, default=Path('build/research/lut-postprocess/sd-image.json'))
    p.add_argument('--image', type=Path, help='verify a saved SD trial BIN instead of rebuilding in memory')
    p.add_argument('--report', type=Path, help='saved build JSON (default: --image with .json suffix)')
    args = p.parse_args()
    if args.report and not args.image:
        p.error('--report requires --image')
    inputs = [args.source]
    if args.image:
        inputs += [args.image, args.report or args.image.with_suffix('.json')]
    if any(path.resolve() == args.output.resolve() for path in inputs):
        raise ValueError('Verification output must differ from every input')
    stock = extract_eg(args.source.read_bytes())
    if args.image:
        image = args.image.read_bytes()
        report = json.loads(inputs[-1].read_text(encoding='utf-8-sig'))
        assert report['preset'] == 'lut-sd-research'
        assert len(image) == report['size'] and sha256(image) == report['sha256']
        assert binascii.crc_hqx(image, 0) == 0
    else:
        image, report = build(stock, sd=True)
    rows = verify(stock, image, report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(image_sha256=report['sha256'], cases=rows,
        saved_image_verified=args.image is not None,
        feature=report['features']['lut_sd_menu'], camera_access=False,
        limitations=['Heap/file/cache/display endpoints replaced; no native task scheduling or camera boot.',
                     'Private route/getter and processing patches execute from the composed image.',
                     'Settings Extensions loading action, followed by original playback retouch; no LUT file browser.',
                     'Persistent host state is pinned until power-off; Disable releases LUT resources only.',
                     'No physical JPEG decode/encode/save, color chart or rendering verification.']), indent=2) + '\n', encoding='utf-8')
    print(f'PASS: {len(rows)} built SD image menu/session/processing cases; no camera access.')


if __name__ == '__main__':
    main()
