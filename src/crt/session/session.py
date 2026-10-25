"""The ride session core. Synchronous and clock-agnostic: the caller passes the time
to every method, so tests and replays can run in simulated time."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from crt.control.policy import Command, GradeCommand, GradePolicy, LevelCommand
from crt.devices.base import HeartRateSample, TrainerSample
from crt.physics.model import MotionState, RiderParams, step
from crt.route.course import Course

MOVING_SPEED = 0.3  # m/s; below this the rider counts as stopped


class RideState(enum.Enum):
    READY = "ready"
    RIDING = "riding"
    PAUSED = "paused"
    FINISHED = "finished"


@dataclass(frozen=True)
class RideSnapshot:
    state: RideState
    elapsed: float  # riding time, pauses excluded [s]
    moving_time: float
    s: float
    remaining: float
    v: float  # simulated speed [m/s]
    power: float  # as used by the physics (0 when samples are stale)
    cadence: float | None
    trainer_speed_kmh: float | None
    heart_rate: int | None
    elevation: float
    terrain_grade: float  # %
    command: Command | None  # last command sent
    ascent: float
    work: float  # J
    braking: float  # J
    trainer_connected: bool
    hr_connected: bool


class RideSession:
    def __init__(
        self,
        course: Course,
        rider: RiderParams,
        policy: GradePolicy,
        *,
        start_s: float = 0.0,
        stale_after: float = 3.0,
        max_step: float = 0.05,
    ) -> None:
        self.course = course
        self.rider = rider
        self.policy = policy
        self.stale_after = stale_after
        self.max_step = max_step
        self.state = RideState.READY
        self.motion = MotionState(s=min(max(start_s, 0.0), course.length))
        self._last_tick: float | None = None
        self._sample: TrainerSample | None = None
        self._hr: HeartRateSample | None = None
        self._command: Command | None = None
        self._paused_by_disconnect = False
        self._elapsed = 0.0
        self._moving = 0.0
        self._ascent = 0.0
        self._power_used = 0.0
        self.trainer_connected = False
        self.hr_connected = False

    # -- inputs ---------------------------------------------------------------

    def add_sample(self, sample: TrainerSample) -> None:
        self._sample = sample

    def add_heart_rate(self, sample: HeartRateSample) -> None:
        self._hr = sample

    def set_trainer_connected(self, up: bool, now: float) -> None:
        """A lost trainer pauses the ride; the reconnected one resumes it."""
        self.trainer_connected = up
        if not up and self.state is RideState.RIDING:
            self.pause(now)
            self._paused_by_disconnect = True
        elif up:
            self.policy.reset()  # the new connection must get the current grade
            if self.state is RideState.PAUSED and self._paused_by_disconnect:
                self.resume(now)

    def set_hr_connected(self, up: bool) -> None:
        self.hr_connected = up

    # -- state machine --------------------------------------------------------

    def start(self, now: float) -> None:
        if self.state is RideState.READY:
            self.state = RideState.RIDING
            self._last_tick = now

    def pause(self, now: float) -> None:
        if self.state is RideState.RIDING:
            self.tick(now)
            self.state = RideState.PAUSED
            self._paused_by_disconnect = False

    def resume(self, now: float) -> None:
        if self.state is RideState.PAUSED:
            self.state = RideState.RIDING
            self._paused_by_disconnect = False
            self._last_tick = now
            # Restart from a standstill: speed is not kept through a pause.
            self.motion = MotionState(self.motion.s, 0.0, self.motion.work, self.motion.braking)
            self.policy.reset()

    @property
    def finished(self) -> bool:
        return self.state is RideState.FINISHED

    # -- simulation -----------------------------------------------------------

    def _power(self, now: float) -> float:
        sample = self._sample
        if sample is None or now - sample.t > self.stale_after:
            return 0.0
        return sample.power

    def tick(self, now: float) -> Command | None:
        """Advance the physics to ``now``; return a command for the trainer, if due."""
        if self.state is not RideState.RIDING or self._last_tick is None:
            return None
        dt_total = max(now - self._last_tick, 0.0)
        self._last_tick = now
        power = self._power(now)
        self._power_used = power
        z_before = self.course.at(self.motion.s).z

        remaining = dt_total
        while remaining > 1e-9:
            dt = min(self.max_step, remaining)
            self.motion = step(self.motion, power, dt, self.course, self.rider)
            remaining -= dt
            if self.motion.v > MOVING_SPEED:
                self._moving += dt
            if self.motion.s >= self.course.length:
                break
        self._elapsed += dt_total - max(remaining, 0.0)
        dz = self.course.at(self.motion.s).z - z_before
        if dz > 0:
            self._ascent += dz

        if self.motion.s >= self.course.length:
            self.motion = MotionState(
                self.course.length, self.motion.v, self.motion.work, self.motion.braking
            )
            self.state = RideState.FINISHED
            return None
        command = self.policy.decide(now, self.motion.s, self.motion.v)
        if command is not None:
            self._command = command
        return command

    def snapshot(self) -> RideSnapshot:
        point = self.course.at(self.motion.s)
        sample = self._sample
        return RideSnapshot(
            state=self.state,
            elapsed=self._elapsed,
            moving_time=self._moving,
            s=self.motion.s,
            remaining=self.course.length - self.motion.s,
            v=self.motion.v,
            power=self._power_used,
            cadence=sample.cadence if sample else None,
            trainer_speed_kmh=sample.speed_kmh if sample else None,
            heart_rate=self._hr.bpm if self._hr else None,
            elevation=point.z,
            terrain_grade=point.grade * 100,
            command=self._command,
            ascent=self._ascent,
            work=self.motion.work,
            braking=self.motion.braking,
            trainer_connected=self.trainer_connected,
            hr_connected=self.hr_connected,
        )


def describe_command(command: Command | None) -> str:
    match command:
        case GradeCommand(params):
            return f"{params.grade_percent:+.1f} %"
        case LevelCommand(level):
            return f"level {level:.1f}"
    return "–"
