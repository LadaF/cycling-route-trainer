"""Small building blocks of the ride window."""

from __future__ import annotations

import statistics
from collections import deque

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

# The widest value a tile shows: all tiles size their value for it, so they match
# and the size does not change with the numbers.
VALUE_REFERENCE = "188.88 km"


class Rolling:
    """Values over the last ``window`` seconds, for display smoothing only."""

    def __init__(self, window: float) -> None:
        self.window = window
        self._values: deque[tuple[float, float]] = deque()

    def add(self, t: float, value: float | None) -> None:
        if value is not None:
            self._values.append((t, value))
        while self._values and self._values[0][0] < t - self.window:
            self._values.popleft()

    def clear(self) -> None:
        self._values.clear()

    def mean(self) -> float | None:
        return statistics.fmean(v for _t, v in self._values) if self._values else None

    def median(self) -> float | None:
        return statistics.median(v for _t, v in self._values) if self._values else None


class MetricTile(QFrame):
    """A caption and a small detail line above a large value. The value is as large
    as the tile allows, so it grows with the window."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._caption = QLabel(caption)
        self._value = QLabel("–")
        self._detail = QLabel("")
        font = self._value.font()
        font.setBold(True)
        self._value.setFont(font)
        # The value takes the space left by the other lines; its font follows the tile
        # size, so its size hint must not hold the tile (and the window) large.
        self._value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        for label in (self._caption, self._detail):
            label.setStyleSheet("color: palette(placeholder-text)")
        # The caption and the detail share the top line, leaving the value more height.
        top = QHBoxLayout()
        top.addWidget(self._caption)
        top.addStretch()
        top.addWidget(self._detail)
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(self._value, stretch=1)
        self._fit_value()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_value()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:  # the text zoom changes the small lines
            self._fit_value()

    def _fit_value(self) -> None:
        margins = self.layout().contentsMargins()
        frame = 2 * self.frameWidth()
        width = self.width() - margins.left() - margins.right() - frame
        line = self.fontMetrics().lineSpacing()  # the caption and the detail
        height = self.height() - margins.top() - margins.bottom() - frame - line
        font = self._value.font()
        font.setPixelSize(100)
        metrics = QFontMetricsF(font)
        scale = min(width / metrics.horizontalAdvance(VALUE_REFERENCE), height / metrics.height())
        font.setPixelSize(max(8, int(100 * scale * 0.95)))
        self._value.setFont(font)

    def set(self, value: str, detail: str = "", *, highlight: bool = False) -> None:
        self._value.setText(value)
        self._detail.setText(detail)
        self._detail.setStyleSheet(
            "color: #d97706; font-weight: bold" if highlight else "color: palette(placeholder-text)"
        )
