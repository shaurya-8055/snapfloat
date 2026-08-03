"""Paths, the running-instance socket, uninstall. Standard library only: hotkeys and uninstall run without Qt."""
import getpass, os, shutil, socket, stat, subprocess, sys, tempfile, time
from pathlib import Path



APP = "SnapFloat"


AUTHOR = "Shaurya Shakya"


HOMEPAGE = "https://github.com/shaurya-8055/snapfloat"


COMMANDS = ("region", "screen", "settings", "quit")


EXT_UUID = "snapfloat@shaurya-8055.github.io"


LEGACY_UUID = "snapfloat@snapfloat.github.io"  # 1.0 used this ID; uninstall cleans it up too


if os.name == "nt":
    SERVER = f"snapfloat-{getpass.getuser()}"
    ADDR = r"\\.\pipe" + "\\" + SERVER
else:
    SERVER = ADDR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), f"snapfloat-{os.getuid()}.sock")


# Screenshots live in a per-user runtime dir on Linux (never shared with other users); %TEMP% is per-user on Windows.
_RUNTIME = os.environ.get("XDG_RUNTIME_DIR")


TMP = (Path(_RUNTIME) / "snapfloat" if os.name != "nt" and _RUNTIME and os.path.isdir(_RUNTIME)
       else Path(tempfile.gettempdir()) / f"snapfloat-{getpass.getuser()}")


LEGACY_TMP = Path(tempfile.gettempdir()) / f"snapfloat-{getpass.getuser()}"  # where 1.0/1.1 kept them


PRINT_MARK = (Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
              / "snapfloat" / "took-printscreen")  # what the PrtSc keys were before SnapFloat took them


if os.environ.get("APPIMAGE"):  # inside an AppImage, sys.executable sits in a mount whose path changes every launch
    CMD = [os.environ["APPIMAGE"]]
elif getattr(sys, "frozen", False):
    CMD = [sys.executable]
else:
    CMD = [sys.executable, "-m", "snapfloat"]


BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # where bundled files (the GNOME extension) live


if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
    # PyInstaller points LD_LIBRARY_PATH at its bundled libs. Child processes (gsettings, gnome-extensions, gdbus, tesseract,
    # wl-copy) would load those old copies and break silently, so give them the system's libraries back.
    if "LD_LIBRARY_PATH_ORIG" in os.environ:
        os.environ["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH_ORIG"]
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


def send(cmd):
    """Hand a command to the running instance. Stdlib only, so hotkeys stay fast."""
    try:
        if os.name == "nt":
            with open(ADDR, "wb") as f:
                f.write(cmd.encode())
        else:
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(ADDR)
                s.sendall(cmd.encode())
        return True
    except OSError:
        return False


def gsettings_list(schema, key):
    out = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True).stdout
    return [p.strip(" '") for p in out.replace("@as", "").strip(" []\n").split(",") if p.strip(" '")]


GNOME_SHOT_KEYS = ("show-screenshot-ui", "screenshot")  # GNOME's PrtSc and Shift+PrtSc; Alt+PrtSc (window) stays GNOME's


def free_print_key(on):
    """Let SnapFloat have the Print Screen key, or hand back exactly what the system had before.

    A marker file remembers the previous values, so a setup SnapFloat never touched is never changed."""
    if on == PRINT_MARK.exists():
        return
    if os.name == "nt":
        import winreg  # Windows 11 sends PrtSc to Snipping Tool unless this is 0
        name = "PrintScreenKeyForSnippingEnabled"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Keyboard") as k:
            if on:
                try:
                    old = str(winreg.QueryValueEx(k, name)[0])
                except OSError:
                    old = "absent"
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 0)
            else:
                old = PRINT_MARK.read_text().strip()
                if old.isdigit():
                    winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(old))
                else:
                    try:
                        winreg.DeleteValue(k, name)
                    except OSError:
                        pass
    elif shutil.which("gsettings"):
        if on and "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
            return
        schema = "org.gnome.shell.keybindings"
        if on:
            old = "\n".join(subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True).stdout.strip()
                            for key in GNOME_SHOT_KEYS)
            for key in GNOME_SHOT_KEYS:
                subprocess.run(["gsettings", "set", schema, key, "[]"], capture_output=True)
        else:
            for key, val in zip(GNOME_SHOT_KEYS, PRINT_MARK.read_text().splitlines() + [""] * len(GNOME_SHOT_KEYS)):
                if val.strip("@as []"):
                    subprocess.run(["gsettings", "set", schema, key, val], capture_output=True)
                else:  # empty: an older SnapFloat had already cleared it, so GNOME's default is the right thing to restore
                    subprocess.run(["gsettings", "reset", schema, key], capture_output=True)
    else:
        return
    if on:
        PRINT_MARK.parent.mkdir(parents=True, exist_ok=True)
        PRINT_MARK.write_text(old)
    else:
        PRINT_MARK.unlink(missing_ok=True)


def uninstall(quit_running=True):
    """Undo everything SnapFloat and install.sh added: shortcuts, autostart, settings, temp shots, the install itself."""
    if quit_running and send("quit"):
        for _ in range(30):
            time.sleep(0.1)
            if not send("ping"):
                break
    home = Path.home()
    free_print_key(False)
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, APP)  # otherwise Windows keeps launching a deleted exe at every login
        except OSError:
            pass
        for key in (r"Software\snapfloat\snapfloat", r"Software\snapfloat"):  # settings (QSettings' registry home)
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass
        shutil.rmtree(PRINT_MARK.parent, ignore_errors=True)
    else:
        if shutil.which("gsettings"):
            keys = "org.gnome.settings-daemon.plugins.media-keys"
            paths = gsettings_list(keys, "custom-keybindings")
            for p in (p for p in paths if "/snapfloat-" in p):
                subprocess.run(["gsettings", "reset-recursively", f"{keys}.custom-keybinding:{p}"], capture_output=True)
            subprocess.run(["gsettings", "set", keys, "custom-keybindings", str([p for p in paths if "/snapfloat-" not in p])],
                           capture_output=True)  # drop them from the list too, or GNOME shows blank shortcuts
            exts = gsettings_list("org.gnome.shell", "enabled-extensions")
            if EXT_UUID in exts or LEGACY_UUID in exts:
                subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions",
                                str([e for e in exts if e not in (EXT_UUID, LEGACY_UUID)])], capture_output=True)
        if shutil.which("gdbus"):
            subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.impl.portal.PermissionStore",
                            "--object-path", "/org/freedesktop/impl/portal/PermissionStore", "--method",
                            "org.freedesktop.impl.portal.PermissionStore.DeletePermission", "screenshot", "screenshot", "snapfloat"],
                           capture_output=True)
        for f in (".config/autostart/snapfloat.desktop", ".local/bin/snapfloat", ".local/share/applications/snapfloat.desktop",
                  ".local/share/icons/hicolor/256x256/apps/snapfloat.png"):
            (home / f).unlink(missing_ok=True)
        for d in (".config/snapfloat", f".local/share/gnome-shell/extensions/{EXT_UUID}",
                  f".local/share/gnome-shell/extensions/{LEGACY_UUID}", ".local/share/snapfloat"):
            shutil.rmtree(home / d, ignore_errors=True)
    for d in (TMP, LEGACY_TMP):
        if d.is_dir() and not d.is_symlink():
            shutil.rmtree(d, ignore_errors=True)


def tmp_dir():
    """Screenshots can be private: keep our folder owner-only, and never trust one somebody else created."""
    global TMP
    if os.name != "nt":
        try:
            TMP.mkdir(mode=0o700, exist_ok=True)
            st = os.lstat(TMP)
            ours = stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()
        except OSError:
            ours = False
        if not ours:  # a symlink or someone else's folder at our name: use a fresh private one instead
            TMP = Path(tempfile.mkdtemp(prefix="snapfloat-"))
        TMP.chmod(0o700)
    else:
        TMP.mkdir(exist_ok=True)
    return TMP


def new_path():
    return tmp_dir() / time.strftime(f"Screenshot %Y-%m-%d at %H.%M.%S.{int(time.time() * 1000) % 1000:03d}.png")


def forget(path):
    for f in (path, path.with_suffix(".jpg"), path.with_suffix(".png")):  # .png: a recording's thumbnail
        f.unlink(missing_ok=True)


def purge():
    if TMP.is_dir() and not TMP.is_symlink():
        for f in TMP.iterdir():
            if f.is_file() or f.is_symlink():
                f.unlink(missing_ok=True)
