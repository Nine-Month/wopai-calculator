"""Solve progress bar: full-width indeterminate stripes; fixed 4mm / 45° determinate fill."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget


_DARK = QColor("#0d6efd")
_LIGHT = QColor("#3d8bfd")
_TRACK = QColor(22, 32, 48, 235)
_BORDER = QColor(13, 110, 253, 115)
_TEXT = QColor("#ffffff")
_STRIPE_MM = 4.0


def _mm_to_px(mm: float) -> float:
    screen = QGuiApplication.primaryScreen()
    dpi = float(screen.logicalDotsPerInchX()) if screen else 96.0
    return max(6.0, mm * dpi / 25.4)


class SolveProgressBar(QWidget):
    """Stripes use absolute geometry so fill growth never stretches the pattern."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SolveProgress")
        self.setMinimumHeight(22)
        self.setMaximumHeight(24)
        self._value = 0
        self._maximum = 100
        self._indeterminate = False
        self._format = ""
        self._phase = 0.0

    def setRange(self, minimum: int, maximum: int) -> None:
        if maximum <= minimum:
            self._indeterminate = True
            self._maximum = 100
        else:
            self._indeterminate = False
            self._maximum = max(1, int(maximum))
            self._value = max(0, min(self._maximum, self._value))
        self.update()

    def setValue(self, value: int) -> None:
        self._indeterminate = False
        self._value = max(0, min(self._maximum, int(value)))
        self.update()

    def setFormat(self, fmt: str) -> None:
        self._format = fmt or ""
        self.update()

    def value(self) -> int:
        return int(self._value)

    def advance_phase(self, step_px: float = 2.0) -> None:
        period = _mm_to_px(_STRIPE_MM) * 2.0
        self._phase = (self._phase + step_px) % period
        self.update()

    def reset_phase(self) -> None:
        self._phase = 0.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        if w < 2 or h < 2:
            p.end()
            return

        radius = 4.0
        track = QRectF(0.5, 0.5, w - 1.0, h - 1.0)

        p.setPen(QPen(_BORDER, 1.0))
        p.setBrush(_TRACK)
        p.drawRoundedRect(track, radius, radius)

        if self._indeterminate:
            fill_w = w - 1.0
        else:
            frac = float(self._value) / float(self._maximum) if self._maximum else 0.0
            fill_w = max(0.0, min(w - 1.0, (w - 1.0) * frac))

        if fill_w > 0.5:
            fill_rect = QRectF(0.5, 0.5, fill_w, h - 1.0)
            clip = QPainterPath()
            clip.addRoundedRect(track, radius, radius)
            p.setClipPath(clip)
            p.setClipRect(fill_rect, Qt.IntersectClip)
            self._paint_stripes(p, w, h)
            p.setClipping(False)

        if self._format:
            p.setPen(_TEXT)
            font = QFont(self.font())
            font.setPointSize(10)
            font.setWeight(QFont.DemiBold)
            p.setFont(font)
            p.drawText(track, int(Qt.AlignCenter), self._format)

        p.end()

    def _paint_stripes(self, p: QPainter, w: float, h: float) -> None:
        """Bootstrap-like hard 45° bands; band width fixed (~4mm); phase scrolls."""
        stripe_w = _mm_to_px(_STRIPE_MM)
        phase = self._phase
        # Top-edge band [x, x+stripe_w]; bottom shifted by -h → 45° edges
        x = -h - stripe_w * 2 - (phase % (stripe_w * 2))
        while x < w + h + stripe_w * 2:
            which = int(math.floor((x + phase) / stripe_w)) & 1
            color = _LIGHT if which == 0 else _DARK
            path = QPainterPath()
            path.moveTo(QPointF(x, 0.0))
            path.lineTo(QPointF(x + stripe_w + 0.5, 0.0))  # slight overlap, no gap
            path.lineTo(QPointF(x + stripe_w + 0.5 - h, h))
            path.lineTo(QPointF(x - h, h))
            path.closeSubpath()
            p.fillPath(path, color)
            x += stripe_w
