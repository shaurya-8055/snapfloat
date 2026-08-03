"""Text recognition (Tesseract), private-info detection, QR codes."""
import os, re, shutil, subprocess, time

from PySide6.QtCore import QCoreApplication, QProcess, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QImage
from shiboken6 import isValid
try:
    import zxingcpp  # reads QR codes; optional when running from source
except ImportError:
    zxingcpp = None

from .common import Config, set_clipboard
from .system import tmp_dir


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


NUMBER = re.compile(r"\+?\d[\d \-().]{2,}\d")


TOKEN = re.compile(r"\b(?:sk-(?:live-|test-|proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
                   r"|xox[abposr]-[A-Za-z0-9-]{10,}|eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,})")  # API keys and tokens


SECRET_WORDS = re.compile(r"\b(?:otp|code|pin|passcode|password|verification|cvv|ssn)\b", re.I)


def is_private_number(m, line=""):
    """Phones, cards, accounts, OTPs (also "482-913", or 4+ digits next to "OTP"/"code"), ID numbers; but not an order
    number next to a year ("#4821 2026"), a date, a time, an IP address, an amount or a plain decimal."""
    s = m.group().strip()
    digits = re.sub(r"\D", "", s)
    if line[m.end():m.end() + 1] == ":" or re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", s) or re.fullmatch(r"\d+[.,]\d+", s):
        return False
    if re.match(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}\b", s) or re.search(r"\d[.,]\d{2}\b", s):
        return False
    if SECRET_WORDS.search(line) and len(digits) >= 4:
        return True
    return (s.startswith("+") or len(digits) >= 10 or re.search(r"\d{6}", s) is not None
            or re.fullmatch(r"\d{3}[- ]\d{3}|\d{3}-\d{2}-\d{4}", s) is not None)


OCR_HINT = ("Install Tesseract OCR to enable this (Ubuntu: sudo apt install tesseract-ocr ·"
            " Windows: winget install UB-Mannheim.TesseractOCR)")


def tesseract():
    """Tesseract's path, including where the Windows installer puts it without adding it to PATH."""
    found = shutil.which("tesseract")
    if not found and os.name == "nt":
        found = shutil.which("tesseract", path=os.path.expandvars(r"%ProgramFiles%\Tesseract-OCR"))
    return found


def ocr_langs():
    """Tesseract's installed languages (its own "osd" helper model isn't one)."""
    out = subprocess.run([tesseract(), "--list-langs"], capture_output=True, text=True).stdout if tesseract() else ""
    return [lang for lang in out.splitlines()[1:] if lang.strip() and lang != "osd"]  # the first line is a heading


def run_ocr(owner, img, tsv, done):
    """Tesseract in a QProcess, so the UI doesn't wait for it. Calls done(output or None, scale), unless `owner` (the
    window that asked) has been closed and deleted meanwhile."""
    exe = tesseract()
    if not exe:
        return done(None, 1)
    scale = 2 if img.width() < 2000 else 1  # small UI text reads far better upscaled
    if scale != 1:
        img = img.scaled(img.width() * scale, img.height() * scale, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    path = tmp_dir() / f"ocr-{time.time_ns()}.bmp"  # BMP: ~20 ms to write on the UI thread, PNG ~250 ms
    img.convertToFormat(QImage.Format_RGB888).save(str(path), "BMP")
    proc = QProcess(QCoreApplication.instance())  # not the owner: it may close (and be deleted) before Tesseract ends
    QTimer.singleShot(60000, proc, proc.kill)  # a stuck Tesseract mustn't leave "Reading text…" up forever
    state = {"done": False}

    def finished(*_):
        if state["done"]:
            return
        state["done"] = True
        path.unlink(missing_ok=True)
        ok = proc.exitStatus() == QProcess.NormalExit and proc.exitCode() == 0
        out = bytes(proc.readAllStandardOutput()).decode(errors="ignore") if ok else None
        proc.deleteLater()
        if owner is None or isValid(owner):
            done(out, scale)
    proc.finished.connect(finished)
    proc.errorOccurred.connect(lambda err: finished() if err == QProcess.FailedToStart else None)
    langs = Config().ocr_langs
    proc.start(exe, [str(path), "stdout"] + (["-l", langs] if langs else []) + (["tsv"] if tsv else []))


def read_qr(img):
    """QR codes and barcodes in an image: copies what they say and returns what to tell the user."""
    codes = [r.text for r in zxingcpp.read_barcodes(img.convertToFormat(QImage.Format_Grayscale8)) if r.text]
    if not codes:
        return "No QR code or barcode found"
    set_clipboard(text="\n".join(codes))
    return f"Copied: {codes[0][:80]}" if len(codes) == 1 else f"Copied {len(codes)} codes"


def private_boxes(tsv, scale):
    """Emails and long numbers (phones, cards, OTPs) in Tesseract's word table, as image rects to blur."""
    lines = {}
    for row in tsv.splitlines()[1:]:
        c = row.split("\t")
        if len(c) == 12 and c[0] == "5" and c[11].strip():
            lines.setdefault(tuple(c[1:5]), []).append((c[11], QRect(int(c[6]), int(c[7]), int(c[8]), int(c[9]))))
    boxes = []
    for words in lines.values():
        text, spans = "", []
        for w, box in words:
            spans.append((len(text), len(text) + len(w), box))
            text += w + " "
        hits = (list(EMAIL.finditer(text)) + list(TOKEN.finditer(text))
                + [m for m in NUMBER.finditer(text) if is_private_number(m, text)])
        for m in hits:
            box = QRect()
            for a, b, wb in spans:
                if a < m.end() and b > m.start():
                    box = box.united(wb)
            boxes.append(QRectF(box.x() / scale, box.y() / scale, box.width() / scale, box.height() / scale).adjusted(-4, -4, 4, 4))
    return boxes


def ocr_message(text):
    """Put recognised text on the clipboard; returns what to tell the user."""
    if text is None:
        return OCR_HINT if not tesseract() else "Couldn't read text from this screenshot"
    text = text.strip()
    if not text:
        return "No text found in this screenshot"
    set_clipboard(text=text)
    return f"Copied {len(text.splitlines())} line{'s' * (len(text.splitlines()) != 1)} of text"
