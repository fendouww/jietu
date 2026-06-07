"""System tray application — entry point."""
import sys
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication, QWidget
from PyQt6.QtGui import QAction, QIcon, QPixmap, QColor, QPainter
from PyQt6.QtCore import Qt, QRect, QTimer

from jietu.capture import CaptureOverlay
from jietu.viewer import PinnedViewer
from jietu.updater import UpdateChecker
from jietu.hotkey import (
    GlobalHotkey, HOTKEY_COMBO, HOTKEY_LABEL, ensure_mac_event_environment,
)
from jietu import translator
from jietu import upgrade
import jietu.startup as startup
from jietu import settings


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
        self._draft_viewer: PinnedViewer | None = None
        self._pending_upgrade_version: str | None = None

        self._upgrading = False
        self._updater = UpdateChecker()
        self._updater.upgrade_requested.connect(self._do_upgrade)
        self._updater.update_available.connect(self._on_update_available)
        self._updater.up_to_date.connect(self._on_up_to_date)

        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_default_icon())
        self._tray.setToolTip(f"截图工具 (jietu) — {HOTKEY_LABEL}")
        self._tray.setContextMenu(self._build_menu())
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

        # System-wide hotkey — register after the event loop starts (macOS needs this).
        self._hotkey = GlobalHotkey(HOTKEY_COMBO)
        self._hotkey.triggered.connect(
            self._start_capture, Qt.ConnectionType.QueuedConnection,
        )
        QTimer.singleShot(0, self._register_hotkey)
        # Re-register when app regains focus (macOS may drop event taps while idle).
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state_changed)

        self._updater.check_async(force=True)
        self._updater.start_periodic_checks()

        # Warm up the OCR model in the background so the first translation
        # doesn't pay the one-time model-load cost.
        translator.preload()

        self.hide()

    def show(self):
        pass

    def _register_hotkey(self):
        if sys.platform == "darwin":
            ensure_mac_event_environment()
        self._hotkey.unregister()
        if self._hotkey.register():
            return
        if sys.platform == "darwin":
            msg = (
                f"{HOTKEY_LABEL}（Option+`）未就绪：请在「系统设置 → 隐私与安全性」"
                "为 Python 勾选「输入监控」和「辅助功能」后完全退出并重启。"
            )
        else:
            msg = (
                f"{HOTKEY_LABEL} 未就绪，可点击托盘图标截图；"
                "若需全局快捷键请重启 jietu。"
            )
        self._tray.showMessage(
            "快捷键未就绪",
            msg,
            QSystemTrayIcon.MessageIcon.Warning,
            8000,
        )

    def _on_app_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            QTimer.singleShot(200, self._register_hotkey)

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background:#2b2b2b; color:white; border:1px solid #555; }"
            "QMenu::item:selected { background:#444; }"
        )

        act_capture = QAction(f"截图  {HOTKEY_LABEL}", self)
        act_capture.triggered.connect(self._start_capture)

        self._act_autostart = QAction("开机自动启动", self)
        self._act_autostart.setCheckable(True)
        self._act_autostart.setChecked(startup.is_enabled())
        self._act_autostart.triggered.connect(self._toggle_autostart)

        self._act_check = QAction("检查更新", self)
        self._act_check.triggered.connect(self._updater.manual_check_async)

        self._act_upgrade = QAction("升级到最新版", self)
        self._act_upgrade.triggered.connect(self._updater.manual_upgrade)

        self._act_auto_upgrade = QAction("自动升级", self)
        self._act_auto_upgrade.setCheckable(True)
        self._act_auto_upgrade.setChecked(settings.auto_upgrade_enabled())
        self._act_auto_upgrade.triggered.connect(self._toggle_auto_upgrade)

        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self._quit)

        menu.addAction(act_capture)
        menu.addSeparator()
        menu.addAction(self._act_autostart)
        menu.addAction(self._act_check)
        menu.addAction(self._act_upgrade)
        menu.addAction(self._act_auto_upgrade)
        menu.addSeparator()
        menu.addAction(act_quit)
        return menu

    def _toggle_auto_upgrade(self):
        settings.set_auto_upgrade(self._act_auto_upgrade.isChecked())
        if self._act_auto_upgrade.isChecked():
            self._updater.check_async(force=True)

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
        self._overlay.draft.connect(self._on_capture_draft)
        self._overlay.draft_cleared.connect(self._on_capture_draft_cleared)
        self._overlay.captured.connect(self._on_captured)
        self._overlay.cancelled.connect(self._on_capture_cancelled)

    def _open_viewer(self, pixmap, rect: QRect, *, capture_session: bool) -> PinnedViewer:
        viewer = PinnedViewer(pixmap, capture_session=capture_session)
        viewer.set_capture(pixmap, rect.topLeft())
        viewer.closed.connect(lambda v=viewer: self._on_viewer_closed(v))
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()
        self._viewers.append(viewer)
        return viewer

    def _on_capture_draft(self, pixmap, rect: QRect):
        if self._draft_viewer is not None:
            self._draft_viewer.set_capture(pixmap, rect.topLeft())
            self._draft_viewer.raise_()
            return
        if self._overlay is None:
            return
        self._draft_viewer = self._open_viewer(pixmap, rect, capture_session=True)
        self._overlay.draft_viewer = self._draft_viewer

    def _on_capture_draft_cleared(self):
        if self._draft_viewer is None:
            return
        viewer = self._draft_viewer
        self._draft_viewer = None
        if self._overlay is not None:
            self._overlay.draft_viewer = None
        self._viewers.remove(viewer)
        viewer.closed.disconnect()
        viewer.close()

    def _on_captured(self, pixmap, rect: QRect):
        overlay = self._overlay
        draft = self._draft_viewer
        self._overlay = None
        self._draft_viewer = None
        if overlay is not None:
            overlay.draft_viewer = None
        if draft is not None:
            draft.set_capture(pixmap, rect.topLeft())
            draft.set_capture_session(False)
            return
        self._open_viewer(pixmap, rect, capture_session=False)

    def _on_capture_cancelled(self):
        self._overlay = None
        if self._draft_viewer is not None:
            viewer = self._draft_viewer
            self._draft_viewer = None
            if viewer in self._viewers:
                self._viewers.remove(viewer)
            viewer.closed.disconnect()
            viewer.close()
        self._try_pending_upgrade()

    def _on_viewer_closed(self, viewer: PinnedViewer):
        if viewer in self._viewers:
            self._viewers.remove(viewer)
        if viewer is self._draft_viewer:
            self._draft_viewer = None
            if self._overlay is not None:
                self._overlay.request_cancel()
        self._try_pending_upgrade()

    def _is_busy(self) -> bool:
        return bool(self._viewers or self._overlay)

    def _try_pending_upgrade(self):
        if self._pending_upgrade_version and not self._is_busy():
            version = self._pending_upgrade_version
            self._pending_upgrade_version = None
            self._do_upgrade(version)

    def _quit(self):
        # Release the exclusive hotkey before exiting.
        try:
            self._hotkey.unregister()
        except Exception:
            pass
        # Exit code 0 tells the watchdog this was intentional — don't restart.
        QApplication.instance().exit(0)

    def _on_up_to_date(self):
        self._tray.showMessage(
            "jietu", "已是最新版本。",
            QSystemTrayIcon.MessageIcon.Information, 2500,
        )

    def _on_update_available(self, version: str):
        self._tray.showMessage(
            "jietu 有新版本",
            f"发现 v{version}，托盘菜单可开启「自动升级」或手动升级。",
            QSystemTrayIcon.MessageIcon.Information, 5000,
        )

    def _do_upgrade(self, version: str):
        """Start the detached upgrader (kills running jietu, reinstalls, restarts)."""
        if self._upgrading:
            return
        if self._is_busy():
            self._pending_upgrade_version = version or self._pending_upgrade_version or ""
            self._tray.showMessage(
                "jietu 有新版本",
                "当前截图结束后将自动升级并重启。",
                QSystemTrayIcon.MessageIcon.Information, 4000,
            )
            return

        self._upgrading = True
        label = f"v{version}" if version else "最新版"
        self._tray.showMessage(
            "jietu 自动升级",
            f"正在升级到 {label}，稍后自动重启…",
            QSystemTrayIcon.MessageIcon.Information, 4000,
        )
        self._launch_upgrade(version)

    def _launch_upgrade(self, target_version: str = ""):
        # Spawn the detached upgrader, release the hotkey, then quit so the
        # upgrader can replace the locked files and relaunch us.
        try:
            upgrade.spawn_detached(target_version)
        except Exception as e:
            self._tray.showMessage("升级失败", str(e),
                                   QSystemTrayIcon.MessageIcon.Warning, 4000)
            self._upgrading = False
            return
        try:
            self._hotkey.unregister()
        except Exception:
            pass
        QApplication.instance().exit(0)
