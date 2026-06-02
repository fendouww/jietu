"""Auto-update: poll GitHub for a newer version and upgrade clients automatically.

When master/pyproject.toml on GitHub has a higher version than the local install,
the app schedules a detached upgrade (jietu.upgrade) while idle.
"""
from __future__ import annotations
import re
import threading
import time
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

from jietu import settings

REPO = "fendouww/jietu"
RAW_TOML_URL = f"https://raw.githubusercontent.com/{REPO}/master/pyproject.toml"
# Minimum seconds between network version checks.
CHECK_INTERVAL = 300
# Qt timer interval for background polling (5 minutes).
POLL_INTERVAL_MS = CHECK_INTERVAL * 1000
_STAMP_FILE = Path.home() / ".jietu" / "last_update_check"
_FAIL_FILE = Path.home() / ".jietu" / "upgrade_fail"


def _parse_version(text: str) -> tuple[int, ...]:
    m = re.search(r'version\s*=\s*"([^"]+)"', text)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


def _fetch_remote_toml() -> str | None:
    try:
        # Cache-bust so clients see the version bump soon after a GitHub push.
        url = f"{RAW_TOML_URL}?_={int(time.time())}"
        req = urllib.request.Request(url, headers={"User-Agent": "jietu-updater"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.read().decode()
    except Exception:
        return None


def _cooldown_ok() -> bool:
    try:
        last = float(_STAMP_FILE.read_text(encoding="utf-8"))
        return time.time() - last >= CHECK_INTERVAL
    except Exception:
        return True


def _mark_checked():
    try:
        _STAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STAMP_FILE.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def _recent_upgrade_failed(version: str) -> bool:
    """Avoid upgrade loops if pip failed for this version recently."""
    try:
        text = _FAIL_FILE.read_text(encoding="utf-8").strip()
        ver, ts = text.split(",", 1)
        if ver != version:
            return False
        return time.time() - float(ts) < 3600
    except Exception:
        return False


def mark_upgrade_failed(version: str) -> None:
    try:
        _FAIL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _FAIL_FILE.write_text(f"{version},{time.time()}", encoding="utf-8")
    except Exception:
        pass


class UpdateChecker(QObject):
    # Emitted on the Qt thread when an upgrade should run.
    # arg = new version string, or "" for a forced/manual upgrade.
    upgrade_requested = pyqtSignal(str)
    # Newer version found but auto-upgrade is disabled in settings.
    update_available = pyqtSignal(str)
    up_to_date = pyqtSignal()   # manual check found nothing newer

    def __init__(self):
        super().__init__()
        self._timer: QTimer | None = None

    def start_periodic_checks(self):
        """Poll GitHub every CHECK_INTERVAL seconds while the app runs."""
        if self._timer is not None:
            return
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_async)
        self._timer.start(POLL_INTERVAL_MS)

    def check_async(self, *, force: bool = False):
        """Background version check (respects cooldown unless force=True)."""
        if not force and not _cooldown_ok():
            return
        threading.Thread(
            target=self._check, args=(False,), daemon=True,
        ).start()

    def manual_upgrade(self):
        """User clicked 升级 — always upgrade (force-reinstall latest)."""
        self.upgrade_requested.emit("")

    def manual_check_async(self):
        """User clicked 检查更新 — check now, upgrade if newer."""
        threading.Thread(
            target=self._check, args=(True,), daemon=True,
        ).start()

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
            if _recent_upgrade_failed(remote_str):
                return
            if settings.auto_upgrade_enabled() or manual:
                self.upgrade_requested.emit(remote_str)
            else:
                self.update_available.emit(remote_str)
        elif manual:
            self.up_to_date.emit()
