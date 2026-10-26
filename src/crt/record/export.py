"""Export a ride log to FIT and GPX files next to it."""

from __future__ import annotations

from pathlib import Path

from crt.record.fit import write_fit
from crt.record.gpx import write_gpx
from crt.record.track import track_from_log
from crt.route.course import Course
from crt.session.log import read_rows

WRITERS = {"fit": write_fit, "gpx": write_gpx}


def export_ride(log_file: Path, formats: list[str], course: Course | None = None) -> list[Path]:
    """Write the ride in each format (``fit``, ``gpx``) next to its log.

    Without ``course``, the course named in the log is loaded. Raises ``ValueError``
    if the log has no riding.
    """
    header, rows = read_rows(log_file)
    track = track_from_log(header, rows, course or Course.load(Path(header["course"])))
    paths = []
    for fmt in formats:
        path = log_file.with_suffix(f".{fmt}")
        WRITERS[fmt](track, path)
        paths.append(path)
    return paths
