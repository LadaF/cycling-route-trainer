"""The processed course: a route on a uniform distance grid with its final elevation
profile. This is what a ride loads; it needs no network access."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FORMAT_VERSION = 1
_ARRAYS = ("s", "lat", "lon", "z", "grade", "bearing", "z_raw", "z_file")


@dataclass(frozen=True, slots=True)
class CoursePoint:
    s: float
    lat: float
    lon: float
    z: float
    grade: float  # dz/ds, a fraction (0.05 = 5 %)
    bearing: float


@dataclass
class Course:
    name: str
    s: np.ndarray  # distance along the route [m], uniform except one end interval
    lat: np.ndarray
    lon: np.ndarray
    z: np.ndarray  # final elevation: bridges fixed, smoothed [m]
    grade: np.ndarray
    bearing: np.ndarray  # degrees from north
    z_raw: np.ndarray  # elevation as sampled from the DEM, for diagnostics
    z_file: np.ndarray  # elevation from the route file (NaN if none), for comparison
    meta: dict = field(default_factory=dict)  # sources, parameters, bridge intervals

    @property
    def length(self) -> float:
        return float(self.s[-1])

    def at(self, s: float) -> CoursePoint:
        s = min(max(s, 0.0), self.length)
        return CoursePoint(
            s,
            float(np.interp(s, self.s, self.lat)),
            float(np.interp(s, self.s, self.lon)),
            float(np.interp(s, self.s, self.z)),
            float(np.interp(s, self.s, self.grade)),
            # Nearest grid point: interpolating would break across the 0/360° wrap.
            float(self.bearing[min(int(np.searchsorted(self.s, s)), len(self.s) - 1)]),
        )

    def reversed(self) -> Course:
        """The same course ridden the other way. Exact: the grid is mirrored, not
        resampled, so its short interval moves to the start."""
        length = self.length
        meta = dict(self.meta)
        meta["reversed"] = not meta.get("reversed", False)
        meta["structures"] = [
            {**iv, "s0": length - iv["s1"], "s1": length - iv["s0"]}
            for iv in reversed(meta.get("structures", []))
        ]
        name = self.name.removesuffix(" (reversed)")
        return Course(
            name if self.meta.get("reversed") else f"{name} (reversed)",
            length - self.s[::-1],
            self.lat[::-1].copy(),
            self.lon[::-1].copy(),
            self.z[::-1].copy(),
            -self.grade[::-1],
            (self.bearing[::-1] + 180.0) % 360.0,
            self.z_raw[::-1].copy(),
            self.z_file[::-1].copy(),
            meta,
        )

    def mean_grade(self, s0: float, s1: float) -> float:
        """Mean grade between two distances; the exact mean of dz/ds is Δz/Δs."""
        s0, s1 = (min(max(v, 0.0), self.length) for v in (s0, s1))
        if s1 - s0 < 1e-6:
            return self.at(s0).grade
        return float((np.interp(s1, self.s, self.z) - np.interp(s0, self.s, self.z)) / (s1 - s0))

    @property
    def total_ascent(self) -> float:
        dz = np.diff(self.z)
        return float(dz[dz > 0].sum())

    def save(self, path: Path) -> None:
        meta = {"format_version": FORMAT_VERSION, "name": self.name, **self.meta}
        with path.open("wb") as f:  # a file object stops numpy from appending ".npz"
            np.savez_compressed(
                f, meta=np.array(json.dumps(meta)), **{k: getattr(self, k) for k in _ARRAYS}
            )

    @classmethod
    def load(cls, path: Path) -> Course:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            if meta.pop("format_version", None) != FORMAT_VERSION:
                raise ValueError(f"{path} was written by an incompatible version")
            name = meta.pop("name")
            return cls(name, **{k: data[k] for k in _ARRAYS}, meta=meta)


def reversed_path(path: Path) -> Path:
    """Where the reversed course of the course file ``path`` is kept: ``X.crt.npz`` and
    ``X.reverse.crt.npz`` are each other's reverse."""
    stem = path.name.removesuffix(".crt.npz")
    if stem.endswith(".reverse"):
        return path.with_name(stem.removesuffix(".reverse") + ".crt.npz")
    return path.with_name(stem + ".reverse.crt.npz")
