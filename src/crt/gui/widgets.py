"""Small building blocks of the ride window."""

from __future__ import annotations

import statistics
from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


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
    """A caption, a large value and a small line under it."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._caption = QLabel(caption)
        self._value = QLabel("–")
        self._detail = QLabel("")
        font = self._value.font()
        font.setPointSizeF(font.pointSizeF() * 2.6)
        font.setBold(True)
        self._value.setFont(font)
        for label in (self._caption, self._detail):
            label.setStyleSheet("color: palette(placeholder-text)")
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        for label in (self._caption, self._value, self._detail):
            label.setAlignment(Qt.AlignmentFlag.AlignRight)
            layout.addWidget(label)

    def set(self, value: str, detail: str = "", *, highlight: bool = False) -> None:
        self._value.setText(value)
        self._detail.setText(detail)
        self._detail.setStyleSheet(
            "color: #d97706; font-weight: bold" if highlight else "color: palette(placeholder-text)"
        )
