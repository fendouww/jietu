"""Annotation tools: rect, arrow, text, pen — movable & resizable objects."""
from enum import Enum
from dataclasses import dataclass, field
from PyQt6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QFontMetrics, QPolygonF
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
            w = max(10, fm.horizontalAdvance(self.text or " "))
            h = max(10, fm.height())
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


def draw_arrow(painter: QPainter, p1: QPoint, p2: QPoint):
    """Draw a line with an arrowhead at p2."""
    painter.drawLine(p1, p2)
    dx = p2.x() - p1.x()
    dy = p2.y() - p1.y()
    length = math.hypot(dx, dy)
    if length < 1:
        return
    ux, uy = dx / length, dy / length
    size = 14
    ax = p2.x() - ux * size + uy * size * 0.4
    ay = p2.y() - uy * size - ux * size * 0.4
    bx = p2.x() - ux * size - uy * size * 0.4
    by = p2.y() - uy * size + ux * size * 0.4
    poly = QPolygonF([
        QPointF(p2.x(), p2.y()),
        QPointF(ax, ay),
        QPointF(bx, by),
    ])
    painter.drawPolygon(poly)


def render_annotation(painter: QPainter, ann: Annotation):
    pen = QPen(ann.color, ann.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if ann.tool == Tool.RECT:
        painter.drawRect(QRect(ann.start, ann.end).normalized())

    elif ann.tool == Tool.ARROW:
        painter.setBrush(ann.color)
        draw_arrow(painter, ann.start, ann.end)

    elif ann.tool == Tool.TEXT and ann.text:
        font = QFont("Microsoft YaHei")
        font.setPixelSize(max(8, ann.font_size))
        painter.setFont(font)
        fm = QFontMetrics(font)
        # start is the top-left; draw baseline accordingly
        painter.drawText(ann.start.x(), ann.start.y() + fm.ascent(), ann.text)

    elif ann.tool == Tool.PEN and len(ann.points) > 1:
        for i in range(len(ann.points) - 1):
            painter.drawLine(ann.points[i], ann.points[i + 1])
