"""Smoke test, no display needed: python test_snapfloat.py"""
import os, sys, tempfile
# isolate everything: a running SnapFloat's socket, temp shots, config and autostart must stay untouched
os.environ.update(QT_QPA_PLATFORM="offscreen", XDG_CURRENT_DESKTOP="", XDG_CONFIG_HOME=tempfile.mkdtemp(),
                  XDG_RUNTIME_DIR=tempfile.mkdtemp(), HOME=tempfile.mkdtemp(), TMPDIR=tempfile.mkdtemp())
sys.argv = sys.argv[:1]
import snapfloat as sf
from PySide6.QtGui import QPixmap, QColor

assert sf.gnome_accel("Ctrl+Shift+4") == "<Control><Shift>4"
assert sf.gnome_accel("Super+S") == "<Super>s"
assert sf.gnome_accel("Print") == "Print"
assert sf.gnome_accel("") == ""

app = sf.App()
assert app.cfg.seconds == 6 and app.cfg.copy is True
pix = QPixmap(800, 600); pix.fill(QColor("red"))
for _ in range(sf.MAX_THUMBS + 2):
    app.show_shot(pix)
assert len(app.thumbs) == sf.MAX_THUMBS, len(app.thumbs)
t = app.thumbs[-1]
scr = app.primaryScreen().availableGeometry()
assert t.geometry().right() == scr.right() and t.geometry().bottom() == scr.bottom(), t.geometry()
assert t.path.exists() and t.path.parent == sf.TMP
assert app.clipboard().pixmap().size() == pix.size()
t.dismiss()
assert t not in app.thumbs and len(app.thumbs) == sf.MAX_THUMBS - 1
app.show_shot(None)  # cancelled capture must not crash
print("ok")
