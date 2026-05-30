import sys
from PyQt6.QtWidgets import QApplication
from jietu.app import App

# Exit code meaning "user quit intentionally" — watchdog will NOT restart on this.
CLEAN_EXIT = 0


def main():
    # --child flag: launched by watchdog, use clean exit code on intentional quit
    is_child = "--child" in sys.argv

    app = QApplication([a for a in sys.argv if a != "--child"])
    app.setQuitOnLastWindowClosed(False)
    window = App(is_child=is_child)
    window.show()
    result = app.exec()
    sys.exit(result)


if __name__ == "__main__":
    main()
