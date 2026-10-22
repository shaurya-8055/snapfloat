"""Scrolling capture: grab an area while it scrolls and stitch the frames into one image."""
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget
from shiboken6 import isValid

from .common import QSS, themed, WAYLAND


def row_sigs(img):
    """One hash per pixel row. Screenshots of scrolled content repeat rows exactly, so equal hashes = the same row."""
    bpl, data = img.bytesPerLine(), bytes(img.constBits())
    return [hash(data[y * bpl:(y + 1) * bpl]) for y in range(img.height())]


def scroll_offset(prev, cur):
    """How many rows the content moved up from frame `prev` to `cur` (row hashes), and how many rows at the bottom stayed
    put (a fixed footer, or blank space). None: it didn't move, moved up, or the frames don't overlap.
    Rows that stay put at the top or bottom (sticky headers, footers) don't take part in the matching."""
    n = len(cur)
    head = next((y for y in range(n) if prev[y] != cur[y]), n)
    if head == n:
        return None
    foot = next(i for i in range(n) if prev[n - 1 - i] != cur[n - 1 - i])
    lo, hi = head, n - foot
    where = {}
    for y in range(lo, hi):
        where.setdefault(prev[y], []).append(y)
    votes = {}
    for y in range(lo, hi):
        ys = where.get(cur[y], ())
        if len(ys) <= 4:  # blank and repeated rows match anywhere: they don't get a vote
            for py in ys:
                if py > y:
                    votes[py - y] = votes.get(py - y, 0) + 1
    if not votes:
        return None
    d = max(votes, key=votes.get)  # ponytail: one best shift; a page of identical rows could fool it
    overlap = hi - lo - d
    if overlap < 8 or sum(cur[y] == prev[y + d] for y in range(lo, lo + overlap)) < 0.8 * overlap:
        return None
    return d, foot


class Stitcher:
    """One tall image from frames of an area taken while it scrolls. Keeps only each frame's new rows (and the rows below
    them, in case those turn out to be part of a fixed footer), never whole frames, so long pages stay small in memory."""

    def __init__(self, first):
        self.first = first.convertToFormat(QImage.Format_RGB32)
        self.sigs, self.parts, self.foot = row_sigs(self.first), [], self.first.height()

    def add(self, img):
        """Returns whether the frame added anything (False: not scrolled, scrolled up, or too far to match)."""
        img = img.convertToFormat(QImage.Format_RGB32)
        if img.size() != self.first.size():
            return False
        sigs = row_sigs(img)
        moved = scroll_offset(self.sigs, sigs)
        if not moved:
            return False
        d, foot = moved
        self.parts.append((d, img.copy(0, img.height() - foot - d, img.width(), foot + d)))
        self.foot = min(self.foot, foot)  # the real footer is what stayed put at every step
        self.sigs = sigs
        return True

    def height(self):
        return self.first.height() + sum(d for d, _ in self.parts)

    def result(self):
        f = self.foot if self.parts else 0
        w, top = self.first.width(), self.first.height() - f
        out = QImage(w, self.height(), QImage.Format_RGB32)
        p = QPainter(out)
        p.drawImage(0, 0, self.first, 0, 0, w, top)
        y = top
        for d, part in self.parts:
            p.drawImage(0, y, part, 0, part.height() - f - d, w, d)  # the frame's new rows, just above the footer
            y += d
        last = self.parts[-1][1] if self.parts else self.first
        p.drawImage(0, y, last, 0, last.height() - f, w, f)
        p.end()
        return out


def stitch(frames):
    s = Stitcher(frames[0])
    for f in frames[1:]:
        s.add(f)
    return s.result()


def locate(part, whole):
    """Where the screenshot `part` sits in the screenshot `whole`, pixel for pixel, as a QRect; None if it isn't there."""
    part, whole = part.convertToFormat(QImage.Format_RGB32), whole.convertToFormat(QImage.Format_RGB32)
    pb, wb, n = part.bytesPerLine(), whole.bytesPerLine(), part.height()
    pd, wd = bytes(part.constBits()), bytes(whole.constBits())
    rows = [pd[y * pb:(y + 1) * pb] for y in range(n)]
    key = max(range(0, n, max(1, n // 50)), key=lambda y: len(set(rows[y])))  # a varied row: few false hits
    for wy in range(key, whole.height() - (n - key) + 1):
        line = wd[wy * wb:(wy + 1) * wb]
        i = line.find(rows[key])
        while i != -1:
            top = wy - key
            if i % 4 == 0 and all(wd[(top + y) * wb + i:(top + y) * wb + i + pb] == rows[y] for y in range(n)):
                return QRect(i // 4, top, part.width(), n)
            i = line.find(rows[key], i + 1)
    return None


class ScrollCapture(QWidget):
    """The bar shown during a scrolling capture. Windows/X11 grab the area every 0.4 s while you scroll; on Wayland every
    grab is a screenshot the desktop takes for us, so you scroll, then press Add. Done stitches the parts into one image."""
    MAX_HEIGHT = 30000  # px: stop there (an endless feed would grow forever)

    def __init__(self, app, first, grab, area=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle("snapfloat-pin")  # Wayland: the GNOME extension keeps windows with this title above others
        self.app, self.take, self.stitcher, self.busy = app, grab, Stitcher(first), False
        themed(self, QSS)
        self.setAttribute(Qt.WA_TranslucentBackground)  # only the rounded card shows
        card = QFrame(objectName="card")
        QHBoxLayout(self).addWidget(card)
        self.layout().setContentsMargins(0, 0, 0, 0)
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 8, 8, 8)
        self.label = QLabel()
        h.addWidget(self.label)
        if WAYLAND:
            h.addWidget(QPushButton("Add", clicked=self.tick, cursor=Qt.PointingHandCursor))
        h.addWidget(QPushButton("Done", objectName="primary", clicked=self.done, cursor=Qt.PointingHandCursor))
        h.addWidget(QPushButton("Cancel", clicked=lambda: self.finish(None), cursor=Qt.PointingHandCursor))
        self.count()
        self.adjustSize()
        self.hide_to_grab = WAYLAND  # Wayland: we can't place this bar, so it might sit over the area
        if area is not None:  # Windows/X11: just below (or above) the area, where the grabs don't see it
            scr, rect = area
            d, g = scr.devicePixelRatio(), scr.geometry()
            r = QRect(g.x() + round(rect.x() / d), g.y() + round(rect.y() / d), round(rect.width() / d), round(rect.height() / d))
            y = r.bottom() + 12 if r.bottom() + 12 + self.height() <= g.bottom() else r.top() - 12 - self.height()
            self.move(max(g.left(), min(r.center().x() - self.width() // 2, g.right() - self.width())), max(g.top(), y))
            self.hide_to_grab = self.frameGeometry().intersects(r)  # no room around a full-screen area
        self.timer = QTimer(self, interval=400, timeout=self.tick)
        if not WAYLAND:
            self.timer.start()

    def count(self):
        n = len(self.stitcher.parts) + 1
        self.label.setText(f"{'Scroll, then Add' if WAYLAND else 'Scroll slowly'} · {n} part{'s' * (n != 1)}")

    def tick(self):
        if self.busy:  # the last grab is still on its way (Wayland)
            return
        self.busy = True
        if self.hide_to_grab:
            self.hide()
            QTimer.singleShot(150, lambda: self.take(self.got))  # let the compositor repaint without us
        else:
            self.take(self.got)

    def got(self, img):
        self.busy = False
        if not isValid(self) or not self.timer:  # finished meanwhile
            return
        if img is not None and self.stitcher.add(img):
            self.count()
        if self.hide_to_grab:
            self.show()
        if self.stitcher.height() >= self.MAX_HEIGHT:
            self.done()

    def done(self):
        self.finish(QPixmap.fromImage(self.stitcher.result()))

    def finish(self, pix):
        self.timer.stop()
        self.timer = None
        self.close()
        self.app.scroller = None
        self.app.show_shot(pix)
