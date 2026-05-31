"""Floating pinned screenshot viewer with annotation and translation support."""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QToolBar, QVBoxLayout, QInputDialog,
    QColorDialog, QLabel, QSizeGrip, QApplication
)
from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPixmap, QImage, QColor, QFont, QFontMetrics, QAction, QIcon,
    QCursor, QPen
)
from PIL import Image
import numpy as np

from jietu.annotator import Annotation, Tool, render_annotation
from jietu.translator import TranslateWorker


TOOLBAR_HEIGHT = 36
HANDLE_SIZE = 8


class PinnedViewer(QWidget):
    """Always-on-top floating window showing a captured screenshot."""

    closed = pyqtSignal()

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self._base = pixmap.copy()
        self._annotations: list[Annotation] = []
        self._translations: list[tuple] = []  # (bbox, text, translated)
        self._show_translation = False
        self._pinned = True

        self._tool = Tool.SELECT
        self._color = QColor(255, 50, 50)
        self._pen_width = 2
        self._drawing: Annotation | None = None
        self._drag_offset: QPoint | None = None

        self._worker: TranslateWorker | None = None
        self._translating = False
        self._has_translated = False
        self._status = ""

        self._setup_ui()

    # ── Window setup ────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # _base holds FULL physical-resolution pixels with DPR set.
        # Window must be sized in LOGICAL pixels = physical / dpr.
        dpr = self._base.devicePixelRatio() or 1.0
        lw = round(self._base.width() / dpr)
        lh = round(self._base.height() / dpr)
        self.resize(lw, lh + TOOLBAR_HEIGHT)

        # Toolbar sits at the BOTTOM, below the screenshot canvas.
        self._toolbar = self._build_toolbar()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addStretch(1)            # canvas area (painted manually)
        layout.addWidget(self._toolbar)  # toolbar pinned to bottom

        grip = QSizeGrip(self)
        grip.resize(16, 16)
        grip.move(lw - 16, lh - 16)  # repositioned in resizeEvent

        self.setMinimumSize(80, 60 + TOOLBAR_HEIGHT)

    def _build_toolbar(self) -> QToolBar:
        tb = QToolBar()
        tb.setFixedHeight(TOOLBAR_HEIGHT)
        tb.setIconSize(QSize(18, 18))
        tb.setStyleSheet(
            "QToolBar { background:#222; border:none; spacing:2px; }"
            "QToolButton { color:white; font-size:14px; padding:2px 6px; border:none; }"
            "QToolButton:checked { background:#555; border-radius:3px; }"
            "QToolButton:hover { background:#444; border-radius:3px; }"
        )

        def act(label: str, tip: str, checkable=False):
            a = QAction(label, self)
            a.setToolTip(tip)
            a.setCheckable(checkable)
            return a

        self._act_select = act("↖", "选择/移动", True)
        self._act_rect   = act("□", "矩形", True)
        self._act_arrow  = act("→", "箭头", True)
        self._act_pen    = act("✏", "画笔", True)
        self._act_text   = act("T", "文字", True)

        self._tool_actions = [
            self._act_select, self._act_rect,
            self._act_arrow, self._act_pen, self._act_text,
        ]
        self._act_select.setChecked(True)

        for a in self._tool_actions:
            tb.addAction(a)

        tb.addSeparator()

        self._act_pin = act("📌", "钉住桌面（置顶）", True)
        self._act_pin.setChecked(True)
        act_color  = act("🎨", "颜色")
        act_trans  = act("译", "OCR翻译")
        act_copy   = act("⎘", "复制图片")
        act_close  = act("✕", "关闭")

        tb.addAction(self._act_pin)
        tb.addAction(act_color)
        tb.addAction(act_trans)
        tb.addAction(act_copy)
        tb.addAction(act_close)

        # Connections
        self._act_select.triggered.connect(lambda: self._set_tool(Tool.SELECT))
        self._act_rect.triggered.connect(lambda:   self._set_tool(Tool.RECT))
        self._act_arrow.triggered.connect(lambda:  self._set_tool(Tool.ARROW))
        self._act_pen.triggered.connect(lambda:    self._set_tool(Tool.PEN))
        self._act_text.triggered.connect(lambda:   self._set_tool(Tool.TEXT))
        self._act_pin.triggered.connect(self._toggle_pin)
        act_color.triggered.connect(self._pick_color)
        act_trans.triggered.connect(self._start_translation)
        act_copy.triggered.connect(self._copy_image)
        act_close.triggered.connect(self._on_close)

        return tb

    # ── Tool selection ────────────────────────────────────────────────────

    def _set_tool(self, tool: Tool):
        self._tool = tool
        for a, t in zip(
            self._tool_actions,
            [Tool.SELECT, Tool.RECT, Tool.ARROW, Tool.PEN, Tool.TEXT],
        ):
            a.setChecked(t == tool)
        self.setCursor(
            Qt.CursorShape.ArrowCursor if tool == Tool.SELECT
            else Qt.CursorShape.CrossCursor
        )

    def _toggle_pin(self):
        self._pinned = self._act_pin.isChecked()
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        if self._pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        pos = self.pos()
        self.setWindowFlags(flags)
        self.move(pos)
        self.show()

    def _pick_color(self):
        c = QColorDialog.getColor(self._color, self)
        if c.isValid():
            self._color = c

    # ── Paint ─────────────────────────────────────────────────────────────

    def _canvas_rect(self) -> QRect:
        # Canvas is the top area; toolbar occupies the bottom TOOLBAR_HEIGHT px.
        return QRect(0, 0, self.width(), self.height() - TOOLBAR_HEIGHT)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cr = self._canvas_rect()

        # Map the full-res physical source rect into the logical canvas rect.
        # On a HiDPI backing store Qt renders at full device resolution → crisp.
        painter.drawPixmap(QRectF(cr), self._base, QRectF(self._base.rect()))

        # Annotations are stored in physical-pixel image coords.
        sx = cr.width()  / self._base.width()
        sy = cr.height() / self._base.height()

        painter.translate(cr.topLeft())
        painter.scale(sx, sy)

        # Committed annotations
        for ann in self._annotations:
            render_annotation(painter, ann)

        # Live drawing preview
        if self._drawing:
            render_annotation(painter, self._drawing)

        # Translation overlay
        if self._show_translation and self._translations:
            self._paint_translations(painter)

        painter.end()

        # Status hint (e.g. "翻译中…") drawn in widget (unscaled) coords
        if self._status:
            sp = QPainter(self)
            sp.fillRect(0, 0, self.width(), 26, QColor(0, 0, 0, 160))
            sp.setPen(QColor(255, 255, 255))
            sp.setFont(QFont("Microsoft YaHei", 11))
            sp.drawText(QRect(0, 0, self.width(), 26),
                        Qt.AlignmentFlag.AlignCenter, self._status)
            sp.end()

    def _paint_translations(self, painter: QPainter):
        for (bbox, _orig, translated) in self._translations:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            x, y = int(min(xs)), int(min(ys))
            w = int(max(xs) - min(xs))
            h = int(max(ys) - min(ys))
            if w <= 0 or h <= 0 or not translated:
                continue

            # Semi-transparent white backing: original text faintly shows
            # through for reference, translation stays clearly readable.
            painter.fillRect(x, y, w, h, QColor(255, 255, 255, 150))

            # Size the font so the glyph height matches the ORIGINAL line height,
            # then shrink only if the translation would overflow the box width.
            font = QFont("Microsoft YaHei")
            size = max(8, h)
            font.setPixelSize(size)
            fm = QFontMetrics(font)
            # Match height: reduce until the font's text height fits the box.
            while size > 8 and fm.height() > h:
                size -= 1
                font.setPixelSize(size)
                fm = QFontMetrics(font)
            # Match width: reduce until the translation fits horizontally.
            while size > 8 and fm.horizontalAdvance(translated) > w:
                size -= 1
                font.setPixelSize(size)
                fm = QFontMetrics(font)

            painter.setFont(font)
            painter.setPen(QColor(20, 20, 20))
            # Left-aligned to the original x, vertically centered → same position.
            painter.drawText(
                QRect(x, y, w, h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                translated,
            )

    # ── Mouse events ──────────────────────────────────────────────────────

    def _to_image_coords(self, pos: QPoint) -> QPoint:
        cr = self._canvas_rect()
        sx = self._base.width()  / cr.width()
        sy = self._base.height() / cr.height()
        lp = pos - cr.topLeft()
        return QPoint(int(lp.x() * sx), int(lp.y() * sy))

    def _in_canvas(self, pos: QPoint) -> bool:
        return pos.y() < self.height() - TOOLBAR_HEIGHT

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.pos()
        if not self._in_canvas(pos):
            return

        if self._tool == Tool.SELECT:
            self._drag_offset = pos
            return

        img_pos = self._to_image_coords(pos)

        if self._tool == Tool.TEXT:
            text, ok = QInputDialog.getText(self, "文字标注", "输入文字:")
            if ok and text:
                self._annotations.append(
                    Annotation(Tool.TEXT, QColor(self._color), self._pen_width,
                               start=img_pos, text=text)
                )
                self.update()
            return

        ann = Annotation(self._tool, QColor(self._color), self._pen_width,
                         start=img_pos, end=img_pos)
        if self._tool == Tool.PEN:
            ann.points = [img_pos]
        self._drawing = ann

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self._tool == Tool.SELECT and self._drag_offset:
            delta = pos - self._drag_offset
            self.move(self.pos() + delta)
            return

        if self._drawing:
            img_pos = self._to_image_coords(pos)
            self._drawing.end = img_pos
            if self._tool == Tool.PEN:
                self._drawing.points.append(img_pos)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_offset = None
        if self._drawing:
            self._annotations.append(self._drawing)
            self._drawing = None
            self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._in_canvas(event.pos()):
                # Cancel any in-progress drawing stroke from the first click
                self._drawing = None
                self._drag_offset = None
                self._copy_image()
                self._on_close()
                return
        if event.button() == Qt.MouseButton.RightButton:
            self._on_close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()

    def contextMenuEvent(self, event):
        self._on_close()

    # ── Actions ───────────────────────────────────────────────────────────

    def _copy_image(self):
        pixmap = self._render_flat()
        QApplication.clipboard().setPixmap(pixmap)

    def _render_flat(self) -> QPixmap:
        """Render base + annotations at full physical resolution (DPR=1).

        Annotations are in physical-pixel coords, so painting onto the
        physical-size pixmap with DPR reset to 1 lines them up exactly.
        The result carries every captured pixel — crisp clipboard + best OCR.
        """
        result = self._base.copy()
        result.setDevicePixelRatio(1.0)
        painter = QPainter(result)
        for ann in self._annotations:
            render_annotation(painter, ann)
        painter.end()
        return result

    def _start_translation(self):
        if self._translating:
            return  # already running — ignore extra clicks
        if self._has_translated:
            # Already have results → toggle overlay on/off
            self._show_translation = not self._show_translation
            self.update()
            return

        self._translating = True
        self._status = "翻译中…（首次需加载模型，请稍候）"
        self.update()

        pil_img = self._pixmap_to_pil(self._render_flat())

        self._worker = TranslateWorker(pil_img, target_lang="zh-CN")
        self._worker.finished.connect(self._on_translated)
        self._worker.error.connect(self._on_trans_error)
        self._worker.run()

    @staticmethod
    def _pixmap_to_pil(pixmap: QPixmap) -> Image.Image:
        """Robust QPixmap → PIL.Image (handles row padding & format)."""
        img = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = img.width(), img.height()
        bpl = img.bytesPerLine()
        ptr = img.constBits()
        ptr.setsize(h * bpl)
        arr = np.frombuffer(bytes(ptr), np.uint8).reshape(h, bpl // 4, 4)
        arr = arr[:, :w, :3]  # drop padding columns and alpha → RGB
        return Image.fromarray(arr, "RGB")

    def _on_translated(self, results: list):
        self._translations = results
        self._show_translation = True
        self._translating = False
        self._has_translated = True
        self._status = ""
        if not results:
            self._status = "未识别到文字"
            QTimer.singleShot(2000, self._clear_status)
        self.update()

    def _clear_status(self):
        self._status = ""
        self.update()

    def _on_trans_error(self, msg: str):
        self._translating = False
        self._status = ""
        self.update()
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "翻译失败", msg)

    def _on_close(self):
        self.closed.emit()
        self.close()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep grip at the canvas bottom-right, just above the bottom toolbar.
        for child in self.children():
            if isinstance(child, QSizeGrip):
                child.move(self.width() - 16, self.height() - TOOLBAR_HEIGHT - 16)
