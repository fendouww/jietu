"""System-wide global hotkey.

Windows: Win32 RegisterHotKey — exclusive system hotkey.
macOS: NSApplication (accessory) + NSEvent monitors + HID CGEventTap + pynput.
"""
from __future__ import annotations
import sys
import time
import threading
from PyQt6.QtCore import QObject, pyqtSignal, QAbstractNativeEventFilter, QTimer

_MODS = {
    "alt":   0x0001,
    "option": 0x0001,
    "ctrl":  0x0002,
    "control": 0x0002,
    "shift": 0x0004,
    "win":   0x0008,
    "super": 0x0008,
    "cmd":   0x0008,
}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312

_VK = {
    "`": 0xC0, "~": 0xC0,
    "-": 0xBD, "=": 0xBB,
    "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF,
    "space": 0x20,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}

# Grave / section physical keys on common Mac layouts (incl. many CN ABC keyboards).
_MAC_GRAVE_KEYCODES = frozenset({50, 10, 33, 41})

_MAC_KEYCODES = {
    0xC0: _MAC_GRAVE_KEYCODES,
    0x20: {49},
    0x70: {122}, 0x71: {120}, 0x72: {99}, 0x73: {118}, 0x74: {96}, 0x75: {97},
    0x76: {98}, 0x77: {100}, 0x78: {101}, 0x79: {109}, 0x7A: {103}, 0x7B: {111},
    0x41: {0}, 0x42: {11}, 0x43: {8}, 0x44: {2}, 0x45: {14}, 0x46: {3}, 0x47: {5},
    0x48: {4}, 0x49: {34}, 0x4A: {38}, 0x4B: {40}, 0x4C: {37}, 0x4D: {46}, 0x4E: {45},
    0x4F: {31}, 0x50: {35}, 0x51: {12}, 0x52: {15}, 0x53: {1}, 0x54: {17}, 0x55: {32},
    0x56: {9}, 0x57: {13}, 0x58: {7}, 0x59: {16}, 0x5A: {6},
}

_DEBOUNCE_SEC = 0.35

HOTKEY_COMBO = "alt+`"
HOTKEY_LABEL = "Alt+`"   # macOS 键帽为 Option

_mac_appkit_ready = False


def _parse_combo(combo: str) -> tuple[int, int]:
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


def ensure_mac_event_environment() -> bool:
    """Background tray apps must be NSApplication (accessory) for global hotkeys."""
    global _mac_appkit_ready
    if sys.platform != "darwin" or _mac_appkit_ready:
        return _mac_appkit_ready
    try:
        import AppKit
        nsapp = AppKit.NSApplication.sharedApplication()
        nsapp.setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory,
        )
        # Required or addGlobalMonitorForEvents often never fires for agent apps.
        AppKit.NSApp.finishLaunching()
        _mac_appkit_ready = True
        return True
    except Exception:
        return False


def _is_alt_grave_combo(combo: str) -> bool:
    parts = {p.strip().lower() for p in combo.split("+")}
    return ("alt" in parts or "option" in parts) and ("`" in parts or "~" in parts)


def _mac_flag_mask(mods: int) -> int:
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


def _quartz_modifiers_ok(flags: int, need_mask: int, *, alt_grave: bool) -> bool:
    import Quartz
    if alt_grave:
        if not (flags & Quartz.kCGEventFlagMaskAlternate):
            return False
        if flags & Quartz.kCGEventFlagMaskCommand:
            return False
        if flags & Quartz.kCGEventFlagMaskControl:
            return False
        return True
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


def _nsevent_modifiers_ok(flags: int, need_mask: int, *, alt_grave: bool) -> bool:
    import AppKit
    flags &= AppKit.NSDeviceIndependentModifierFlagsMask
    if alt_grave:
        opt = AppKit.NSEventModifierFlagOption
        if not (flags & opt):
            return False
        if flags & AppKit.NSEventModifierFlagCommand:
            return False
        if flags & AppKit.NSEventModifierFlagControl:
            return False
        return True
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


class _MacPynputAltGrave:
    """Manual Option+` listener — GlobalHotKeys often misses ` on macOS."""

    def __init__(self, on_fire):
        self._on_fire = on_fire
        self._option_down = False
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False
        opt_keys = set()
        for name in ("alt", "alt_l", "alt_r", "alt_gr"):
            k = getattr(keyboard.Key, name, None)
            if k is not None:
                opt_keys.add(k)

        def on_press(key):
            if key in opt_keys:
                self._option_down = True
                return
            if not self._option_down:
                return
            if self._is_grave_key(key):
                self._on_fire()

        def on_release(key):
            if key in opt_keys:
                self._option_down = False

        try:
            self._listener = keyboard.Listener(
                on_press=on_press, on_release=on_release,
            )
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception:
            self._listener = None
            return False

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    @staticmethod
    def _is_grave_key(key) -> bool:
        from pynput.keyboard import KeyCode
        if isinstance(key, KeyCode):
            if key.char in ("`", "~"):
                return True
            if key.vk in (50, 10, 33, 41):
                return True
        return False


class GlobalHotkey(QObject):
    triggered = pyqtSignal()

    _HOTKEY_ID = 0xB001

    def __init__(self, combo: str = HOTKEY_COMBO):
        super().__init__()
        self._combo = combo
        self._alt_grave = _is_alt_grave_combo(combo)
        self._filter = None
        self._listener = None
        self._mac_pynput: _MacPynputAltGrave | None = None
        self._mac_loop = None
        self._mac_monitors: list = []
        self._registered = False
        self._last_fire = 0.0
        self._backends: list[str] = []

    def _emit_triggered(self):
        now = time.monotonic()
        if now - self._last_fire < _DEBOUNCE_SEC:
            return
        self._last_fire = now
        QTimer.singleShot(0, self.triggered.emit)

    def register(self) -> bool:
        if sys.platform == "darwin":
            ensure_mac_event_environment()
        if sys.platform == "win32":
            self._registered = self._register_win()
        elif sys.platform == "darwin":
            ok = False
            if self._register_mac_nsevent():
                ok = True
            if self._register_mac_quartz():
                ok = True
            if self._alt_grave and self._register_mac_pynput_alt_grave():
                ok = True
            elif self._register_pynput():
                ok = True
            self._registered = ok
        else:
            self._registered = self._register_pynput()
        return self._registered

    def backends(self) -> str:
        return ", ".join(self._backends) if self._backends else "none"

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
        if self._mac_pynput:
            self._mac_pynput.stop()
            self._mac_pynput = None
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def _mac_key_match(self, key_code: int, key_codes: frozenset) -> bool:
        if key_code in key_codes:
            return True
        if self._alt_grave and key_code in _MAC_GRAVE_KEYCODES:
            return True
        return False

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

        def on_key(event):
            try:
                if not self._mac_key_match(event.keyCode(), key_codes):
                    return
                if not _nsevent_modifiers_ok(
                    event.modifierFlags(), need_mask,
                    alt_grave=self._alt_grave,
                ):
                    return
                self._emit_triggered()
            except Exception:
                pass

        def global_handler(event):
            on_key(event)

        def local_handler(event):
            on_key(event)
            return event

        try:
            mask = AppKit.NSEventMaskKeyDown
            global_m = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, global_handler,
            )
            if global_m is None:
                return False
            self._mac_monitors.append(global_m)
            local_m = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                mask, local_handler,
            )
            if local_m is not None:
                self._mac_monitors.append(local_m)
            self._backends.append("nsevent")
            return True
        except Exception:
            return False

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
                if not self._mac_key_match(kc, key_codes):
                    return event
                if not _quartz_modifiers_ok(
                    flags, need_mask, alt_grave=self._alt_grave,
                ):
                    return event
                self._emit_triggered()
            except Exception:
                pass
            return event

        def run():
            mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            tap = None
            for location in (
                Quartz.kCGHIDEventTap,
                Quartz.kCGSessionEventTap,
            ):
                tap = Quartz.CGEventTapCreate(
                    location,
                    Quartz.kCGHeadInsertEventTap,
                    Quartz.kCGEventTapOptionListenOnly,
                    mask, callback, None,
                )
                if tap:
                    break
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

        threading.Thread(target=run, daemon=True).start()
        ready.wait(timeout=2.0)
        if tap_ok[0]:
            self._backends.append("quartz")
        return tap_ok[0]

    def _register_mac_pynput_alt_grave(self) -> bool:
        self._mac_pynput = _MacPynputAltGrave(self._emit_triggered)
        if self._mac_pynput.start():
            self._backends.append("pynput-alt")
            return True
        self._mac_pynput = None
        return False

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

    def _register_pynput(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False
        parts = []
        for raw in self._combo.replace("<", "").replace(">", "").split("+"):
            tok = raw.strip().lower()
            if tok in _MODS:
                parts.append("<alt>" if tok in ("alt", "option") else f"<{tok}>")
            else:
                parts.append(tok)
        combo = "+".join(parts)
        try:
            self._listener = keyboard.GlobalHotKeys(
                {combo: self._emit_triggered},
            )
            self._listener.daemon = True
            self._listener.start()
            self._backends.append("pynput")
            return True
        except Exception:
            self._listener = None
            return False
