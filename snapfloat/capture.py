"""Taking the screenshot: the desktop portal (Wayland), the region selector (Windows/X11), shutter feedback."""
import math, os, random, shutil, subprocess, time

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QRect, QRectF, Qt, QTimer, QUrl, QVariantAnimation, Signal, SLOT, Slot
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .common import ACCENT, WAYLAND
from .system import tmp_dir


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

    def capture(self, interactive, fallback=True):
        """fallback: if a silent full-screen shot isn't allowed, open the screenshot UI instead (or report failure)."""
        self.interactive, self.fallback = interactive, fallback
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
        if code == 2 and not self.interactive and self.fallback:  # silent full-screen shot refused (no permission): use the screenshot UI
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
    """Frozen screen, drag a rectangle, or (Windows) click a window. One per monitor; selection stays on one monitor.
    `shot`: the screenshot to show (default: grab the screen; Wayland apps can't). `picked`: the rectangle chosen, in its pixels."""

    def __init__(self, screen, finish, windows=(), shot=None, hint=""):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.shot, self.hint = shot if shot is not None else screen.grabWindow(0), hint
        self.finish, self.origin, self.sel, self.hover, self.picked = finish, None, QRect(), None, None
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
        if self.hint and self.sel.isEmpty():  # what to do, until the drag starts
            f = p.font()
            f.setPixelSize(15)
            f.setBold(True)
            p.setFont(f)
            box = p.fontMetrics().boundingRect(self.hint).adjusted(-18, -10, 18, 10)
            box.moveCenter(QPoint(self.width() // 2, 48))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(28, 28, 30, 230))
            p.drawRoundedRect(box, box.height() / 2, box.height() / 2)
            p.setPen(QColor("white"))
            p.drawText(box, Qt.AlignCenter, self.hint)

    def window_at(self, pos):
        d = self.shot.devicePixelRatio()
        return next((w for w in self.windows if w.contains((pos * d).toPoint())), None)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return self.finish(None)
        self.origin, self.hover = e.position().toPoint(), self.window_at(e.position())

    def mouseMoveEvent(self, e):
        if self.origin is not None:
            self.sel = QRect(self.origin, e.position().toPoint()).normalized().intersected(self.rect())  # this screen only
        else:
            self.hover = self.window_at(e.position())
        self.update()

    def mouseReleaseEvent(self, e):
        if self.origin is not None:
            self.picked = self.src(self.sel).toRect() if self.sel.width() > 3 and self.sel.height() > 3 else self.hover
            self.finish(self.shot.copy(self.picked) if self.picked else None)  # a click: the window under it, if any

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
        wav = tmp_dir() / "shutter.wav"
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
