"""Screen region selection and capture.

One overlay window PER screen — a single window cannot span multiple displays
on macOS ("Displays have separate Spaces"), and per-screen overlays also handle
mixed-DPI setups correctly (each screen uses its own grab and scale).

After the first drag, the selection stays on screen so the user can move or
resize it; double-click inside the box or press Enter to confirm.
"""
from __future__ import annotations
import sys
import mss
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, QObject, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPixmap, QImage, QGuiApplication, QPen


HANDLE = 8   # handle hit radius in logical px


class _ScreenOverlay(QWidget):
    """Translucent overlay covering ONE screen; drag to select a region."""

    selected = pyqtSignal(QPixmap, QRect)   # cropped pixmap, GLOBAL logical rect
    cancelled = pyqtSignal()
    adjusting = pyqtSignal(object)          # self — other screens should close

    def __init__(self, screen, pixmap: QPixmap):
        super().__init__()
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._sel: QRect | None = None        # confirmed selection (adjust mode)
        self._mode = "drag"                 # 'drag' | 'adjust'
        self._interaction: str | None = None
        self._press_pos: QPoint | None = None
        self._resize_idx = -1
        self._orig_sel: QRect | None = None
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

    # ── Selection helpers ─────────────────────────────────────────────────

    def _active_sel(self) -> QRect | None:
        if self._mode == "adjust" and self._sel is not None:
            return self._sel
        if self._origin and self._current:
            return QRect(self._origin, self._current).normalized()
        return None

    def _handle_points(self, sel: QRect) -> list[QPoint]:
        return [
            sel.topLeft(), sel.topRight(), sel.bottomRight(), sel.bottomLeft(),
            QPoint(sel.center().x(), sel.top()),
            QPoint(sel.right(), sel.center().y()),
            QPoint(sel.center().x(), sel.bottom()),
            QPoint(sel.left(), sel.center().y()),
        ]

    def _hit_handle(self, pos: QPoint, sel: QRect) -> int:
        for i, hp in enumerate(self._handle_points(sel)):
            if QRect(hp.x() - HANDLE, hp.y() - HANDLE,
                     HANDLE * 2, HANDLE * 2).contains(pos):
                return i
        return -1

    def _resized_rect(self, old: QRect, now: QPoint) -> QRect:
        l, t, r, b = old.left(), old.top(), old.right(), old.bottom()
        idx = self._resize_idx
        if idx == 0:
            l, t = now.x(), now.y()
        elif idx == 1:
            r, t = now.x(), now.y()
        elif idx == 2:
            r, b = now.x(), now.y()
        elif idx == 3:
            l, b = now.x(), now.y()
        elif idx == 4:
            t = now.y()
        elif idx == 5:
            r = now.x()
        elif idx == 6:
            b = now.y()
        elif idx == 7:
            l = now.x()
        rect = QRect(QPoint(l, t), QPoint(r, b)).normalized()
        return rect.intersected(self.rect())

    def _confirm_selection(self):
        sel = self._sel
        if sel is None or sel.width() <= 5 or sel.height() <= 5:
            return
        phys = self._to_physical(sel)
        cropped = self._pix.copy(phys)
        cropped.setDevicePixelRatio(self._scale_x)
        global_rect = sel.translated(self._screen_origin)
        self.selected.emit(cropped, global_rect)

    def _enter_adjust(self, sel: QRect):
        self._sel = sel
        self._mode = "adjust"
        self._origin = None
        self._current = None
        self.adjusting.emit(self)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    # ── Events ────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._mode == "adjust":
                self._confirm_selection()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.pos()

        if self._mode == "adjust" and self._sel is not None:
            self._press_pos = pos
            hi = self._hit_handle(pos, self._sel)
            if hi >= 0:
                self._interaction = "resize"
                self._resize_idx = hi
                self._orig_sel = QRect(self._sel)
                return
            if self._sel.contains(pos):
                self._interaction = "move"
                return
            # Click outside → start a fresh drag selection.
            self._mode = "drag"
            self._sel = None
            self.setCursor(Qt.CursorShape.CrossCursor)

        self._origin = pos
        self._current = pos

    def mouseMoveEvent(self, event):
        pos = event.pos()

        if self._mode == "adjust" and self._interaction and self._sel is not None:
            if self._interaction == "move" and self._press_pos is not None:
                delta = pos - self._press_pos
                self._sel = self._sel.translated(delta).intersected(self.rect())
                self._press_pos = pos
            elif self._interaction == "resize" and self._orig_sel is not None:
                self._sel = self._resized_rect(self._orig_sel, pos)
            self.update()
            return

        if self._origin:
            self._current = pos
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._mode == "adjust":
            self._interaction = None
            self._resize_idx = -1
            self._orig_sel = None
            self._press_pos = None
            return

        if not self._origin:
            return
        sel = QRect(self._origin, event.pos()).normalized()
        self._origin = None
        self._current = None
        if sel.width() > 5 and sel.height() > 5:
            self._enter_adjust(sel)
        else:
            self.cancelled.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._mode == "adjust" and self._sel is not None:
            if self._sel.contains(event.pos()):
                self._confirm_selection()

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

        sel = self._active_sel()
        if sel is None or sel.width() <= 0 or sel.height() <= 0:
            return

        painter.drawPixmap(QRectF(sel), self._pix, QRectF(self._to_physical(sel)))
        painter.setPen(QPen(QColor(255, 100, 50), 2))
        painter.drawRect(sel)

        if self._mode == "adjust":
            painter.setBrush(QColor(255, 255, 255))
            painter.setPen(QPen(QColor(255, 100, 50), 1))
            for hp in self._handle_points(sel):
                painter.drawRect(hp.x() - HANDLE // 2, hp.y() - HANDLE // 2,
                                 HANDLE, HANDLE)

        painter.setPen(QColor(255, 255, 255))
        hint = "双击确认 · Enter" if self._mode == "adjust" else ""
        painter.drawText(sel.x() + 4, max(14, sel.y() - 6),
                         f"{sel.width()} × {sel.height()}  {hint}".strip())


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
        # macOS: native Quartz grab guarantees full Retina resolution (mss can
        # return 1x on Retina → blurry). Other platforms: mss per-monitor.
        pairs = None
        if sys.platform == "darwin":
            pairs = self._grab_mac_quartz()
        if not pairs:
            screens = sorted(QGuiApplication.screens(),
                             key=lambda s: (s.geometry().x(), s.geometry().y()))
            grabs = self._grab_per_screen(len(screens))
            pairs = list(zip(screens, grabs))
        for screen, pix in pairs:
            ov = _ScreenOverlay(screen, pix)
            ov.selected.connect(self._on_selected)
            ov.cancelled.connect(self._on_cancelled)
            ov.adjusting.connect(self._on_adjusting)
            self._overlays.append(ov)

    def _on_adjusting(self, active: _ScreenOverlay):
        """Keep only the overlay that is adjusting its selection."""
        for ov in self._overlays:
            if ov is not active:
                ov.close()
        self._overlays = [active]

    def _grab_mac_quartz(self):
        """Per-display capture via Quartz CGDisplayCreateImage (full Retina)."""
        try:
            import Quartz
        except Exception:
            return None
        try:
            err, ids, _ = Quartz.CGGetActiveDisplayList(16, None, None)
            if err or not ids:
                return None
        except Exception:
            return None

        screens = QGuiApplication.screens()
        pairs = []
        for did in ids:
            img = Quartz.CGDisplayCreateImage(did)
            if img is None:
                continue
            w = Quartz.CGImageGetWidth(img)
            h = Quartz.CGImageGetHeight(img)
            bpr = Quartz.CGImageGetBytesPerRow(img)
            provider = Quartz.CGImageGetDataProvider(img)
            data = Quartz.CGDataProviderCopyData(provider)
            qimg = QImage(bytes(data), w, h, bpr,
                          QImage.Format.Format_ARGB32).copy()
            pix = QPixmap.fromImage(qimg)
            b = Quartz.CGDisplayBounds(did)
            screen = self._match_screen(screens, int(b.origin.x), int(b.origin.y))
            if screen is not None:
                pairs.append((screen, pix))
        return pairs or None

    @staticmethod
    def _match_screen(screens, x: int, y: int):
        best, best_d = None, None
        for s in screens:
            g = s.geometry()
            d = abs(g.x() - x) + abs(g.y() - y)
            if best_d is None or d < best_d:
                best, best_d = s, d
        return best

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
