"""Markup shapes: painting them into a screenshot, hit-testing, the toolbar icons."""
import math, random

from PySide6.QtCore import QLineF, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QIcon, QImage, QPainter, QPainterPath, QPainterPathStroker,
                           QPen, QPixmap, QPolygonF)



TOOLS = [("select", "Select: move a mark, Delete removes it", "V"), ("arrow", "Arrow", "A"), ("rect", "Box", "R"),
         ("ellipse", "Ellipse", "E"), ("pen", "Pen", "P"), ("highlight", "Highlighter", "H"), ("text", "Text", "T"),
         ("step", "Numbered steps", "N"), ("magnify", "Magnifier: drag from the centre outwards", "M"),
         ("spotlight", "Spotlight: darken everything else", "S"), ("blur", "Blur: hide private info", "B"), ("crop", "Crop", "C"),
         ("picker", "Pick a colour from the screenshot (copies its hex)", "I"),
         ("ocr", "Copy text from an area: drag a box over it", "X")]


COLORS = ["#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#ffffff", "#000000"]


TEXT_PX = 22  # on-screen text size while typing; stored in image pixels so it looks the same after export


SCALES = (0.5, 0.75, 1, 1.5, 2, 3)  # [ and ] step through these mark sizes


def pixelate(img, r):
    """Redaction that can't be undone: the area's average colour as a mosaic of random shades.

    A mosaic made from the real pixels keeps enough of each glyph to guess the digits back (tested), so nothing of the
    content survives here except its average colour; the shading is random, seeded by the box so repaints don't flicker."""
    avg = img.copy(r).scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).pixelColor(0, 0)
    block = max(8, min(r.width(), r.height()) // 3)
    cols, rows = r.width() // block + 1, r.height() // block + 1
    rnd = random.Random(f"{r.x()},{r.y()},{r.width()},{r.height()}")
    tile = QImage(cols, rows, QImage.Format_ARGB32_Premultiplied)
    for x in range(cols):
        for y in range(rows):
            tile.setPixelColor(x, y, avg.lighter(rnd.randint(90, 112)))
    return tile.scaled(cols * block, rows * block, Qt.IgnoreAspectRatio, Qt.FastTransformation).copy(0, 0, r.width(), r.height())


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


def text_font(size):
    f = QFont()
    f.setPixelSize(max(1, round(size)))
    f.setBold(True)
    return f


def draw_text(p, at, text, c, size):
    """Bold, outlined text; `at` is the first line's baseline. Several lines are stacked by the font's line spacing."""
    f = text_font(size)
    step = QFontMetricsF(f).lineSpacing()
    path = QPainterPath()
    for i, line in enumerate(text.split("\n")):
        path.addText(QPointF(at.x(), at.y() + i * step), f, line)
    outline = QColor(0, 0, 0, 170) if c.lightness() > 100 else QColor(255, 255, 255, 210)
    p.strokePath(path, QPen(outline, size / 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(path, c)


def paint_blurs(p, img, shapes):
    """Redaction boxes, each pixelated once and cached on its shape."""
    for s in shapes:
        if s["t"] != "blur":
            continue
        for rr in s.get("rs") or [s["r"]]:  # "Hide private info" stores all its boxes in one shape: one undo
            r = rr.toRect().intersected(img.rect())
            if r.width() > 1 and r.height() > 1:
                key = (r.x(), r.y(), r.width(), r.height())
                cache = s.setdefault("_px", {})
                if key not in cache:
                    if len(cache) > 64:  # a box still being dragged out creates a new key per mouse move
                        cache.clear()
                    cache[key] = pixelate(img, r)
                p.drawImage(r.topLeft(), cache[key])


def callout_rect(s):
    """A magnifier shows its source circle at 2x in a circle twice as wide, centred on s["at"]."""
    rad = max(s["r"].width(), s["r"].height())
    return QRectF(s["at"].x() - rad, s["at"].y() - rad, 2 * rad, 2 * rad)


def place_callout(src, img):
    """Default spot for a magnifier's callout: up and to the right of the source, kept inside the image."""
    rad = max(src.width(), src.height())
    c = src.center() + QPointF(rad * 1.6, -rad * 1.6)
    if c.x() + rad > img.width():
        c.setX(src.center().x() - rad * 1.6)
    if c.y() - rad < 0:
        c.setY(src.center().y() + rad * 1.6)
    return c


def paint_magnifier(p, img, shapes, s):
    src, out, col = s["r"], callout_rect(s), QColor(s["c"])
    if src.width() < 2:
        return
    p.setPen(QPen(col, s["w"]))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(src)
    line = QLineF(src.center(), out.center())
    if line.length() > src.width() / 2 + out.width() / 2:  # connector between the two rings
        u = line.unitVector()
        d = QPointF(u.dx(), u.dy())
        p.drawLine(src.center() + d * (src.width() / 2), out.center() - d * (out.width() / 2))
    clip = QPainterPath()
    clip.addEllipse(out)
    p.save()
    p.setClipPath(clip, Qt.IntersectClip)
    p.fillPath(clip, QColor("white"))
    p.translate(out.center())
    p.scale(2, 2)
    p.translate(-src.center())
    d = max(src.width(), src.height())
    sq = QRectF(src.center() - QPointF(d / 2, d / 2), QSizeF(d, d))
    p.drawImage(sq, img, sq)  # only the part that shows: cheap even on a 5K shot
    paint_blurs(p, img, shapes)  # a magnifier must never reveal what a blur hides
    p.restore()
    p.setPen(QPen(QColor("white"), s["w"] * 2.2))
    p.drawEllipse(out)
    p.setPen(QPen(col, s["w"]))
    p.drawEllipse(out)


def paint_shapes(p, img, shapes):
    """Markup in image pixels. Blurs first (they sample the clean screenshot), then the spotlight dimming, magnifiers,
    and the marks, which stay crisp on top."""
    paint_blurs(p, img, shapes)
    spots = [s for s in shapes if s["t"] == "spotlight"]
    if spots:  # one dimming pass with a hole per spotlight
        dim, holes = QPainterPath(), QPainterPath()
        dim.addRect(QRectF(img.rect()))
        holes.setFillRule(Qt.WindingFill)  # overlapping spotlights stay bright where they overlap
        for sp in spots:
            holes.addRoundedRect(sp["r"], sp["w"] * 2.5, sp["w"] * 2.5)
        p.fillPath(dim.subtracted(holes), QColor(0, 0, 0, 150))
    for s in shapes:
        if s["t"] == "magnify":
            paint_magnifier(p, img, shapes, s)
    for s in shapes:
        t, c = s["t"], QColor(s["c"])
        if t == "rect":
            p.setPen(QPen(c, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(s["r"], s["w"] * 1.5, s["w"] * 1.5)
        elif t == "ellipse":
            p.setPen(QPen(c, s["w"]))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(s["r"])
        elif t == "pen":
            p.setPen(QPen(c, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(s["path"])
        elif t == "arrow":
            draw_arrow(p, s["a"], s["b"], c, s["w"])
        elif t == "highlight":
            c.setAlphaF(0.38)
            p.setPen(QPen(c, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(s["path"])
        elif t == "text":
            draw_text(p, s["at"], s["text"], c, s["size"])
        elif t == "step":
            rad = s["size"]
            p.setPen(QPen(QColor(255, 255, 255, 235), rad * 0.14))
            p.setBrush(c)
            p.drawEllipse(s["at"], rad, rad)
            p.setFont(text_font(rad * 1.1))
            p.setPen(QColor("black") if c.lightness() > 160 else QColor("white"))
            p.drawText(QRectF(s["at"].x() - rad, s["at"].y() - rad, 2 * rad, 2 * rad), Qt.AlignCenter, str(s["n"]))


def shape_bounds(s):
    """A mark's extent in image pixels (for selecting it and drawing its selection frame)."""
    t, w = s["t"], s.get("w", 0)
    if t == "blur":
        b = QRectF()
        for r in s.get("rs") or [s["r"]]:
            b = b.united(r)
    elif t == "arrow":
        b = QRectF(s["a"], s["b"]).normalized().adjusted(-w * 4, -w * 4, w * 4, w * 4)
    elif t in ("pen", "highlight"):
        b = s["path"].boundingRect()
    elif t == "text":
        fm = QFontMetricsF(text_font(s["size"]))
        lines = s["text"].split("\n")
        width = max(fm.horizontalAdvance(line) for line in lines)
        b = QRectF(s["at"].x(), s["at"].y() - fm.ascent(), width, fm.lineSpacing() * (len(lines) - 1) + fm.height())
    elif t == "step":
        b = QRectF(s["at"].x() - s["size"], s["at"].y() - s["size"], 2 * s["size"], 2 * s["size"])
    elif t == "magnify":
        b = s["r"].united(callout_rect(s))
    else:
        b = QRectF(s["r"])
    return b.adjusted(-w / 2, -w / 2, w / 2, w / 2)


def shape_hit(s, pt, tol):
    """Is `pt` (image pixels) on this mark? Strokes are hit along their line, areas anywhere inside."""
    t = s["t"]
    if t == "arrow":
        a, b = s["a"], s["b"]
        ab = b - a
        n = QPointF.dotProduct(ab, ab)
        k = 0 if n == 0 else max(0.0, min(1.0, QPointF.dotProduct(pt - a, ab) / n))
        return QLineF(pt, a + ab * k).length() <= tol + s["w"] * 2
    if t in ("pen", "highlight"):
        stroker = QPainterPathStroker()
        stroker.setWidth(s["w"] + 2 * tol)
        return stroker.createStroke(s["path"]).contains(pt)
    if t == "magnify":
        src, out = QPainterPath(), QPainterPath()
        src.addEllipse(s["r"].adjusted(-tol, -tol, tol, tol))
        out.addEllipse(callout_rect(s))
        return src.contains(pt) or out.contains(pt)
    if t == "blur" and s.get("rs"):
        return any(r.adjusted(-tol, -tol, tol, tol).contains(pt) for r in s["rs"])
    return shape_bounds(s).adjusted(-tol, -tol, tol, tol).contains(pt)


def shape_moved(s, d):
    """A copy of the mark shifted by d (shapes are never changed in place, so undo can keep the old ones)."""
    n = {k: v for k, v in s.items() if k != "_px"}
    if "r" in n:
        n["r"] = n["r"].translated(d)
    if "rs" in n:
        n["rs"] = [r.translated(d) for r in n["rs"]]
    for k in ("a", "b", "at"):
        if k in n:
            n[k] = n[k] + d
    if "path" in n:
        n["path"] = n["path"].translated(d)
    return n


def shape_scaled(s, f):
    """A copy with its stroke width / text size scaled by f (for [ and ] on a selected mark)."""
    n = {k: v for k, v in s.items() if k != "_px"}
    for k in ("w", "size"):
        if k in n:
            n[k] = n[k] * f
    return n


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
    elif name == "ellipse":
        p.drawEllipse(QRectF(3, 6, 16, 10))
    elif name == "pen":
        squiggle = QPainterPath(QPointF(3.5, 15))
        squiggle.cubicTo(QPointF(7, 3), QPointF(11, 21), QPointF(18.5, 7))
        p.drawPath(squiggle)
    elif name == "spotlight":  # dark surround, clear window in the middle
        shade = QPainterPath()
        shade.addRoundedRect(QRectF(2, 3, 18, 16), 3, 3)
        hole = QPainterPath()
        hole.addRoundedRect(QRectF(7, 7.5, 8, 7), 2, 2)
        q = QColor(c)
        q.setAlphaF(0.55)
        p.fillPath(shade.subtracted(hole), q)
        p.drawPath(hole)
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
    elif name == "step":
        p.drawEllipse(QRectF(3, 3, 16, 16))
        f = QFont()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(3, 3, 16, 16), Qt.AlignCenter, "1")
    elif name == "private":  # an eye, crossed out
        p.drawEllipse(QRectF(3, 7, 16, 8))
        p.drawEllipse(QRectF(9, 9, 4, 4))
        p.drawLine(QPointF(4, 18), QPointF(18, 4))
    elif name == "ocr":  # a dashed selection box around a "T"
        p.setPen(QPen(c, 1.5, Qt.DashLine))
        p.drawRect(QRectF(2.5, 4.5, 17, 13))
        p.setPen(c)
        f = QFont()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(2.5, 4.5, 17, 13), Qt.AlignCenter, "T")
    elif name == "copytext":
        p.drawRoundedRect(QRectF(7, 3, 12, 14), 2, 2)
        p.drawLine(QPointF(10, 7.5), QPointF(16, 7.5))
        p.drawLine(QPointF(10, 11), QPointF(16, 11))
        p.drawPolyline(QPolygonF([QPointF(4, 7), QPointF(4, 19), QPointF(14, 19)]))
    elif name == "select":  # a mouse pointer
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(6, 3), QPointF(6, 17.5), QPointF(9.5, 14), QPointF(12, 19.5), QPointF(14, 18.5),
                                 QPointF(11.5, 13), QPointF(16.5, 13)]))
    elif name == "magnify":
        p.drawEllipse(QRectF(3, 3, 12, 12))
        p.setPen(QPen(c, 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(14, 14), QPointF(19, 19))
    elif name == "picker":  # an eyedropper
        p.drawLine(QPointF(4, 18), QPointF(13, 9))
        p.setPen(QPen(c, 4.5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(13.5, 8.5), QPointF(17, 5))
        p.setPen(QPen(c, 1.8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(11, 6.5), QPointF(15.5, 11))
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
