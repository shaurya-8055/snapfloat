"""The tray app: capture flow, thumbnails, Recent; and --self-test."""
import faulthandler, os, re, shutil, stat, subprocess, sys, tempfile, time
from pathlib import Path

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEventLoop, QLockFile, QPoint, QPointF,
                            QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from shiboken6 import isValid
try:
    import zxingcpp  # reads QR codes; optional when running from source
except ImportError:
    zxingcpp = None

from .capture import Countdown, Flash, Portal, select_region, Selector, shutter
from .common import Config, icon, M, MAX_RECENT, MAX_THUMBS, recording_icon, set_clipboard, WAYLAND
from .draw import text_font
from .editor import Editor
from .effects import combine
from .hotkeys import ensure_gnome_extension, gnome_bind, gnome_ext_problem, set_autostart, WinHotkeys
from .ocr import run_ocr, tesseract
from .record import poster, record_missing, Recorder
from .scroll import ScrollCapture
from .settings import Settings
from .system import (APP, AUTHOR, CMD, forget, free_print_key, gsettings_list, LEGACY_TMP, LOG, new_path, purge, send,
                     SERVER, tmp_dir)
from .thumb import Thumb


class App(QApplication):
    hotkey = Signal(str)

    def __init__(self):
        super().__init__(sys.argv[:1])
        self.setQuitOnLastWindowClosed(False)
        self.setApplicationName(APP)
        self.setDesktopFileName("snapfloat")  # Wayland app_id: how the GNOME extension spots our windows
        self.setWindowIcon(icon())
        self.cfg, self.thumbs, self.recent, self.dialog, self.hotkeys = Config(), [], [], None, None
        self.pins, self.scroller, self.portal_to, self.picker = [], None, None, None
        self.flash = self.pending = None  # keep these short-lived windows referenced while they animate
        self.busy_since, self.submenus = 0.0, []
        tmp_dir()
        purge()
        self.aboutToQuit.connect(purge)
        try:  # 1.0/1.1 kept shots in /tmp: clear that out, if it's really ours
            st = os.lstat(LEGACY_TMP)
            if LEGACY_TMP != tmp_dir() and stat.S_ISDIR(st.st_mode) and (os.name == "nt" or st.st_uid == os.getuid()):
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
        """Scrolling capture: freeze the screen and drag the area once, then scroll through it (see ScrollCapture).
        Every later screenshot is cropped to exactly that area."""
        if not self.start_capture():
            return
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

        def frozen(full):
            if full is None or full.isNull():
                return self.show_shot(None)
            origin, mine = QPoint(), full
            if WAYLAND and len(QGuiApplication.screens()) > 1:  # the desktop's screenshot spans every screen: just ours
                vg = scr.virtualGeometry()
                k, g = full.width() / vg.width(), scr.geometry().translated(-vg.topLeft())
                origin = QPoint(round(g.x() * k), round(g.y() * k))
                mine = full.copy(QRect(origin, QSize(round(g.width() * k), round(g.height() * k))))
            shot = QPixmap.fromImage(mine)
            shot.setDevicePixelRatio(mine.width() / scr.geometry().width())
            self.picker = Selector(scr, lambda pix: picked(pix, origin, full.size()), shot=shot)
            self.picker.showFullScreen()
            self.picker.activateWindow()

        def picked(pix, origin, size):
            rect = self.picker.picked
            self.picker.close()
            self.picker = None
            if pix is None:
                return self.show_shot(None)

            def grab(then):
                at = rect.translated(origin)
                self.full_screen(lambda img: then(img.copy(at) if img is not None and img.size() == size else None))
            self.scroller = ScrollCapture(self, pix.toImage(), grab, None if WAYLAND else (scr, rect))
            self.scroller.show()
        self.hide_thumbs(lambda: self.full_screen(frozen))

    def full_screen(self, then):
        """then(screenshot of the screen in use, or None). On Wayland the portal takes it (of every screen), so it
        arrives later."""
        if WAYLAND:
            self.portal_to = then
            return self.portal.capture(False)
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        then(scr.grabWindow(0).toImage())

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


def keep_log():
    """Started from login or a shortcut, nobody sees our output: write it (errors, and the stack of a hard crash) to LOG.
    The previous session's log stays as .log.1, so restarting after a crash doesn't wipe the evidence."""
    if sys.stderr is not None and sys.stderr.isatty():
        return  # run in a terminal: print there
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists():
            LOG.replace(LOG.with_suffix(".log.1"))
        sys.stdout = sys.stderr = open(LOG, "w", buffering=1, encoding="utf-8", errors="replace")
        os.dup2(sys.stderr.fileno(), 2)  # Qt's own warnings too
        faulthandler.enable(sys.stderr, all_threads=True)
    except OSError:
        pass


def run(arg, report=None):
    """Start the tray app (or just the self-test). `arg`: a command from the command line, e.g. "region"."""
    if report is not None:
        sys.exit(self_test(report))
    keep_log()
    # Two copies starting at once (autostart + an early PrtSc) would fight over the socket and each other's files.
    lock = QLockFile(SERVER + ".lock" if os.name != "nt" else os.path.join(tempfile.gettempdir(), SERVER + ".lock"))
    if not lock.tryLock(200):
        for _ in range(30):  # the other copy is still starting: hand it our command once it listens
            if send(arg or "settings"):
                break
            time.sleep(0.1)
        sys.exit(0)
    app = App()
    if arg in ("region", "screen"):
        QTimer.singleShot(300, lambda: app.command(arg))
    elif arg == "settings" or not Config().s.contains("seconds"):
        QTimer.singleShot(300, app.settings)  # asked for, or first run
    sys.exit(app.exec())
