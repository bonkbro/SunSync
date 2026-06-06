#!/usr/bin/env bash
#
# SunSync — start a KDE virtual monitor for Sunshine streaming.
#
# Runs as a Sunshine prep-cmd "do" command. Creates a krfb virtual monitor
# matching the resolution and frame rate requested by the Moonlight client,
# then powers off the physical displays so they don't waste electricity while
# you stream. The companion stop script restores everything.
#
# Sunshine exports SUNSHINE_CLIENT_WIDTH / _HEIGHT / _FPS into this environment.
# Requires: krfb-virtualmonitor (krfb), kscreen-doctor (libkscreen), qdbus6.
#
set -u

# --- Wayland / D-Bus session environment (Sunshine's service env is minimal) --
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
export QT_QPA_PLATFORM=wayland

NAME="sunshine-vmon"
VMON="Virtual-${NAME}"

# --- Resolution / fps from the Moonlight client (with sane fallbacks) ---------
WIDTH="${SUNSHINE_CLIENT_WIDTH:-1920}"
HEIGHT="${SUNSHINE_CLIENT_HEIGHT:-1080}"
FPS="${SUNSHINE_CLIENT_FPS%.*}"; FPS="${FPS:-60}"
FPS_MHZ=$(( FPS * 1000 ))
RES="${WIDTH}x${HEIGHT}"

STATE_DIR="${XDG_RUNTIME_DIR}/sunsync-vmon"
mkdir -p "$STATE_DIR"

# krfb-virtualmonitor always opens a network port. Use a random password so the
# virtual monitor isn't trivially reachable from the LAN. The capture path
# (capture=kwin) is local and does not need this password.
PASS="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)"
PORT=5910

# --- Keep the session awake ---------------------------------------------------
loginctl unlock-session "${XDG_SESSION_ID:-}" 2>/dev/null \
    || loginctl unlock-sessions 2>/dev/null || true
qdbus6 org.freedesktop.ScreenSaver /ScreenSaver \
    org.freedesktop.ScreenSaver.Inhibit "Sunshine" "Game streaming" \
    > "$STATE_DIR/ss-cookie" 2>/dev/null || true

# --- Create the virtual monitor ----------------------------------------------
pkill -f krfb-virtualmonitor 2>/dev/null || true
krfb-virtualmonitor --resolution "$RES" --scale 1 --name "$NAME" \
    --password "$PASS" --port "$PORT" &
echo $! > "$STATE_DIR/vmon.pid"

# Wait for KWin to register the new output.
sleep 3

kscreen-doctor "output.${VMON}.addCustomMode.${WIDTH}.${HEIGHT}.${FPS_MHZ}.full" 2>/dev/null || true
kscreen-doctor "output.${VMON}.enable"            2>/dev/null || true
kscreen-doctor "output.${VMON}.mode.${RES}@${FPS}" 2>/dev/null || true
kscreen-doctor "output.${VMON}.priority.1"        2>/dev/null || true

# Let KWin finish reloading its config after the new output.
sleep 4

# --- Power off the physical displays ------------------------------------------
# Auto-detect every enabled + connected output (except the virtual one) so this
# works on any machine, not just one with DP-1/DP-2. Record them for restore.
: > "$STATE_DIR/disabled-outputs"
while IFS= read -r out; do
    [ -n "$out" ] || continue
    [ "$out" = "$VMON" ] && continue
    echo "$out" >> "$STATE_DIR/disabled-outputs"
    kscreen-doctor "output.${out}.disable" 2>/dev/null || true
done < <(
    kscreen-doctor -o 2>/dev/null \
        | sed 's/\x1b\[[0-9;]*m//g' \
        | awk '
            $1=="Output:"   { if (name && en && conn) print name; name=$3; en=0; conn=0; next }
            $1=="enabled"   { en=1 }
            $1=="connected" { conn=1 }
            END             { if (name && en && conn) print name }
        '
)

sleep 0.5
qdbus6 org.kde.KWin /KWin org.kde.KWin.minimizeAll 2>/dev/null || true
