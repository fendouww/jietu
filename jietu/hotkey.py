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

# Map a Windows virtual-key (the canonical key id used by _parse_combo) to the
# macOS keyboard keycode used by Quartz event taps.
_MAC_KEYCODES = {
    0xC0: 50,   # ` (grave)
    0x20: 49,   # space
    0x70: 122, 0x71: 120, 0x72: 99, 0x73: 118, 0x74: 96, 0x75: 97,  # F1-F6
    0x76: 98, 0x77: 100, 0x78: 101, 0x79: 109, 0x7A: 103, 0x7B: 111,  # F7-F12
    # letters A-Z (0x41-0x5A) → macOS keycodes
    0x41: 0, 0x42: 11, 0x43: 8, 0x44: 2, 0x45: 14, 0x46: 3, 0x47: 5,
    0x48: 4, 0x49: 34, 0x4A: 38, 0x4B: 40, 0x4C: 37, 0x4D: 46, 0x4E: 45,
    0x4F: 31, 0x50: 35, 0x51: 12, 0x52: 15, 0x53: 1, 0x54: 17, 0x55: 32,
    0x56: 9, 0x57: 13, 0x58: 7, 0x59: 16, 0x5A: 6,
}


def _mac_flag_mask(mods: int) -> int:
    """Windows modifier bits → Quartz CGEventFlags mask."""
    import Quartz
    mask = 0
    if mods & 0x0002:  # ctrl
        mask |= Quartz.kCGEventFlagMaskControl
    if mods & 0x0001:  # alt
        mask |= Quartz.kCGEventFlagMaskAlternate
    if mods & 0x0004:  # shift
        mask |= Quartz.kCGEventFlagMaskShift
    if mods & 0x0008:  # cmd/win
        mask |= Quartz.kCGEventFlagMaskCommand
    return mask


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
        self._mac_loop = None
        self._registered = False

    # ── Public API ──────────────────────────────────────────────────────────

    def register(self) -> bool:
        if sys.platform == "win32":
            self._registered = self._register_win()
        elif sys.platform == "darwin":
            # Native Quartz event tap is reliable for Ctrl+<char>; pynput drops
            # the char event while Ctrl is held on macOS. Fall back if missing.
            self._registered = self._register_mac() or self._register_pynput()
        else:
            self._registered = self._register_pynput()
        return self._registered

    def unregister(self):
        if sys.platform == "win32":
            self._unregister_win()
        if self._mac_loop is not None:
            try:
                import Quartz
                Quartz.CFRunLoopStop(self._mac_loop)
            except Exception:
                pass
            self._mac_loop = None
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    # ── macOS (native Quartz event tap) ──────────────────────────────────────

    def _register_mac(self) -> bool:
        try:
            import Quartz  # provided by pyobjc (pulled in via ocrmac)
        except Exception:
            return False
        try:
            mods, vk = _parse_combo(self._combo)
        except ValueError:
            return False
        key_code = _MAC_KEYCODES.get(vk)
        if key_code is None:
            return False
        need_mask = _mac_flag_mask(mods)

        def callback(proxy, type_, event, refcon):
            try:
                flags = Quartz.CGEventGetFlags(event)
                kc = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode)
                if kc == key_code and (flags & need_mask) == need_mask:
                    self.triggered.emit()
            except Exception:
                pass
            return event

        def run():
            mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                mask, callback, None,
            )
            if not tap:
                return
            src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            self._mac_loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(
                self._mac_loop, src, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            Quartz.CFRunLoopRun()

        import threading
        self._mac_thread_ok = threading.Event()
        t = threading.Thread(target=run, daemon=True)
        t.start()
        # Give the tap a moment; if Accessibility is denied, tap is None and the
        # loop returns immediately, but we still report True to avoid a false
        # "occupied" warning — the user just needs Accessibility permission.
        return True

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
