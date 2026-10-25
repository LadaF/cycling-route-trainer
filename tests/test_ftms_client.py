import asyncio
import struct

import pytest

from crt.devices import uuids
from crt.devices.ftms.client import FtmsClient, FtmsCommandError
from crt.devices.ftms.codec import SimulationParameters


class FakeServices:
    def __init__(self, present: set[str]) -> None:
        self._present = present

    def get_characteristic(self, uuid: str):
        return uuid if uuid in self._present else None


class FakeBle:
    """Minimal stand-in for a connected BleakClient running an FTMS trainer."""

    def __init__(self, *, result: int = 0x01, respond: bool = True, early: bool = False) -> None:
        self.values = {
            uuids.FTMS_FEATURE: bytes.fromhex("82400000 0c200000"),
            uuids.SUPPORTED_RESISTANCE_LEVEL_RANGE: struct.pack("<hhH", 0, 1000, 10),
        }
        self.services = FakeServices(
            set(self.values) | {uuids.INDOOR_BIKE_DATA, uuids.FTMS_CONTROL_POINT}
        )
        self.callbacks = {}
        self.written: list[bytes] = []
        self.result = result
        self.respond = respond
        self.early = early  # indicate before the write call returns

    async def read_gatt_char(self, uuid):
        return bytearray(self.values[uuid])

    async def start_notify(self, uuid, callback):
        self.callbacks[uuid] = callback

    async def write_gatt_char(self, uuid, data, response):
        assert uuid == uuids.FTMS_CONTROL_POINT and response
        self.written.append(bytes(data))
        if not self.respond:
            return
        indication = bytearray([0x80, data[0], self.result])
        callback = self.callbacks[uuids.FTMS_CONTROL_POINT]
        if self.early:
            callback(None, indication)
        else:
            asyncio.get_running_loop().call_later(0.01, callback, None, indication)

    def notify(self, uuid, data: bytes) -> None:
        self.callbacks[uuid](None, bytearray(data))


async def test_capabilities_and_data():
    ble = FakeBle()
    received = []
    client = FtmsClient(ble, on_bike_data=lambda t, d, raw: received.append(d))
    await client.start()
    assert client.features.supports_simulation
    assert client.resistance_range.maximum == pytest.approx(100.0)
    assert client.inclination_range is None
    ble.notify(uuids.INDOOR_BIKE_DATA, bytes.fromhex("4400 c409 b400 c800"))
    assert received[0].instantaneous_power_w == 200


@pytest.mark.parametrize("early", [False, True])
async def test_command_success(early):
    ble = FakeBle(early=early)
    client = FtmsClient(ble)
    await client.start()
    response = await client.set_simulation(SimulationParameters(3.0))
    assert response.ok
    assert ble.written == [bytes.fromhex("11 0000 2c01 28 33")]


async def test_command_rejected():
    ble = FakeBle(result=0x05)
    client = FtmsClient(ble)
    await client.start()
    with pytest.raises(FtmsCommandError) as excinfo:
        await client.set_target_power(200)
    assert "CONTROL_NOT_PERMITTED" in str(excinfo.value)
    assert not (await client.command(bytes.fromhex("05c800"), check=False)).ok


async def test_command_timeout_and_recovery():
    ble = FakeBle(respond=False)
    client = FtmsClient(ble, response_timeout=0.05)
    await client.start()
    with pytest.raises(TimeoutError):
        await client.request_control()
    ble.respond = True
    assert (await client.request_control()).ok


async def test_unsolicited_response_is_ignored():
    ble = FakeBle()
    client = FtmsClient(ble)
    await client.start()
    ble.notify(uuids.FTMS_CONTROL_POINT, bytes.fromhex("800001"))  # no pending command
    assert (await client.request_control()).ok
