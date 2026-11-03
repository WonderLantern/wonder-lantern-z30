import importlib.util
import struct
import unittest
from unittest.mock import patch
from z30_extensions.features.ui_text import TRANSLATIONS, utf16_label, prepare, configure_entry, row_code


class LabelTests(unittest.TestCase):
    def test_translation_bounds_and_roundtrip(self):
        for pair in TRANSLATIONS.values():
            for label in pair:
                raw = utf16_label(label)
                self.assertLessEqual(len(raw), 50)
                self.assertEqual(raw[:-2].decode('utf-16-le'), label)
        for label in ('a\0b', 'a'*25, '\U0001f600'*13):
            with self.assertRaises(ValueError):
                utf16_label(label)


@unittest.skipUnless(importlib.util.find_spec('unicorn') and importlib.util.find_spec('keystone'),
                     'Native helper execution needs Unicorn and Keystone')
class TextExecutionTests(unittest.TestCase):
    def test_language_encoding_binding_and_bounded_module_output(self):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_SP
        class Image:
            def words(self, address, count):
                return (0xc2013368, 0xc18202d0)
            def read(self, address, size):
                return bytes.fromhex('0f240264')
        class Plan:
            image = Image()
            def __init__(self):
                self.tail = bytearray()
            def alloc(self, raw):
                address = 0x12080000 + len(self.tail)
                self.tail.extend(raw + bytes(-len(raw) % 4))
                return address
            def put(self, address, raw):
                off = address - 0x12080000
                self.tail[off:off+len(raw)] = raw
        plan, functions = Plan(), {}
        # The stub image has no font table; cjk_fonts has its own tests.
        with patch('z30_extensions.features.ui_text.cjk_fonts.add'):
            resources = prepare(plan, functions)
        record = resources.alloc('Enabled')
        entry_offset = 38*28
        def controls(flags):
            raw = bytearray(39*28)
            struct.pack_into('<I',raw,entry_offset+16,flags)
            struct.pack_into('<I',raw,entry_offset+24,0x2333)
            return plan.alloc(raw)
        ascii_controls = controls(0x1201)
        unicode_controls = controls(0x3001)
        configure_entry(plan, functions, ascii_controls, unicode_controls)
        from z30_extensions.assembly import assemble
        functions['labels'] = 0x10011001
        functions['row'] = plan.alloc(bytes(256))
        plan.put(functions['row'],assemble(row_code(functions,0xc1003000,0x2333,[955,956,957,958]),
                                          functions['row'],256))
        for language in range(36):
            expected, kind, font = {26: ('启用声音', 3, 2), 27: ('啟用聲音', 3, 0)}.get(
                language, ('Enabled', 2, None))
            uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
            for address, size in ((0x10010000,0x500000),(0x12080000,0x10000),
                                  (0xc1000000,0x10000),(0xc2010000,0x10000)):
                uc.mem_map(address, size)
            uc.mem_write(0x12080000, bytes(plan.tail))
            from unicorn import UC_PROT_READ, UC_PROT_EXEC
            uc.mem_protect(0x12080000,0x10000,UC_PROT_READ|UC_PROT_EXEC)
            captured, bindings, setters, row_arguments = [], [], [], []
            widget = 0xc1001000
            uc.mem_write(widget+12, struct.pack('<I',1))
            def hook(engine, address, size, data):
                if address == 0x10010000:
                    engine.emu_stop()
                elif address == 0x1045e344:
                    row_arguments.append(engine.reg_read(UC_ARM_REG_R1))
                    engine.emu_stop()
                elif address == 0x10011000:
                    engine.reg_write(UC_ARM_REG_PC, engine.reg_read(UC_ARM_REG_LR))
                elif address == 0x10465984:
                    engine.reg_write(UC_ARM_REG_R0, language)
                    engine.reg_write(UC_ARM_REG_PC, engine.reg_read(UC_ARM_REG_LR))
                elif address in (0x10454efe, 0x10454f5a):
                    setters.append(address)
                    pointer = engine.reg_read(UC_ARM_REG_R0)
                    unit = 2 if address == 0x10454f5a else 1
                    raw = bytearray()
                    for i in range(25 if unit == 2 else 51):
                        char = bytes(engine.mem_read(pointer+i*unit,unit))
                        if char == bytes(unit): break
                        raw.extend(char)
                    else: self.fail('Text helper did not terminate within the native limit')
                    captured.append(raw.decode('utf-16-le' if unit == 2 else 'ascii'))
                    engine.reg_write(UC_ARM_REG_PC, engine.reg_read(UC_ARM_REG_LR))
                elif address == 0x104546c2:
                    new = engine.reg_read(UC_ARM_REG_R2)
                    bindings.append(new)
                    engine.mem_write(widget+12, struct.pack('<I',new))
                    engine.reg_write(UC_ARM_REG_PC, engine.reg_read(UC_ARM_REG_LR))
            uc.hook_add(UC_HOOK_CODE, hook)
            def run(name, first, second=40):
                uc.reg_write(UC_ARM_REG_R0,first)
                uc.reg_write(UC_ARM_REG_R1,second)
                uc.reg_write(UC_ARM_REG_R2,0)
                uc.reg_write(UC_ARM_REG_SP,0xc100f000)
                uc.reg_write(UC_ARM_REG_LR,0x10010001)
                uc.emu_start(functions[name]|1,0,count=1000)
            run('text',record)
            self.assertEqual(captured[-1],expected)
            if font is not None:
                self.assertEqual(bytes(uc.mem_read(0xc2013368+0xe10+120*16,1)),bytes([font]))
            run('text_entry',record,39)
            self.assertEqual(captured[-1],expected)
            self.assertEqual(setters[-1], 0x10454f5a if kind == 3 else 0x10454efe)
            page, metadata = 0xc1003000, 0xc1003100
            uc.mem_write(page+40, struct.pack('<I',metadata))
            run('entry_select',page)
            selected = struct.unpack('<I', uc.mem_read(metadata+12,4))[0]
            self.assertEqual(selected, unicode_controls if kind == 3 else ascii_controls)
            self.assertEqual(uc.reg_read(UC_ARM_REG_R0),page)
            # Cold entry: caller prefetched the opposite encoding's record
            # before setup initialization changed the metadata table pointer.
            stale = ascii_controls if kind == 3 else unicode_controls
            uc.mem_write(metadata+12,struct.pack('<I',stale))
            run('row',page,stale+entry_offset)
            self.assertEqual(row_arguments[-1],selected+entry_offset)
            flags = struct.unpack('<I',uc.mem_read(row_arguments[-1]+16,4))[0]
            self.assertEqual(flags,0x3001 if kind == 3 else 0x1201)
            # Other stock rows retain the caller's descriptor and table.
            run('row',page,stale)
            self.assertEqual(row_arguments[-1],stale)
            # Owned-page descriptors are passed through without substitution.
            other_page = 0xc1003200
            uc.mem_write(other_page,struct.pack('<I',(955<<16)|2))
            uc.mem_write(other_page+40,struct.pack('<I',metadata))
            run('row',other_page,stale+entry_offset)
            self.assertEqual(row_arguments[-1],stale+entry_offset)
            run('text_bind',widget)
            self.assertEqual(bindings,[0,kind])
            run('text_bind',widget)
            self.assertEqual(bindings,[0,kind,kind])
            uc.mem_write(0xc1002000,b'A'*50+b'\0')
            run('text_ascii',0xc1002000)
            self.assertEqual(captured[-1],'A'*(24 if kind == 3 else 50))
