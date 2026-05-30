"""Floating pinned screenshot viewer with annotation and translation support."""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QToolBar, QVBoxLayout, QInputDialog,
    QColorDialog, QLabel, QSizeGrip, QApplication
)
from PyQt6.QtCore import Qt, QPoint, QRect, QSize, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPixmap, QColor, QFont, QAction, QIcon,
    QCursor, QPen
)
from PIL import Image
import io

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

        self._setup_ui()

    # ── Window setup ────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        # Window must be sized in LOGICAL pixels.
        # _base is stored as physical pixels with DPR set.
        dpr = self._base.devicePixelRatio()
        lw = int(self._base.width() / dpr)
        lh = int(self._base.height() / dpr)
        self.resize(lw, lh + TOOLBAR_HEIGHT)

        self._toolbar = self._build_toolbar()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toolbar)

        grip = QSizeGrip(self)
        grip.resize(16, 16)
        grip.move(lw - 16, lh + TOOLBAR_HEIGHT - 16)

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
        return QRect(0, TOOLBAR_HEIGHT, self.width(), self.height() - TOOLBAR_HEIGHT)

    def paintEvent(self, _event):
        painter = QPainter(self)
        cr = self._canvas_rect()

        # Scale base image to current widget size
        scaled = self._base.scaled(
            cr.width(), cr.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(cr.topLeft(), scaled)

        # Scale factor for annotations (original coords → display coords)
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

    def _paint_translations(self, painter: QPainter):
        for (bbox, _orig, translated) in self._translations:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            x, y = int(min(xs)), int(min(ys))
            w = int(max(xs) - min(xs))
            h = int(max(ys) - min(ys))

            # White background block
            painter.fillRect(x, y, w, h, QColor(255, 255, 255, 230))

            font_size = max(10, int(h * 0.65))
            font = QFont("Microsoft YaHei, Arial", font_size)
            painter.setFont(font)
            painter.setPen(QColor(20, 20, 20))
            painter.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter, translated)

    # ── Mouse events ──────────────────────────────────────────────────────

    def _to_image_coords(self, pos: QPoint) -> QPoint:
        cr = self._canvas_rect()
        sx = self._base.width()  / cr.width()
        sy = self._base.height() / cr.height()
        lp = pos - cr.topLeft()
        return QPoint(int(lp.x() * sx), int(lp.y() * sy))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.pos()
        if pos.y() < TOOLBAR_HEIGHT:
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
            if event.pos().y() >= TOOLBAR_HEIGHT:
                # Cancel any in-progress drawing stroke from the first click
                self._drawing = None
                self._drag_offset = None
                self._copy_image()
                self._on_close()
                return
        if event.button() == Qt.MouseButton.RightButton:
            self._on_close()

    def contextMenuEvent(self, event):
        self._on_close()

    # ── Actions ───────────────────────────────────────────────────────────

    def _copy_image(self):
        pixmap = self._render_flat()
        QApplication.clipboard().setPixmap(pixmap)

    def _render_flat(self) -> QPixmap:
        """Render base + annotations into one pixmap (physical pixel coords)."""
        dpr = self._base.devicePixelRatio()
        result = self._base.copy()
        # Reset DPR so QPainter works in physical pixel coordinates,
        # matching the physical pixel coords stored in annotations.
        result.setDevicePixelRatio(1.0)
        painter = QPainter(result)
        for ann in self._annotations:
            render_annotation(painter, ann)
        painter.end()
        result.setDevicePixelRatio(dpr)
        return result

    def _start_translation(self):
        if self._translating:
            # Toggle display if already done
            self._show_translation = not self._show_translation
            self.update()
            return

        self._translating = True
        flat = self._render_flat()

        # Convert QPixmap → PIL Image
        buf = flat.toImage()
        buf.save("/tmp/_jietu_ocr.png") if False else None
        arr = buf.bits().asarray(buf.width() * buf.height() * 4)
        pil_img = Image.frombytes(
            "RGBA", (buf.width(), buf.height()), bytes(arr), "raw", "BGRA"
        ).convert("RGB")

        self._worker = TranslateWorker(pil_img, target_lang="zh-CN")
        self._worker.finished.connect(self._on_translated)
        self._worker.error.connect(self._on_trans_error)
        self._worker.run()

    def _on_translated(self, results: list):
        self._translations = results
        self._show_translation = True
        self._translating = False
        self.update()

    def _on_trans_error(self, msg: str):
        self._translating = False
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "翻译失败", msg)

    def _on_close(self):
        self.closed.emit()
        self.close()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Relocate grip
        for child in self.children():
            if isinstance(child, QSizeGrip):
                child.move(self.width() - 16, self.height() - 16)
