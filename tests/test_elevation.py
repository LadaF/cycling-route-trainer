import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from crt.elevation import bridges, cuzk
from crt.elevation.base import fill_from, sample_bilinear
from crt.elevation.copernicus import CopernicusGlo30, tile_name
from crt.route.geometry import LocalProjection


def write_tif(path, data, transform):
    with rasterio.open(
        path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=1,
        dtype="float32", transform=transform,
    ) as ds:  # fmt: skip
        ds.write(data.astype(np.float32), 1)


def test_sample_bilinear_plane_and_outside():
    transform = from_origin(1000.0, 2000.0, 2.0, 2.0)  # top-left corner, 2 m pixels
    rows, cols = np.mgrid[0:20, 0:30]
    cx, cy = 1000.0 + (cols + 0.5) * 2, 2000.0 - (rows + 0.5) * 2  # pixel centres
    data = 5.0 + 0.1 * cx + 0.3 * cy
    x = np.array([1010.3, 1030.0, 1050.9])
    y = np.array([1990.1, 1975.5, 1965.0])
    assert sample_bilinear(data, transform, x, y) == pytest.approx(5.0 + 0.1 * x + 0.3 * y)
    assert np.isnan(sample_bilinear(data, transform, np.array([900.0]), np.array([1990.0])))[0]


def test_fill_from_uses_providers_in_order():
    class P:
        """Fake provider; the "latitude" is the point index."""

        def __init__(self, name, values):
            self.name, self.values = name, np.array(values, dtype=float)

        def sample(self, lat, lon):
            return self.values[np.asarray(lat, dtype=int)]

    first = P("first", [1.0, np.nan, 3.0])
    second = P("second", [np.nan, 20.0, 30.0])
    z, used = fill_from([first, second], np.arange(3.0), np.zeros(3))
    assert z.tolist() == [1.0, 20.0, 3.0]
    assert used.tolist() == [0, 1, 0]


def test_cuzk_samples_cached_tile(tmp_path):
    x0, y0 = -816000.0, -1031000.0  # near the example route
    i = int(np.floor((x0 - cuzk.ORIGIN_X) / cuzk.TILE))
    j = int(np.floor((y0 - cuzk.ORIGIN_Y) / cuzk.TILE))
    size = cuzk.TILE_PIXELS + 2 * cuzk.BORDER
    left = cuzk.ORIGIN_X + i * cuzk.TILE - cuzk.BORDER * cuzk.PIXEL
    bottom = cuzk.ORIGIN_Y + j * cuzk.TILE - cuzk.BORDER * cuzk.PIXEL
    top = bottom + size * cuzk.PIXEL
    rows, cols = np.mgrid[0:size, 0:size]
    cx, cy = left + (cols + 0.5) * cuzk.PIXEL, top - (rows + 0.5) * cuzk.PIXEL
    data = 400.0 + 0.01 * (cx - left) - 0.02 * (cy - bottom)
    data[:5, :5] = 0.0  # the service's "no data"
    write_tif(tmp_path / f"{i}_{j}.tif", data, from_origin(left, top, cuzk.PIXEL, cuzk.PIXEL))

    to_wgs = Transformer.from_crs("EPSG:5514", "EPSG:4326", always_xy=True)
    px = np.array([left + 2.0, left + 250.3, left + 499.0, left + 4.0])  # inner tile edges
    py = np.array([bottom + 2.0, bottom + 100.7, bottom + 499.0, top - 4.0])
    lon, lat = to_wgs.transform(px, py)
    z = cuzk.CuzkDmr5g(cache=tmp_path).sample(np.asarray(lat), np.asarray(lon))
    expected = 400.0 + 0.01 * (px - left) - 0.02 * (py - bottom)
    assert z[:3] == pytest.approx(expected[:3], abs=1e-3)
    assert np.isnan(z[3])  # next to a zero pixel


def test_cuzk_outside_extent_needs_no_download(tmp_path):
    z = cuzk.CuzkDmr5g(cache=tmp_path).sample(np.array([48.0]), np.array([2.35]))  # Paris
    assert np.isnan(z).all()
    assert not list(tmp_path.iterdir())


def test_copernicus_tile_names():
    assert tile_name(50, 13) == "Copernicus_DSM_COG_10_N50_00_E013_00_DEM"
    assert tile_name(-1, -75) == "Copernicus_DSM_COG_10_S01_00_W075_00_DEM"


def test_copernicus_samples_cached_tile_and_absent(tmp_path):
    res = 0.01
    n = 101
    transform = from_origin(13.0 - res / 2, 51.0 + res / 2, res, res)
    rows, cols = np.mgrid[0:n, 0:n]
    lon_c, lat_c = 13.0 + cols * res, 51.0 - rows * res
    write_tif(tmp_path / f"{tile_name(50, 13)}.tif", 100 + 50 * lon_c + 20 * lat_c, transform)
    (tmp_path / f"{tile_name(50, 14)}.absent").touch()
    lat = np.array([50.123, 50.5, 50.999, 50.5])
    lon = np.array([13.456, 13.0, 13.5, 14.5])
    z = CopernicusGlo30(cache=tmp_path).sample(lat, lon)
    assert z[:3] == pytest.approx(100 + 50 * lon[:3] + 20 * lat[:3], abs=1e-3)
    assert np.isnan(z[3])


def test_simplify_keeps_corners():
    x = np.array([0.0, 1.0, 2.0, 3.0, 3.0, 3.0])
    y = np.array([0.0, 0.01, 0.0, 0.0, 1.0, 2.0])
    assert bridges.simplify(x, y, tolerance=0.1).tolist() == [0, 3, 5]


def test_bridge_along_route_is_matched_crossing_is_not():
    proj = LocalProjection(50.0, 14.0)
    s = np.arange(0.0, 1001.0, 2.0)
    x, y = s.copy(), np.zeros_like(s)
    bearing = np.full_like(s, 90.0)

    def structure(kind, bx, by):
        lat, lon = proj.inverse(np.array(bx), np.array(by))
        return bridges.Structure(kind, "S", np.asarray(lat), np.asarray(lon))

    along = structure("bridge", [400.0, 450.0, 500.0], [3.0, 3.0, 3.0])
    across = structure("bridge", [700.0, 700.0], [-50.0, 50.0])  # route passes under it
    tunnel = structure("tunnel", [800.0, 830.0], [0.0, 0.0])
    intervals = bridges.match_intervals(s, x, y, bearing, [along, across, tunnel], proj)
    assert [(iv.kind, iv.s0, iv.s1) for iv in intervals] == [
        ("bridge", pytest.approx(395.0, abs=2.5), pytest.approx(505.0, abs=2.5)),
        ("tunnel", pytest.approx(795.0, abs=2.5), pytest.approx(835.0, abs=2.5)),
    ]


def test_merge_and_apply_intervals():
    merged = bridges.merge_intervals(
        [bridges.Interval("bridge", "", 10, 30), bridges.Interval("bridge", "B", 25, 40)]
    )
    assert merged == [bridges.Interval("bridge", "B", 10, 40)]
    s = np.arange(0.0, 51.0, 1.0)
    z = np.full_like(s, 100.0)
    z[15:35] = 60.0  # the valley under the bridge
    fixed = bridges.apply_intervals(s, z, merged)
    assert fixed == pytest.approx(np.full_like(s, 100.0))
