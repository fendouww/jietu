"""System-wide global hotkey listener (works without app focus)."""
from __future__ import annotations
from PyQt6.QtCore import QObject, pyqtSignal


class GlobalHotkey(QObject):
    """Listens for a system-wide hotkey and emits `triggered` on the Qt thread."""

    triggered = pyqtSignal()

    def __init__(self, combo: str = "<ctrl>+`"):
        super().__init__()
        self._combo = combo
        self._listener = None

    def start(self):
        try:
            from pynput import keyboard
        except Exception:
            return False  # pynput not available; tray menu still works

        try:
            # GlobalHotKeys maps combo string -> callback
            self._listener = keyboard.GlobalHotKeys(
                {self._combo: self._on_activate}
            )
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception:
            self._listener = None
            return False

    def _on_activate(self):
        # Called from pynput's listener thread — emit signal to hop to Qt thread.
        self.triggered.emit()

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
