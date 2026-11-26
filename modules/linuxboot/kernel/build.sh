#!/bin/sh
# Build the Z30 Linux demo kernel (zImage with an embedded initramfs). Runs in WSL/Linux.
#
#   sh modules/linuxboot/kernel/build.sh SRC TOOLCHAIN_PREFIX OUT [BUSYBOX_SRC]
#
# SRC: a Linux source tree (tested: 6.18.65); TOOLCHAIN_PREFIX: a soft-float ARM
# Linux compiler prefix (tested: Bootlin armv5-eabi--musl--stable-2026.08-1,
# .../bin/arm-buildroot-linux-musleabi-); OUT: build directory. Writes OUT/zImage.
# BUSYBOX_SRC: optional BusyBox source dir (default: sibling busybox-1.37.0 of SRC).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
src=$1 cross=$2 out=$3
bb_src=${4:-"$(dirname "$src")/busybox-1.37.0"}
mkdir -p "$out"
out=$(cd "$out" && pwd)

# 1. Driver into the tree.
for f in z30-overlay-fb.c z30-earlycon.c z30-smp.c z30-overlay.h; do cp "$here/$f" "$src/drivers/video/fbdev/$f"; done
if ! grep -q z30-smp "$src/drivers/video/fbdev/Makefile"; then
  # Older trees of this script had the driver only: start the entries over.
  sed -i '/FB_Z30_OVERLAY/d' "$src/drivers/video/fbdev/Makefile"
  sed -i '/^config FB_Z30_OVERLAY$/,$d' "$src/drivers/video/fbdev/Kconfig"
fi
if ! grep -q FB_Z30_OVERLAY "$src/drivers/video/fbdev/Kconfig"; then
  cat >> "$src/drivers/video/fbdev/Kconfig" <<'EOF'

config FB_Z30_OVERLAY
	bool "Nikon Z30 LCD overlay framebuffer (Wonder Lantern)"
	depends on FB && OF && ARM && MMU
	select FB_SYSMEM_HELPERS_DEFERRED
	select SERIAL_CORE
	select SERIAL_CORE_CONSOLE
	select SERIAL_EARLYCON
	help
	  RGB565 framebuffer shown through the LCD overlay plane that the
	  camera firmware leaves running when the Wonder Lantern handoff
	  starts Linux, the "earlycon=z30ovl" early console on the same
	  plane, and the Nikon Z30 machine descriptor.
EOF
  echo 'obj-$(CONFIG_FB_Z30_OVERLAY) += z30-overlay-fb.o z30-earlycon.o' >> "$src/drivers/video/fbdev/Makefile"
  echo 'ifeq ($(CONFIG_FB_Z30_OVERLAY),y)' >> "$src/drivers/video/fbdev/Makefile"
  echo 'obj-$(CONFIG_SMP) += z30-smp.o' >> "$src/drivers/video/fbdev/Makefile"
  echo 'endif' >> "$src/drivers/video/fbdev/Makefile"
fi

# 1b. Milbeaut SDHCI quirks for Nikon Z30 SD0 (card slot) and SD1 (SDIO Wi-Fi):
#  - Enable SoC clock gate in CRG (0x0428100C divider + 0x0428123C W1C gate):
#      SD0 (z30,cd-inv): CRG+0x00C[20:16]=0x11 (50 MHz), CRG+0x23C=0x10 (bit 4)
#      SD1 (z30,sdio):   hold ARM926 in reset (CRG+0x05C bit 0 = 1),
#                        if WL_REG_ON (GPIO 0x5b = 0x04282014 bit 14) is HIGH, pulse LOW 100ms -> HIGH 200ms
#                        to cleanly hard-reset BCM43455 into 1-bit SDIO init state,
#                        CRG+0x00C[28:24]=0x11 (50 MHz), CRG+0x23C=0x40 (bit 6)
#  - MLB_WP_CD_LED_SET: SD0 needs BIT(1) (0x6); SD1 needs 0x00030204 (stock 0x102AA29E)
#  - MLB_CDR_SET: both SD0 and SD1 use 1 (stock 0x102AA298 / 0x102AA2FC)
if ! grep -q 'z30-wl-reset' "$src/drivers/mmc/host/sdhci-milbeaut.c"; then
  python3 - "$src/drivers/mmc/host/sdhci-milbeaut.c" <<'PYEOF'
import sys
from pathlib import Path

p = Path(sys.argv[1])
s = p.read_text()

# Replace MLB_CDR_SET line (handles both pristine upstream and previous z30,cd-inv patch)
for old in (
    '\tsdhci_writel(host, MLB_CDR_SET_CLK2POW16, MLB_CDR_SET);',
    '\tsdhci_writel(host, device_property_read_bool(((struct f_sdhost_priv *)sdhci_priv(host))->dev, "z30,cd-inv") ? 1 : MLB_CDR_SET_CLK2POW16, MLB_CDR_SET);',
):
    if old in s:
        s = s.replace(
            old,
            '\t{ struct device *zdev = ((struct f_sdhost_priv *)sdhci_priv(host))->dev;\n'
            '\t  bool z30 = device_property_read_bool(zdev, "z30,cd-inv") || device_property_read_bool(zdev, "z30,sdio");\n'
            '\t  sdhci_writel(host, z30 ? 1 : MLB_CDR_SET_CLK2POW16, MLB_CDR_SET); }',
            1,
        )
        break

# Replace MLB_WP_CD_LED_SET line
for old in (
    '\tsdhci_writel(host, MLB_WP_CD_LED_SET_LED_INV, MLB_WP_CD_LED_SET);',
    '\tsdhci_writel(host, MLB_WP_CD_LED_SET_LED_INV | (device_property_read_bool(((struct f_sdhost_priv *)sdhci_priv(host))->dev, "z30,cd-inv") ? BIT(1) : 0), MLB_WP_CD_LED_SET);',
):
    if old in s:
        s = s.replace(
            old,
            '\t{ struct device *zdev = ((struct f_sdhost_priv *)sdhci_priv(host))->dev;\n'
            '\t  u32 wp_cd = MLB_WP_CD_LED_SET_LED_INV;\n'
            '\t  if (device_property_read_bool(zdev, "z30,cd-inv")) wp_cd |= BIT(1);\n'
            '\t  if (device_property_read_bool(zdev, "z30,sdio")) wp_cd |= 0x00030200u;\n'
            '\t  sdhci_writel(host, wp_cd, MLB_WP_CD_LED_SET); }',
            1,
        )
        break

# Replace CRG clock/reset hook before sdhci_milbeaut_init(host)
crg_hook = (
    '\tif (device_property_read_bool(dev, "z30,cd-inv") || device_property_read_bool(dev, "z30,sdio")) {\n'
    '\t\tvoid __iomem *crg = ioremap(0x04281000, 0x1300);\n'
    '\t\tif (crg) {\n'
    '\t\t\tu32 v = readl(crg + 0x00c);\n'
    '\t\t\tif (device_property_read_bool(dev, "z30,cd-inv")) {\n'
    '\t\t\t\tv = (v & ~0x001f0000u) | 0x00110000u;\n'
    '\t\t\t\twritel(v, crg + 0x00c);\n'
    '\t\t\t\twritel(0x10u, crg + 0x23c);\n'
    '\t\t\t}\n'
    '\t\t\tif (device_property_read_bool(dev, "z30,sdio")) {\n'
    '\t\t\t\t/* z30-wl-reset: stop ARM926, pulse WL_REG_ON if powered, enable SD1 clock */\n'
    '\t\t\t\twritel(readl(crg + 0x05c) | 1u, crg + 0x05c);\n'
    '\t\t\t\tif (readl(crg + 0x1014) & BIT(14)) {\n'
    '\t\t\t\t\twritel(0x40000000u, crg + 0x1014);\n'
    '\t\t\t\t\tmsleep(100);\n'
    '\t\t\t\t\twritel(0x40004000u, crg + 0x1014);\n'
    '\t\t\t\t\tmsleep(200);\n'
    '\t\t\t\t}\n'
    '\t\t\t\tv = (v & ~0x1f000000u) | 0x11000000u;\n'
    '\t\t\t\twritel(v, crg + 0x00c);\n'
    '\t\t\t\twritel(0x40u, crg + 0x23c);\n'
    '\t\t\t\tudelay(100);\n'
    '\t\t\t}\n'
    '\t\t\tiounmap(crg);\n'
    '\t\t}\n'
    '\t}\n'
    '\tsdhci_milbeaut_init(host);'
)
old_v1 = (
    '\tif (device_property_read_bool(dev, "z30,cd-inv") || device_property_read_bool(dev, "z30,sdio")) {\n'
    '\t\tvoid __iomem *crg = ioremap(0x04281000, 0x300);\n'
    '\t\tif (crg) {\n'
    '\t\t\tu32 v = readl(crg + 0x00c);\n'
    '\t\t\tif (device_property_read_bool(dev, "z30,cd-inv")) {\n'
    '\t\t\t\tv = (v & ~0x001f0000u) | 0x00110000u;\n'
    '\t\t\t\twritel(v, crg + 0x00c);\n'
    '\t\t\t\twritel(0x10u, crg + 0x23c);\n'
    '\t\t\t}\n'
    '\t\t\tif (device_property_read_bool(dev, "z30,sdio")) {\n'
    '\t\t\t\twritel(readl(crg + 0x05c) | 1u, crg + 0x05c);\n'
    '\t\t\t\tv = (v & ~0x1f000000u) | 0x11000000u;\n'
    '\t\t\t\twritel(v, crg + 0x00c);\n'
    '\t\t\t\twritel(0x40u, crg + 0x23c);\n'
    '\t\t\t\tudelay(100);\n'
    '\t\t\t}\n'
    '\t\t\tiounmap(crg);\n'
    '\t\t}\n'
    '\t}\n'
    '\tsdhci_milbeaut_init(host);'
)
old_crg = '\tif (device_property_read_bool(dev, "z30,cd-inv")) { void __iomem *crg = ioremap(0x04281000, 0x300); if (crg) { writel(0x00110000, crg + 0x00c); writel(0x10, crg + 0x23c); iounmap(crg); } }\n\tsdhci_milbeaut_init(host);'
if old_v1 in s:
    s = s.replace(old_v1, crg_hook, 1)
elif old_crg in s:
    s = s.replace(old_crg, crg_hook, 1)
elif '\tsdhci_milbeaut_init(host);' in s:
    s = s.replace('\tsdhci_milbeaut_init(host);', crg_hook, 1)

p.write_text(s)
PYEOF
fi

# 1b2. For z30,sdio (Murata 1MW / BCM43455, 1.8V VIO): stock ARM926 SD1_Set_Bus_Power
#      (0xc100d1a0) sets BOTH F_SDH30_MSEL_O_1_8 (0x2a200114 bit 18) AND SDHCI_CTRL_VDD_180
#      (0x2a20003e bit 3) before CMD5 while keeping SDHCI_POWER_330 (0x0f).
if ! grep -q 'z30-sdio-vdd180' "$src/drivers/mmc/host/sdhci-milbeaut.c"; then
  python3 - "$src/drivers/mmc/host/sdhci-milbeaut.c" <<'PYEOF'
import sys
from pathlib import Path

p = Path(sys.argv[1])
s = p.read_text()
old_ops = "\t.set_clock = sdhci_set_clock,"
new_ops = "\t.set_clock = sdhci_milbeaut_set_clock,"
fn = (
    "/* z30-sdio-vdd180: enforce 1.8V signaling (MSEL_O_1_8 + SDHCI_CTRL_VDD_180) on SD1 */\n"
    "static void sdhci_milbeaut_set_clock(struct sdhci_host *host, unsigned int clock)\n"
    "{\n"
    "\tstruct f_sdhost_priv *priv = sdhci_priv(host);\n"
    "\tsdhci_set_clock(host, clock);\n"
    "\tif (clock && device_property_read_bool(priv->dev, \"z30,sdio\")) {\n"
    "\t\tu32 io2 = sdhci_readl(host, F_SDH30_IO_CONTROL2);\n"
    "\t\tsdhci_writel(host, io2 | F_SDH30_MSEL_O_1_8, F_SDH30_IO_CONTROL2);\n"
    "\t\tsdhci_writew(host, sdhci_readw(host, SDHCI_HOST_CONTROL2) | SDHCI_CTRL_VDD_180,\n"
    "\t\t\t     SDHCI_HOST_CONTROL2);\n"
    "\t}\n"
    "}\n\n"
    "static const struct sdhci_ops sdhci_milbeaut_ops = {"
)
assert old_ops in s, ".set_clock = sdhci_set_clock not found"
s = s.replace("static const struct sdhci_ops sdhci_milbeaut_ops = {", fn, 1)
s = s.replace(old_ops, new_ops, 1)
p.write_text(s)
PYEOF
fi

# 1c. Brief SDIO attach diagnostic print (CMD5 err / ocr / rocr / init_card err)
if ! grep -q 'z30-sdio-dbg' "$src/drivers/mmc/core/sdio.c"; then
  python3 - "$src/drivers/mmc/core/sdio.c" <<'PYEOF'
import sys
from pathlib import Path

p = Path(sys.argv[1])
s = p.read_text()
old = """\terr = mmc_send_io_op_cond(host, 0, &ocr);
\tif (err)
\t\treturn err;"""
new = """\terr = mmc_send_io_op_cond(host, 0, &ocr);
\tpr_info("%s: z30-sdio-dbg CMD5 err=%d ocr=0x%08x avail=0x%08x\\n", mmc_hostname(host), err, ocr, host->ocr_avail);
\tif (err)
\t\treturn err;"""
assert old in s, "mmc_send_io_op_cond block not found"
s = s.replace(old, new, 1)

old2 = """\terr = mmc_sdio_init_card(host, rocr, NULL);
\tif (err)
\t\tgoto err;"""
new2 = """\terr = mmc_sdio_init_card(host, rocr, NULL);
\tpr_info("%s: z30-sdio-dbg init_card rocr=0x%08x err=%d\\n", mmc_hostname(host), rocr, err);
\tif (err)
\t\tgoto err;"""
assert old2 in s, "mmc_sdio_init_card block not found"
s = s.replace(old2, new2, 1)
p.write_text(s)
PYEOF
fi

# 1d. Brief brcmf_attach breadcrumbs to trace wlan0 creation
if ! grep -q 'z30-brcm-step' "$src/drivers/net/wireless/broadcom/brcm80211/brcmfmac/core.c"; then
  python3 - "$src/drivers/net/wireless/broadcom/brcm80211/brcmfmac/core.c" <<'PYEOF'
import sys
from pathlib import Path

p = Path(sys.argv[1])
s = p.read_text()
old = """\t/* Bus is ready, do any initialization */
\tret = brcmf_c_preinit_dcmds(ifp);
\tif (ret < 0)
\t\tgoto fail;

\tbrcmf_feat_attach(drvr);

\tret = brcmf_proto_init_done(drvr);
\tif (ret < 0)
\t\tgoto fail;

\tbrcmf_proto_add_if(drvr, ifp);

\tdrvr->config = brcmf_cfg80211_attach(drvr, ops,
\t\t\t\t\t     drvr->settings->p2p_enable);
\tif (drvr->config == NULL) {
\t\tret = -ENOMEM;
\t\tgoto fail;
\t}

\tret = brcmf_net_attach(ifp, false);"""
new = """\t/* Bus is ready, do any initialization */
\tret = brcmf_c_preinit_dcmds(ifp);
\tpr_info("z30-brcm-step: preinit=%d\\n", ret);
\tif (ret < 0)
\t\tgoto fail;

\tbrcmf_feat_attach(drvr);
\tpr_info("z30-brcm-step: feat done\\n");

\tret = brcmf_proto_init_done(drvr);
\tpr_info("z30-brcm-step: proto=%d\\n", ret);
\tif (ret < 0)
\t\tgoto fail;

\tbrcmf_proto_add_if(drvr, ifp);

\tdrvr->config = brcmf_cfg80211_attach(drvr, ops,
\t\t\t\t\t     drvr->settings->p2p_enable);
\tpr_info("z30-brcm-step: cfg80211=%p\\n", drvr->config);
\tif (drvr->config == NULL) {
\t\tret = -ENOMEM;
\t\tgoto fail;
\t}

\tret = brcmf_net_attach(ifp, false);
\tpr_info("z30-brcm-step: net_attach=%d\\n", ret);"""
assert old in s, "brcmf_attach block not found"
s = s.replace(old, new, 1)
p.write_text(s)
PYEOF
fi

# 2. Userspace: BusyBox (static musl, soft float) + showcase tools.
mkdir -p "$out/bin"
"${cross}gcc" -static -O2 -Wall -Wextra -o "$out/bin/htop" "$here/tools/htop.c"
"${cross}gcc" -static -O2 -Wall -Wextra -o "$out/bin/cmatrix" "$here/tools/cmatrix.c"
"${cross}gcc" -static -O2 -Wall -Wextra -o "$out/bin/tty-clock" "$here/tools/tty_clock.c"
"${cross}gcc" -static -O2 -Wall -Wextra -o "$out/bin/z30-mandel" "$here/init.c"
"${cross}gcc" -static -O2 -Wall -Wextra -o "$out/bin/z30-dash" "$here/tools/dashboard.c"
"${cross}strip" "$out/bin/htop" "$out/bin/cmatrix" "$out/bin/tty-clock" "$out/bin/z30-mandel" "$out/bin/z30-dash"
tr -d '\r' < "$here/tools/neofetch.sh" > "$out/bin/neofetch"
tr -d '\r' < "$here/tools/rc_init.sh" > "$out/rc_init.sh"
tr -d '\r' < "$here/tools/udhcpc.script" > "$out/udhcpc.script"
tr -d '\r' < "$here/tools/power.sh" > "$out/power.sh"
chmod 755 "$out/bin/neofetch" "$out/rc_init.sh" "$out/udhcpc.script" "$out/power.sh"

cat > "$out/initramfs.list" <<EOF
dir /dev 755 0 0
nod /dev/console 600 0 0 c 5 1
nod /dev/null 666 0 0 c 1 3
dir /etc 755 0 0
slink /etc/resolv.conf /tmp/resolv.conf 777 0 0
dir /proc 755 0 0
dir /sys 755 0 0
dir /tmp 1777 0 0
dir /mnt 755 0 0
dir /mnt/sd 755 0 0
dir /root 700 0 0
dir /bin 755 0 0
dir /sbin 755 0 0
dir /usr 755 0 0
dir /usr/bin 755 0 0
dir /usr/sbin 755 0 0
dir /usr/share 755 0 0
dir /usr/share/udhcpc 755 0 0
file /usr/share/udhcpc/default.script $out/udhcpc.script 755 0 0
dir /lib 755 0 0
dir /lib/firmware 755 0 0
dir /lib/firmware/brcm 755 0 0
EOF

# Include prebuilt static soft-float wpa_supplicant / wpa_cli if present in $out/bin.
if [ -x "$out/bin/wpa_supplicant" ]; then
  echo "file /bin/wpa_supplicant $out/bin/wpa_supplicant 755 0 0" >> "$out/initramfs.list"
fi
if [ -x "$out/bin/wpa_cli" ]; then
  echo "file /bin/wpa_cli $out/bin/wpa_cli 755 0 0" >> "$out/initramfs.list"
fi

# Include extracted BCM43455 Wi-Fi firmware & Nikon NVRAM if present.
fw_dir="$here/../../../build/linux/firmware/brcm"
if [ -f "$fw_dir/brcmfmac43455-sdio.bin" ] && [ -f "$fw_dir/brcmfmac43455-sdio.txt" ]; then
  cp "$fw_dir/brcmfmac43455-sdio.bin" "$out/brcmfmac43455-sdio.bin"
  cp "$fw_dir/brcmfmac43455-sdio.txt" "$out/brcmfmac43455-sdio.txt"
  cat >> "$out/initramfs.list" <<EOF
file /lib/firmware/brcm/brcmfmac43455-sdio.bin $out/brcmfmac43455-sdio.bin 644 0 0
file /lib/firmware/brcm/brcmfmac43455-sdio.txt $out/brcmfmac43455-sdio.txt 644 0 0
EOF
fi

if [ -d "$bb_src" ]; then
  bb_out="$out/busybox-obj"
  bb_inst="$out/busybox-install"
  if [ ! -x "$bb_inst/bin/busybox" ]; then
    mkdir -p "$bb_out" "$bb_inst"
    make -C "$bb_src" O="$bb_out" ARCH=arm CROSS_COMPILE="$cross" defconfig >/dev/null
    # Static musl soft-float build; disable Linux 6.8+ removed CBQ tc & HW SHA accel.
    sed -i \
      -e 's/^# CONFIG_STATIC is not set$/CONFIG_STATIC=y/' \
      -e 's/^CONFIG_TC=y$/# CONFIG_TC is not set/' \
      -e 's/^CONFIG_SHA1_HWACCEL=y$/# CONFIG_SHA1_HWACCEL is not set/' \
      -e 's/^CONFIG_SHA256_HWACCEL=y$/# CONFIG_SHA256_HWACCEL is not set/' \
      -e 's/^CONFIG_FEATURE_UTMP=y$/# CONFIG_FEATURE_UTMP is not set/' \
      -e 's/^CONFIG_FEATURE_WTMP=y$/# CONFIG_FEATURE_WTMP is not set/' \
      "$bb_out/.config"
    make -C "$bb_src" O="$bb_out" ARCH=arm CROSS_COMPILE="$cross" oldconfig </dev/null >/dev/null
    make -C "$bb_src" O="$bb_out" ARCH=arm CROSS_COMPILE="$cross" -j"$(nproc)" busybox
    make -C "$bb_src" O="$bb_out" ARCH=arm CROSS_COMPILE="$cross" CONFIG_PREFIX="$bb_inst" install >/dev/null
    "${cross}strip" "$bb_inst/bin/busybox"
  fi
  echo "file /bin/busybox $bb_inst/bin/busybox 755 0 0" >> "$out/initramfs.list"
  for d in bin sbin usr/bin usr/sbin; do
    [ -d "$bb_inst/$d" ] || continue
    for link in "$bb_inst/$d"/*; do
      [ -L "$link" ] || continue
      name=$(basename "$link")
      [ "$name" = "busybox" ] && continue
      case "$d/$name" in
        sbin/reboot|sbin/poweroff|sbin/halt|sbin/shutdown) continue ;;
      esac
      echo "slink /$d/$name /bin/busybox 777 0 0" >> "$out/initramfs.list"
    done
  done
  cat >> "$out/initramfs.list" <<EOF
file /init $out/rc_init.sh 755 0 0
file /sbin/z30-power $out/power.sh 755 0 0
slink /sbin/reboot /sbin/z30-power 777 0 0
slink /sbin/poweroff /sbin/z30-power 777 0 0
slink /sbin/halt /sbin/z30-power 777 0 0
slink /sbin/shutdown /sbin/z30-power 777 0 0
file /bin/neofetch $out/bin/neofetch 755 0 0
file /bin/htop $out/bin/htop 755 0 0
file /bin/cmatrix $out/bin/cmatrix 755 0 0
file /bin/tty-clock $out/bin/tty-clock 755 0 0
file /bin/z30-mandel $out/bin/z30-mandel 755 0 0
file /bin/z30-dash $out/bin/z30-dash 755 0 0
EOF
else
  echo "file /init $out/bin/z30-mandel 755 0 0" >> "$out/initramfs.list"
fi

# 3. Kernel: tinyconfig + the Z30 fragment.
make -C "$src" O="$out/obj" ARCH=arm CROSS_COMPILE="$cross" tinyconfig >/dev/null
tr -d '\r' < "$here/z30.config" > "$out/z30.config"
echo "CONFIG_INITRAMFS_SOURCE=\"$out/initramfs.list\"" >> "$out/z30.config"
"$src/scripts/kconfig/merge_config.sh" -m -O "$out/obj" "$out/obj/.config" "$out/z30.config" >/dev/null
make -C "$src" O="$out/obj" ARCH=arm CROSS_COMPILE="$cross" olddefconfig >/dev/null
# Every fragment line must have survived Kconfig's dependency resolution.
missing=0
while IFS= read -r line; do
  case "$line" in
    CONFIG_*=*) grep -qx "$line" "$out/obj/.config" || { echo "not set: $line" >&2; missing=1; } ;;
    "# CONFIG_"*" is not set") grep -qx "$line" "$out/obj/.config" || grep -q "^${line#\# }" "$out/obj/.config" && \
        { grep -q "^$(echo "$line" | sed 's/# \(CONFIG_[A-Z0-9_]*\) is not set/\1=/')" "$out/obj/.config" && { echo "still set: $line" >&2; missing=1; }; } || true ;;
  esac
done < "$out/z30.config"
[ "$missing" = 0 ] || exit 1
make -C "$src" O="$out/obj" ARCH=arm CROSS_COMPILE="$cross" -j"$(nproc)" zImage
cp "$out/obj/arch/arm/boot/zImage" "$out/zImage"
ls -l "$out/zImage"
