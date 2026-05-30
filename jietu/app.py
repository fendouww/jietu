"""System tray application — entry point."""
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication, QWidget, QMessageBox
from PyQt6.QtGui import QAction, QIcon, QPixmap, QColor, QPainter, QKeySequence, QShortcut
from PyQt6.QtCore import Qt, QRect

from jietu.capture import CaptureOverlay
from jietu.viewer import PinnedViewer
from jietu.updater import UpdateChecker


def _default_icon() -> QIcon:
    px = QPixmap(32, 32)
    px.fill(QColor(0, 0, 0, 0))
    painter = QPainter(px)
    painter.setBrush(QColor(255, 80, 50))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "截")
    painter.end()
    return QIcon(px)


class App(QWidget):
    def __init__(self):
        super().__init__()
        self._viewers: list[PinnedViewer] = []
        self._overlay: CaptureOverlay | None = None

        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_default_icon())
        self._tray.setToolTip("截图工具 (jietu)")
        self._tray.setContextMenu(self._build_menu())
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

        shortcut = QShortcut(QKeySequence("Ctrl+`"), self)
        shortcut.activated.connect(self._start_capture)

        self._updater = UpdateChecker()
        self._updater.update_available.connect(self._on_update_available)
        self._updater.update_done.connect(self._on_update_done)
        self._updater.check_async()

        self.hide()

    def show(self):
        pass

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background:#2b2b2b; color:white; border:1px solid #555; }"
            "QMenu::item:selected { background:#444; }"
        )
        act_capture = QAction("截图  Ctrl+`", self)
        act_capture.triggered.connect(self._start_capture)

        self._act_update = QAction("检查更新", self)
        self._act_update.triggered.connect(self._updater.check_async)

        act_quit = QAction("退出", self)
        act_quit.triggered.connect(QApplication.quit)

        menu.addAction(act_capture)
        menu.addSeparator()
        menu.addAction(self._act_update)
        menu.addSeparator()
        menu.addAction(act_quit)
        return menu

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._start_capture()

    def _start_capture(self):
        if self._overlay:
            return
        self._overlay = CaptureOverlay()
        self._overlay.captured.connect(self._on_captured)
        self._overlay.cancelled.connect(self._on_capture_cancelled)

    def _on_captured(self, pixmap, rect: QRect):
        self._overlay = None
        viewer = PinnedViewer(pixmap)
        viewer.move(rect.topLeft())
        viewer.closed.connect(lambda v=viewer: self._viewers.remove(v))
        viewer.show()
        self._viewers.append(viewer)

    def _on_capture_cancelled(self):
        self._overlay = None

    def _on_update_available(self, new_version: str):
        self._tray.showMessage(
            "jietu 有新版本",
            f"发现 v{new_version}，正在后台更新…",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )
        self._act_update.setText("更新中…")
        self._act_update.setEnabled(False)
        self._updater.upgrade_async()

    def _on_update_done(self):
        self._act_update.setText("检查更新")
        self._act_update.setEnabled(True)
        self._tray.showMessage(
            "jietu 更新完成",
            "点击「重启」立即生效，或下次启动时自动使用新版本。",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )
        self._tray.messageClicked.connect(UpdateChecker.restart)
