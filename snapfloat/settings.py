"""The Settings window."""
import os, re, sys

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QKeySequence, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea, QSlider, QToolButton, QVBoxLayout,
                               QWidget)

from .common import ACCENT, CORNERS, DEFAULTS, icon, QSS, SMALL_MAX, themed, WAYLAND
from .hotkeys import gnome_ext_problem, is_fkey, KEY_NAMES
from .ocr import ocr_langs
from .system import APP, AUTHOR, HOMEPAGE, uninstall


SHIFTED = dict(zip("!@#$%^&*()", "1234567890"))  # ponytail: US layout; others may record the shifted symbol


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
