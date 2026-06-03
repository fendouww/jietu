"""System-wide global hotkey.

Windows: WH_KEYBOARD_LL blocks Alt+`; hidden QWidget receives PostMessage.
macOS: HID CGEventTap consumes Option+` (mask built with 1<<type, not CGEventMaskBit).
"""
from __future__ import annotations
import sys
import time
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt

_MODS = {
    "alt": 0x0001, "option": 0x0001,
    "ctrl": 0x0002, "control": 0x0002,
    "shift": 0x0004,
    "win": 0x0008, "super": 0x0008, "cmd": 0x0008,
}
_MOD_NOREPEAT = 0x4000

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
_MAC_TAP_HEALTH_MS = 15_000

HOTKEY_COMBO = "alt+`"
HOTKEY_LABEL = "Alt+`"

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


def _cg_mask(*event_types: int) -> int:
    """Build CGEventMask without CGEventMaskBit (breaks on tap-disabled types)."""
    mask = 0
    for t in event_types:
        if 0 <= t < 64:
            mask |= 1 << t
    return mask


def ensure_mac_event_environment() -> bool:
    """Tray app: accessory policy after QApplication has started."""
    global _mac_appkit_ready
    if sys.platform != "darwin" or _mac_appkit_ready:
        return _mac_appkit_ready
    try:
        import AppKit
        nsapp = AppKit.NSApplication.sharedApplication()
        nsapp.setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory,
        )
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


class _WinMsgSink(QWidget):
    """Native HWND target for PostMessage / RegisterHotKey delivery."""

    def __init__(self, on_fire, hotkey_id: int | None = None):
        super().__init__()
        self._on_fire = on_fire
        self._hotkey_id = hotkey_id
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.resize(1, 1)

    def nativeEvent(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == GlobalHotkey._WM_JIETU:
                self._on_fire()
            elif (
                self._hotkey_id is not None
                and msg.message == 0x0312
                and msg.wParam == self._hotkey_id
            ):
                self._on_fire()
        return False, 0


class GlobalHotkey(QObject):
    triggered = pyqtSignal()

    _WM_JIETU = 0x8000 + 0xB001

    def __init__(self, combo: str = HOTKEY_COMBO):
        super().__init__()
        self._combo = combo
        self._alt_grave = _is_alt_grave_combo(combo)
        self._listener = None
        self._win_hook = None
        self._hook_proc_ref = None
        self._win_sink: _WinMsgSink | None = None
        self._win_hwnd = 0
        self._win_hotkey_id: int | None = None
        self._win_vk = 0
        self._win_need_alt = False
        self._win_need_ctrl = False
        self._win_need_shift = False
        self._mac_tap = None
        self._mac_tap_source = None
        self._mac_health: QTimer | None = None
        self._key_codes: frozenset[int] = frozenset()
        self._need_mask = 0
        self._registered = False
        self._last_fire = 0.0

    def _emit_triggered(self):
        now = time.monotonic()
        if now - self._last_fire < _DEBOUNCE_SEC:
            return
        self._last_fire = now
        QTimer.singleShot(0, self.triggered.emit)

    def register(self) -> bool:
        if sys.platform == "darwin":
            ensure_mac_event_environment()
            self._registered = self._register_mac_exclusive()
        elif sys.platform == "win32":
            self._registered = self._register_win_exclusive()
        else:
            self._registered = self._register_pynput()
        return self._registered

    def unregister(self):
        if sys.platform == "win32":
            self._unregister_win()
        self._teardown_mac_tap()

    # ── macOS ────────────────────────────────────────────────────────────────

    def _register_mac_exclusive(self) -> bool:
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
        self._key_codes = key_codes
        self._need_mask = _mac_flag_mask(mods & 0x000F)

        # Only key-down in the mask; disabled-tap types use huge IDs and break pyobjc.
        mask = _cg_mask(Quartz.kCGEventKeyDown)
        hotkey_self = self

        def callback(proxy, type_, event, refcon):
            import Quartz as Q
            try:
                if type_ in (
                    Q.kCGEventTapDisabledByTimeout,
                    Q.kCGEventTapDisabledByUserInput,
                ):
                    if hotkey_self._mac_tap is not None:
                        Q.CGEventTapEnable(hotkey_self._mac_tap, True)
                    return event
                if type_ != Q.kCGEventKeyDown:
                    return event
                flags = Q.CGEventGetFlags(event)
                kc = Q.CGEventGetIntegerValueField(
                    event, Q.kCGKeyboardEventKeycode,
                )
                if not hotkey_self._key_match(kc):
                    return event
                if not _quartz_modifiers_ok(
                    flags, hotkey_self._need_mask,
                    alt_grave=hotkey_self._alt_grave,
                ):
                    return event
                hotkey_self._emit_triggered()
                return None
            except Exception:
                return event

        try:
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGHIDEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,
                mask,
                callback,
                None,
            )
        except (ValueError, TypeError, OverflowError):
            tap = None

        if tap is None:
            return False

        self._mac_tap = tap
        src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._mac_tap_source = src
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetMain(),
            src,
            Quartz.kCFRunLoopCommonModes,
        )
        Quartz.CGEventTapEnable(tap, True)
        if not Quartz.CGEventTapIsEnabled(tap):
            self._teardown_mac_tap()
            return False

        self._mac_health = QTimer(self)
        self._mac_health.timeout.connect(self._mac_tap_health)
        self._mac_health.start(_MAC_TAP_HEALTH_MS)
        return True

    def _key_match(self, key_code: int) -> bool:
        if key_code in self._key_codes:
            return True
        return self._alt_grave and key_code in _MAC_GRAVE_KEYCODES

    def _mac_tap_health(self):
        if self._mac_tap is None:
            return
        try:
            import Quartz
            if not Quartz.CGEventTapIsEnabled(self._mac_tap):
                Quartz.CGEventTapEnable(self._mac_tap, True)
        except Exception:
            pass

    def _teardown_mac_tap(self):
        if self._mac_health is not None:
            self._mac_health.stop()
            self._mac_health = None
        if self._mac_tap is not None:
            try:
                import Quartz
                Quartz.CGEventTapEnable(self._mac_tap, False)
                if self._mac_tap_source is not None:
                    Quartz.CFRunLoopRemoveSource(
                        Quartz.CFRunLoopGetMain(),
                        self._mac_tap_source,
                        Quartz.kCFRunLoopCommonModes,
                    )
            except Exception:
                pass
        self._mac_tap = None
        self._mac_tap_source = None

    # ── Windows ───────────────────────────────────────────────────────────────

    def _win_modifiers_ok(self) -> bool:
        import ctypes
        user32 = ctypes.windll.user32

        def down(vk: int) -> bool:
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)

        alt = down(0x12)
        ctrl = down(0x11)
        shift = down(0x10)
        win = down(0x5B) or down(0x5C)

        if self._win_need_alt and not alt:
            return False
        if not self._win_need_alt and alt:
            return False
        if self._win_need_ctrl and not ctrl:
            return False
        if not self._win_need_ctrl and ctrl:
            return False
        if self._win_need_shift and not shift:
            return False
        if win:
            return False
        return True

    def _register_win_exclusive(self) -> bool:
        import ctypes
        from ctypes import wintypes

        try:
            mods, vk = _parse_combo(self._combo)
        except ValueError:
            return False

        self._win_vk = vk
        self._win_need_alt = bool(mods & 0x0001)
        self._win_need_ctrl = bool(mods & 0x0002)
        self._win_need_shift = bool(mods & 0x0004)

        self._win_sink = _WinMsgSink(self._emit_triggered)
        self._win_sink.show()
        self._win_hwnd = int(self._win_sink.winId())
        if not self._win_hwnd:
            return self._register_win_register_hotkey(mods, vk)

        WM_KEYDOWN = 0x0100
        WM_SYSKEYDOWN = 0x0104
        WH_KEYBOARD_LL = 13

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
        )
        hotkey_self = self

        @HOOKPROC
        def hook_proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == hotkey_self._win_vk and hotkey_self._win_modifiers_ok():
                    user32.PostMessageW(
                        hotkey_self._win_hwnd,
                        GlobalHotkey._WM_JIETU,
                        0, 0,
                    )
                    return 1
            return user32.CallNextHookEx(hotkey_self._win_hook, nCode, wParam, lParam)

        self._hook_proc_ref = hook_proc
        self._win_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            hook_proc,
            kernel32.GetModuleHandleW(None),
            0,
        )
        if not self._win_hook:
            self._hook_proc_ref = None
            return self._register_win_register_hotkey(mods, vk)
        return True

    def _register_win_register_hotkey(self, mods: int, vk: int) -> bool:
        """Fallback when the low-level hook cannot be installed."""
        import ctypes
        user32 = ctypes.windll.user32
        hotkey_id = 0xB001
        if self._win_sink is not None:
            self._win_sink.close()
        self._win_sink = _WinMsgSink(self._emit_triggered, hotkey_id=hotkey_id)
        self._win_sink.show()
        self._win_hwnd = int(self._win_sink.winId())
        if not self._win_hwnd:
            return False
        if not user32.RegisterHotKey(self._win_hwnd, hotkey_id, mods, vk):
            return False
        self._win_hotkey_id = hotkey_id
        return True

    def _unregister_win(self):
        import ctypes
        if self._win_hook:
            try:
                ctypes.windll.user32.UnhookWindowsHookEx(self._win_hook)
            except Exception:
                pass
            self._win_hook = None
        self._hook_proc_ref = None
        hotkey_id = getattr(self, "_win_hotkey_id", None)
        if hotkey_id is not None:
            try:
                ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
            self._win_hotkey_id = None
        if self._win_sink is not None:
            self._win_sink.close()
            self._win_sink = None
        self._win_hwnd = 0

    # ── Linux ─────────────────────────────────────────────────────────────────

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
            return True
        except Exception:
            self._listener = None
            return False
