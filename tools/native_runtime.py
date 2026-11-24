"""Small optional Unicorn fixture for target functions, with explicit RAM input.

No filesystem, camera, MMIO or GPU model. Initialized stock scatter bytes are
copied by the fixture; property storage is seeded from native descriptors.
"""
import struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
from unicorn.arm_const import *
from z30_extensions.platform.z30_120 import BASE, SCATTER, StockImage

RAM, STACK, STOP = 0x50000000, 0x60000000, 0x600FF000


class Context:
    def __init__(self, stock, image):
        self.stock = StockImage(stock)
        self.u = u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A15)
        # The stock kernel enables CP10/CP11 access (it runs NEON throughout);
        # the emulator starts with them off, so mirror the runtime state.
        u.reg_write(UC_ARM_REG_C1_C0_2, 0xF00000)
        u.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        u.mem_map(BASE, (len(image) + 4095) & ~4095)
        u.mem_write(BASE, image)
        u.mem_map(RAM, 0x100000)
        u.mem_map(STACK, 0x100000)
        u.mem_map(0xC1800000, 0x100000)
        source, destination, size, _ = SCATTER
        u.mem_write(destination, image[source - BASE:source - BASE + size])
        self.mapped = {p for a, b, _ in u.mem_regions() for p in range(a, b + 1, 4096)}
        self.map(0xC2ABCA34, 4)
        u.mem_write(0xC2ABCA34, struct.pack('<I', BASE + 0x1F61B61))
        def log(emulator, address, size, data):
            emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, log, begin=0x10236DD8, end=0x10236DD8)
        self.properties = {}

    def map(self, address, size):
        for page in range(address & ~4095, (address + size + 4095) & ~4095, 4096):
            if page not in self.mapped:
                self.u.mem_map(page, 4096)
                self.mapped.add(page)

    def seed(self, key, value=None):
        offset = 0x114F774C + (key >> 8) * 28 + 12
        start, count = struct.unpack('<2H', self.stock.read(offset, 4))
        if key >> 8 >= 0x56 or key & 255 >= count:
            raise ValueError('Unknown property descriptor')
        kind, length, storage, source = self.stock.words(0x114EB97C + (start + (key & 255)) * 16, 4)
        self.map(storage, length)
        self.u.mem_write(storage, self.stock.read(source, length))
        self.properties[key] = (storage, length)
        if value is not None:
            self.set(key, value)

    def set(self, key, value):
        storage, length = self.properties[key]
        if length != 4:
            raise ValueError('Fixture only sets scalar 32-bit properties')
        self.u.mem_write(storage, struct.pack('<I', value & 0xFFFFFFFF))

    def call(self, address, args=(), limit=3000000):
        u = self.u
        registers = (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                     UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)
        saved = [0x11220000 + i for i in range(8)]
        for register, value in zip(registers, saved): u.reg_write(register, value)
        for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            u.reg_write(register, value)
        u.reg_write(UC_ARM_REG_SP, STACK + 0xF0000)
        u.reg_write(UC_ARM_REG_LR, STOP | 1)
        u.emu_start(address | 1, STOP, timeout=30000000, count=limit)
        if u.reg_read(UC_ARM_REG_PC) != STOP:
            raise ValueError(f'Function did not return at {u.reg_read(UC_ARM_REG_PC):#x}')
        if [u.reg_read(register) for register in registers] != saved or u.reg_read(UC_ARM_REG_SP) != STACK + 0xF0000:
            raise ValueError('Target function violated preserved-register/stack contract')
        return u.reg_read(UC_ARM_REG_R0)

    def block(self, start, stop):
        self.u.reg_write(UC_ARM_REG_SP, STACK + 0xF0000)
        self.u.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.u.emu_start(start | 1, stop, timeout=30000000, count=100000)
        if self.u.reg_read(UC_ARM_REG_PC) != stop or self.u.reg_read(UC_ARM_REG_SP) != STACK + 0xF0000:
            raise ValueError('Native block did not reach its bounded continuation')
