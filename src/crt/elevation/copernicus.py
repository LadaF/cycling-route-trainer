"""Copernicus GLO-30: global 1″ (~30 m) elevation model, from AWS Open Data.

Note that it is a *surface* model: in forests and towns it includes part of the
vegetation and buildings, so it is only a fallback where no terrain model exists.
Whole 1°×1° tiles are downloaded once (~20–40 MB each) and cached; tiles over the sea
do not exist, which is remembered with an empty marker file.
Data © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided
under COPERNICUS by the European Union and ESA.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import numpy as np

from crt.elevation.base import cache_dir, read_raster, sample_bilinear

log = logging.getLogger(__name__)

BUCKET_URL = "https://copernicus-dem-30m.s3.amazonaws.com"


def tile_name(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_floor):02d}_00_{ew}{abs(lon_floor):03d}_00_DEM"


class CopernicusGlo30:
    name = "Copernicus GLO-30"

    def __init__(self, *, cache: Path | None = None, timeout: float = 300.0):
        self._cache = cache or cache_dir("copernicus-glo30")
        self._timeout = timeout

    def _tile(self, lat_floor: int, lon_floor: int) -> Path | None:
        name = tile_name(lat_floor, lon_floor)
        path = self._cache / f"{name}.tif"
        absent = self._cache / f"{name}.absent"
        if path.exists():
            return path
        if absent.exists():
            return None
        log.info("downloading %s", name)
        tmp = path.with_suffix(".part")
        with (
            httpx.Client(timeout=self._timeout) as client,
            client.stream("GET", f"{BUCKET_URL}/{name}/{name}.tif") as response,
        ):
            # the bucket answers 403 or 404 for tiles that do not exist (sea)
            if response.status_code in (403, 404):
                absent.touch()
                return None
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_bytes(1 << 20):
                    f.write(chunk)
        tmp.replace(path)
        return path

    def sample(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        lat, lon = np.asarray(lat), np.asarray(lon)
        z = np.full(len(lat), np.nan)
        tlat, tlon = np.floor(lat).astype(int), np.floor(lon).astype(int)
        for a, b in sorted({(int(p), int(q)) for p, q in zip(tlat, tlon, strict=True)}):
            path = self._tile(a, b)
            if path is None:
                continue
            data, transform = read_raster(path)
            sel = (tlat == a) & (tlon == b)
            z[sel] = sample_bilinear(data, transform, lon[sel], lat[sel])
            # Points in the outermost half pixel of a tile have no neighbour inside it.
            # They are rare enough that nearest-neighbour is good enough there.
            edge = sel & np.isnan(z)
            if edge.any():
                col, row = ~transform @ (lon[edge], lat[edge])
                r = np.clip(np.floor(row).astype(int), 0, data.shape[0] - 1)
                c = np.clip(np.floor(col).astype(int), 0, data.shape[1] - 1)
                z[edge] = data[r, c]
        return z
