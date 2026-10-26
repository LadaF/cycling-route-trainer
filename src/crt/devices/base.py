"""Interfaces between the ride session and the devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from crt.control.policy import Command


@dataclass(frozen=True, slots=True)
class TrainerSample:
    t: float  # session clock [s]
    power: float  # W
    cadence: float | None = None  # rpm
    speed_kmh: float | None = None  # the trainer's own speed; logged, never used


@dataclass(frozen=True, slots=True)
class HeartRateSample:
    t: float
    bpm: int


SampleCallback = Callable[[TrainerSample], None]
ConnectionCallback = Callable[[bool], None]


class Trainer(Protocol):
    supports_simulation: bool
    connected: bool

    async def run(self) -> None:
        """Connect, take control and keep the connection alive until cancelled."""
        ...

    async def send(self, command: Command) -> bool:
        """Send a command; False if the trainer did not accept it."""
        ...

    async def finish(self) -> None:
        """Leave the trainer in a neutral state at the end of a ride."""
        ...
