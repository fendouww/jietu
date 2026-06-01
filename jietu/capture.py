"""Screen region selection and capture.

One overlay window PER screen — a single window cannot span multiple displays
on macOS ("Displays have separate Spaces"), and per-screen overlays also handle
mixed-DPI setups correctly (each screen uses its own grab and scale).
"""
from __future__ import annotations
import mss
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, QObject, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPixmap, QImage, QGuiApplication, QPen


class _ScreenOverlay(QWidget):
    """Translucent overlay covering ONE screen; drag to select a region."""

    selected = pyqtSignal(QPixmap, QRect)   # cropped pixmap, GLOBAL logical rect
    cancelled = pyqtSignal()

    def __init__(self, screen, pixmap: QPixmap):
        super().__init__()
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._pix = pixmap                      # physical-pixel grab of this screen
        geo = screen.geometry()                 # logical, global
        self._screen_origin = geo.topLeft()
        self._scale_x = self._pix.width() / max(1, geo.width())
        self._scale_y = self._pix.height() / max(1, geo.height())

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setScreen(screen)
        self.setGeometry(geo)
        self.show()
        self.setGeometry(geo)
        self.activateWindow()
        self.raise_()
        self.setFocus()

    # ── Events ────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()

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
            sel = QRect(self._origin, event.pos()).normalized()
            if sel.width() > 5 and sel.height() > 5:
                phys = self._to_physical(sel)
                cropped = self._pix.copy(phys)
                cropped.setDevicePixelRatio(self._scale_x)
                global_rect = sel.translated(self._screen_origin)
                self.selected.emit(cropped, global_rect)
            else:
                self.cancelled.emit()

    def _to_physical(self, r: QRect) -> QRect:
        return QRect(
            int(r.x() * self._scale_x), int(r.y() * self._scale_y),
            int(r.width() * self._scale_x), int(r.height() * self._scale_y),
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        src_full = QRectF(self._pix.rect())
        painter.drawPixmap(QRectF(self.rect()), self._pix, src_full)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        if self._origin and self._current:
            sel = QRect(self._origin, self._current).normalized()
            painter.drawPixmap(QRectF(sel), self._pix, QRectF(self._to_physical(sel)))
            painter.setPen(QPen(QColor(255, 100, 50), 2))
            painter.drawRect(sel)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(sel.x() + 4, sel.y() - 6,
                             f"{sel.width()} × {sel.height()}")


class CaptureOverlay(QObject):
    """Creates one overlay per screen and forwards the first selection."""

    captured = pyqtSignal(QPixmap, QRect)
    cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._overlays: list[_ScreenOverlay] = []
        self._done = False
        self._build()

    def _build(self):
        screens = sorted(QGuiApplication.screens(),
                         key=lambda s: (s.geometry().x(), s.geometry().y()))
        grabs = self._grab_per_screen(len(screens))
        for screen, pix in zip(screens, grabs):
            ov = _ScreenOverlay(screen, pix)
            ov.selected.connect(self._on_selected)
            ov.cancelled.connect(self._on_cancelled)
            self._overlays.append(ov)

    def _grab_per_screen(self, n: int) -> list[QPixmap]:
        """Grab each physical monitor; pair to screens by sorted position."""
        with mss.mss() as sct:
            mons = sorted(sct.monitors[1:], key=lambda m: (m["left"], m["top"]))
            out = []
            for m in mons:
                shot = sct.grab(m)
                img = QImage(bytes(shot.bgra), shot.width, shot.height,
                             shot.width * 4, QImage.Format.Format_ARGB32)
                out.append(QPixmap.fromImage(img.copy()))
        # Defensive: if counts differ, pad by repeating the last grab.
        while len(out) < n and out:
            out.append(out[-1])
        return out

    def _on_selected(self, pixmap: QPixmap, global_rect: QRect):
        if self._done:
            return
        self._done = True
        self._close_all()
        self.captured.emit(pixmap, global_rect)

    def _on_cancelled(self):
        if self._done:
            return
        self._done = True
        self._close_all()
        self.cancelled.emit()

    def _close_all(self):
        for ov in self._overlays:
            ov.close()
        self._overlays.clear()
