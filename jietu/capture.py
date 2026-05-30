"""Screen region selection and capture — DPI-aware via mss."""
from __future__ import annotations
import mss
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPixmap, QImage, QGuiApplication, QPen


class CaptureOverlay(QWidget):
    """Full-screen translucent overlay for drag-to-select region capture."""

    captured = pyqtSignal(QPixmap, QRect)   # physical pixmap, logical rect
    cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._dpr: float = QGuiApplication.primaryScreen().devicePixelRatio()
        self._screen_pixmap = self._grab_all_screens()
        self._setup_window()

    # ── Capture ───────────────────────────────────────────────────────────────

    def _grab_all_screens(self) -> QPixmap:
        """Capture the full virtual desktop in physical pixels via mss."""
        with mss.mss() as sct:
            # monitors[0] = all monitors combined virtual desktop
            mon = sct.monitors[0]
            shot = sct.grab(mon)
            raw = bytes(shot.bgra)
            img = QImage(raw, shot.width, shot.height,
                         shot.width * 4, QImage.Format.Format_ARGB32)
            px = QPixmap.fromImage(img)
        # Tell Qt the DPR so it knows the logical size
        px.setDevicePixelRatio(self._dpr)
        return px

    # ── Window ────────────────────────────────────────────────────────────────

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Position/size in logical pixels — covers all monitors
        screens = QGuiApplication.screens()
        total = screens[0].geometry()
        for s in screens[1:]:
            total = total.united(s.geometry())
        self.setGeometry(total)
        self.showFullScreen()
        # Grab keyboard/focus so Esc works immediately
        self.activateWindow()
        self.raise_()
        self.setFocus()

    # ── Events ────────────────────────────────────────────────────────────────

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
                # Crop at FULL physical resolution (keep all pixels for crisp
                # image + better OCR). Set DPR so the viewer knows the logical
                # size; the image itself stays high-res.
                dpr = self._dpr
                phys = QRect(
                    int(logical_rect.x() * dpr),
                    int(logical_rect.y() * dpr),
                    int(logical_rect.width() * dpr),
                    int(logical_rect.height() * dpr),
                )
                cropped = self._screen_pixmap.copy(phys)
                cropped.setDevicePixelRatio(dpr)
                self.captured.emit(cropped, logical_rect)
            else:
                self.cancelled.emit()
            self.close()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        dpr = self._dpr
        w, h = self._screen_pixmap.width(), self._screen_pixmap.height()

        # Draw full-screen background — map logical widget rect → full physical pixmap
        painter.drawPixmap(
            QRectF(self.rect()),
            self._screen_pixmap,
            QRectF(0, 0, w, h),
        )
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        if self._origin and self._current:
            sel = QRect(self._origin, self._current).normalized()
            # Source region in physical pixels
            phys_sel = QRectF(
                sel.x() * dpr, sel.y() * dpr,
                sel.width() * dpr, sel.height() * dpr,
            )
            # Restore original pixels inside selection
            painter.drawPixmap(QRectF(sel), self._screen_pixmap, phys_sel)
            # Border
            pen = QPen(QColor(255, 100, 50), 2)
            painter.setPen(pen)
            painter.drawRect(sel)

            # Size hint
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                sel.x() + 4, sel.y() - 6,
                f"{sel.width()} × {sel.height()}",
            )
