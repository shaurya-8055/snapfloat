#!/usr/bin/env bash
# SnapFloat installer for Ubuntu/Debian.   ./install.sh  (re-run to update)   ./install.sh --uninstall
set -euo pipefail
DIR="$HOME/.local/share/snapfloat"
BIN="$HOME/.local/bin/snapfloat"
APPS="$HOME/.local/share/applications/snapfloat.desktop"
UUID="snapfloat@shaurya-8055.github.io"
LEGACY_UUID="snapfloat@snapfloat.github.io"  # the 1.0 ID
EXT="$HOME/.local/share/gnome-shell/extensions/$UUID"
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = "--uninstall" ]; then
    # the app's own uninstall is stdlib-only, so system python works even if the install's venv is already gone
    python3 "$SRC/snapfloat.py" --uninstall
    exit 0
fi

# tesseract-ocr powers "Copy text" and "Hide private info"; wl-clipboard lets a background app set the clipboard on Wayland;
# GStreamer's PipeWire source and openh264 (plugins-bad) record the screen as MP4
PKGS="python3-venv libxcb-cursor0 tesseract-ocr wl-clipboard gstreamer1.0-pipewire gstreamer1.0-plugins-bad"
if ! dpkg -s $PKGS >/dev/null 2>&1; then
    { sudo apt-get update -qq && sudo apt-get install -y $PKGS; } ||
        echo "warning: couldn't install system packages (no sudo?). Install them yourself: sudo apt install $PKGS"
fi
mkdir -p "$DIR" "$(dirname "$BIN")" "$(dirname "$APPS")"
cp "$SRC/snapfloat.py" "$DIR/"
mkdir -p "$HOME/.local/share/icons/hicolor/256x256/apps"
cp "$SRC/packaging/snapfloat.png" "$HOME/.local/share/icons/hicolor/256x256/apps/snapfloat.png"
# check pip, not python: a venv made while python3-venv was missing has python but no pip, and would stay broken
[ -x "$DIR/venv/bin/pip" ] || python3 -m venv --clear "$DIR/venv"
# PySide6 6.9 is pinned (newer versions draw no drag image on Wayland) but predates Python 3.14; its abi3 wheel runs fine there
"$DIR/venv/bin/pip" install -q --upgrade --ignore-requires-python -r "$SRC/requirements.txt"

printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$DIR/venv/bin/python" "$DIR/snapfloat.py" > "$BIN"
chmod +x "$BIN"
cat > "$APPS" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SnapFloat
Comment=Floating screenshot thumbnail you can drag into any app
Exec="$BIN"
Icon=snapfloat
Categories=Utility;
DESKTOP

# GNOME on Wayland: the extension puts thumbnails in the corner (apps aren't allowed to)
NEED_RELOGIN=0
if command -v gnome-extensions >/dev/null; then
    # 1.0's extension ID: drop its files (a running copy keeps working until logout); forget it once it isn't loaded
    rm -rf "$HOME/.local/share/gnome-shell/extensions/$LEGACY_UUID"
    if ! gnome-extensions info "$LEGACY_UUID" 2>/dev/null | grep -qE 'State: (ACTIVE|ENABLED)$'; then
        python3 - "$LEGACY_UUID" <<'PY'
import ast, subprocess, sys
cur = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"], capture_output=True, text=True).stdout
cur = ast.literal_eval(cur.replace("@as", "").strip() or "[]")
if sys.argv[1] in cur:
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str([e for e in cur if e != sys.argv[1]])])
PY
    fi
    mkdir -p "$EXT"
    if ! diff -q "$SRC/gnome-extension" "$EXT" >/dev/null 2>&1; then
        NEED_RELOGIN=1  # GNOME keeps running the old extension code until the next login
    fi
    cp "$SRC"/gnome-extension/* "$EXT/"
    if ! gnome-extensions enable "$UUID" 2>/dev/null; then
        # shell hasn't seen it yet (new install): mark it enabled for the next login
        python3 - "$UUID" <<'PY'
import ast, subprocess, sys
cur = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"], capture_output=True, text=True).stdout
cur = ast.literal_eval(cur.replace("@as", "").strip() or "[]")
if sys.argv[1] not in cur:
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(cur + [sys.argv[1]])])
PY
        NEED_RELOGIN=1
    fi
fi

# Wayland: full-screen capture (Ctrl+Shift+3) is a silent screenshot, which GNOME only allows after an "Allow"
# dialog that it refuses to show for a tray app. Record that permission up front, as clicking Allow would.
if [ "${XDG_SESSION_TYPE:-}" = wayland ] && command -v gdbus >/dev/null; then
    gdbus call --session --dest org.freedesktop.impl.portal.PermissionStore \
        --object-path /org/freedesktop/impl/portal/PermissionStore \
        --method org.freedesktop.impl.portal.PermissionStore.SetPermission \
        screenshot true screenshot snapfloat "['yes']" >/dev/null 2>&1 \
        && echo "Allowed SnapFloat to take full-screen screenshots (./install.sh --uninstall revokes it)."
fi

"$BIN" --quit 2>/dev/null || true
sleep 1
nohup "$BIN" >/dev/null 2>&1 &
echo "SnapFloat installed and running. PrtSc (or Ctrl+Shift+4) = region, Shift+PrtSc (or Ctrl+Shift+3) = full screen."
[ "$NEED_RELOGIN" = 1 ] && echo "Log out and back in once to activate the GNOME extension (corner placement)."
exit 0
