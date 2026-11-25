"""Bounded native heap-contract and LUT session ownership/lock verification."""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from z30_extensions.host import lut
from z30_extensions.container import extract_eg
from z30_extensions.features.lut import core as lut_core, session
from z30_extensions.platform.z30_120 import StockImage, sha256
from native_runtime import Context, RAM
from trace_lut_retouch import put, word, returning
from trace_lut_buffers import registers
from verify_lut_sdload import Fixture, CODE, ARENA, payload, table_text
from verify_lut_core_native import image_descriptor, gradient_image, PARAMS, DESC_IN, DESC_OUT, Y_IN, C_IN, Y_OUT, C_OUT
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0


def heap_contract(data):
    c = Context(data, data)
    c.map(0xC1F8A208, 40)
    u = c.u
    for index, base in enumerate((RAM, 0x52000000)):
        u.mem_write(0xC1F8A208 + index * 20, struct.pack('<5I', 0x901 + index, 0x1000000, base, 0, 0))
    events = []
    error, allocated = 0, RAM + 0x70000
    def privileged(uc, address, size, unused):
        args = registers(uc, 4)
        events.append(dict(svc=hex(address), arguments=args))
        if address == 0x10036160:
            put(uc, args[2], allocated)
        uc.reg_write(UC_ARM_REG_R0, error)
        uc.reg_write(UC_ARM_REG_PC, address + 4)
    for address in (0x10036160, 0x10036320):
        u.hook_add(UC_HOOK_CODE, privileged, begin=address, end=address)
    rows = []
    for pool in (0, 1):
        for error in (0, 5):
            events.clear()
            size = 8 * 1024 * 1024
            got = c.call(session.ALLOCATE, (size, pool))
            assert got == (allocated if not error else 0)
            assert len(events) == 1 and events[0]['arguments'][:2] == [0x901 + pool, size]
            assert events[0]['arguments'][3] == 0
            rows.append(dict(case='native_allocate', pool=pool, substituted_svc_status=error, result=got, calls=list(events)))
    for address, pool in ((RAM + 0x70000, 0), (0x52001000, 1)):
        for error in (0, 5):
            events.clear()
            got = c.call(session.RELEASE, (address,))
            assert got == error and len(events) == 1
            assert events[0]['arguments'][:2] == [0x901 + pool, address]
            rows.append(dict(case='native_release', pool=pool, substituted_svc_status=error, result=got, calls=list(events)))
    events.clear()
    assert c.call(session.RELEASE, (0x70000000,)) == 0x70000000 and not events
    rows.append(dict(case='release_outside_regions', result='unchanged pointer; no SVC'))
    return rows


class SessionFixture(Fixture):
    def __init__(self, data):
        super().__init__(data)
        self.u.mem_write(CODE, session.build_manager(CODE, read_capacity=4096, max_read_capacity=4096))
        self.fallback, self.path = ARENA + 0x100, ARENA + 0x300
        self.u.mem_write(self.fallback, self.binary)
        self.u.mem_write(self.path, b'A:\\WL-LUT.CUBE\0')
        self.u.mem_write(ARENA, session.build_session(self.fallback, len(self.binary)))
        self.live, self.heap_events, self.fail_free = {}, [], set()
        self.file_live = False
        self.heap_next, self.alloc_count, self.fail_alloc = 0x53000000, 0, 0
        for address in (session.ALLOCATE, session.RELEASE):
            self.u.hook_add(UC_HOOK_CODE, self.heap, begin=address, end=address)

    def fresh(self):
        # Keep one engine alive throughout the ownership scenarios.
        assert not self.live and not self.file_live
        self.heap_next = 0x53000000
        self.u.mem_write(ARENA, session.build_session(self.fallback, len(self.binary)))

    def boundary(self, u, address, size, unused):
        if address == session.lut_sdload.FS_OPEN:
            assert not self.file_live, 'new open while a prior handle is still owned'
        else:
            assert self.file_live
        super().boundary(u, address, size, unused)
        if address == session.lut_sdload.FS_OPEN and self.error != 'open':
            self.file_live = True
        elif address == session.lut_sdload.FS_CLOSE and self.error != 'close':
            self.file_live = False

    def heap(self, u, address, size, unused):
        pointer = registers(u, 1)[0]
        if address == session.ALLOCATE:
            self.alloc_count += 1
            assert registers(u, 2)[1] == 0
            kind = 'candidate' if pointer == session.lut_sdload.SD_SLOT_SIZE else 'input'
            self.heap_events.append(dict(kind='allocate', bytes=pointer, resource=kind))
            if self.alloc_count == self.fail_alloc:
                returning(u)
                return
            result = self.heap_next
            self.heap_next += (pointer + 8191) & ~4095
            self.c.map(result, pointer)
            self.live[result] = (pointer, kind)
            returning(u, result)
        else:
            assert pointer in self.live, ('double free or unowned pointer', hex(pointer))
            kind = self.live[pointer][1]
            failed = kind in self.fail_free
            self.heap_events.append(dict(kind='release', pointer=hex(pointer), resource=kind, failed=failed))
            if not failed:
                del self.live[pointer]
            returning(u, 5 if failed else 0)

    def call(self, content=b'', error=None, reset=False, fail_alloc=0, fail_free=()):
        self.content, self.offset, self.error, self.chunk = content, 0, error, 17
        self.alloc_count, self.fail_alloc, self.fail_free = 0, fail_alloc, set(fail_free)
        self.heap_events.clear(); self.events.clear()
        result = self.c.call(CODE + (4 if reset else 0), (ARENA, self.path), limit=10_000_000)
        assert word(self.u, ARENA + session.LOCK) == 0
        assert word(self.u, ARENA + 8) == result
        owned = {word(self.u, ARENA + offset) for offset in (session.OWNED, session.PENDING_INPUT, session.RETIRED)} - {0}
        assert set(self.live) == owned, ('untracked allocation', self.live, owned)
        assert self.file_live == (word(self.u, ARENA + session.PENDING_FD) != 0xFFFFFFFF)
        return result

    def row(self, name, result):
        return dict(case=name, result=result, committed=word(self.u, ARENA + session.COMMITTED),
                    live=[hex(x) for x in self.live], pending_fd=word(self.u, ARENA + session.PENDING_FD),
                    heap_calls=list(self.heap_events), file_calls=list(self.events))


def ownership(data):
    rows = []
    text = table_text().encode()
    f = SessionFixture(data)
    for name, kwargs, result in [
        ('first_allocate_failure', dict(fail_alloc=1), 5),
        ('second_allocate_failure', dict(fail_alloc=2), 5),
        ('missing_file', dict(error='open'), 1),
        ('read_failure', dict(error='read'), 2),
        ('close_failure', dict(error='close'), 2),
        ('bad_text', dict(content=text + b'0 0 0\n'), 3),
    ]:
        f.fresh()
        content = kwargs.pop('content', text)
        assert f.call(content, **kwargs) == result and not f.live, name
        assert word(f.u, ARENA + 4) == f.fallback
        rows.append(f.row(name, result))
        if f.file_live:
            assert f.call(text, error='close') == 7 and not any(e['kind'] == 'open' for e in f.events)
            rows.append(f.row('pending_close_blocks_reopen', 7))
            assert f.call(reset=True) == 0 and not f.file_live
            rows.append(f.row('pending_close_retry', 0))
    f.fresh()
    assert f.call(text) == 0
    active = word(f.u, ARENA + 4)
    assert active == word(f.u, ARENA + session.OWNED) and len(f.live) == 1
    assert bytes(f.u.mem_read(active, word(f.u, ARENA + 12))) == payload(text.decode())
    rows.append(f.row('load_keeps_only_payload', 0))
    assert f.call(text + b'0 0 0\n') == 3 and word(f.u, ARENA + 4) == active
    rows.append(f.row('failed_reload_keeps_active', 3))
    assert f.call(lut.identity_cube(2).encode()) == 0 and word(f.u, ARENA + 4) != active
    assert active not in f.live and len(f.live) == 1
    rows.append(f.row('successful_reload_releases_old', 0))
    assert f.call(reset=True) == 0 and not f.live and word(f.u, ARENA + 4) == f.fallback
    rows.append(f.row('reset_to_embedded', 0))
    for case, initial in [('failed_load_two_failed_frees', text + b'0 0 0\n'), ('committed_load_failed_input_free', text)]:
        f.fresh()
        kinds = ('input', 'candidate') if case.startswith('failed') else ('input',)
        status = 7 if case.startswith('failed') else 8
        assert f.call(initial, fail_free=kinds) == status
        assert bool(word(f.u, ARENA + session.COMMITTED)) == (status == 8)
        rows.append(f.row(case, status))
        assert f.call(text, fail_free=kinds) == 7
        assert not any(e['kind'] == 'allocate' for e in f.heap_events)
        rows.append(f.row(case + '_retry_blocked', 7))
        assert f.call(text) == 0 and len(f.live) == 1
        rows.append(f.row(case + '_retry_released', 0))
        assert f.call(reset=True) == 0 and not f.live
    f.fresh()
    assert f.call(text) == 0
    old = word(f.u, ARENA + 4)
    assert f.call(text, fail_free=('input', 'candidate')) == 8
    assert word(f.u, ARENA + session.RETIRED) == old and len(f.live) == 3
    rows.append(f.row('commit_keeps_both_pending_frees', 8))
    assert f.call(reset=True) == 0 and not f.live
    rows.append(f.row('reset_drains_all_pending', 0))
    assert f.call(text) == 0
    assert f.call(reset=True, fail_free=('candidate',)) == 8
    assert word(f.u, ARENA + 4) == f.fallback and len(f.live) == 1
    rows.append(f.row('reset_pending_release', 8))
    assert f.call(reset=True) == 0 and not f.live
    rows.append(f.row('reset_pending_retry', 0))
    # A held lock blocks loads/reset without changing owner-visible state.
    for entry in (CODE, CODE + 4):
        put(f.u, ARENA + session.LOCK, 1)
        before = bytes(f.u.mem_read(ARENA, session.SESSION_BYTES))
        f.events.clear(); f.heap_events.clear()
        assert f.c.call(entry, (ARENA, f.path)) == 6
        assert bytes(f.u.mem_read(ARENA, session.SESSION_BYTES)) == before
        assert not f.events and not f.heap_events
        rows.append(dict(case='busy_load' if entry == CODE else 'busy_reset', result=6, owner_unchanged=True))
        put(f.u, ARENA + session.LOCK, 0)
    f.fresh()
    f.u.mem_write(CODE, session.build_manager(CODE, read_capacity=128, max_read_capacity=4096))
    f.u.ctl_remove_cache(CODE, CODE + 0x4000)
    assert f.call(text) == 0
    sizes = [e['bytes'] for e in f.heap_events if e['kind'] == 'allocate' and e['resource'] == 'input']
    assert sizes[0] == 128 and len(sizes) > 1 and all(b == a * 2 for a, b in zip(sizes, sizes[1:]))
    assert len(f.live) == 1
    rows.append(f.row('input_grows_after_closed_overflow', 0))
    assert f.call(reset=True) == 0
    assert f.call(text, fail_alloc=3) == 5 and not f.live
    rows.append(f.row('growth_allocate_failure', 5))
    assert f.call(text, fail_free=('input',)) == 7
    assert word(f.u, ARENA + 4) == f.fallback and len(f.live) == 1
    rows.append(f.row('growth_release_failure_retained', 7))
    assert f.call(reset=True) == 0 and not f.live
    assert f.call(b'x' * 4097) == 4 and not f.live
    rows.append(f.row('growth_reaches_maximum', 4))
    assert f.call(text, error='close') == 2 and not f.live and f.file_live
    assert not any(e['kind'] == 'allocate' and e['bytes'] > 128 for e in f.heap_events if e['resource'] == 'input')
    rows.append(f.row('overflow_close_failure_blocks_growth', 2))
    assert f.call(reset=True) == 0 and not f.file_live
    return rows, f


def processing_lock(f):
    rows, cache = [], []
    assert f.call(table_text().encode()) == 0
    u, core = f.u, RAM + 0x10000
    u.mem_write(core, lut_core.build_lut_core(ARENA, core, active_payload=True, session_lock=True))
    def record(uc, address, size, unused):
        assert word(uc, ARENA + session.LOCK) == 1
        cache.append(address); returning(uc)
    for address in (lut_core.CACHE_INVALIDATE, lut_core.CACHE_CLEAN):
        u.hook_add(UC_HOOK_CODE, record, begin=address, end=address)
    width, height, stride = 8, 3, 16
    yin, cin = gradient_image(width, height, stride, stride)
    u.mem_write(Y_IN, yin); u.mem_write(C_IN, cin)
    u.mem_write(Y_OUT, bytes(len(yin))); u.mem_write(C_OUT, bytes(len(cin)))
    image_descriptor(u, DESC_IN, stride, stride, width, height, Y_IN, C_IN)
    image_descriptor(u, DESC_OUT, stride, stride, width, height, Y_OUT, C_OUT)
    u.mem_write(PARAMS, bytes(24)); put(u, PARAMS + 4, DESC_IN); put(u, PARAMS + 16, DESC_OUT)
    assert f.c.call(core, (PARAMS,)) == 1 and len(cache) == 4
    assert word(u, ARENA + session.LOCK) == 0
    # The fresh output planes were zeroed; bytes outside the window stay zero.
    want_y, want_c = lut.apply_frame_int(payload(table_text()), yin, cin, width, height, stride, stride,
                                         base_y=bytes(len(yin)), base_c=bytes(len(cin)))
    assert bytes(u.mem_read(Y_OUT, len(yin))) == want_y and bytes(u.mem_read(C_OUT, len(cin))) == want_c
    rows.append(dict(case='processing_holds_session_lock', cache_calls=4, pixel_parity=True))
    cache.clear(); put(u, ARENA + session.LOCK, 1)
    assert f.c.call(core, (PARAMS,)) == 0 and not cache
    assert word(u, ARENA + session.LOCK) == 1
    assert bytes(u.mem_read(Y_OUT, len(yin))) == want_y
    rows.append(dict(case='busy_processing', owner_lock_preserved=True, output_unchanged=True))
    put(u, ARENA + session.LOCK, 0)
    assert f.c.call(core, (0,)) == 0 and word(u, ARENA + session.LOCK) == 0
    rows.append(dict(case='null_processing_does_not_acquire', result=0))
    # A validation failure after acquiring must release the lock too.
    u.mem_write(DESC_IN, b'\x02')
    assert f.c.call(core, (PARAMS,)) == 0 and word(u, ARENA + session.LOCK) == 0
    rows.append(dict(case='processing_failure_unlocks', result=0))
    assert f.call(reset=True) == 0 and not f.live
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=Path('Z_30_0120.bin'))
    p.add_argument('--output', type=Path, default=Path('build/research/lut-postprocess/session.json'))
    args = p.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('Source and output must differ')
    data = extract_eg(args.source.read_bytes()); StockImage(data)
    heaps = heap_contract(data)
    owners, fixture = ownership(data)
    locks = processing_lock(fixture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(source_sha256=sha256(data), heap_contract=heaps,
        static_boot_heap_config=dict(address='0x114a32c4',
            words=[hex(x) for x in StockImage(data).words(0x114A32C4, 6)],
            meaning='Two 12-byte configs: General 16 MiB, Heap 25 MiB; not runtime free space.'),
        ownership=owners, processing=locks, camera_access=False,
        limitations=['Heap region table and SVC results substituted; no real free-space proof.',
                     'Session heap/file/cache boundaries replaced; not a real task or menu.',
                     'Lock execution and held-owner cases are not a multicore stress test.',
                     'Ownership fixtures use 4096 bytes and growth fixtures 128..4096; default grows 64 KiB..8 MiB.',
                     'No runtime trigger or SD-enabled firmware image produced.']), indent=2) + '\n', encoding='utf-8')
    print(f'PASS: {len(heaps)} native heap, {len(owners)} ownership, {len(locks)} processing-lock cases.')


if __name__ == '__main__':
    main()
