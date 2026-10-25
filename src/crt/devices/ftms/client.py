"""FTMS client on top of a connected bleak client.

Control point protocol: every command is written with response, and the trainer
answers with an indication on the same characteristic (0x80, request op code,
result code). Commands are serialised so each indication is matched to its request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from bleak import BleakClient

from crt.devices import uuids
from crt.devices.codec import CodecError
from crt.devices.ftms import codec
from crt.devices.ftms.codec import (
    ControlPointResponse,
    FitnessMachineFeatures,
    IndoorBikeData,
    MachineStatus,
    SimulationParameters,
    SupportedRange,
)

log = logging.getLogger(__name__)

BikeDataCallback = Callable[[float, IndoorBikeData, bytes], None]
StatusCallback = Callable[[float, MachineStatus, bytes], None]


class FtmsCommandError(RuntimeError):
    def __init__(self, response: ControlPointResponse) -> None:
        super().__init__(f"FTMS command rejected: {response}")
        self.response = response


class FtmsClient:
    """Fitness Machine Service client.

    Callbacks receive ``(time.monotonic() at receipt, decoded value, raw bytes)``.
    """

    def __init__(
        self,
        ble: BleakClient,
        *,
        on_bike_data: BikeDataCallback | None = None,
        on_status: StatusCallback | None = None,
        response_timeout: float = 3.0,
    ) -> None:
        self._ble = ble
        self._on_bike_data = on_bike_data
        self._on_status = on_status
        self._response_timeout = response_timeout
        self._command_lock = asyncio.Lock()
        self._pending: tuple[int, asyncio.Future[ControlPointResponse]] | None = None

        self.features: FitnessMachineFeatures | None = None
        self.inclination_range: SupportedRange | None = None
        self.resistance_range: SupportedRange | None = None
        self.power_range: SupportedRange | None = None

    def _has(self, uuid: str) -> bool:
        return self._ble.services.get_characteristic(uuid) is not None

    async def read_capabilities(self) -> None:
        """Read the feature bits and supported ranges the trainer exposes."""
        self.features = codec.parse_features(await self._ble.read_gatt_char(uuids.FTMS_FEATURE))
        for attr, uuid, parse in (
            ("inclination_range", uuids.SUPPORTED_INCLINATION_RANGE, codec.parse_inclination_range),
            (
                "resistance_range",
                uuids.SUPPORTED_RESISTANCE_LEVEL_RANGE,
                codec.parse_resistance_range,
            ),
            ("power_range", uuids.SUPPORTED_POWER_RANGE, codec.parse_power_range),
        ):
            if self._has(uuid):
                setattr(self, attr, parse(await self._ble.read_gatt_char(uuid)))

    async def start(self, *, control: bool = True) -> None:
        """Read capabilities and subscribe to data. With ``control``, also subscribe
        to the control point so commands can be sent."""
        await self.read_capabilities()
        await self._ble.start_notify(uuids.INDOOR_BIKE_DATA, self._handle_bike_data)
        if self._has(uuids.FTMS_STATUS):
            await self._ble.start_notify(uuids.FTMS_STATUS, self._handle_status)
        if control:
            await self._ble.start_notify(uuids.FTMS_CONTROL_POINT, self._handle_control_point)

    # -- notifications ------------------------------------------------------

    def _handle_bike_data(self, _sender: object, data: bytearray) -> None:
        t = time.monotonic()
        try:
            value = codec.parse_indoor_bike_data(data)
        except CodecError:
            log.warning("undecodable indoor bike data: %s", data.hex())
            return
        if self._on_bike_data:
            self._on_bike_data(t, value, bytes(data))

    def _handle_status(self, _sender: object, data: bytearray) -> None:
        t = time.monotonic()
        try:
            value = codec.parse_machine_status(data)
        except CodecError:
            log.warning("undecodable machine status: %s", data.hex())
            return
        if self._on_status:
            self._on_status(t, value, bytes(data))

    def _handle_control_point(self, _sender: object, data: bytearray) -> None:
        try:
            response = codec.parse_control_point_response(data)
        except CodecError:
            log.warning("unexpected control point indication: %s", data.hex())
            return
        if self._pending and self._pending[0] == response.request_opcode:
            future = self._pending[1]
            if not future.done():
                future.set_result(response)
        else:
            log.warning("unsolicited control point response: %s", response)

    # -- commands -----------------------------------------------------------

    async def command(self, payload: bytes, *, check: bool = True) -> ControlPointResponse:
        """Send a raw control point command and wait for its response."""
        async with self._command_lock:
            future: asyncio.Future[ControlPointResponse] = (
                asyncio.get_running_loop().create_future()
            )
            self._pending = (payload[0], future)
            try:
                await self._ble.write_gatt_char(uuids.FTMS_CONTROL_POINT, payload, response=True)
                response = await asyncio.wait_for(future, self._response_timeout)
            finally:
                self._pending = None
        log.debug("control point %s -> %s", payload.hex(), response)
        if check and not response.ok:
            raise FtmsCommandError(response)
        return response

    async def request_control(self) -> ControlPointResponse:
        return await self.command(codec.request_control())

    async def reset(self) -> ControlPointResponse:
        return await self.command(codec.reset())

    async def start_or_resume(self) -> ControlPointResponse:
        return await self.command(codec.start_or_resume())

    async def stop(self, *, pause: bool = False) -> ControlPointResponse:
        return await self.command(codec.stop_or_pause(pause))

    async def set_target_power(self, watts: float) -> ControlPointResponse:
        return await self.command(codec.set_target_power(watts))

    async def set_target_resistance_level(
        self, level: float, *, wide: bool = False
    ) -> ControlPointResponse:
        return await self.command(codec.set_target_resistance_level(level, wide=wide))

    async def set_target_inclination(self, percent: float) -> ControlPointResponse:
        return await self.command(codec.set_target_inclination(percent))

    async def set_simulation(self, params: SimulationParameters) -> ControlPointResponse:
        return await self.command(codec.set_indoor_bike_simulation(params))
