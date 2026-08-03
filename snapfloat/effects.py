"""Whole-image effects: Beautify and Combine."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap



def flat(pix):
    img = pix.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(1)
    return img


def beautify(pix):
    """Social-ready: padded gradient background, rounded corners, soft shadow."""
    img = flat(pix)
    w, h = img.width(), img.height()
    pad = max(48, int(max(w, h) * 0.06))
    rad = max(10, pad // 4)
    out = QImage(w + 2 * pad, h + 2 * pad, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, out.width(), out.height())
    g.setColorAt(0, QColor("#7b5cff"))
    g.setColorAt(1, QColor("#2fb4ff"))
    p.fillRect(out.rect(), g)
    r = QRectF(pad, pad, w, h)
    p.setPen(Qt.NoPen)
    for i in range(pad // 2, 0, -max(1, pad // 12)):
        p.setBrush(QColor(0, 0, 0, 10))
        p.drawRoundedRect(r.adjusted(-i, -i + pad / 8, i, i + pad / 8), rad + i, rad + i)
    clip = QPainterPath()
    clip.addRoundedRect(r, rad, rad)
    p.setClipPath(clip)
    p.drawImage(r, img)
    p.end()
    return QPixmap.fromImage(out)


def combine(pixes, gap=16):
    """Stack screenshots into one image (oldest on top): one picture to send instead of five."""
    top = max(px.devicePixelRatio() for px in pixes)
    imgs = []
    for px in pixes:  # a 1x shot next to a 2x one: scale it up so both read at the same size
        i, d = flat(px), px.devicePixelRatio()
        imgs.append(i if d >= top else i.scaled(round(i.width() * top / d), round(i.height() * top / d),
                                                Qt.KeepAspectRatio, Qt.SmoothTransformation))
    out = QImage(max(i.width() for i in imgs), sum(i.height() for i in imgs) + gap * (len(imgs) - 1), QImage.Format_RGB32)
    out.fill(QColor("white"))
    p = QPainter(out)
    y = 0
    for i in imgs:
        p.drawImage((out.width() - i.width()) // 2, y, i)
        y += i.height() + gap
    p.end()
    return QPixmap.fromImage(out)
