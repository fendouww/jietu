"""Screen region selection and capture."""
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPixmap, QScreen, QGuiApplication, QPen


class CaptureOverlay(QWidget):
    """Full-screen translucent overlay for drag-to-select region capture."""

    captured = pyqtSignal(QPixmap, QRect)
    cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._screen_pixmap = self._grab_all_screens()
        self._setup_window()

    def _grab_all_screens(self) -> QPixmap:
        screens = QGuiApplication.screens()
        total = screens[0].geometry()
        for s in screens[1:]:
            total = total.united(s.geometry())
        return QGuiApplication.primaryScreen().grabWindow(
            0, total.x(), total.y(), total.width(), total.height()
        )

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        screens = QGuiApplication.screens()
        total = screens[0].geometry()
        for s in screens[1:]:
            total = total.united(s.geometry())
        self.setGeometry(total)
        self.showFullScreen()

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
            rect = QRect(self._origin, event.pos()).normalized()
            if rect.width() > 10 and rect.height() > 10:
                cropped = self._screen_pixmap.copy(rect)
                self.captured.emit(cropped, rect)
            else:
                self.cancelled.emit()
            self.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        # Dim the background
        painter.drawPixmap(self.rect(), self._screen_pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        if self._origin and self._current:
            sel = QRect(self._origin, self._current).normalized()
            # Clear selection area (show original)
            painter.drawPixmap(sel, self._screen_pixmap, sel)
            # Draw border
            pen = QPen(QColor(255, 100, 50), 2)
            painter.setPen(pen)
            painter.drawRect(sel)
