"""Planar geometry of a route: local projection, cleaning and uniform resampling."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import Transformer


@dataclass(frozen=True)
class LocalProjection:
    """Transverse Mercator centred on the route: metric, with negligible distortion
    over any distance that can be ridden, and independent of the country."""

    lat0: float
    lon0: float

    def _crs(self) -> str:
        return f"+proj=tmerc +lat_0={self.lat0} +lon_0={self.lon0} +k=1 +ellps=WGS84 +units=m"

    def forward(self, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        t = Transformer.from_crs("EPSG:4326", self._crs(), always_xy=True)
        return t.transform(lon, lat)

    def inverse(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        t = Transformer.from_crs(self._crs(), "EPSG:4326", always_xy=True)
        lon, lat = t.transform(x, y)
        return lat, lon

    @classmethod
    def for_points(cls, lat: np.ndarray, lon: np.ndarray) -> LocalProjection:
        return cls(float(np.mean(lat)), float(np.mean(lon)))


def drop_close_points(x: np.ndarray, y: np.ndarray, min_spacing: float) -> np.ndarray:
    """Indices of points kept when each must be at least ``min_spacing`` from the
    previous kept one. Removes duplicates and GPS jitter while standing still."""
    keep = [0]
    lx, ly = x[0], y[0]
    for i in range(1, len(x)):
        if (x[i] - lx) ** 2 + (y[i] - ly) ** 2 >= min_spacing**2:
            keep.append(i)
            lx, ly = x[i], y[i]
    if keep[-1] != len(x) - 1 and len(keep) > 1:
        keep[-1] = len(x) - 1  # always end at the last point of the route
    return np.array(keep)


def cumulative_distance(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])


def resample(
    s: np.ndarray, spacing: float, *arrays: np.ndarray
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Linearly resample arrays given at distances ``s`` onto a uniform grid.

    The last grid point is the end of the route, so the final interval may be shorter.
    """
    grid = np.arange(0.0, s[-1], spacing)
    grid = np.append(grid, s[-1]) if s[-1] - grid[-1] > 1e-9 else grid
    return grid, [np.interp(grid, s, a) for a in arrays]


def bearing_deg(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Direction of travel, degrees clockwise from grid north."""
    dx, dy = np.gradient(x), np.gradient(y)
    return np.degrees(np.arctan2(dx, dy)) % 360.0
