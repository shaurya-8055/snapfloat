"""Screen recording: the ScreenCast portal + GStreamer on Wayland, ffmpeg on Windows/X11."""
import os, shutil, signal, subprocess, threading, time
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap, QPolygonF
try:
    import jeepney  # Wayland screen recording talks to the ScreenCast portal with it; optional when running from source
except ImportError:
    jeepney = None

from .common import WAYLAND
from .system import tmp_dir


VIDEO = (".mp4", ".webm")


def record_missing():
    """What has to be installed before recording works here, or "" if nothing."""
    if not WAYLAND:
        return "" if shutil.which("ffmpeg") else "ffmpeg"
    if jeepney is None:
        return "jeepney"
    gst = shutil.which("gst-inspect-1.0")
    return "" if gst and subprocess.run([gst, "--exists", "pipewiresrc"]).returncode == 0 else "gstreamer1.0-pipewire"


def gst_encoder():
    """H.264 in MP4 (plays everywhere) with openh264 from plugins-bad, else VP8 in WebM (always in plugins-good).
    Not x264enc: it fails on a window with an odd width or height, which openh264 crops to even."""
    for name, tail, ext in (("openh264enc", "video/x-raw,format=I420 ! openh264enc complexity=low ! h264parse ! mp4mux", ".mp4"),
                            ("vp8enc", "vp8enc deadline=1 cpu-used=8 ! webmmux", ".webm")):
        if subprocess.run(["gst-inspect-1.0", "--exists", name]).returncode == 0:
            return tail.split(), ext
    raise RuntimeError("GStreamer has no video encoder (install gstreamer1.0-plugins-good)")


FPS = 24  # plenty for screen recordings, and a fifth less work than 30


def record_cmd(stem, fd=None, node=None):
    """The recorder command and the file it writes. Wayland: GStreamer on the portal's PipeWire stream; else ffmpeg.

    Video is recorded at the screen's logical size (what you see; half the pixels each way on a 2x screen): encoding
    every physical pixel took more than a whole CPU core on a 3456x1944 screen and still fell behind; this takes a third."""
    scr = max(QGuiApplication.screens(), key=lambda s: s.size().width() * s.size().height())
    if WAYLAND:
        tail, ext = gst_encoder()
        path = stem.with_suffix(ext)
        fit = f"video/x-raw,width=[2,{scr.size().width()}],height=[2,{scr.size().height()}],pixel-aspect-ratio=1/1"
        # -e: Ctrl+C (SIGINT) ends the stream cleanly, so the file is finalized; each argv item is escaped by gst-launch.
        # Shrink (keeping the shape) before converting colours: both cost per pixel.
        return (["gst-launch-1.0", "-e", "-q", "pipewiresrc", f"fd={fd}", f"path={node}", "do-timestamp=true", "!",
                 "videorate", "drop-only=true", f"max-rate={FPS}", "!", "videoscale", "!", fit, "!",
                 "videoconvert", "!", "queue", "!"] + tail + ["!", "filesink", f"location={path}"], path)
    path = stem.with_suffix(".mp4")
    d = scr.devicePixelRatio()
    grab = ["-f", "gdigrab", "-i", "desktop"] if os.name == "nt" else ["-f", "x11grab", "-i", os.environ.get("DISPLAY", ":0")]
    return ([shutil.which("ffmpeg") or "ffmpeg", "-y", "-loglevel", "error", "-nostats", "-framerate", str(FPS)] + grab +
            ["-vf", f"scale=trunc(iw/{d:g}/2)*2:trunc(ih/{d:g}/2)*2", "-c:v", "libx264", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", str(path)], path)


def screencast_portal():
    """Wayland: the desktop's picker asks which screen or window to share. Returns (connection, fd, PipeWire node).
    The session lasts as long as the connection; raises if the user cancels. Blocking: run it off the UI thread.
    (QtDBus can't send the portal's uint32 options from Python, hence jeepney.)"""
    from jeepney import DBusAddress, MatchRule, new_method_call
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection
    conn = open_dbus_connection(bus="SESSION", enable_fds=True)
    portal = DBusAddress("/org/freedesktop/portal/desktop", "org.freedesktop.portal.Desktop", "org.freedesktop.portal.ScreenCast")
    try:
        def ask(method, sig, *args, **options):
            token = f"snapfloat{time.time_ns()}"
            rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request", member="Response",
                             path=f"/org/freedesktop/portal/desktop/request/{conn.unique_name[1:].replace('.', '_')}/{token}")
            conn.send_and_get_reply(message_bus.AddMatch(rule))
            with conn.filter(rule) as responses:
                opts = {"handle_token": ("s", token), **options}
                conn.send_and_get_reply(new_method_call(portal, method, sig + "a{sv}", (*args, opts)))
                code, results = conn.recv_until_filtered(responses).body
            if code != 0:
                raise RuntimeError("cancelled" if code == 1 else "the desktop refused to share the screen")
            return results
        session = ask("CreateSession", "", session_handle_token=("s", f"snapfloat{time.time_ns()}"))["session_handle"][1]
        ask("SelectSources", "o", session, types=("u", 1 | 2), cursor_mode=("u", 2))  # a monitor or a window; cursor drawn in
        node = ask("Start", "os", session, "")["streams"][1][0][0]
        fd = conn.send_and_get_reply(new_method_call(portal, "OpenPipeWireRemote", "oa{sv}", (session, {}))).body[0]
        return conn, fd.to_raw_fd(), node
    except BaseException:
        conn.close()
        raise


class Recorder(QObject):
    """One screen recording at a time. finished(path) gets the video, or "" if nothing was recorded."""
    finished = Signal(str)
    error = Signal(str)
    _go = Signal(object)  # from the portal thread
    _ended = Signal()  # from the thread waiting on the recorder

    def __init__(self):
        super().__init__()
        self.proc = self.conn = None
        self.busy = False
        self._go.connect(self.launch)
        self._ended.connect(self.ended)

    def start(self):
        self.busy = True
        if WAYLAND:
            threading.Thread(target=lambda: self._go.emit(self.portal()), daemon=True).start()
        else:
            self.launch((None, None, None))

    @staticmethod
    def portal():
        try:
            return screencast_portal()
        except Exception as e:
            return e

    def launch(self, src):
        fd = None
        try:
            if isinstance(src, Exception):
                raise src
            self.conn, fd, node = src
            stem = tmp_dir() / time.strftime(f"Recording %Y-%m-%d at %H.%M.%S.{int(time.time() * 1000) % 1000:03d}")
            cmd, self.path = record_cmd(stem, fd, node)
            self.log = open(stem.with_suffix(".log"), "w+b")
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log,
                                         pass_fds=(fd,) if fd is not None else (),
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.cleanup()
            if str(e) != "cancelled":
                self.error.emit(str(e))
            return self.finished.emit("")
        finally:
            if fd is not None:
                os.close(fd)  # the recorder has its own copy
        threading.Thread(target=lambda: (self.proc.wait(), self._ended.emit()), daemon=True).start()

    def stop(self):
        if not self.proc or self.proc.poll() is not None:
            return
        if WAYLAND:
            self.proc.send_signal(signal.SIGINT)  # gst-launch -e: finish the file, then exit
        else:
            try:
                self.proc.stdin.write(b"q")  # ffmpeg's own "stop"
                self.proc.stdin.flush()
            except OSError:
                self.proc.terminate()

    def ended(self):
        ok = self.proc.returncode == 0 and self.path.exists() and self.path.stat().st_size > 0
        if not ok:
            self.log.seek(0)
            why = self.log.read().decode(errors="ignore").strip().splitlines()
            self.error.emit(why[-1] if why else f"the recorder stopped (exit code {self.proc.returncode})")
            self.path.unlink(missing_ok=True)
        self.cleanup()
        self.finished.emit(str(self.path) if ok else "")

    def cleanup(self):
        if self.conn:
            self.conn.close()  # ends the portal session (and GNOME's "sharing the screen" indicator)
        if getattr(self, "log", None):
            self.log.close()
            Path(self.log.name).unlink(missing_ok=True)
        self.proc = self.conn = self.log = None
        self.busy = False


def poster(video):
    """A recording's thumbnail: its first frame (saved beside it, for Recent) with a play badge."""
    png = video.with_suffix(".png")
    cmd = (["gst-launch-1.0", "-q", "filesrc", f"location={video}", "!", "decodebin", "!", "videoconvert", "!", "pngenc",
            "snapshot=true", "!", "filesink", f"location={png}"] if WAYLAND else
           [shutil.which("ffmpeg") or "ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-frames:v", "1", str(png)])
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        pass
    pix = QPixmap(str(png))
    if pix.isNull():
        pix = QPixmap(640, 360)
        pix.fill(QColor("#1c1c1e"))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    rad = min(pix.width(), pix.height()) / 6
    c = QPointF(pix.width() / 2, pix.height() / 2)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 150))
    p.drawEllipse(c, rad, rad)
    p.setBrush(QColor("white"))
    p.drawPolygon(QPolygonF([c + QPointF(-rad * 0.35, -rad * 0.5), c + QPointF(-rad * 0.35, rad * 0.5), c + QPointF(rad * 0.55, 0)]))
    p.end()
    pix.save(str(png), "PNG")
    return pix
