from pathlib import Path

import numpy as np
import pytest

from crt.route.course import Course
from crt.route.geometry import (
    LocalProjection,
    bearing_deg,
    cumulative_distance,
    drop_close_points,
    resample,
)
from crt.route.parsers import RawRoute, RouteFormatError, read_route
from crt.route.pipeline import PipelineOptions, build_course

GPX_TRACK = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
 <trk><name>Test track</name>
  <trkseg>
   <trkpt lat="50.0" lon="14.0"><ele>200.0</ele></trkpt>
   <trkpt lat="50.001" lon="14.0"><ele>201.0</ele></trkpt>
  </trkseg>
  <trkseg>
   <trkpt lat="50.002" lon="14.0"><ele>202.5</ele></trkpt>
  </trkseg>
 </trk>
</gpx>
"""

GPX_ROUTE_NO_ELE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
 <rte><name>Planned</name>
  <rtept lat="49.0" lon="16.0"/>
  <rtept lat="49.0" lon="16.01"/>
 </rte>
</gpx>
"""

TCX = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
 <Courses><Course><Name>Course A</Name><Track>
  <Trackpoint><Position><LatitudeDegrees>50.0</LatitudeDegrees>
   <LongitudeDegrees>14.0</LongitudeDegrees></Position><AltitudeMeters>300</AltitudeMeters>
  </Trackpoint>
  <Trackpoint><Time>2020-01-01T00:00:00Z</Time></Trackpoint>
  <Trackpoint><Position><LatitudeDegrees>50.01</LatitudeDegrees>
   <LongitudeDegrees>14.0</LongitudeDegrees></Position><AltitudeMeters>310</AltitudeMeters>
  </Trackpoint>
 </Track></Course></Courses>
</TrainingCenterDatabase>
"""


def test_read_gpx_track_joins_segments(tmp_path):
    path = tmp_path / "a.gpx"
    path.write_text(GPX_TRACK)
    r = read_route(path)
    assert r.name == "Test track"
    assert r.lat.tolist() == [50.0, 50.001, 50.002]
    assert r.ele.tolist() == [200.0, 201.0, 202.5]


def test_read_gpx_route_without_elevation(tmp_path):
    path = tmp_path / "b.GPX"
    path.write_text(GPX_ROUTE_NO_ELE)
    r = read_route(path)
    assert r.name == "Planned"
    assert r.ele is None
    assert len(r.lon) == 2


def test_read_tcx_skips_points_without_position(tmp_path):
    path = tmp_path / "c.tcx"
    path.write_text(TCX)
    r = read_route(path)
    assert r.name == "Course A"
    assert r.lat.tolist() == [50.0, 50.01]
    assert r.ele.tolist() == [300.0, 310.0]


def test_unsupported_and_too_short(tmp_path):
    with pytest.raises(RouteFormatError):
        read_route(tmp_path / "x.kml")
    path = tmp_path / "one.gpx"
    path.write_text(GPX_ROUTE_NO_ELE.replace('  <rtept lat="49.0" lon="16.01"/>\n', ""))
    with pytest.raises(RouteFormatError):
        read_route(path)


def test_drop_close_points_and_resample():
    x = np.array([0.0, 0.5, 1.0, 3.0, 3.2, 6.0])
    keep = drop_close_points(x, np.zeros_like(x), 2.0)
    assert keep.tolist() == [0, 3, 5]
    grid, (v,) = resample(np.array([0.0, 10.0]), 4.0, np.array([0.0, 100.0]))
    assert grid.tolist() == [0.0, 4.0, 8.0, 10.0]
    assert v.tolist() == [0.0, 40.0, 80.0, 100.0]


def test_projection_distance_and_bearing():
    lat = np.array([50.0, 50.01])
    lon = np.array([14.0, 14.0])
    proj = LocalProjection.for_points(lat, lon)
    x, y = proj.forward(lat, lon)
    assert cumulative_distance(np.asarray(x), np.asarray(y))[-1] == pytest.approx(1112.4, abs=1.0)
    lat2, lon2 = proj.inverse(x, y)
    assert np.allclose(lat2, lat) and np.allclose(lon2, lon)
    assert bearing_deg(np.array([0.0, 0.0]), np.array([0.0, 1.0])).tolist() == [0.0, 0.0]
    assert bearing_deg(np.array([0.0, 1.0]), np.array([0.0, 0.0])).tolist() == [90.0, 90.0]


def make_course() -> Course:
    s = np.arange(0.0, 101.0, 10.0)
    z = np.array([0, 1, 2, 3, 4, 5, 4, 3, 3, 3, 3], dtype=float)
    n = len(s)
    return Course(
        "test", s, np.linspace(50, 50.001, n), np.full(n, 14.0), z, np.gradient(z, s),
        np.full(n, 0.0), z, np.full(n, np.nan), {"note": "x"},
    )  # fmt: skip


def test_course_queries():
    c = make_course()
    assert c.length == 100.0
    assert c.total_ascent == 5.0
    assert c.at(25.0).z == pytest.approx(2.5)
    assert c.at(-5.0).s == 0.0 and c.at(500.0).s == 100.0
    assert c.mean_grade(0.0, 50.0) == pytest.approx(0.1)
    assert c.mean_grade(50.0, 80.0) == pytest.approx(-2 / 30)
    assert c.mean_grade(90.0, 200.0) == pytest.approx(0.0)


def test_course_save_load(tmp_path):
    c = make_course()
    path = tmp_path / "c.crt.npz"
    c.save(path)
    assert path.exists()
    d = Course.load(path)
    assert d.name == "test" and d.meta == {"note": "x"}
    assert np.array_equal(d.z, c.z)
    assert np.isnan(d.z_file).all()


class SlopeProvider:
    """5 % climb towards the north, starting at 100 m at latitude 50."""

    name = "slope"

    def sample(self, lat, lon):
        return 100.0 + (np.asarray(lat) - 50.0) * 111_250.0 * 0.05


def test_pipeline_with_fake_dem(tmp_path):
    lat = np.linspace(50.0, 50.009, 50)
    raw = RawRoute("north", lat, np.full(50, 14.0), None)
    c = build_course(
        Path("north.gpx"), PipelineOptions(fix_bridges=False), providers=[SlopeProvider()], raw=raw
    )
    assert c.length == pytest.approx(1000.0, rel=0.002)
    assert c.s[1] == pytest.approx(2.0)
    middle = (c.s > 100) & (c.s < 900)
    assert np.allclose(c.grade[middle], 0.05, rtol=0.01)
    assert c.meta["elevation_coverage"] == {"slope": 1.0}
    assert c.meta["structures_checked"] is False


def test_pipeline_falls_back_to_file_elevation(tmp_path):
    lat = np.linspace(50.0, 50.009, 50)
    raw = RawRoute("north", lat, np.full(50, 14.0), np.linspace(200, 250, 50))

    class Nothing:
        name = "nothing"

        def sample(self, lat, lon):
            return np.full(len(lat), np.nan)

    c = build_course(
        Path("x.gpx"), PipelineOptions(fix_bridges=False), providers=[Nothing()], raw=raw
    )
    assert c.z_raw[0] == pytest.approx(200.0) and c.z_raw[-1] == pytest.approx(250.0)
    assert c.meta["elevation_coverage"]["route file"] == 1.0


def encode_fit(name: str, points: list[tuple[float, float, float | None]]) -> bytes:
    """Minimal FIT course file: a course message with the name, then records."""
    import struct

    from fitdecode.utils import compute_crc

    def definition(local: int, global_num: int, fields: list[tuple[int, int, int]]) -> bytes:
        body = struct.pack("<BBHB", 0, 0, global_num, len(fields))
        return bytes([0x40 | local]) + body + b"".join(bytes(f) for f in fields)

    semi = 2**31 / 180.0
    data = definition(0, 31, [(5, 16, 0x07)])  # course.name, string[16]
    data += b"\x00" + name.encode().ljust(16, b"\x00")
    # record: position_lat, position_long (sint32 semicircles), altitude (uint16, x5 +500)
    data += definition(1, 20, [(0, 4, 0x85), (1, 4, 0x85), (2, 2, 0x84)])
    for lat, lon, alt in points:
        raw_alt = 0xFFFF if alt is None else round((alt + 500) * 5)  # 0xFFFF = invalid
        data += b"\x01" + struct.pack("<iiH", round(lat * semi), round(lon * semi), raw_alt)
    header = struct.pack("<BBHI4s", 14, 0x10, 2132, len(data), b".FIT")
    header += struct.pack("<H", compute_crc(header))
    return header + data + struct.pack("<H", compute_crc(header + data))


def test_read_fit_course(tmp_path):
    path = tmp_path / "course.fit"
    path.write_bytes(
        encode_fit(
            "Troja", [(50.12, 14.44, 300.0), (50.121, 14.441, None), (50.122, 14.442, 290.4)]
        )
    )
    r = read_route(path)
    assert r.name == "Troja"
    assert r.lat == pytest.approx([50.12, 50.121, 50.122], abs=1e-7)
    assert r.lon == pytest.approx([14.44, 14.441, 14.442], abs=1e-7)
    assert r.ele[0] == pytest.approx(300.0) and np.isnan(r.ele[1])
    assert r.ele[2] == pytest.approx(290.4)
