"""Elevation provider interface, raster sampling and the on-disk cache."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import numpy as np
import platformdirs
import rasterio
from affine import Affine
from scipy.ndimage import map_coordinates

log = logging.getLogger(__name__)


class ElevationProvider(Protocol):
    name: str

    def sample(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        """Elevations [m] at the points; NaN where the provider has no data."""
        ...


def cache_dir(*parts: str) -> Path:
    path = Path(platformdirs.user_cache_dir("crt")).joinpath("elevation", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sample_bilinear(
    data: np.ndarray, transform: Affine, x: np.ndarray, y: np.ndarray
) -> np.ndarray:
    """Bilinear interpolation of a north-up raster at map coordinates ``x, y``.

    Values in ``data`` are taken at pixel centres. Points outside the raster, or next
    to a NaN pixel, give NaN.
    """
    col, row = ~transform @ (np.asarray(x), np.asarray(y))
    # pixel (0, 0) covers [0, 1) in pixel coordinates; its centre is at 0.5
    coords = np.vstack([np.ravel(row) - 0.5, np.ravel(col) - 0.5])
    return map_coordinates(data, coords, order=1, mode="constant", cval=np.nan)


def read_raster(path: Path) -> tuple[np.ndarray, Affine]:
    # The ČÚZK tiles describe EPSG:5514 slightly differently from the EPSG registry,
    # which makes GDAL warn. Only the pixel grid is used here, never the CRS.
    with rasterio.Env(GTIFF_SRS_SOURCE="EPSG"), rasterio.open(path) as ds:
        data = ds.read(1).astype(np.float64)
        if ds.nodata is not None:
            data[data == ds.nodata] = np.nan
        return data, ds.transform


def fill_from(providers: list[ElevationProvider], lat: np.ndarray, lon: np.ndarray):
    """Sample with each provider in turn, filling only points still missing.

    Returns the elevations and, per point, the index of the provider used (-1 = none).
    """
    z = np.full(len(lat), np.nan)
    used = np.full(len(lat), -1, dtype=np.int8)
    for i, provider in enumerate(providers):
        missing = np.isnan(z)
        if not missing.any():
            break
        values = provider.sample(lat[missing], lon[missing])
        got = ~np.isnan(values)
        idx = np.flatnonzero(missing)[got]
        z[idx] = values[got]
        used[idx] = i
        log.info("%s: %d of %d points", provider.name, got.sum(), missing.sum())
    return z, used
