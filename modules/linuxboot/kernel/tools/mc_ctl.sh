#!/bin/sh
# Manage bareiron Minecraft 26.3 server on Nikon Z30 Linux.
MC_BIN="/bin/bareiron"
[ -x /tmp/bareiron ] && MC_BIN="/tmp/bareiron"
MC_DIR="/tmp/mc"
MC_LOG="/tmp/mc.log"
MC_PID="/tmp/mc.pid"
MC_STATUS="/tmp/mc_status"

is_running() {
    [ -r "$MC_PID" ] || return 1
    pid=$(cat "$MC_PID" 2>/dev/null)
    [ -n "$pid" ] && [ -d "/proc/$pid" ]
}

case "${1:-status}" in
    start)
        if is_running; then
            echo "bareiron already running (PID $(cat "$MC_PID"))"
            exit 0
        fi
        if [ ! -x "$MC_BIN" ]; then
            echo "bareiron binary not found at $MC_BIN"
            exit 1
        fi
        mkdir -p "$MC_DIR"
        : > "$MC_LOG"
        (cd "$MC_DIR" && exec "$MC_BIN" >> "$MC_LOG" 2>&1) &
        echo $! > "$MC_PID"
        echo "bareiron started (PID $!, port 25565, dir $MC_DIR)"
        ;;
    stop)
        if is_running; then
            pid=$(cat "$MC_PID")
            kill "$pid" 2>/dev/null || true
            sleep 1
            [ -d "/proc/$pid" ] && kill -9 "$pid" 2>/dev/null || true
            rm -f "$MC_PID" "$MC_STATUS"
            echo "bareiron stopped"
        else
            rm -f "$MC_PID" "$MC_STATUS"
            echo "bareiron is not running"
        fi
        ;;
    restart)
        "$0" stop
        "$0" start
        ;;
    status)
        if is_running; then
            echo "bareiron running (PID $(cat "$MC_PID"))"
            [ -r "$MC_STATUS" ] && cat "$MC_STATUS"
        else
            echo "bareiron stopped"
        fi
        ;;
    log)
        [ -r "$MC_LOG" ] && tail -n 30 "$MC_LOG" || echo "No log yet"
        ;;
    *)
        echo "Usage: mc-ctl [start|stop|restart|status|log]"
        exit 1
        ;;
esac
