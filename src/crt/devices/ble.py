"""BLE helpers shared by the tools and the application: scanning, device lookup,
device information and GATT dumps."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from crt.devices import uuids

# MAC address (Linux, Windows) or CoreBluetooth UUID (macOS)
_ADDRESS_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f-]{36}$")


@dataclass(frozen=True, slots=True)
class ScanResult:
    address: str
    name: str | None
    rssi: int
    services: tuple[str, ...]  # short names of known services, e.g. ("FTMS",)


def name_matches(needle: str, name: str | None) -> bool:
    """Case- and accent-insensitive substring match, so "fenix" finds "fēnix 7 Pro"."""

    def fold(text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text)
        return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()

    return fold(needle) in fold(name or "")


async def scan(
    timeout: float = 8.0, *, include_all: bool = False, name: str | None = None
) -> list[ScanResult]:
    found = await BleakScanner.discover(timeout, return_adv=True)
    results = []
    for device, adv in found.values():
        known = tuple(
            sorted(
                {uuids.KNOWN_SERVICES[u] for u in adv.service_uuids if u in uuids.KNOWN_SERVICES}
            )
        )
        device_name = adv.local_name or device.name
        if name is not None and not name_matches(name, device_name):
            continue
        if known or include_all or name is not None:
            results.append(ScanResult(device.address, device_name, adv.rssi, known))
    return sorted(results, key=lambda r: -r.rssi)


async def find_device(address_or_name: str, timeout: float = 10.0) -> BLEDevice:
    """Find a device by address, or by a part of its name (case and accents ignored).

    Connecting through a freshly discovered BLEDevice is more reliable than through
    a bare address string, especially with BlueZ.
    """
    if _ADDRESS_RE.match(address_or_name):
        device = await BleakScanner.find_device_by_address(address_or_name, timeout)
    else:
        device = await BleakScanner.find_device_by_filter(
            lambda d, adv: name_matches(address_or_name, adv.local_name or d.name), timeout
        )
    if device is None:
        raise BleakError(f"device {address_or_name!r} not found (is it awake and not in use?)")
    return device


async def read_device_info(client: BleakClient) -> dict[str, str | int]:
    """Device Information and Battery Service values that the device exposes."""
    info: dict[str, str | int] = {}
    for uuid, name in uuids.DEVICE_INFORMATION.items():
        if client.services.get_characteristic(uuid) is not None:
            raw = await client.read_gatt_char(uuid)
            info[name] = raw.decode("utf-8", errors="replace").rstrip("\x00")
    if client.services.get_characteristic(uuids.BATTERY_LEVEL) is not None:
        info["battery_percent"] = (await client.read_gatt_char(uuids.BATTERY_LEVEL))[0]
    return info


@dataclass(slots=True)
class CharacteristicDump:
    uuid: str
    handle: int
    description: str
    properties: list[str]
    value_hex: str | None = None
    read_error: str | None = None


@dataclass(slots=True)
class ServiceDump:
    uuid: str
    description: str
    characteristics: list[CharacteristicDump] = field(default_factory=list)


async def dump_gatt(client: BleakClient) -> list[ServiceDump]:
    """All services and characteristics, with the current value of readable ones."""
    services = []
    for service in client.services:
        dump = ServiceDump(service.uuid, service.description)
        for char in service.characteristics:
            entry = CharacteristicDump(
                char.uuid, char.handle, char.description, list(char.properties)
            )
            if "read" in char.properties:
                try:
                    entry.value_hex = (await client.read_gatt_char(char)).hex()
                except (BleakError, OSError) as exc:
                    entry.read_error = str(exc)
            dump.characteristics.append(entry)
        services.append(dump)
    return services
