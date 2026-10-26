"""The ride as a 1 Hz track: the logged states placed on the course, ready for export."""

from __future__ import annotations

import datetime
import itertools
from dataclasses import dataclass, field

from crt.route.course import Course
from crt.session.session import MOVING_SPEED


@dataclass(frozen=True)
class TrackPoint:
    time: datetime.datetime  # UTC
    lat: float
    lon: float
    elevation: float
    distance: float  # from the start of the ride [m]
    speed: float  # simulated [m/s]
    power: float
    cadence: float | None
    heart_rate: int | None


@dataclass
class Track:
    name: str
    points: list[TrackPoint]
    pauses: list[tuple[datetime.datetime, datetime.datetime]] = field(default_factory=list)

    @property
    def start(self) -> datetime.datetime:
        return self.points[0].time

    @property
    def end(self) -> datetime.datetime:
        return self.points[-1].time

    @property
    def elapsed(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def timer_time(self) -> float:
        """Elapsed time without pauses [s]."""
        return self.elapsed - sum((b - a).total_seconds() for a, b in self.pauses)

    @property
    def distance(self) -> float:
        return self.points[-1].distance

    def paused_between(self, a: TrackPoint, b: TrackPoint) -> bool:
        return any(a.time <= p0 and p1 <= b.time for p0, p1 in self.pauses)

    def intervals(self):
        """Consecutive point pairs that are not separated by a pause."""
        for a, b in itertools.pairwise(self.points):
            if not self.paused_between(a, b):
                yield a, b

    @property
    def moving_time(self) -> float:
        return sum(
            (b.time - a.time).total_seconds() for a, b in self.intervals() if b.speed > MOVING_SPEED
        )

    @property
    def ascent(self) -> float:
        return sum(max(b.elevation - a.elevation, 0.0) for a, b in self.intervals())

    @property
    def descent(self) -> float:
        return sum(max(a.elevation - b.elevation, 0.0) for a, b in self.intervals())


def track_from_log(header: dict, rows: list[dict], course: Course) -> Track:
    """Build the track from a ride log (see ``crt.session.log.read_rows``).

    Only the riding part is kept: from the start event to the finish (or stop),
    without pauses. Cadence comes from the latest trainer sample, everything else
    from the 1 Hz state rows.
    """
    started = datetime.datetime.fromisoformat(header["started"]).astimezone(datetime.UTC)
    start_s = header.get("start_s", 0.0)
    points: list[TrackPoint] = []
    pauses = []
    riding = False
    paused_at: float | None = None
    cadence: float | None = None

    def at(t: float) -> datetime.datetime:
        return started + datetime.timedelta(seconds=t)

    for row in rows:
        kind, t = row["kind"], float(row["t"])
        if kind == "sample":
            cadence = float(row["cadence_rpm"]) if row["cadence_rpm"] else None
        elif kind == "event":
            text = row["command"]
            if text == "start":
                riding = True
            elif text in ("finish", "stop"):
                break
            elif text == "paused" and riding:
                paused_at = t
            elif text == "resumed" and paused_at is not None:
                pauses.append((at(paused_at), at(t)))
                paused_at = None
        elif kind == "state" and riding and paused_at is None:
            s = float(row["s_m"])
            p = course.at(s)
            points.append(
                TrackPoint(
                    at(t), p.lat, p.lon, float(row["z_m"]), s - start_s, float(row["v_mps"]),
                    float(row["power_w"]), cadence,
                    int(row["hr_bpm"]) if row["hr_bpm"] else None,
                )
            )  # fmt: skip
    if not points:
        raise ValueError("the log has no riding")
    return Track(header.get("course_name", course.name), points, pauses)
