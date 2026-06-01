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

        self._tool = Tool.SELECT
        self._color = QColor(255, 50, 50)
        self._pen_width = 2
        self._drawing: Annotation | None = None
        self._drag_offset: QPoint | None = None
        self._win_drag_offset: QPoint | None = None

        # Selection / editing state
        self._selected: Annotation | None = None
        self._interaction = None          # 'move' | 'resize' | 'window' | None
        self._resize_idx = -1             # which corner handle (0..3)
        self._press_widget: QPoint | None = None
        self._orig_bounds: QRect | None = None
        self._editor = None               # inline QLineEdit while typing text
        self._editing_ann: Annotation | None = None

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

        act_color  = act("🎨", "颜色")
        act_trans  = act("译", "OCR翻译")
        act_save   = act("💾", "保存为图片")
        act_close  = act("✕", "关闭")

        tb.addAction(act_color)
        tb.addAction(act_trans)
        tb.addAction(act_save)
        tb.addAction(act_close)

        # Connections
        self._act_select.triggered.connect(lambda: self._set_tool(Tool.SELECT))
        self._act_rect.triggered.connect(lambda:   self._set_tool(Tool.RECT))
        self._act_arrow.triggered.connect(lambda:  self._set_tool(Tool.ARROW))
        self._act_pen.triggered.connect(lambda:    self._set_tool(Tool.PEN))
        self._act_text.triggered.connect(lambda:   self._set_tool(Tool.TEXT))
        act_color.triggered.connect(self._pick_color)
        act_trans.triggered.connect(self._start_translation)
        act_save.triggered.connect(self._save_image)
        act_close.triggered.connect(self._on_close)

        return tb

    # ── Tool selection ────────────────────────────────────────────────────

    def _set_tool(self, tool: Tool):
        self._commit_editor()
        self._tool = tool
        if tool != Tool.SELECT:
            self._selected = None      # hide handles while drawing
        for a, t in zip(
            self._tool_actions,
            [Tool.SELECT, Tool.RECT, Tool.ARROW, Tool.PEN, Tool.TEXT],
        ):
            a.setChecked(t == tool)
        self.setCursor(
            Qt.CursorShape.ArrowCursor if tool == Tool.SELECT
            else Qt.CursorShape.CrossCursor
        )
        self.update()

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

        # Selection overlay (bounding box + corner handles) in widget coords
        if self._selected is not None and self._editor is None:
            sp = QPainter(self)
            wb = self._img_rect_to_widget(self._selected.bounds())
            sp.setPen(QPen(QColor(0, 140, 255), 1, Qt.PenStyle.DashLine))
            sp.setBrush(Qt.BrushStyle.NoBrush)
            sp.drawRect(wb)
            sp.setPen(QPen(QColor(0, 140, 255), 1))
            sp.setBrush(QColor(255, 255, 255))
            for hr in self._handle_rects(self._selected):
                sp.drawRect(hr)
            sp.end()

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
        base_img = self._base.toImage()
        for (bbox, _orig, translated) in self._translations:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            x, y = int(min(xs)), int(min(ys))
            w = int(max(xs) - min(xs))
            h = int(max(ys) - min(ys))
            if w <= 0 or h <= 0 or not translated:
                continue

            # Fill with the ORIGINAL background color sampled from the image
            # around this text → blends in and fully hides the source text.
            bg = self._sample_bg(base_img, x, y, w, h)
            painter.fillRect(x, y, w, h, bg)

            # Pick a text color that contrasts with that background.
            lum = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
            text_color = QColor(30, 30, 30) if lum > 140 else QColor(235, 235, 235)

            # Size the font so the glyph height matches the ORIGINAL line height,
            # then shrink only if the translation would overflow the box width.
            font = QFont("Microsoft YaHei")
            size = max(8, h)
            font.setPixelSize(size)
            fm = QFontMetrics(font)
            while size > 8 and fm.height() > h:
                size -= 1
                font.setPixelSize(size)
                fm = QFontMetrics(font)
            while size > 8 and fm.horizontalAdvance(translated) > w:
                size -= 1
                font.setPixelSize(size)
                fm = QFontMetrics(font)

            painter.setFont(font)
            painter.setPen(text_color)
            # Left-aligned to the original x, vertically centered → same position.
            painter.drawText(
                QRect(x, y, w, h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                translated,
            )

    @staticmethod
    def _sample_bg(img: QImage, x: int, y: int, w: int, h: int) -> QColor:
        """Estimate the background color from the border pixels of a box.

        Text usually sits in the center, so the box perimeter is mostly
        background. Take the median of perimeter samples for robustness.
        """
        iw, ih = img.width(), img.height()
        pts = []
        sx = max(1, w // 10)
        sy = max(1, h // 6)
        for px in range(x, x + w, sx):
            pts.append((px, y))
            pts.append((px, y + h - 1))
        for py in range(y, y + h, sy):
            pts.append((x, py))
            pts.append((x + w - 1, py))

        rs, gs, bs = [], [], []
        for px, py in pts:
            if 0 <= px < iw and 0 <= py < ih:
                c = img.pixelColor(px, py)
                rs.append(c.red()); gs.append(c.green()); bs.append(c.blue())
        if not rs:
            return QColor(255, 255, 255)
        rs.sort(); gs.sort(); bs.sort()
        m = len(rs) // 2
        return QColor(rs[m], gs[m], bs[m])

    # ── Mouse events ──────────────────────────────────────────────────────

    def _to_image_coords(self, pos: QPoint) -> QPoint:
        cr = self._canvas_rect()
        sx = self._base.width()  / cr.width()
        sy = self._base.height() / cr.height()
        lp = pos - cr.topLeft()
        return QPoint(int(lp.x() * sx), int(lp.y() * sy))

    def _img_to_widget(self, p: QPoint) -> QPoint:
        cr = self._canvas_rect()
        sx = cr.width()  / self._base.width()
        sy = cr.height() / self._base.height()
        return QPoint(int(cr.x() + p.x() * sx), int(cr.y() + p.y() * sy))

    def _img_rect_to_widget(self, r: QRect) -> QRect:
        return QRect(self._img_to_widget(r.topLeft()),
                     self._img_to_widget(r.bottomRight()))

    def _handle_rects(self, ann: Annotation) -> list[QRect]:
        """4 corner handles (widget coords) for the selected annotation."""
        wb = self._img_rect_to_widget(ann.bounds())
        s = HANDLE_SIZE
        corners = [wb.topLeft(), wb.topRight(), wb.bottomRight(), wb.bottomLeft()]
        return [QRect(c.x() - s // 2, c.y() - s // 2, s, s) for c in corners]

    def _hit_annotation(self, img_pos: QPoint) -> Annotation | None:
        """Topmost annotation under the point (image coords), or None."""
        for ann in reversed(self._annotations):
            if ann.contains(img_pos):
                return ann
        return None

    def _in_canvas(self, pos: QPoint) -> bool:
        return pos.y() < self.height() - TOOLBAR_HEIGHT

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.pos()
        if not self._in_canvas(pos):
            return
        self._commit_editor()  # finish any in-progress text edit

        if self._tool == Tool.SELECT:
            self._press_widget = pos
            # 1) resize handle of the current selection?
            if self._selected is not None:
                for i, hr in enumerate(self._handle_rects(self._selected)):
                    if hr.contains(pos):
                        self._interaction = "resize"
                        self._resize_idx = i
                        self._orig_bounds = self._selected.bounds()
                        return
            # 2) hit an annotation → select & move it
            hit = self._hit_annotation(self._to_image_coords(pos))
            if hit is not None:
                self._selected = hit
                self._interaction = "move"
                self.update()
                return
            # 3) empty area → deselect & drag the window.
            # Use GLOBAL coords: the window moves under the cursor, so widget-
            # local coords would shift each event and lag behind the cursor.
            self._selected = None
            self._interaction = "window"
            self._win_drag_offset = event.globalPosition().toPoint() - self.pos()
            self.update()
            return

        img_pos = self._to_image_coords(pos)

        if self._tool == Tool.TEXT:
            self._open_text_editor(pos, img_pos)
            return

        ann = Annotation(self._tool, QColor(self._color), self._pen_width,
                         start=img_pos, end=img_pos)
        if self._tool == Tool.PEN:
            ann.points = [img_pos]
        self._drawing = ann

    def mouseMoveEvent(self, event):
        pos = event.pos()

        if self._tool == Tool.SELECT and self._interaction:
            if self._interaction == "window" and self._win_drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._win_drag_offset)
                return
            if self._selected is None or self._press_widget is None:
                return
            img_now = self._to_image_coords(pos)
            img_press = self._to_image_coords(self._press_widget)
            if self._interaction == "move":
                self._selected.translate(img_now - img_press)
                self._press_widget = pos
            elif self._interaction == "resize" and self._orig_bounds:
                self._selected.resize_to(
                    self._resized_rect(self._orig_bounds, img_now))
            self.update()
            return

        if self._drawing:
            img_pos = self._to_image_coords(pos)
            self._drawing.end = img_pos
            if self._tool == Tool.PEN:
                self._drawing.points.append(img_pos)
            self.update()

    def _resized_rect(self, old: QRect, img_now: QPoint) -> QRect:
        """New bounds when dragging corner _resize_idx to img_now."""
        l, t, r, b = old.left(), old.top(), old.right(), old.bottom()
        if self._resize_idx == 0:      # top-left
            l, t = img_now.x(), img_now.y()
        elif self._resize_idx == 1:    # top-right
            r, t = img_now.x(), img_now.y()
        elif self._resize_idx == 2:    # bottom-right
            r, b = img_now.x(), img_now.y()
        elif self._resize_idx == 3:    # bottom-left
            l, b = img_now.x(), img_now.y()
        rect = QRect(QPoint(l, t), QPoint(r, b)).normalized()
        if rect.width() < 6:
            rect.setWidth(6)
        if rect.height() < 6:
            rect.setHeight(6)
        return rect

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_offset = None
        self._win_drag_offset = None
        self._interaction = None
        self._resize_idx = -1
        self._orig_bounds = None
        if self._drawing:
            self._annotations.append(self._drawing)
            self._selected = self._drawing
            self._drawing = None
            self.update()

    def wheelEvent(self, event):
        """Scroll over the image zooms the screenshot, anchored at the cursor."""
        p = event.position().toPoint()
        if not self._in_canvas(p) or self._editor is not None:
            return
        cr = self._canvas_rect()
        if cr.width() <= 0 or cr.height() <= 0:
            return

        factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
        # Fraction of the image under the cursor (kept fixed across the zoom).
        fx = max(0.0, min(1.0, p.x() / cr.width()))
        fy = max(0.0, min(1.0, p.y() / cr.height()))
        g = event.globalPosition().toPoint()

        aspect = self._base.height() / self._base.width()
        new_w = int(cr.width() * factor)
        new_w = max(60, min(new_w, self._base.width() * 4))
        new_h = max(40, int(new_w * aspect))

        self.resize(new_w, new_h + TOOLBAR_HEIGHT)
        # Move so the same image point stays under the cursor.
        self.move(int(g.x() - fx * new_w), int(g.y() - fy * new_h))
        self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._in_canvas(event.pos()):
            img_pos = self._to_image_coords(event.pos())
            hit = self._hit_annotation(img_pos)
            if hit is not None and hit.tool == Tool.TEXT:
                # Edit this text in place
                self._edit_text_annotation(hit)
                return
            # Empty area → copy and close
            self._drawing = None
            self._interaction = None
            self._copy_image()
            self._on_close()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._on_close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self._editor is not None:
                self._cancel_editor()
                return
            self._on_close()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected is not None and self._editor is None:
                if self._selected in self._annotations:
                    self._annotations.remove(self._selected)
                self._selected = None
                self.update()

    # ── Inline text editing ────────────────────────────────────────────────

    def _open_text_editor(self, widget_pos: QPoint, img_pos: QPoint,
                          existing: Annotation | None = None):
        from PyQt6.QtWidgets import QLineEdit
        cr = self._canvas_rect()
        sx = cr.width() / self._base.width()   # image→widget scale

        if existing is not None:
            self._editing_ann = existing
            img_pos = existing.start
            widget_pos = self._img_to_widget(existing.start)
            font_px_img = existing.font_size
            text = existing.text
            color = existing.color
        else:
            self._editing_ann = None
            font_px_img = max(16, self._pen_width * 10)
            text = ""
            color = QColor(self._color)

        self._editor_img_pos = img_pos
        self._editor_font_img = font_px_img
        self._editor_color = color

        ed = QLineEdit(self)
        ed.setText(text)
        widget_font = max(10, int(font_px_img * sx))
        ed.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,40); border: 1px dashed "
            f"rgba(0,0,0,120); color: {color.name()}; "
            f"font-family: 'Microsoft YaHei'; font-size: {widget_font}px; padding:0; }}"
        )
        ed.move(widget_pos)
        ed.resize(max(120, int(len(text) * widget_font * 0.7) + 40), widget_font + 10)
        ed.returnPressed.connect(self._commit_editor)
        ed.show()
        ed.setFocus()
        self._editor = ed

    def _edit_text_annotation(self, ann: Annotation):
        # Temporarily remove from list while editing; recommit on finish.
        if ann in self._annotations:
            self._annotations.remove(ann)
        self._selected = None
        self.update()
        self._open_text_editor(self._img_to_widget(ann.start), ann.start, existing=ann)

    def _commit_editor(self):
        if self._editor is None:
            return
        text = self._editor.text().strip()
        ed = self._editor
        self._editor = None
        ed.deleteLater()
        if text:
            ann = Annotation(
                Tool.TEXT, QColor(self._editor_color), self._pen_width,
                start=self._editor_img_pos, text=text,
                font_size=self._editor_font_img,
            )
            self._annotations.append(ann)
            self._selected = ann
        self._editing_ann = None
        self.update()

    def _cancel_editor(self):
        if self._editor is None:
            return
        ed = self._editor
        self._editor = None
        ed.deleteLater()
        # Restore the original annotation if we were editing one
        if self._editing_ann is not None:
            self._annotations.append(self._editing_ann)
        self._editing_ann = None
        self.update()

    def contextMenuEvent(self, event):
        self._on_close()

    # ── Actions ───────────────────────────────────────────────────────────

    def _copy_image(self):
        self._commit_editor()
        pixmap = self._render_flat()
        QApplication.clipboard().setPixmap(pixmap)

    def _save_image(self):
        self._commit_editor()
        from PyQt6.QtWidgets import QFileDialog
        from PyQt6.QtCore import QStandardPaths, QDateTime
        pics = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation) or ""
        stamp = QDateTime.currentDateTime().toString("yyyyMMdd_HHmmss")
        default = f"{pics}/jietu_{stamp}.png" if pics else f"jietu_{stamp}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存为图片", default,
            "PNG 图片 (*.png);;JPEG 图片 (*.jpg);;所有文件 (*.*)",
        )
        if not path:
            return
        pixmap = self._render_flat()
        if not pixmap.save(path):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "保存失败", f"无法保存到：\n{path}")

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
