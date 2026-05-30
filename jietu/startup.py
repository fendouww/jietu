"""Cross-platform auto-start registration (runs jietu-daemon on login)."""
from __future__ import annotations
import sys
from pathlib import Path


APP_NAME = "jietu"
_PLIST_ID = "com.fendouww.jietu"


def _daemon_cmd() -> list[str]:
    """Absolute command that launches the watchdog without a console window."""
    if sys.platform == "win32":
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        exe = str(pythonw) if pythonw.exists() else sys.executable
    else:
        exe = sys.executable
    return [exe, "-m", "jietu.watchdog"]


def is_enabled() -> bool:
    if sys.platform == "win32":
        return _win_is_enabled()
    if sys.platform == "darwin":
        return _mac_plist_path().exists()
    return False


def enable():
    if sys.platform == "win32":
        _win_write(True)
    elif sys.platform == "darwin":
        _mac_write(True)


def disable():
    if sys.platform == "win32":
        _win_write(False)
    elif sys.platform == "darwin":
        _mac_write(False)


# ── Windows ──────────────────────────────────────────────────────────────────

def _win_reg_key():
    import winreg
    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        access=winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
    )


def _win_is_enabled() -> bool:
    import winreg
    try:
        key = _win_reg_key()
        winreg.QueryValueEx(key, APP_NAME)
        return True
    except (FileNotFoundError, OSError):
        return False


def _win_write(on: bool):
    import winreg
    key = _win_reg_key()
    if on:
        cmd_parts = _daemon_cmd()
        # Quote parts that contain spaces
        cmd_str = " ".join(f'"{p}"' if " " in p else p for p in cmd_parts)
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd_str)
    else:
        try:
            winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass


# ── macOS ─────────────────────────────────────────────────────────────────────

def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_ID}.plist"


def _mac_write(on: bool):
    import subprocess
    plist = _mac_plist_path()
    if on:
        args_xml = "\n        ".join(
            f"<string>{c}</string>" for c in _daemon_cmd()
        )
        plist.write_text(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_PLIST_ID}</string>
    <key>ProgramArguments</key>
    <array>
        {args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
""")
        subprocess.run(["launchctl", "load", str(plist)], check=False)
    else:
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], check=False)
            plist.unlink(missing_ok=True)
