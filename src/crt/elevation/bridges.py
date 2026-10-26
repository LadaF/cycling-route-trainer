"""Bridge and tunnel correction.

A terrain model has no bridges: on a viaduct it gives the valley floor, in a tunnel
the hill above. Bridges and tunnels are taken from OpenStreetMap (Overpass API). The
route intervals that run *along* them are found, and the elevation inside each
interval is replaced by a straight line between its ends. Passing *under* a bridge is
not matched, because the route there is not parallel to the bridge.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass

import httpx
import numpy as np

from crt import __version__
from crt.elevation.base import cache_dir
from crt.route.geometry import LocalProjection

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Overpass rejects requests with generic library user agents (HTTP 406).
HEADERS = {"User-Agent": f"cycling-route-trainer/{__version__}", "Accept": "application/json"}


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


def simplify(x: np.ndarray, y: np.ndarray, tolerance: float) -> np.ndarray:
    """Ramer–Douglas–Peucker simplification. Returns the indices of kept points."""
    keep = np.zeros(len(x), dtype=bool)
    keep[[0, -1]] = True
    stack = [(0, len(x) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        dx, dy = x[b] - x[a], y[b] - y[a]
        norm = np.hypot(dx, dy)
        px, py = x[a + 1 : b] - x[a], y[a + 1 : b] - y[a]
        d = np.hypot(px, py) if norm < 1e-9 else np.abs(dx * py - dy * px) / norm
        k = int(np.argmax(d))
        if d[k] > tolerance:
            m = a + 1 + k
            keep[m] = True
            stack += [(a, m), (m, b)]
    return np.flatnonzero(keep)


def _query_overpass(query: str, timeout: float) -> dict:
    """One attempt only: the public server is often overloaded, and the pipeline then
    simply continues without the correction."""
    response = httpx.post(
        OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=httpx.Timeout(timeout)
    )
    response.raise_for_status()
    result = response.json()
    if "remark" in result and not result.get("elements"):
        # Timeouts and memory limits come as a remark with HTTP 200; not to be cached.
        raise httpx.HTTPError(f"Overpass: {result['remark'][:200]}")
    return result


def fetch_structures(
    lat: np.ndarray, lon: np.ndarray, x: np.ndarray, y: np.ndarray, *, radius: float = 25.0,
    timeout: float = 5.0,
) -> list[Structure]:  # fmt: skip
    """Road bridges and tunnels within ``radius`` metres of the route, from Overpass.

    ``timeout`` bounds each network step of the request and, rounded up, the query on
    the server, so the server does not keep working after we give up.
    """
    idx = simplify(x, y, tolerance=5.0)
    line = ",".join(f"{lat[i]:.6f},{lon[i]:.6f}" for i in idx)
    query = (
        f"[out:json][timeout:{math.ceil(timeout)}];"
        f'(way["highway"]["bridge"]["bridge"!="no"](around:{radius:g},{line});'
        f'way["highway"]["tunnel"]["tunnel"!="no"](around:{radius:g},{line}););'
        "out tags geom;"
    )
    cache = cache_dir("overpass") / f"{hashlib.sha256(query.encode()).hexdigest()[:24]}.json"
    if cache.exists():
        result = json.loads(cache.read_text())
    else:
        log.info("querying Overpass for bridges and tunnels (%d route vertices)", len(idx))
        result = _query_overpass(query, timeout)
        cache.write_text(json.dumps(result))

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
