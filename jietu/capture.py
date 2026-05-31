"""Screen region selection and capture — multi-monitor & DPI aware via mss."""
from __future__ import annotations
import mss
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPixmap, QImage, QGuiApplication, QPen


class CaptureOverlay(QWidget):
    """Full virtual-desktop translucent overlay for drag-to-select capture."""

    captured = pyqtSignal(QPixmap, QRect)   # cropped pixmap, logical rect
    cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._origin: QPoint | None = None
        self._current: QPoint | None = None

        # Logical bounding rect of ALL screens (the virtual desktop).
        self._virtual = self._virtual_geometry()
        # Full virtual desktop grabbed at physical resolution.
        self._screen_pixmap = self._grab_all_screens()

        # Scale = physical pixels / logical points (handles HiDPI uniformly).
        vw = max(1, self._virtual.width())
        vh = max(1, self._virtual.height())
        self._scale_x = self._screen_pixmap.width() / vw
        self._scale_y = self._screen_pixmap.height() / vh

        self._setup_window()

    # ── Geometry & capture ────────────────────────────────────────────────

    @staticmethod
    def _virtual_geometry() -> QRect:
        screens = QGuiApplication.screens()
        total = screens[0].geometry()
        for s in screens[1:]:
            total = total.united(s.geometry())
        return total

    def _grab_all_screens(self) -> QPixmap:
        """Capture the full virtual desktop in physical pixels via mss."""
        with mss.mss() as sct:
            mon = sct.monitors[0]          # [0] = all monitors combined
            shot = sct.grab(mon)
            img = QImage(bytes(shot.bgra), shot.width, shot.height,
                         shot.width * 4, QImage.Format.Format_ARGB32)
            return QPixmap.fromImage(img.copy())

    # ── Window ────────────────────────────────────────────────────────────

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Span the whole virtual desktop with a normal (non-fullscreen) window.
        # showFullScreen() would clamp us to ONE screen, shrinking the capture.
        self.setGeometry(self._virtual)
        self.show()
        self.setGeometry(self._virtual)   # reassert after WM may have moved us
        self.activateWindow()
        self.raise_()
        self.setFocus()

    # ── Events ────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.pos()
            self._current = event.pos()

    def mouseMoveEvent(self, event):
        if self._origin:
            self._current = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._origin:
            logical_rect = QRect(self._origin, event.pos()).normalized()
            if logical_rect.width() > 5 and logical_rect.height() > 5:
                phys = self._to_physical(logical_rect)
                cropped = self._screen_pixmap.copy(phys)
                cropped.setDevicePixelRatio(self._scale_x)
                self.captured.emit(cropped, logical_rect)
            else:
                self.cancelled.emit()
            self.close()

    def _to_physical(self, r: QRect) -> QRect:
        return QRect(
            int(r.x() * self._scale_x),
            int(r.y() * self._scale_y),
            int(r.width() * self._scale_x),
            int(r.height() * self._scale_y),
        )

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        src_full = QRectF(self._screen_pixmap.rect())

        # Background: full desktop dimmed
        painter.drawPixmap(QRectF(self.rect()), self._screen_pixmap, src_full)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        if self._origin and self._current:
            sel = QRect(self._origin, self._current).normalized()
            phys_sel = QRectF(self._to_physical(sel))
            # Restore original (bright) pixels inside the selection
            painter.drawPixmap(QRectF(sel), self._screen_pixmap, phys_sel)
            painter.setPen(QPen(QColor(255, 100, 50), 2))
            painter.drawRect(sel)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(sel.x() + 4, sel.y() - 6,
                             f"{sel.width()} × {sel.height()}")
