import datetime
import io

import fitdecode
import gpxpy
import pytest

from crt.record.fit import fit_bytes
from crt.record.gpx import TPX, gpx_xml
from crt.record.track import track_from_log

HEADER = {"course_name": "test ride", "started": "2026-10-26T18:00:00+01:00", "start_s": 100.0}


def row(t, kind, **fields):
    empty = dict.fromkeys(["power_w", "cadence_rpm", "trainer_speed_kmh", "hr_bpm", "s_m",
                           "v_mps", "z_m", "terrain_grade_pct", "command"], "")  # fmt: skip
    return {**empty, "t": f"{t:.3f}", "kind": kind, **fields}


def state(t, s, v=5.0, power=200.0, hr="140"):
    return row(t, "state", power_w=str(power), s_m=str(s), v_mps=str(v), z_m=str(200 + s / 100),
               hr_bpm=hr)  # fmt: skip


@pytest.fixture
def rows():
    """20 s riding, a 10 s pause, 10 s more riding."""
    rows = [state(0.0, 100.0, v=0.0), row(0.5, "event", command="start")]
    for i in range(21):
        rows.append(row(i + 0.9, "sample", power_w="200", cadence_rpm="90"))
        rows.append(state(i + 1.0, 100.0 + 5 * i))
    rows.append(row(21.5, "event", command="paused"))
    rows += [state(22.0 + i, 200.0, v=0.0, power=0.0) for i in range(10)]
    rows.append(row(31.5, "event", command="resumed"))
    for i in range(11):
        rows.append(state(32.0 + i, 200.0 + 5 * i))
    rows.append(row(42.5, "event", command="finish"))
    rows.append(state(43.0, 999.0))  # after the finish: ignored
    return rows


@pytest.fixture
def track(rows, make_course):
    return track_from_log(HEADER, rows, make_course())


def test_track_keeps_only_riding(track):
    assert len(track.points) == 32
    assert track.start == datetime.datetime(2026, 10, 26, 17, 0, 1, tzinfo=datetime.UTC)
    assert track.points[0].distance == 0.0  # measured from start_s
    assert track.distance == pytest.approx(150.0)
    assert track.elapsed == pytest.approx(41.0)
    assert track.timer_time == pytest.approx(31.0)  # the 10 s pause is left out
    assert track.moving_time == pytest.approx(30.0)  # the gap across the pause is not moving
    assert track.ascent == pytest.approx(1.5)
    assert track.points[0].cadence == 90.0 and track.points[0].heart_rate == 140


def test_track_without_riding(make_course):
    with pytest.raises(ValueError):
        track_from_log(HEADER, [state(0.0, 100.0)], make_course())


def test_fit_decodes_as_virtual_ride(track):
    messages = []
    with fitdecode.FitReader(io.BytesIO(fit_bytes(track))) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                messages.append(frame)
    records = [m for m in messages if m.name == "record"]
    assert len(records) == len(track.points)
    assert records[0].get_value("heart_rate") == 140
    assert records[0].get_value("power") == 200
    assert records[-1].get_value("distance") == pytest.approx(150.0)
    assert records[0].get_value("position_lat") * 180 / 2**31 == pytest.approx(
        track.points[0].lat, abs=1e-6
    )
    timer = [m.get_value("event_type") for m in messages if m.name == "event"]
    assert timer == ["start", "stop_all", "start", "stop_all"]
    (session,) = [m for m in messages if m.name == "session"]
    assert session.get_value("sport") == "cycling"
    assert session.get_value("sub_sport") == "virtual_activity"
    assert session.get_value("total_timer_time") == pytest.approx(31.0)
    assert session.get_value("total_distance") == pytest.approx(150.0)
    assert [m.name for m in messages][-3:] == ["lap", "session", "activity"]


def test_gpx_splits_segments_at_pauses(track):
    text = gpx_xml(track)
    gpx = gpxpy.parse(text)
    segments = gpx.tracks[0].segments
    assert [len(s.points) for s in segments] == [21, 11]
    assert gpx.tracks[0].name == "test ride"
    assert f'xmlns:gpxtpx="{TPX}"' in text
    assert "<power>200</power>" in text
    assert "<gpxtpx:hr>140</gpxtpx:hr>" in text and "<gpxtpx:cad>90</gpxtpx:cad>" in text
