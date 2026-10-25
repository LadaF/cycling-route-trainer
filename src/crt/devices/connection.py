"""Long-running BLE connections that reconnect by themselves: trainer and HR sensor."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from bleak import BleakClient
from bleak.exc import BleakError

from crt.control.policy import Command, GradeCommand, LevelCommand
from crt.devices.base import ConnectionCallback, HeartRateSample, SampleCallback, TrainerSample
from crt.devices.ble import find_device
from crt.devices.ftms.client import FtmsClient, FtmsCommandError
from crt.devices.ftms.codec import IndoorBikeData, SimulationParameters
from crt.devices.hrs import HeartRateClient, HeartRateMeasurement

log = logging.getLogger(__name__)

Clock = Callable[[], float]
CONNECTION_ERRORS = (BleakError, TimeoutError, FtmsCommandError, OSError)


async def keep_connected(
    target: str,
    label: str,
    setup: Callable[[BleakClient], Awaitable[None]],
    on_connection: ConnectionCallback,
    *,
    retry_delay: float = 2.0,
    max_retry_delay: float = 15.0,
) -> None:
    """Connect to ``target``, run ``setup`` and wait for a disconnect; then start again.

    Runs until cancelled. ``on_connection(True/False)`` reports the state.
    """
    delay = retry_delay
    while True:
        disconnected = asyncio.Event()
        try:
            device = await find_device(target)
            # Bind this attempt's event: a late callback from an old client must not
            # mark the new connection as lost.
            client = BleakClient(device, disconnected_callback=lambda _c, ev=disconnected: ev.set())
            async with client:
                await setup(client)
                log.info("%s connected (%s)", label, device.address)
                on_connection(True)
                delay = retry_delay
                await disconnected.wait()
        except CONNECTION_ERRORS as exc:
            log.warning("%s: %s", label, exc)
        on_connection(False)
        log.info("%s: reconnecting in %.0f s", label, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_retry_delay)


class BleTrainer:
    """FTMS trainer: merges data packets into samples and sends commands."""

    def __init__(
        self,
        target: str,
        clock: Clock,
        on_sample: SampleCallback,
        on_connection: ConnectionCallback,
    ) -> None:
        self._target = target
        self._clock = clock
        self._on_sample = on_sample
        self._on_connection = on_connection
        self._ftms: FtmsClient | None = None
        self._fields: dict[str, float] = {}
        self.supports_simulation = True  # until the device says otherwise
        self.connected = False

    def _bike_data(self, _t: float, data: IndoorBikeData, _raw: bytes) -> None:
        self._fields.update(data.present_fields())
        if data.more_data:
            return  # wait for the last packet of a split notification
        power = self._fields.get("instantaneous_power_w")
        if power is None:
            return
        self._on_sample(
            TrainerSample(
                self._clock(),
                float(power),
                self._fields.get("instantaneous_cadence_rpm"),
                self._fields.get("instantaneous_speed_kmh"),
            )
        )

    async def _setup(self, client: BleakClient) -> None:
        ftms = FtmsClient(client, on_bike_data=self._bike_data)
        await ftms.start(control=True)
        self.supports_simulation = bool(ftms.features and ftms.features.supports_simulation)
        await ftms.request_control()
        await ftms.start_or_resume()
        self._ftms = ftms

    def _connection(self, up: bool) -> None:
        self.connected = up
        if not up:
            self._ftms = None
        self._on_connection(up)

    async def run(self) -> None:
        await keep_connected(self._target, "trainer", self._setup, self._connection)

    async def send(self, command: Command) -> None:
        ftms = self._ftms
        if ftms is None:
            return
        try:
            match command:
                case GradeCommand(params):
                    await ftms.set_simulation(params)
                case LevelCommand(level):
                    await ftms.set_target_resistance_level(level)
        except CONNECTION_ERRORS as exc:
            log.warning("trainer command failed: %s", exc)

    async def finish(self) -> None:
        if self._ftms is not None:
            await self.send(GradeCommand(SimulationParameters(0.0)))


class BleHeartRate:
    def __init__(self, target: str, clock: Clock, on_sample, on_connection) -> None:
        self._target = target
        self._clock = clock
        self._on_sample = on_sample
        self._on_connection = on_connection
        self.connected = False

    def _measurement(self, _t: float, m: HeartRateMeasurement, _raw: bytes) -> None:
        self._on_sample(HeartRateSample(self._clock(), m.bpm))

    async def _setup(self, client: BleakClient) -> None:
        await HeartRateClient(client, self._measurement).start()

    def _connection(self, up: bool) -> None:
        self.connected = up
        self._on_connection(up)

    async def run(self) -> None:
        await keep_connected(self._target, "heart rate", self._setup, self._connection)
