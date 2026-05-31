"""Auto-update: check GitHub for a newer version and request a safe upgrade.

The actual upgrade is performed by a detached process (jietu.upgrade) that
terminates the running app first, so files don't stay locked. This module only
detects new versions and signals the app to start that process.
"""
from __future__ import annotations
import re
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


def _fetch_remote_toml() -> str | None:
    try:
        req = urllib.request.Request(RAW_TOML_URL, headers={"User-Agent": "jietu-updater"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.read().decode()
    except Exception:
        return None


def _cooldown_ok() -> bool:
    try:
        last = float(_STAMP_FILE.read_text())
        return time.time() - last >= CHECK_INTERVAL
    except Exception:
        return True


def _mark_checked():
    try:
        _STAMP_FILE.write_text(str(time.time()))
    except Exception:
        pass


class UpdateChecker(QObject):
    # Emitted (on the Qt thread) when an upgrade should run.
    # arg = new version string, or "" for a forced/manual upgrade.
    upgrade_requested = pyqtSignal(str)
    up_to_date = pyqtSignal()   # manual check found nothing newer

    def __init__(self):
        super().__init__()

    def check_async(self):
        """Auto check, gated by the 1-hour cooldown."""
        if not _cooldown_ok():
            return
        threading.Thread(target=self._check, args=(False,), daemon=True).start()

    def manual_upgrade(self):
        """User clicked 升级 — always upgrade (force-reinstall latest)."""
        self.upgrade_requested.emit("")

    def manual_check_async(self):
        """User clicked 检查更新 — check now, upgrade only if newer."""
        threading.Thread(target=self._check, args=(True,), daemon=True).start()

    def _check(self, manual: bool):
        _mark_checked()
        content = _fetch_remote_toml()
        if not content:
            if manual:
                self.up_to_date.emit()
            return

        from jietu import __version__
        local = _parse_version(f'version = "{__version__}"')
        remote = _parse_version(content)
        m = re.search(r'version\s*=\s*"([^"]+)"', content)
        remote_str = m.group(1) if m else ""

        if remote > local:
            self.upgrade_requested.emit(remote_str)
        elif manual:
            self.up_to_date.emit()
