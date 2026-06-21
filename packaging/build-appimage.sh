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
# pinned tool and runtime, checked by hash: a release build must not change because "continuous" moved
fetch() {  # url file sha256
    [ -f "$2" ] || curl -fsSL -o "$2" "$1"
    echo "$3  $2" | sha256sum -c --quiet || { rm -f "$2"; echo "checksum mismatch: $2" >&2; exit 1; }
}
fetch https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage appimagetool \
    ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
fetch https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64 appimage-runtime \
    2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d
chmod +x appimagetool
ARCH=x86_64 ./appimagetool --appimage-extract-and-run --runtime-file appimage-runtime AppDir dist/SnapFloat-x86_64.AppImage
