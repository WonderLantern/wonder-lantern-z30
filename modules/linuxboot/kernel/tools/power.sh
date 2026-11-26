#!/bin/sh
# Clean shutdown / reboot wrapper for Nikon Z30 Linux (PID 1 is rc_init.sh, not busybox init).
# Installed as /sbin/z30-power with symlinks: /sbin/reboot, /sbin/poweroff, /sbin/halt, /sbin/shutdown.
set -u

prog=$(basename "$0")
action="reboot"
case "$prog" in
    poweroff|halt) action="poweroff" ;;
    reboot) action="reboot" ;;
    shutdown)
        action="poweroff"
        for arg in "$@"; do
            case "$arg" in
                -r|--reboot) action="reboot" ;;
                -h|-H|-P|--halt|--poweroff) action="poweroff" ;;
            esac
        done
        ;;
esac

msg() {
    echo "$1"
    [ -w /dev/tty0 ] && printf '\033[2J\033[H\n  %s\n' "$1" > /dev/tty0 2>/dev/null || true
}

# Pause the LCD dashboard so our shutdown status stays visible.
touch /tmp/dash.pause 2>/dev/null || true
killall z30-dash 2>/dev/null || true

if [ "$action" = "poweroff" ]; then
    msg "Powering off Nikon Z30 (syncing SD & resetting via SP805)..."
else
    msg "Rebooting Nikon Z30 back to stock firmware..."
fi

# Stop network helpers cleanly.
killall wpa_supplicant udhcpc 2>/dev/null || true

# Flush buffers and safely unmount SD card before hardware reset.
sync
if grep -q ' /mnt/sd ' /proc/mounts 2>/dev/null; then
    mount -o remount,ro /mnt/sd 2>/dev/null || true
    umount /mnt/sd 2>/dev/null || umount -l /mnt/sd 2>/dev/null || true
fi
sync
usleep 100000 2>/dev/null || true

if [ "$action" = "poweroff" ]; then
    exec /bin/busybox poweroff -f
else
    exec /bin/busybox reboot -f
fi
