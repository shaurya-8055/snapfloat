"""Settings, constants and helpers shared by the windows: theme, clipboard, icon."""
import os, shutil, subprocess, sys, time
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, Qt, qVersion
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImageReader, QPainter, QPen, QPixmap

from .system import tmp_dir


WAYLAND = os.environ.get("XDG_SESSION_TYPE") == "wayland"


if sys.platform.startswith("linux"):
    # native Wayland: XWayland can't drag into Wayland apps. Corner placement then needs the GNOME extension.
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland" if WAYLAND else "xcb")


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


QSS = """
QDialog { background: @bg; }
QLabel { color: @fg; font-size: 13px; }
QLabel#h1 { font-size: 20px; font-weight: 600; }
QLabel#title { font-size: 14px; font-weight: 600; }
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
