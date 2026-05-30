"""Auto-update: check GitHub for newer version and upgrade via pip."""
from __future__ import annotations
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

REPO = "fendouww/jietu"
RAW_TOML_URL = f"https://raw.githubusercontent.com/{REPO}/master/pyproject.toml"
CHECK_INTERVAL = 3600   # seconds between auto-checks (1 hour)
_STAMP_FILE = Path.home() / ".jietu_update_check"


def _parse_version(text: str) -> tuple[int, ...]:
    m = re.search(r'version\s*=\s*"([^"]+)"', text)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


def _fetch_remote_version() -> str | None:
    try:
        req = urllib.request.Request(RAW_TOML_URL, headers={"User-Agent": "jietu-updater"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.read().decode()
    except Exception:
        return None


def _cooldown_ok() -> bool:
    """Return True if enough time has passed since the last check."""
    try:
        last = float(_STAMP_FILE.read_text())
        return time.time() - last >= CHECK_INTERVAL
    except Exception:
        return True  # file missing or unreadable → check now


def _mark_checked():
    try:
        _STAMP_FILE.write_text(str(time.time()))
    except Exception:
        pass


class UpdateChecker(QObject):
    update_done = pyqtSignal(bool)   # True = upgraded, False = already up-to-date or error

    def __init__(self):
        super().__init__()
        self._upgrading = False

    def check_async(self):
        """Check only if the 1-hour cooldown has elapsed."""
        if not _cooldown_ok():
            return
        threading.Thread(target=self._check, daemon=True).start()

    def force_check_async(self):
        """Check regardless of cooldown (user-triggered)."""
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        _mark_checked()
        content = _fetch_remote_version()
        if not content:
            return

        from jietu import __version__
        local = _parse_version(f'version = "{__version__}"')
        remote = _parse_version(content)

        if remote > local:
            self._upgrade()

    def _upgrade(self):
        if self._upgrading:
            return
        self._upgrading = True
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet",
                 f"git+https://github.com/{REPO}.git"],
                capture_output=True,
            )
            success = result.returncode == 0
        except Exception:
            success = False
        finally:
            self._upgrading = False

        self.update_done.emit(success)

    @staticmethod
    def restart():
        import os
        os.execv(sys.executable, [sys.executable, "-m", "jietu"])
