"""Persistent user preferences (~/.jietu/settings)."""
from __future__ import annotations
from pathlib import Path

_SETTINGS_DIR = Path.home() / ".jietu"
_SETTINGS_FILE = _SETTINGS_DIR / "settings"


def _read() -> dict[str, str]:
    try:
        out: dict[str, str] = {}
        for line in _SETTINGS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
        return out
    except Exception:
        return {}


def _write(data: dict[str, str]) -> None:
    try:
        _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        lines = [f"{k}={v}\n" for k, v in sorted(data.items())]
        _SETTINGS_FILE.write_text("".join(lines), encoding="utf-8")
    except Exception:
        pass


def auto_upgrade_enabled() -> bool:
    return _read().get("auto_upgrade", "1") != "0"


def set_auto_upgrade(enabled: bool) -> None:
    data = _read()
    data["auto_upgrade"] = "1" if enabled else "0"
    _write(data)
