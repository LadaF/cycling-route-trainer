"""ČÚZK DMR 5G: lidar terrain model of Czechia, 2 m grid, S-JTSK (EPSG:5514).

The ArcGIS ImageServer's ``getSamples`` operation is forbidden for the public, but
``exportImage`` works. Tiles of 500 m, aligned to the native pixel grid, are
downloaded with a one-pixel border (so bilinear interpolation never needs a neighbour
tile) and cached as GeoTIFF files. Data © ČÚZK, CC BY 4.0.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
from pyproj import Transformer

from crt.elevation.base import cache_dir, read_raster, sample_bilinear

log = logging.getLogger(__name__)

SERVICE_URL = "https://ags.cuzk.gov.cz/arcgis/rest/services/3D/dmr5g/ImageServer"
# Native grid of the service (from its metadata): pixel edges lie at origin + k * 2 m.
ORIGIN_X = -904703.6
ORIGIN_Y = -1227414.12
EXTENT = (-904703.6, -1227414.12, -431605.6, -935118.12)  # xmin, ymin, xmax, ymax
PIXEL = 2.0
TILE_PIXELS = 250  # 500 m
TILE = TILE_PIXELS * PIXEL
BORDER = 1  # extra pixels on each side


class CuzkDmr5g:
    name = "ČÚZK DMR 5G"

    def __init__(self, *, cache: Path | None = None, workers: int = 4, timeout: float = 60.0):
        self._cache = cache or cache_dir("cuzk-dmr5g")
        self._workers = workers
        self._timeout = timeout
        self._to_sjtsk = Transformer.from_crs("EPSG:4326", "EPSG:5514", always_xy=True)

    def _tile_path(self, i: int, j: int) -> Path:
        return self._cache / f"{i}_{j}.tif"

    def _download(self, client: httpx.Client, i: int, j: int) -> None:
        x0 = ORIGIN_X + i * TILE - BORDER * PIXEL
        y0 = ORIGIN_Y + j * TILE - BORDER * PIXEL
        size = TILE_PIXELS + 2 * BORDER
        params = {
            "bbox": f"{x0:.2f},{y0:.2f},{x0 + size * PIXEL:.2f},{y0 + size * PIXEL:.2f}",
            "bboxSR": "5514",
            "imageSR": "5514",
            "size": f"{size},{size}",
            "format": "tiff",
            "pixelType": "F32",
            "interpolation": "RSP_NearestNeighbor",  # grid-aligned: no resampling
            "compression": "LZ77",
            "f": "image",
        }
        response = client.get(f"{SERVICE_URL}/exportImage", params=params)
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("image/tiff"):
            raise RuntimeError(f"DMR 5G tile {i},{j}: unexpected response {response.text[:200]}")
        path = self._tile_path(i, j)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(response.content)
        tmp.replace(path)  # atomic: an interrupted download never leaves a broken tile

    def sample(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        x, y = self._to_sjtsk.transform(np.asarray(lon), np.asarray(lat))
        x, y = np.asarray(x), np.asarray(y)
        z = np.full(len(x), np.nan)
        inside = (x >= EXTENT[0]) & (x < EXTENT[2]) & (y >= EXTENT[1]) & (y < EXTENT[3])
        if not inside.any():
            return z
        ti = np.floor((x - ORIGIN_X) / TILE).astype(int)
        tj = np.floor((y - ORIGIN_Y) / TILE).astype(int)
        tiles = sorted({(int(a), int(b)) for a, b in zip(ti[inside], tj[inside], strict=True)})

        missing = [t for t in tiles if not self._tile_path(*t).exists()]
        if missing:
            log.info("downloading %d DMR 5G tiles", len(missing))
            with (
                httpx.Client(timeout=self._timeout) as client,
                ThreadPoolExecutor(self._workers) as pool,
            ):
                list(pool.map(lambda t: self._download(client, *t), missing))

        for i, j in tiles:
            data, transform = read_raster(self._tile_path(i, j))
            data[data <= 0] = np.nan  # the service returns 0 outside Czechia
            sel = inside & (ti == i) & (tj == j)
            z[sel] = sample_bilinear(data, transform, x[sel], y[sel])
        return z
