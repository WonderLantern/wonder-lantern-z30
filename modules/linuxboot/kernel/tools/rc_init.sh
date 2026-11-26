#!/bin/sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
export HOME=/root
export TERM=linux

trap 'exec /sbin/reboot' TERM
trap 'exec /sbin/poweroff' USR1 USR2

mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/pts
mount -t devpts devpts /dev/pts 2>/dev/null || true
mount -t tmpfs tmpfs /tmp
mount -t debugfs debugfs /sys/kernel/debug 2>/dev/null || true
mkdir -p /mnt/sd

# Wait briefly for Milbeaut SDHCI (mmcblk0) to enumerate, then mount read-only
# so pulling the battery to return to stock firmware can never corrupt the card.
sd_dev=""
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if [ -b /dev/mmcblk0p1 ]; then
        sd_dev=/dev/mmcblk0p1
        break
    elif [ -b /dev/mmcblk0 ]; then
        sd_dev=/dev/mmcblk0
    fi
    usleep 200000 2>/dev/null || sleep 1
done

if [ -n "$sd_dev" ]; then
    mount -o ro "$sd_dev" /mnt/sd 2>/dev/null || \
    mount -t vfat -o ro "$sd_dev" /mnt/sd 2>/dev/null || \
    mount -t exfat -o ro "$sd_dev" /mnt/sd 2>/dev/null || true
fi

# Start an interactive root shell on the USB CDC ACM serial gadget (/dev/ttyGS0)
# whenever the Synopsys DWC3 controller is bound and a host opens the port.
(
    while true; do
        if [ -c /dev/ttyGS0 ]; then
            /sbin/getty -L 115200 ttyGS0 vt100 -n -l /bin/sh
        fi
        sleep 1
    done
) &

# Bring up loopback and auto-connect Wi-Fi in background if wlan0 + WIFI.CONF exist.
ip link set lo up 2>/dev/null || true
(
    now_s() { read u _ < /proc/uptime; echo "${u%.*}"; }
    T0=$(now_s)
    while [ ! -d /sys/class/net/wlan0 ]; do
        [ $(($(now_s) - T0)) -ge 15 ] && break
        sleep 1 2>/dev/null || true
    done
    if [ -d /sys/class/net/wlan0 ] && [ -r /mnt/sd/Z30/LINUX/WIFI.CONF ] && [ -x /bin/wpa_supplicant ]; then
        tr -d '\r' < /mnt/sd/Z30/LINUX/WIFI.CONF > /tmp/wpa.conf
        ip link set wlan0 up 2>/dev/null || true
        /bin/wpa_supplicant -B -D wext -i wlan0 -c /tmp/wpa.conf >/tmp/wpa.log 2>&1
        /sbin/udhcpc -i wlan0 -s /usr/share/udhcpc/default.script -b >/tmp/udhcpc.log 2>&1
        /usr/sbin/telnetd -l /bin/sh 2>/dev/null || /bin/busybox telnetd -l /bin/sh 2>/dev/null || true
    fi
) &

# Allow an optional user script on the SD card to override the dashboard.
if [ -r /mnt/sd/Z30/LINUX/AUTORUN.SH ]; then
    /bin/sh /mnt/sd/Z30/LINUX/AUTORUN.SH
fi

# Run the real-time LCD system dashboard; create /tmp/dash.pause from USB shell to pause it.
while true; do
    if [ ! -e /tmp/dash.pause ]; then
        /bin/z30-dash
    fi
    sleep 1
done
