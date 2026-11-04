"""Route preparation: file → processed Course.

parse → project → drop close points → resample → sample DEM → fix bridges and
tunnels → smooth → grade.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d

from crt.elevation import bridges
from crt.elevation.base import ElevationProvider, fill_from
from crt.elevation.copernicus import CopernicusGlo30
from crt.elevation.cuzk import CuzkDmr5g
from crt.elevation.ign import IgnRgeAlti
from crt.route.course import Course, reversed_path
from crt.route.geometry import (
    LocalProjection,
    bearing_deg,
    cumulative_distance,
    drop_close_points,
    resample,
)
from crt.route.parsers import RawRoute, read_route

log = logging.getLogger(__name__)

SOURCES = ("auto", "dmr5g", "rgealti", "copernicus", "file")


@dataclass(frozen=True)
class PipelineOptions:
    source: str = "auto"  # one of SOURCES
    spacing: float = 2.0  # course grid [m]
    min_point_spacing: float = 2.0  # input points closer than this are dropped [m]
    # Gaussian sigma of the elevation profile [m]; 0 = none. 20 m removes the steps
    # where the GPS track strays from the road onto a cutting or embankment.
    smoothing: float = 20.0
    # Bridges and tunnels from an Overpass answer saved from the browser (the cached
    # one, or ``structures_file``); without one the course is marked as not checked.
    fix_bridges: bool = True
    structures_file: str | None = None

    @classmethod
    def of(cls, course: Course) -> PipelineOptions:
        """The options a course was prepared with (those this version still knows)."""
        names = {f.name for f in dataclasses.fields(cls)}
        saved = course.meta.get("options", {})
        return cls(**{k: v for k, v in saved.items() if k in names})


def providers_for(source: str) -> list[ElevationProvider]:
    match source:
        case "auto":
            return [CuzkDmr5g(), IgnRgeAlti(), CopernicusGlo30()]
        case "dmr5g":
            return [CuzkDmr5g()]
        case "rgealti":
            return [IgnRgeAlti()]
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


@dataclass(frozen=True)
class RouteGrid:
    """The route resampled to the course grid, before any elevation is sampled."""

    s: np.ndarray
    x: np.ndarray
    y: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    z_file: np.ndarray  # elevation from the route file, NaN if none
    projection: LocalProjection


def route_grid(raw: RawRoute, options: PipelineOptions) -> RouteGrid:
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
    return RouteGrid(s, x, y, lat, lon, z_file, projection)


def structure_query(path: Path, options: PipelineOptions | None = None) -> str:
    """The Overpass query for the bridges and tunnels of a route, to run in a browser."""
    g = route_grid(read_route(path), options or PipelineOptions())
    return bridges.structure_query(g.lat, g.lon, g.x, g.y)


def overpass_turbo_url(query: str) -> str:
    """Overpass Turbo with the query filled in and run at once."""
    from urllib.parse import quote

    return f"https://overpass-turbo.eu/?Q={quote(query)}&R"


def build_course(
    path: Path,
    options: PipelineOptions | None = None,
    *,
    providers: list[ElevationProvider] | None = None,
    raw: RawRoute | None = None,
) -> Course:
    options = options or PipelineOptions()
    raw = raw or read_route(path)
    g = route_grid(raw, options)
    s, x, y, lat, lon, z_file, projection = g.s, g.x, g.y, g.lat, g.lon, g.z_file, g.projection
    bearing = bearing_deg(x, y)

    providers = providers_for(options.source) if providers is None else providers
    z, used = fill_from(providers, lat, lon)
    coverage = {p.name: float(np.mean(used == i)) for i, p in enumerate(providers)}
    if np.isnan(z).any() and raw.ele is not None:
        missing = np.isnan(z)
        z[missing] = z_file[missing]
        coverage["route file"] = float(np.mean(missing & ~np.isnan(z)))
    z_raw = _fill_gaps(s, z)

    intervals: list[bridges.Interval] = []
    structures_checked = False
    if options.fix_bridges and options.source != "file":
        if options.structures_file:  # given explicitly: a bad file is an error
            structures = bridges.import_structures(Path(options.structures_file), lat, lon, x, y)
        else:
            structures = bridges.cached_structures(lat, lon, x, y)
        if structures is not None:
            intervals = bridges.match_intervals(s, x, y, bearing, structures, projection)
            structures_checked = True
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


def prepared_course(path: Path, options: PipelineOptions | None = None) -> tuple[Course, Path]:
    """The course for ``path``, and the file it is kept in.

    A ``.crt.npz`` file is loaded as it is. A route file is prepared once and the
    course saved next to it; that file is reused while it is newer than the route.
    """
    if path.name.endswith(".crt.npz"):
        return Course.load(path), path
    saved = path.with_suffix(".crt.npz")
    if saved.exists() and saved.stat().st_mtime >= path.stat().st_mtime:
        return Course.load(saved), saved
    course = build_course(path, options)
    try:
        course.save(saved)
    except OSError as exc:
        log.warning("cannot save the prepared course: %s", exc)
        return course, path
    return course, saved


def route_of(course: Course) -> Path | None:
    """The route file a course was prepared from, if it is still there."""
    source = course.meta.get("source_file")
    path = Path(source) if source else None
    return path if path is not None and path.is_file() else None


def rebuild_course(course_file: Path, *, answer: str | None = None, **changes) -> Course:
    """Prepare the route of a course file again, with some options changed, and save
    it. A reversed course stays reversed, and the course for the other direction, if
    kept next to it, is replaced as well.

    ``answer`` is an Overpass answer copied from the browser: it is kept as the
    route's bridges and tunnels, and the correction is switched on."""
    old = Course.load(course_file)
    route = route_of(old)
    if route is None:
        raise FileNotFoundError(f"the route {old.meta.get('source_file')} is not there")
    # An answer file given before is in the cache by now, and may be gone.
    changes = {"structures_file": None, **changes}
    options = dataclasses.replace(PipelineOptions.of(old), **changes)
    if answer is not None:
        options = dataclasses.replace(options, fix_bridges=True)
        g = route_grid(read_route(route), options)
        bridges.import_answer(answer, "the pasted answer", g.lat, g.lon, g.x, g.y)
    course = build_course(route, options)
    if old.meta.get("reversed"):
        course = course.reversed()
    course.save(course_file)
    other = reversed_path(course_file)
    if other.exists():
        course.reversed().save(other)
    return course
