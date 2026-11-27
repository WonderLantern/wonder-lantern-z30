#!/bin/sh
# Switch Nikon Z30 LCD dashboard page (sys / mc / toggle) via telnet or USB shell.
MODE_FILE=/tmp/dash.mode

cur="sys"
[ -r "$MODE_FILE" ] && read -r cur < "$MODE_FILE"

case "${1:-toggle}" in
    mc|minecraft)
        echo "mc" > "$MODE_FILE"
        echo "Dashboard switched to: Minecraft Server (mc)"
        ;;
    sys|system)
        echo "sys" > "$MODE_FILE"
        echo "Dashboard switched to: System Monitor (sys)"
        ;;
    toggle|"")
        if [ "$cur" = "mc" ]; then
            echo "sys" > "$MODE_FILE"
            echo "Dashboard switched to: System Monitor (sys)"
        else
            echo "mc" > "$MODE_FILE"
            echo "Dashboard switched to: Minecraft Server (mc)"
        fi
        ;;
    status)
        echo "Current dashboard mode: $cur"
        ;;
    *)
        echo "Usage: dash [sys|mc|toggle|status]"
        exit 1
        ;;
esac
