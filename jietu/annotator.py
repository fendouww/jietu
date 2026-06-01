"""Annotation tools: rect, arrow, text, pen — movable & resizable objects."""
from enum import Enum
from dataclasses import dataclass, field
from PyQt6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QFont, QFontMetrics, QPolygonF, QPainterPath,
)
import math


class Tool(Enum):
    SELECT = "select"
    RECT = "rect"
    ARROW = "arrow"
    TEXT = "text"
    PEN = "pen"


@dataclass
class Annotation:
    tool: Tool
    color: QColor
    width: int
    start: QPoint = field(default_factory=QPoint)
    end: QPoint = field(default_factory=QPoint)
    text: str = ""
    points: list = field(default_factory=list)   # for pen
    font_size: int = 28                          # for text (image px)

    # ── Geometry ──────────────────────────────────────────────────────────

    def bounds(self) -> QRect:
        if self.tool == Tool.PEN and self.points:
            xs = [p.x() for p in self.points]
            ys = [p.y() for p in self.points]
            return QRect(QPoint(min(xs), min(ys)),
                         QPoint(max(xs), max(ys))).normalized()
        if self.tool == Tool.TEXT:
            font = QFont("Microsoft YaHei")
            font.setPixelSize(max(8, self.font_size))
            fm = QFontMetrics(font)
            lines = (self.text or " ").split("\n")
            w = max(10, max(fm.horizontalAdvance(ln or " ") for ln in lines))
            h = max(10, fm.lineSpacing() * len(lines))
            return QRect(self.start, QSize(w, h))
        return QRect(self.start, self.end).normalized()

    def translate(self, d: QPoint):
        self.start = self.start + d
        self.end = self.end + d
        self.points = [p + d for p in self.points]

    def resize_to(self, new: QRect):
        """Reshape the annotation to fit a new bounding rect."""
        old = self.bounds()
        if old.width() <= 0 or old.height() <= 0:
            return
        if self.tool == Tool.TEXT:
            self.start = new.topLeft()
            self.font_size = max(8, new.height())
            return
        sx = new.width() / old.width()
        sy = new.height() / old.height()

        def mp(p: QPoint) -> QPoint:
            return QPoint(
                round(new.x() + (p.x() - old.x()) * sx),
                round(new.y() + (p.y() - old.y()) * sy),
            )

        self.start = mp(self.start)
        self.end = mp(self.end)
        self.points = [mp(p) for p in self.points]

    def contains(self, p: QPoint, margin: int = 4) -> bool:
        return self.bounds().adjusted(-margin, -margin, margin, margin).contains(p)


def draw_arrow(painter: QPainter, p1: QPoint, p2: QPoint, width: int, color: QColor):
    """Filled teardrop arrow: thin at the tail (p1), thick head at p2."""
    dx = p2.x() - p1.x()
    dy = p2.y() - p1.y()
    L = math.hypot(dx, dy)
    if L < 1:
        return
    ux, uy = dx / L, dy / L           # along the arrow
    nx, ny = -uy, ux                  # perpendicular

    base = max(2.0, float(width))
    head_half = min(max(base * 2.4, L * 0.085), L * 0.32)  # arrowhead half-width
    head_len = min(head_half * 2.4, L * 0.55)              # arrowhead length (long)
    shaft_half = head_half * 0.20                          # shaft half-width (slim)

    # Neck = where the shaft meets the arrowhead
    cx, cy = p2.x() - ux * head_len, p2.y() - uy * head_len

    def P(x, y):
        return QPointF(x, y)

    tail = P(p1.x(), p1.y())
    neck_l = P(cx + nx * shaft_half, cy + ny * shaft_half)
    neck_r = P(cx - nx * shaft_half, cy - ny * shaft_half)
    wing_l = P(cx + nx * head_half, cy + ny * head_half)
    wing_r = P(cx - nx * head_half, cy - ny * head_half)
    tip = P(p2.x(), p2.y())

    # Gentle convex bulge along the shaft → water-drop silhouette
    mx, my = (p1.x() + cx) / 2, (p1.y() + cy) / 2
    ctrl_l = P(mx + nx * shaft_half * 0.9, my + ny * shaft_half * 0.9)
    ctrl_r = P(mx - nx * shaft_half * 0.9, my - ny * shaft_half * 0.9)

    path = QPainterPath()
    path.moveTo(tail)
    path.quadTo(ctrl_l, neck_l)
    path.lineTo(wing_l)
    path.lineTo(tip)
    path.lineTo(wing_r)
    path.lineTo(neck_r)
    path.quadTo(ctrl_r, tail)
    path.closeSubpath()

    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPath(path)
    painter.restore()


def render_annotation(painter: QPainter, ann: Annotation):
    pen = QPen(ann.color, ann.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if ann.tool == Tool.RECT:
        painter.drawRect(QRect(ann.start, ann.end).normalized())

    elif ann.tool == Tool.ARROW:
        draw_arrow(painter, ann.start, ann.end, ann.width, ann.color)

    elif ann.tool == Tool.TEXT and ann.text:
        font = QFont("Microsoft YaHei")
        font.setPixelSize(max(8, ann.font_size))
        painter.setFont(font)
        fm = QFontMetrics(font)
        # start is the top-left; draw each line (multi-line support)
        ls = fm.lineSpacing()
        for i, line in enumerate(ann.text.split("\n")):
            painter.drawText(ann.start.x(),
                             ann.start.y() + fm.ascent() + i * ls, line)

    elif ann.tool == Tool.PEN and len(ann.points) > 1:
        for i in range(len(ann.points) - 1):
            painter.drawLine(ann.points[i], ann.points[i + 1])
