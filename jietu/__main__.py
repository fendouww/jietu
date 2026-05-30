import sys
from PyQt6.QtWidgets import QApplication
from jietu.app import App


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = App()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
