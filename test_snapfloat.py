"""Smoke test, no display needed: python test_snapfloat.py"""
import os, sys, tempfile
# isolate everything: a running SnapFloat's socket, temp shots, config and autostart must stay untouched
tempfile.tempdir = tempfile.mkdtemp()  # gettempdir() is cached, so TMPDIR alone would still hit the real /tmp
os.environ.update(QT_QPA_PLATFORM="offscreen", XDG_CURRENT_DESKTOP="", XDG_CONFIG_HOME=tempfile.mkdtemp(),
                  XDG_RUNTIME_DIR=tempfile.mkdtemp(), HOME=tempfile.mkdtemp(), TMPDIR=tempfile.tempdir)
sys.argv = sys.argv[:1]
import snapfloat as sf
assert str(sf.TMP).startswith(tempfile.tempdir), "test must never touch the real SnapFloat temp folder"
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPixmap

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
nice = sf.beautify(QPixmap.fromImage(img))
assert nice.width() > img.width() + 90 and nice.toImage().pixelColor(2, 2) != QColor("white")
both = sf.combine([QPixmap.fromImage(img), QPixmap.fromImage(img)])
assert both.size().toTuple() == (400, 300 * 2 + 16)

# private temp folder, start-at-login default
assert (sf.TMP.stat().st_mode & 0o777) == 0o700, oct(sf.TMP.stat().st_mode)
assert app.cfg.autostart is True and (sf.Path.home() / ".config/autostart/snapfloat.desktop").exists()

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
