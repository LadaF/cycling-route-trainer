"""Bridge and tunnel correction.

A terrain model has no bridges: on a viaduct it gives the valley floor, in a tunnel
the hill above. Bridges and tunnels are taken from OpenStreetMap, from an Overpass
answer saved in the browser: the public Overpass server serves browser tools first
and turns applications away when it is busy, which is nearly always. The answer is
cached, keyed by the query, so a route needs it only once. The route intervals that
run *along* the structures are found, and the elevation inside each interval is
replaced by a straight line between its ends. Passing *under* a bridge is not
matched, because the route there is not parallel to the bridge.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from crt.elevation.base import cache_dir
from crt.route.geometry import LocalProjection


@dataclass(frozen=True)
class Structure:
    kind: str  # "bridge" or "tunnel"
    name: str
    lat: np.ndarray
    lon: np.ndarray


@dataclass(frozen=True)
class Interval:
    kind: str
    name: str
    s0: float
    s1: float

    def to_json(self) -> dict:
        return asdict(self)


def _check_answer(result: object) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("elements"), list):
        raise ValueError("not an Overpass JSON answer (no list of elements)")
    if "remark" in result and not result["elements"]:
        # Timeouts and memory limits come as a remark with HTTP 200; not to be cached.
        raise ValueError(f"Overpass: {result['remark'][:200]}")
    if any(el.get("type") == "way" and "geometry" not in el for el in result["elements"]):
        raise ValueError("the ways have no geometry; the query must end with 'out tags geom;'")


def structure_query(
    lat: np.ndarray, lon: np.ndarray, x: np.ndarray, y: np.ndarray, *, radius: float = 25.0,
    chunk: float = 2000.0, timeout: float = 25.0,
) -> str:  # fmt: skip
    """The Overpass query for the road bridges and tunnels near the route.

    The route is cut into pieces of ``chunk`` metres, and the query asks for the
    structures in the bounding box of each piece, widened by ``radius`` metres. Boxes
    are much cheaper for the server than a distance search along the route line; the
    extra structures they bring are dropped by ``match_intervals``. Building passages
    (``tunnel=building_passage``) are at ground level and not wanted.

    ``timeout`` (rounded up) limits the query on the server.
    """
    s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    piece = (s // chunk).astype(int)
    dlat = radius / 111_320
    boxes = []
    for k in np.unique(piece):
        # Each piece overlaps the next by one point, so no route segment falls between boxes.
        sel = np.flatnonzero(piece == k)
        sel = np.append(sel, min(sel[-1] + 1, len(s) - 1))
        la, lo = lat[sel], lon[sel]
        dlon = dlat / math.cos(math.radians(float(la.mean())))
        edges = (la.min() - dlat, lo.min() - dlon, la.max() + dlat, lo.max() + dlon)
        boxes.append(",".join(f"{v:.6f}" for v in edges))
    statements = "".join(
        f'way["highway"]["bridge"]["bridge"!="no"]({box});'
        f'way["highway"]["tunnel"]["tunnel"!="no"]["tunnel"!="building_passage"]({box});'
        for box in boxes
    )
    return f"[out:json][timeout:{math.ceil(timeout)}];\n({statements});\nout tags geom;"


def _cache_path(query: str) -> Path:
    # Keyed without the [timeout:N] header: the answer does not depend on it.
    body = query.split("\n", 1)[1]
    return cache_dir("overpass") / f"{hashlib.sha256(body.encode()).hexdigest()[:24]}.json"


def cached_structures(
    lat: np.ndarray, lon: np.ndarray, x: np.ndarray, y: np.ndarray
) -> list[Structure] | None:
    """Road bridges and tunnels near the route from a saved answer, None if there is none."""
    cache = _cache_path(structure_query(lat, lon, x, y))
    if not cache.exists():
        return None
    return parse_structures(json.loads(cache.read_text()))


class StructuresFileError(ValueError):
    """An Overpass answer given by the user (a file or pasted text) cannot be used."""


def import_answer(
    text: str, origin: str, lat: np.ndarray, lon: np.ndarray, x: np.ndarray, y: np.ndarray
) -> list[Structure]:
    """Structures from an Overpass answer saved or copied from the browser, kept in the
    cache as the answer for this route. Overpass Turbo's "raw OSM data" and GeoJSON
    exports both work; GeoJSON is converted to the raw form. ``origin`` names the
    answer in errors."""
    try:
        if text.lstrip().startswith("[out:"):
            raise ValueError(
                "this is the query, not its answer. Run it in Overpass Turbo and, when the"
                " map shows the answer, take Export → Data → raw OSM data (or the Data tab)"
            )
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("not JSON (an Overpass answer starts with '{')") from exc
        if isinstance(result, dict) and result.get("type") == "FeatureCollection":
            result = _from_geojson(result)
        _check_answer(result)
    except (ValueError, KeyError, TypeError) as exc:
        raise StructuresFileError(f"{origin}: {exc}") from exc
    _cache_path(structure_query(lat, lon, x, y)).write_text(json.dumps(result))
    return parse_structures(result)


def import_structures(
    path: Path, lat: np.ndarray, lon: np.ndarray, x: np.ndarray, y: np.ndarray
) -> list[Structure]:
    """Structures from an Overpass answer saved in a file (see ``import_answer``)."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise StructuresFileError(f"{path}: not a JSON file") from exc
    return import_answer(text, str(path), lat, lon, x, y)


def _from_geojson(collection: dict) -> dict:
    """Overpass Turbo GeoJSON (tags flattened into the properties, next to "@id" and
    similar keys) as an Overpass answer with ways and their geometry."""
    elements = []
    for feature in collection["features"]:
        geometry = feature.get("geometry") or {}
        match geometry.get("type"):
            case "LineString":
                lines = [geometry["coordinates"]]
            case "MultiLineString":
                lines = geometry["coordinates"]
            case _:
                continue
        props = feature.get("properties") or {}
        tags = props.get("tags") or {k: v for k, v in props.items() if not k.startswith("@")}
        for line in lines:
            points = [{"lat": float(lat), "lon": float(lon)} for lon, lat, *_ in line]
            elements.append({"type": "way", "tags": tags, "geometry": points})
    return {"elements": elements}


def parse_structures(result: dict) -> list[Structure]:
    structures = []
    for el in result.get("elements", []):
        tags = el.get("tags", {})
        geom = el.get("geometry") or []
        if el.get("type") != "way" or len(geom) < 2:
            continue
        kind = "bridge" if tags.get("bridge", "no") != "no" else "tunnel"
        name = tags.get("bridge:name") or tags.get("tunnel:name") or tags.get("name") or ""
        structures.append(
            Structure(
                kind,
                name,
                np.array([g["lat"] for g in geom]),
                np.array([g["lon"] for g in geom]),
            )
        )
    return structures


def match_intervals(
    s: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    bearing: np.ndarray,
    structures: list[Structure],
    projection: LocalProjection,
    *,
    tolerance: float = 10.0,
    max_angle_deg: float = 30.0,
    pad: float = 5.0,
    min_length: float = 5.0,
) -> list[Interval]:
    """Route intervals running along the given structures.

    A route point matches a structure when it is within ``tolerance`` metres of it and
    travels within ``max_angle_deg`` of its direction (either way). Each run of matched
    points is extended by ``pad`` at both ends to reach the abutments or portals.
    """
    cos_max = np.cos(np.radians(max_angle_deg))
    heading = np.radians(bearing)
    ux, uy = np.sin(heading), np.cos(heading)  # unit direction of travel
    intervals = []
    for st in structures:
        bx, by = (np.asarray(v) for v in projection.forward(st.lat, st.lon))
        near = (
            (x > bx.min() - tolerance)
            & (x < bx.max() + tolerance)
            & (y > by.min() - tolerance)
            & (y < by.max() + tolerance)
        )
        if not near.any():
            continue
        cand = np.flatnonzero(near)
        best = np.full(len(cand), np.inf)
        for k in range(len(bx) - 1):
            ax, ay, dx, dy = bx[k], by[k], bx[k + 1] - bx[k], by[k + 1] - by[k]
            seg_len = np.hypot(dx, dy)
            if seg_len < 1e-6:
                continue
            t = ((x[cand] - ax) * dx + (y[cand] - ay) * dy) / seg_len**2
            # Only points alongside the segment count; near its end is not on the bridge.
            alongside = (t >= 0.0) & (t <= 1.0)
            dist = np.hypot(x[cand] - (ax + t * dx), y[cand] - (ay + t * dy))
            parallel = np.abs(ux[cand] * dx + uy[cand] * dy) / seg_len >= cos_max
            best = np.where(alongside & parallel & (dist < best), dist, best)
        matched = cand[best <= tolerance]
        if len(matched) == 0:
            continue
        # split into runs of consecutive grid points (small gaps tolerated)
        breaks = np.flatnonzero(np.diff(matched) > 3)
        for run in np.split(matched, breaks + 1):
            s0, s1 = s[run[0]], s[run[-1]]
            if s1 - s0 >= min_length:
                intervals.append(Interval(st.kind, st.name, s0 - pad, s1 + pad))
    return merge_intervals(intervals)


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping intervals (e.g. one bridge mapped as several OSM ways)."""
    merged: list[Interval] = []
    for iv in sorted(intervals, key=lambda i: i.s0):
        if merged and iv.s0 <= merged[-1].s1:
            last = merged[-1]
            name = last.name if last.name or not iv.name else iv.name
            kind = last.kind if last.kind == iv.kind else "bridge/tunnel"
            merged[-1] = Interval(kind, name, last.s0, max(last.s1, iv.s1))
        else:
            merged.append(iv)
    return merged


def apply_intervals(s: np.ndarray, z: np.ndarray, intervals: list[Interval]) -> np.ndarray:
    """Replace the elevation inside each interval by a straight line between its ends."""
    z = z.copy()
    for iv in intervals:
        s0, s1 = max(iv.s0, s[0]), min(iv.s1, s[-1])
        inside = (s > s0) & (s < s1)
        z0, z1 = np.interp(s0, s, z), np.interp(s1, s, z)
        z[inside] = z0 + (z1 - z0) * (s[inside] - s0) / (s1 - s0)
    return z
