"""Global shortcuts (Windows hotkeys, GNOME keybindings), the GNOME extension, autostart."""
import os, re, shlex, shutil, subprocess, sys, threading
from pathlib import Path

from PySide6.QtCore import QSettings

from .common import WAYLAND
from .system import APP, BUNDLE, CMD, EXT_UUID, gsettings_list


KEY_NAMES = {  # key name as saved in settings (lowercased) -> (Windows virtual-key code, GNOME/X keysym)
    "print": (0x2C, "Print"), "prtsc": (0x2C, "Print"), "printscreen": (0x2C, "Print"), "space": (0x20, "space"),
    "tab": (0x09, "Tab"), "esc": (0x1B, "Escape"), "ins": (0x2D, "Insert"), "del": (0x2E, "Delete"), "home": (0x24, "Home"),
    "end": (0x23, "End"), "pgup": (0x21, "Page_Up"), "pgdown": (0x22, "Page_Down"), "left": (0x25, "Left"),
    "up": (0x26, "Up"), "right": (0x27, "Right"), "down": (0x28, "Down"), "plus": (0xBB, "plus"),
}


MOD_NAMES = {"ctrl": ("<Control>", 2), "control": ("<Control>", 2), "shift": ("<Shift>", 4), "alt": ("<Alt>", 1),
             "win": ("<Super>", 8), "super": ("<Super>", 8), "meta": ("<Super>", 8)}


def split_keys(seq):
    """'Ctrl+Shift++' -> ['Ctrl', 'Shift', 'Plus']: a trailing '+' is the plus key, not a separator."""
    seq = (seq or "").strip()
    if seq.endswith("+"):
        head = seq[:-1].rstrip("+")
        seq = head + "+Plus" if head else "Plus"
    return [p.strip() for p in seq.split("+") if p.strip()]


def is_fkey(name):
    return len(name) > 1 and name[0] in "fF" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24


class WinHotkeys(threading.Thread):
    """Windows global hotkeys via RegisterHotKey; the message loop must live on the registering thread."""
    MODS = {"alt": 1, "ctrl": 2, "control": 2, "shift": 4, "win": 8, "meta": 8, "super": 8}

    def __init__(self, keys, fire):
        super().__init__(daemon=True)
        self.keys, self.fire, self.tid, self.failed = keys, fire, None, []
        self.ready = threading.Event()

    def parse(self, seq):
        """'Ctrl+Shift+4' -> (modifiers, virtual-key); vk is 0 for a key we can't map."""
        mods, vk = 0x4000, 0  # MOD_NOREPEAT
        for part in split_keys(seq):
            low = part.lower()
            if low in self.MODS:
                mods |= self.MODS[low]
            elif low in KEY_NAMES:
                vk = KEY_NAMES[low][0]
            elif is_fkey(part):
                vk = 0x6F + int(part[1:])
            elif len(part) == 1:
                import ctypes
                vk = ctypes.windll.user32.VkKeyScanW(ord(part)) & 0xFF
        return mods, vk

    def run(self):
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        self.tid = ctypes.windll.kernel32.GetCurrentThreadId()
        try:
            for i, (cmd, seq) in enumerate(self.keys):
                if not seq:
                    continue
                mods, vk = self.parse(seq)
                if not vk or not u.RegisterHotKey(None, i + 1, mods, vk):
                    self.failed.append(seq)
        except Exception:  # a bad saved shortcut must never stop the app from starting
            self.failed.append("(invalid shortcut)")
        finally:
            self.ready.set()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                self.fire(self.keys[msg.wParam - 1][0])
        for i in range(len(self.keys)):
            u.UnregisterHotKey(None, i + 1)

    def stop(self):
        import ctypes
        self.ready.wait()
        ctypes.windll.user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)  # WM_QUIT
        self.join()


def gnome_accel(seq):
    """'Ctrl+Shift+4' -> '<Control><Shift>4', 'Ctrl+PgUp' -> '<Control>Page_Up', 'Ctrl++' -> '<Control>plus'"""
    parts = split_keys(seq)
    if not parts:
        return ""
    key = parts[-1]
    name = KEY_NAMES[key.lower()][1] if key.lower() in KEY_NAMES else key.upper() if is_fkey(key) else key.lower()
    return "".join(MOD_NAMES[p.lower()][0] for p in parts[:-1] if p.lower() in MOD_NAMES) + name


def gnome_bind(keys):
    """Linux has no app-level global hotkeys on Wayland: register GNOME custom shortcuts that call us."""
    if not shutil.which("gsettings") or "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        return False
    schema = "org.gnome.settings-daemon.plugins.media-keys"
    base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
    old = gsettings_list(schema, "custom-keybindings")
    ours, seen = [], {}
    for cmd, seq in keys:
        seen[cmd] = seen.get(cmd, 0) + 1
        path = f"{base}snapfloat-{cmd}{'' if seen[cmd] == 1 else seen[cmd]}/"  # snapfloat-region, snapfloat-region2, ...
        ours.append(path)
        rel = f"{schema}.custom-keybinding:{path}"
        for k, v in (("name", f"{APP} {cmd}"), ("command", shlex.join(CMD + [f"--{cmd}"])), ("binding", gnome_accel(seq))):
            subprocess.run(["gsettings", "set", rel, k, v])
    for path in (p for p in old if "/snapfloat-" in p and p not in ours):  # e.g. PrtSc just turned off
        subprocess.run(["gsettings", "reset-recursively", f"{schema}.custom-keybinding:{path}"])
    subprocess.run(["gsettings", "set", schema, "custom-keybindings", str([p for p in old if "/snapfloat-" not in p] + ours)])
    return True


def ensure_gnome_extension():
    """AppImage users never run install.sh: put the bundled GNOME extension in place (active after the next login)."""
    import filecmp
    src = BUNDLE / "gnome-extension"
    dst = Path.home() / ".local/share/gnome-shell/extensions" / EXT_UUID
    if not (WAYLAND and src.is_dir() and shutil.which("gsettings") and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "")):
        return
    fresh = not dst.exists()
    stale = any(not (dst / f.relative_to(src)).is_file() or not filecmp.cmp(f, dst / f.relative_to(src), shallow=False)
                for f in src.rglob("*") if f.is_file())  # a changed file, or one a newer AppImage added
    if fresh or stale:
        shutil.copytree(src, dst, dirs_exist_ok=True)
    exts = gsettings_list("org.gnome.shell", "enabled-extensions")
    if fresh and EXT_UUID not in exts:  # only on first install: respect a user who turned it off later
        subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(exts + [EXT_UUID])], capture_output=True)


def gnome_ext_problem():
    """None when the GNOME extension is placing thumbnails, else a sentence saying what to do."""
    if not WAYLAND or not shutil.which("gnome-extensions"):
        return None  # ponytail: not GNOME Wayland, nothing to nag about; KDE etc. get default window placement
    info = subprocess.run(["gnome-extensions", "info", EXT_UUID], capture_output=True, text=True).stdout
    if re.search(r"^\s*State:\s*(ACTIVE|ENABLED)\s*$", info, re.M):
        return None
    if "OUT OF DATE" in info:
        return "Your GNOME is newer than the SnapFloat extension supports, so thumbnails open in the middle. Update SnapFloat."
    return "Thumbnails open in the middle until the SnapFloat GNOME extension is active. Log out and back in once after installing."


def desktop_exec(args):
    """Quote a command line for a .desktop Exec= key: double quotes, \\ " ` $ escaped, % doubled."""
    def q(a):
        a = a.replace("%", "%%")
        return a if re.fullmatch(r"[\w./+%-]+", a) else '"' + re.sub(r'(["`$\\])', r"\\\1", a) + '"'
    return " ".join(q(a) for a in args)


def set_autostart(on):
    if os.name == "nt":
        exe = Path(sys.executable).with_name("pythonw.exe")
        cmd = subprocess.list2cmdline([str(exe) if exe.exists() and not getattr(sys, "frozen", False) else sys.executable] + CMD[1:])
        run = QSettings(r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run", QSettings.NativeFormat)
        run.setValue(APP, cmd) if on else run.remove(APP)
    else:
        f = Path.home() / ".config" / "autostart" / "snapfloat.desktop"
        if on:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"[Desktop Entry]\nType=Application\nName={APP}\nExec={desktop_exec(CMD)}\nX-GNOME-Autostart-enabled=true\n")
        else:
            f.unlink(missing_ok=True)
