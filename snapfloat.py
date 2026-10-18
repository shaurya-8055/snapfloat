#!/usr/bin/env python3
"""SnapFloat: macOS-style floating screenshot thumbnail for Linux and Windows.

Take a screenshot -> a thumbnail floats in the corner -> drag it into any chat.
Nothing lands in your Screenshots folder unless you ask for it.
Usage: snapfloat [--region | --screen | --settings | --quit]
"""
import getpass, math, os, shlex, shutil, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path

APP = "SnapFloat"
COMMANDS = ("region", "screen", "settings", "quit")
if os.name == "nt":
    SERVER = f"snapfloat-{getpass.getuser()}"
    ADDR = r"\\.\pipe" + "\\" + SERVER
else:
    SERVER = ADDR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), f"snapfloat-{os.getuid()}.sock")
TMP = Path(tempfile.gettempdir()) / f"snapfloat-{getpass.getuser()}"
CMD = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]


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


ARG = None
if __name__ == "__main__":
    ARG = next((a[2:] for a in sys.argv[1:] if a[2:] in COMMANDS), None)
    if send(ARG or "settings") or ARG == "quit":  # already running: just forward the command
        sys.exit(0)

WAYLAND = os.environ.get("XDG_SESSION_TYPE") == "wayland"
if sys.platform.startswith("linux"):
    # native Wayland: XWayland can't drag into Wayland apps. Corner placement then needs the GNOME extension.
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland" if WAYLAND else "xcb")
EXT_UUID = "snapfloat@snapfloat.github.io"

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QLineF, QMimeData, QObject, QPoint, QPointF, QPropertyAnimation,
                            QRect, QRectF, QSettings, QSize, QSizeF, Qt, QTimer, QUrl, QVariantAnimation, Signal, Slot, SLOT)
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QDrag, QFont, QGuiApplication, QIcon, QImage, QKeySequence,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMenu, QPushButton, QSlider, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget)

DEFAULTS = dict(seconds=6, corner="bottom-right", size=220, copy=True, save=False,
                folder=str(Path.home() / "Pictures" / "Screenshots"), region_key="Ctrl+Shift+4",
                screen_key="Ctrl+Shift+3", watch_clipboard=False, autostart=False, small=False, sound=True, flash=True)
CORNERS = ["bottom-right", "bottom-left", "top-right", "top-left"]
MAX_THUMBS = 5
MAX_RECENT = 10
SMALL_MAX = 1920  # "smaller files": longest side of the JPG handed to chat apps
M = 10  # shadow margin around a thumbnail
ACCENT = QColor("#0a84ff")


class Config:
    def __init__(self):
        self.s = QSettings("snapfloat", "snapfloat")

    def __getattr__(self, k):
        d = DEFAULTS[k]
        return self.s.value(k, d, type=type(d))

    def set(self, k, v):
        self.s.setValue(k, v)


def new_path():
    TMP.mkdir(exist_ok=True)
    return TMP / time.strftime(f"Screenshot %Y-%m-%d at %H.%M.%S.{int(time.time() * 1000) % 1000:03d}.png")


def forget(path):
    path.unlink(missing_ok=True)
    path.with_suffix(".jpg").unlink(missing_ok=True)


def purge():
    for f in TMP.glob("*"):
        f.unlink(missing_ok=True)


def icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(ACCENT)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(QColor("white"))
    p.drawRoundedRect(30, 34, 22, 16, 4, 4)  # the little floating thumbnail
    p.end()
    return QIcon(pix)


# ---------- capture ----------

class Portal(QObject):
    """Wayland: apps can't read the screen, so ask xdg-desktop-portal (GNOME/KDE screenshot UI)."""
    done = Signal(str)

    def capture(self, interactive):
        bus = QDBusConnection.sessionBus()
        token = f"snapfloat{int(time.time() * 1000)}"
        path = f"/org/freedesktop/portal/desktop/request/{bus.baseService()[1:].replace('.', '_')}/{token}"
        bus.connect("org.freedesktop.portal.Desktop", path, "org.freedesktop.portal.Request", "Response",
                    self, SLOT("on_response(uint,QVariantMap)"))
        msg = QDBusMessage.createMethodCall("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                            "org.freedesktop.portal.Screenshot", "Screenshot")
        msg.setArguments(["", {"handle_token": token, "interactive": interactive}])
        bus.call(msg)

    @Slot("uint", "QVariantMap")
    def on_response(self, code, results):
        self.done.emit(QUrl(results.get("uri", "")).toLocalFile() if code == 0 else "")


if WAYLAND:
    from PySide6.QtDBus import QDBusConnection, QDBusMessage


class Selector(QWidget):
    """Windows/X11: frozen screen, drag a rectangle. One per monitor; selection stays on one monitor."""

    def __init__(self, screen, finish):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.shot, self.finish, self.origin, self.sel = screen.grabWindow(0), finish, None, QRect()
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)

    def src(self, r):
        d = self.shot.devicePixelRatio()
        return QRectF(r.x() * d, r.y() * d, r.width() * d, r.height() * d)

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(QRectF(self.rect()), self.shot, QRectF(self.shot.rect()))
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if not self.sel.isEmpty():
            p.drawPixmap(QRectF(self.sel), self.shot, self.src(self.sel))
            p.setPen(QPen(ACCENT, 2))
            p.drawRect(self.sel)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return self.finish(None)
        self.origin = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.origin:
            self.sel = QRect(self.origin, e.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, e):
        if self.origin:
            ok = self.sel.width() > 3 and self.sel.height() > 3
            self.finish(self.shot.copy(self.src(self.sel).toRect()) if ok else None)

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

    sels += [Selector(s, finish) for s in QGuiApplication.screens()]  # grab every screen before covering any
    for s in sels:
        s.showFullScreen()
        s.activateWindow()


# ---------- capture feedback (Windows/X11: on Wayland GNOME's screenshot service flashes and plays its sound) ----------

def write_click(path):
    """A synthesized two-blade shutter click, so Windows needs no bundled sound file."""
    import random, struct, wave
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
            TMP.mkdir(exist_ok=True)
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


# ---------- markup ----------

TOOLS = [("arrow", "Arrow", "A"), ("rect", "Box", "R"), ("highlight", "Highlighter", "H"), ("text", "Text", "T"),
         ("blur", "Blur: hide private info", "B"), ("crop", "Crop", "C")]
COLORS = ["#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#ffffff", "#000000"]
TEXT_PX = 22  # on-screen text size while typing; stored in image pixels so it looks the same after export


def pixelate(img, r):
    """Coarse mosaic, big enough blocks that text can't be read back."""
    block = max(10, min(r.width(), r.height()) // 5)
    small = img.copy(r).scaled(max(1, r.width() // block), max(1, r.height() // block),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return small.scaled(r.size(), Qt.IgnoreAspectRatio, Qt.FastTransformation)


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


def draw_text(p, at, text, c, size):
    f = QFont()
    f.setPixelSize(max(1, round(size)))
    f.setBold(True)
    path = QPainterPath()
    path.addText(at, f, text)
    outline = QColor(0, 0, 0, 170) if c.lightness() > 100 else QColor(255, 255, 255, 210)
    p.strokePath(path, QPen(outline, size / 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(path, c)


def paint_shapes(p, img, shapes):
    """Markup in image pixels. Blurs go first: they sample the clean screenshot, and later marks stay crisp on top."""
    for s in shapes:
        if s["t"] == "blur":
            r = s["r"].toRect().intersected(img.rect())
            if r.width() > 1 and r.height() > 1:
                p.drawImage(r.topLeft(), pixelate(img, r))
    for s in shapes:
        t, c = s["t"], QColor(s["c"])
        if t == "rect":
            p.setPen(QPen(c, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(s["r"], s["w"] * 1.5, s["w"] * 1.5)
        elif t == "arrow":
            draw_arrow(p, s["a"], s["b"], c, s["w"])
        elif t == "highlight":
            c.setAlphaF(0.38)
            p.setPen(QPen(c, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(s["path"])
        elif t == "text":
            draw_text(p, s["at"], s["text"], c, s["size"])


def crop_rect(shapes, img):
    crops = [s["r"] for s in shapes if s["t"] == "crop"]
    r = crops[-1].intersected(QRectF(img.rect())) if crops else QRectF()
    return r if r.width() >= 2 and r.height() >= 2 else None


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


class TextBox(QLineEdit):
    """Inline text entry on the canvas. Enter places the text, Esc drops it."""

    def __init__(self, canvas, color):
        super().__init__(canvas)
        f = QFont()
        f.setPixelSize(TEXT_PX)
        f.setBold(True)
        self.setFont(f)
        self.set_color(color)
        self.resize(240, self.sizeHint().height())
        self.textChanged.connect(lambda t: self.resize(max(240, self.fontMetrics().horizontalAdvance(t) + 30), self.height()))

    def set_color(self, c):
        self.setStyleSheet(f"QLineEdit {{ background: rgba(0, 0, 0, 0.35); color: {c}; border: 1px dashed rgba(255, 255, 255, 0.8);"
                           " border-radius: 4px; padding: 2px 4px; }")

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            return self.parent().commit_text()  # consumed: Enter must not also trigger the editor's Done
        if e.key() == Qt.Key_Escape:
            return self.parent().drop_text()
        super().keyPressEvent(e)


class Canvas(QWidget):
    """The screenshot, scaled to fit, with markup drawn on top. Shapes live in image pixels."""

    def __init__(self, editor, img, bg):
        super().__init__()
        self.ed, self.img, self.bg = editor, img, bg
        self.shapes, self.undone, self.cur, self.text = [], [], None, None
        self.k, self.off, self.view = 1.0, QPointF(), QPixmap()
        self.rescale = QTimer(self, singleShot=True, interval=60, timeout=self.build_view)  # don't rescale on every resize step
        self.setCursor(Qt.CrossCursor)
        self.setMinimumSize(400, 260)

    def resizeEvent(self, _):
        iw, ih = self.img.width(), self.img.height()
        self.k = max(0.01, min((self.width() - 32) / iw, (self.height() - 32) / ih, 1 / self.devicePixelRatioF()))
        self.off = QPointF((self.width() - iw * self.k) / 2, (self.height() - ih * self.k) / 2)
        self.rescale.start()

    def build_view(self):
        """Pre-scale the screenshot once per size, so drawing frames only blit it."""
        d = self.devicePixelRatioF()
        self.view = QPixmap.fromImage(self.img.scaled(round(self.img.width() * self.k * d), round(self.img.height() * self.k * d),
                                                      Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.view.setDevicePixelRatio(d)
        self.update()

    def to_img(self, pos):
        return (QPointF(pos) - self.off) / self.k

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
        p.restore()
        crop = crop_rect(live, self.img)
        if crop is not None:
            cr = QRectF(self.off + crop.topLeft() * self.k, crop.size() * self.k)
            shade, hole = QPainterPath(), QPainterPath()
            shade.addRect(target)
            hole.addRect(cr)
            p.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 150))
            p.setPen(QPen(QColor("white"), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(cr)
        if self.cur and self.cur["t"] == "blur":
            r = self.cur["r"]
            p.setPen(QPen(QColor("white"), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(self.off + r.topLeft() * self.k, r.size() * self.k))

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self.commit_text()
        tool, c, a = self.ed.tool, self.ed.color, self.to_img(e.position())
        if tool == "text":
            return self.start_text(e.position())
        w = (18 if tool == "highlight" else 4) / self.k  # widths are set in screen pixels: what you see is what you get
        self.cur = dict(t=tool, a=a, b=a, r=QRectF(a, a), c=c, w=w, path=QPainterPath(a))

    def mouseMoveEvent(self, e):
        if not self.cur:
            return
        b = self.to_img(e.position())
        self.cur["b"], self.cur["r"] = b, QRectF(self.cur["a"], b).normalized()
        self.cur["path"].lineTo(b)
        self.update()

    def mouseReleaseEvent(self, _):
        cur, self.cur = self.cur, None
        if cur:
            size = cur["path"].length() if cur["t"] == "highlight" else QLineF(cur["a"], cur["b"]).length()
            if size * self.k > 4:  # ignore stray clicks
                self.push(cur)
            self.update()

    def push(self, shape):
        self.shapes.append(shape)
        self.undone.clear()
        self.update()

    def undo(self):
        if self.text:
            return self.drop_text()
        if self.shapes:
            self.undone.append(self.shapes.pop())
            self.update()

    def redo(self):
        if self.undone:
            self.shapes.append(self.undone.pop())
            self.update()

    def start_text(self, pos):
        box = TextBox(self, self.ed.color)
        box.move(int(pos.x()) - 6, int(pos.y()) - box.fontMetrics().ascent() - 6)
        box.show()
        box.setFocus()
        self.text = (box, self.to_img(pos))

    def commit_text(self):
        if not self.text:
            return
        box, at = self.text
        self.text = None
        if box.text().strip():
            self.push(dict(t="text", at=at, text=box.text(), c=self.ed.color, size=TEXT_PX / self.k))
        box.deleteLater()
        self.setFocus()

    def drop_text(self):
        if self.text:
            self.text[0].deleteLater()
            self.text = None
            self.setFocus()

    def render(self):
        out = self.img.copy()
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        paint_shapes(p, self.img, self.shapes)
        p.end()
        crop = crop_rect(self.shapes, self.img)
        return out.copy(crop.toRect()) if crop is not None else out


class Editor(QDialog):
    """Quick markup: arrows, boxes, highlighter, text, blur, crop. Done updates the thumbnail in place."""

    def __init__(self, app, pix, on_done):
        super().__init__()
        self.on_done = on_done
        pal = themed(self, QSS + EDITOR_QSS)
        self.setWindowTitle(f"Markup · {APP}")
        self.setWindowIcon(icon())
        img = pix.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(1)
        self.tool, self.color = "arrow", COLORS[0]
        self.canvas = Canvas(self, img, QColor(pal["bg"]))

        bar = QFrame(objectName="bar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(4)
        self.tools = QButtonGroup(self)
        for name, tip, key in TOOLS:
            b = self.tool_button(name, f"{tip}  ({key})", pal["fg"], checkable=True, checked=name == self.tool)
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
            self.swatches.addButton(b)
            h.addWidget(b)
        h.addWidget(self.vsep())
        undo = self.tool_button("undo", "Undo  (Ctrl+Z)", pal["fg"])
        undo.clicked.connect(lambda: self.canvas.undo())
        redo = self.tool_button("redo", "Redo  (Ctrl+Shift+Z)", pal["fg"])
        redo.clicked.connect(lambda: self.canvas.redo())
        h.addWidget(undo)
        h.addWidget(redo)
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
        self.resize(max(760, int(img.width() / d * k) + 32), max(480, int(img.height() / d * k) + 32 + bar.sizeHint().height()))

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
        self.canvas.setCursor(Qt.IBeamCursor if name == "text" else Qt.CrossCursor)
        for b in self.tools.buttons():
            b.setChecked(b.property("tool") == name)

    def set_color(self, c):
        self.color = c
        if self.canvas.text:
            self.canvas.text[0].set_color(c)

    def keyPressEvent(self, e):
        keys = {k: n for n, _, k in TOOLS}
        if e.matches(QKeySequence.Undo):
            self.canvas.undo()
        elif e.matches(QKeySequence.Redo) or (e.key() == Qt.Key_Z and e.modifiers() == Qt.ControlModifier | Qt.ShiftModifier):
            self.canvas.redo()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish()
        elif not e.modifiers() and e.text().upper() in keys:
            self.set_tool(keys[e.text().upper()])
        else:
            super().keyPressEvent(e)  # Esc cancels

    def finish(self):
        self.canvas.commit_text()
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
        self.app, self.path, self.full, self.editor = app, path, pix, None
        self.press, self.hover, self.pinned, self.dragging, self.leaving = None, False, False, False, False
        self.swiping, self.swipe_from, self.lifted = False, None, False
        self.setToolTip("Drag into any app · Click to mark up · Flick to the edge to dismiss · Right-click for more")
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
        if not self.press or (pos - self.press).manhattanLength() <= QApplication.startDragDistance():
            return
        d = pos - self.press
        if d.x() * self.side() > 0 and abs(d.x()) > 2 * abs(d.y()):  # toward the screen edge: a flick to dismiss
            self.swiping, self.swipe_from, self.press = True, self.press, None
            self.swipe_anim.stop()
            return self.hold()
        hot, self.press = self.press, None
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(self.drag_file()))])
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.setPixmap(self.card)  # the whole thumbnail lifts off and follows the cursor, held where it was grabbed
        drag.setHotSpot(hot)
        self.dragging = self.lifted = True  # no timeout mid-drag: closing would delete us under drag.exec()
        self.hold()
        dropped = drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction) != Qt.IgnoreAction
        self.dragging = False
        if dropped:
            self.dismiss()  # dropped somewhere: job done, file never touches Screenshots
        else:
            self.lifted, self.v = False, 0.0  # nowhere to drop: slide back into the corner
            self.appear.start()
            self.restart()

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
        else:
            self.markup()

    def contextMenuEvent(self, e):
        self.hold()
        m = QMenu(self)
        m.addAction("Mark up…", self.markup)
        m.addAction("Copy", lambda: self.app.copy(self.full))
        m.addAction("Open in viewer", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path))))
        m.addAction(f"Save to {Path(self.app.cfg.folder).name}", self.save)
        m.addAction("Save As…", self.save_as)
        m.addAction("Unpin" if self.pinned else "Pin (keep on screen)", self.toggle_pin)
        m.addSeparator()
        m.addAction("Close", self.dismiss)
        m.exec(e.globalPos())
        self.restart()

    def drag_file(self):
        if not self.app.cfg.small:
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
            self.app.copy(pix)
        self.app.layout()

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.update()

    def save(self, dest=None):
        dest = Path(dest or Path(self.app.cfg.folder) / self.path.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, dest)
        self.dismiss()

    def save_as(self):
        dest, _ = QFileDialog.getSaveFileName(self, "Save screenshot", str(Path(self.app.cfg.folder) / self.path.name), "PNG (*.png)")
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


# ---------- hotkeys ----------

class WinHotkeys(threading.Thread):
    """Windows global hotkeys via RegisterHotKey; the message loop must live on the registering thread."""
    MODS = {"alt": 1, "ctrl": 2, "control": 2, "shift": 4, "win": 8, "meta": 8, "super": 8}

    def __init__(self, keys, fire):
        super().__init__(daemon=True)
        self.keys, self.fire, self.tid, self.failed = keys, fire, None, []
        self.ready = threading.Event()

    def parse(self, seq):
        import ctypes
        mods, vk = 0x4000, 0  # MOD_NOREPEAT
        for part in (p.strip() for p in seq.split("+")):
            low = part.lower()
            if low in self.MODS:
                mods |= self.MODS[low]
            elif len(part) == 1:
                vk = ctypes.windll.user32.VkKeyScanW(ord(part)) & 0xFF
            elif low[0] == "f" and low[1:].isdigit():
                vk = 0x6F + int(low[1:])
            elif low in ("print", "printscreen", "prtsc"):
                vk = 0x2C
        return mods, vk

    def run(self):
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        self.tid = ctypes.windll.kernel32.GetCurrentThreadId()
        for i, (cmd, seq) in enumerate(self.keys):
            if seq and not u.RegisterHotKey(None, i + 1, *self.parse(seq)):
                self.failed.append(seq)
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
    """'Ctrl+Shift+4' -> '<Control><Shift>4'"""
    mods = {"ctrl": "<Control>", "control": "<Control>", "shift": "<Shift>", "alt": "<Alt>",
            "win": "<Super>", "super": "<Super>", "meta": "<Super>"}
    parts = [p.strip() for p in seq.split("+") if p.strip()]
    key = parts[-1] if parts else ""
    return "".join(mods.get(p.lower(), "") for p in parts[:-1]) + (key if len(key) > 1 else key.lower())


def gnome_bind(keys):
    """Linux has no app-level global hotkeys on Wayland: register GNOME custom shortcuts that call us."""
    if not shutil.which("gsettings") or "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        return False
    schema = "org.gnome.settings-daemon.plugins.media-keys"
    base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
    out = subprocess.run(["gsettings", "get", schema, "custom-keybindings"], capture_output=True, text=True).stdout
    paths = [p.strip(" '") for p in out.replace("@as", "").strip(" []\n").split(",") if p.strip(" '")]
    for cmd, seq in keys:
        path = f"{base}snapfloat-{cmd}/"
        if path not in paths:
            paths.append(path)
        rel = f"{schema}.custom-keybinding:{path}"
        for k, v in (("name", f"{APP} {cmd}"), ("command", shlex.join(CMD + [f"--{cmd}"])), ("binding", gnome_accel(seq))):
            subprocess.run(["gsettings", "set", rel, k, v])
    subprocess.run(["gsettings", "set", schema, "custom-keybindings", str(paths)])
    return True


def gnome_ext_active():
    if not shutil.which("gnome-extensions"):
        return True  # ponytail: not GNOME, nothing to nag about; KDE etc. get default window placement
    return "ACTIVE" in subprocess.run(["gnome-extensions", "info", EXT_UUID], capture_output=True, text=True).stdout


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
            f.write_text(f"[Desktop Entry]\nType=Application\nName={APP}\nExec={shlex.join(CMD)}\nX-GNOME-Autostart-enabled=true\n")
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
"""
EDITOR_QSS = """
QFrame#bar { background: @card; border: none; border-bottom: 1px solid @border; }
QToolButton#tool { background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }
QToolButton#tool:hover { background: @input; }
QToolButton#tool:checked { background: rgba(10, 132, 255, 0.22); border: 1px solid @accent; }
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
        name = QKeySequence(k).toString()
        self.setText("+".join(mods + [SHIFTED.get(name, name)]))


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
        self.sound_t, self.flash_t = toggle(c.sound), toggle(c.flash)
        self.folder = QLineEdit(c.folder, toolTip=c.folder)
        folder_row = QWidget(minimumWidth=270)
        fl = QHBoxLayout(folder_row)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self.folder, 1)
        fl.addWidget(QPushButton("Browse…", clicked=self.browse, cursor=Qt.PointingHandCursor))
        self.region_key, self.screen_key = KeyEdit(c.region_key), KeyEdit(c.screen_key)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 18)
        root.setSpacing(16)
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
        if WAYLAND and not gnome_ext_active():
            root.addWidget(QLabel("⚠  Thumbnails open in the middle until the SnapFloat GNOME extension is active. "
                                  "<b>Log out and back in once</b> after installing.", objectName="warn", wordWrap=True))

        root.addWidget(self.card("Thumbnail", [
            ("Show for", "Hovering pauses it", self.seconds),
            ("Position", "Corner of the screen", self.corner),
            ("Size", None, self.thumb_size)]))
        root.addWidget(self.card("After capture", [
            ("Copy to clipboard", "Paste anywhere with Ctrl+V", self.copy_t),
            ("Send smaller files", f"Drag as JPG, max {SMALL_MAX} px: faster uploads", self.small_t),
            ("Save when it disappears", "Off: it's deleted, nothing piles up", self.save_t),
            ("Save folder", None, folder_row)]))
        where = "Added to GNOME keyboard shortcuts" if sys.platform.startswith("linux") else "Work in every app"
        root.addWidget(self.card("Shortcuts", [
            ("Capture region", where, self.region_key),
            ("Capture screen", "Backspace clears", self.screen_key)]))
        general = [("Start at login", None, self.autostart_t)]
        if not WAYLAND:  # on Wayland GNOME's screenshot service already flashes and plays its sound
            general += [("Shutter sound", None, self.sound_t), ("Screen flash", None, self.flash_t),
                        ("Also catch copied images", "Win+Shift+S, PrtSc", self.watch_t)]
        root.addWidget(self.card("General", general))

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(QPushButton("Cancel", clicked=self.reject, cursor=Qt.PointingHandCursor))
        save = QPushButton("Save", objectName="primary", clicked=self.accept, cursor=Qt.PointingHandCursor, default=True)
        buttons.addWidget(save)
        root.addLayout(buttons)

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

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def accept(self):
        values = dict(seconds=self.seconds.value(), corner=self.corner.value(), size=self.thumb_size.value(),
                      copy=self.copy_t.isChecked(), save=self.save_t.isChecked(), small=self.small_t.isChecked(),
                      folder=self.folder.text().strip() or DEFAULTS["folder"], region_key=self.region_key.text(),
                      screen_key=self.screen_key.text(), autostart=self.autostart_t.isChecked(),
                      watch_clipboard=self.watch_t.isChecked(), sound=self.sound_t.isChecked(), flash=self.flash_t.isChecked())
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
        self.flash = self.pending = None  # keep these short-lived windows referenced while they animate
        purge()
        self.aboutToQuit.connect(purge)

        QLocalServer.removeServer(SERVER)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self.on_connection)
        self.server.listen(SERVER)

        if WAYLAND:
            self.portal = Portal()
            self.portal.done.connect(self.on_portal)
        self.hotkey.connect(self.command)
        self.clipboard().dataChanged.connect(self.on_clipboard)

        self.tray = QSystemTrayIcon(icon(), self)
        self.tray.setToolTip(APP)
        self.tray.activated.connect(lambda r: self.region() if r == QSystemTrayIcon.Trigger else None)
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.show()
        self.apply()
        if WAYLAND and not gnome_ext_active():
            self.tray.showMessage(APP, "Log out and back in once so the SnapFloat GNOME extension can put thumbnails in the corner.")

    def apply(self):
        c = self.cfg
        self.menu.clear()
        self.menu.addAction(f"Capture region\t{c.region_key}", self.region)
        self.menu.addAction(f"Capture screen\t{c.screen_key}", self.screen)
        later = self.menu.addMenu("Capture after…")
        for n in (3, 5, 10):
            later.addAction(f"{n} seconds", lambda n=n: self.delayed(n))
        self.recent_menu = self.menu.addMenu("Recent")
        self.build_recent()
        self.menu.addSeparator()
        self.menu.addAction("Settings…", self.settings)
        self.menu.addAction("Quit", self.quit)
        keys = [("region", c.region_key), ("screen", c.screen_key)]
        if os.name == "nt":
            if self.hotkeys:
                self.hotkeys.stop()
            self.hotkeys = WinHotkeys(keys, self.hotkey.emit)
            self.hotkeys.start()
            self.hotkeys.ready.wait()
            if self.hotkeys.failed:
                self.tray.showMessage(APP, f"Shortcut already used by another app: {', '.join(self.hotkeys.failed)}")
        else:
            gnome_bind(keys)  # ponytail: GNOME only; on KDE/XFCE bind `snapfloat --region` by hand (see README)
        set_autostart(c.autostart)
        self.layout()

    def on_connection(self):
        sock = self.server.nextPendingConnection()
        sock.readyRead.connect(lambda: self.command(bytes(sock.readAll()).decode().strip()))
        sock.disconnected.connect(sock.deleteLater)

    def command(self, cmd):
        {"region": self.region, "screen": self.screen, "settings": self.settings, "quit": self.quit}.get(cmd, lambda: None)()

    def hide_thumbs(self, then):
        for t in self.thumbs:
            t.hide()
        QTimer.singleShot(150, then)  # let the compositor repaint without them

    def region(self):
        self.hide_thumbs(lambda: self.portal.capture(True) if WAYLAND else select_region(self.captured))

    def screen(self):
        def grab():
            if WAYLAND:
                return self.portal.capture(False)
            scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            self.captured(scr.grabWindow(0))
        self.hide_thumbs(grab)

    def delayed(self, seconds):
        for t in self.thumbs:
            t.hide()
        self.pending = Countdown(self, seconds, self.region)
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
        if not src or not Path(src).exists():
            return self.show_shot(None)
        dst = new_path()
        shutil.move(src, dst)  # the portal drops files in ~/Pictures: take it out of there
        self.show_shot(QPixmap(str(dst)), dst)

    def on_clipboard(self):
        cb = self.clipboard()
        if self.cfg.watch_clipboard and not cb.ownsClipboard() and cb.mimeData().hasImage():
            self.show_shot(QPixmap.fromImage(cb.image()), copy=False)

    def copy(self, pix):
        self.clipboard().setPixmap(pix)

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
            self.show_shot(QPixmap(str(path)), path, copy=False)

    def clear_recent(self):
        shown = {t.path for t in self.thumbs}
        for path, _, _ in self.recent:
            if path not in shown:
                forget(path)
        self.recent = [r for r in self.recent if r[0] in shown]
        self.build_recent()

    def show_shot(self, pix, path=None, copy=True):
        if pix is not None and not pix.isNull():
            if path is None:
                path = new_path()
                pix.save(str(path), "PNG")
            if copy and self.cfg.copy:
                self.copy(pix)
            self.thumbs.append(Thumb(self, path, pix))
            self.remember(path, pix)
            if len(self.thumbs) > MAX_THUMBS:
                self.thumbs[0].dismiss()
        self.layout()
        for t in self.thumbs:
            t.show()

    def corner_pos(self, w, h, offset):
        scr = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
        corner = self.cfg.corner
        x = scr.x() + scr.width() - w if "right" in corner else scr.left()
        y = scr.y() + scr.height() - h - offset if "bottom" in corner else scr.top() + offset
        return QPoint(x, y)

    def layout(self):
        offset = 0
        for t in reversed(self.thumbs):  # newest sits in the corner, older ones stack away from it
            pos = self.corner_pos(t.width(), t.height(), offset)
            if t.isVisible() and not WAYLAND and t.pos() != pos:  # glide into the freed slot
                a = QPropertyAnimation(t, b"pos", t, endValue=pos, duration=220, easingCurve=QEasingCurve.OutCubic)
                a.start(QAbstractAnimation.DeleteWhenStopped)
            else:
                t.move(pos)
            t.setWindowTitle(f"snapfloat-thumb {self.cfg.corner} {offset}")  # Wayland: the GNOME extension places it from this
            offset += t.height() - M

    def settings(self):
        if not self.dialog or not self.dialog.isVisible():
            self.dialog = Settings(self)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()


if __name__ == "__main__":
    app = App()
    if ARG and ARG != "settings":
        QTimer.singleShot(300, lambda: app.command(ARG))
    elif not ARG and not Config().s.contains("seconds"):
        QTimer.singleShot(300, app.settings)  # first run: show settings once
    sys.exit(app.exec())
