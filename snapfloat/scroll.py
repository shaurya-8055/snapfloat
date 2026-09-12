"""Scrolling capture: grab an area while it scrolls and stitch the frames into one image."""
import sys, threading

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from .common import QSS, themed, WAYLAND


SIG_W = 96  # frames are compared shrunk to this width: fast, blind to sub-pixel differences, yet words still differ


def signature(img):
    """A frame reduced to what the matching looks at. Exact pixels can't be compared: on a fractionally scaled screen
    (167%, 125%...) scrolled text is redrawn at sub-pixel positions, so no row ever repeats exactly."""
    return img.scaled(SIG_W, img.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)


class Content:
    """Where a signature has content worth matching on: rows with something in them, within the columns that moved.
    Blank rows look the same at any scroll position, and columns that stay put (a sidebar, a side panel, the desktop
    beside a window) match only at "no scroll": either would make a wrong position look as good as the right one."""

    def __init__(self, sig, moving):
        g = sig.convertToFormat(QImage.Format_Grayscale8)
        bpl, data, w = g.bytesPerLine(), bytes(g.constBits()), g.width()
        cols = [x for x in range(w) if moving[x]]
        on = [max(r) - min(r) > 24 for r in (bytes(data[y * bpl + x] for x in cols) for y in range(g.height()))]
        # black must be opaque (alpha 0xff): RGB32 requires it, and with alpha 0 the multiply let everything through
        live = b"".join(b"\xff\xff\xff\xff" if m else b"\x00\x00\x00\xff" for m in moving)
        blank = b"\x00\x00\x00\xff" * w
        self.mask = QImage(b"".join(live if o else blank for o in on), w, len(on), 4 * w, QImage.Format_RGB32).copy()
        self.share = len(cols) / w  # of the width that counts
        self.before = [0]  # how many content rows come before each row
        for o in on:
            self.before.append(self.before[-1] + o)

    def count(self, y, n):
        return self.before[y + n] - self.before[y]


def mean_diff(a, b, ay, by, n, content=None):
    """Average difference (0-255) between n rows of signature a from row ay and of b from row by; with `content` (b's),
    over b's content rows only (None if there are too few to judge). The pixel work is a Qt "difference" paint, a
    multiply by the mask and a smooth shrink, so it runs in C, not Python."""
    rows = content.count(by, n) if content else n
    if content and rows < 3:
        return None
    w = a.width()
    d = a.copy(0, ay, w, n)
    p = QPainter(d)
    p.setCompositionMode(QPainter.CompositionMode_Difference)
    p.drawImage(0, 0, b, 0, by, w, n)
    if content:
        p.setCompositionMode(QPainter.CompositionMode_Multiply)
        p.drawImage(0, 0, content.mask, 0, by, w, n)
    p.end()
    small = d.scaled(4, 16, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)  # kept: constBits() points into it
    px = bytes(small.constBits())  # its 64 pixels, read at once
    total = sum(map(max, px[0::4], px[1::4], px[2::4]))  # each pixel's strongest channel (blue, green, red; not alpha)
    return total / 64 * n / rows / (content.share if content else 1)


def changes(a, b):
    """Between two signatures: which rows are unchanged, and which columns changed. Judged by the largest difference,
    not the average: after a scroll by whole lines, rows of similar text line up and differ only in a few characters."""
    d = a.copy()
    p = QPainter(d)
    p.setCompositionMode(QPainter.CompositionMode_Difference)
    p.drawImage(0, 0, b)
    p.end()
    bpl, data, w = d.bytesPerLine(), bytes(d.constBits()), d.width()
    rows = (data[y * bpl:(y + 1) * bpl] for y in range(d.height()))
    same = [max(max(r[0::4]), max(r[1::4]), max(r[2::4])) < 16 for r in rows]  # blue, green, red; not alpha (always 255)
    moving = [max(max(data[x * 4 + k::bpl]) for k in range(3)) >= 16 for x in range(w)]
    return same, moving


def log(*what):
    print("scrolling capture:", *what, file=sys.stderr, flush=True)  # into the log file: why a part did or didn't join


def scroll_offset(prev, cur):
    """How far the content moved up from signature `prev` to `cur`, and how many rows at the bottom stayed put (a fixed
    footer, or blank space), and which signature columns moved: (rows, footer, moving), or a reason it can't be used: "still" (it didn't move) or "lost" (moved
    up, too far, or nothing to match on). Rows that stay put at the top or bottom (sticky header, footer) are left out."""
    n = cur.height()
    same, moving = changes(prev, cur)
    head = next((y for y in range(n) if not same[y]), n)
    if head == n:
        return "still"
    foot = next(i for i in range(n) if not same[n - 1 - i])
    lo, hi = head, n - foot
    band = hi - lo
    least = max(12, band // 20)  # the frames must overlap by at least this much
    if band < 2 * least:
        return "lost"
    # Try every shift at full detail. Anything coarser (fewer shifts, a shorter, narrower or blurred signature) blurs away
    # what tells similar lines apart, often just a few characters, and lands a line off. It runs off the UI thread.
    con = Content(cur, moving)
    costs = {d: c for d in range(1, band - least) if (c := mean_diff(prev, cur, lo + d, lo, band - d, con)) is not None}
    if not costs:
        log(f"nothing to match on (band {band} rows, {sum(moving)}/{len(moving)} columns moved)")
        return "lost"
    d = min(costs, key=costs.get)
    best, rival = costs[d], min((c for k, c in costs.items() if abs(k - d) > 6), default=255)
    still = mean_diff(prev, cur, lo, lo, band, con)
    log(f"shift {d}: best {best:.2f}, runner-up {rival:.2f}, unmoved {still if still is None else round(still, 2)}; "
        f"rows {lo}-{hi}, {sum(moving)}/{len(moving)} columns moved")
    if still is None or best > 6 or best > 0.6 * still:
        return "lost"
    # Lines of text look alike, so a wrong shift by whole lines can match almost as well: the best must stand out from
    # the best clearly different shift (twice as good), or the frame is skipped: better than joined in the wrong place
    if rival < 2 * best + 0.2:
        return "lost"
    return d, foot, moving


def plain(img):
    """A frame as plain pixels. The first one is cut from the frozen screen, which is tagged with the screen's scale
    (e.g. 1.67): drawn as is, it covered only 60% of the result, and the rest showed leftover memory."""
    img = img.convertToFormat(QImage.Format_RGB32)
    img.setDevicePixelRatio(1)
    return img


class Stitcher:
    """One tall image from frames of an area taken while it scrolls. Keeps only each frame's new rows (and the rows below
    them, in case those turn out to be part of a fixed footer), never whole frames, so long pages stay small in memory."""

    def __init__(self, first):
        self.first = plain(first)
        self.sig, self.parts, self.foot = signature(self.first), [], self.first.height()
        self.moved = [False] * SIG_W  # columns that scrolled at least once
        g = self.sig.convertToFormat(QImage.Format_Grayscale8)
        bpl, data = g.bytesPerLine(), bytes(g.constBits())
        self.inked = [max(data[x::bpl]) - min(data[x::bpl]) > 24 for x in range(SIG_W)]  # columns with anything in them

    def add(self, img):
        """Adds a frame. Returns "added", or why not: "still" (not scrolled), "lost" (scrolled up, too far, or no match)."""
        img = plain(img)
        if img.size() != self.first.size():
            return "lost"
        sig = signature(img)
        moved = scroll_offset(self.sig, sig)
        if isinstance(moved, str):
            return moved
        d, foot, cols = moved
        self.moved = [a or b for a, b in zip(self.moved, cols)]
        self.parts.append((d, img.copy(0, img.height() - foot - d, img.width(), foot + d)))
        self.foot = min(self.foot, foot)  # the real footer is what stayed put at every step
        self.sig = sig
        return "added"

    def height(self):
        return self.first.height() + sum(d for d, _ in self.parts)

    def result(self):
        f = self.foot if self.parts else 0
        w, top = self.first.width(), self.first.height() - f
        out = QImage(w, self.height(), QImage.Format_RGB32)
        out.fill(QColor("white"))  # never show whatever memory it was given, if something isn't drawn
        p = QPainter(out)
        p.drawImage(0, 0, self.first, 0, 0, w, top)
        y = top
        for d, part in self.parts:
            p.drawImage(0, y, part, 0, part.height() - f - d, w, d)  # the frame's new rows, just above the footer
            y += d
        last = self.parts[-1][1] if self.parts else self.first
        p.drawImage(0, y, last, 0, last.height() - f, w, f)
        p.end()
        return out.copy(self.span(w)) if self.parts else out

    def span(self, w):
        """The result without the strips at the sides that never scrolled but show something (a sidebar, a side panel):
        they'd only repeat down the page. Narrow ones (a scrollbar) and blank margins stay."""
        cols = self.moved
        left = next((i for i, m in enumerate(cols) if m), 0)
        right = next((i for i, m in enumerate(reversed(cols)) if m), 0)
        inked = lambda xs: any(self.inked[x] for x in xs)
        left = left if left >= SIG_W // 25 and inked(range(left)) else 0  # at least 4% of the width, and not blank
        right = right if right >= SIG_W // 25 and inked(range(SIG_W - right, SIG_W)) else 0
        x0, x1 = max(0, left - 1) * w // SIG_W, w - max(0, right - 1) * w // SIG_W  # a column short: never cut content
        return QRect(x0, 0, max(1, x1 - x0), self.height())


def stitch(frames):
    s = Stitcher(frames[0])
    for f in frames[1:]:
        s.add(f)
    return s.result()


class ScrollCapture(QWidget):
    """The bar shown during a scrolling capture. Windows/X11 grab the area every 0.4 s while you scroll; on Wayland every
    grab is a screenshot the desktop takes for us, so you scroll, then press Add. Done stitches the parts into one image."""
    # Stop at 25 megapixels (100 MB; about 4 screens tall at 3456 px wide): an endless feed would grow forever, and a
    # row cap alone let a full-width capture on a 2x screen reach ~0.9 GB with the strips it's built from.
    MAX_PIXELS = 25_000_000
    LONGEST = "Scroll back up a little: each step must be less than the area's height"
    matched = Signal(str)  # from the matching thread

    def __init__(self, app, first, grab, area=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle("snapfloat-pin")  # Wayland: the GNOME extension keeps windows with this title above others
        self.app, self.take, self.stitcher, self.busy, self.finishing = app, grab, Stitcher(first), False, False
        self.matched.connect(self.added)
        themed(self, QSS)
        self.setAttribute(Qt.WA_TranslucentBackground)  # only the rounded card shows
        card = QFrame(objectName="card")
        self.fade = QGraphicsOpacityEffect(card, opacity=1.0)  # see tick()
        card.setGraphicsEffect(self.fade)
        QHBoxLayout(self).addWidget(card)
        self.layout().setContentsMargins(0, 0, 0, 0)
        h = QHBoxLayout(card)
        h.setContentsMargins(16, 10, 10, 10)
        h.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.label = QLabel(objectName="title")  # what just happened
        self.hint = QLabel(objectName="hint")  # what to do next
        text.addWidget(self.label)
        text.addWidget(self.hint)
        self.hint.ensurePolished()  # its font comes from the stylesheet; sized for the longest hint, the buttons don't jump
        self.hint.setMinimumWidth(self.hint.fontMetrics().horizontalAdvance(self.LONGEST) + 4)
        h.addLayout(text)
        h.addSpacing(10)
        if WAYLAND:
            h.addWidget(QPushButton("Add", objectName="primary", clicked=self.tick, cursor=Qt.PointingHandCursor))
        h.addWidget(QPushButton("Done", objectName="" if WAYLAND else "primary", clicked=self.done, cursor=Qt.PointingHandCursor))
        h.addWidget(QPushButton("Cancel", clicked=lambda: self.finish(None), cursor=Qt.PointingHandCursor))
        self.say("start")
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

    def say(self, what):
        """Show what the last grab did, and what to do next."""
        n = len(self.stitcher.parts) + 1
        parts = f"{n} part{'s' * (n != 1)}"
        title, hint = {
            "start": (f"Scrolling capture · {parts}", "Scroll down a little, then press Add" if WAYLAND else
                      "Scroll down slowly. Press Done at the end"),
            "added": (f"✓ Added · {parts}", "Scroll a little more, then Add. Done when you reach the end" if WAYLAND else
                      "Keep scrolling slowly. Press Done at the end"),
            "still": (f"Nothing moved · {parts}", "Scroll down a little first, then press Add"),
            "lost": (f"Couldn't join that part · {parts}", self.LONGEST),
            "failed": (f"Couldn't take a screenshot · {parts}", "Try Add again" if WAYLAND else "Keep scrolling slowly"),
        }[what]
        self.label.setText(title)
        self.hint.setText(hint)
        self.adjustSize()

    def tick(self):
        if self.busy:  # the last grab is still on its way (Wayland)
            return
        self.busy = True
        if self.hide_to_grab:
            # Turn see-through rather than hide: GNOME fades a hidden window out over a few hundred ms, so it was still
            # in every screenshot (over the very area being captured, where GNOME puts it) and no part could join.
            self.fade.setOpacity(0.0)
            self.repaint()
            QTimer.singleShot(150, lambda: self.take(self.got))  # let the compositor show the frame without us
        else:
            self.take(self.got)

    def got(self, img):
        if not isValid(self) or not self.timer:  # finished meanwhile
            return
        self.fade.setOpacity(1.0)  # back as soon as the screenshot is taken
        if img is None:
            return self.added("failed")
        # matching takes a few hundred ms: off the UI thread (QImage painting is thread-safe); busy until it's back
        threading.Thread(target=lambda: self.matched.emit(self.stitcher.add(img)), daemon=True).start()

    def added(self, what):
        self.busy = False
        if not isValid(self) or not self.timer:
            return
        if WAYLAND or what != "still":  # grabbing on its own (Windows/X11), "nothing moved" is just between scrolls
            self.say(what)
        if self.finishing or self.stitcher.height() * self.stitcher.first.width() >= self.MAX_PIXELS:
            self.done()

    def done(self):
        if self.busy:  # a frame is still being matched: finish when it's in, never with half of it
            self.finishing = True
            return
        self.finish(QPixmap.fromImage(self.stitcher.result()))

    def finish(self, pix):
        self.timer.stop()
        self.timer = None
        self.close()
        self.app.scroller = None
        self.app.show_shot(pix)
