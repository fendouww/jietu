"""System-wide global hotkey.

Windows: Win32 RegisterHotKey — truly EXCLUSIVE (the OS routes the combo to us
only; if another app already owns it, registration fails and we report it).
macOS: NSEvent global/local monitors + Quartz event tap + pynput fallback.
"""
from __future__ import annotations
import sys
import time
import threading
from PyQt6.QtCore import QObject, pyqtSignal, QAbstractNativeEventFilter, QTimer

# ── Windows virtual-key / modifier tables ────────────────────────────────────
_MODS = {
    "alt":   0x0001,  # MOD_ALT
    "option": 0x0001,
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

# macOS keycodes for a logical key (US grave + ISO section key on many layouts).
_MAC_KEYCODES = {
    0xC0: {50, 10},
    0x20: {49},
    0x70: {122}, 0x71: {120}, 0x72: {99}, 0x73: {118}, 0x74: {96}, 0x75: {97},
    0x76: {98}, 0x77: {100}, 0x78: {101}, 0x79: {109}, 0x7A: {103}, 0x7B: {111},
    0x41: {0}, 0x42: {11}, 0x43: {8}, 0x44: {2}, 0x45: {14}, 0x46: {3}, 0x47: {5},
    0x48: {4}, 0x49: {34}, 0x4A: {38}, 0x4B: {40}, 0x4C: {37}, 0x4D: {46}, 0x4E: {45},
    0x4F: {31}, 0x50: {35}, 0x51: {12}, 0x52: {15}, 0x53: {1}, 0x54: {17}, 0x55: {32},
    0x56: {9}, 0x57: {13}, 0x58: {7}, 0x59: {16}, 0x5A: {6},
}

_DEBOUNCE_SEC = 0.35


def _parse_combo(combo: str) -> tuple[int, int]:
    """'alt+`' / '<ctrl>+a' -> (modifiers, virtual_key). Raises on bad key."""
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


def _mac_flag_mask(mods: int) -> int:
    """Windows modifier bits → Quartz CGEventFlags mask."""
    import Quartz
    mask = 0
    if mods & 0x0002:
        mask |= Quartz.kCGEventFlagMaskControl
    if mods & 0x0001:
        mask |= Quartz.kCGEventFlagMaskAlternate
    if mods & 0x0004:
        mask |= Quartz.kCGEventFlagMaskShift
    if mods & 0x0008:
        mask |= Quartz.kCGEventFlagMaskCommand
    return mask


def _mac_modifiers_satisfied(flags: int, need_mask: int) -> bool:
    """Required modifiers held; no extra ctrl/cmd/shift unless requested."""
    import Quartz
    if (flags & need_mask) != need_mask:
        return False
    optional = (
        Quartz.kCGEventFlagMaskControl
        | Quartz.kCGEventFlagMaskCommand
        | Quartz.kCGEventFlagMaskShift
        | Quartz.kCGEventFlagMaskAlternate
    )
    required = need_mask & optional
    extra = optional & ~required
    return (flags & extra) == 0


def _nsevent_modifiers_satisfied(flags: int, need_mask: int) -> bool:
    """Same rules for NSEvent modifierFlags (device-independent bits)."""
    import AppKit
    # Strip device-dependent / lock bits (caps lock, fn, etc.)
    flags &= AppKit.NSDeviceIndependentModifierFlagsMask
    if (flags & need_mask) != need_mask:
        return False
    optional = (
        AppKit.NSEventModifierFlagControl
        | AppKit.NSEventModifierFlagCommand
        | AppKit.NSEventModifierFlagShift
        | AppKit.NSEventModifierFlagOption
    )
    required = need_mask & optional
    extra = optional & ~required
    return (flags & extra) == 0


def _nsevent_flag_mask(mods: int) -> int:
    import AppKit
    mask = 0
    if mods & 0x0002:
        mask |= AppKit.NSEventModifierFlagControl
    if mods & 0x0001:
        mask |= AppKit.NSEventModifierFlagOption
    if mods & 0x0004:
        mask |= AppKit.NSEventModifierFlagShift
    if mods & 0x0008:
        mask |= AppKit.NSEventModifierFlagCommand
    return mask


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
    """Emits `triggered` when the system-wide hotkey fires."""

    triggered = pyqtSignal()

    _HOTKEY_ID = 0xB001

    def __init__(self, combo: str = "ctrl+`"):
        super().__init__()
        self._combo = combo
        self._filter = None
        self._listener = None
        self._mac_loop = None
        self._mac_monitors: list = []
        self._registered = False
        self._last_fire = 0.0

    def _emit_triggered(self):
        now = time.monotonic()
        if now - self._last_fire < _DEBOUNCE_SEC:
            return
        self._last_fire = now
        QTimer.singleShot(0, self.triggered.emit)

    def register(self) -> bool:
        if sys.platform == "win32":
            self._registered = self._register_win()
        elif sys.platform == "darwin":
            # NSEvent + Quartz + pynput (Ctrl+` needs Quartz/pynput when Ctrl held).
            self._registered = (
                self._register_mac_nsevent()
                or self._register_mac_quartz()
                or self._register_pynput()
            )
        else:
            self._registered = self._register_pynput()
        return self._registered

    def unregister(self):
        if sys.platform == "win32":
            self._unregister_win()
        if self._mac_monitors:
            try:
                import AppKit
                for m in self._mac_monitors:
                    AppKit.NSEvent.removeMonitor_(m)
            except Exception:
                pass
            self._mac_monitors.clear()
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

    # ── macOS (NSEvent — works well for Option/Alt) ─────────────────────────

    def _register_mac_nsevent(self) -> bool:
        try:
            import AppKit
        except Exception:
            return False
        try:
            mods, vk = _parse_combo(self._combo)
        except ValueError:
            return False
        key_codes = _MAC_KEYCODES.get(vk)
        if not key_codes:
            return False
        need_mask = _nsevent_flag_mask(mods & 0x000F)

        def handler(event):
            try:
                if event.keyCode() not in key_codes:
                    return event
                flags = event.modifierFlags()
                if not _nsevent_modifiers_satisfied(flags, need_mask):
                    return event
                self._emit_triggered()
            except Exception:
                pass
            return event

        try:
            mask = AppKit.NSEventMaskKeyDown
            global_m = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, handler,
            )
            if global_m is None:
                return False
            self._mac_monitors.append(global_m)
            # Also catch key when jietu / overlay has focus.
            local_m = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                mask, handler,
            )
            if local_m is not None:
                self._mac_monitors.append(local_m)
            return True
        except Exception:
            return False

    # ── macOS (Quartz CGEventTap) ───────────────────────────────────────────

    def _register_mac_quartz(self) -> bool:
        try:
            import Quartz
        except Exception:
            return False
        try:
            mods, vk = _parse_combo(self._combo)
        except ValueError:
            return False
        key_codes = _MAC_KEYCODES.get(vk)
        if not key_codes:
            return False
        need_mask = _mac_flag_mask(mods & 0x000F)
        ready = threading.Event()
        tap_ok = [False]

        def callback(proxy, type_, event, refcon):
            try:
                if type_ != Quartz.kCGEventKeyDown:
                    return event
                flags = Quartz.CGEventGetFlags(event)
                kc = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode)
                if kc in key_codes and _mac_modifiers_satisfied(flags, need_mask):
                    self._emit_triggered()
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
                ready.set()
                return
            tap_ok[0] = True
            src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            self._mac_loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(
                self._mac_loop, src, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            ready.set()
            Quartz.CFRunLoopRun()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        ready.wait(timeout=2.0)
        return tap_ok[0]

    # ── Windows (exclusive) ──────────────────────────────────────────────────

    def _register_win(self) -> bool:
        import ctypes
        from PyQt6.QtWidgets import QApplication
        try:
            mods, vk = _parse_combo(self._combo)
        except ValueError:
            return False

        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, self._HOTKEY_ID, mods, vk):
            return False

        self._filter = _WinHotkeyFilter(self._HOTKEY_ID, self._emit_triggered)
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

    # ── Fallback (pynput) ────────────────────────────────────────────────────

    def _register_pynput(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False

        parts = []
        for raw in self._combo.replace("<", "").replace(">", "").split("+"):
            tok = raw.strip().lower()
            if tok in _MODS:
                # pynput on macOS: <alt> = Option key
                parts.append("<alt>" if tok in ("alt", "option") else f"<{tok}>")
            else:
                parts.append(tok)
        combo = "+".join(parts)

        try:
            self._listener = keyboard.GlobalHotKeys(
                {combo: self._emit_triggered}
            )
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception:
            self._listener = None
            return False
