#!/bin/sh
# Toggle BCM43455 (Murata 1MW) Wi-Fi on Nikon Z30 Linux without relying on stock menu.
# The EX MCU wireless power rail is enabled once during linuxboot handoff; this script
# controls WL_REG_ON (GPIO 0x5b = 0x04282014 bit 14) and SD1 (2a200000.mmc) bind/unbind.
set -u

SD1_DEV="2a200000.mmc"
SD1_DRV="/sys/bus/platform/drivers/sdhci-milbeaut"
WL_GPIO_DATA="0x04282014"
WL_CONF="/mnt/sd/Z30/LINUX/WIFI.CONF"

wl_reg_on_state() {
    val=$(devmem "$WL_GPIO_DATA" 32 2>/dev/null || echo "0x0")
    if [ $((val & 0x4000)) -ne 0 ]; then
        echo "HIGH"
    else
        echo "LOW"
    fi
}

wifi_off() {
    killall wpa_supplicant udhcpc 2>/dev/null || true
    ip link set wlan0 down 2>/dev/null || true
    if [ -e "$SD1_DRV/$SD1_DEV" ]; then
        echo "$SD1_DEV" > "$SD1_DRV/unbind" 2>/dev/null || true
    fi
    # Pull WL_REG_ON LOW (hardware shutdown, ~15 uA)
    devmem "$WL_GPIO_DATA" 32 0x40000000 >/dev/null 2>&1 || true
    echo "Wi-Fi OFF (WL_REG_ON=$(wl_reg_on_state))"
}

wifi_on() {
    if [ ! -e "$SD1_DRV/$SD1_DEV" ]; then
        echo "$SD1_DEV" > "$SD1_DRV/bind"
    fi
    i=0
    while [ ! -d /sys/class/net/wlan0 ] && [ "$i" -lt 15 ]; do
        sleep 1
        i=$((i + 1))
    done
    if [ ! -d /sys/class/net/wlan0 ]; then
        echo "Wi-Fi ON failed: wlan0 did not appear (check dmesg)" >&2
        exit 1
    fi
    ip link set wlan0 up 2>/dev/null || true
    if [ -r "$WL_CONF" ] && [ -x /bin/wpa_supplicant ]; then
        killall wpa_supplicant udhcpc 2>/dev/null || true
        tr -d '\r' < "$WL_CONF" > /tmp/wpa.conf
        /bin/wpa_supplicant -B -D wext -i wlan0 -c /tmp/wpa.conf >/tmp/wpa.log 2>&1
        /sbin/udhcpc -i wlan0 -s /usr/share/udhcpc/default.script -b >/tmp/udhcpc.log 2>&1
        echo "Wi-Fi ON (wlan0 up, connecting via $WL_CONF)"
    else
        echo "Wi-Fi ON (wlan0 up; no $WL_CONF)"
    fi
}

wifi_status() {
    bound="no"
    [ -e "$SD1_DRV/$SD1_DEV" ] && bound="yes"
    echo "WL_REG_ON : $(wl_reg_on_state)"
    echo "SD1 bound : $bound"
    if [ -d /sys/class/net/wlan0 ]; then
        ip -4 addr show dev wlan0 2>/dev/null || ifconfig wlan0 2>/dev/null
    else
        echo "wlan0     : absent"
    fi
}

case "${1:-status}" in
    on|start)   wifi_on ;;
    off|stop)   wifi_off ;;
    restart)    wifi_off; sleep 1; wifi_on ;;
    status)     wifi_status ;;
    *)          echo "Usage: wifi-ctl [on|off|restart|status]"; exit 1 ;;
esac
