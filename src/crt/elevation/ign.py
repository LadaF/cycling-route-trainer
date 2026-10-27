"""IGN RGE ALTI: terrain model of France, 1–5 m grid, from the Géoplateforme API.

The altimetry service returns elevations at given points, so the route points are
sent in batches and each batch's answer is cached under a hash of its coordinates;
the same route is then never queried again. Points outside France come back as
-99999. A failed request is not repeated: the provider gives no data for the route
and the next provider (Copernicus) is used, rather than a mix of the two models with
steps where they meet. Data © IGN, Licence Ouverte 2.0.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import httpx
import numpy as np

from crt import __version__
from crt.elevation.base import cache_dir

log = logging.getLogger(__name__)

SERVICE_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
RESOURCE = "ign_rge_alti_wld"
NO_DATA = -99999.0
BATCH = 5000  # points per request, the service maximum
# Metropolitan France with Corsica; only points inside are sent.
EXTENT = (41.3, -5.3, 51.2, 9.7)  # lat_min, lon_min, lat_max, lon_max
HEADERS = {"User-Agent": f"cycling-route-trainer/{__version__}"}


class IgnRgeAlti:
    name = "IGN RGE ALTI"

    def __init__(self, *, cache: Path | None = None, timeout: float = 30.0):
        self._cache = cache or cache_dir("ign-rgealti")
        self._timeout = timeout

    def _batch(self, client: httpx.Client, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        coords = {
            "lon": "|".join(f"{v:.6f}" for v in lon),
            "lat": "|".join(f"{v:.6f}" for v in lat),
        }
        key = hashlib.sha256(json.dumps(coords).encode()).hexdigest()[:24]
        path = self._cache / f"{key}.json"
        if path.exists():
            return np.array(json.loads(path.read_text()), dtype=float)
        response = client.post(SERVICE_URL, json={**coords, "resource": RESOURCE, "zonly": "true"})
        response.raise_for_status()
        z = response.json()["elevations"]
        if len(z) != len(lat):
            raise ValueError(f"IGN returned {len(z)} elevations for {len(lat)} points")
        path.write_text(json.dumps(z))
        return np.array(z, dtype=float)

    def sample(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        lat, lon = np.asarray(lat), np.asarray(lon)
        z = np.full(len(lat), np.nan)
        inside = (lat >= EXTENT[0]) & (lat <= EXTENT[2]) & (lon >= EXTENT[1]) & (lon <= EXTENT[3])
        idx = np.flatnonzero(inside)
        if len(idx) == 0:
            return z
        try:
            with httpx.Client(headers=HEADERS, timeout=self._timeout) as client:
                for start in range(0, len(idx), BATCH):
                    part = idx[start : start + BATCH]
                    z[part] = self._batch(client, lat[part], lon[part])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("IGN RGE ALTI not used: %s", exc)
            return np.full(len(lat), np.nan)
        z[z <= NO_DATA + 1] = np.nan
        return z
