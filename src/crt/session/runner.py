"""Runs a ride session in (optionally accelerated) real time with real or mock devices."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import replace

from crt.control.policy import Command, LevelPolicy
from crt.devices.base import HeartRateSample, Trainer, TrainerSample
from crt.session.log import RideLog
from crt.session.session import RideSession, RideSnapshot, RideState

log = logging.getLogger(__name__)


class Clock:
    """Session time in seconds since creation; ``speedup`` > 1 only makes sense with
    the mock trainer."""

    def __init__(self, speedup: float = 1.0) -> None:
        self.speedup = speedup
        self._t0 = time.monotonic()

    def now(self) -> float:
        return (time.monotonic() - self._t0) * self.speedup

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds / self.speedup)


class RideRunner:
    """Connects devices to the session. Device objects are created with this runner's
    callbacks (``trainer_sample`` etc.) and then passed to ``run``."""

    def __init__(self, session: RideSession, clock: Clock, ride_log: RideLog | None = None):
        self.session = session
        self.clock = clock
        self.log = ride_log
        self._pending: Command | None = None
        self._wake = asyncio.Event()
        self._stop = False
        self._state = session.state

    def _note_state(self, now: float) -> None:
        """Log pauses and resumptions, whatever caused them, so exports can leave
        the paused time out."""
        state = self.session.state
        if self.log and state is not self._state:
            if state is RideState.PAUSED:
                self.log.event(now, "paused")
            elif self._state is RideState.PAUSED and state is RideState.RIDING:
                self.log.event(now, "resumed")
        self._state = state

    # -- device callbacks -----------------------------------------------------

    def trainer_sample(self, sample: TrainerSample) -> None:
        self.session.add_sample(sample)
        if self.log:
            self.log.sample(sample)

    def trainer_connection(self, up: bool) -> None:
        now = self.clock.now()
        self.session.set_trainer_connected(up, now)
        if self.log:
            self.log.event(now, f"trainer {'connected' if up else 'disconnected'}")
        self._note_state(now)

    def hr_sample(self, sample: HeartRateSample) -> None:
        self.session.add_heart_rate(sample)
        if self.log:
            self.log.heart_rate(sample)

    def hr_connection(self, up: bool) -> None:
        self.session.set_hr_connected(up)
        if self.log:
            self.log.event(self.clock.now(), f"heart rate {'connected' if up else 'disconnected'}")

    # -- rider controls -------------------------------------------------------

    def pause(self) -> None:
        now = self.clock.now()
        self.session.pause(now)
        self._note_state(now)

    def resume(self) -> None:
        now = self.clock.now()
        self.session.resume(now)
        self._note_state(now)

    def stop(self) -> None:
        """End the ride before the finish; ``run`` then returns as usual."""
        self._stop = True

    def set_difficulty(self, difficulty: float) -> None:
        """Scale the grade sent to the trainer from now on (the physics keeps the true
        grade); the new grade is sent at once."""
        policy = self.session.policy
        policy.settings = replace(policy.settings, difficulty=difficulty)
        policy.reset()
        if self.log:
            self.log.event(self.clock.now(), f"difficulty {difficulty * 100:.0f} %")

    # -- loops ----------------------------------------------------------------

    def _check_simulation_support(self, trainer: Trainer, now: float) -> None:
        """Switch to resistance levels once the trainer reports no simulation mode."""
        policy = self.session.policy
        if not trainer.supports_simulation and not isinstance(policy, LevelPolicy):
            self.session.policy = LevelPolicy(policy.course, policy.settings, policy.rider)
            log.warning("trainer has no simulation mode: using resistance levels")
            if self.log:
                self.log.event(now, "resistance levels")

    async def _sender(self, trainer: Trainer) -> None:
        """Sends only the latest command: older ones are obsolete if the trainer is slow."""
        while True:
            await self._wake.wait()
            self._wake.clear()
            command, self._pending = self._pending, None
            if command is not None:
                sent_at = self.clock.now()
                ok = await trainer.send(command)
                if self.log:
                    self.log.command(sent_at, command)
                    if not ok:
                        self.log.event(self.clock.now(), "command failed")

    async def run(
        self,
        trainer: Trainer,
        hr=None,
        *,
        on_update: Callable[[RideSnapshot], None] | None = None,
        tick_hz: float = 20.0,
        update_hz: float = 4.0,
    ) -> RideSnapshot:
        tasks = [asyncio.create_task(trainer.run()), asyncio.create_task(self._sender(trainer))]
        if hr is not None:
            tasks.append(asyncio.create_task(hr.run()))
        try:
            while not self.session.trainer_connected:
                if self._stop:
                    return self.session.snapshot()
                if on_update:
                    on_update(self.session.snapshot())
                await asyncio.sleep(0.2)
            self.session.start(self.clock.now())
            self._state = self.session.state
            if self.log:
                self.log.event(self.clock.now(), "start")

            next_state = next_update = 0.0
            while not self.session.finished and not self._stop:
                now = self.clock.now()
                self._check_simulation_support(trainer, now)
                command = self.session.tick(now)
                if command is not None:
                    self._pending = command
                    self._wake.set()
                if self.log and now >= next_state:
                    self.log.state(now, self.session.snapshot())
                    next_state = now + 1.0
                if on_update and now >= next_update:
                    on_update(self.session.snapshot())
                    next_update = now + self.clock.speedup / update_hz
                await self.clock.sleep(1.0 / tick_hz)

            if self.log:
                self.log.state(self.clock.now(), self.session.snapshot())
                self.log.event(self.clock.now(), "finish" if self.session.finished else "stop")
            if self.session.trainer_connected:
                await trainer.finish()
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        return self.session.snapshot()


def replay(
    session: RideSession,
    samples: list[TrainerSample],
    *,
    until: float | None = None,
    tick_hz: float = 20.0,
) -> RideSnapshot:
    """Run a session over recorded samples in simulated time (no devices, no waiting),
    up to ``until`` (default: the last sample)."""
    session.set_trainer_connected(True, 0.0)
    start = samples[0].t if samples else 0.0
    session.start(start)
    end = until if until is not None else (samples[-1].t if samples else 0.0)
    i, now = 0, start
    while not session.finished and now <= end:
        while i < len(samples) and samples[i].t <= now:
            session.add_sample(samples[i])
            i += 1
        session.tick(now)
        now += 1.0 / tick_hz
    return session.snapshot()
