#!/usr/bin/env python3
"""SnapFloat: macOS-style floating screenshot thumbnail for Linux and Windows.

Take a screenshot -> a thumbnail floats in the corner -> drag it into any chat.
Nothing lands in your Screenshots folder unless you ask for it.
Usage: snapfloat [--region | --screen | --settings | --quit]
"""
import getpass, math, os, random, re, shlex, shutil, signal, socket, stat, subprocess, sys, tempfile, threading, time
from pathlib import Path

APP = "SnapFloat"
AUTHOR = "Shaurya Shakya"
HOMEPAGE = "https://github.com/shaurya-8055/snapfloat"
COMMANDS = ("region", "screen", "settings", "quit")
EXT_UUID = "snapfloat@shaurya-8055.github.io"
LEGACY_UUID = "snapfloat@snapfloat.github.io"  # 1.0 used this ID; uninstall cleans it up too
if os.name == "nt":
    SERVER = f"snapfloat-{getpass.getuser()}"
    ADDR = r"\\.\pipe" + "\\" + SERVER
else:
    SERVER = ADDR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), f"snapfloat-{os.getuid()}.sock")
# Screenshots live in a per-user runtime dir on Linux (never shared with other users); %TEMP% is per-user on Windows.
_RUNTIME = os.environ.get("XDG_RUNTIME_DIR")
TMP = (Path(_RUNTIME) / "snapfloat" if os.name != "nt" and _RUNTIME and os.path.isdir(_RUNTIME)
       else Path(tempfile.gettempdir()) / f"snapfloat-{getpass.getuser()}")
LEGACY_TMP = Path(tempfile.gettempdir()) / f"snapfloat-{getpass.getuser()}"  # where 1.0/1.1 kept them
PRINT_MARK = (Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
              / "snapfloat" / "took-printscreen")  # what the PrtSc keys were before SnapFloat took them
if os.environ.get("APPIMAGE"):  # inside an AppImage, sys.executable sits in a mount whose path changes every launch
    CMD = [os.environ["APPIMAGE"]]
elif getattr(sys, "frozen", False):
    CMD = [sys.executable]
else:
    CMD = [sys.executable, str(Path(__file__).resolve())]
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # where bundled files (the GNOME extension) live
if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
    # PyInstaller points LD_LIBRARY_PATH at its bundled libs. Child processes (gsettings, gnome-extensions, gdbus, tesseract,
    # wl-copy) would load those old copies and break silently, so give them the system's libraries back.
    if "LD_LIBRARY_PATH_ORIG" in os.environ:
        os.environ["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH_ORIG"]
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


def send(cmd):
    """Hand a command to the running instance. Stdlib only, so hotkeys stay fast."""
    try:
        if os.name == "nt":
            with open(ADDR, "wb") as f:
                f.write(cmd.encode())
        else:
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(ADDR)
                s.sendall(cmd.encode())
        return True
    except OSError:
        return False


def gsettings_list(schema, key):
    out = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True).stdout
    return [p.strip(" '") for p in out.replace("@as", "").strip(" []\n").split(",") if p.strip(" '")]


GNOME_SHOT_KEYS = ("show-screenshot-ui", "screenshot")  # GNOME's PrtSc and Shift+PrtSc; Alt+PrtSc (window) stays GNOME's


def free_print_key(on):
    """Let SnapFloat have the Print Screen key, or hand back exactly what the system had before.

    A marker file remembers the previous values, so a setup SnapFloat never touched is never changed."""
    if on == PRINT_MARK.exists():
        return
    if os.name == "nt":
        import winreg  # Windows 11 sends PrtSc to Snipping Tool unless this is 0
        name = "PrintScreenKeyForSnippingEnabled"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Keyboard") as k:
            if on:
                try:
                    old = str(winreg.QueryValueEx(k, name)[0])
                except OSError:
                    old = "absent"
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 0)
            else:
                old = PRINT_MARK.read_text().strip()
                if old.isdigit():
                    winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(old))
                else:
                    try:
                        winreg.DeleteValue(k, name)
                    except OSError:
                        pass
    elif shutil.which("gsettings"):
        if on and "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
            return
        schema = "org.gnome.shell.keybindings"
        if on:
            old = "\n".join(subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True).stdout.strip()
                            for key in GNOME_SHOT_KEYS)
            for key in GNOME_SHOT_KEYS:
                subprocess.run(["gsettings", "set", schema, key, "[]"], capture_output=True)
        else:
            for key, val in zip(GNOME_SHOT_KEYS, PRINT_MARK.read_text().splitlines() + [""] * len(GNOME_SHOT_KEYS)):
                if val.strip("@as []"):
                    subprocess.run(["gsettings", "set", schema, key, val], capture_output=True)
                else:  # empty: an older SnapFloat had already cleared it, so GNOME's default is the right thing to restore
                    subprocess.run(["gsettings", "reset", schema, key], capture_output=True)
    else:
        return
    if on:
        PRINT_MARK.parent.mkdir(parents=True, exist_ok=True)
        PRINT_MARK.write_text(old)
    else:
        PRINT_MARK.unlink(missing_ok=True)


def uninstall(quit_running=True):
    """Undo everything SnapFloat and install.sh added: shortcuts, autostart, settings, temp shots, the install itself."""
    if quit_running and send("quit"):
        for _ in range(30):
            time.sleep(0.1)
            if not send("ping"):
                break
    home = Path.home()
    free_print_key(False)
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, APP)  # otherwise Windows keeps launching a deleted exe at every login
        except OSError:
            pass
        for key in (r"Software\snapfloat\snapfloat", r"Software\snapfloat"):  # settings (QSettings' registry home)
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass
        shutil.rmtree(PRINT_MARK.parent, ignore_errors=True)
    else:
        if shutil.which("gsettings"):
            keys = "org.gnome.settings-daemon.plugins.media-keys"
            paths = gsettings_list(keys, "custom-keybindings")
            for p in (p for p in paths if "/snapfloat-" in p):
                subprocess.run(["gsettings", "reset-recursively", f"{keys}.custom-keybinding:{p}"], capture_output=True)
            subprocess.run(["gsettings", "set", keys, "custom-keybindings", str([p for p in paths if "/snapfloat-" not in p])],
                           capture_output=True)  # drop them from the list too, or GNOME shows blank shortcuts
            exts = gsettings_list("org.gnome.shell", "enabled-extensions")
            if EXT_UUID in exts or LEGACY_UUID in exts:
                subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions",
                                str([e for e in exts if e not in (EXT_UUID, LEGACY_UUID)])], capture_output=True)
        if shutil.which("gdbus"):
            subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.impl.portal.PermissionStore",
                            "--object-path", "/org/freedesktop/impl/portal/PermissionStore", "--method",
                            "org.freedesktop.impl.portal.PermissionStore.DeletePermission", "screenshot", "screenshot", "snapfloat"],
                           capture_output=True)
        for f in (".config/autostart/snapfloat.desktop", ".local/bin/snapfloat", ".local/share/applications/snapfloat.desktop",
                  ".local/share/icons/hicolor/256x256/apps/snapfloat.png"):
            (home / f).unlink(missing_ok=True)
        for d in (".config/snapfloat", f".local/share/gnome-shell/extensions/{EXT_UUID}",
                  f".local/share/gnome-shell/extensions/{LEGACY_UUID}", ".local/share/snapfloat"):
            shutil.rmtree(home / d, ignore_errors=True)
    for d in (TMP, LEGACY_TMP):
        if d.is_dir() and not d.is_symlink():
            shutil.rmtree(d, ignore_errors=True)


ARG = SELF_TEST = None
if __name__ == "__main__":
    # --self-test[=report.txt]: checks a build works on this machine (CI runs it on the AppImage and the exe)
    SELF_TEST = next((a.partition("=")[2] for a in sys.argv[1:] if a.partition("=")[0] == "--self-test"), None)
    if SELF_TEST is not None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"  # no display needed
    elif "--uninstall" in sys.argv:
        uninstall()
        if "--quiet" in sys.argv:  # package managers (Scoop) uninstall without a popup
            pass
        elif os.name == "nt":  # the exe has no console to print to
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "SnapFloat removed. Delete SnapFloat.exe to finish.", APP, 0)
        else:
            print("SnapFloat removed.")
        sys.exit(0)
    ARG = next((a[2:] for a in sys.argv[1:] if a[2:] in COMMANDS), None)
    if SELF_TEST is None and (send(ARG or "settings") or ARG == "quit"):  # already running: just forward the command
        sys.exit(0)

WAYLAND = os.environ.get("XDG_SESSION_TYPE") == "wayland"
if sys.platform.startswith("linux"):
    # native Wayland: XWayland can't drag into Wayland apps. Corner placement then needs the GNOME extension.
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland" if WAYLAND else "xcb")

from PySide6.QtCore import (QAbstractAnimation, QCoreApplication, QEasingCurve, QLineF, QLockFile, QMimeData, QObject, QPoint,
                            QPointF, QPropertyAnimation, QProcess, QRect, QRectF, QSettings, QSize, QSizeF, Qt, QTimer, QUrl,
                            QEventLoop, QVariantAnimation, Signal, Slot, SLOT, qVersion)
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QDrag, QFont, QGuiApplication, QIcon, QImage, QImageReader,
                           QFontMetricsF, QKeySequence, QLinearGradient, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QPixmap, QPolygonF)
from shiboken6 import isValid
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget)
try:
    import zxingcpp  # reads QR codes; optional when running from source
except ImportError:
    zxingcpp = None
try:
    import jeepney  # Wayland screen recording talks to the ScreenCast portal with it; optional when running from source
except ImportError:
    jeepney = None

DEFAULTS = dict(seconds=6, corner="bottom-right", size=220, copy=True, save=False,
                folder=str(Path.home() / "Pictures" / "Screenshots"), region_key="Ctrl+Shift+4",
                screen_key="Ctrl+Shift+3", print_key=True, watch_clipboard=False, autostart=True, small=False, sound=True, flash=True, bound="",
                ocr_langs="")
CORNERS = ["bottom-right", "bottom-left", "top-right", "top-left"]
MAX_THUMBS = 5
MAX_RECENT = 10
SMALL_MAX = 1920  # "smaller files": longest side of the JPG handed to chat apps
M = 10  # shadow margin around a thumbnail
ACCENT = QColor("#0a84ff")
# Qt 6.10+ sends an empty drag image on Wayland (why requirements.txt pins 6.9). If a newer Qt ends up installed anyway,
# don't empty the corner during a drag: nothing would follow the cursor and the screenshot would seem to vanish.
DRAG_IMAGE_OK = not WAYLAND or tuple(int(x) for x in qVersion().split(".")[:2]) < (6, 10)
QImageReader.setAllocationLimit(0)  # a "Combine" of several 5K shots is bigger than Qt's default 256 MB read limit


class Config:
    def __init__(self):
        self.s = QSettings("snapfloat", "snapfloat")

    def __getattr__(self, k):
        d = DEFAULTS[k]
        return self.s.value(k, d, type=type(d))

    def set(self, k, v):
        self.s.setValue(k, v)


def tmp_dir():
    """Screenshots can be private: keep our folder owner-only, and never trust one somebody else created."""
    global TMP
    if os.name != "nt":
        try:
            TMP.mkdir(mode=0o700, exist_ok=True)
            st = os.lstat(TMP)
            ours = stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()
        except OSError:
            ours = False
        if not ours:  # a symlink or someone else's folder at our name: use a fresh private one instead
            TMP = Path(tempfile.mkdtemp(prefix="snapfloat-"))
        TMP.chmod(0o700)
    else:
        TMP.mkdir(exist_ok=True)
    return TMP


def new_path():
    return tmp_dir() / time.strftime(f"Screenshot %Y-%m-%d at %H.%M.%S.{int(time.time() * 1000) % 1000:03d}.png")


def forget(path):
    for f in (path, path.with_suffix(".jpg"), path.with_suffix(".png")):  # .png: a recording's thumbnail
        f.unlink(missing_ok=True)


def purge():
    if TMP.is_dir() and not TMP.is_symlink():
        for f in TMP.iterdir():
            if f.is_file() or f.is_symlink():
                f.unlink(missing_ok=True)


def icon():
    pix = QPixmap(256, 256)  # drawn large, scaled down as needed: crisp in HiDPI trays and as the app-menu icon
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(4, 4)
    p.setBrush(ACCENT)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(QColor("white"))
    p.drawRoundedRect(30, 34, 22, 16, 4, 4)  # the little floating thumbnail
    p.end()
    return QIcon(pix)


def recording_icon():
    pix = icon().pixmap(256, 256)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("white"), 12))
    p.setBrush(QColor("#ff3b30"))
    p.drawEllipse(QRectF(136, 8, 112, 112))  # a red "recording" dot on the usual icon
    p.end()
    return QIcon(pix)


# ---------- capture ----------

class Portal(QObject):
    """Wayland: apps can't read the screen, so ask xdg-desktop-portal (GNOME/KDE screenshot UI)."""
    done = Signal(str)
    error = Signal(str)

    def _match(self, connect):
        bus = QDBusConnection.sessionBus()
        args = ("org.freedesktop.portal.Desktop", self.path, "org.freedesktop.portal.Request", "Response",
                self, SLOT("on_response(uint,QVariantMap)"))
        try:
            bus.connect(*args) if connect else bus.disconnect(*args)
        except (TypeError, RuntimeError):
            pass

    def capture(self, interactive):
        self.interactive = interactive
        bus = QDBusConnection.sessionBus()
        token = f"snapfloat{time.time_ns()}"
        self.path = f"/org/freedesktop/portal/desktop/request/{bus.baseService()[1:].replace('.', '_')}/{token}"
        self._match(True)
        msg = QDBusMessage.createMethodCall("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                            "org.freedesktop.portal.Screenshot", "Screenshot")
        msg.setArguments(["", {"handle_token": token, "interactive": interactive}])
        reply = bus.call(msg)
        if reply.type() == QDBusMessage.ErrorMessage:  # no portal or no backend: say so instead of waiting forever
            self._match(False)
            self.error.emit(reply.errorMessage() or "the desktop's screenshot service isn't available")
            self.done.emit("")

    @Slot("uint", "QVariantMap")
    def on_response(self, code, results):
        self._match(False)
        if code == 2 and not self.interactive:  # silent full-screen shot refused (no permission): use the screenshot UI
            return self.capture(True)
        self.done.emit(QUrl(results.get("uri", "")).toLocalFile() if code == 0 else "")


if WAYLAND:
    from PySide6.QtDBus import QDBusConnection, QDBusMessage


def windows_under_desktop():
    """Windows: the visible top-level windows, topmost first, as rects in physical desktop pixels (empty elsewhere).
    Read before the selection overlay covers them."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    user32, dwm, rects = ctypes.windll.user32, ctypes.windll.dwmapi, []

    def visit(hwnd, _):
        cloaked, r = ctypes.c_int(0), wintypes.RECT()
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))  # DWMWA_CLOAKED: other desktops, hidden UWP
            # DWMWA_EXTENDED_FRAME_BOUNDS: the visible frame, without the invisible resize borders
            if not cloaked.value and not dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) \
                    and r.right - r.left > 40 and r.bottom - r.top > 40:
                rects.append(QRect(r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True
    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)  # top of the z-order first
    return rects


class Selector(QWidget):
    """Windows/X11: frozen screen, drag a rectangle, or (Windows) click a window. One per monitor; selection stays on one monitor."""

    def __init__(self, screen, finish, windows=()):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.shot, self.finish, self.origin, self.sel, self.hover = screen.grabWindow(0), finish, None, QRect(), None
        # Qt 6 keeps each screen's native origin at its geometry's top-left, so desktop pixels minus that origin are pixels
        # of this screen's shot. ponytail: untested on real Windows, mixed-DPI setups especially
        self.windows = [r.translated(-screen.geometry().topLeft()).intersected(self.shot.rect()) for r in windows]
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(bool(windows))  # highlight the window under the pointer

    def src(self, r):
        d = self.shot.devicePixelRatio()
        return QRectF(r.x() * d, r.y() * d, r.width() * d, r.height() * d)

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(QRectF(self.rect()), self.shot, QRectF(self.shot.rect()))
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        d, h = self.shot.devicePixelRatio(), self.hover
        r = QRectF(self.sel) if not self.sel.isEmpty() else QRectF(h.x() / d, h.y() / d, h.width() / d, h.height() / d) if h else None
        if r is not None:
            p.drawPixmap(r, self.shot, self.src(r))
            p.setPen(QPen(ACCENT, 2))
            p.drawRect(r)

    def window_at(self, pos):
        d = self.shot.devicePixelRatio()
        return next((w for w in self.windows if w.contains((pos * d).toPoint())), None)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return self.finish(None)
        self.origin, self.hover = e.position().toPoint(), self.window_at(e.position())

    def mouseMoveEvent(self, e):
        if self.origin is not None:
            self.sel = QRect(self.origin, e.position().toPoint()).normalized()
        else:
            self.hover = self.window_at(e.position())
        self.update()

    def mouseReleaseEvent(self, e):
        if self.origin is not None:
            if self.sel.width() > 3 and self.sel.height() > 3:
                self.finish(self.shot.copy(self.src(self.sel).toRect()))
            else:  # a click: the window under it, if any
                self.finish(self.shot.copy(self.hover) if self.hover else None)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.finish(None)


def select_region(callback):
    sels = []

    def finish(pix):
        for s in sels:
            s.close()
        sels.clear()
        callback(pix)  # None = cancelled; still brings hidden thumbnails back

    windows = windows_under_desktop()
    sels += [Selector(s, finish, windows) for s in QGuiApplication.screens()]  # grab every screen before covering any
    for s in sels:
        s.showFullScreen()
        s.activateWindow()


# ---------- scrolling capture ----------

def row_sigs(img):
    """One hash per pixel row. Screenshots of scrolled content repeat rows exactly, so equal hashes = the same row."""
    bpl, data = img.bytesPerLine(), bytes(img.constBits())
    return [hash(data[y * bpl:(y + 1) * bpl]) for y in range(img.height())]


def scroll_offset(prev, cur):
    """How many rows the content moved up from frame `prev` to `cur` (row hashes), and how many rows at the bottom stayed
    put (a fixed footer, or blank space). None: it didn't move, moved up, or the frames don't overlap.
    Rows that stay put at the top or bottom (sticky headers, footers) don't take part in the matching."""
    n = len(cur)
    head = next((y for y in range(n) if prev[y] != cur[y]), n)
    if head == n:
        return None
    foot = next(i for i in range(n) if prev[n - 1 - i] != cur[n - 1 - i])
    lo, hi = head, n - foot
    where = {}
    for y in range(lo, hi):
        where.setdefault(prev[y], []).append(y)
    votes = {}
    for y in range(lo, hi):
        ys = where.get(cur[y], ())
        if len(ys) <= 4:  # blank and repeated rows match anywhere: they don't get a vote
            for py in ys:
                if py > y:
                    votes[py - y] = votes.get(py - y, 0) + 1
    if not votes:
        return None
    d = max(votes, key=votes.get)  # ponytail: one best shift; a page of identical rows could fool it
    overlap = hi - lo - d
    if overlap < 8 or sum(cur[y] == prev[y + d] for y in range(lo, lo + overlap)) < 0.8 * overlap:
        return None
    return d, foot


class Stitcher:
    """One tall image from frames of an area taken while it scrolls. Keeps only each frame's new rows (and the rows below
    them, in case those turn out to be part of a fixed footer), never whole frames, so long pages stay small in memory."""

    def __init__(self, first):
        self.first = first.convertToFormat(QImage.Format_RGB32)
        self.sigs, self.parts, self.foot = row_sigs(self.first), [], self.first.height()

    def add(self, img):
        """Returns whether the frame added anything (False: not scrolled, scrolled up, or too far to match)."""
        img = img.convertToFormat(QImage.Format_RGB32)
        if img.size() != self.first.size():
            return False
        sigs = row_sigs(img)
        moved = scroll_offset(self.sigs, sigs)
        if not moved:
            return False
        d, foot = moved
        self.parts.append((d, img.copy(0, img.height() - foot - d, img.width(), foot + d)))
        self.foot = min(self.foot, foot)  # the real footer is what stayed put at every step
        self.sigs = sigs
        return True

    def height(self):
        return self.first.height() + sum(d for d, _ in self.parts)

    def result(self):
        f = self.foot if self.parts else 0
        w, top = self.first.width(), self.first.height() - f
        out = QImage(w, self.height(), QImage.Format_RGB32)
        p = QPainter(out)
        p.drawImage(0, 0, self.first, 0, 0, w, top)
        y = top
        for d, part in self.parts:
            p.drawImage(0, y, part, 0, part.height() - f - d, w, d)  # the frame's new rows, just above the footer
            y += d
        last = self.parts[-1][1] if self.parts else self.first
        p.drawImage(0, y, last, 0, last.height() - f, w, f)
        p.end()
        return out


def stitch(frames):
    s = Stitcher(frames[0])
    for f in frames[1:]:
        s.add(f)
    return s.result()


def locate(part, whole):
    """Where the screenshot `part` sits in the screenshot `whole`, pixel for pixel, as a QRect; None if it isn't there."""
    part, whole = part.convertToFormat(QImage.Format_RGB32), whole.convertToFormat(QImage.Format_RGB32)
    pb, wb, n = part.bytesPerLine(), whole.bytesPerLine(), part.height()
    pd, wd = bytes(part.constBits()), bytes(whole.constBits())
    rows = [pd[y * pb:(y + 1) * pb] for y in range(n)]
    key = max(range(0, n, max(1, n // 50)), key=lambda y: len(set(rows[y])))  # a varied row: few false hits
    for wy in range(key, whole.height() - (n - key) + 1):
        line = wd[wy * wb:(wy + 1) * wb]
        i = line.find(rows[key])
        while i != -1:
            top = wy - key
            if i % 4 == 0 and all(wd[(top + y) * wb + i:(top + y) * wb + i + pb] == rows[y] for y in range(n)):
                return QRect(i // 4, top, part.width(), n)
            i = line.find(rows[key], i + 1)
    return None


class ScrollCapture(QWidget):
    """The bar shown during a scrolling capture. Windows/X11 grab the area every 0.4 s while you scroll; on Wayland every
    grab is a screenshot the desktop takes for us, so you scroll, then press Add. Done stitches the parts into one image."""
    MAX_HEIGHT = 30000  # px: stop there (an endless feed would grow forever)

    def __init__(self, app, first, grab, area=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle("snapfloat-pin")  # Wayland: the GNOME extension keeps windows with this title above others
        self.app, self.take, self.stitcher, self.busy = app, grab, Stitcher(first), False
        themed(self, QSS)
        self.setAttribute(Qt.WA_TranslucentBackground)  # only the rounded card shows
        card = QFrame(objectName="card")
        QHBoxLayout(self).addWidget(card)
        self.layout().setContentsMargins(0, 0, 0, 0)
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 8, 8, 8)
        self.label = QLabel()
        h.addWidget(self.label)
        if WAYLAND:
            h.addWidget(QPushButton("Add", clicked=self.tick, cursor=Qt.PointingHandCursor))
        h.addWidget(QPushButton("Done", objectName="primary", clicked=self.done, cursor=Qt.PointingHandCursor))
        h.addWidget(QPushButton("Cancel", clicked=lambda: self.finish(None), cursor=Qt.PointingHandCursor))
        self.count()
        self.adjustSize()
        self.hide_to_grab = WAYLAND  # Wayland: we can't place this bar, so it might sit over the area
        if area is not None:  # Windows/X11: just below (or above) the area, where the grabs don't see it
            scr, rect = area
            d, g = scr.devicePixelRatio(), scr.geometry()
            r = QRect(g.x() + round(rect.x() / d), g.y() + round(rect.y() / d), round(rect.width() / d), round(rect.height() / d))
            y = r.bottom() + 12 if r.bottom() + 12 + self.height() <= g.bottom() else r.top() - 12 - self.height()
            self.move(max(g.left(), min(r.center().x() - self.width() // 2, g.right() - self.width())), max(g.top(), y))
            self.hide_to_grab = self.frameGeometry().intersects(r)  # no room around a full-screen area
        self.timer = QTimer(self, interval=400, timeout=self.tick)
        if not WAYLAND:
            self.timer.start()

    def count(self):
        n = len(self.stitcher.parts) + 1
        self.label.setText(f"{'Scroll, then Add' if WAYLAND else 'Scroll slowly'} · {n} part{'s' * (n != 1)}")

    def tick(self):
        if self.busy:  # the last grab is still on its way (Wayland)
            return
        self.busy = True
        if self.hide_to_grab:
            self.hide()
            QTimer.singleShot(150, lambda: self.take(self.got))  # let the compositor repaint without us
        else:
            self.take(self.got)

    def got(self, img):
        self.busy = False
        if not isValid(self) or not self.timer:  # finished meanwhile
            return
        if img is not None and self.stitcher.add(img):
            self.count()
        if self.hide_to_grab:
            self.show()
        if self.stitcher.height() >= self.MAX_HEIGHT:
            self.done()

    def done(self):
        self.finish(QPixmap.fromImage(self.stitcher.result()))

    def finish(self, pix):
        self.timer.stop()
        self.timer = None
        self.close()
        self.app.scroller = None
        self.app.show_shot(pix)


# ---------- capture feedback (Windows/X11: on Wayland GNOME's screenshot service flashes and plays its sound) ----------

def write_click(path):
    """A synthesized two-blade shutter click, so Windows needs no bundled sound file."""
    import struct, wave
    rate, out = 22050, []
    for i in range(int(rate * 0.12)):
        t = i / rate
        env = math.exp(-t * 90) + (0.7 * math.exp(-(t - 0.05) * 80) if t > 0.05 else 0)
        out.append(int(random.uniform(-1, 1) * env * 0.45 * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(out)}h", *out))


def shutter():
    if os.name == "nt":
        import winsound
        wav = TMP / "shutter.wav"
        if not wav.exists():
            tmp_dir()
            write_click(wav)
        winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    elif shutil.which("canberra-gtk-play"):  # ponytail: X11 without libcanberra stays silent
        subprocess.Popen(["canberra-gtk-play", "-i", "camera-shutter"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Flash(QWidget):
    """A white blink over the captured screen that fades out."""

    def __init__(self, screen):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setGeometry(screen.geometry())
        self.a = 1.0
        self.anim = QVariantAnimation(self, startValue=1.0, endValue=0.0, duration=320, easingCurve=QEasingCurve.OutCubic,
                                      valueChanged=self.fade, finished=self.close)

    def fade(self, a):
        self.a = a
        self.update()

    def showEvent(self, _):
        self.anim.start()

    def paintEvent(self, _):
        QPainter(self).fillRect(self.rect(), QColor(255, 255, 255, int(150 * self.a)))


class Countdown(QWidget):
    """Delayed capture: a ring counting down in the thumbnail corner. Click it to cancel."""

    def __init__(self, app, seconds, then):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.app, self.then, self.total = app, then, seconds
        self.setFixedSize(112, 112)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to cancel")
        self.setWindowTitle(f"snapfloat-thumb {app.cfg.corner} 0")  # Wayland: the GNOME extension parks it in the corner
        self.move(app.corner_pos(self.width(), self.height(), 0))
        self.ring = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=seconds * 1000,
                                      valueChanged=self.update, finished=self.fire)

    def showEvent(self, _):
        self.ring.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        left = self.total * (1 - (self.ring.currentValue() or 0.0))
        r = QRectF(self.rect()).adjusted(14, 14, -14, -14)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 20, 22, 215))
        p.drawEllipse(r)
        ring = r.adjusted(7, 7, -7, -7)
        p.setPen(QPen(QColor(255, 255, 255, 50), 4))
        p.drawEllipse(ring)
        p.setPen(QPen(ACCENT, 4, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(ring, 90 * 16, int(360 * 16 * left / self.total))
        pop = max(0.0, (left % 1) - 0.8) / 0.2  # the number pops at the start of each second, then settles
        f = QFont()
        f.setPixelSize(34)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.translate(r.center())
        p.scale(1 + 0.25 * pop, 1 + 0.25 * pop)
        p.drawText(QRectF(-40, -40, 80, 80), Qt.AlignCenter, str(max(1, math.ceil(left))))

    def mousePressEvent(self, _):
        self.ring.stop()
        self.close()
        self.app.show_shot(None)  # cancelled: bring hidden thumbnails back

    def fire(self):
        self.close()
        QTimer.singleShot(200, self.then)  # let the compositor repaint without the ring


# ---------- screen recording ----------

VIDEO = (".mp4", ".webm")


def record_missing():
    """What has to be installed before recording works here, or "" if nothing."""
    if not WAYLAND:
        return "" if shutil.which("ffmpeg") else "ffmpeg"
    if jeepney is None:
        return "jeepney"
    gst = shutil.which("gst-inspect-1.0")
    return "" if gst and subprocess.run([gst, "--exists", "pipewiresrc"]).returncode == 0 else "gstreamer1.0-pipewire"


def gst_encoder():
    """H.264 in MP4 (plays everywhere) with openh264 from plugins-bad, else VP8 in WebM (always in plugins-good).
    Not x264enc: it fails on a window with an odd width or height, which openh264 crops to even."""
    for name, tail, ext in (("openh264enc", "video/x-raw,format=I420 ! openh264enc ! h264parse ! mp4mux", ".mp4"),
                            ("vp8enc", "vp8enc deadline=1 cpu-used=8 ! webmmux", ".webm")):
        if subprocess.run(["gst-inspect-1.0", "--exists", name]).returncode == 0:
            return tail.split(), ext
    raise RuntimeError("GStreamer has no video encoder (install gstreamer1.0-plugins-good)")


def record_cmd(stem, fd=None, node=None):
    """The recorder command and the file it writes. Wayland: GStreamer on the portal's PipeWire stream; else ffmpeg."""
    if WAYLAND:
        tail, ext = gst_encoder()
        path = stem.with_suffix(ext)
        # -e: Ctrl+C (SIGINT) ends the stream cleanly, so the file is finalized; each argv item is escaped by gst-launch
        return (["gst-launch-1.0", "-e", "-q", "pipewiresrc", f"fd={fd}", f"path={node}", "do-timestamp=true", "!",
                 "videoconvert", "!", "queue", "!"] + tail + ["!", "filesink", f"location={path}"], path)
    path = stem.with_suffix(".mp4")
    grab = ["-f", "gdigrab", "-i", "desktop"] if os.name == "nt" else ["-f", "x11grab", "-i", os.environ.get("DISPLAY", ":0")]
    return ([shutil.which("ffmpeg") or "ffmpeg", "-y", "-loglevel", "error", "-nostats", "-framerate", "30"] + grab +
            ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
            path)


def screencast_portal():
    """Wayland: the desktop's picker asks which screen or window to share. Returns (connection, fd, PipeWire node).
    The session lasts as long as the connection; raises if the user cancels. Blocking: run it off the UI thread.
    (QtDBus can't send the portal's uint32 options from Python, hence jeepney.)"""
    from jeepney import DBusAddress, MatchRule, new_method_call
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection
    conn = open_dbus_connection(bus="SESSION", enable_fds=True)
    portal = DBusAddress("/org/freedesktop/portal/desktop", "org.freedesktop.portal.Desktop", "org.freedesktop.portal.ScreenCast")
    try:
        def ask(method, sig, *args, **options):
            token = f"snapfloat{time.time_ns()}"
            rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request", member="Response",
                             path=f"/org/freedesktop/portal/desktop/request/{conn.unique_name[1:].replace('.', '_')}/{token}")
            conn.send_and_get_reply(message_bus.AddMatch(rule))
            with conn.filter(rule) as responses:
                opts = {"handle_token": ("s", token), **options}
                conn.send_and_get_reply(new_method_call(portal, method, sig + "a{sv}", (*args, opts)))
                code, results = conn.recv_until_filtered(responses).body
            if code != 0:
                raise RuntimeError("cancelled" if code == 1 else "the desktop refused to share the screen")
            return results
        session = ask("CreateSession", "", session_handle_token=("s", f"snapfloat{time.time_ns()}"))["session_handle"][1]
        ask("SelectSources", "o", session, types=("u", 1 | 2), cursor_mode=("u", 2))  # a monitor or a window; cursor drawn in
        node = ask("Start", "os", session, "")["streams"][1][0][0]
        fd = conn.send_and_get_reply(new_method_call(portal, "OpenPipeWireRemote", "oa{sv}", (session, {}))).body[0]
        return conn, fd.to_raw_fd(), node
    except BaseException:
        conn.close()
        raise


class Recorder(QObject):
    """One screen recording at a time. finished(path) gets the video, or "" if nothing was recorded."""
    finished = Signal(str)
    error = Signal(str)
    _go = Signal(object)  # from the portal thread
    _ended = Signal()  # from the thread waiting on the recorder

    def __init__(self):
        super().__init__()
        self.proc = self.conn = None
        self.busy = False
        self._go.connect(self.launch)
        self._ended.connect(self.ended)

    def start(self):
        self.busy = True
        if WAYLAND:
            threading.Thread(target=lambda: self._go.emit(self.portal()), daemon=True).start()
        else:
            self.launch((None, None, None))

    @staticmethod
    def portal():
        try:
            return screencast_portal()
        except Exception as e:
            return e

    def launch(self, src):
        fd = None
        try:
            if isinstance(src, Exception):
                raise src
            self.conn, fd, node = src
            stem = tmp_dir() / time.strftime(f"Recording %Y-%m-%d at %H.%M.%S.{int(time.time() * 1000) % 1000:03d}")
            cmd, self.path = record_cmd(stem, fd, node)
            self.log = open(stem.with_suffix(".log"), "w+b")
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log,
                                         pass_fds=(fd,) if fd is not None else (),
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.cleanup()
            if str(e) != "cancelled":
                self.error.emit(str(e))
            return self.finished.emit("")
        finally:
            if fd is not None:
                os.close(fd)  # the recorder has its own copy
        threading.Thread(target=lambda: (self.proc.wait(), self._ended.emit()), daemon=True).start()

    def stop(self):
        if not self.proc or self.proc.poll() is not None:
            return
        if WAYLAND:
            self.proc.send_signal(signal.SIGINT)  # gst-launch -e: finish the file, then exit
        else:
            try:
                self.proc.stdin.write(b"q")  # ffmpeg's own "stop"
                self.proc.stdin.flush()
            except OSError:
                self.proc.terminate()

    def ended(self):
        ok = self.proc.returncode == 0 and self.path.exists() and self.path.stat().st_size > 0
        if not ok:
            self.log.seek(0)
            why = self.log.read().decode(errors="ignore").strip().splitlines()
            self.error.emit(why[-1] if why else f"the recorder stopped (exit code {self.proc.returncode})")
            self.path.unlink(missing_ok=True)
        self.cleanup()
        self.finished.emit(str(self.path) if ok else "")

    def cleanup(self):
        if self.conn:
            self.conn.close()  # ends the portal session (and GNOME's "sharing the screen" indicator)
        if getattr(self, "log", None):
            self.log.close()
            Path(self.log.name).unlink(missing_ok=True)
        self.proc = self.conn = self.log = None
        self.busy = False


def poster(video):
    """A recording's thumbnail: its first frame (saved beside it, for Recent) with a play badge."""
    png = video.with_suffix(".png")
    cmd = (["gst-launch-1.0", "-q", "filesrc", f"location={video}", "!", "decodebin", "!", "videoconvert", "!", "pngenc",
            "snapshot=true", "!", "filesink", f"location={png}"] if WAYLAND else
           [shutil.which("ffmpeg") or "ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-frames:v", "1", str(png)])
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        pass
    pix = QPixmap(str(png))
    if pix.isNull():
        pix = QPixmap(640, 360)
        pix.fill(QColor("#1c1c1e"))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    rad = min(pix.width(), pix.height()) / 6
    c = QPointF(pix.width() / 2, pix.height() / 2)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 150))
    p.drawEllipse(c, rad, rad)
    p.setBrush(QColor("white"))
    p.drawPolygon(QPolygonF([c + QPointF(-rad * 0.35, -rad * 0.5), c + QPointF(-rad * 0.35, rad * 0.5), c + QPointF(rad * 0.55, 0)]))
    p.end()
    pix.save(str(png), "PNG")
    return pix


# ---------- markup ----------

TOOLS = [("select", "Select: move a mark, Delete removes it", "V"), ("arrow", "Arrow", "A"), ("rect", "Box", "R"),
         ("ellipse", "Ellipse", "E"), ("pen", "Pen", "P"), ("highlight", "Highlighter", "H"), ("text", "Text", "T"),
         ("step", "Numbered steps", "N"), ("magnify", "Magnifier: drag from the centre outwards", "M"),
         ("spotlight", "Spotlight: darken everything else", "S"), ("blur", "Blur: hide private info", "B"), ("crop", "Crop", "C"),
         ("picker", "Pick a colour from the screenshot (copies its hex)", "I"),
         ("ocr", "Copy text from an area: drag a box over it", "X")]
COLORS = ["#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#ffffff", "#000000"]
TEXT_PX = 22  # on-screen text size while typing; stored in image pixels so it looks the same after export
SCALES = (0.5, 0.75, 1, 1.5, 2, 3)  # [ and ] step through these mark sizes


def pixelate(img, r):
    """Redaction that can't be undone: the area's average colour as a mosaic of random shades.

    A mosaic made from the real pixels keeps enough of each glyph to guess the digits back (tested), so nothing of the
    content survives here except its average colour; the shading is random, seeded by the box so repaints don't flicker."""
    avg = img.copy(r).scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).pixelColor(0, 0)
    block = max(8, min(r.width(), r.height()) // 3)
    cols, rows = r.width() // block + 1, r.height() // block + 1
    rnd = random.Random(f"{r.x()},{r.y()},{r.width()},{r.height()}")
    tile = QImage(cols, rows, QImage.Format_ARGB32_Premultiplied)
    for x in range(cols):
        for y in range(rows):
            tile.setPixelColor(x, y, avg.lighter(rnd.randint(90, 112)))
    return tile.scaled(cols * block, rows * block, Qt.IgnoreAspectRatio, Qt.FastTransformation).copy(0, 0, r.width(), r.height())


def draw_arrow(p, a, b, c, w):
    line = QLineF(a, b)
    if line.length() < 1:
        return
    u = line.unitVector()
    dx, dy, head, ang = u.dx(), u.dy(), max(w * 4.5, 8), math.radians(28)
    tips = [QPointF(b.x() - head * (dx * math.cos(t) - dy * math.sin(t)), b.y() - head * (dx * math.sin(t) + dy * math.cos(t)))
            for t in (ang, -ang)]
    p.setPen(QPen(c, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawLine(a, QPointF(b.x() - dx * head * 0.6, b.y() - dy * head * 0.6))
    p.setBrush(c)
    p.drawPolygon(QPolygonF([b] + tips))


def text_font(size):
    f = QFont()
    f.setPixelSize(max(1, round(size)))
    f.setBold(True)
    return f


def draw_text(p, at, text, c, size):
    """Bold, outlined text; `at` is the first line's baseline. Several lines are stacked by the font's line spacing."""
    f = text_font(size)
    step = QFontMetricsF(f).lineSpacing()
    path = QPainterPath()
    for i, line in enumerate(text.split("\n")):
        path.addText(QPointF(at.x(), at.y() + i * step), f, line)
    outline = QColor(0, 0, 0, 170) if c.lightness() > 100 else QColor(255, 255, 255, 210)
    p.strokePath(path, QPen(outline, size / 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(path, c)


def paint_blurs(p, img, shapes):
    """Redaction boxes, each pixelated once and cached on its shape."""
    for s in shapes:
        if s["t"] != "blur":
            continue
        for rr in s.get("rs") or [s["r"]]:  # "Hide private info" stores all its boxes in one shape: one undo
            r = rr.toRect().intersected(img.rect())
            if r.width() > 1 and r.height() > 1:
                key = (r.x(), r.y(), r.width(), r.height())
                cache = s.setdefault("_px", {})
                if key not in cache:
                    if len(cache) > 64:  # a box still being dragged out creates a new key per mouse move
                        cache.clear()
                    cache[key] = pixelate(img, r)
                p.drawImage(r.topLeft(), cache[key])


def callout_rect(s):
    """A magnifier shows its source circle at 2x in a circle twice as wide, centred on s["at"]."""
    rad = max(s["r"].width(), s["r"].height())
    return QRectF(s["at"].x() - rad, s["at"].y() - rad, 2 * rad, 2 * rad)


def place_callout(src, img):
    """Default spot for a magnifier's callout: up and to the right of the source, kept inside the image."""
    rad = max(src.width(), src.height())
    c = src.center() + QPointF(rad * 1.6, -rad * 1.6)
    if c.x() + rad > img.width():
        c.setX(src.center().x() - rad * 1.6)
    if c.y() - rad < 0:
        c.setY(src.center().y() + rad * 1.6)
    return c


def paint_magnifier(p, img, shapes, s):
    src, out, col = s["r"], callout_rect(s), QColor(s["c"])
    if src.width() < 2:
        return
    p.setPen(QPen(col, s["w"]))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(src)
    line = QLineF(src.center(), out.center())
    if line.length() > src.width() / 2 + out.width() / 2:  # connector between the two rings
        u = line.unitVector()
        d = QPointF(u.dx(), u.dy())
        p.drawLine(src.center() + d * (src.width() / 2), out.center() - d * (out.width() / 2))
    clip = QPainterPath()
    clip.addEllipse(out)
    p.save()
    p.setClipPath(clip, Qt.IntersectClip)
    p.fillPath(clip, QColor("white"))
    p.translate(out.center())
    p.scale(2, 2)
    p.translate(-src.center())
    d = max(src.width(), src.height())
    sq = QRectF(src.center() - QPointF(d / 2, d / 2), QSizeF(d, d))
    p.drawImage(sq, img, sq)  # only the part that shows: cheap even on a 5K shot
    paint_blurs(p, img, shapes)  # a magnifier must never reveal what a blur hides
    p.restore()
    p.setPen(QPen(QColor("white"), s["w"] * 2.2))
    p.drawEllipse(out)
    p.setPen(QPen(col, s["w"]))
    p.drawEllipse(out)


def paint_shapes(p, img, shapes):
    """Markup in image pixels. Blurs first (they sample the clean screenshot), then the spotlight dimming, magnifiers,
    and the marks, which stay crisp on top."""
    paint_blurs(p, img, shapes)
    spots = [s for s in shapes if s["t"] == "spotlight"]
    if spots:  # one dimming pass with a hole per spotlight
        dim, holes = QPainterPath(), QPainterPath()
        dim.addRect(QRectF(img.rect()))
        holes.setFillRule(Qt.WindingFill)  # overlapping spotlights stay bright where they overlap
        for sp in spots:
            holes.addRoundedRect(sp["r"], sp["w"] * 2.5, sp["w"] * 2.5)
        p.fillPath(dim.subtracted(holes), QColor(0, 0, 0, 150))
    for s in shapes:
        if s["t"] == "magnify":
            paint_magnifier(p, img, shapes, s)
    for s in shapes:
        t, c = s["t"], QColor(s["c"])
        if t == "rect":
            p.setPen(QPen(c, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(s["r"], s["w"] * 1.5, s["w"] * 1.5)
        elif t == "ellipse":
            p.setPen(QPen(c, s["w"]))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(s["r"])
        elif t == "pen":
            p.setPen(QPen(c, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(s["path"])
        elif t == "arrow":
            draw_arrow(p, s["a"], s["b"], c, s["w"])
        elif t == "highlight":
            c.setAlphaF(0.38)
            p.setPen(QPen(c, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(s["path"])
        elif t == "text":
            draw_text(p, s["at"], s["text"], c, s["size"])
        elif t == "step":
            rad = s["size"]
            p.setPen(QPen(QColor(255, 255, 255, 235), rad * 0.14))
            p.setBrush(c)
            p.drawEllipse(s["at"], rad, rad)
            p.setFont(text_font(rad * 1.1))
            p.setPen(QColor("black") if c.lightness() > 160 else QColor("white"))
            p.drawText(QRectF(s["at"].x() - rad, s["at"].y() - rad, 2 * rad, 2 * rad), Qt.AlignCenter, str(s["n"]))


def shape_bounds(s):
    """A mark's extent in image pixels (for selecting it and drawing its selection frame)."""
    t, w = s["t"], s.get("w", 0)
    if t == "blur":
        b = QRectF()
        for r in s.get("rs") or [s["r"]]:
            b = b.united(r)
    elif t == "arrow":
        b = QRectF(s["a"], s["b"]).normalized().adjusted(-w * 4, -w * 4, w * 4, w * 4)
    elif t in ("pen", "highlight"):
        b = s["path"].boundingRect()
    elif t == "text":
        fm = QFontMetricsF(text_font(s["size"]))
        lines = s["text"].split("\n")
        width = max(fm.horizontalAdvance(line) for line in lines)
        b = QRectF(s["at"].x(), s["at"].y() - fm.ascent(), width, fm.lineSpacing() * (len(lines) - 1) + fm.height())
    elif t == "step":
        b = QRectF(s["at"].x() - s["size"], s["at"].y() - s["size"], 2 * s["size"], 2 * s["size"])
    elif t == "magnify":
        b = s["r"].united(callout_rect(s))
    else:
        b = QRectF(s["r"])
    return b.adjusted(-w / 2, -w / 2, w / 2, w / 2)


def shape_hit(s, pt, tol):
    """Is `pt` (image pixels) on this mark? Strokes are hit along their line, areas anywhere inside."""
    t = s["t"]
    if t == "arrow":
        a, b = s["a"], s["b"]
        ab = b - a
        n = QPointF.dotProduct(ab, ab)
        k = 0 if n == 0 else max(0.0, min(1.0, QPointF.dotProduct(pt - a, ab) / n))
        return QLineF(pt, a + ab * k).length() <= tol + s["w"] * 2
    if t in ("pen", "highlight"):
        stroker = QPainterPathStroker()
        stroker.setWidth(s["w"] + 2 * tol)
        return stroker.createStroke(s["path"]).contains(pt)
    if t == "magnify":
        src, out = QPainterPath(), QPainterPath()
        src.addEllipse(s["r"].adjusted(-tol, -tol, tol, tol))
        out.addEllipse(callout_rect(s))
        return src.contains(pt) or out.contains(pt)
    if t == "blur" and s.get("rs"):
        return any(r.adjusted(-tol, -tol, tol, tol).contains(pt) for r in s["rs"])
    return shape_bounds(s).adjusted(-tol, -tol, tol, tol).contains(pt)


def shape_moved(s, d):
    """A copy of the mark shifted by d (shapes are never changed in place, so undo can keep the old ones)."""
    n = {k: v for k, v in s.items() if k != "_px"}
    if "r" in n:
        n["r"] = n["r"].translated(d)
    if "rs" in n:
        n["rs"] = [r.translated(d) for r in n["rs"]]
    for k in ("a", "b", "at"):
        if k in n:
            n[k] = n[k] + d
    if "path" in n:
        n["path"] = n["path"].translated(d)
    return n


def shape_scaled(s, f):
    """A copy with its stroke width / text size scaled by f (for [ and ] on a selected mark)."""
    n = {k: v for k, v in s.items() if k != "_px"}
    for k in ("w", "size"):
        if k in n:
            n[k] = n[k] * f
    return n


def crop_rect(shapes, img):
    crops = [s["r"] for s in shapes if s["t"] == "crop"]
    r = crops[-1].intersected(QRectF(img.rect())) if crops else QRectF()
    return r if r.width() >= 2 and r.height() >= 2 else None


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
NUMBER = re.compile(r"\+?\d[\d \-().]{2,}\d")
TOKEN = re.compile(r"\b(?:sk-(?:live-|test-|proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
                   r"|xox[abposr]-[A-Za-z0-9-]{10,}|eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,})")  # API keys and tokens
SECRET_WORDS = re.compile(r"\b(?:otp|code|pin|passcode|password|verification|cvv|ssn)\b", re.I)


def is_private_number(m, line=""):
    """Phones, cards, accounts, OTPs (also "482-913", or 4+ digits next to "OTP"/"code"), ID numbers; but not an order
    number next to a year ("#4821 2026"), a date, a time, an IP address, an amount or a plain decimal."""
    s = m.group().strip()
    digits = re.sub(r"\D", "", s)
    if line[m.end():m.end() + 1] == ":" or re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", s) or re.fullmatch(r"\d+[.,]\d+", s):
        return False
    if re.match(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}\b", s) or re.search(r"\d[.,]\d{2}\b", s):
        return False
    if SECRET_WORDS.search(line) and len(digits) >= 4:
        return True
    return (s.startswith("+") or len(digits) >= 10 or re.search(r"\d{6}", s) is not None
            or re.fullmatch(r"\d{3}[- ]\d{3}|\d{3}-\d{2}-\d{4}", s) is not None)


OCR_HINT = ("Install Tesseract OCR to enable this (Ubuntu: sudo apt install tesseract-ocr ·"
            " Windows: winget install UB-Mannheim.TesseractOCR)")


def tesseract():
    """Tesseract's path, including where the Windows installer puts it without adding it to PATH."""
    found = shutil.which("tesseract")
    if not found and os.name == "nt":
        found = shutil.which("tesseract", path=os.path.expandvars(r"%ProgramFiles%\Tesseract-OCR"))
    return found


def ocr_langs():
    """Tesseract's installed languages (its own "osd" helper model isn't one)."""
    out = subprocess.run([tesseract(), "--list-langs"], capture_output=True, text=True).stdout if tesseract() else ""
    return [lang for lang in out.splitlines()[1:] if lang.strip() and lang != "osd"]  # the first line is a heading


def run_ocr(owner, img, tsv, done):
    """Tesseract in a QProcess, so the UI doesn't wait for it. Calls done(output or None, scale), unless `owner` (the
    window that asked) has been closed and deleted meanwhile."""
    exe = tesseract()
    if not exe:
        return done(None, 1)
    scale = 2 if img.width() < 2000 else 1  # small UI text reads far better upscaled
    if scale != 1:
        img = img.scaled(img.width() * scale, img.height() * scale, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    path = tmp_dir() / f"ocr-{time.time_ns()}.bmp"  # BMP: ~20 ms to write on the UI thread, PNG ~250 ms
    img.convertToFormat(QImage.Format_RGB888).save(str(path), "BMP")
    proc = QProcess(QCoreApplication.instance())  # not the owner: it may close (and be deleted) before Tesseract ends
    QTimer.singleShot(60000, proc, proc.kill)  # a stuck Tesseract mustn't leave "Reading text…" up forever
    state = {"done": False}

    def finished(*_):
        if state["done"]:
            return
        state["done"] = True
        path.unlink(missing_ok=True)
        ok = proc.exitStatus() == QProcess.NormalExit and proc.exitCode() == 0
        out = bytes(proc.readAllStandardOutput()).decode(errors="ignore") if ok else None
        proc.deleteLater()
        if owner is None or isValid(owner):
            done(out, scale)
    proc.finished.connect(finished)
    proc.errorOccurred.connect(lambda err: finished() if err == QProcess.FailedToStart else None)
    langs = Config().ocr_langs
    proc.start(exe, [str(path), "stdout"] + (["-l", langs] if langs else []) + (["tsv"] if tsv else []))


def read_qr(img):
    """QR codes and barcodes in an image: copies what they say and returns what to tell the user."""
    codes = [r.text for r in zxingcpp.read_barcodes(img.convertToFormat(QImage.Format_Grayscale8)) if r.text]
    if not codes:
        return "No QR code or barcode found"
    set_clipboard(text="\n".join(codes))
    return f"Copied: {codes[0][:80]}" if len(codes) == 1 else f"Copied {len(codes)} codes"


def private_boxes(tsv, scale):
    """Emails and long numbers (phones, cards, OTPs) in Tesseract's word table, as image rects to blur."""
    lines = {}
    for row in tsv.splitlines()[1:]:
        c = row.split("\t")
        if len(c) == 12 and c[0] == "5" and c[11].strip():
            lines.setdefault(tuple(c[1:5]), []).append((c[11], QRect(int(c[6]), int(c[7]), int(c[8]), int(c[9]))))
    boxes = []
    for words in lines.values():
        text, spans = "", []
        for w, box in words:
            spans.append((len(text), len(text) + len(w), box))
            text += w + " "
        hits = (list(EMAIL.finditer(text)) + list(TOKEN.finditer(text))
                + [m for m in NUMBER.finditer(text) if is_private_number(m, text)])
        for m in hits:
            box = QRect()
            for a, b, wb in spans:
                if a < m.end() and b > m.start():
                    box = box.united(wb)
            boxes.append(QRectF(box.x() / scale, box.y() / scale, box.width() / scale, box.height() / scale).adjusted(-4, -4, 4, 4))
    return boxes


def set_clipboard(pix=None, path=None, text=None):
    """Wayland only lets a *focused* app set the clipboard, and thumbnails never take focus: hand it to wl-copy."""
    if WAYLAND and shutil.which("wl-copy"):
        try:
            if text is not None:
                subprocess.run(["wl-copy"], input=text.encode(), timeout=5, check=True)
            else:
                if path is None:
                    path = tmp_dir() / f"clip-{time.time_ns()}.png"
                    pix.save(str(path), "PNG")
                with open(path, "rb") as f:
                    subprocess.run(["wl-copy", "--type", "image/png"], stdin=f, timeout=5, check=True)
            return
        except (OSError, subprocess.SubprocessError):
            pass  # fall back to Qt (works whenever we do have focus)
    if text is not None:
        QGuiApplication.clipboard().setText(text)
    else:
        QGuiApplication.clipboard().setPixmap(pix)


def ocr_message(text):
    """Put recognised text on the clipboard; returns what to tell the user."""
    if text is None:
        return OCR_HINT if not tesseract() else "Couldn't read text from this screenshot"
    text = text.strip()
    if not text:
        return "No text found in this screenshot"
    set_clipboard(text=text)
    return f"Copied {len(text.splitlines())} line{'s' * (len(text.splitlines()) != 1)} of text"


def flat(pix):
    img = pix.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(1)
    return img


def beautify(pix):
    """Social-ready: padded gradient background, rounded corners, soft shadow."""
    img = flat(pix)
    w, h = img.width(), img.height()
    pad = max(48, int(max(w, h) * 0.06))
    rad = max(10, pad // 4)
    out = QImage(w + 2 * pad, h + 2 * pad, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, out.width(), out.height())
    g.setColorAt(0, QColor("#7b5cff"))
    g.setColorAt(1, QColor("#2fb4ff"))
    p.fillRect(out.rect(), g)
    r = QRectF(pad, pad, w, h)
    p.setPen(Qt.NoPen)
    for i in range(pad // 2, 0, -max(1, pad // 12)):
        p.setBrush(QColor(0, 0, 0, 10))
        p.drawRoundedRect(r.adjusted(-i, -i + pad / 8, i, i + pad / 8), rad + i, rad + i)
    clip = QPainterPath()
    clip.addRoundedRect(r, rad, rad)
    p.setClipPath(clip)
    p.drawImage(r, img)
    p.end()
    return QPixmap.fromImage(out)


def combine(pixes, gap=16):
    """Stack screenshots into one image (oldest on top): one picture to send instead of five."""
    top = max(px.devicePixelRatio() for px in pixes)
    imgs = []
    for px in pixes:  # a 1x shot next to a 2x one: scale it up so both read at the same size
        i, d = flat(px), px.devicePixelRatio()
        imgs.append(i if d >= top else i.scaled(round(i.width() * top / d), round(i.height() * top / d),
                                                Qt.KeepAspectRatio, Qt.SmoothTransformation))
    out = QImage(max(i.width() for i in imgs), sum(i.height() for i in imgs) + gap * (len(imgs) - 1), QImage.Format_RGB32)
    out.fill(QColor("white"))
    p = QPainter(out)
    y = 0
    for i in imgs:
        p.drawImage((out.width() - i.width()) // 2, y, i)
        y += i.height() + gap
    p.end()
    return QPixmap.fromImage(out)


def tool_icon(name, fg):
    pix = QPixmap(44, 44)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(fg)
    p.setPen(QPen(c, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    if name == "arrow":
        draw_arrow(p, QPointF(5, 17), QPointF(17, 5), c, 1.8)
    elif name == "rect":
        p.drawRoundedRect(QRectF(3.5, 5.5, 15, 11), 2.5, 2.5)
    elif name == "ellipse":
        p.drawEllipse(QRectF(3, 6, 16, 10))
    elif name == "pen":
        squiggle = QPainterPath(QPointF(3.5, 15))
        squiggle.cubicTo(QPointF(7, 3), QPointF(11, 21), QPointF(18.5, 7))
        p.drawPath(squiggle)
    elif name == "spotlight":  # dark surround, clear window in the middle
        shade = QPainterPath()
        shade.addRoundedRect(QRectF(2, 3, 18, 16), 3, 3)
        hole = QPainterPath()
        hole.addRoundedRect(QRectF(7, 7.5, 8, 7), 2, 2)
        q = QColor(c)
        q.setAlphaF(0.55)
        p.fillPath(shade.subtracted(hole), q)
        p.drawPath(hole)
    elif name == "highlight":
        h = QColor(c)
        h.setAlphaF(0.5)
        p.setPen(QPen(h, 6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(5, 16), QPointF(17, 6))
    elif name == "text":
        f = QFont()
        f.setPixelSize(16)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, 22, 22), Qt.AlignCenter, "T")
    elif name == "blur":
        for i in range(4):
            for j in range(4):
                q = QColor(c)
                q.setAlphaF(0.85 if (i + j) % 2 else 0.25)
                p.fillRect(QRectF(3 + i * 4, 3 + j * 4, 4, 4), q)
    elif name == "crop":
        p.drawPolyline(QPolygonF([QPointF(7, 2), QPointF(7, 15), QPointF(20, 15)]))
        p.drawPolyline(QPolygonF([QPointF(2, 7), QPointF(15, 7), QPointF(15, 20)]))
    elif name == "step":
        p.drawEllipse(QRectF(3, 3, 16, 16))
        f = QFont()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(3, 3, 16, 16), Qt.AlignCenter, "1")
    elif name == "private":  # an eye, crossed out
        p.drawEllipse(QRectF(3, 7, 16, 8))
        p.drawEllipse(QRectF(9, 9, 4, 4))
        p.drawLine(QPointF(4, 18), QPointF(18, 4))
    elif name == "ocr":  # a dashed selection box around a "T"
        p.setPen(QPen(c, 1.5, Qt.DashLine))
        p.drawRect(QRectF(2.5, 4.5, 17, 13))
        p.setPen(c)
        f = QFont()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(2.5, 4.5, 17, 13), Qt.AlignCenter, "T")
    elif name == "copytext":
        p.drawRoundedRect(QRectF(7, 3, 12, 14), 2, 2)
        p.drawLine(QPointF(10, 7.5), QPointF(16, 7.5))
        p.drawLine(QPointF(10, 11), QPointF(16, 11))
        p.drawPolyline(QPolygonF([QPointF(4, 7), QPointF(4, 19), QPointF(14, 19)]))
    elif name == "select":  # a mouse pointer
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(6, 3), QPointF(6, 17.5), QPointF(9.5, 14), QPointF(12, 19.5), QPointF(14, 18.5),
                                 QPointF(11.5, 13), QPointF(16.5, 13)]))
    elif name == "magnify":
        p.drawEllipse(QRectF(3, 3, 12, 12))
        p.setPen(QPen(c, 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(14, 14), QPointF(19, 19))
    elif name == "picker":  # an eyedropper
        p.drawLine(QPointF(4, 18), QPointF(13, 9))
        p.setPen(QPen(c, 4.5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(13.5, 8.5), QPointF(17, 5))
        p.setPen(QPen(c, 1.8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(11, 6.5), QPointF(15.5, 11))
    elif name in ("undo", "redo"):
        if name == "redo":
            p.translate(22, 0)
            p.scale(-1, 1)
        path = QPainterPath(QPointF(17, 17))
        path.cubicTo(QPointF(18, 8), QPointF(11, 5), QPointF(5, 9.5))
        p.drawPath(path)
        p.drawPolyline(QPolygonF([QPointF(5, 4.5), QPointF(5, 9.5), QPointF(10, 9.5)]))
    p.end()
    return QIcon(pix)


class TextBox(QPlainTextEdit):
    """Inline text entry on the canvas. Enter places the text, Shift+Enter starts a new line, Esc drops it."""

    def __init__(self, canvas, color, px):
        super().__init__(canvas)
        self.setFont(text_font(px))
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(2)
        self.set_color(color)
        self.textChanged.connect(self.fit)
        self.fit()

    def text(self):
        return self.toPlainText()

    def fit(self):
        fm = self.fontMetrics()
        lines = self.text().split("\n")
        self.resize(max(240, max(fm.horizontalAdvance(line) for line in lines) + 30), fm.lineSpacing() * len(lines) + 16)

    def set_color(self, c):
        self.setStyleSheet(f"QPlainTextEdit {{ background: rgba(0, 0, 0, 0.35); color: {c}; border: 1px dashed rgba(255, 255, 255, 0.8);"
                           " border-radius: 4px; padding: 2px 4px; }")

    def focusNextPrevChild(self, _):
        return False  # Tab stays in the box instead of moving focus to a toolbar button mid-word

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers() & Qt.ShiftModifier:
            return self.parent().commit_text()  # consumed: Enter must not also trigger the editor's Done
        if e.key() == Qt.Key_Escape:
            return self.parent().drop_text()
        super().keyPressEvent(e)


class Canvas(QWidget):
    """The screenshot, scaled to fit, with markup drawn on top. Shapes live in image pixels.

    Shapes are never changed in place once committed: every edit makes a new list, and undo/redo swap whole lists."""

    def __init__(self, editor, img, bg):
        super().__init__()
        self.ed, self.img, self.bg = editor, img, bg
        self.shapes, self.history, self.undone = [], [], []
        self.cur, self.text, self.sel, self.drag, self.proposals = None, None, None, None, []
        self.k, self.off, self.view = 1.0, QPointF(), QPixmap()
        self.rescale = QTimer(self, singleShot=True, interval=60, timeout=self.build_view)  # don't rescale on every resize step
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)  # the colour picker shows the hex under the pointer
        self.setMinimumSize(400, 260)

    def resizeEvent(self, _):
        iw, ih = self.img.width(), self.img.height()
        self.k = max(0.01, min((self.width() - 32) / iw, (self.height() - 32) / ih, 1 / self.devicePixelRatioF()))
        self.off = QPointF((self.width() - iw * self.k) / 2, (self.height() - ih * self.k) / 2)
        self.rescale.start()
        self.ed.place_status()

    def build_view(self):
        """Pre-scale the screenshot once per size, so drawing frames only blit it."""
        d = self.devicePixelRatioF()
        self.view = QPixmap.fromImage(self.img.scaled(round(self.img.width() * self.k * d), round(self.img.height() * self.k * d),
                                                      Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.view.setDevicePixelRatio(d)
        self.update()

    def to_img(self, pos):
        return (QPointF(pos) - self.off) / self.k

    def to_screen(self, r):
        return QRectF(self.off + r.topLeft() * self.k, r.size() * self.k)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), self.bg)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        target = QRectF(self.off, QSizeF(self.img.width() * self.k, self.img.height() * self.k))
        p.setPen(Qt.NoPen)
        for i in (8, 5, 2):  # soft shadow so the screenshot's edge reads on any background
            p.setBrush(QColor(0, 0, 0, 14))
            p.drawRoundedRect(target.adjusted(-i, -i + 2, i, i + 2), i, i)
        if self.view.isNull():
            p.drawImage(target, self.img)
        else:
            p.drawPixmap(target, self.view, QRectF(self.view.rect()))
        live = self.shapes + ([self.cur] if self.cur else [])
        p.save()
        p.translate(self.off)
        p.scale(self.k, self.k)
        p.setClipRect(QRectF(self.img.rect()))
        paint_shapes(p, self.img, live)
        paint_blurs(p, self.img, [s for s in self.proposals if s["on"]])  # a preview of what Enter will blur
        p.restore()
        crop = crop_rect(live, self.img)
        if crop is not None:
            shade, hole = QPainterPath(), QPainterPath()
            shade.addRect(target)
            hole.addRect(self.to_screen(crop))
            p.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 150))
            p.setPen(QPen(QColor("white"), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self.to_screen(crop))
        p.setBrush(Qt.NoBrush)
        if self.cur and self.cur["t"] in ("blur", "ocr"):
            p.setPen(QPen(ACCENT if self.cur["t"] == "ocr" else QColor("white"), 1.5, Qt.DashLine))
            p.drawRect(self.to_screen(self.cur["r"]))
        for s in self.proposals:  # solid: will be blurred; dashed: left visible
            p.setPen(QPen(ACCENT, 2, Qt.SolidLine if s["on"] else Qt.DashLine))
            p.drawRect(self.to_screen(s["r"]).adjusted(-2, -2, 2, 2))
        if self.sel is not None:
            p.setPen(QPen(ACCENT, 1.5, Qt.DashLine))
            p.drawRect(self.to_screen(shape_bounds(self.shapes[self.sel])).adjusted(-4, -4, 4, 4))

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self.commit_text()
        tool, c, a, z = self.ed.tool, self.ed.color, self.to_img(e.position()), self.ed.scale
        if self.proposals:  # reviewing "Hide private info": clicks only switch boxes on and off
            for s in self.proposals:
                if s["r"].contains(a):
                    s["on"] = not s["on"]
            return self.update()
        if tool == "select":
            self.sel = next((i for i in reversed(range(len(self.shapes))) if shape_hit(self.shapes[i], a, 6 / self.k)), None)
            if self.sel is not None:
                self.drag = (a, self.shapes[self.sel], list(self.shapes))
            return self.update()
        self.sel = None
        if tool == "picker":
            return self.ed.pick(self.pixel(a))
        if tool == "text":
            return self.start_text(e.position())
        if tool == "step":
            n = 1 + max((s["n"] for s in self.shapes if s["t"] == "step"), default=0)
            return self.push(dict(t="step", at=a, n=n, c=c, size=15 * z / self.k))
        w = (18 if tool == "highlight" else 4) * z / self.k  # widths are set in screen pixels: what you see is what you get
        self.cur = dict(t=tool, a=a, b=a, r=QRectF(a, a), c=c, w=w, path=QPainterPath(a), at=a)

    def pixel(self, a):
        if not QRectF(self.img.rect()).contains(a):
            return None
        return self.img.pixelColor(int(a.x()), int(a.y())).name()

    def mouseMoveEvent(self, e):
        b = self.to_img(e.position())
        if self.drag:
            start, orig, _ = self.drag
            self.shapes[self.sel] = shape_moved(orig, b - start)  # the list was copied on press: undo keeps the original
            return self.update()
        if not self.cur:
            if self.ed.tool == "picker":
                self.ed.say(self.pixel(b) or "")
            return
        cur = self.cur
        cur["b"], cur["r"] = b, QRectF(cur["a"], b).normalized()
        if cur["t"] == "magnify":  # a circle around where the drag started, its 2x callout placed alongside
            rad = QLineF(cur["a"], b).length()
            cur["r"] = QRectF(cur["a"] - QPointF(rad, rad), QSizeF(2 * rad, 2 * rad))
            cur["at"] = place_callout(cur["r"], self.img)
        cur["path"].lineTo(b)
        self.update()

    def mouseReleaseEvent(self, _):
        if self.drag:
            orig, before = self.drag[1], self.drag[2]
            self.drag = None
            if self.shapes[self.sel] is not orig:
                self.history.append(before)
                self.undone.clear()
            return
        cur, self.cur = self.cur, None
        if cur:
            size = cur["path"].length() if cur["t"] in ("highlight", "pen") else QLineF(cur["a"], cur["b"]).length()
            if size * self.k > 4:  # ignore stray clicks
                if cur["t"] == "ocr":
                    self.ed.copy_text_area(cur["r"])  # a selection, not a mark: nothing is drawn into the shot
                else:
                    self.push(cur)
            self.update()

    def change(self, shapes):
        self.history.append(self.shapes)
        self.shapes = shapes
        self.undone.clear()
        self.update()

    def push(self, shape):
        self.sel = None
        self.change(self.shapes + [shape])

    def delete_selected(self):
        if self.sel is not None:
            i, self.sel = self.sel, None
            self.change(self.shapes[:i] + self.shapes[i + 1:])

    def scale_selected(self, f):
        i = self.sel
        self.change(self.shapes[:i] + [shape_scaled(self.shapes[i], f)] + self.shapes[i + 1:])

    def undo(self):
        if self.text:
            return self.drop_text()
        if self.history and not self.drag:
            self.undone.append(self.shapes)
            self.shapes, self.sel = self.history.pop(), None
            self.update()

    def redo(self):
        if self.undone and not self.drag:
            self.history.append(self.shapes)
            self.shapes, self.sel = self.undone.pop(), None
            self.update()

    def propose(self, boxes):
        self.proposals = [dict(t="blur", r=r, on=True) for r in boxes]
        self.update()

    def apply_proposals(self, keep=True):
        boxes = [s["r"] for s in self.proposals if s["on"]] if keep else []
        self.proposals = []
        if boxes:
            self.push(dict(t="blur", rs=boxes, r=boxes[0], c="#000", w=1))
        self.update()
        return len(boxes)

    def start_text(self, pos):
        box = TextBox(self, self.ed.color, TEXT_PX * self.ed.scale)
        box.move(int(pos.x()) - 7, int(pos.y()) - box.fontMetrics().ascent() - 7)
        box.show()
        box.setFocus()
        self.text = (box, self.to_img(pos), self.ed.scale)

    def commit_text(self):
        if not self.text:
            return
        box, at, z = self.text
        self.text = None
        if box.text().strip():
            self.push(dict(t="text", at=at, text=box.text().rstrip("\n"), c=self.ed.color, size=TEXT_PX * z / self.k))
        box.deleteLater()
        self.setFocus()

    def drop_text(self):
        if self.text:
            self.text[0].deleteLater()
            self.text = None
            self.setFocus()

    def render(self, crop=True):
        out = self.img.copy()
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        paint_shapes(p, self.img, self.shapes)
        p.end()
        box = crop_rect(self.shapes, self.img) if crop else None
        return out.copy(box.toRect()) if box is not None else out


class Editor(QDialog):
    """Quick markup: arrows, boxes, highlighter, text, blur, crop. Done updates the thumbnail in place."""

    def __init__(self, app, pix, on_done):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)  # each editor holds a full-size copy of the shot: free it on close
        self.on_done, self.pending, self.finish_after_ocr = on_done, 0, False
        pal = themed(self, QSS + EDITOR_QSS)
        self.setWindowTitle(f"Markup · {APP}")
        self.setWindowIcon(icon())
        img = pix.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(1)
        self.tool, self.color, self.scale = "arrow", COLORS[0], 1
        self.canvas = Canvas(self, img, QColor(pal["bg"]))

        bar = QFrame(objectName="bar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(4)
        self.tools = QButtonGroup(self)
        ocr = bool(tesseract())
        for name, tip, key in TOOLS:
            b = self.tool_button(name, f"{tip}  ({key})" if name != "ocr" or ocr else OCR_HINT, pal["fg"],
                                 checkable=True, checked=name == self.tool)
            b.setEnabled(name != "ocr" or ocr)
            b.clicked.connect(lambda _=False, n=name: self.set_tool(n))
            b.setProperty("tool", name)
            self.tools.addButton(b)
            h.addWidget(b)
        h.addWidget(self.vsep())
        self.swatches = QButtonGroup(self)
        for c in COLORS:
            b = QToolButton(checkable=True, checked=c == self.color, cursor=Qt.PointingHandCursor, toolTip="Color")
            b.setFixedSize(24, 24)
            b.setStyleSheet(f"QToolButton {{ background: {c}; border: 2px solid {pal['border']}; border-radius: 12px; }}"
                            f"QToolButton:checked {{ border: 2px solid {pal['fg']}; }}")
            b.clicked.connect(lambda _=False, c=c: self.set_color(c))
            b.setProperty("color", c)
            self.swatches.addButton(b)
            h.addWidget(b)
        self.size_label = QLabel(objectName="hint", toolTip="Size of new marks, or of the selected one:  [  smaller,  ]  bigger")
        self.size_label.setFixedWidth(44)
        self.size_label.setAlignment(Qt.AlignCenter)
        h.addWidget(self.size_label)
        self.show_scale()
        h.addWidget(self.vsep())
        undo = self.tool_button("undo", "Undo  (Ctrl+Z)", pal["fg"])
        undo.clicked.connect(lambda: self.canvas.undo())
        redo = self.tool_button("redo", "Redo  (Ctrl+Shift+Z)", pal["fg"])
        redo.clicked.connect(lambda: self.canvas.redo())
        h.addWidget(undo)
        h.addWidget(redo)
        h.addWidget(self.vsep())
        private = self.tool_button("private", "Hide private info: blur emails, phone numbers, OTPs" if ocr else OCR_HINT, pal["fg"])
        private.clicked.connect(self.hide_private)
        copytext = self.tool_button("copytext", "Copy all the text (for just a part, drag a box with the X tool)" if ocr else OCR_HINT,
                                    pal["fg"])
        copytext.clicked.connect(self.copy_text)
        for b in (private, copytext):
            b.setEnabled(ocr)
            h.addWidget(b)
        self.status = QLabel(self.canvas, objectName="toast")  # floats over the canvas: the toolbar has no room to spare
        self.status.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.status.hide()
        self.status_timer = QTimer(self, singleShot=True, interval=4000, timeout=self.status.hide)
        h.addStretch()
        h.addWidget(QPushButton("Cancel", clicked=self.reject, autoDefault=False, cursor=Qt.PointingHandCursor))
        h.addWidget(QPushButton("Done", objectName="primary", clicked=self.finish, autoDefault=False, cursor=Qt.PointingHandCursor))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(bar)
        root.addWidget(self.canvas, 1)
        avail = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        d = app.devicePixelRatio()
        k = min(avail.width() * 0.85 * d / img.width(), (avail.height() * 0.85 - 56) * d / img.height(), 1.0)
        # at least 1120 wide: room for the whole toolbar
        self.resize(max(1120, int(img.width() / d * k) + 32), max(480, int(img.height() / d * k) + 32 + bar.sizeHint().height()))

    @staticmethod
    def tool_button(name, tip, fg, **kw):
        b = QToolButton(objectName="tool", toolTip=tip, icon=tool_icon(name, fg), cursor=Qt.PointingHandCursor, **kw)
        b.setIconSize(QSize(22, 22))
        return b

    @staticmethod
    def vsep():
        s = QFrame(objectName="sep")
        s.setFixedSize(1, 24)
        return s

    def set_tool(self, name):
        self.canvas.commit_text()
        self.tool = name
        self.canvas.sel = None
        self.canvas.update()
        self.canvas.setCursor({"text": Qt.IBeamCursor, "select": Qt.ArrowCursor}.get(name, Qt.CrossCursor))
        for b in self.tools.buttons():
            b.setChecked(b.property("tool") == name)

    def set_color(self, c):
        self.color = c
        self.swatches.setExclusive(False)  # a picked colour may match no swatch: then none is checked
        for b in self.swatches.buttons():
            b.setChecked(b.property("color") == c)
        self.swatches.setExclusive(True)
        if self.canvas.text:
            self.canvas.text[0].set_color(c)

    def pick(self, c):
        if c:
            self.set_color(c)
            set_clipboard(text=c)
            self.say(f"Colour {c} copied · now drawing in it")

    def show_scale(self):
        self.size_label.setText(f"{self.scale:g}×")

    def resize_marks(self, up):
        cv = self.canvas
        if cv.sel is not None:
            return cv.scale_selected(1.25 if up else 0.8)
        i = SCALES.index(self.scale) + (1 if up else -1)
        self.scale = SCALES[max(0, min(len(SCALES) - 1, i))]
        self.show_scale()

    def keyPressEvent(self, e):
        keys = {k: n for n, _, k in TOOLS}
        cv = self.canvas
        if e.matches(QKeySequence.Undo):
            cv.undo()
        elif e.matches(QKeySequence.Redo) or (e.key() == Qt.Key_Z and e.modifiers() == Qt.ControlModifier | Qt.ShiftModifier):
            cv.redo()
        elif cv.proposals and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
            n = cv.apply_proposals(keep=e.key() != Qt.Key_Escape)
            self.say(f"Blurred {n} private item{'s' * (n != 1)} · Ctrl+Z to undo" if n else "Nothing blurred")
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish()
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and cv.sel is not None:
            cv.delete_selected()
        elif e.key() == Qt.Key_Escape and cv.sel is not None:
            cv.sel = None
            cv.update()
        elif e.text() in ("[", "]"):
            self.resize_marks(e.text() == "]")
        elif not e.modifiers() and e.text().upper() in keys:
            if keys[e.text().upper()] != "ocr" or tesseract():
                self.set_tool(keys[e.text().upper()])
        else:
            super().keyPressEvent(e)  # Esc cancels

    def say(self, text, keep=False):
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self.place_status()
        self.status_timer.stop() if keep else self.status_timer.start()

    def place_status(self):
        s, cv = self.status, self.canvas
        s.adjustSize()
        s.move((cv.width() - s.width()) // 2, cv.height() - s.height() - 14)

    def ocr(self, img, tsv, done):
        """Run OCR for this editor; Done waits for it, so a redaction still being computed is never skipped."""
        self.pending += 1
        self.setCursor(Qt.BusyCursor)

        def finished(out, scale):
            self.pending -= 1
            if not self.pending:
                self.unsetCursor()
            done(out, scale)
            if self.finish_after_ocr and not self.pending:
                self.finish()
        run_ocr(self, img, tsv, finished)

    def hide_private(self):
        if self.pending:
            return self.say("Still reading the screenshot…")
        self.canvas.commit_text()
        self.say("Looking for private info…")

        def done(tsv, scale):
            boxes = private_boxes(tsv, scale) if tsv else []
            if boxes:
                self.canvas.propose(boxes)
                self.say(f"{len(boxes)} found · click a box to leave it visible · Enter blurs them · Esc cancels", keep=True)
            else:
                self.say("Nothing private found" if tsv is not None else "Couldn't read this screenshot")
        self.ocr(self.canvas.img, True, done)

    def copy_text_area(self, r):
        area = r.toRect().intersected(self.canvas.img.rect())
        if area.width() < 4 or area.height() < 4:
            return
        self.say("Reading text…")
        # what you see: blurred text stays hidden (the box is in uncropped image coordinates)
        self.ocr(self.canvas.render(crop=False).copy(area), False, lambda text, _scale: self.say(ocr_message(text)))

    def copy_text(self):
        self.say("Reading text…")
        self.ocr(self.canvas.render(), False, lambda text, _scale: self.say(ocr_message(text)))

    def finish(self):
        self.canvas.commit_text()
        self.canvas.apply_proposals()  # Done while reviewing private info: blur what's marked
        if self.pending:  # e.g. Done right after "Hide private info": wait, or the blur would be left out
            self.finish_after_ocr = True
            return self.say("Finishing after the text is read…")
        if self.canvas.shapes:
            self.on_done(QPixmap.fromImage(self.canvas.render()))
        self.accept()


# ---------- the floating thumbnail ----------

class Thumb(QWidget):
    def __init__(self, app, path, pix):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.app, self.path, self.full, self.editor, self.glide = app, path, pix, None, None
        self.video = path.suffix in VIDEO
        self.home = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        self.press, self.hover, self.pinned, self.dragging, self.leaving = None, False, False, False, False
        self.swiping, self.swipe_from, self.lifted = False, None, False
        self.setToolTip(f"Drag into any app (Shift: all of them) · Click to {'play' if self.video else 'mark up'} · "
                        "Flick to the edge to dismiss · Right-click for more")
        self.build()
        self.v = self.hv = self.swipe = 0.0  # appear progress, hover progress, swipe offset in px
        self.appear = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=320,
                                        easingCurve=QEasingCurve.OutCubic, valueChanged=self.set_v)
        self.hover_anim = QVariantAnimation(self, duration=150, easingCurve=QEasingCurve.OutCubic, valueChanged=self.set_hv)
        self.swipe_anim = QVariantAnimation(self, duration=260, easingCurve=QEasingCurve.OutBack, valueChanged=self.set_swipe)
        self.countdown = QVariantAnimation(self, startValue=1.0, endValue=0.0, valueChanged=self.update, finished=self.expire)
        self.restart()

    def build(self):
        d, size = self.app.devicePixelRatio(), self.app.cfg.size
        img = QPixmap(self.full)
        img.setDevicePixelRatio(1)
        self.pix = img.scaled(int(size * d), int(size * d), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.pix.setDevicePixelRatio(d)
        self.setFixedSize(int(self.pix.width() / d) + 2 * M, int(self.pix.height() / d) + 2 * M)
        self.card = self.render_card()

    def render_card(self):
        """Shadow + rounded screenshot + border, drawn once. Animation frames just blit this."""
        d = self.app.devicePixelRatio()
        card = QPixmap(round(self.width() * d), round(self.height() * d))
        card.setDevicePixelRatio(d)
        card.fill(Qt.transparent)
        p = QPainter(card)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect().adjusted(M, M, -M, -M))
        p.setPen(Qt.NoPen)
        for i in range(M, 0, -2):  # soft shadow
            p.setBrush(QColor(0, 0, 0, 10))
            p.drawRoundedRect(r.adjusted(-i, -i + 2, i, i + 2), 8 + i, 8 + i)
        clip = QPainterPath()
        clip.addRoundedRect(r, 8, 8)
        p.setClipPath(clip)
        p.drawPixmap(r, self.pix, QRectF(self.pix.rect()))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 220), 2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(clip)
        p.end()
        return card

    def side(self):
        return 1 if "right" in self.app.cfg.corner else -1

    def set_v(self, v):
        self.v = v
        self.update()

    def set_hv(self, v):
        self.hv = v
        self.update()

    def set_swipe(self, x):
        self.swipe = x
        self.update()

    def showEvent(self, _):
        if self.v == 0 and not self.leaving:
            self.appear.start()

    def restart(self):
        self.countdown.stop()
        if self.app.cfg.seconds and not (self.pinned or self.hover or self.dragging or self.swiping or self.leaving or self.editor):
            self.countdown.setDuration(self.app.cfg.seconds * 1000)
            self.countdown.start()

    def hold(self):
        self.countdown.stop()
        self.update()

    def close_rect(self):
        return QRect(M + 6, M + 6, 22, 22)

    def paintEvent(self, _):
        if self.lifted:
            return  # it's following the cursor as the drag image; the corner stays empty meanwhile
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        w, h, v = self.width(), self.height(), self.v
        fade = v * (1 - 0.7 * min(1.0, self.swipe / w))  # fades as it's flicked away
        s = 0.92 + 0.08 * v  # slide in from the screen edge while growing and fading in
        p.translate(w / 2 + self.side() * ((1 - v) * 40 + self.swipe), h / 2)
        p.scale(s, s)
        p.translate(-w / 2, -h / 2)
        p.setOpacity(fade)
        p.drawPixmap(0, 0, self.card)
        p.setPen(Qt.NoPen)
        if self.countdown.state() == QAbstractAnimation.Running:  # time left
            bar = QRectF(M + 10, h - M - 9, (w - 2 * M - 20), 4)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRoundedRect(bar, 2, 2)
            p.setBrush(QColor(255, 255, 255, 230))
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * self.countdown.currentValue(), bar.height()), 2, 2)
        if self.pinned:
            p.setBrush(ACCENT)
            p.drawEllipse(QRectF(w - M - 18, M + 8, 10, 10))
        if self.hv:
            p.setOpacity(fade * self.hv)
            c = QRectF(self.close_rect())
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawEllipse(c)
            p.setPen(QPen(QColor("white"), 2, Qt.SolidLine, Qt.RoundCap))
            x = c.adjusted(7.5, 7.5, -7.5, -7.5)
            p.drawLine(x.topLeft(), x.bottomRight())
            p.drawLine(x.topRight(), x.bottomLeft())

    def fade_hover(self, to):
        self.hover_anim.stop()
        self.hover_anim.setStartValue(self.hv)
        self.hover_anim.setEndValue(to)
        self.hover_anim.start()

    def enterEvent(self, _):
        self.hover = True
        self.hold()
        self.fade_hover(1.0)

    def leaveEvent(self, _):
        self.hover = False
        self.restart()
        self.fade_hover(0.0)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.press = e.position().toPoint()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self.swiping:
            return self.set_swipe(max(0.0, (pos.x() - self.swipe_from.x()) * self.side()))
        if self.press is None or (pos - self.press).manhattanLength() <= QApplication.startDragDistance():
            return
        d = pos - self.press
        if d.x() * self.side() > 0 and abs(d.x()) > 2 * abs(d.y()):  # toward the screen edge: a flick to dismiss
            self.swiping, self.swipe_from, self.press = True, self.press, None
            self.swipe_anim.stop()
            return self.hold()
        hot, self.press = self.press, None
        shift = (e.modifiers() | QApplication.keyboardModifiers()) & Qt.ShiftModifier
        group = list(self.app.thumbs) if shift and len(self.app.thumbs) > 1 else [self]  # Shift: send every shot at once
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(t.drag_file())) for t in group])
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.setPixmap(self.card)  # the whole thumbnail lifts off and follows the cursor, held where it was grabbed
        drag.setHotSpot(hot)
        for t in group:  # no timeout mid-drag: closing would delete us under drag.exec()
            t.dragging, t.lifted = True, DRAG_IMAGE_OK
            t.hold()
        # Copy only: a file manager taking Move would pull the file out from under Recent
        dropped = drag.exec(Qt.CopyAction, Qt.CopyAction) != Qt.IgnoreAction
        for t in group:
            if not isValid(t):  # closed meanwhile (the drag runs a nested event loop)
                continue
            t.dragging = False
            if dropped:
                t.dismiss()  # used: it's gone (still in Recent if the app didn't take it)
            else:
                t.lifted, t.v = False, 0.0  # nowhere to drop: slide back into the corner
                t.appear.start()
                t.restart()

    def mouseReleaseEvent(self, e):
        if self.swiping:
            self.swiping = False
            if self.swipe > 50:
                return self.dismiss()  # keeps sliding out from where the finger let go
            self.swipe_anim.setStartValue(self.swipe)
            self.swipe_anim.setEndValue(0.0)
            self.swipe_anim.start()  # not far enough: spring back
            return self.restart()
        if self.press is None:
            return
        self.press = None
        if self.close_rect().contains(e.position().toPoint()):
            self.dismiss()
        elif self.video:
            self.open()
        else:
            self.markup()

    def contextMenuEvent(self, e):
        self.hold()
        m = QMenu(self)
        if not self.video:
            m.addAction("Mark up…", self.markup)
            m.addAction("Copy", lambda: self.app.copy(self.full, self.path))
            m.addAction("Copy text", self.copy_text).setEnabled(bool(tesseract()))
            qr = m.addAction("Read QR code", lambda: self.app.tray.showMessage(APP, read_qr(self.full.toImage())))
            qr.setEnabled(zxingcpp is not None)
            m.addAction("Beautify", lambda: self.set_image(beautify(self.full))).setEnabled(self.editor is None)
            shots = sum(not t.video for t in self.app.thumbs)
            if shots > 1:
                m.addAction(f"Combine all {shots} into one", self.app.combine_all)
        m.addAction("Play" if self.video else "Open in viewer", self.open)
        m.addAction(f"Save to {Path(self.app.cfg.folder).name}", self.save)
        m.addAction("Save As…", self.save_as)
        if not self.video:
            m.addAction("Float on screen", self.float_on_screen)
        m.addAction("Let it hide" if self.pinned else "Keep (don't auto-hide)", self.toggle_pin)
        m.addSeparator()
        m.addAction("Close", self.dismiss)
        m.exec(e.globalPos())
        m.deleteLater()
        if isValid(self):
            self.restart()

    def copy_text(self):
        run_ocr(self.app, self.full.toImage(), False, lambda text, _scale: self.app.tray.showMessage(APP, ocr_message(text)))

    def open(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))

    def drag_file(self):
        if self.video or not self.app.cfg.small:
            return self.path
        jpg = self.path.with_suffix(".jpg")
        if not jpg.exists():
            img = self.full.toImage()
            if max(img.width(), img.height()) > SMALL_MAX:
                img = img.scaled(SMALL_MAX, SMALL_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            img.convertToFormat(QImage.Format_RGB888).save(str(jpg), "JPG", 85)
        return jpg

    def markup(self):
        if self.editor:
            self.editor.raise_()
            return self.editor.activateWindow()
        self.editor = Editor(self.app, self.full, self.set_image)
        self.editor.finished.connect(self.markup_closed)
        self.hold()
        self.editor.show()

    def markup_closed(self):
        self.editor = None
        self.restart()

    def set_image(self, pix):
        """Markup done: the thumbnail, its file, the clipboard and Recent all get the edited version."""
        self.full = pix
        pix.save(str(self.path), "PNG")
        self.path.with_suffix(".jpg").unlink(missing_ok=True)
        self.build()
        self.update()
        self.app.remember(self.path, pix)
        if self.app.cfg.copy:
            self.app.copy(pix, self.path)
        self.pinned = True  # an edited shot is meant to be used: it stays until you drag or close it
        self.app.layout()

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.update()

    def float_on_screen(self):
        w = PinWindow(self.app, self.full)
        w.show()
        w.activateWindow()  # so Esc closes it
        self.dismiss()  # the floating copy takes over; the shot stays in Recent

    def save(self, dest=None):
        dest = Path(dest or Path(self.app.cfg.folder).expanduser() / self.path.name)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, dest)
        except OSError as err:  # unplugged drive, no permission...: say so, it's still under Recent
            self.app.tray.showMessage(APP, f"Couldn't save the screenshot to {dest.parent}: {err.strerror or err}")
        self.dismiss()

    def save_as(self):
        start = str(Path(self.app.cfg.folder).expanduser() / self.path.name)
        ext = self.path.suffix
        dest, _ = QFileDialog.getSaveFileName(self, "Save recording" if self.video else "Save screenshot", start,
                                              f"{ext[1:].upper()} (*{ext})")
        if dest:
            self.save(dest)

    def expire(self):
        if self.app.cfg.save:
            self.save()
        else:
            self.dismiss()

    def dismiss(self):
        """The file stays (in Recent) until it falls off the list or SnapFloat quits."""
        if self.leaving:
            return
        self.leaving = True
        if self.editor:
            self.editor.reject()
        if self in self.app.thumbs:
            self.app.thumbs.remove(self)
        self.countdown.stop()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.app.layout()
        self.appear.stop()
        self.appear.setStartValue(self.v)
        self.appear.setEndValue(0.0)
        self.appear.setDuration(200)
        self.appear.setEasingCurve(QEasingCurve.InCubic)
        self.appear.finished.connect(self.close)
        self.appear.start()


class PinWindow(QWidget):
    """"Float on screen": the shot in its own always-on-top window, to keep in view while you work.
    Drag to move · scroll to zoom · Ctrl+scroll to fade · double-click or Esc to close."""

    def __init__(self, app, pix):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)  # fading is painted: Wayland has no window opacity
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("snapfloat-pin")  # Wayland: the GNOME extension keeps it on top
        self.app, self.pix, self.opacity = app, pix, 1.0
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.avail = scr.availableGeometry()
        size = QSizeF(pix.size()) / scr.devicePixelRatio()  # a 2x screen's shot floats at the size it was on screen
        k = min(1.0, self.avail.width() * 0.7 / size.width(), self.avail.height() * 0.7 / size.height())
        self.aspect = size.width() / size.height()
        self.resize((size * k).toSize())
        self.move(self.avail.center() - self.rect().center())
        self.setToolTip("Drag to move · scroll to zoom · Ctrl+scroll to fade · double-click or Esc to close")
        app.pins.append(self)  # top-level windows need a Python reference, or they're collected
        self.destroyed.connect(lambda: app.pins.remove(self))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(self.opacity)
        p.drawPixmap(QRectF(self.rect()), self.pix, QRectF(self.pix.rect()))
        p.setPen(QColor(0, 0, 0, 70))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.windowHandle().startSystemMove()  # the compositor moves it: works on Wayland too
        elif e.button() == Qt.RightButton:
            m = QMenu(self)
            m.addAction("Copy", lambda: self.app.copy(self.pix))
            m.addAction("Close", self.close)
            m.exec(e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, _):
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120
        if e.modifiers() & Qt.ControlModifier:
            self.opacity = max(0.2, min(1.0, self.opacity + 0.1 * steps))
            return self.update()
        w = max(80, min(self.avail.width(), round(self.width() * 1.1 ** steps)))
        self.resize(w, round(w / self.aspect))


# ---------- hotkeys ----------

KEY_NAMES = {  # key name as saved in settings (lowercased) -> (Windows virtual-key code, GNOME/X keysym)
    "print": (0x2C, "Print"), "prtsc": (0x2C, "Print"), "printscreen": (0x2C, "Print"), "space": (0x20, "space"),
    "tab": (0x09, "Tab"), "esc": (0x1B, "Escape"), "ins": (0x2D, "Insert"), "del": (0x2E, "Delete"), "home": (0x24, "Home"),
    "end": (0x23, "End"), "pgup": (0x21, "Page_Up"), "pgdown": (0x22, "Page_Down"), "left": (0x25, "Left"),
    "up": (0x26, "Up"), "right": (0x27, "Right"), "down": (0x28, "Down"), "plus": (0xBB, "plus"),
}
MOD_NAMES = {"ctrl": ("<Control>", 2), "control": ("<Control>", 2), "shift": ("<Shift>", 4), "alt": ("<Alt>", 1),
             "win": ("<Super>", 8), "super": ("<Super>", 8), "meta": ("<Super>", 8)}


def split_keys(seq):
    """'Ctrl+Shift++' -> ['Ctrl', 'Shift', 'Plus']: a trailing '+' is the plus key, not a separator."""
    seq = (seq or "").strip()
    if seq.endswith("+"):
        head = seq[:-1].rstrip("+")
        seq = head + "+Plus" if head else "Plus"
    return [p.strip() for p in seq.split("+") if p.strip()]


def is_fkey(name):
    return len(name) > 1 and name[0] in "fF" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24


class WinHotkeys(threading.Thread):
    """Windows global hotkeys via RegisterHotKey; the message loop must live on the registering thread."""
    MODS = {"alt": 1, "ctrl": 2, "control": 2, "shift": 4, "win": 8, "meta": 8, "super": 8}

    def __init__(self, keys, fire):
        super().__init__(daemon=True)
        self.keys, self.fire, self.tid, self.failed = keys, fire, None, []
        self.ready = threading.Event()

    def parse(self, seq):
        """'Ctrl+Shift+4' -> (modifiers, virtual-key); vk is 0 for a key we can't map."""
        mods, vk = 0x4000, 0  # MOD_NOREPEAT
        for part in split_keys(seq):
            low = part.lower()
            if low in self.MODS:
                mods |= self.MODS[low]
            elif low in KEY_NAMES:
                vk = KEY_NAMES[low][0]
            elif is_fkey(part):
                vk = 0x6F + int(part[1:])
            elif len(part) == 1:
                import ctypes
                vk = ctypes.windll.user32.VkKeyScanW(ord(part)) & 0xFF
        return mods, vk

    def run(self):
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        self.tid = ctypes.windll.kernel32.GetCurrentThreadId()
        try:
            for i, (cmd, seq) in enumerate(self.keys):
                if not seq:
                    continue
                mods, vk = self.parse(seq)
                if not vk or not u.RegisterHotKey(None, i + 1, mods, vk):
                    self.failed.append(seq)
        except Exception:  # a bad saved shortcut must never stop the app from starting
            self.failed.append("(invalid shortcut)")
        finally:
            self.ready.set()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                self.fire(self.keys[msg.wParam - 1][0])
        for i in range(len(self.keys)):
            u.UnregisterHotKey(None, i + 1)

    def stop(self):
        import ctypes
        self.ready.wait()
        ctypes.windll.user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)  # WM_QUIT
        self.join()


def gnome_accel(seq):
    """'Ctrl+Shift+4' -> '<Control><Shift>4', 'Ctrl+PgUp' -> '<Control>Page_Up', 'Ctrl++' -> '<Control>plus'"""
    parts = split_keys(seq)
    if not parts:
        return ""
    key = parts[-1]
    name = KEY_NAMES[key.lower()][1] if key.lower() in KEY_NAMES else key.upper() if is_fkey(key) else key.lower()
    return "".join(MOD_NAMES[p.lower()][0] for p in parts[:-1] if p.lower() in MOD_NAMES) + name


def gnome_bind(keys):
    """Linux has no app-level global hotkeys on Wayland: register GNOME custom shortcuts that call us."""
    if not shutil.which("gsettings") or "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        return False
    schema = "org.gnome.settings-daemon.plugins.media-keys"
    base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
    old = gsettings_list(schema, "custom-keybindings")
    ours, seen = [], {}
    for cmd, seq in keys:
        seen[cmd] = seen.get(cmd, 0) + 1
        path = f"{base}snapfloat-{cmd}{'' if seen[cmd] == 1 else seen[cmd]}/"  # snapfloat-region, snapfloat-region2, ...
        ours.append(path)
        rel = f"{schema}.custom-keybinding:{path}"
        for k, v in (("name", f"{APP} {cmd}"), ("command", shlex.join(CMD + [f"--{cmd}"])), ("binding", gnome_accel(seq))):
            subprocess.run(["gsettings", "set", rel, k, v])
    for path in (p for p in old if "/snapfloat-" in p and p not in ours):  # e.g. PrtSc just turned off
        subprocess.run(["gsettings", "reset-recursively", f"{schema}.custom-keybinding:{path}"])
    subprocess.run(["gsettings", "set", schema, "custom-keybindings", str([p for p in old if "/snapfloat-" not in p] + ours)])
    return True


def ensure_gnome_extension():
    """AppImage users never run install.sh: put the bundled GNOME extension in place (active after the next login)."""
    import filecmp
    src = BUNDLE / "gnome-extension"
    dst = Path.home() / ".local/share/gnome-shell/extensions" / EXT_UUID
    if not (WAYLAND and src.is_dir() and shutil.which("gsettings") and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "")):
        return
    fresh = not dst.exists()
    if fresh or filecmp.dircmp(src, dst).diff_files:  # new install, or an updated AppImage with a newer extension
        shutil.copytree(src, dst, dirs_exist_ok=True)
    exts = gsettings_list("org.gnome.shell", "enabled-extensions")
    if fresh and EXT_UUID not in exts:  # only on first install: respect a user who turned it off later
        subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(exts + [EXT_UUID])], capture_output=True)


def gnome_ext_problem():
    """None when the GNOME extension is placing thumbnails, else a sentence saying what to do."""
    if not WAYLAND or not shutil.which("gnome-extensions"):
        return None  # ponytail: not GNOME Wayland, nothing to nag about; KDE etc. get default window placement
    info = subprocess.run(["gnome-extensions", "info", EXT_UUID], capture_output=True, text=True).stdout
    if re.search(r"^\s*State:\s*(ACTIVE|ENABLED)\s*$", info, re.M):
        return None
    if "OUT OF DATE" in info:
        return "Your GNOME is newer than the SnapFloat extension supports, so thumbnails open in the middle. Update SnapFloat."
    return "Thumbnails open in the middle until the SnapFloat GNOME extension is active. Log out and back in once after installing."


def desktop_exec(args):
    """Quote a command line for a .desktop Exec= key: double quotes, \\ " ` $ escaped, % doubled."""
    def q(a):
        a = a.replace("%", "%%")
        return a if re.fullmatch(r"[\w./+%-]+", a) else '"' + re.sub(r'(["`$\\])', r"\\\1", a) + '"'
    return " ".join(q(a) for a in args)


def set_autostart(on):
    if os.name == "nt":
        exe = Path(sys.executable).with_name("pythonw.exe")
        cmd = subprocess.list2cmdline([str(exe) if exe.exists() and not getattr(sys, "frozen", False) else sys.executable] + CMD[1:])
        run = QSettings(r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run", QSettings.NativeFormat)
        run.setValue(APP, cmd) if on else run.remove(APP)
    else:
        f = Path.home() / ".config" / "autostart" / "snapfloat.desktop"
        if on:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"[Desktop Entry]\nType=Application\nName={APP}\nExec={desktop_exec(CMD)}\nX-GNOME-Autostart-enabled=true\n")
        else:
            f.unlink(missing_ok=True)


# ---------- settings ----------

SHIFTED = dict(zip("!@#$%^&*()", "1234567890"))  # ponytail: US layout; others may record the shifted symbol
QSS = """
QDialog { background: @bg; }
QLabel { color: @fg; font-size: 13px; }
QLabel#h1 { font-size: 20px; font-weight: 600; }
QLabel#sub, QLabel#hint { color: @muted; font-size: 12px; }
QLabel#section { color: @muted; font-size: 11px; font-weight: 600; padding-left: 4px; }
QFrame#card { background: @card; border: 1px solid @border; border-radius: 12px; }
QFrame#sep { background: @border; border: none; }
QLineEdit { background: @input; color: @fg; border: 1px solid @border; border-radius: 7px; padding: 6px 9px; font-size: 13px; }
QLineEdit:focus { border: 1px solid @accent; }
QPushButton { background: @input; color: @fg; border: 1px solid @border; border-radius: 7px; padding: 7px 16px; font-size: 13px; }
QPushButton:hover { border: 1px solid @accent; }
QPushButton#primary { background: @accent; border: 1px solid @accent; color: white; font-weight: 600; }
QPushButton#primary:hover { background: @accenthover; }
QSlider::groove:horizontal { height: 4px; background: @border; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @accent; border-radius: 2px; }
QSlider::handle:horizontal { background: white; border: 1px solid @border; width: 18px; height: 18px; margin: -8px 0; border-radius: 9px; }
QFrame#screen { background: @input; border: 1px solid @border; border-radius: 7px; }
QToolButton#corner { background: @border; border: none; border-radius: 4px; }
QToolButton#corner:hover { background: @muted; }
QToolButton#corner:checked { background: @accent; }
QLabel#warn { background: rgba(255, 176, 32, 0.18); border: 1px solid #ffb020; border-radius: 10px; padding: 10px 12px; }
QScrollArea, QWidget#body { background: @bg; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @border; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { height: 0; background: none; }
"""
EDITOR_QSS = """
QFrame#bar { background: @card; border: none; border-bottom: 1px solid @border; }
QToolButton#tool { background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }
QToolButton#tool:hover { background: @input; }
QToolButton#tool:checked { background: rgba(10, 132, 255, 0.22); border: 1px solid @accent; }
QLabel#toast { background: rgba(28, 28, 30, 0.9); color: white; border-radius: 10px; padding: 7px 14px; }
"""


def themed(widget, qss):
    """Apply a stylesheet in the system's light or dark palette; returns the palette."""
    dark = (QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
            or widget.palette().window().color().lightness() < 128)
    pal = (dict(bg="#1c1c1e", card="#2c2c2e", input="#3a3a3c", border="#48484a", fg="#f2f2f7", muted="#98989d") if dark else
           dict(bg="#f2f2f7", card="#ffffff", input="#ffffff", border="#d8d8dd", fg="#1c1c1e", muted="#6e6e73"))
    for k, v in sorted(dict(pal, accent=ACCENT.name(), accenthover=ACCENT.lighter(115).name()).items(), key=lambda kv: -len(kv[0])):
        qss = qss.replace("@" + k, v)
    widget.setStyleSheet(qss)
    return pal


class Toggle(QCheckBox):
    """Switch with a sliding knob."""

    def __init__(self, checked, off):
        super().__init__(checked=checked, cursor=Qt.PointingHandCursor)
        self.setFixedSize(44, 26)
        self.off, self.t = QColor(off), float(checked)
        self.anim = QVariantAnimation(self, duration=180, easingCurve=QEasingCurve.OutCubic, valueChanged=self.slide)
        self.toggled.connect(self.animate)

    def animate(self, on):
        self.anim.stop()
        self.anim.setStartValue(self.t)
        self.anim.setEndValue(float(on))
        self.anim.start()

    def slide(self, t):
        self.t = t
        self.update()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        a, b = self.off.getRgbF()[:3], ACCENT.getRgbF()[:3]
        p.setBrush(QColor.fromRgbF(*(x + (y - x) * self.t for x, y in zip(a, b))))
        p.drawRoundedRect(QRectF(self.rect()), 13, 13)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(3 + self.t * 18, 3, 20, 20))


class KeyEdit(QLineEdit):
    """Click, press a combo, done. Backspace clears."""

    def __init__(self, text):
        super().__init__(text, readOnly=True, alignment=Qt.AlignCenter, placeholderText="Press keys…", cursor=Qt.PointingHandCursor)
        self.setFixedWidth(170)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Backspace, Qt.Key_Delete):
            return self.clear()
        if k in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_Super_L, Qt.Key_Super_R, Qt.Key_Escape, Qt.Key_Tab):
            return super().keyPressEvent(e) if k in (Qt.Key_Escape, Qt.Key_Tab) else None
        mods = [n for f, n in ((Qt.ControlModifier, "Ctrl"), (Qt.AltModifier, "Alt"), (Qt.ShiftModifier, "Shift"),
                               (Qt.MetaModifier, "Super")) if e.modifiers() & f]
        name = SHIFTED.get(QKeySequence(k).toString(), QKeySequence(k).toString())
        if name == "+":
            name = "Plus"
        if len(name) > 1 and name.lower() not in KEY_NAMES and not is_fkey(name):
            return  # a key we couldn't register as a shortcut: ignore it rather than save a dead binding
        self.setText("+".join(mods + [name]))


class CornerPicker(QFrame):
    """A tiny screen: click the corner the thumbnail should live in."""

    def __init__(self, value):
        super().__init__(objectName="screen")
        self.setFixedSize(110, 68)
        g = QGridLayout(self)
        g.setContentsMargins(6, 6, 6, 6)
        g.setRowStretch(1, 1)
        g.setColumnStretch(1, 1)
        self.group = QButtonGroup(self)
        for c in CORNERS:
            b = QToolButton(objectName="corner", checkable=True, checked=c == value, cursor=Qt.PointingHandCursor,
                            toolTip=c.replace("-", " ").capitalize())
            b.setFixedSize(30, 18)
            b.setProperty("corner", c)
            self.group.addButton(b)
            g.addWidget(b, 0 if c.startswith("top") else 2, 0 if c.endswith("left") else 2)

    def value(self):
        return self.group.checkedButton().property("corner")


class Settings(QDialog):
    def __init__(self, app):
        super().__init__()
        self.app = app
        c = app.cfg
        pal = themed(self, QSS)
        self.setWindowTitle(f"{APP} settings")
        self.setWindowIcon(icon())
        self.setFixedWidth(500)

        def toggle(v):
            return Toggle(v, pal["border"])

        self.seconds = self.slider(0, 30, min(c.seconds, 30), lambda v: "Until closed" if v == 0 else f"{v} s")
        self.thumb_size = self.slider(120, 400, c.size, lambda v: f"{v} px")
        self.corner = CornerPicker(c.corner)
        self.copy_t, self.save_t, self.small_t = toggle(c.copy), toggle(c.save), toggle(c.small)
        self.autostart_t, self.watch_t = toggle(c.autostart), toggle(c.watch_clipboard)
        self.sound_t, self.flash_t, self.print_t = toggle(c.sound), toggle(c.flash), toggle(c.print_key)
        self.folder = QLineEdit(c.folder, toolTip=c.folder)
        folder_row = QWidget(minimumWidth=270)
        fl = QHBoxLayout(folder_row)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self.folder, 1)
        fl.addWidget(QPushButton("Browse…", clicked=self.browse, cursor=Qt.PointingHandCursor))
        self.region_key, self.screen_key = KeyEdit(c.region_key), KeyEdit(c.screen_key)
        self.langs = ocr_langs()
        self.ocr_langs = QLineEdit(c.ocr_langs, placeholderText="e.g. eng+hin", toolTip="Installed: " + ", ".join(self.langs))
        self.ocr_langs.setFixedWidth(130)

        # The rows live in a scroll area: on short screens they keep their natural height (instead of being squeezed until
        # labels overlap) and the window scrolls; Cancel/Save stay pinned below.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QWidget(objectName="body")
        root = QVBoxLayout(body)
        root.setContentsMargins(22, 22, 22, 12)
        root.setSpacing(16)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icon().pixmap(44, 44))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(QLabel(APP, objectName="h1"))
        titles.addWidget(QLabel("Screenshots that float, then disappear.", objectName="sub"))
        header.addWidget(logo)
        header.addSpacing(6)
        header.addLayout(titles, 1)
        root.addLayout(header)
        problem = gnome_ext_problem()
        if problem:
            root.addWidget(QLabel(f"⚠  {problem}", objectName="warn", wordWrap=True))

        root.addWidget(self.card("Thumbnail", [
            ("Show for", "Hovering pauses it", self.seconds),
            ("Position", "Corner of the screen", self.corner),
            ("Size", None, self.thumb_size)]))
        root.addWidget(self.card("After capture", [
            ("Copy to clipboard", "Paste anywhere with Ctrl+V", self.copy_t),
            ("Send smaller files", f"Drag as JPG, max {SMALL_MAX} px: faster uploads", self.small_t),
            ("Save when it disappears", "Off: it's deleted, nothing piles up", self.save_t),
            ("Save folder", None, folder_row)]))
        system = "GNOME's" if sys.platform.startswith("linux") else "Snipping Tool's"
        root.addWidget(self.card("Shortcuts", [
            ("Print Screen key", f"PrtSc: region · Shift+PrtSc: whole screen (instead of {system})", self.print_t),
            ("Capture region", "Also works, any time", self.region_key),
            ("Capture screen", "Click a field and press keys · Backspace clears", self.screen_key)]))
        general = [("Start at login", "Otherwise the first shortcut has to start it (slower)", self.autostart_t)]
        if self.langs:
            more = ", ".join(self.langs[:4]) + ("…" if len(self.langs) > 4 else "")
            general.append(("Text languages", f"For Copy text · installed: {more}", self.ocr_langs))
        if not WAYLAND:  # on Wayland GNOME's screenshot service already flashes and plays its sound
            general += [("Shutter sound", None, self.sound_t), ("Screen flash", None, self.flash_t),
                        ("Also catch copied images", "Win+Shift+S, PrtSc", self.watch_t)]
        general.append(("Uninstall", "Removes shortcuts, autostart, settings and temp files",
                        QPushButton("Uninstall…", clicked=self.uninstall, cursor=Qt.PointingHandCursor)))
        root.addWidget(self.card("General", general))

        buttons = QHBoxLayout()
        credit = QLabel(f'Made by {AUTHOR} · <a href="{HOMEPAGE}" style="color: {ACCENT.name()}; text-decoration: none;">GitHub</a>',
                        objectName="hint", openExternalLinks=True)
        buttons.addWidget(credit)
        buttons.addStretch()
        buttons.addWidget(QPushButton("Cancel", clicked=self.reject, cursor=Qt.PointingHandCursor))
        save = QPushButton("Save", objectName="primary", clicked=self.accept, cursor=Qt.PointingHandCursor, default=True)
        buttons.addWidget(save)
        footer = QWidget(objectName="body")
        footer.setLayout(buttons)
        buttons.setContentsMargins(22, 10, 22, 16)
        outer.addWidget(footer)
        avail = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        self.resize(500, min(body.sizeHint().height() + footer.sizeHint().height() + 4, int(avail.height() * 0.9)))

    @staticmethod
    def slider(lo, hi, value, fmt):
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        s = QSlider(Qt.Horizontal, minimum=lo, maximum=hi, value=value, cursor=Qt.PointingHandCursor)
        s.setFixedWidth(170)
        label = QLabel(fmt(value), objectName="hint", minimumWidth=78, alignment=Qt.AlignRight | Qt.AlignVCenter)
        s.valueChanged.connect(lambda v: label.setText(fmt(v)))
        h.addWidget(s)
        h.addWidget(label)
        box.value = s.value
        return box

    @staticmethod
    def card(title, rows):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(QLabel(title.upper(), objectName="section"))
        frame = QFrame(objectName="card")
        f = QVBoxLayout(frame)
        f.setContentsMargins(16, 4, 16, 4)
        f.setSpacing(0)
        for i, (label, hint, w) in enumerate(rows):
            if i:
                sep = QFrame(objectName="sep")
                sep.setFixedHeight(1)
                f.addWidget(sep)
            row = QHBoxLayout()
            row.setContentsMargins(0, 10, 0, 10)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(QLabel(label))
            if hint:
                text.addWidget(QLabel(hint, objectName="hint"))
            row.addLayout(text, 1)
            row.addWidget(w)
            f.addLayout(row)
        v.addWidget(frame)
        return box

    def uninstall(self):
        if QMessageBox.question(self, f"Uninstall {APP}", "Remove SnapFloat's shortcuts, autostart, settings and temporary "
                                "screenshots? Screenshots you saved stay where they are.") != QMessageBox.Yes:
            return
        uninstall(quit_running=False)  # we are the running instance
        QMessageBox.information(self, APP, "SnapFloat removed. Delete SnapFloat.exe to finish." if os.name == "nt" else
                                "SnapFloat removed.")
        self.app.quit()

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def accept(self):
        values = dict(seconds=self.seconds.value(), corner=self.corner.value(), size=self.thumb_size.value(),
                      copy=self.copy_t.isChecked(), save=self.save_t.isChecked(), small=self.small_t.isChecked(),
                      folder=self.folder.text().strip() or DEFAULTS["folder"], region_key=self.region_key.text(),
                      screen_key=self.screen_key.text(), autostart=self.autostart_t.isChecked(),
                      watch_clipboard=self.watch_t.isChecked(), sound=self.sound_t.isChecked(), flash=self.flash_t.isChecked(),
                      print_key=self.print_t.isChecked(),
                      ocr_langs="+".join(lang for lang in re.split(r"[+,\s]+", self.ocr_langs.text()) if lang in self.langs))
        for k, v in values.items():
            self.app.cfg.set(k, v)
        self.app.apply()
        super().accept()


# ---------- app ----------

class App(QApplication):
    hotkey = Signal(str)

    def __init__(self):
        super().__init__(sys.argv[:1])
        self.setQuitOnLastWindowClosed(False)
        self.setApplicationName(APP)
        self.setDesktopFileName("snapfloat")  # Wayland app_id: how the GNOME extension spots our windows
        self.setWindowIcon(icon())
        self.cfg, self.thumbs, self.recent, self.dialog, self.hotkeys = Config(), [], [], None, None
        self.pins, self.scroller, self.portal_to = [], None, None
        self.flash = self.pending = None  # keep these short-lived windows referenced while they animate
        self.busy_since, self.submenus = 0.0, []
        tmp_dir()
        purge()
        self.aboutToQuit.connect(purge)
        try:  # 1.0/1.1 kept shots in /tmp: clear that out, if it's really ours
            st = os.lstat(LEGACY_TMP)
            if LEGACY_TMP != TMP and stat.S_ISDIR(st.st_mode) and (os.name == "nt" or st.st_uid == os.getuid()):
                shutil.rmtree(LEGACY_TMP, ignore_errors=True)
        except OSError:
            pass

        QLocalServer.removeServer(SERVER)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self.on_connection)
        if not self.server.listen(SERVER):
            print(f"{APP}: can't listen on {SERVER}: {self.server.errorString()}", file=sys.stderr)

        if WAYLAND:
            self.portal = Portal()
            self.portal.done.connect(self.on_portal)
            self.portal.error.connect(lambda msg: self.tray.showMessage(APP, f"Couldn't take a screenshot: {msg}"))
        self.hotkey.connect(self.command)
        self.clipboard().dataChanged.connect(self.on_clipboard)
        self.recorder = Recorder()
        self.recorder.finished.connect(self.recorded)
        self.recorder.error.connect(lambda msg: self.tray.showMessage(APP, f"Couldn't record the screen: {msg}"))

        self.tray = QSystemTrayIcon(icon(), self)
        self.tray.setToolTip(f"{APP} · made by {AUTHOR}")
        self.tray.activated.connect(self.tray_click)
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.show()
        ensure_gnome_extension()
        self.apply()
        problem = gnome_ext_problem()
        if problem:
            self.tray.showMessage(APP, problem)

    def apply(self):
        c = self.cfg
        for m in self.submenus:  # menu.clear() leaves the old submenus alive
            m.deleteLater()
        self.menu.clear()
        self.menu.addAction(f"Capture region\t{'PrtSc' if c.print_key else c.region_key}", self.region)
        self.menu.addAction(f"Capture screen\t{'Shift+PrtSc' if c.print_key else c.screen_key}", self.screen)
        self.menu.addAction("Scrolling capture", self.scrolling)
        later = self.menu.addMenu("Capture after…")
        for n in (3, 5, 10):
            later.addAction(f"{n} seconds", lambda n=n: self.delayed(n))
        missing = record_missing()
        label = "Stop recording" if self.recorder.busy else f"Record screen (install {missing})" if missing else "Record screen"
        self.record_action = self.menu.addAction(label, self.record)
        self.record_action.setEnabled(self.recorder.busy or not missing)
        self.recent_menu = self.menu.addMenu("Recent")
        self.submenus = [later, self.recent_menu]
        self.build_recent()
        self.menu.addSeparator()
        self.menu.addAction("Settings…", self.settings)
        self.menu.addAction("Quit", self.quit)
        keys = [("region", c.region_key), ("screen", c.screen_key)]
        if c.print_key:
            keys += [("region", "Print"), ("screen", "Shift+Print")]
        free_print_key(c.print_key)
        if os.name == "nt":
            if self.hotkeys:
                self.hotkeys.stop()
            self.hotkeys = WinHotkeys(keys, self.hotkey.emit)
            self.hotkeys.start()
            self.hotkeys.ready.wait(3)
            if self.hotkeys.failed:
                self.tray.showMessage(APP, f"Shortcut already used by another app: {', '.join(self.hotkeys.failed)}")
        else:  # ponytail: GNOME only; on KDE/XFCE bind `snapfloat --region` by hand (see README)
            wanted = repr((keys, CMD))
            registered = "/snapfloat-" in "".join(gsettings_list("org.gnome.settings-daemon.plugins.media-keys",
                                                                 "custom-keybindings")) if shutil.which("gsettings") else False
            if wanted != c.bound or not registered:  # ~20 gsettings calls: only when something actually changed
                if gnome_bind(keys):
                    c.set("bound", wanted)
        set_autostart(c.autostart)
        self.layout()

    def on_connection(self):
        sock = self.server.nextPendingConnection()
        sock.readyRead.connect(lambda: self.command(bytes(sock.readAll()).decode().strip()))
        sock.disconnected.connect(sock.deleteLater)

    def command(self, cmd):
        {"region": self.region, "screen": self.screen, "settings": self.settings, "quit": self.quit}.get(cmd, lambda: None)()

    def tray_click(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.record() if self.recorder.busy else self.region()  # while recording, a click on the tray stops it

    def record(self):
        if self.recorder.busy:
            return self.recorder.stop()
        self.recorder.start()
        self.record_action.setText("Stop recording")
        self.tray.setIcon(recording_icon())
        self.tray.setToolTip(f"{APP} · recording: click to stop")

    def recorded(self, path):
        self.record_action.setText("Record screen")
        self.tray.setIcon(icon())
        self.tray.setToolTip(f"{APP} · made by {AUTHOR}")
        if path:
            self.show_shot(poster(Path(path)), Path(path), copy=False)

    def hide_thumbs(self, then):
        for t in self.thumbs:
            t.hide()
        QTimer.singleShot(150, then)  # let the compositor repaint without them

    def start_capture(self):
        """One capture at a time: a second PrtSc while the first is on screen would stack two sessions."""
        if self.busy_since and time.monotonic() - self.busy_since < 60:  # 60 s: never stuck if a reply got lost
            return False
        self.busy_since = time.monotonic()
        return True

    def region(self):
        if self.start_capture():
            self.capture_region()

    def capture_region(self):
        self.hide_thumbs(lambda: self.portal.capture(True) if WAYLAND else select_region(self.captured))

    def screen(self):
        if not self.start_capture():
            return

        def grab():
            if WAYLAND:
                return self.portal.capture(False)
            scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            self.captured(scr.grabWindow(0))
        self.hide_thumbs(grab)

    def scrolling(self):
        """Scrolling capture: pick the area once, then scroll through it (see ScrollCapture)."""
        if not self.start_capture():
            return
        if WAYLAND:
            self.portal_to = self.scroll_start
            self.hide_thumbs(lambda: self.portal.capture(True))
        else:
            self.hide_thumbs(lambda: select_region(lambda pix: self.scroll_start(pix.toImage() if pix else None)))

    def full_screen(self, then):
        """then(screenshot of the screen in use, or None). On Wayland the portal takes it, so it arrives later."""
        if WAYLAND:
            self.portal_to = then
            return self.portal.capture(False)
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        then(scr.grabWindow(0).toImage())

    def scroll_start(self, first):
        """The area was picked. Neither the portal nor our selector says where it is on screen, so find it in a full
        screenshot taken straight away; every later grab is cropped to that spot."""
        if first is None or first.isNull():
            return self.show_shot(None)
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

        def found(full):
            rect = locate(first, full) if full is not None else None
            if rect is None:
                self.tray.showMessage(APP, "Scrolling capture lost track of the area (did it change?). Please try again.")
                return self.show_shot(None)
            size = full.size()

            def grab(then):
                self.full_screen(lambda img: then(img.copy(rect) if img is not None and img.size() == size else None))
            self.scroller = ScrollCapture(self, first, grab, None if WAYLAND else (scr, rect))
            self.scroller.show()
        self.full_screen(found)

    def delayed(self, seconds):
        if not self.start_capture():
            return
        for t in self.thumbs:
            t.hide()
        self.pending = Countdown(self, seconds, self.capture_region)
        self.pending.show()

    def captured(self, pix):
        """Windows/X11 capture finished. (On Wayland GNOME gives the feedback itself.)"""
        if pix is not None and not pix.isNull():
            if self.cfg.sound:
                shutter()
            if self.cfg.flash:
                self.flash = Flash(QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
                self.flash.show()
        self.show_shot(pix)

    def on_portal(self, src):
        if self.portal_to:  # a scrolling capture asked for this one
            then, self.portal_to = self.portal_to, None
            img = QImage(src) if src and Path(src).exists() else None
            if src:
                Path(src).unlink(missing_ok=True)  # the portal drops files in ~/Pictures: don't leave them there
            return then(img if img is not None and not img.isNull() else None)
        if not src or not Path(src).exists():
            return self.show_shot(None)
        dst = new_path()
        shutil.move(src, dst)  # the portal drops files in ~/Pictures: take it out of there
        self.show_shot(QPixmap(str(dst)), dst)

    def on_clipboard(self):
        cb = self.clipboard()
        if WAYLAND:  # not offered there, and our own wl-copy would look like "someone else copied an image"
            return
        if self.cfg.watch_clipboard and not cb.ownsClipboard() and cb.mimeData().hasImage():
            self.show_shot(QPixmap.fromImage(cb.image()), copy=False)

    def copy(self, pix, path=None):
        set_clipboard(pix, path)

    def remember(self, path, pix):
        """Recent: the last few shots of this session, re-draggable after their thumbnail is gone."""
        self.recent = [r for r in self.recent if r[0] != path]
        self.recent.insert(0, (path, time.strftime("%H:%M:%S"), QIcon(pix.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))))
        for old, _, _ in self.recent[MAX_RECENT:]:
            if all(t.path != old for t in self.thumbs):
                forget(old)  # ponytail: one still on screen is cleaned up on quit instead
        del self.recent[MAX_RECENT:]
        self.build_recent()

    def build_recent(self):
        m = self.recent_menu
        m.clear()
        for path, when, ic in self.recent:
            m.addAction(ic, when, lambda p=path: self.reshow(p))
        if self.recent:
            m.addSeparator()
            m.addAction("Clear", self.clear_recent)
        m.menuAction().setEnabled(bool(self.recent))

    def reshow(self, path):
        if not path.exists():  # e.g. the temp folder was cleaned behind our back
            self.recent = [r for r in self.recent if r[0] != path]
            self.build_recent()
            return self.tray.showMessage(APP, "That screenshot is no longer available.")
        if all(t.path != path for t in self.thumbs):
            pix = QPixmap(str(path.with_suffix(".png")))  # a recording's thumbnail sits beside it
            if pix.isNull():
                return self.tray.showMessage(APP, "That screenshot couldn't be opened.")
            self.show_shot(pix, path, copy=False)

    def combine_all(self):
        if any(t.editor for t in self.thumbs):
            return self.tray.showMessage(APP, "Finish marking up first: combining would close the editor.")
        shots = [t for t in self.thumbs if not t.video]
        pix = combine([t.full for t in shots])
        for t in shots:
            t.dismiss()
        self.show_shot(pix)

    def clear_recent(self):
        shown = {t.path for t in self.thumbs}
        for path, _, _ in self.recent:
            if path not in shown:
                forget(path)
        self.recent = [r for r in self.recent if r[0] in shown]
        self.build_recent()

    def show_shot(self, pix, path=None, copy=True):
        self.busy_since = 0.0  # every capture ends here, taken or cancelled
        if pix is not None and not pix.isNull():
            if path is None:
                path = new_path()
                pix.save(str(path), "PNG")
            if copy and self.cfg.copy:
                self.copy(pix, path)
            self.thumbs.append(Thumb(self, path, pix))
            self.remember(path, pix)
            if len(self.thumbs) > MAX_THUMBS:  # oldest first, but never one being edited, dragged or pinned
                victim = next((t for t in self.thumbs[:-1] if not (t.editor or t.pinned or t.dragging)), None)
                if victim:
                    victim.dismiss()
        self.layout()
        for t in self.thumbs:
            t.show()

    def corner_pos(self, w, h, offset, scr=None):
        scr = scr or (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        corner = self.cfg.corner
        x = scr.x() + scr.width() - w if "right" in corner else scr.left()
        y = scr.y() + scr.height() - h - offset if "bottom" in corner else scr.top() + offset
        return QPoint(x, y)

    def layout(self):
        offsets = {}  # per screen: each thumbnail stays on the monitor it was taken on
        for t in reversed(self.thumbs):  # newest sits in the corner, older ones stack away from it
            home = (t.home.x(), t.home.y(), t.home.width(), t.home.height())
            offset = offsets.get(home, 0)
            pos = self.corner_pos(t.width(), t.height(), offset, t.home)
            if t.glide is not None and isValid(t.glide):
                t.glide.stop()  # a newer layout wins over a glide still in flight
            if t.isVisible() and not WAYLAND and t.pos() != pos:  # glide into the freed slot
                t.glide = QPropertyAnimation(t, b"pos", t, endValue=pos, duration=220, easingCurve=QEasingCurve.OutCubic)
                t.glide.start(QAbstractAnimation.DeleteWhenStopped)
            else:
                t.move(pos)
            t.setWindowTitle(f"snapfloat-thumb {self.cfg.corner} {offset}")  # Wayland: the GNOME extension places it from this
            offsets[home] = offset + t.height() - M

    def settings(self):
        if not self.dialog or not self.dialog.isVisible():
            self.dialog = Settings(self)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()


def self_test(report):
    """Draw every mark, round-trip the image formats, run the helper programs SnapFloat starts (a frozen build's
    libraries must not break them), read text and a QR code. Writes the results to `report` (or stdout); returns the exit code."""
    app = QApplication(sys.argv[:1])
    lines, failed = [], []

    def check(name, fn):
        try:
            note = fn()
            lines.append(f"ok    {name}" + (f" ({note})" if note else ""))
        except Exception as e:
            failed.append(name)
            lines.append(f"FAIL  {name}: {type(e).__name__}: {e}")

    def drawing():
        img = QImage(640, 400, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor("#336699"))
        ed = Editor(app, QPixmap.fromImage(img), lambda p: None)
        a, b = QPointF(40, 40), QPointF(300, 200)
        path = QPainterPath(a)
        path.lineTo(b)
        ed.canvas.change([dict(t=t, a=a, b=b, r=QRectF(a, b), at=b, path=path, c="#ff3b30", w=4, size=20, n=1, text="Hi\nthere")
                          for t in ("blur", "spotlight", "magnify", "rect", "ellipse", "pen", "arrow", "highlight", "text", "step")])
        out = ed.canvas.render()
        ed.close()
        assert out.size() == img.size() and out.pixelColor(60, 60) != img.pixelColor(60, 60), "marks weren't drawn"

    def formats():
        img = QImage(64, 48, QImage.Format_RGB32)
        img.fill(QColor("red"))
        for fmt in ("png", "jpg", "bmp"):
            path = tmp_dir() / f"self-test.{fmt}"
            assert img.save(str(path)) and QImage(str(path)).width() == 64, f"can't write and read {fmt.upper()}"
            path.unlink()

    def programs():
        # A real gsettings read: with the wrong libraries it can't load GNOME's settings module, yet still exits 0 and
        # quietly uses a throwaway store (how v1.1.0 lost settings). So stderr is checked too.
        calls = {"gsettings": ["get", "org.gnome.desktop.interface", "color-scheme"], "gnome-extensions": ["version"],
                 "wl-copy": ["--version"], "gst-launch-1.0": ["--version"], "tesseract": ["--version"]}
        ran = []
        for n, args in calls.items():
            exe = tesseract() if n == "tesseract" else shutil.which(n) if os.name != "nt" else None
            if exe:
                r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=30)
                broken = re.search(r"Failed to load module|undefined symbol|symbol lookup error|not found \(required by|"
                                   r"error while loading shared", r.stderr)
                assert not broken and (r.returncode == 0 or "No such schema" in r.stderr), \
                    f"{n} exited {r.returncode}: {r.stderr.strip()[:300]}"
                ran.append(n)
        return "ran " + ", ".join(ran) if ran else "none installed"

    def ocr():
        if not tesseract():
            return "skipped: Tesseract isn't installed"
        img = QImage(900, 160, QImage.Format_RGB32)
        img.fill(QColor("white"))
        p = QPainter(img)
        p.setFont(text_font(64))
        p.drawText(30, 110, "SnapFloat 2468")
        p.end()
        loop, out = QEventLoop(), []
        run_ocr(None, img, False, lambda text, _scale: (out.append(text), loop.quit()))
        QTimer.singleShot(60000, loop.quit)
        if not out:
            loop.exec()
        assert out and out[0] and "2468" in out[0], f"read {out[0] if out else 'nothing'!r}"

    def qr():
        if zxingcpp is None:
            assert not getattr(sys, "frozen", False), "zxing-cpp isn't bundled"
            return "skipped: zxing-cpp isn't installed"
        code = bytes(zxingcpp.create_barcode("snapfloat", zxingcpp.BarcodeFormat.QRCode).to_image(scale=6))
        side = int(len(code) ** 0.5)
        img = QImage(code, side, side, side, QImage.Format_Grayscale8)
        assert [r.text for r in zxingcpp.read_barcodes(img)] == ["snapfloat"]  # read_qr itself would replace the clipboard

    for name, fn in (("drawing", drawing), ("image formats", formats), ("helper programs", programs), ("OCR", ocr), ("QR", qr)):
        check(name, fn)
    lines.append(f"{APP} self-test: " + (f"FAILED ({', '.join(failed)})" if failed else "passed"))
    if report:
        Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        print("\n".join(lines))
    return 1 if failed else 0


if __name__ == "__main__":
    if SELF_TEST is not None:
        sys.exit(self_test(SELF_TEST))
    # Two copies starting at once (autostart + an early PrtSc) would fight over the socket and each other's files.
    lock = QLockFile(SERVER + ".lock" if os.name != "nt" else os.path.join(tempfile.gettempdir(), SERVER + ".lock"))
    if not lock.tryLock(200):
        for _ in range(30):  # the other copy is still starting: hand it our command once it listens
            if send(ARG or "settings"):
                break
            time.sleep(0.1)
        sys.exit(0)
    app = App()
    if ARG in ("region", "screen"):
        QTimer.singleShot(300, lambda: app.command(ARG))
    elif ARG == "settings" or not Config().s.contains("seconds"):
        QTimer.singleShot(300, app.settings)  # asked for, or first run
    sys.exit(app.exec())
