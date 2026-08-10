"""Smoke test, no display needed: python test_snapfloat.py (from the repo root)"""
import os, sys, tempfile
# isolate everything: a running SnapFloat's socket, temp shots, config and autostart must stay untouched
tempfile.tempdir = tempfile.mkdtemp()  # gettempdir() is cached, so TMPDIR alone would still hit the real /tmp
os.environ.update(QT_QPA_PLATFORM="offscreen", XDG_CURRENT_DESKTOP="", XDG_CONFIG_HOME=tempfile.mkdtemp(),
                  XDG_RUNTIME_DIR=tempfile.mkdtemp(), HOME=tempfile.mkdtemp(), TMPDIR=tempfile.tempdir,
                  XDG_SESSION_TYPE="", WAYLAND_DISPLAY="")  # never inherit the desktop's Wayland session
sys.argv = sys.argv[:1]
import importlib, pkgutil
import snapfloat


class _Package:
    """The tests reach every module's names as sf.X, as when SnapFloat was one file: reads are live (sf.TMP follows
    tmp_dir()), and patching sf.X replaces X in every module that has it."""
    mods = [importlib.import_module(f"snapfloat.{m.name}") for m in pkgutil.iter_modules(snapfloat.__path__)
            if m.name != "__main__"]

    def __getattr__(self, k):
        for m in self.mods:
            if hasattr(m, k):
                return getattr(m, k)
        raise AttributeError(k)

    def __setattr__(self, k, v):
        owners = [m for m in self.mods if hasattr(m, k)]
        assert owners, k
        for m in owners:
            setattr(m, k, v)


sf = _Package()
assert str(sf.TMP).startswith(tempfile.tempdir), "test must never touch the real SnapFloat temp folder"
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPixmap

assert sf.gnome_accel("Ctrl+Shift+4") == "<Control><Shift>4"
assert sf.gnome_accel("Super+S") == "<Super>s"
assert sf.gnome_accel("Print") == "Print"
assert sf.gnome_accel("") == ""

app = sf.App()
assert app.cfg.seconds == 6 and app.cfg.copy is True
pix = QPixmap(800, 600)
pix.fill(QColor("red"))

# thumbnails stack in the corner; Recent keeps the last MAX_RECENT and deletes older files
for _ in range(sf.MAX_RECENT + 2):
    app.show_shot(pix)
assert len(app.thumbs) == sf.MAX_THUMBS, len(app.thumbs)
assert len(app.recent) == sf.MAX_RECENT
assert len(list(sf.TMP.glob("*.png"))) == sf.MAX_RECENT, "evicted shots must be deleted"
t = app.thumbs[-1]
scr = app.primaryScreen().availableGeometry()
assert t.geometry().right() == scr.right() and t.geometry().bottom() == scr.bottom(), t.geometry()
assert t.path.exists() and t.path.parent == sf.TMP and app.recent[0][0] == t.path
assert app.clipboard().pixmap().size() == pix.size()
t.dismiss()
assert t not in app.thumbs and len(app.thumbs) == sf.MAX_THUMBS - 1
assert t.path.exists(), "a dismissed shot stays available in Recent"
app.reshow(t.path)
assert app.thumbs[-1].path == t.path
app.show_shot(None)  # cancelled capture must not crash

# smaller files: a JPG capped at SMALL_MAX
big = QPixmap(2400, 1500)
big.fill(QColor("blue"))
app.show_shot(big)
app.cfg.set("small", True)
jpg = app.thumbs[-1].drag_file()
assert jpg.suffix == ".jpg" and max(QImage(str(jpg)).width(), QImage(str(jpg)).height()) == sf.SMALL_MAX
app.cfg.set("small", False)
assert app.thumbs[-1].drag_file().suffix == ".png"

# markup: blur pixelates, crop crops, every shape renders
img = QImage(400, 300, QImage.Format_ARGB32_Premultiplied)
for x in range(400):
    for y in range(300):
        img.setPixelColor(x, y, QColor((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256))
ed = sf.Editor(app, QPixmap.fromImage(img), lambda p: None)
cv = ed.canvas
cv.resize(500, 400)
cv.push(dict(t="blur", r=QRectF(0, 0, 100, 100), c="#ff3b30", w=4))
cv.push(dict(t="arrow", a=QPointF(150, 150), b=QPointF(250, 200), c="#ff3b30", w=4))
cv.push(dict(t="rect", r=QRectF(200, 50, 80, 40), c="#34c759", w=4))
cv.push(dict(t="text", at=QPointF(150, 260), text="hello", c="#ffffff", size=22))
hl = sf.QPainterPath(QPointF(10, 200))
hl.lineTo(QPointF(120, 210))
cv.push(dict(t="highlight", path=hl, c="#ffcc00", w=18))
out = cv.render()
assert out.size() == img.size()
assert out.pixelColor(1, 1) == out.pixelColor(8, 8), "blur must flatten detail into blocks"
assert out.pixelColor(300, 280) == img.pixelColor(300, 280), "untouched areas stay identical"
cv.push(dict(t="crop", r=QRectF(50, 40, 200, 150), c="#ff3b30", w=4))
assert cv.render().size().toTuple() == (200, 150)
cv.undo()
assert cv.render().size() == img.size() and cv.undone
cv.redo()
assert cv.render().size().toTuple() == (200, 150)

# markup "Done" updates the thumbnail in place
th = app.thumbs[-1]
old_h = th.height()
th.set_image(QPixmap.fromImage(cv.render()))
assert th.full.size().toTuple() == (200, 150) and th.height() != old_h and QImage(str(th.path)).width() == 200
assert th.pinned, "an edited thumbnail stays until it's used"
assert app.clipboard().pixmap().size().toTuple() == (200, 150), "Done puts the edited version on the clipboard"
th.pinned = False

# swipe: past the threshold dismisses, short flick springs back
th.swiping, th.swipe = True, 20
th.mouseReleaseEvent(None)
assert th in app.thumbs and not th.swiping
th.swiping, th.swipe = True, 80
th.mouseReleaseEvent(None)
assert th not in app.thumbs

app.clear_recent()
assert all(p.exists() for p in (x.path for x in app.thumbs))

# beyond macOS: private-info finder, numbered steps, beautify, combine
tsv = "\n".join(["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
                 "5\t1\t1\t1\t1\t1\t10\t20\t60\t20\t95\tCall", "5\t1\t1\t1\t1\t2\t80\t20\t40\t20\t95\tme:",
                 "5\t1\t1\t1\t1\t3\t130\t20\t50\t20\t95\t+91", "5\t1\t1\t1\t1\t4\t190\t20\t90\t20\t95\t98765",
                 "5\t1\t1\t1\t1\t5\t290\t20\t90\t20\t95\t43210",
                 "5\t1\t1\t1\t2\t1\t10\t60\t300\t20\t95\tme@example.com",
                 "5\t1\t1\t1\t3\t1\t10\t100\t80\t20\t95\tOrder", "5\t1\t1\t1\t3\t2\t100\t100\t60\t20\t95\t#4821",
                 "5\t1\t1\t1\t3\t3\t170\t100\t50\t20\t95\t2026"])
boxes = sf.private_boxes(tsv, 2)
assert len(boxes) == 2, boxes  # the phone number (3 words) and the email; "#4821 2026" stays readable
for text, private in (("482913", True), ("4111 1111 1111 1111", True), ("(555) 123-4567", True), ("+44 20 7946", True),
                      ("4821 2026", False), ("12/10/2026", False)):
    m = sf.NUMBER.search(text)
    assert bool(m and sf.is_private_number(m)) == private, text
assert boxes[0].toRect() == QRectF(65, 10, 125, 10).adjusted(-4, -4, 4, 4).toRect(), boxes[0]
assert boxes[1].toRect() == QRectF(5, 30, 150, 10).adjusted(-4, -4, 4, 4).toRect(), boxes[1]
cv.shapes.clear()
cv.push(dict(t="step", at=QPointF(200, 150), n=1, c="#0a84ff", size=20))
cv.push(dict(t="blur", rs=[QRectF(0, 0, 60, 60), QRectF(300, 200, 60, 60)], r=QRectF(0, 0, 60, 60), c="#000", w=1))
out = cv.render()
assert out.pixelColor(200, 135).blue() > 200, "step badge drawn in its colour"
assert out.pixelColor(301, 201) == out.pixelColor(306, 206), "multi-box blur covers every box"
cv.undo()
assert out.size() == cv.render().size() and len(cv.shapes) == 1, "one undo removes all auto-blurred boxes"
# "copy text from an area": a selection, never drawn into the screenshot
picked = []
ed.copy_text_area = picked.append
before = len(cv.shapes)
cv.cur = dict(t="ocr", a=QPointF(10, 10), b=QPointF(200, 120), r=QRectF(10, 10, 190, 110), c="#000", w=1,
              path=sf.QPainterPath(QPointF(10, 10)))
cv.mouseReleaseEvent(None)
assert picked == [QRectF(10, 10, 190, 110)] and len(cv.shapes) == before
# ellipse, pen, spotlight
cv.shapes.clear()
cv.push(dict(t="ellipse", r=QRectF(100, 50, 200, 100), c="#ff3b30", w=6))
pen = sf.QPainterPath(QPointF(20, 250))
pen.lineTo(QPointF(120, 250))
cv.push(dict(t="pen", path=pen, c="#0a84ff", w=6))
out = cv.render()
assert out.pixelColor(100, 100).red() > 200 and out.pixelColor(200, 100) == img.pixelColor(200, 100), "ellipse outline only"
assert out.pixelColor(70, 250).blue() > 200, "pen stroke drawn"
cv.shapes.clear()
cv.push(dict(t="spotlight", r=QRectF(150, 100, 100, 80), c="#000", w=4))
cv.push(dict(t="arrow", a=QPointF(20, 20), b=QPointF(120, 60), c="#ff3b30", w=6))
out = cv.render()
assert out.pixelColor(200, 140) == img.pixelColor(200, 140), "inside the spotlight stays untouched"
assert out.pixelColor(380, 280).lightness() < img.pixelColor(380, 280).lightness(), "outside is darkened"
assert out.pixelColor(70, 40).red() > 200, "marks stay bright on top of the dimming"
nice = sf.beautify(QPixmap.fromImage(img))
assert nice.width() > img.width() + 90 and nice.toImage().pixelColor(2, 2) != QColor("white")
both = sf.combine([QPixmap.fromImage(img), QPixmap.fromImage(img)])
assert both.size().toTuple() == (400, 300 * 2 + 16)

# ---- audit regressions ----
# shortcuts: "+" and named keys never break parsing (a crash here froze Windows at every launch)
assert sf.split_keys("Ctrl+Shift++") == ["Ctrl", "Shift", "Plus"] and sf.split_keys("+") == ["Plus"] and sf.split_keys("") == []
assert sf.WinHotkeys([], None).parse("Ctrl+Shift++") == (0x4000 | 2 | 4, 0xBB)
assert sf.WinHotkeys([], None).parse("Ctrl+PgUp") == (0x4000 | 2, 0x21)
assert sf.WinHotkeys([], None).parse("Shift+Print") == (0x4000 | 4, 0x2C)
assert sf.gnome_accel("Ctrl+Shift++") == "<Control><Shift>plus" and sf.gnome_accel("Ctrl+PgUp") == "<Control>Page_Up"
assert sf.gnome_accel("Super+F5") == "<Super>F5" and sf.gnome_accel("Shift+Print") == "<Shift>Print"

# redaction keeps nothing of the content but its average colour: two different texts, same average -> same pixels
a, b = QImage(60, 20, QImage.Format_ARGB32_Premultiplied), QImage(60, 20, QImage.Format_ARGB32_Premultiplied)
for x in range(60):
    for y in range(20):
        a.setPixelColor(x, y, QColor("black") if x < 30 else QColor("white"))
        b.setPixelColor(x, y, QColor("white") if x < 30 else QColor("black"))
box = sf.QRect(0, 0, 60, 20)
assert sf.pixelate(a, box) == sf.pixelate(b, box), "redaction must not depend on what's under it"
assert sf.pixelate(a, box).size() == box.size()

# private-info rules: more secrets caught, fewer harmless numbers blurred
def private(text):
    return any(sf.is_private_number(m, text) for m in sf.NUMBER.finditer(text)) or bool(sf.TOKEN.search(text))
for text, want in (("Your OTP is 4821", True), ("code: 482-913", True), ("482 913", True), ("SSN 123-45-6789", True),
                   ("key sk-live-abcdefghijklmnop1234", True), ("+91 98765 43210", True),
                   ("2026-10-22 14:35:07", False), ("192.168.100.200", False), ("1500.00 2026", False),
                   ("0.123456", False), ("Order #4821 2026", False)):
    assert private(text) == want, text

# overlapping spotlights stay bright where they overlap
cv.shapes.clear()
cv.push(dict(t="spotlight", r=QRectF(50, 50, 100, 80), c="#000", w=4))
cv.push(dict(t="spotlight", r=QRectF(100, 70, 100, 80), c="#000", w=4))
assert cv.render().pixelColor(125, 100) == img.pixelColor(125, 100)
cv.shapes.clear()

# editor: freed when closed, and Done waits for a running "Hide private info"
ed2 = sf.Editor(app, QPixmap.fromImage(img), lambda p: done_calls.append(p))
done_calls = []
assert ed2.testAttribute(sf.Qt.WA_DeleteOnClose)
ed2.pending = 1
ed2.finish()
assert ed2.finish_after_ocr and not done_calls, "Done must wait for OCR"
ed2.canvas.push(dict(t="blur", r=QRectF(0, 0, 50, 50), c="#000", w=1))  # what the OCR result would add
ed2.pending = 0
ed2.finish()
assert len(done_calls) == 1, "and finish once it's back, with the blur"
crop_ed = sf.Editor(app, QPixmap.fromImage(img), lambda p: None)
crop_ed.canvas.push(dict(t="crop", r=QRectF(10, 10, 50, 50), c="#000", w=1))
assert crop_ed.canvas.render().size().toTuple() == (50, 50) and crop_ed.canvas.render(crop=False).size() == img.size()

# editor: select/move/delete/resize are undoable; a moved mark keeps the original for undo
mk = sf.Editor(app, QPixmap.fromImage(img), lambda p: None)
mc = mk.canvas
mc.resize(500, 400)
mc.resizeEvent(None)
mc.push(dict(t="rect", r=QRectF(100, 100, 50, 50), c="#ff3b30", w=4))
mk.set_tool("select")
press = lambda pt, kind: getattr(mc, kind)(QMouseEvent(
    {"mousePressEvent": QEvent.MouseButtonPress, "mouseMoveEvent": QEvent.MouseMove,
     "mouseReleaseEvent": QEvent.MouseButtonRelease}[kind], mc.off + pt * mc.k, mc.off + pt * mc.k,
    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
press(QPointF(100, 125), "mousePressEvent")
assert mc.sel == 0, "clicking a box's edge selects it"
press(QPointF(140, 165), "mouseMoveEvent")
press(QPointF(140, 165), "mouseReleaseEvent")
assert mc.shapes[0]["r"].topLeft() == QPointF(140, 140)
mk.resize_marks(True)
assert mc.shapes[0]["w"] == 5
mc.undo()
mc.undo()
assert mc.shapes[0]["r"].topLeft() == QPointF(100, 100) and mc.shapes[0]["w"] == 4, "undo restores move and resize"
mc.redo()
mc.sel = 0
mc.delete_selected()
assert not mc.shapes
mc.undo()
assert len(mc.shapes) == 1
mk.set_tool("arrow")
mk.resize_marks(True)
assert mk.scale == 1.5 and mk.size_label.text() == "1.5×"

# a magnifier never shows what a blur hides, and multi-line text renders every line
mc.change([dict(t="blur", r=QRectF(0, 0, 100, 100), c="#000", w=1),
           dict(t="magnify", r=QRectF(30, 30, 40, 40), at=QPointF(250, 150), c="#ff3b30", w=2)])
out = mc.render()
assert out.pixelColor(250, 150) == out.pixelColor(50, 50), "callout centre shows the blurred pixel, not the original"
assert out.pixelColor(50, 50) != img.pixelColor(50, 50)
mc.change([dict(t="text", at=QPointF(20, 40), text="one\ntwo", c="#ffffff", size=22)])
b = sf.shape_bounds(mc.shapes[0])
assert b.height() > 40, "two lines tall"
assert any(mc.render().pixelColor(x, 62) != img.pixelColor(x, 62) for x in range(20, 60)), "second line drawn"

# step numbers continue after a deletion; the picker copies a pixel's colour
mc.change([dict(t="step", at=QPointF(10, 10), n=1, c="#000", size=10), dict(t="step", at=QPointF(40, 10), n=2, c="#000", size=10)])
mc.change(mc.shapes[1:])
mk.set_tool("step")
press(QPointF(80, 80), "mousePressEvent")
assert mc.shapes[-1]["n"] == 3
mk.set_tool("picker")
press(QPointF(10, 20), "mousePressEvent")
assert mk.color == img.pixelColor(10, 20).name() and app.clipboard().text() == mk.color

# "Hide private info" is a preview: a clicked box stays visible, Enter blurs the rest as one undo
mc.change([])
mc.propose([QRectF(0, 0, 40, 40), QRectF(200, 200, 40, 40)])
press(QPointF(210, 210), "mousePressEvent")
assert not mc.shapes and [s["on"] for s in mc.proposals] == [True, False]
assert mc.apply_proposals() == 1 and mc.shapes[0]["rs"] == [QRectF(0, 0, 40, 40)] and not mc.proposals
mk.close()

# Read QR code: decodes, copies, and says so; a plain shot just says nothing was found
if sf.zxingcpp:
    code = bytes(sf.zxingcpp.create_barcode("https://example.com/qr", sf.zxingcpp.BarcodeFormat.QRCode).to_image(scale=6))
    side = int(len(code) ** 0.5)
    qr = QImage(code, side, side, side, QImage.Format_Grayscale8).copy()
    canvas = QImage(800, 500, QImage.Format_RGB32)
    canvas.fill(QColor("white"))
    from PySide6.QtGui import QPainter
    qp = QPainter(canvas)
    qp.drawImage(300, 120, qr)
    qp.end()
    assert sf.read_qr(canvas) == ("✓ Copied to clipboard", "https://example.com/qr")
    assert app.clipboard().text() == "https://example.com/qr"
    assert sf.read_qr(img)[0] == "No QR code found"

# a 6th screenshot never closes one that's being edited or is pinned
for t in list(app.thumbs):
    t.dismiss()
small = QPixmap(100, 80)
small.fill(QColor("green"))
app.show_shot(small)
kept = app.thumbs[-1]
kept.pinned = True
for _ in range(sf.MAX_THUMBS):
    app.show_shot(small)
assert kept in app.thumbs, "pinned thumbnail evicted"
kept.pinned = False

# one capture at a time; every capture result (even a cancel) frees it again
app.busy_since = 0.0
assert app.start_capture() and not app.start_capture()
app.show_shot(None)
assert app.start_capture()
app.show_shot(None)

# combine: a 1x shot next to a 2x one comes out at the same visual scale
one, two = QPixmap(100, 50), QPixmap(200, 100)
one.fill(QColor("red"))
two.fill(QColor("blue"))
two.setDevicePixelRatio(2)
assert sf.combine([one, two]).width() == 200

# autostart Exec= quoting (spaces, quotes, $, %)
assert sf.desktop_exec(["/home/a b/Snap Float.AppImage"]) == '"/home/a b/Snap Float.AppImage"'
assert sf.desktop_exec(["/x/snapfloat", "--region"]) == "/x/snapfloat --region"
assert sf.desktop_exec(['/a"b$c%d']) == '"/a\\"b\\$c%%d"'

# temp folder: refuse one someone else planted (symlink), and purge leaves folders alone / never creates it
real_tmp = sf.TMP
victim = sf.Path(tempfile.mkdtemp())
(victim / "keep.txt").write_text("x")
planted = sf.Path(tempfile.mkdtemp()) / "planted"
planted.symlink_to(victim)
victim.chmod(0o755)
sf.TMP = planted
assert sf.tmp_dir() != planted, "a planted symlink must be refused"
assert (victim.stat().st_mode & 0o777) == 0o755, "the folder it points to must not be re-permissioned"
assert not sf.TMP.is_symlink() and (sf.TMP.stat().st_mode & 0o777) == 0o700
(sf.TMP / "sub").mkdir()
sf.purge()
assert (sf.TMP / "sub").is_dir()
gone = sf.Path(tempfile.mkdtemp()) / "nope"
sf.TMP = gone
sf.purge()
assert not gone.exists(), "purge must not create the folder (it ran after uninstall had removed it)"
sf.TMP = real_tmp
assert (victim / "keep.txt").exists(), "a planted symlink must never be emptied"

# Selector: a drag starting exactly at the monitor's top-left pixel still selects
got = []
sel = sf.Selector(app.primaryScreen(), got.append)
class Ev:
    def __init__(self, x, y): self.p = QPointF(x, y)
    def position(self): return self.p
    def button(self): return sf.Qt.LeftButton
sel.mousePressEvent(Ev(0, 0))
sel.mouseMoveEvent(Ev(40, 30))
sel.mouseReleaseEvent(Ev(40, 30))
assert got and got[0] is not None, "selection starting at (0, 0) was ignored"
sel.close()

# private temp folder, start-at-login default
assert (sf.TMP.stat().st_mode & 0o777) == 0o700, oct(sf.TMP.stat().st_mode)
assert app.cfg.autostart is True and (sf.Path.home() / ".config/autostart/snapfloat.desktop").exists()

# Float on screen: its own window (titled for the GNOME extension), zooms keeping its shape, fades, Esc closes it
for t in list(app.thumbs):
    t.dismiss()
app.show_shot(pix)
app.thumbs[-1].float_on_screen()
pin = app.pins[-1]
assert pin.windowTitle() == "snapfloat-pin" and not app.thumbs, "the floating copy replaces the thumbnail"
from PySide6.QtGui import QWheelEvent, QKeyEvent
from PySide6.QtCore import QPoint
wheel = lambda mods: pin.wheelEvent(QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120), Qt.NoButton, mods,
                                                Qt.NoScrollPhase, False))
w0 = pin.width()
wheel(Qt.NoModifier)
assert pin.width() > w0 and abs(pin.width() / pin.height() - 800 / 600) < 0.02
wheel(Qt.ControlModifier)
assert pin.opacity == 1.0, "can't go above fully opaque"
pin.wheelEvent(QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, -360), Qt.NoButton, Qt.ControlModifier,
                           Qt.NoScrollPhase, False))
assert abs(pin.opacity - 0.7) < 1e-9
pin.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
app.sendPostedEvents(None, QEvent.DeferredDelete)
assert not app.pins, "closing frees it"

# region selector (Windows/X11): a click captures the window under it (Windows), a drag still captures the region
scr = app.primaryScreen()
got = []
origin = scr.geometry().topLeft()
sel = sf.Selector(scr, got.append, [sf.QRect(origin.x() + 50, origin.y() + 40, 300, 200), sf.QRect(origin, scr.geometry().size())])
sel.resize(scr.geometry().size())
d = sel.shot.devicePixelRatio()
ev = lambda kind, x, y: getattr(sel, kind)(QMouseEvent(
    {"mousePressEvent": QEvent.MouseButtonPress, "mouseMoveEvent": QEvent.MouseMove,
     "mouseReleaseEvent": QEvent.MouseButtonRelease}[kind], QPointF(x, y), QPointF(x, y), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
ev("mouseMoveEvent", 100 / d, 100 / d)
assert sel.hover == sf.QRect(50, 40, 300, 200), "topmost window under the pointer is highlighted"
ev("mousePressEvent", 100 / d, 100 / d)
ev("mouseReleaseEvent", 100 / d, 100 / d)
assert got[-1].size().toTuple() == (300, 200), "a click captures that window"
sel.origin, sel.sel = None, sf.QRect()
ev("mousePressEvent", 10, 10)
ev("mouseMoveEvent", 60, 50)
ev("mouseReleaseEvent", 60, 50)
assert abs(got[-1].width() - 50 * d) <= d and abs(got[-1].height() - 40 * d) <= d, "a drag still captures the region"
sel.close()
assert sf.windows_under_desktop() == [] or os.name == "nt"
# scrolling capture: frames of a scrolled page stitch back into exactly the page
import random as _random
from PySide6.QtGui import QPainter as _QP


def page(w, h, seed):
    """A fake web page: random text-like blocks with blank gaps (blank rows match everywhere: the hard case)."""
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor("white"))
    rnd, p = _random.Random(seed), _QP(img)
    for y in range(0, h, 18):
        if 1450 <= y < 1600:  # a long blank stretch
            continue
        for _ in range(rnd.randint(1, 4)):
            p.fillRect(rnd.randrange(w - 40), y + rnd.randrange(6), rnd.randint(5, 40), rnd.randint(3, 10),
                       QColor(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    p.end()
    return img


W, VIEW, HEAD, FOOT = 300, 330, 40, 30
content = page(W, 3000, 1)
header, footer = page(W, HEAD, 2), page(W, FOOT, 3)


def frame(top):
    img = QImage(W, HEAD + VIEW + FOOT, QImage.Format_RGB32)
    p = _QP(img)
    p.drawImage(0, 0, header)
    p.drawImage(0, HEAD, content, 0, top, W, VIEW)
    p.drawImage(0, HEAD + VIEW, footer)
    p.end()
    return img


# duplicates, a scroll back up (290), a jump too far to match (2600 after 2000), two frames ending in blank rows (1150, 1250)
tops = [0, 0, 37, 150, 151, 300, 290, 480, 780, 1050, 1150, 1250, 1400, 1400, 1700, 2000, 2600, 2300, 2600, 2670]
frames = [frame(t) for t in tops]
expect = QImage(W, HEAD + 3000 + FOOT, QImage.Format_RGB32)
ep = _QP(expect)
ep.drawImage(0, 0, header)
ep.drawImage(0, HEAD, content)
ep.drawImage(0, HEAD + 3000, footer)
ep.end()
st = sf.Stitcher(frames[0])
added = [st.add(f) for f in frames[1:]]
A, S, L = "added", "still", "lost"
assert added == [S, A, A, A, A, L, A, A, A, A, A, A, S, A, A, L, A, A, A], added
assert st.foot == FOOT, "blank rows that happened to stay put aren't mistaken for the footer"
assert sf.stitch(frames) == expect, "stitched page differs from the original"
assert sf.stitch(frames[:1]) == frames[0], "a single frame is returned as is"
assert sf.stitch([frame(500)] * 3) == frame(500), "no scrolling: just the one frame"
plain = [frame(t) for t in (0, 200, 400)]  # no header/footer at all
for i, f in enumerate(plain):
    plain[i] = f.copy(0, HEAD, W, VIEW)
assert sf.stitch(plain) == content.copy(0, 0, W, 400 + VIEW)

# fractional scaling (167%): scrolled text is redrawn at sub-pixel positions, so no row repeats exactly; it must still join,
# and a scroll back up or a jump past the area must be refused rather than joined in the wrong place
K, FW, FH, FHEAD = 5 / 3, 700, 500, 40
_rnd = _random.Random(7)
_lines = [" ".join("".join(_rnd.choice("abcdefghijklmnop") for _ in range(_rnd.randint(2, 8))) for _ in range(_rnd.randint(3, 9)))
          for _ in range(150)]


def screen_at(top):
    img = QImage(FW, FH, QImage.Format_RGB32)
    img.fill(QColor("white"))
    qp = _QP(img)
    qp.setRenderHint(_QP.TextAntialiasing)
    qp.scale(K, K)
    qp.setClipRect(sf.QRectF(0, FHEAD, FW / K, FH / K - FHEAD))
    qp.translate(0, FHEAD - top)
    for i, t in enumerate(_lines):
        qp.drawText(8, 18 + i * 22, f"{i:03d} {t}")
    qp.setClipping(False)
    qp.resetTransform()
    qp.scale(K, K)
    qp.fillRect(sf.QRectF(0, 0, FW / K, FHEAD), QColor(235, 235, 240))
    qp.drawText(8, 26, "sticky header")
    qp.end()
    return img


fs = sf.Stitcher(screen_at(0))
steps = [(97, A), (180, A), (171, L), (333, A), (334, A), (500, A), (1000, L), (640, A)]
assert [fs.add(screen_at(t)) for t, _ in steps] == [w for _, w in steps]
assert abs(fs.result().height() - (FH + 640 * K)) <= 1, fs.result().height()

full = page(800, 600, 4)

# the capture loop: grabs while you scroll, ignores failed grabs, Done makes a thumbnail of the whole page
for t in list(app.thumbs):
    t.dismiss()
feed = iter(frames[1:6] + [None] + frames[6:])
sc = sf.ScrollCapture(app, frames[0], lambda then: then(next(feed)))
assert sc.timer.isActive() and not sc.hide_to_grab
for _ in range(len(frames)):
    sc.tick()
assert sc.label.text().endswith(f"{added.count('added') + 1} parts")
sc.done()
assert sc.timer is None and app.scroller is None
assert app.thumbs[-1].full.toImage().convertToFormat(QImage.Format_RGB32) == expect
app.thumbs[-1].dismiss()

# start-up: the screen is frozen, you drag the area on it, and every later grab is cropped to exactly that area
real_full = app.full_screen
app.full_screen = lambda then: then(full)
app.busy_since = 0.0
app.scrolling()
app.processEvents()  # thumbnails hide first
for _ in range(20):
    if app.picker:
        break
    app.processEvents()
    sf.time.sleep(0.02)
pk = app.picker
assert pk is not None and pk.shot.toImage().convertToFormat(QImage.Format_RGB32) == full, "the frozen screen is the screenshot"
d = pk.shot.devicePixelRatio()
mouse = lambda kind, x, y, b=Qt.LeftButton: getattr(pk, kind)(QMouseEvent(
    {"mousePressEvent": QEvent.MouseButtonPress, "mouseMoveEvent": QEvent.MouseMove,
     "mouseReleaseEvent": QEvent.MouseButtonRelease}[kind], QPointF(x / d, y / d), QPointF(x / d, y / d), b, b, Qt.NoModifier))
mouse("mousePressEvent", 120, 40)
mouse("mouseMoveEvent", 320, 140)
mouse("mouseReleaseEvent", 320, 140)
sc, picked = app.scroller, pk.picked
assert sc is not None and app.picker is None and abs(picked.width() - 200) <= 1 and abs(picked.height() - 100) <= 1
got = []
sc.take(got.append)
assert got[0] == full.copy(picked), "later grabs are cropped to the dragged area"
assert sc.stitcher.first == full.copy(picked)
sc.finish(None)
assert app.scroller is None and app.busy_since == 0.0
app.scrolling()  # Esc / right-click on the frozen screen cancels
for _ in range(20):
    if app.picker:
        break
    app.processEvents()
    sf.time.sleep(0.02)
app.picker.finish(None)
assert app.scroller is None and app.picker is None and app.busy_since == 0.0
app.full_screen = real_full
# screen recording: the commands, then start → stop → a video thumbnail (a fake recorder stands in for ffmpeg/GStreamer)
from pathlib import Path
stem = Path(tempfile.gettempdir()) / "Recording x"
cmd, out = sf.record_cmd(stem)
assert out.suffix == ".mp4" and cmd[cmd.index("-f") + 1] == "x11grab" and "yuv420p" in cmd and cmd[-1] == str(out)
real_enc, sf.WAYLAND = sf.gst_encoder, True
sf.gst_encoder = lambda: (["vp8enc", "!", "webmmux"], ".webm")
try:
    cmd, out = sf.record_cmd(stem, 7, 42)
    assert cmd[:3] == ["gst-launch-1.0", "-e", "-q"] and "fd=7" in cmd and "path=42" in cmd
    assert out.suffix == ".webm" and cmd[-1] == f"location={out}", "the path stays one argv item (gst-launch escapes its spaces)"
finally:
    sf.WAYLAND, sf.gst_encoder = False, real_enc

import time
def wait_for(cond, secs=15):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    return cond()

real_cmd, errors = sf.record_cmd, []
app.recorder.error.connect(errors.append)
fake = "import sys; sys.stdin.read(1); open(sys.argv[1], 'wb').write(b'video')"  # like ffmpeg: "q" on stdin stops it
sf.record_cmd = lambda stem, fd=None, node=None: ([sys.executable, "-c", fake, str(stem.with_suffix(".mp4"))], stem.with_suffix(".mp4"))
try:
    for t in list(app.thumbs):
        t.dismiss()
    app.record()
    assert app.recorder.busy and app.record_action.text() == "Stop recording"
    app.tray_click(sf.QSystemTrayIcon.Trigger)  # a tray click stops it
    assert wait_for(lambda: not app.recorder.busy), "recording never finished"
    vid = app.thumbs[-1]
    assert vid.video and vid.path.suffix == ".mp4" and vid.path.read_bytes() == b"video" and vid.drag_file() == vid.path
    assert vid.path.with_suffix(".png").exists() and not vid.path.with_suffix(".log").exists() and not errors
    assert app.record_action.text() == "Record screen"
    vid.dismiss()
    app.reshow(vid.path)
    assert app.thumbs[-1].path == vid.path, "a recording comes back from Recent"
    sf.forget(vid.path)
    assert not vid.path.exists() and not vid.path.with_suffix(".png").exists()
    app.thumbs[-1].dismiss()
    n = len(app.thumbs)
    sf.record_cmd = lambda stem, fd=None, node=None: ([sys.executable, "-c", "import sys; sys.stderr.write('no screen\\n'); sys.exit(1)"],
                                                      stem.with_suffix(".mp4"))
    app.record()
    assert wait_for(lambda: not app.recorder.busy)
    assert errors == ["no screen"] and len(app.thumbs) == n, "a failed recording says why and shows nothing"
finally:
    sf.record_cmd = real_cmd

# --self-test checks a build end to end and exits non-zero on any failure
r = sf.subprocess.run([sys.executable, "-m", "snapfloat", "--self-test"], capture_output=True, text=True, timeout=120,
                      cwd=os.path.dirname(os.path.abspath(__file__)))
assert r.returncode == 0 and "self-test: passed" in r.stdout, r.stdout + r.stderr

# uninstall removes everything install.sh and the app created (GNOME calls disabled: never touch the real desktop)
home = sf.Path.home()
for f in (".local/bin/snapfloat", ".local/share/applications/snapfloat.desktop", ".local/share/snapfloat/venv/x",
          f".local/share/gnome-shell/extensions/{sf.EXT_UUID}/extension.js"):
    (home / f).parent.mkdir(parents=True, exist_ok=True)
    (home / f).write_text("x")
real_which, sf.shutil.which = sf.shutil.which, lambda name: None
try:
    sf.uninstall(quit_running=False)
finally:
    sf.shutil.which = real_which
left = [str(p.relative_to(home)) for p in home.rglob("*") if "snapfloat" in str(p).lower() and p.is_file()]
assert not left and not sf.TMP.exists(), left
print("ok")
