"""Simulated trainer: a scripted rider for rides and tests without hardware."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import numpy as np

from crt.control.policy import Command, GradeCommand
from crt.devices.base import ConnectionCallback, SampleCallback, TrainerSample


def constant_power(watts: float) -> Callable[[float], float]:
    return lambda _t: watts


def power_from_csv(path: Path) -> Callable[[float], float]:
    """Power profile from a CSV with columns ``time_s,power_w``, held constant between
    rows. Lines that are not two numbers (header, comments) are skipped."""
    rows = []
    for line in path.read_text().splitlines():
        try:
            t, p = (float(v) for v in line.split(",")[:2])
        except ValueError:
            continue
        rows.append((t, p))
    if not rows:
        raise ValueError(f"{path}: no time_s,power_w rows")
    t_arr, p_arr = np.array(rows).T

    def power(now: float) -> float:
        i = int(np.searchsorted(t_arr, now, side="right")) - 1
        return float(p_arr[max(i, 0)])

    return power


class MockTrainer:
    """Emits power samples at ``rate_hz`` (the Saris H3 sends 1 Hz) and records the
    commands it receives."""

    supports_simulation = True

    def __init__(
        self,
        power: Callable[[float], float],
        clock: Callable[[], float],
        sleep: Callable[[float], object],
        on_sample: SampleCallback,
        on_connection: ConnectionCallback,
        *,
        rate_hz: float = 1.0,
        cadence: float = 85.0,
    ) -> None:
        self._power = power
        self._clock = clock
        self._sleep = sleep
        self._on_sample = on_sample
        self._on_connection = on_connection
        self._interval = 1.0 / rate_hz
        self._cadence = cadence
        self.commands: list[tuple[float, Command]] = []
        self.connected = False

    async def run(self) -> None:
        self.connected = True
        self._on_connection(True)
        while True:
            now = self._clock()
            self._on_sample(TrainerSample(now, self._power(now), self._cadence, None))
            await self._sleep(self._interval)

    async def send(self, command: Command) -> bool:
        self.commands.append((self._clock(), command))
        return True

    async def finish(self) -> None:
        await asyncio.sleep(0)

    @property
    def last_grade(self) -> float | None:
        for _t, cmd in reversed(self.commands):
            if isinstance(cmd, GradeCommand):
                return cmd.params.grade_percent
        return None
