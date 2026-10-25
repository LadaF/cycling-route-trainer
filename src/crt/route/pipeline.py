"""Route preparation: file → processed Course.

parse → project → drop close points → resample → sample DEM → fix bridges and
tunnels → smooth → grade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
from scipy.ndimage import gaussian_filter1d

from crt.elevation import bridges
from crt.elevation.base import ElevationProvider, fill_from
from crt.elevation.copernicus import CopernicusGlo30
from crt.elevation.cuzk import CuzkDmr5g
from crt.route.course import Course
from crt.route.geometry import (
    LocalProjection,
    bearing_deg,
    cumulative_distance,
    drop_close_points,
    resample,
)
from crt.route.parsers import RawRoute, read_route

log = logging.getLogger(__name__)

SOURCES = ("auto", "dmr5g", "copernicus", "file")


@dataclass(frozen=True)
class PipelineOptions:
    source: str = "auto"  # one of SOURCES
    spacing: float = 2.0  # course grid [m]
    min_point_spacing: float = 2.0  # input points closer than this are dropped [m]
    # Gaussian sigma of the elevation profile [m]; 0 = none. 20 m removes the steps
    # where the GPS track strays from the road onto a cutting or embankment.
    smoothing: float = 20.0
    fix_bridges: bool = True  # needs the public Overpass API; skipped if it fails


def providers_for(source: str) -> list[ElevationProvider]:
    match source:
        case "auto":
            return [CuzkDmr5g(), CopernicusGlo30()]
        case "dmr5g":
            return [CuzkDmr5g()]
        case "copernicus":
            return [CopernicusGlo30()]
        case "file":
            return []
    raise ValueError(f"unknown elevation source {source!r}; use one of {SOURCES}")


def _fill_gaps(s: np.ndarray, z: np.ndarray) -> np.ndarray:
    ok = ~np.isnan(z)
    if not ok.any():
        raise ValueError("no elevation data for any point of the route")
    return np.interp(s, s[ok], z[ok])


def build_course(
    path: Path,
    options: PipelineOptions | None = None,
    *,
    providers: list[ElevationProvider] | None = None,
    raw: RawRoute | None = None,
) -> Course:
    options = options or PipelineOptions()
    raw = raw or read_route(path)
    projection = LocalProjection.for_points(raw.lat, raw.lon)
    x, y = (np.asarray(v) for v in projection.forward(raw.lat, raw.lon))
    keep = drop_close_points(x, y, options.min_point_spacing)
    x, y = x[keep], y[keep]
    file_ele = raw.ele[keep] if raw.ele is not None else None

    s_raw = cumulative_distance(x, y)
    arrays = [x, y] + ([file_ele] if file_ele is not None else [])
    s, resampled = resample(s_raw, options.spacing, *arrays)
    x, y = resampled[0], resampled[1]
    z_file = resampled[2] if file_ele is not None else np.full(len(s), np.nan)
    lat, lon = (np.asarray(v) for v in projection.inverse(x, y))
    bearing = bearing_deg(x, y)

    providers = providers_for(options.source) if providers is None else providers
    z, used = fill_from(providers, lat, lon)
    coverage = {p.name: float(np.mean(used == i)) for i, p in enumerate(providers)}
    if np.isnan(z).any() and file_ele is not None:
        missing = np.isnan(z)
        z[missing] = z_file[missing]
        coverage["route file"] = float(np.mean(missing & ~np.isnan(z)))
    z_raw = _fill_gaps(s, z)

    intervals: list[bridges.Interval] = []
    structures_checked = False
    if options.fix_bridges and options.source != "file":
        try:
            structures = bridges.fetch_structures(lat, lon, x, y)
            intervals = bridges.match_intervals(s, x, y, bearing, structures, projection)
            structures_checked = True
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("bridge and tunnel correction skipped: %s", exc)
    z_fixed = bridges.apply_intervals(s, z_raw, intervals)

    sigma = options.smoothing / options.spacing
    z_final = gaussian_filter1d(z_fixed, sigma, mode="nearest") if sigma > 0 else z_fixed
    grade = np.gradient(z_final, s)

    meta = {
        "source_file": str(path),
        "options": options.__dict__,
        "elevation_coverage": coverage,
        "structures_checked": structures_checked,
        "structures": [iv.to_json() for iv in intervals],
    }
    return Course(raw.name, s, lat, lon, z_final, grade, bearing, z_raw, z_file, meta)
