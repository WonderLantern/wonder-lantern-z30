"""Run the resident card loader (Thumb) in Unicorn against fake native services."""
import struct
import unittest
from pathlib import Path

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                                   UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_SP)
    import keystone  # noqa: F401
except ImportError:  # optional research dependencies
    Uc = None

from z30_extensions import card
from z30_extensions.features import card_loader as cl
from z30_extensions.platform.z30_120 import BASE, SCATTER, CAPACITY

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Z_30_0120.bin'
POOL = 0xD0000000
STACK = 0xE0010000
SUCCESS = cl.SUCCESS


class Camera:
    """Resident image in Unicorn; native calls answered in Python."""

    def __init__(self, image, functions, files, pool_fail=False):
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(0x10000000, CAPACITY + 0x20000)
        uc.mem_write(BASE, image[:-2])
        uc.mem_map(0xCC400000, 0x700000)
        uc.mem_write(0xCC400000 + 0x20000, image[:card.DRAM_END - BASE])
        source, destination, length, _ = SCATTER
        uc.mem_map(card.RAM_LO, card.RAM_HI - card.RAM_LO)       # main RAM, scatter data copied in
        uc.mem_write(destination, image[source - BASE:source - BASE + length])
        uc.mem_map(POOL, 0x400000)
        uc.mem_map(STACK - 0x10000, 0x10000)
        self.files, self.pool_fail = files, pool_fail
        self.open_files, self.next_fd = {}, 3
        self.allocations, self.freed, self.syncs, self.log = [], [], [], b''
        self.accel = 1
        self.services = {
            cl.MEMSET: self.memset, cl.SET_PATH: self.set_path, cl.OPEN: self.open,
            cl.READ: self.read, cl.CLOSE: self.close, cl.FD_CLOSE: self.fd_close,
            cl.FD_WRITE: self.fd_write, cl.POOL_ALLOC: self.alloc, cl.POOL_FREE: self.free,
            functions['sync']: self.sync, cl.ACCEL_READ: lambda: self.accel,
        }
        for address in self.services:
            uc.hook_add(UC_HOOK_CODE, self._service, begin=address, end=address)
        self.stub = functions['stub']

    def r(self, reg):
        return self.uc.reg_read(reg)

    def _service(self, uc, address, size, user):
        result = self.services[address]()
        uc.reg_write(UC_ARM_REG_R0, result & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def memset(self):
        self.uc.mem_write(self.r(UC_ARM_REG_R0), bytes(self.r(UC_ARM_REG_R1)))
        return 0

    def set_path(self):
        obj, path = self.r(UC_ARM_REG_R0), self.r(UC_ARM_REG_R1)
        self.uc.mem_write(obj + 32, struct.pack('<I', path))
        return SUCCESS

    def _path(self, obj):
        address = struct.unpack('<I', self.uc.mem_read(obj + 32, 4))[0]
        return bytes(self.uc.mem_read(address, 32)).split(b'\0')[0].decode()

    def open(self):
        obj, flags = self.r(UC_ARM_REG_R0), self.r(UC_ARM_REG_R1)
        path = self._path(obj)
        if flags == cl.CREATE:
            self.files[path] = b''
        elif path not in self.files:
            return 0x0437FFFF
        fd = self.next_fd
        self.next_fd += 1
        self.open_files[fd] = [path, 0]
        self.uc.mem_write(obj + 8, struct.pack('<i', fd))
        return SUCCESS

    def read(self):
        obj, buf, size, out = (self.r(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        fd = struct.unpack('<i', self.uc.mem_read(obj + 8, 4))[0]
        path, pos = self.open_files[fd]
        chunk = self.files[path][pos:pos + min(size, 4096)]   # short reads on purpose
        self.open_files[fd][1] += len(chunk)
        self.uc.mem_write(buf, chunk)
        self.uc.mem_write(out, struct.pack('<I', len(chunk)))
        return SUCCESS

    def close(self):
        fd = struct.unpack('<i', self.uc.mem_read(self.r(UC_ARM_REG_R0) + 8, 4))[0]
        self.open_files.pop(fd)
        return SUCCESS

    def fd_close(self):
        self.open_files.pop(self.r(UC_ARM_REG_R0), None)
        return 0

    def fd_write(self):
        fd, buf, size, out = (self.r(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        path = self.open_files[fd][0]
        self.files[path] += bytes(self.uc.mem_read(buf, size))
        self.uc.mem_write(out, struct.pack('<I', size))
        return 0

    def alloc(self):
        if self.pool_fail:
            return 0
        address = POOL + 0x1230 + sum(a[1] + 0x100 for a in self.allocations)
        self.allocations.append((address, self.r(UC_ARM_REG_R0)))
        return address

    def free(self):
        self.freed.append(self.r(UC_ARM_REG_R0))
        return 0

    def sync(self):
        self.syncs.append((self.r(UC_ARM_REG_R0), self.r(UC_ARM_REG_R1)))
        return 1

    def tick(self):
        self.uc.reg_write(UC_ARM_REG_SP, STACK)
        stops = {cl.IMU_SUCCESS: True, cl.FAILURE: False}
        result = {}

        def stop(uc, address, size, user):
            if address in stops:
                result['success'] = stops[address]
                uc.emu_stop()
        hook = self.uc.hook_add(UC_HOOK_CODE, stop)
        self.uc.emu_start(card_loader_site(self) | 1, 0, count=20_000_000)
        self.uc.hook_del(hook)
        return result.get('success')

    def cell(self):
        return struct.unpack('<I', self.uc.mem_read(cl.CELL, 4))[0]

    def read_mem(self, address, size):
        return bytes(self.uc.mem_read(address, size))


def card_loader_site(camera):
    return cl.IMU_SITE


MARK = 0xC1832D24          # +0 of the unused page-0 menu record: zero in stock data
RAM_SLOT = 0xC5000000      # heap address the synthetic feature patches


def synthetic_feature(result=1):
    """focal-length plus, in card builds, a RAM edit and a load-time function that
    records its arguments (tail base, INFO table, tail size) at MARK."""
    from unittest import mock
    from z30_extensions.assembly import assemble, constant
    from z30_extensions.features import focal_length
    stock_add = focal_length.add

    def add(plan):
        stock_add(plan)
        if not plan.card:
            return
        fn = plan.alloc(bytes(32))
        plan.put(fn, assemble(f'''{constant('r3', MARK)} str r1,[r3]; str r0,[r3,#4]; str r2,[r3,#8];
                                  movs r0,#{result}; bx lr;''', fn, 32))
        plan.on_load(fn | 1)
        plan.ram_edit(RAM_SLOT, struct.pack('<I', fn | 1), bytes(4), 'test pointer into the tail')
    return mock.patch.object(focal_length, 'add', add)


@unittest.skipUnless(Uc and SOURCE.exists(), 'needs unicorn, keystone and Z_30_0120.bin')
class CardLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        cls.stock = extract_eg(SOURCE.read_bytes())
        cls.resident = card.build_resident(cls.stock)
        cls.image, cls.rid = cls.resident.image, cls.resident.identity
        loader = cls.resident.report['features']['card_loader']
        cls.functions = {k: int(v, 16) for k, v in loader['functions'].items()}
        cls.info = int(loader['info'], 16)
        # Without the ready banner: its load-time function changes memory the Python
        # reference loader (no load-time calls) does not; test_ready_banner_is_armed covers it.
        cls.patch, _ = card.build_card(cls.stock, 'full', cls.resident, ready_banner=False)
        cls.blob = card.pack(cls.patch)

    def camera(self, blob=None, **kw):
        files = {} if blob is None else {'A:\\Z30\\WL.PATCH': blob}
        return Camera(self.image, self.functions, files, **kw)

    def run_ticks(self, camera, ticks):
        for _ in range(ticks):
            self.assertTrue(camera.tick())   # the stock accelerometer result passes through

    def loaded(self, blob, code=1):
        cam = self.camera(blob)
        self.run_ticks(cam, cl.START)
        state = cam.cell()
        self.assertEqual(state & 0xFFFF, cl.DONE | (code << 8), hex(state))
        return cam, state & 0xFFFF0000

    def test_loads_like_the_reference_loader(self):
        cam = self.camera(self.blob)
        self.run_ticks(cam, cl.START - 1)
        self.assertEqual(cam.cell(), cl.START - 1)          # waiting, nothing read yet
        self.run_ticks(cam, 1)
        state = cam.cell()
        self.assertEqual(state & 0xFFFF, cl.DONE | (1 << 8), hex(state))
        base = state & 0xFFFF0000
        allocation, size = cam.allocations[0]
        self.assertTrue(allocation <= base - 8 and base - 8 + len(self.blob) - card.HEADER.size <= allocation + size)
        # Python reference on the same starting memory.
        reference = ReferenceMemory(self.image, base, len(self.patch.tail))
        card.load(self.patch, reference, base, self.rid)
        self.assertEqual(cam.read_mem(base, len(self.patch.tail)), reference.read(base, len(self.patch.tail)))
        for edit in self.patch.edits:
            target = card.target_address(edit.address, len(edit.expected))
            self.assertEqual(cam.read_mem(target, len(edit.expected)), reference.read(target, len(edit.expected)),
                             hex(edit.address))
        self.assertIn((base, len(self.patch.tail)), cam.syncs)
        code_edits = [e for e in self.patch.edits if e.address < card.DRAM_END]
        self.assertEqual(len(cam.syncs), 1 + len(code_edits))
        self.assertEqual(cam.freed, [])
        self.assertTrue(cam.files['A:\\Z30\\WL.LOG'].startswith(b'WL.PATCH S=01 B='))
        self.run_ticks(cam, 3)                              # done: later ticks do nothing
        self.assertEqual(cam.cell(), state)

    def test_ready_banner_is_armed(self):
        from z30_extensions.features import ready_banner
        patch, report = card.build_card(self.stock, 'full', self.resident)
        self.assertEqual(report['card']['init_functions'], 1)
        cam = self.camera(card.pack(patch))
        # banner_start reads the UI language through the stock property getter: answer Simplified.
        cam.services[ready_banner.GET_PROPERTY] = lambda: ready_banner.SIMPLIFIED
        cam.uc.hook_add(UC_HOOK_CODE, cam._service, begin=ready_banner.GET_PROPERTY, end=ready_banner.GET_PROPERTY)
        self.run_ticks(cam, cl.START)
        cell = cam.cell()
        self.assertEqual(cell & 0xFFFF, cl.DONE | (1 << 8), hex(cell))
        base = cell & 0xFFFF0000
        banner = report['features']['ready_banner']
        # The report comes from one of build_card's links; its base follows from the load-time function.
        report_base = (int(banner['on_load'], 16) & ~1) - patch.init[0]
        state = base + int(banner['state'], 16) - report_base
        phase, _t0, language = struct.unpack('<3I', cam.read_mem(state, 12))
        self.assertEqual((phase, language), (ready_banner.ARMED, 1))

    @unittest.skip('ready_banner.REPAINT_TRIGGER is off (camera, 2026-11-25)')
    def test_ready_banner_repaint_trigger(self):
        """The widget recorder and the UI tick marking widgets dirty (portrait-ui calls them)."""
        from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1
        from z30_extensions.features import ready_banner as rb
        patch, report = card.build_card(self.stock, 'full', self.resident)
        cam = self.camera(card.pack(patch))
        cam.services[rb.GET_PROPERTY] = lambda: rb.SIMPLIFIED
        cam.uc.hook_add(UC_HOOK_CODE, cam._service, begin=rb.GET_PROPERTY, end=rb.GET_PROPERTY)
        self.run_ticks(cam, cl.START)
        base = cam.cell() & 0xFFFF0000
        banner = report['features']['ready_banner']
        link = (int(banner['on_load'], 16) & ~1) - patch.init[0]
        at = lambda address: base + (int(address, 16) & ~1) - link
        state, tick, record = at(banner['state']), at(banner['repaint']['tick']), at(banner['repaint']['record'])
        stop = BASE
        def call(function, *args):
            for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1), args):
                cam.uc.reg_write(reg, value)
            cam.uc.reg_write(UC_ARM_REG_SP, STACK)
            cam.uc.reg_write(UC_ARM_REG_LR, stop | 1)
            cam.uc.emu_start(function | 1, stop, count=100000)
        word = lambda address: struct.unpack('<I', cam.read_mem(address, 4))[0]
        put = lambda address, value: cam.uc.mem_write(address, struct.pack('<I', value))
        widgets = [0xC4000000 + 0x100 * i for i in range(12)]
        for w in widgets:
            cam.uc.mem_write(w, bytes(0x100))
            put(w + 0x4C, 0x10400001)
        put(widgets[1] + 0x4C, rb.BACKGROUND_DRAW)        # background-only: never recorded
        live = rb.LIVE_CONTAINER + 0x9C
        for w in widgets[:3] + widgets[:3]:              # duplicates are recorded once
            call(record, w, live)
        call(record, widgets[3], 0xC5000000)             # another container: ignored
        self.assertEqual(word(state + 28), 2)
        self.assertEqual([word(state + 32 + 4 * i) for i in range(2)], [widgets[0], widgets[2]])
        for w in widgets[4:12]:                          # the ring keeps the latest RING
            call(record, w, live)
        self.assertEqual(word(state + 28), 10)
        ring = sorted(word(state + 32 + 4 * i) for i in range(rb.RING))
        self.assertEqual(ring, sorted(widgets[4:12]))
        # Photo live view without a setting page.
        cam.uc.mem_write(rb.VIEW_KIND, b'\x00'); cam.uc.mem_write(rb.UI_SERVICE_PHASE, b'\x01')
        put(rb.PHOTO_BACKEND, 1); put(rb.OVERLAY_BUSY[0], 0); put(rb.OVERLAY_BUSY[1], 0)
        self.assertEqual(word(state), rb.ARMED)
        put(rb.OVERLAY_BUSY[0], 1)                       # a setting page: nothing yet
        call(tick)
        self.assertEqual((word(state + 24), word(widgets[4] + rb.DIRTY)), (0, 0))
        put(rb.OVERLAY_BUSY[0], 0)
        # UI phase and photo backend are only recorded (camera, 2026-11-25): it still marks.
        cam.uc.mem_write(rb.UI_SERVICE_PHASE, bytes([3])); put(rb.PHOTO_BACKEND, 0)
        call(tick)
        self.assertEqual(word(state + 24), rb.MARK_SHOW)
        self.assertEqual(tuple(cam.read_mem(state + 68, 4)),
                         (rb.TICK_FAIL_PHASE | rb.TICK_FAIL_BACKEND, 3, 0, 0))
        self.assertTrue(all(word(w + rb.DIRTY) == 1 for w in widgets[4:12]))
        for w in widgets:
            put(w + rb.DIRTY, 0)
        call(tick)                                       # once only
        self.assertFalse(any(word(w + rb.DIRTY) for w in widgets))
        # Showing: the erase mark once SHOW_MS has passed.
        put(state, rb.SHOWING); put(rb.MS_CLOCK, 10_000); put(state + 4, 9_000)
        call(tick)
        self.assertEqual(word(state + 24), rb.MARK_SHOW)
        put(rb.MS_CLOCK, 11_500)
        call(tick)
        self.assertEqual(word(state + 24), rb.MARK_SHOW | rb.MARK_ERASE)
        self.assertTrue(all(word(w + rb.DIRTY) == 1 for w in widgets[4:12]))

    def test_ram_edit_and_load_time_function(self):
        with synthetic_feature():
            patch, _ = card.build_card(self.stock, 'focal-length', self.resident)
        cam, base = self.loaded(card.pack(patch))
        function = base + patch.init[0]
        self.assertEqual(struct.unpack('<I', cam.read_mem(RAM_SLOT, 4))[0], function | 1)
        tail_base, info, tail_size = struct.unpack('<3I', cam.read_mem(MARK, 12))
        self.assertEqual((tail_base, info, tail_size), (base, self.info, len(patch.tail)))
        fields = dict(zip(cl.INFO_FIELDS, struct.unpack(f'<{len(cl.INFO_FIELDS)}I',
                                                        cam.read_mem(info, 4 * len(cl.INFO_FIELDS)))))
        self.assertEqual(fields, dict(abi=cl.INIT_ABI, size=4 * len(cl.INFO_FIELDS),
                                      sync=self.functions['sync'] | 1, cell=cl.CELL,
                                      pool_alloc=cl.POOL_ALLOC | 1, pool_free=cl.POOL_FREE | 1))

    def test_load_time_failure_is_reported(self):
        with synthetic_feature(result=0):
            patch, _ = card.build_card(self.stock, 'focal-length', self.resident)
        cam, base = self.loaded(card.pack(patch), code=14)
        self.assertEqual(struct.unpack('<I', cam.read_mem(RAM_SLOT, 4))[0], (base + patch.init[0]) | 1)

    def test_menu_tab_card(self):
        from z30_extensions.features import menu_tab
        from z30_extensions.features.sound_layout import SETUP_META_SOURCE
        patch, _ = card.build_card(self.stock, 'full', self.resident, menu_tab=True)
        cam, _ = self.loaded(card.pack(patch))
        rows = lambda address: struct.unpack('<I', cam.read_mem(address, 4))[0] & 0xFFFF
        self.assertEqual(rows(menu_tab.TAB_META + 20), 8)
        self.assertEqual(rows(SCATTER[1] + SETUP_META_SOURCE - SCATTER[0] + 20), 38)   # no setup row
        # The row count is the last tab bar word written.
        metas = [e.address for e in patch.edits if menu_tab.TAB_META <= e.address < menu_tab.TAB_META + 28]
        self.assertEqual(metas, [menu_tab.TAB_META + n for n in (4, 12, 16, 20)])

    def test_unknown_sections(self):
        optional = card.parse(self.blob)
        optional.extra.append((99, 0, b'abcd'))
        self.loaded(card.pack(optional))
        required = card.parse(self.blob)
        required.extra.append((99, card.REQUIRED, b'abcd'))
        self.expect_failure(card.pack(required), 13)

    def test_no_file_gives_up_and_stays_stock(self):
        cam = self.camera()
        self.run_ticks(cam, cl.LIMIT + 2)
        self.assertEqual(cam.cell(), cl.DONE | (2 << 8))
        self.assertEqual(cam.allocations, [])
        self.assertNotIn('A:\\Z30\\WL.LOG', cam.files)
        self.assertEqual(cam.read_mem(cl.IMU_SITE + card.DRAM_ALIAS, 8), self.image[cl.IMU_SITE - BASE:][:8])

    def test_stock_failure_branch_kept(self):
        cam = self.camera()
        cam.accel = 0
        self.assertFalse(cam.tick())

    def expect_failure(self, blob, code, **kw):
        cam = self.camera(blob, **kw)
        before = cam.read_mem(0xCC400000 + 0x20000, card.DRAM_END - BASE)
        ram = cam.read_mem(card.RAM_LO, card.RAM_HI - card.RAM_LO)
        self.run_ticks(cam, cl.START)
        self.assertEqual(cam.cell(), cl.DONE | (code << 8), hex(cam.cell()))
        self.assertEqual(cam.read_mem(0xCC400000 + 0x20000, card.DRAM_END - BASE), before)
        cell = cl.CELL - card.RAM_LO
        after = cam.read_mem(card.RAM_LO, card.RAM_HI - card.RAM_LO)
        self.assertTrue(after[:cell] == ram[:cell] and after[cell + 4:] == ram[cell + 4:])
        self.assertEqual(cam.freed, [a for a, _ in cam.allocations])
        self.assertEqual(cam.syncs, [])
        self.assertTrue(cam.files['A:\\Z30\\WL.LOG'].startswith(b'WL.PATCH S=%02X B=00000000' % code))

    def test_failures_write_nothing(self):
        damaged = bytearray(self.blob)
        damaged[-1] ^= 1
        self.expect_failure(bytes(damaged), 7)
        other = bytearray(self.blob)
        struct.pack_into('<I', other, 8, self.rid ^ 1)
        self.expect_failure(bytes(other), 5)
        self.expect_failure(self.blob[:20], 3)
        self.expect_failure(self.blob + b'\0' * 4, 4)
        self.expect_failure(self.blob, 6, pool_fail=True)
        # A patch for different memory: the first code edit expects other bytes.
        patch = card.parse(self.blob)
        edit = next(e for e in patch.edits if e.address < card.DRAM_END)
        edit.expected = bytes(b ^ 0x5A for b in edit.expected)
        self.expect_failure(card.pack(patch), 9)
        # An edit outside the writable memory, and a load-time function outside the tail.
        patch = card.parse(self.blob)
        patch.edits[0].address = 0x10700000
        self.expect_failure(card.pack(patch), 8)
        patch = card.parse(self.blob)
        patch.init = [len(patch.tail)]
        self.expect_failure(card.pack(patch), 8)


class ReferenceMemory:
    def __init__(self, image, base, tail):
        ram = bytearray(card.RAM_HI - card.RAM_LO)
        source, destination, length, _ = SCATTER
        ram[destination - card.RAM_LO:destination - card.RAM_LO + length] = image[source - BASE:source - BASE + length]
        self.regions = {0xCC400000 + 0x20000: bytearray(image[:card.DRAM_END - BASE]),
                        card.RAM_LO: ram, base: bytearray(tail)}

    def _find(self, address, size):
        for start, data in self.regions.items():
            if start <= address and address + size <= start + len(data):
                return data, address - start
        raise KeyError(hex(address))

    def read(self, address, size):
        data, offset = self._find(address, size)
        return bytes(data[offset:offset + size])

    def write(self, address, value):
        data, offset = self._find(address, len(value))
        data[offset:offset + len(value)] = value


if __name__ == '__main__':
    unittest.main()
