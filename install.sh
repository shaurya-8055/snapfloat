#!/usr/bin/env bash
# SnapFloat installer for Ubuntu/Debian.   ./install.sh  (re-run to update)   ./install.sh --uninstall
set -euo pipefail
DIR="$HOME/.local/share/snapfloat"
BIN="$HOME/.local/bin/snapfloat"
APPS="$HOME/.local/share/applications/snapfloat.desktop"
UUID="snapfloat@snapfloat.github.io"
EXT="$HOME/.local/share/gnome-shell/extensions/$UUID"
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = "--uninstall" ]; then
    "$BIN" --quit 2>/dev/null || true
    gnome-extensions disable "$UUID" 2>/dev/null || true
    gdbus call --session --dest org.freedesktop.impl.portal.PermissionStore \
        --object-path /org/freedesktop/impl/portal/PermissionStore \
        --method org.freedesktop.impl.portal.PermissionStore.DeletePermission screenshot screenshot snapfloat >/dev/null 2>&1 || true
    for k in region screen; do
        gsettings reset-recursively "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/snapfloat-$k/" 2>/dev/null || true
    done
    rm -rf "$DIR" "$EXT" "$BIN" "$APPS" "$HOME/.config/autostart/snapfloat.desktop" "$HOME/.config/snapfloat"
    echo "SnapFloat removed."
    exit 0
fi

dpkg -s python3-venv libxcb-cursor0 >/dev/null 2>&1 || sudo apt-get install -y python3-venv libxcb-cursor0
mkdir -p "$DIR" "$(dirname "$BIN")" "$(dirname "$APPS")"
cp "$SRC/snapfloat.py" "$DIR/"
[ -x "$DIR/venv/bin/python" ] || python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q --upgrade -r "$SRC/requirements.txt"

printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$DIR/venv/bin/python" "$DIR/snapfloat.py" > "$BIN"
chmod +x "$BIN"
cat > "$APPS" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SnapFloat
Comment=Floating screenshot thumbnail you can drag into any app
Exec=$BIN
Icon=camera-photo
Categories=Utility;
DESKTOP

# GNOME on Wayland: the extension puts thumbnails in the corner (apps aren't allowed to)
NEED_RELOGIN=0
if command -v gnome-extensions >/dev/null; then
    mkdir -p "$EXT"
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
echo "SnapFloat installed and running. Ctrl+Shift+4 = region, Ctrl+Shift+3 = full screen."
[ "$NEED_RELOGIN" = 1 ] && echo "Log out and back in once to activate the GNOME extension (corner placement)."
exit 0
