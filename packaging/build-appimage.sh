#!/usr/bin/env bash
# Build dist/SnapFloat-x86_64.AppImage. Run from the repo root with Python 3.12 (CI does this on ubuntu-22.04).
set -euo pipefail
python -m pip install -q -r requirements.txt pyinstaller
pyinstaller --noconfirm --onedir --windowed --name snapfloat --add-data "gnome-extension:gnome-extension" snapfloat.py
rm -rf AppDir && mkdir -p AppDir && cp -r dist/snapfloat/. AppDir/
cp packaging/snapfloat.desktop packaging/snapfloat.png AppDir/
ln -sf snapfloat.png AppDir/.DirIcon
cat > AppDir/AppRun <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/snapfloat" "$@"
RUN
chmod +x AppDir/AppRun
if [ ! -x appimagetool ]; then
    curl -fsSL -o appimagetool https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x appimagetool
fi
ARCH=x86_64 ./appimagetool --appimage-extract-and-run AppDir dist/SnapFloat-x86_64.AppImage
