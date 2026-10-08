"""Command line: snapfloat [--region | --screen | --settings | --quit | --uninstall [--quiet] | --self-test[=report.txt]]

Only the standard library is loaded until Qt is needed: a hotkey that just forwards a command to the running copy stays
fast, and uninstall works with the system python."""
import os, sys

from snapfloat.system import APP, COMMANDS, send, uninstall


def main():
    # --self-test[=report.txt]: checks a build works on this machine (CI runs it on the AppImage and the exe)
    report = next((a.partition("=")[2] for a in sys.argv[1:] if a.partition("=")[0] == "--self-test"), None)
    if report is not None:
        if os.name != "nt":  # Windows always has a display, and its offscreen mode finds no fonts (blank text)
            os.environ["QT_QPA_PLATFORM"] = "offscreen"  # no display needed
    elif "--uninstall" in sys.argv:
        uninstall()
        if "--quiet" in sys.argv:  # package managers (Scoop) uninstall without a popup
            pass
        elif os.name == "nt":  # the exe has no console to print to
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "SnapFloat removed. Delete SnapFloat.exe to finish.", APP, 0)
        else:
            print("SnapFloat removed.")
        sys.exit(0)
    arg = next((a[2:] for a in sys.argv[1:] if a[2:] in COMMANDS), None)
    if report is None and (send(arg or "settings") or arg == "quit"):  # already running: just forward the command
        sys.exit(0)
    from snapfloat.app import run
    run(arg, report)


if __name__ == "__main__":
    main()
