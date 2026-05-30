"""System tray application — entry point."""
import sys
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication, QWidget
from PyQt6.QtGui import QAction, QIcon, QPixmap, QColor, QPainter
from PyQt6.QtCore import Qt, QRect

from jietu.capture import CaptureOverlay
from jietu.viewer import PinnedViewer
from jietu.updater import UpdateChecker
from jietu.hotkey import GlobalHotkey
import jietu.startup as startup


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
    def __init__(self, is_child: bool = False):
        super().__init__()
        self._is_child = is_child  # if True, exit 0 on user quit so watchdog stops
        self._viewers: list[PinnedViewer] = []
        self._overlay: CaptureOverlay | None = None
        self._pending_restart = False

        self._updater = UpdateChecker()
        self._updater.update_done.connect(self._on_update_done)

        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_default_icon())
        self._tray.setToolTip("截图工具 (jietu)")
        self._tray.setContextMenu(self._build_menu())
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

        # System-wide EXCLUSIVE hotkey (Win32 RegisterHotKey). QShortcut would NOT work.
        self._hotkey = GlobalHotkey("ctrl+`")
        self._hotkey.triggered.connect(self._start_capture)
        if not self._hotkey.register():
            self._tray.showMessage(
                "快捷键被占用",
                "Ctrl+` 已被其他程序独占，截图请点击托盘图标。",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )

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

        self._act_autostart = QAction("开机自动启动", self)
        self._act_autostart.setCheckable(True)
        self._act_autostart.setChecked(startup.is_enabled())
        self._act_autostart.triggered.connect(self._toggle_autostart)

        self._act_update = QAction("检查更新", self)
        self._act_update.triggered.connect(self._updater.force_check_async)

        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self._quit)

        menu.addAction(act_capture)
        menu.addSeparator()
        menu.addAction(self._act_autostart)
        menu.addAction(self._act_update)
        menu.addSeparator()
        menu.addAction(act_quit)
        return menu

    def _toggle_autostart(self):
        try:
            if self._act_autostart.isChecked():
                startup.enable()
            else:
                startup.disable()
        except Exception as e:
            self._act_autostart.setChecked(not self._act_autostart.isChecked())
            self._tray.showMessage("自启动设置失败", str(e),
                                   QSystemTrayIcon.MessageIcon.Warning, 3000)

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
        viewer.closed.connect(lambda v=viewer: self._on_viewer_closed(v))
        viewer.show()
        self._viewers.append(viewer)

    def _on_capture_cancelled(self):
        self._overlay = None

    def _on_viewer_closed(self, viewer: PinnedViewer):
        self._viewers.remove(viewer)
        if self._pending_restart and not self._viewers:
            UpdateChecker.restart()

    def _quit(self):
        # Release the exclusive hotkey before exiting.
        try:
            self._hotkey.unregister()
        except Exception:
            pass
        # Exit code 0 tells the watchdog this was intentional — don't restart.
        QApplication.instance().exit(0)

    def _on_update_done(self, success: bool):
        self._act_update.setText("检查更新")
        self._act_update.setEnabled(True)
        if not success:
            return
        if self._viewers:
            # Screenshots are pinned — don't force-restart, notify instead
            self._tray.showMessage(
                "jietu 已更新",
                "关闭所有截图后将自动重启生效。",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
            self._pending_restart = True
        else:
            UpdateChecker.restart()
