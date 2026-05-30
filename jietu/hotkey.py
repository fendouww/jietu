"""System-wide global hotkey.

Windows: Win32 RegisterHotKey — truly EXCLUSIVE (the OS routes the combo to us
only; if another app already owns it, registration fails and we report it).
Other platforms: pynput fallback (non-exclusive listener).
"""
from __future__ import annotations
import sys
from PyQt6.QtCore import QObject, pyqtSignal, QAbstractNativeEventFilter

# ── Windows virtual-key / modifier tables ────────────────────────────────────
_MODS = {
    "alt":   0x0001,  # MOD_ALT
    "ctrl":  0x0002,  # MOD_CONTROL
    "control": 0x0002,
    "shift": 0x0004,  # MOD_SHIFT
    "win":   0x0008,  # MOD_WIN
    "super": 0x0008,
    "cmd":   0x0008,
}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312

_VK = {
    "`": 0xC0, "~": 0xC0,            # VK_OEM_3
    "-": 0xBD, "=": 0xBB,
    "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF,
    "space": 0x20,
    "printscreen": 0x2C, "prtsc": 0x2C, "snapshot": 0x2C,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


def _parse_combo(combo: str) -> tuple[int, int]:
    """'ctrl+`' / '<ctrl>+a' -> (modifiers, virtual_key). Raises on bad key."""
    mods = 0
    vk = None
    for raw in combo.replace("<", "").replace(">", "").split("+"):
        tok = raw.strip().lower()
        if not tok:
            continue
        if tok in _MODS:
            mods |= _MODS[tok]
        elif len(tok) == 1 and tok.isalpha():
            vk = ord(tok.upper())
        elif len(tok) == 1 and tok.isdigit():
            vk = ord(tok)
        elif tok in _VK:
            vk = _VK[tok]
        else:
            raise ValueError(f"unknown key token: {tok!r}")
    if vk is None:
        raise ValueError("no key specified")
    return mods | _MOD_NOREPEAT, vk


class _WinHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hotkey_id: int, callback):
        super().__init__()
        self._id = hotkey_id
        self._cb = callback

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_HOTKEY and msg.wParam == self._id:
                self._cb()
        return False, 0


class GlobalHotkey(QObject):
    """Emits `triggered` when the system-wide hotkey fires.

    `register()` returns True on exclusive success (Windows) or best-effort
    success (other platforms), False if the combo could not be claimed.
    """

    triggered = pyqtSignal()

    _HOTKEY_ID = 0xB001

    def __init__(self, combo: str = "ctrl+`"):
        super().__init__()
        self._combo = combo
        self._filter = None
        self._listener = None
        self._registered = False

    # ── Public API ──────────────────────────────────────────────────────────

    def register(self) -> bool:
        if sys.platform == "win32":
            self._registered = self._register_win()
        else:
            self._registered = self._register_pynput()
        return self._registered

    def unregister(self):
        if sys.platform == "win32":
            self._unregister_win()
        elif self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    # ── Windows (exclusive) ──────────────────────────────────────────────────

    def _register_win(self) -> bool:
        import ctypes
        from PyQt6.QtWidgets import QApplication
        try:
            mods, vk = _parse_combo(self._combo)
        except ValueError:
            return False

        user32 = ctypes.windll.user32
        # hwnd=0 → WM_HOTKEY posted to this thread's message queue
        if not user32.RegisterHotKey(None, self._HOTKEY_ID, mods, vk):
            return False  # already owned by another app

        self._filter = _WinHotkeyFilter(self._HOTKEY_ID, self.triggered.emit)
        QApplication.instance().installNativeEventFilter(self._filter)
        return True

    def _unregister_win(self):
        import ctypes
        from PyQt6.QtWidgets import QApplication
        if self._filter:
            QApplication.instance().removeNativeEventFilter(self._filter)
            self._filter = None
        try:
            ctypes.windll.user32.UnregisterHotKey(None, self._HOTKEY_ID)
        except Exception:
            pass

    # ── Fallback (pynput, non-exclusive) ─────────────────────────────────────

    def _register_pynput(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False
        # Translate 'ctrl+`' -> pynput '<ctrl>+`'
        parts = []
        for raw in self._combo.replace("<", "").replace(">", "").split("+"):
            tok = raw.strip().lower()
            if tok in _MODS:
                parts.append(f"<{tok}>")
            else:
                parts.append(tok)
        combo = "+".join(parts)
        try:
            self._listener = keyboard.GlobalHotKeys(
                {combo: self.triggered.emit}
            )
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception:
            self._listener = None
            return False
