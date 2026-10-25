"""Resistance policies: from the rider's position on the course to trainer commands.

The minimum applies only to what the trainer is told. The physics always uses the
true terrain grade.
"""

from __future__ import annotations

from dataclasses import dataclass

from crt.devices.ftms.codec import SimulationParameters
from crt.physics.model import RiderParams, air_density
from crt.route.course import Course


@dataclass(frozen=True)
class GradeCommand:
    params: SimulationParameters


@dataclass(frozen=True)
class LevelCommand:
    level: float


Command = GradeCommand | LevelCommand


@dataclass(frozen=True)
class ControlSettings:
    min_grade: float = 0.0  # % — resistance never below this grade
    difficulty: float = 1.0  # scales the terrain grade, 0…1
    lookahead_s: float = 1.0  # trainer response time (the H3 reacts almost at once)
    max_grade: float = 20.0  # % — trainer limits
    lowest_grade: float = -10.0  # %
    max_rate_hz: float = 2.0
    min_change: float = 0.1  # % — smaller changes are not sent…
    keepalive_s: float = 5.0  # …unless this long has passed since the last command
    # Resistance-level fallback, for trainers without simulation mode (uncalibrated)
    level_at_flat: float = 2.0
    level_per_percent: float = 0.5
    min_level: float = 1.0
    max_level: float = 10.0


class GradePolicy:
    """FTMS simulation mode: send the (clamped) grade plus the rider's Crr and Cw."""

    def __init__(self, course: Course, settings: ControlSettings, rider: RiderParams) -> None:
        self.course = course
        self.settings = settings
        self.rider = rider
        self._last: float | None = None
        self._last_time = -1e9

    def target_grade(self, s: float, v: float) -> float:
        """Grade to command [%] at position ``s`` and speed ``v``."""
        st = self.settings
        ahead = v * st.lookahead_s
        grade = self.course.mean_grade(s, s + ahead) if ahead > 1.0 else self.course.at(s).grade
        g = st.difficulty * grade * 100
        g = max(g, st.min_grade)
        return round(min(max(g, st.lowest_grade), st.max_grade), 2)

    def _due(self, now: float, value: float, threshold: float) -> bool:
        st = self.settings
        if now - self._last_time < 1.0 / st.max_rate_hz:
            return False
        return (
            self._last is None
            or abs(value - self._last) >= threshold
            or now - self._last_time >= st.keepalive_s
        )

    def decide(self, now: float, s: float, v: float) -> Command | None:
        g = self.target_grade(s, v)
        if not self._due(now, g, self.settings.min_change):
            return None
        self._last, self._last_time = g, now
        rho = air_density(self.course.at(s).z, self.rider)
        return GradeCommand(
            SimulationParameters(
                grade_percent=g,
                crr=self.rider.crr,
                cw_kg_per_m=round(0.5 * rho * self.rider.cda, 2),
            )
        )

    def reset(self) -> None:
        """Forget the last command, so the next decision is sent (e.g. after reconnect)."""
        self._last, self._last_time = None, -1e9


class LevelPolicy(GradePolicy):
    """Fallback for trainers without simulation mode: map the grade to a resistance level."""

    def decide(self, now: float, s: float, v: float) -> Command | None:
        st = self.settings
        g = self.target_grade(s, v)
        level = st.level_at_flat + st.level_per_percent * g
        level = round(min(max(level, st.min_level), st.max_level), 1)
        if not self._due(now, level, 0.1):
            return None
        self._last, self._last_time = level, now
        return LevelCommand(level)
