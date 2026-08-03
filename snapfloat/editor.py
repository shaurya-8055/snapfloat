"""The markup editor window."""
from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, QSizeF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from .common import ACCENT, EDITOR_QSS, icon, QSS, set_clipboard, themed
from .draw import (COLORS, crop_rect, paint_blurs, paint_shapes, place_callout, SCALES, shape_bounds, shape_hit,
                   shape_moved, shape_scaled, text_font, TEXT_PX, tool_icon, TOOLS)
from .ocr import OCR_HINT, ocr_message, private_boxes, run_ocr, tesseract
from .system import APP


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
