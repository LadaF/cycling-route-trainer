"""Read route geometry from GPX, TCX and FIT files."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import fitdecode
import gpxpy
import numpy as np


@dataclass(frozen=True)
class RawRoute:
    """Route points as found in the file. ``ele`` is None if the file has no elevations,
    otherwise it may contain NaN for points without one."""

    name: str
    lat: np.ndarray
    lon: np.ndarray
    ele: np.ndarray | None


class RouteFormatError(ValueError):
    pass


def _make(name: str, points: list[tuple[float, float, float | None]]) -> RawRoute:
    if len(points) < 2:
        raise RouteFormatError("the route has fewer than two points")
    arr = np.array([(la, lo, np.nan if e is None else e) for la, lo, e in points], dtype=float)
    ele = arr[:, 2] if not np.isnan(arr[:, 2]).all() else None
    return RawRoute(name, arr[:, 0], arr[:, 1], ele)


def read_gpx(path: Path) -> RawRoute:
    with path.open(encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    points: list[tuple[float, float, float | None]] = []
    name = path.stem
    # Tracks take precedence; all segments are joined in order.
    for track in gpx.tracks:
        name = track.name or name
        for segment in track.segments:
            points += [(p.latitude, p.longitude, p.elevation) for p in segment.points]
    if not points:
        for route in gpx.routes:
            name = route.name or name
            points += [(p.latitude, p.longitude, p.elevation) for p in route.points]
    return _make(name, points)


_TCX_NS = {"tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}


def read_tcx(path: Path) -> RawRoute:
    root = ET.parse(path).getroot()
    name_el = root.find(".//tcx:Course/tcx:Name", _TCX_NS)
    points: list[tuple[float, float, float | None]] = []
    for tp in root.iterfind(".//tcx:Trackpoint", _TCX_NS):
        lat = tp.find("tcx:Position/tcx:LatitudeDegrees", _TCX_NS)
        lon = tp.find("tcx:Position/tcx:LongitudeDegrees", _TCX_NS)
        if lat is None or lon is None:
            continue  # trackpoints without a position (e.g. pauses) are allowed
        alt = tp.find("tcx:AltitudeMeters", _TCX_NS)
        points.append((float(lat.text), float(lon.text), None if alt is None else float(alt.text)))
    name = name_el.text if name_el is not None and name_el.text else path.stem
    return _make(name, points)


_SEMICIRCLE_DEG = 180.0 / 2**31


def read_fit(path: Path) -> RawRoute:
    """Activity or course FIT file: positions of the ``record`` messages."""
    points: list[tuple[float, float, float | None]] = []
    name = path.stem
    with fitdecode.FitReader(path) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "course" and frame.has_field("name"):
                name = frame.get_value("name") or name
            if frame.name != "record":
                continue
            lat = frame.get_value("position_lat", fallback=None)
            lon = frame.get_value("position_long", fallback=None)
            if lat is None or lon is None:
                continue
            ele = None
            for field in ("enhanced_altitude", "altitude"):
                if frame.has_field(field) and frame.get_value(field) is not None:
                    ele = float(frame.get_value(field))
                    break
            points.append((lat * _SEMICIRCLE_DEG, lon * _SEMICIRCLE_DEG, ele))
    return _make(name, points)


READERS = {".gpx": read_gpx, ".tcx": read_tcx, ".fit": read_fit}


def read_route(path: Path) -> RawRoute:
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise RouteFormatError(f"unsupported route format {path.suffix!r} (use GPX, TCX or FIT)")
    return reader(path)
