"""Raw ride log: every trainer and HR sample, every command, and the state at 1 Hz.

CSV with a ``#``-prefixed JSON header holding the ride settings, so a ride can be
replayed with the same course and parameters.
"""

from __future__ import annotations

import csv
import datetime
import json
from dataclasses import asdict
from pathlib import Path
from typing import IO

import platformdirs

from crt.control.policy import Command, ControlSettings, GradeCommand, LevelCommand
from crt.devices.base import HeartRateSample, TrainerSample
from crt.physics.model import RiderParams
from crt.session.session import RideSnapshot

COLUMNS = [
    "t", "kind", "power_w", "cadence_rpm", "trainer_speed_kmh", "hr_bpm",
    "s_m", "v_mps", "z_m", "terrain_grade_pct", "command",
]  # fmt: skip


def _num(value: float | None, digits: int = 3) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def default_log_path() -> Path:
    """A new file named after the current time in the user data directory."""
    rides = Path(platformdirs.user_data_dir("crt")) / "rides"
    rides.mkdir(parents=True, exist_ok=True)
    return rides / f"{datetime.datetime.now():%Y-%m-%d_%H%M%S}.csv"


def ride_header(
    course_file: Path, course_name: str, trainer: str, start_s: float,
    rider: RiderParams, control: ControlSettings,
) -> dict:  # fmt: skip
    """Everything ``replay`` and ``export`` need to rebuild the ride."""
    return {
        "course": str(course_file.resolve()), "course_name": course_name, "trainer": trainer,
        "started": datetime.datetime.now().isoformat(), "start_s": start_s,
        "rider": asdict(rider), "control": asdict(control),
    }  # fmt: skip


class RideLog:
    def __init__(self, path: Path, header: dict) -> None:
        self.path = path
        self._file: IO[str] = path.open("w", newline="", encoding="utf-8")
        self._file.write("# " + json.dumps(header) + "\n")
        self._writer = csv.DictWriter(self._file, COLUMNS)
        self._writer.writeheader()

    def _row(self, t: float, kind: str, **fields: str) -> None:
        self._writer.writerow({"t": f"{t:.3f}", "kind": kind, **fields})

    def sample(self, s: TrainerSample) -> None:
        self._row(
            s.t, "sample", power_w=_num(s.power, 1), cadence_rpm=_num(s.cadence, 1),
            trainer_speed_kmh=_num(s.speed_kmh, 2),
        )  # fmt: skip

    def heart_rate(self, s: HeartRateSample) -> None:
        self._row(s.t, "hr", hr_bpm=str(s.bpm))

    def command(self, t: float, command: Command) -> None:
        match command:
            case GradeCommand(params):
                text = json.dumps(asdict(params))
            case LevelCommand(level):
                text = json.dumps({"level": level})
        self._row(t, "command", command=text)

    def state(self, t: float, snap: RideSnapshot) -> None:
        self._row(
            t, "state", power_w=_num(snap.power, 1), s_m=_num(snap.s, 2), v_mps=_num(snap.v),
            z_m=_num(snap.elevation, 2), terrain_grade_pct=_num(snap.terrain_grade, 2),
            hr_bpm="" if snap.heart_rate is None else str(snap.heart_rate),
        )  # fmt: skip

    def event(self, t: float, text: str) -> None:
        self._row(t, "event", command=text)

    def close(self) -> None:
        self._file.close()


def read_rows(path: Path) -> tuple[dict, list[dict]]:
    """Header and all rows of a ride log, as strings."""
    with path.open(encoding="utf-8") as f:
        first = f.readline()
        if not first.startswith("# "):
            raise ValueError(f"{path} is not a ride log")
        return json.loads(first[2:]), list(csv.DictReader(f))


def read_log(path: Path) -> tuple[dict, list[TrainerSample], list[dict]]:
    """Header, trainer samples and state rows of a ride log."""
    header, rows = read_rows(path)
    samples, states = [], []
    for row in rows:
        if row["kind"] == "sample":
            samples.append(
                TrainerSample(
                    float(row["t"]),
                    float(row["power_w"]),
                    float(row["cadence_rpm"]) if row["cadence_rpm"] else None,
                    float(row["trainer_speed_kmh"]) if row["trainer_speed_kmh"] else None,
                )
            )
        elif row["kind"] == "state":
            states.append(row)
    return header, samples, states
