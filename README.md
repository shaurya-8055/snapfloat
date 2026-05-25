# SnapFloat

**The macOS screenshot thumbnail, for Linux and Windows.** Free and open source.

Take a screenshot and a thumbnail floats in the corner of your screen. Drag it straight into WhatsApp, Slack, Discord, Gmail, ChatGPT, or any other app.
Nothing is saved to your Screenshots folder, so there's no pile of old screenshots to clean up later.

<p align="center"><img src="docs/settings.png" width="420" alt="SnapFloat settings window"></p>

---

## Contents
- [Why](#why)
- [Features](#features)
- [Install](#install)
- [How to use](#how-to-use)
- [Settings](#settings)
- [Privacy: where screenshots go](#privacy-where-screenshots-go)
- [Platform notes](#platform-notes)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Build from source](#build-from-source)
- [Project structure](#project-structure)
- [Contributing](#contributing)
- [Roadmap](#roadmap)
- [License](#license)

## Why
Most screenshots are sent once and never looked at again. On Linux and Windows every one of them is saved as a file, so you end up deleting hundreds by hand.
macOS fixed this years ago with its floating thumbnail. SnapFloat brings the same workflow to Linux and Windows.

## Features
- **Floating thumbnail**, smooth slide-in, in the corner you choose
- **Drag & drop** into any app (it's also copied to the clipboard, so `Ctrl+V` works too)
- **Auto-dismiss** after N seconds, with a countdown bar; hovering pauses it, `0` keeps it until you close it
- **Click** to open, **right-click** for Copy, Save, Save As, Pin, Close
- **Stacks** several shots; the newest sits in the corner, and the rest glide into place
- **Save on timeout** (optional) to a folder of your choice
- **Custom shortcuts**, start at login, tray icon
- **Windows bonus:** optionally show a thumbnail for `Win+Shift+S` / `PrtSc` captures too
- **Lightweight:** one Python file, no telemetry, no network access

## Install

### Windows
1. Download `SnapFloat.exe` from [Releases](https://github.com/shaurya-8055/snapfloat/releases).
2. Run it. It sits in the system tray (click `^` near the clock if you don't see it).
3. Press `Ctrl+Shift+4` and select an area.

> Windows SmartScreen may warn about an unknown publisher because the exe isn't code-signed. Click **More info → Run anyway**, or [build it yourself](#build-from-source).

### Ubuntu / Debian (GNOME)
```sh
git clone https://github.com/shaurya-8055/snapfloat
cd snapfloat
./install.sh
```
Then **log out and back in once**. This activates the small GNOME Shell extension that keeps thumbnails in the corner (see [Platform notes](#platform-notes)).
The installer also registers the shortcuts in *Settings → Keyboard → Custom Shortcuts*. Run `./install.sh` again to update.

### Other Linux desktops (KDE, XFCE, …)
```sh
pip install -r requirements.txt
python3 snapfloat.py
```
Then bind these commands in your desktop's keyboard settings:
| Command | Suggested key |
|---|---|
| `python3 /path/to/snapfloat.py --region` | `Ctrl+Shift+4` |
| `python3 /path/to/snapfloat.py --screen` | `Ctrl+Shift+3` |

### Requirements
- Python 3.9+ (not needed for the Windows exe)
- Linux: an X11 session, or a Wayland session with `xdg-desktop-portal` (default on Ubuntu, Fedora, and most others)

## How to use

### Shortcuts (default; change them in Settings)
| Keys | Action |
|---|---|
| `Ctrl+Shift+4` | Capture a region |
| `Ctrl+Shift+3` | Capture the whole screen |
| `Esc` / right-click | Cancel a region selection (Windows/X11) |

### The thumbnail
| Do this | And this happens |
|---|---|
| Drag it into an app | The screenshot is sent there, and the thumbnail goes away |
| `Ctrl+V` anywhere | Pastes the screenshot (it's already on your clipboard) |
| Click | Opens it in your image viewer |
| Hover | Pauses the countdown |
| Click the **✕** | Closes it (the file is deleted) |
| Right-click | Copy · Open · Save · Save As · Pin · Close |

### Command line
```sh
snapfloat              # start in the tray (or open Settings if it's already running)
snapfloat --region     # capture a region (starts the app if needed)
snapfloat --screen     # capture the whole screen
snapfloat --settings   # open Settings
snapfloat --quit       # quit
```

## Settings
Open from the tray icon → **Settings…**

| Setting | What it does | Default |
|---|---|---|
| Show for | Seconds the thumbnail stays; `0` = until you close it | 6 s |
| Position | Which screen corner | bottom-right |
| Size | Thumbnail size | 220 px |
| Copy to clipboard | Also put every shot on the clipboard | on |
| Save when it disappears | Keep a copy in the save folder after timeout | off |
| Save folder | Where *Save* puts files | `~/Pictures/Screenshots` |
| Capture region / screen | Global shortcuts. Click the field and press keys; Backspace clears it | `Ctrl+Shift+4` / `Ctrl+Shift+3` |
| Start at login | Launch automatically | off |
| Also catch copied images | (Windows/X11) Show a thumbnail for `Win+Shift+S`/`PrtSc` captures | off |

Settings are stored in `~/.config/snapfloat/snapfloat.conf` (Linux) or the registry under `HKCU\Software\snapfloat` (Windows).

## Privacy: where screenshots go
- Screenshots are written to a private temp folder (`/tmp/snapfloat-<user>` or `%TEMP%\snapfloat-<user>`).
- When a thumbnail closes, its file is deleted about 2 minutes later, which gives the app you dropped it into time to read it. All of them are deleted when SnapFloat quits.
- Nothing goes to your Screenshots folder unless you click **Save** or turn on *Save when it disappears*.
- SnapFloat makes no network connections.

## Platform notes

### Linux on Wayland (Ubuntu, Fedora, …)
Wayland is stricter than X11, which is the main reason a tool like this didn't exist before:
- **Capturing:** apps can't read the screen directly, so SnapFloat asks the desktop's own screenshot UI (via `xdg-desktop-portal`). That UI saves into `~/Pictures`, and SnapFloat immediately moves the file out to its temp folder.
- **Placement:** Wayland doesn't let apps position their windows or keep them on top. The bundled GNOME Shell extension (`gnome-extension/`) does that for SnapFloat's thumbnails. It touches only windows titled `snapfloat-thumb …`, and it also stops thumbnails from taking focus away from the app you're typing in. Without it, thumbnails still work but open wherever GNOME places them.
- **Shortcuts:** apps can't grab global keys, so SnapFloat registers GNOME custom shortcuts that run `snapfloat --region` / `--screen`.

**Want the `Print` key?** GNOME Settings → Keyboard → View and Customize Shortcuts → Screenshots: clear *Take a screenshot interactively*. Then set SnapFloat's region shortcut to `Print`.

### Windows
- Shortcuts are real global hotkeys. If one is taken by another app, SnapFloat shows a tray message, so pick another.
- To stop Windows saving its own `Win+Shift+S` captures: Snipping Tool → Settings → turn off *Automatically save screenshots*.

## Troubleshooting
| Problem | Fix |
|---|---|
| Thumbnail appears in the middle of the screen (GNOME) | Log out and back in once after installing. Check with `gnome-extensions info snapfloat@snapfloat.github.io` (should say `ACTIVE`) |
| Shortcut does nothing (GNOME) | Check *Settings → Keyboard → Custom Shortcuts* for "SnapFloat region". Another shortcut may use the same keys |
| No tray icon (GNOME) | Needs the AppIndicator extension (enabled by default on Ubuntu; on Fedora install *AppIndicator and KStatusNotifierItem Support*) |
| Drag doesn't drop into an app | Paste with `Ctrl+V` instead, and [open an issue](https://github.com/shaurya-8055/snapfloat/issues) naming the app |
| `Could not load the Qt platform plugin "xcb"` | `sudo apt install libxcb-cursor0` |
| Windows: "Shortcut already used" | Choose different keys in Settings |

Debug logs: run `snapfloat` from a terminal. For drag & drop details: `QT_LOGGING_RULES="qt.qpa.xdnd.debug=true" snapfloat`.

## Uninstall
- **Linux:** `./install.sh --uninstall`
- **Windows:** quit from the tray, turn off *Start at login* first if you enabled it, then delete `SnapFloat.exe`.

## Build from source
```sh
git clone https://github.com/shaurya-8055/snapfloat && cd snapfloat
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python snapfloat.py
```

**Windows exe:**
```sh
pip install pyinstaller
pyinstaller --onefile --noconsole --name SnapFloat snapfloat.py   # → dist/SnapFloat.exe
```

**Releases:** push a tag like `v1.0.0`. GitHub Actions (`.github/workflows/release.yml`) builds `SnapFloat.exe` and attaches it to the release. You can also run the workflow manually from the Actions tab to get the exe as a build artifact.

## Project structure
```
snapfloat.py              the whole app: capture, thumbnail, settings, hotkeys, tray
gnome-extension/          GNOME Shell extension: corner placement + focus handling on Wayland
install.sh                Linux installer / updater / uninstaller
test_snapfloat.py         smoke test (runs without a display)
.github/workflows/        Windows exe build on release
```

## Contributing
Contributions are welcome: bug reports, fixes, and features from the roadmap.

**Reporting a bug.** Open an [issue](https://github.com/shaurya-8055/snapfloat/issues) with:
- OS and version (e.g. Ubuntu 26.04 / Windows 11), desktop (GNOME/KDE), and session type (`echo $XDG_SESSION_TYPE`)
- What you did, what you expected, and what happened
- Terminal output from running `snapfloat` in a terminal

**Making a change:**
1. Fork, then create a branch: `git checkout -b fix-something`
2. Set up a dev environment as in [Build from source](#build-from-source)
3. Run the smoke test: `python test_snapfloat.py` (it must print `ok`)
4. If you touched the GNOME extension, check its syntax: `node --input-type=module --check < gnome-extension/extension.js`
5. Test it for real on your platform. Say in the PR what you tested on (e.g. "Ubuntu 26.04 GNOME Wayland, Windows 11")
6. Open a pull request with a short description of the why

**Code style**
- Keep it small. SnapFloat is intentionally one file with one dependency (PySide6). Please discuss new dependencies in an issue first.
- Match the surrounding code: PEP 8, ~120-character lines, short comments that explain *why*.
- Everything must keep working on Windows, X11 and Wayland. Gate platform-specific code with `os.name == "nt"` or `WAYLAND`.
- UI work must stay smooth. Draw expensive things once (see `Thumb.render_card`) and only fade or move them in animation frames.

## Roadmap
- [ ] Quick markup on click: arrow, highlight, **blur** for hiding private info
- [ ] Copy text from a screenshot (OCR)
- [ ] AppImage / Flatpak / `.deb` packages
- [ ] KDE Plasma: corner placement via a KWin script
- [ ] Publish the GNOME extension on extensions.gnome.org

## License
[MIT](LICENSE): free to use, modify and share.
