"""Route map: Leaflet with OpenStreetMap tiles, or a plain track plot without network.

The web view keeps a disk cache, so the same route needs the tile servers only once.
If Leaflet does not load (offline), ``failed`` is emitted and the window swaps in
``TrackView``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import platformdirs
import pyqtgraph as pg
from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QWidget

from crt import __version__
from crt.route.course import Course

MAX_POINTS = 4000  # polyline vertices sent to the page

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html, body, #map { height: 100%; margin: 0; }</style>
</head><body><div id="map"></div><script>
if (typeof L !== "undefined") {
  const route = ROUTE;
  const map = L.map("map", {zoomControl: true});
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  }).addTo(map);
  const line = L.polyline(route, {color: "#dc2626", weight: 4, opacity: 0.8}).addTo(map);
  map.fitBounds(line.getBounds(), {padding: [20, 20]});
  const rider = L.circleMarker(route[0], {radius: 7, color: "#fff", weight: 2,
                                          fillColor: "#1d4ed8", fillOpacity: 1}).addTo(map);
  let follow = false;
  window.setPosition = (lat, lon) => {
    rider.setLatLng([lat, lon]);
    if (follow) map.panTo([lat, lon], {animate: false});
  };
  window.setFollow = (on) => {
    follow = on;
    if (on) map.setView(rider.getLatLng(), 15); else map.fitBounds(line.getBounds());
  };
}
</script></body></html>
"""


def _route_points(course: Course) -> list[list[float]]:
    step = max(1, len(course.s) // MAX_POINTS)
    idx = np.r_[np.arange(0, len(course.s), step), len(course.s) - 1]
    return [[round(float(course.lat[i]), 6), round(float(course.lon[i]), 6)] for i in idx]


def _profile():
    from PySide6.QtWebEngineCore import QWebEngineProfile

    profile = QWebEngineProfile("crt")
    cache = Path(platformdirs.user_cache_dir("crt")) / "web"
    profile.setCachePath(str(cache))
    profile.setPersistentStoragePath(str(cache / "storage"))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    # The OSM tile policy asks for a User-Agent that identifies the application.
    profile.setHttpUserAgent(f"cycling-route-trainer/{__version__} (QtWebEngine)")
    return profile


class MapView(QWidget):
    failed = Signal()

    def __init__(self, course: Course) -> None:
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QVBoxLayout

        super().__init__()
        self._profile = _profile()
        self._view = QWebEngineView(self)
        self._view.setPage(QWebEnginePage(self._profile, self._view))
        self._ready = False
        self._view.loadFinished.connect(self._loaded)
        html = PAGE.replace("ROUTE", json.dumps(_route_points(course)))
        # An https base URL, so the tile requests carry a Referer as the OSM policy asks.
        self._view.setHtml(html, QUrl("https://cycling-route-trainer.invalid/"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

    def _loaded(self, ok: bool) -> None:
        self._view.page().runJavaScript("typeof L !== 'undefined'", 0, self._check)

    def _check(self, leaflet: object) -> None:
        self._ready = bool(leaflet)
        if not self._ready:
            self.failed.emit()

    def set_position(self, lat: float, lon: float) -> None:
        if self._ready:
            self._view.page().runJavaScript(f"setPosition({lat:.6f}, {lon:.6f})")

    def set_follow(self, on: bool) -> None:
        if self._ready:
            self._view.page().runJavaScript(f"setFollow({'true' if on else 'false'})")


class TrackView(pg.PlotWidget):
    """The route as a plain x/y line, for when the map cannot load."""

    def __init__(self, course: Course) -> None:
        super().__init__()
        self._kx = float(np.cos(np.radians(np.mean(course.lat))))
        self.setAspectLocked(True)
        self.hideAxis("left")
        self.hideAxis("bottom")
        self.plot(course.lon * self._kx, course.lat, pen=pg.mkPen("#dc2626", width=3))
        self._dot = pg.ScatterPlotItem(size=12, brush="#1d4ed8", pen=pg.mkPen("w", width=2))
        self.addItem(self._dot)
        self.set_position(float(course.lat[0]), float(course.lon[0]))

    def set_position(self, lat: float, lon: float) -> None:
        self._dot.setData([lon * self._kx], [lat])

    def set_follow(self, on: bool) -> None:
        pass
