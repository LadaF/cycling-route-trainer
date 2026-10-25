"""Heart Rate Service (0x180D): measurement codec and BLE client."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from bleak import BleakClient

from crt.devices import uuids
from crt.devices.codec import Reader


@dataclass(frozen=True, slots=True)
class HeartRateMeasurement:
    bpm: int
    contact: bool | None  # None when the sensor does not report skin contact
    energy_expended_kj: int | None = None
    rr_intervals_s: tuple[float, ...] = ()


def parse_heart_rate_measurement(data: bytes | bytearray) -> HeartRateMeasurement:
    r = Reader(data)
    flags = r.u8()
    bpm = r.u16() if flags & 0x01 else r.u8()
    contact = bool(flags & 0x02) if flags & 0x04 else None
    energy = r.u16() if flags & 0x08 else None
    rr: list[float] = []
    if flags & 0x10:
        while r.remaining >= 2:
            rr.append(r.u16() / 1024)
    return HeartRateMeasurement(bpm, contact, energy, tuple(rr))


HeartRateCallback = Callable[[float, HeartRateMeasurement, bytes], None]


class HeartRateClient:
    """Subscribes to heart rate measurements of a connected BLE sensor.

    Callbacks receive ``(time.monotonic() at receipt, measurement, raw bytes)``.
    """

    def __init__(self, ble: BleakClient, on_measurement: HeartRateCallback) -> None:
        self._ble = ble
        self._on_measurement = on_measurement

    async def start(self) -> None:
        await self._ble.start_notify(uuids.HEART_RATE_MEASUREMENT, self._handle)

    def _handle(self, _sender: object, data: bytearray) -> None:
        self._on_measurement(time.monotonic(), parse_heart_rate_measurement(data), bytes(data))
