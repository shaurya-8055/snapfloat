"""The floating thumbnail, and screenshots floated on screen."""
import shutil
from pathlib import Path

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QMimeData, QRect, QRectF, QSizeF, Qt, QTimer, QUrl,
                            QVariantAnimation)
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QDrag, QGuiApplication, QImage, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QWidget
from shiboken6 import isValid
try:
    import zxingcpp  # reads QR codes; optional when running from source
except ImportError:
    zxingcpp = None

from .common import ACCENT, DRAG_IMAGE_OK, M, SMALL_MAX
from .editor import Editor
from .effects import beautify
from .ocr import ocr_message, read_qr, run_ocr, tesseract
from .record import VIDEO
from .system import APP


class Menu(QMenu):
    """A context menu whose items run once it has closed. Several close the window the menu belongs to (Close, Float on
    screen); run from inside the open menu, that freed the menu under itself on Wayland and crashed the app."""

    def addAction(self, text, fn):
        return super().addAction(text, lambda: QTimer.singleShot(0, self.parent(), fn))  # dropped if the owner is gone


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
        self.toast = None  # (title, detail) shown on the thumbnail for a moment: what Read QR code / Copy text did
        self.toast_timer = QTimer(self, singleShot=True, interval=3000, timeout=self.notify)
        self.restart()

    def notify(self, title=None, detail=""):
        """Say something on the thumbnail itself (where you're looking) for 3 s; no arguments clears it."""
        self.toast = (title, detail) if title else None
        if title:
            self.toast_timer.start()
            self.restart()  # don't disappear while it's being read
        self.update()

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
        if self.toast:
            title, detail = self.toast
            box = QRectF(M + 8, 0, w - 2 * M - 16, 46 if detail else 30)
            box.moveBottom(h - M - 16)
            p.setBrush(QColor(28, 28, 30, 235))
            p.drawRoundedRect(box, 9, 9)
            f = p.font()
            f.setPixelSize(13)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("white"))
            inner = box.adjusted(10, 5, -10, -5)
            p.drawText(inner, Qt.AlignLeft | Qt.AlignTop, p.fontMetrics().elidedText(title, Qt.ElideRight, int(inner.width())))
            if detail:
                f.setBold(False)
                f.setPixelSize(12)
                p.setFont(f)
                p.setPen(QColor(255, 255, 255, 190))
                p.drawText(inner, Qt.AlignLeft | Qt.AlignBottom,
                           p.fontMetrics().elidedText(detail.replace("\n", " "), Qt.ElideRight, int(inner.width())))
            p.setPen(Qt.NoPen)
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
        m = Menu(self)
        if not self.video:
            m.addAction("Mark up…", self.markup)
            m.addAction("Copy", lambda: self.app.copy(self.full, self.path))
            m.addAction("Copy text", self.copy_text).setEnabled(bool(tesseract()))
            qr = m.addAction("Read QR code", lambda: self.notify(*read_qr(self.full.toImage())))
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
        self.notify("Reading text…")

        def done(text, _scale):
            msg = ocr_message(text)
            self.notify(*(("✓ " + msg, "On the clipboard: paste it anywhere") if msg.startswith("Copied") else (msg, "")))
        run_ocr(self, self.full.toImage(), False, done)  # owner = this thumbnail: dropped if it closes first

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
            m = Menu(self)
            m.addAction("Copy", lambda: self.app.copy(self.pix))
            m.addAction("Close", self.close)
            m.exec(e.globalPosition().toPoint())
            m.deleteLater()

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
