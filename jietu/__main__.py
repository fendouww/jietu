import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtCore import Qt

# Exit code meaning "user quit intentionally" — watchdog will NOT restart on this.
CLEAN_EXIT = 0


def _enable_hidpi():
    """Make the process per-monitor DPI aware and stop Qt from rounding the
    scale factor — otherwise captures on 125%/150% displays look blurry."""
    if sys.platform == "win32":
        import ctypes
        try:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass


def main():
    _enable_hidpi()
    if sys.platform == "darwin":
        from jietu.hotkey import ensure_mac_event_environment
        ensure_mac_event_environment()
    # --child flag: launched by watchdog, use clean exit code on intentional quit
    is_child = "--child" in sys.argv

    app = QApplication([a for a in sys.argv if a != "--child"])
    app.setQuitOnLastWindowClosed(False)
    window = App(is_child=is_child)
    window.show()
    result = app.exec()
    sys.exit(result)


# Import App after _enable_hidpi is defined; QApplication is created in main().
from jietu.app import App  # noqa: E402


if __name__ == "__main__":
    main()
