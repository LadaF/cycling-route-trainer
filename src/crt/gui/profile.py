"""Elevation profile: the whole route, and the next 2 km coloured by grade."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QPainterPath, QPolygonF
from PySide6.QtWidgets import QGraphicsPathItem, QVBoxLayout, QWidget

from crt.route.course import Course

BIN = 25.0  # m, resolution of the grade colouring
BEHIND, AHEAD = 200.0, 2000.0  # m shown around the rider in the zoomed panel

# (upper grade limit in %, colour)
GRADE_COLOURS = [
    (-2.0, "#3b82f6"),
    (2.0, "#22c55e"),
    (4.0, "#a3e635"),
    (6.0, "#facc15"),
    (8.0, "#f97316"),
    (10.0, "#ef4444"),
    (np.inf, "#9f1239"),
]


def grade_colour(grade_pct: float) -> str:
    for limit, colour in GRADE_COLOURS:
        if grade_pct < limit:
            return colour
    return GRADE_COLOURS[-1][1]


def grade_bands(
    x: np.ndarray, z: np.ndarray, base: float, grade: np.ndarray
) -> list[QGraphicsPathItem]:
    """Bins between ``x`` edges, filled from ``base`` up to the profile, one item per colour."""
    paths: dict[str, QPainterPath] = {}
    for i, g in enumerate(grade):
        corners = [(x[i], base), (x[i], z[i]), (x[i + 1], z[i + 1]), (x[i + 1], base)]
        path = paths.setdefault(grade_colour(g), QPainterPath())
        path.addPolygon(QPolygonF([QPointF(px, py) for px, py in corners]))
    items = []
    for colour, path in paths.items():
        item = QGraphicsPathItem(path)
        item.setPen(pg.mkPen(colour))  # covers the seams between neighbouring bands
        item.setBrush(pg.mkBrush(colour))
        items.append(item)
    return items


class ProfileView(QWidget):
    def __init__(self, course: Course) -> None:
        super().__init__()
        self.course = course
        km = course.s / 1000
        self._base = float(course.z.min()) - 20

        self.overview = pg.PlotWidget()
        self.overview.setMouseEnabled(x=False, y=False)
        self.overview.hideButtons()
        self.overview.plot(km, course.z, pen=pg.mkPen("#64748b", width=1.5),
                           fillLevel=self._base, brush=(100, 116, 139, 60))  # fmt: skip
        self.overview.setXRange(0, km[-1], padding=0.01)
        self.overview.setLabel("bottom", "km")
        self.overview.setLabel("left", "m")
        self._overview_marker = pg.InfiniteLine(angle=90, pen=pg.mkPen("#dc2626", width=2))
        self.overview.addItem(self._overview_marker)

        # The zoomed panel shows the same coloured profile; following the rider only
        # moves its view range.
        edges = np.append(np.arange(0.0, course.length, BIN), course.length)
        z = np.interp(edges, course.s, course.z)
        grade = np.diff(z) / np.diff(edges) * 100
        self._edges, self._z_edges = edges, z
        self.zoom = pg.PlotWidget()
        self.zoom.setMouseEnabled(x=False, y=False)
        self.zoom.hideButtons()
        for item in grade_bands(edges / 1000, z, self._base, grade):
            self.zoom.addItem(item)
        self.zoom.plot(edges / 1000, z, pen=pg.mkPen("#334155", width=1.5))
        self.zoom.setLabel("bottom", "km")
        self.zoom.setLabel("left", "m")
        self._zoom_marker = pg.InfiniteLine(angle=90, pen=pg.mkPen("#dc2626", width=2))
        self._dot = pg.ScatterPlotItem(size=11, brush="#dc2626", pen=pg.mkPen("w", width=1.5))
        self.zoom.addItem(self._zoom_marker)
        self.zoom.addItem(self._dot)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.zoom, stretch=3)
        layout.addWidget(self.overview, stretch=1)
        self.set_position(0.0)
        self._apply_font()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:  # the window's text zoom
            self._apply_font()

    def _apply_font(self) -> None:
        # pyqtgraph axes do not follow the application font by themselves.
        for plot in (self.zoom, self.overview):
            for name in ("left", "bottom"):
                axis = plot.getAxis(name)
                axis.setTickFont(self.font())
                axis.label.setFont(self.font())

    def set_position(self, s: float) -> None:
        z = float(np.interp(s, self.course.s, self.course.z))
        self._overview_marker.setPos(s / 1000)
        self._zoom_marker.setPos(s / 1000)
        self._dot.setData([s / 1000], [z])
        lo, hi = s - BEHIND, s + AHEAD
        self.zoom.setXRange(lo / 1000, hi / 1000, padding=0)
        visible = self._z_edges[(self._edges >= lo) & (self._edges <= hi)]
        z_lo, z_hi = (visible.min(), visible.max()) if len(visible) else (z, z)
        margin = max((z_hi - z_lo) * 0.15, 5.0)
        self.zoom.setYRange(z_lo - margin, z_hi + margin, padding=0)
