"""Annotation tools: rect, arrow, text, pen."""
from enum import Enum
from dataclasses import dataclass, field
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QPolygonF
from PyQt6.QtCore import QPointF
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
    points: list = field(default_factory=list)  # for pen


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
        font = QFont("Arial", max(12, ann.width * 4))
        painter.setFont(font)
        painter.drawText(ann.start, ann.text)

    elif ann.tool == Tool.PEN and len(ann.points) > 1:
        for i in range(len(ann.points) - 1):
            painter.drawLine(ann.points[i], ann.points[i + 1])
