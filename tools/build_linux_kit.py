"""Build the card files for booting Linux on the Z30 (experimental branch, docs/linux.md).

Writes OUTDIR/Z30/LINUX/ZIMAGE, Z30.DTB and (with --initrd) INITRD. Without
--kernel the kernel is the handoff test kernel (modules/linuxboot/testkernel),
which paints the LCD and counts seconds instead of running Linux.

The DTB is generated here (no dtc needed). Values only the running camera knows
are big-endian placeholder words that the handoff (modules/linuxboot) replaces:
GIC distributor / CPU interface, LCD overlay planes and geometry, initrd range.

  python tools/build_linux_kit.py --output output/linux-test
  python tools/build_linux_kit.py --output output/linux --kernel zImage --initrd initramfs.cpio.gz
"""
import argparse
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Placeholders (modules/linuxboot/linuxboot.c PH_*).
PH = dict(gicd=0x5a300001, gicc=0x5a300002, color=0x5a300003, alpha=0x5a300004, width=0x5a300005,
          height=0x5a300006, cstride=0x5a300007, astride=0x5a300008, initrd_start=0x5a300009,
          initrd_end=0x5a30000a, mpidr=0x5a30000b, mpidr1=0x5a30000c, release=0x5a30000d)
# Linux memory: the stock image-buffer area, cached and identity mapped (docs/linux.md).
MEMORY = (0x90000000, 0x2d000000)          # 0x90000000..0xBCFFFFFF, 720 MiB
CPU_MPIDR = 0xf00                          # MPIDR 0x80000F00 measured on the camera (cluster 15, core 0);
                                           # the handoff writes the booting core's real MPIDR[23:0] into reg
TIMER_HZ = 48_000_000                      # stock 0x100200D4 writes CNTFRQ = 48 MHz
# earlycon=z30ovl: kernel log on the LCD overlay from the first parameter on (no UART on the
# camera); keep_bootcon keeps it until the overlay framebuffer driver takes the screen.
BOOTARGS = 'earlycon=z30ovl keep_bootcon console=tty0 loglevel=8 rdinit=/init g_serial.use_acm=0'


class FDT:
    """Minimal flattened device tree writer (version 17)."""

    def __init__(self):
        self.struct = bytearray()
        self.strings = bytearray()
        self.offsets = {}

    def _string(self, name):
        if name not in self.offsets:
            self.offsets[name] = len(self.strings)
            self.strings += name.encode() + b'\0'
        return self.offsets[name]

    def _align(self):
        self.struct += bytes(-len(self.struct) % 4)

    def begin(self, name):
        self.struct += struct.pack('>I', 1) + name.encode() + b'\0'
        self._align()

    def end(self):
        self.struct += struct.pack('>I', 2)

    def prop(self, name, value=b''):
        if isinstance(value, str):
            value = value.encode() + b'\0'
        elif isinstance(value, int):
            value = struct.pack('>I', value)
        elif isinstance(value, (list, tuple)):
            value = b''.join(v.encode() + b'\0' if isinstance(v, str) else struct.pack('>I', v) for v in value)
        self.struct += struct.pack('>III', 3, len(value), self._string(name)) + value
        self._align()

    def blob(self):
        body = self.struct + struct.pack('>I', 9)
        header_size, rsvmap = 40, bytes(16)
        off_rsv = header_size
        off_struct = off_rsv + len(rsvmap)
        off_strings = off_struct + len(body)
        total = off_strings + len(self.strings)
        header = struct.pack('>10I', 0xd00dfeed, total, off_struct, off_strings, off_rsv, 17, 16, 0,
                             len(self.strings), len(body))
        return header + rsvmap + bytes(body) + bytes(self.strings)


def device_tree(earlycon_only=False, no_mmc=False, no_usb=False, no_sdio=False):
    f = FDT()
    f.begin('')
    f.prop('#address-cells', 1)
    f.prop('#size-cells', 1)
    f.prop('model', 'Nikon Z30 (Wonder Lantern handoff)')
    f.prop('compatible', ['wonderlantern,z30'])
    f.prop('interrupt-parent', 1)

    f.begin('chosen')
    f.prop('bootargs', BOOTARGS)
    f.prop('linux,initrd-start', PH['initrd_start'])
    f.prop('linux,initrd-end', PH['initrd_end'])
    f.prop('z30,gicd', PH['gicd'])
    f.end()

    f.begin(f'memory@{MEMORY[0]:x}')
    f.prop('device_type', 'memory')
    f.prop('reg', list(MEMORY))
    f.end()

    f.begin('cpus')
    f.prop('#address-cells', 1)
    f.prop('#size-cells', 0)
    # Core 1 waits in the handoff's parking loop (modules/linuxboot/lowlevel.S); the
    # kernel's "wonderlantern,z30-smp" method writes secondary_startup_arm's physical
    # address to cpu-release-addr and sends SEV.
    for n, mpidr in ((0, PH['mpidr']), (1, PH['mpidr1'])):
        f.begin(f'cpu@{CPU_MPIDR + n:x}')
        f.prop('device_type', 'cpu')
        f.prop('compatible', 'arm,cortex-a7')
        f.prop('reg', mpidr)
        f.prop('enable-method', 'wonderlantern,z30-smp')
        if n:
            f.prop('cpu-release-addr', PH['release'])
        f.end()
    f.end()

    f.begin('interrupt-controller')
    f.prop('compatible', 'arm,cortex-a7-gic')
    f.prop('#interrupt-cells', 3)
    f.prop('interrupt-controller')
    f.prop('reg', [PH['gicd'], 0x1000, PH['gicc'], 0x2000])
    f.prop('phandle', 1)
    f.end()

    f.begin('timer')
    f.prop('compatible', 'arm,armv7-timer')
    # PPIs 13, 14, 11, 10; CPU mask for 2 cores, level low.
    f.prop('interrupts', [1, 13, 0x308, 1, 14, 0x308, 1, 11, 0x308, 1, 10, 0x308])
    f.prop('clock-frequency', TIMER_HZ)
    f.prop('arm,cpu-registers-not-fw-configured')
    f.end()

    # LCD overlay plane left running by the stock firmware (YUV 4:2:2 "Y0 Y1 U V" + alpha).
    f.begin('overlay-fb')
    f.prop('compatible', 'wonderlantern,z30-overlay')
    if earlycon_only:
        # No framebuffer driver: the early console (which reads this node from the flat
        # tree, status or not) keeps the screen for the whole boot. A test of the earlycon.
        f.prop('status', 'disabled')
    f.prop('z30,overlay-color', PH['color'])
    f.prop('z30,overlay-alpha', PH['alpha'])
    f.prop('z30,width', PH['width'])
    f.prop('z30,height', PH['height'])
    f.prop('z30,color-stride', PH['cstride'])
    f.prop('z30,alpha-stride', PH['astride'])
    f.end()

    # Milbeaut SDHCI 3.0 card slot (0x2A100000, GIC 230 = SPI 198). Stock runs HS 50 MHz
    # with DIV=0 (SD0_CLOCKCTL=0x000D0003), so BCLK = 50 MHz -> core_clk = 200 MHz.
    # Keep 3.3 V DS/HS (no-1-8-v), ignore WP pin, and skip SDIO/eMMC probes on card slot.
    f.begin('sd-clk')
    f.prop('compatible', 'fixed-clock')
    f.prop('#clock-cells', 0)
    f.prop('clock-frequency', 200_000_000)
    f.prop('phandle', 2)
    f.end()

    f.begin('mmc@2a100000')
    f.prop('compatible', 'socionext,milbeaut-m10v-sdhci-3.0')
    if no_mmc:
        f.prop('status', 'disabled')
    f.prop('reg', [0x2a100000, 0x1000])
    f.prop('interrupts', [0, 198, 4])
    f.prop('clocks', [2, 2])
    f.prop('clock-names', ['core', 'iface'])
    f.prop('bus-width', 4)
    f.prop('max-frequency', 50_000_000)
    f.prop('no-1-8-v')
    f.prop('no-sdio')
    f.prop('no-mmc')
    f.prop('disable-wp')
    f.prop('z30,cd-inv')
    f.end()

    # Milbeaut SDHCI 3.0 SDIO slot (0x2A200000, GIC 233 = SPI 201): BCM43455 / Murata 1MW Wi-Fi.
    # sdhci-milbeaut holds ARM926 in reset (0x0428105C bit 0) and enables SD1 clock (0x0428123C bit 6).
    f.begin('mmc@2a200000')
    f.prop('compatible', 'socionext,milbeaut-m10v-sdhci-3.0')
    if no_mmc or no_sdio:
        f.prop('status', 'disabled')
    f.prop('reg', [0x2a200000, 0x1000])
    f.prop('interrupts', [0, 201, 4])
    f.prop('clocks', [2, 2])
    f.prop('clock-names', ['core', 'iface'])
    f.prop('bus-width', 4)
    f.prop('max-frequency', 25_000_000)
    f.prop('no-1-8-v')
    f.prop('no-sd')
    f.prop('no-mmc')
    f.prop('non-removable')
    f.prop('disable-wp')
    f.prop('fujitsu,cmd-dat-delay-select')
    f.prop('z30,sdio')
    f.end()

    # Synopsys DWC3 USB controller (0x2AA00000, GIC 236 = SPI 204; stock ISR 0x1031AD7E).
    # Z30 Type-C is USB 2.0 High-Speed; keep PHY clocks running across handoff.
    f.begin('usb@2aa00000')
    f.prop('compatible', 'snps,dwc3')
    if no_usb:
        f.prop('status', 'disabled')
    f.prop('reg', [0x2aa00000, 0x10000])
    f.prop('interrupts', [0, 204, 4])
    f.prop('dr_mode', 'peripheral')
    f.prop('maximum-speed', 'high-speed')
    f.prop('phy_type', 'utmi')
    f.prop('snps,dis_u2_susphy_quirk')
    f.prop('snps,dis_u3_susphy_quirk')
    f.prop('snps,dis-del-phy-power-chg-quirk')
    f.prop('snps,dis_rxdet_inp3_quirk')
    f.end()

    f.end()
    return f.blob()


def build_test_kernel(out):
    subprocess.run(['sh', str(ROOT / 'modules' / 'linuxboot' / 'testkernel' / 'build.sh'), str(out)], check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--kernel', type=Path, help='zImage (default: the handoff test kernel)')
    p.add_argument('--initrd', type=Path)
    p.add_argument('--earlycon-only', action='store_true',
                   help='disable the overlay framebuffer driver: the whole boot stays on the early console')
    p.add_argument('--no-mmc', action='store_true',
                   help='disable mmc@2a100000 (for z30-qemu, which has no Milbeaut SD bridge model)')
    p.add_argument('--no-usb', action='store_true',
                   help='disable usb@2aa00000 (for z30-qemu, which has no DWC3 model)')
    a = p.parse_args()
    target = a.output / 'Z30' / 'LINUX'
    target.mkdir(parents=True, exist_ok=True)
    if a.kernel:
        kernel = a.kernel.read_bytes()
        if len(kernel) < 0x30 or struct.unpack_from('<I', kernel, 0x24)[0] != 0x016f2818:
            sys.exit(f'{a.kernel} is not an ARM zImage (no 0x016F2818 magic at 0x24)')
    else:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'tk.bin'
            build_test_kernel(out)
            kernel = out.read_bytes()
    (target / 'ZIMAGE').write_bytes(kernel)
    dtb = device_tree(a.earlycon_only, a.no_mmc, a.no_usb)
    (target / 'Z30.DTB').write_bytes(dtb)
    if a.initrd:
        (target / 'INITRD').write_bytes(a.initrd.read_bytes())
    elif (target / 'INITRD').exists():
        (target / 'INITRD').unlink()
    print(f'Wrote {target}: ZIMAGE {len(kernel)} bytes ({"zImage" if a.kernel else "test kernel"}), '
          f'Z30.DTB {len(dtb)} bytes' + (f', INITRD {a.initrd.stat().st_size} bytes' if a.initrd else ''))


if __name__ == '__main__':
    main()
