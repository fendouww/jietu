"""Auto-update: check GitHub for newer version and upgrade via pip."""
from __future__ import annotations
import re
import subprocess
import sys
import threading
import urllib.request

from PyQt6.QtCore import QObject, pyqtSignal

REPO = "fendouww/jietu"
RAW_TOML_URL = f"https://raw.githubusercontent.com/{REPO}/master/pyproject.toml"


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


class UpdateChecker(QObject):
    update_available = pyqtSignal(str)   # new version string
    update_done = pyqtSignal()           # pip finished, ready to restart

    def __init__(self):
        super().__init__()
        self._upgrading = False

    def check_async(self):
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        content = _fetch_remote_version()
        if not content:
            return

        from jietu import __version__
        local = _parse_version(f'version = "{__version__}"')
        remote_str = re.search(r'version\s*=\s*"([^"]+)"', content)
        if not remote_str:
            return
        remote = _parse_version(content)

        if remote > local:
            self.update_available.emit(remote_str.group(1))

    def upgrade_async(self):
        if self._upgrading:
            return
        self._upgrading = True
        threading.Thread(target=self._upgrade, daemon=True).start()

    def _upgrade(self):
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet",
                 f"git+https://github.com/{REPO}.git"],
                check=True,
                capture_output=True,
            )
        except Exception:
            pass
        finally:
            self._upgrading = False
            self.update_done.emit()

    @staticmethod
    def restart():
        import os
        os.execv(sys.executable, [sys.executable, "-m", "jietu"])
