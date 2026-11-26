"""Linux handoff kit (experimental branch): generated DTB and payload contract."""
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'src'))

import build_linux_kit as kit  # noqa: E402


def parse(blob):
    """Flatten a DTB into {'/path/prop': bytes}."""
    magic, total, off_struct, off_strings = struct.unpack_from('>4I', blob)
    assert magic == 0xd00dfeed and total == len(blob)
    props, path, o = {}, [], off_struct
    while True:
        token, = struct.unpack_from('>I', blob, o); o += 4
        if token == 1:
            end = blob.index(b'\0', o); path.append(blob[o:end].decode()); o = (end + 4) & ~3
        elif token == 2:
            path.pop()
        elif token == 3:
            length, name_off = struct.unpack_from('>II', blob, o); o += 8
            name = blob[off_strings + name_off:blob.index(b'\0', off_strings + name_off)].decode()
            props['/'.join(path) + '/' + name] = blob[o:o + length]; o = (o + length + 3) & ~3
        elif token == 9:
            return props


class LinuxKitTests(unittest.TestCase):
    def setUp(self):
        self.blob = kit.device_tree()
        self.props = parse(self.blob)

    def test_every_placeholder_is_there(self):
        words = [struct.unpack_from('>I', self.blob, o)[0] for o in range(0, len(self.blob) - 3, 4)]
        for name, value in kit.PH.items():
            self.assertIn(value, words, name)

    def test_placeholders_match_the_payload(self):
        source = (ROOT / 'modules' / 'linuxboot' / 'linuxboot.c').read_text()
        names = dict(gicd='GICD', gicc='GICC', color='COLOR', alpha='ALPHA', width='WIDTH', height='HEIGHT',
                     cstride='CSTRIDE', astride='ASTRIDE', initrd_start='INITRD_START', initrd_end='INITRD_END',
                     mpidr='MPIDR', mpidr1='MPIDR1', release='RELEASE')
        for key, value in kit.PH.items():
            self.assertIn(f'#define PH_{names[key]} {value:#x}u', source)

    def test_cpu_timer_memory(self):
        self.assertEqual(self.props['/cpus/cpu@f00/reg'], struct.pack('>I', kit.PH['mpidr']))   # filled at handoff
        self.assertEqual(self.props['/timer/clock-frequency'], struct.pack('>I', 48_000_000))
        base, size = struct.unpack('>II', self.props['/memory@90000000/reg'])
        # Inside the stock image-buffer area, clear of the handoff's fixed destinations' end.
        self.assertGreaterEqual(base, 0x8a600000)
        self.assertLessEqual(base + size, 0xbf300000)
        self.assertTrue(base <= 0x92000000 < 0x98000000 < 0x9ff00000 < base + size)
        self.assertEqual(base & 0x07ffffff, 0)   # AUTO_ZRELADDR: zImage at 0x92000000 decompresses to base + 0x8000

    def test_mmc_node(self):
        self.assertEqual(self.props['/mmc@2a100000/compatible'], b'socionext,milbeaut-m10v-sdhci-3.0\0')
        self.assertEqual(self.props['/mmc@2a100000/reg'], struct.pack('>II', 0x2a100000, 0x1000))
        self.assertEqual(self.props['/mmc@2a100000/interrupts'], struct.pack('>III', 0, 198, 4))
        self.assertEqual(self.props['/sd-clk/clock-frequency'], struct.pack('>I', 200_000_000))
        self.assertNotIn('/mmc@2a100000/status', self.props)
        self.assertEqual(self.props['/mmc@2a200000/compatible'], b'socionext,milbeaut-m10v-sdhci-3.0\0')
        self.assertEqual(self.props['/mmc@2a200000/reg'], struct.pack('>II', 0x2a200000, 0x1000))
        self.assertEqual(self.props['/mmc@2a200000/interrupts'], struct.pack('>III', 0, 201, 4))
        self.assertIn('/mmc@2a200000/non-removable', self.props)
        self.assertIn('/mmc@2a200000/z30,sdio', self.props)
        self.assertNotIn('/mmc@2a200000/status', self.props)
        no_mmc = parse(kit.device_tree(no_mmc=True))
        self.assertEqual(no_mmc['/mmc@2a100000/status'], b'disabled\0')
        self.assertEqual(no_mmc['/mmc@2a200000/status'], b'disabled\0')
        no_sdio = parse(kit.device_tree(no_sdio=True))
        self.assertNotIn('/mmc@2a100000/status', no_sdio)
        self.assertEqual(no_sdio['/mmc@2a200000/status'], b'disabled\0')

    def test_usb_node(self):
        self.assertEqual(self.props['/usb@2aa00000/compatible'], b'snps,dwc3\0')
        self.assertEqual(self.props['/usb@2aa00000/reg'], struct.pack('>II', 0x2aa00000, 0x10000))
        self.assertEqual(self.props['/usb@2aa00000/interrupts'], struct.pack('>III', 0, 204, 4))
        self.assertEqual(self.props['/usb@2aa00000/dr_mode'], b'peripheral\0')
        self.assertEqual(self.props['/usb@2aa00000/maximum-speed'], b'high-speed\0')
        self.assertEqual(self.props['/usb@2aa00000/phy_type'], b'utmi\0')
        self.assertNotIn('/usb@2aa00000/status', self.props)
        no_usb = parse(kit.device_tree(no_usb=True))
        self.assertEqual(no_usb['/usb@2aa00000/status'], b'disabled\0')

    def test_payload_is_flat(self):
        from z30_extensions.features.experimental import linuxboot_payload as p
        self.assertEqual(len(p.CODE), p.SIZE)
        self.assertLess(p.SIZE, 8192)


if __name__ == '__main__':
    unittest.main()
