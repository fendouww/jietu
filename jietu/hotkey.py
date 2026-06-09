"""System-wide global hotkey.

Windows: WH_KEYBOARD_LL (consume) + RegisterHotKey (backup) + health watchdog.
macOS: HID CGEventTap on a dedicated thread (consume) + NSEvent (backup) + watchdog.
"""
from __future__ import annotations
import sys
import time
import threading
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
_HEALTH_MS = 2500

HOTKEY_COMBO = "alt+~"
HOTKEY_LABEL = "Alt+~"

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
            # ~ is Shift+` on the same physical key.
            if tok == "~":
                mods |= _MODS["shift"]
        else:
            raise ValueError(f"unknown key token: {tok!r}")
    if vk is None:
        raise ValueError("no key specified")
    return mods | _MOD_NOREPEAT, vk


def _cg_mask(*event_types: int) -> int:
    mask = 0
    for t in event_types:
        if 0 <= t < 64:
            mask |= 1 << t
    return mask


def ensure_mac_event_environment() -> bool:
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
        need_shift = bool(need_mask & Quartz.kCGEventFlagMaskShift)
        has_shift = bool(flags & Quartz.kCGEventFlagMaskShift)
        if need_shift != has_shift:
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
        need_shift = bool(need_mask & AppKit.NSEventModifierFlagShift)
        has_shift = bool(flags & AppKit.NSEventModifierFlagShift)
        if need_shift != has_shift:
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


class _WinMsgSink(QWidget):
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
    _WIN_HOTKEY_ID = 0xB001

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
        self._win_mods = 0
        self._win_need_alt = False
        self._win_need_ctrl = False
        self._win_need_shift = False
        self._mac_tap = None
        self._mac_loop = None
        self._mac_tap_thread: threading.Thread | None = None
        self._mac_monitors: list = []
        self._health: QTimer | None = None
        self._key_codes: frozenset[int] = frozenset()
        self._need_mask = 0
        self._nsevent_need_mask = 0
        self._registered = False
        self._last_fire = 0.0
        self._health_busy = False

    def _emit_triggered(self):
        now = time.monotonic()
        if now - self._last_fire < _DEBOUNCE_SEC:
            return
        self._last_fire = now
        QTimer.singleShot(0, self.triggered.emit)

    def _prepare_combo(self) -> bool:
        try:
            mods, vk = _parse_combo(self._combo)
        except ValueError:
            return False
        key_codes = _MAC_KEYCODES.get(vk)
        if not key_codes:
            return False
        self._key_codes = key_codes
        self._need_mask = _mac_flag_mask(mods & 0x000F)
        self._nsevent_need_mask = _nsevent_flag_mask(mods & 0x000F)
        self._win_vk = vk
        self._win_mods = mods
        self._win_need_alt = bool(mods & 0x0001)
        self._win_need_ctrl = bool(mods & 0x0002)
        self._win_need_shift = bool(mods & 0x0004)
        return True

    def register(self) -> bool:
        if not self._prepare_combo():
            return False
        if sys.platform == "darwin":
            ensure_mac_event_environment()
            ok = self._register_mac_nsevent()
            ok = self._register_mac_tap() or ok
            self._registered = ok
        elif sys.platform == "win32":
            self._registered = self._register_win_all()
        else:
            self._registered = self._register_pynput()
        if self._registered:
            self._start_health()
        return self._registered

    def unregister(self):
        self._stop_health()
        if sys.platform == "win32":
            self._unregister_win()
        self._teardown_mac()

    def _start_health(self):
        if self._health is not None:
            return
        self._health = QTimer(self)
        self._health.timeout.connect(self._health_check)
        self._health.start(_HEALTH_MS)

    def _stop_health(self):
        if self._health is not None:
            self._health.stop()
            self._health = None

    def _health_check(self):
        if self._health_busy:
            return
        self._health_busy = True
        try:
            if sys.platform == "darwin":
                self._health_mac()
            elif sys.platform == "win32":
                self._health_win()
        finally:
            self._health_busy = False

    def _health_mac(self):
        tap_ok = False
        if self._mac_tap is not None:
            try:
                import Quartz
                if not Quartz.CGEventTapIsEnabled(self._mac_tap):
                    Quartz.CGEventTapEnable(self._mac_tap, True)
                tap_ok = Quartz.CGEventTapIsEnabled(self._mac_tap)
            except Exception:
                tap_ok = False
        if not tap_ok:
            self._teardown_mac_tap()
            self._register_mac_tap()
        if not self._mac_monitors:
            self._register_mac_nsevent()

    def _health_win(self):
        if self._win_sink is None or not self._win_hwnd:
            self._unregister_win()
            self._register_win_all()
            return
        if not self._win_hotkey_id:
            self._ensure_win_register_hotkey()
        if not self._win_hook:
            self._install_win_hook()

    # ── macOS ────────────────────────────────────────────────────────────────

    def _key_match(self, key_code: int) -> bool:
        if key_code in self._key_codes:
            return True
        return self._alt_grave and key_code in _MAC_GRAVE_KEYCODES

    def _on_mac_hotkey(self):
        self._emit_triggered()

    def _register_mac_nsevent(self) -> bool:
        if self._mac_monitors:
            return True
        try:
            import AppKit
        except Exception:
            return False
        hotkey_self = self

        def on_key(event):
            try:
                if not hotkey_self._key_match(event.keyCode()):
                    return
                if not _nsevent_modifiers_ok(
                    event.modifierFlags(),
                    hotkey_self._nsevent_need_mask,
                    alt_grave=hotkey_self._alt_grave,
                ):
                    return
                hotkey_self._on_mac_hotkey()
            except Exception:
                pass

        def global_handler(event):
            on_key(event)

        def local_handler(event):
            on_key(event)
            return event

        try:
            mask = AppKit.NSEventMaskKeyDown
            g = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, global_handler,
            )
            if g is None:
                return False
            self._mac_monitors.append(g)
            loc = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                mask, local_handler,
            )
            if loc is not None:
                self._mac_monitors.append(loc)
            return True
        except Exception:
            return False

    def _register_mac_tap(self) -> bool:
        if self._mac_tap is not None:
            return True
        ready = threading.Event()
        ok_flag = [False]
        hotkey_self = self

        def thread_main():
            try:
                import Quartz as Q
            except Exception:
                ready.set()
                return

            mask = _cg_mask(Q.kCGEventKeyDown)

            def callback(proxy, type_, event, refcon):
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
                    hotkey_self._on_mac_hotkey()
                    return None
                except Exception:
                    return event

            try:
                tap = Q.CGEventTapCreate(
                    Q.kCGHIDEventTap,
                    Q.kCGHeadInsertEventTap,
                    Q.kCGEventTapOptionDefault,
                    mask,
                    callback,
                    None,
                )
            except (ValueError, TypeError, OverflowError):
                tap = None
            if tap is None:
                ready.set()
                return
            hotkey_self._mac_tap = tap
            src = Q.CFMachPortCreateRunLoopSource(None, tap, 0)
            hotkey_self._mac_loop = Q.CFRunLoopGetCurrent()
            Q.CFRunLoopAddSource(
                hotkey_self._mac_loop, src, Q.kCFRunLoopCommonModes,
            )
            Q.CGEventTapEnable(tap, True)
            ok_flag[0] = Q.CGEventTapIsEnabled(tap)
            ready.set()
            Q.CFRunLoopRun()

        self._mac_tap_thread = threading.Thread(
            target=thread_main, name="jietu-hotkey-tap", daemon=True,
        )
        self._mac_tap_thread.start()
        ready.wait(timeout=3.0)
        return ok_flag[0]

    def _teardown_mac_tap(self):
        if self._mac_loop is not None:
            try:
                import Quartz
                Quartz.CFRunLoopStop(self._mac_loop)
            except Exception:
                pass
        self._mac_loop = None
        self._mac_tap = None
        self._mac_tap_thread = None

    def _teardown_mac_nsevent(self):
        if not self._mac_monitors:
            return
        try:
            import AppKit
            for m in self._mac_monitors:
                AppKit.NSEvent.removeMonitor_(m)
        except Exception:
            pass
        self._mac_monitors.clear()

    def _teardown_mac(self):
        self._teardown_mac_tap()
        self._teardown_mac_nsevent()

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

    def _ensure_win_sink(self) -> bool:
        if self._win_sink is not None and self._win_hwnd:
            return True
        self._win_sink = _WinMsgSink(
            self._emit_triggered,
            hotkey_id=self._win_hotkey_id,
        )
        self._win_sink.show()
        self._win_hwnd = int(self._win_sink.winId())
        return bool(self._win_hwnd)

    def _register_win_all(self) -> bool:
        if not self._ensure_win_sink():
            return False
        hook_ok = self._install_win_hook()
        reg_ok = self._ensure_win_register_hotkey()
        return hook_ok or reg_ok

    def _install_win_hook(self) -> bool:
        if self._win_hook:
            return True
        import ctypes
        from ctypes import wintypes

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
                    if hotkey_self._win_hwnd:
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
        return bool(self._win_hook)

    def _ensure_win_register_hotkey(self) -> bool:
        if self._win_hotkey_id:
            return True
        import ctypes
        user32 = ctypes.windll.user32
        if not self._ensure_win_sink():
            return False
        hid = self._WIN_HOTKEY_ID
        if not user32.RegisterHotKey(self._win_hwnd, hid, self._win_mods, self._win_vk):
            return False
        self._win_hotkey_id = hid
        if self._win_sink is not None:
            self._win_sink._hotkey_id = hid
        return True

    def _unregister_win(self):
        import ctypes
        user32 = ctypes.windll.user32
        if self._win_hook:
            try:
                user32.UnhookWindowsHookEx(self._win_hook)
            except Exception:
                pass
            self._win_hook = None
        self._hook_proc_ref = None
        if self._win_hotkey_id is not None and self._win_hwnd:
            try:
                user32.UnregisterHotKey(self._win_hwnd, self._win_hotkey_id)
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
            elif tok == "~":
                parts.extend(["<shift>", "`"])
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
