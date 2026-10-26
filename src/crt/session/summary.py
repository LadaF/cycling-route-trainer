"""Plain-text ride summary, shared by the CLI and the GUI."""

from __future__ import annotations

from crt.session.session import RideSnapshot


def hms(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def summary_lines(snap: RideSnapshot, course_length: float) -> list[str]:
    avg_speed = snap.s / snap.moving_time * 3.6 if snap.moving_time > 0 else 0.0
    avg_power = snap.work / snap.elapsed if snap.elapsed > 0 else 0.0
    braked = f", braked {snap.braking / 1000:.0f} kJ" if snap.braking > 0 else ""
    return [
        f"distance {snap.s / 1000:.2f} of {course_length / 1000:.2f} km",
        f"time {hms(snap.elapsed)}, moving {hms(snap.moving_time)}",
        f"average speed {avg_speed:.1f} km/h, average power {avg_power:.0f} W",
        f"ascent {snap.ascent:.0f} m, work {snap.work / 1000:.0f} kJ{braked}",
    ]
