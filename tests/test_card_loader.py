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
        uc.mem_map(destination, 0x100000)
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


@unittest.skipUnless(Uc and SOURCE.exists(), 'needs unicorn, keystone and Z_30_0120.bin')
class CardLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from z30_extensions.container import extract_eg
        cls.stock = extract_eg(SOURCE.read_bytes())
        cls.image, cls.report, cls.resident, cls.rid = card.build_resident(cls.stock)
        cls.functions = {k: int(v, 16) for k, v in cls.report['features']['card_loader']['functions'].items()}
        cls.patch, _ = card.build_card(cls.stock, 'full', cls.resident, cls.rid, cls.image)
        cls.blob = card.pack(cls.patch)

    def camera(self, blob=None, **kw):
        files = {} if blob is None else {'A:\\Z30\\WL.PATCH': blob}
        return Camera(self.image, self.functions, files, **kw)

    def run_ticks(self, camera, ticks):
        for _ in range(ticks):
            self.assertTrue(camera.tick())   # the stock accelerometer result passes through

    def test_loads_like_the_reference_loader(self):
        cam = self.camera(self.blob)
        self.run_ticks(cam, cl.START - 1)
        self.assertEqual(cam.cell(), cl.START - 1)          # waiting, nothing read yet
        self.run_ticks(cam, 1)
        state = cam.cell()
        self.assertEqual(state & 0xFFFF, cl.DONE | (1 << 8), hex(state))
        base = state & 0xFFFF0000
        allocation, size = cam.allocations[0]
        self.assertTrue(allocation <= base and base + len(self.blob) - card.HEADER.size <= allocation + size)
        # Python reference on the same starting memory.
        reference = ReferenceMemory(self.image, base, len(self.patch.tail))
        card.load(self.patch, reference, base, self.rid)
        self.assertEqual(cam.read_mem(base, len(self.patch.tail)), reference.read(base, len(self.patch.tail)))
        for edit in self.patch.edits:
            target = edit.address + card.DRAM_ALIAS if edit.address < card.DRAM_END else edit.address
            self.assertEqual(cam.read_mem(target, len(edit.expected)), reference.read(target, len(edit.expected)),
                             hex(edit.address))
        self.assertIn((base, len(self.patch.tail)), cam.syncs)
        code_edits = [e for e in self.patch.edits if e.address < card.DRAM_END]
        self.assertEqual(len(cam.syncs), 1 + len(code_edits))
        self.assertEqual(cam.freed, [])
        self.assertTrue(cam.files['A:\\Z30\\WL.LOG'].startswith(b'WL.PATCH S=01 B='))
        self.run_ticks(cam, 3)                              # done: later ticks do nothing
        self.assertEqual(cam.cell(), state)

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
        scatter = cam.read_mem(SCATTER[1], SCATTER[2])
        self.run_ticks(cam, cl.START)
        self.assertEqual(cam.cell(), cl.DONE | (code << 8), hex(cam.cell()))
        self.assertEqual(cam.read_mem(0xCC400000 + 0x20000, card.DRAM_END - BASE), before)
        cell = cl.CELL - SCATTER[1]
        after = cam.read_mem(SCATTER[1], SCATTER[2])
        self.assertEqual(after[:cell] + after[cell + 4:], scatter[:cell] + scatter[cell + 4:])
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


class ReferenceMemory:
    def __init__(self, image, base, tail):
        self.regions = {
            0xCC400000 + 0x20000: bytearray(image[:card.DRAM_END - BASE]),
            SCATTER[1]: bytearray(image[SCATTER[0] - BASE:SCATTER[0] - BASE + SCATTER[2]]),
            base: bytearray(tail),
        }

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
