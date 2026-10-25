"""Encoding and decoding of Fitness Machine Service (FTMS) characteristics.

Pure functions on bytes, no BLE I/O. Field layouts follow the Bluetooth SIG
Fitness Machine Service 1.0 specification and the GATT Specification Supplement.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

from crt.devices.codec import CodecError, Reader

# ---------------------------------------------------------------------------
# Indoor Bike Data (0x2AD2)
# ---------------------------------------------------------------------------


class IndoorBikeDataFlags(IntFlag):
    MORE_DATA = 1 << 0  # inverted: instantaneous speed is present when this bit is 0
    AVERAGE_SPEED = 1 << 1
    INSTANTANEOUS_CADENCE = 1 << 2
    AVERAGE_CADENCE = 1 << 3
    TOTAL_DISTANCE = 1 << 4
    RESISTANCE_LEVEL = 1 << 5
    INSTANTANEOUS_POWER = 1 << 6
    AVERAGE_POWER = 1 << 7
    EXPENDED_ENERGY = 1 << 8
    HEART_RATE = 1 << 9
    METABOLIC_EQUIVALENT = 1 << 10
    ELAPSED_TIME = 1 << 11
    REMAINING_TIME = 1 << 12


@dataclass(frozen=True, slots=True)
class IndoorBikeData:
    """One Indoor Bike Data notification. Fields absent from the packet are None.

    A trainer may split its data over several notifications (``more_data`` is True
    on all but the last), so consumers should merge packets field by field.
    """

    more_data: bool
    instantaneous_speed_kmh: float | None = None
    average_speed_kmh: float | None = None
    instantaneous_cadence_rpm: float | None = None
    average_cadence_rpm: float | None = None
    total_distance_m: int | None = None
    resistance_level: int | None = None
    instantaneous_power_w: int | None = None
    average_power_w: int | None = None
    total_energy_kcal: int | None = None
    energy_per_hour_kcal: int | None = None
    energy_per_minute_kcal: int | None = None
    heart_rate_bpm: int | None = None
    metabolic_equivalent: float | None = None
    elapsed_time_s: int | None = None
    remaining_time_s: int | None = None

    def present_fields(self) -> dict[str, float | int]:
        """The measured fields carried by this packet."""
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name != "more_data" and getattr(self, name) is not None
        }


def parse_indoor_bike_data(data: bytes | bytearray) -> IndoorBikeData:
    r = Reader(data)
    flags = IndoorBikeDataFlags(r.u16())
    F = IndoorBikeDataFlags
    fields: dict[str, float | int] = {}
    if not flags & F.MORE_DATA:
        fields["instantaneous_speed_kmh"] = r.u16() * 0.01
    if flags & F.AVERAGE_SPEED:
        fields["average_speed_kmh"] = r.u16() * 0.01
    if flags & F.INSTANTANEOUS_CADENCE:
        fields["instantaneous_cadence_rpm"] = r.u16() * 0.5
    if flags & F.AVERAGE_CADENCE:
        fields["average_cadence_rpm"] = r.u16() * 0.5
    if flags & F.TOTAL_DISTANCE:
        fields["total_distance_m"] = r.u24()
    if flags & F.RESISTANCE_LEVEL:
        fields["resistance_level"] = r.s16()
    if flags & F.INSTANTANEOUS_POWER:
        fields["instantaneous_power_w"] = r.s16()
    if flags & F.AVERAGE_POWER:
        fields["average_power_w"] = r.s16()
    if flags & F.EXPENDED_ENERGY:
        fields["total_energy_kcal"] = r.u16()
        fields["energy_per_hour_kcal"] = r.u16()
        fields["energy_per_minute_kcal"] = r.u8()
    if flags & F.HEART_RATE:
        fields["heart_rate_bpm"] = r.u8()
    if flags & F.METABOLIC_EQUIVALENT:
        fields["metabolic_equivalent"] = r.u8() * 0.1
    if flags & F.ELAPSED_TIME:
        fields["elapsed_time_s"] = r.u16()
    if flags & F.REMAINING_TIME:
        fields["remaining_time_s"] = r.u16()
    return IndoorBikeData(more_data=bool(flags & F.MORE_DATA), **fields)


# ---------------------------------------------------------------------------
# Fitness Machine Feature (0x2ACC)
# ---------------------------------------------------------------------------


class MachineFeature(IntFlag):
    AVERAGE_SPEED = 1 << 0
    CADENCE = 1 << 1
    TOTAL_DISTANCE = 1 << 2
    INCLINATION = 1 << 3
    ELEVATION_GAIN = 1 << 4
    PACE = 1 << 5
    STEP_COUNT = 1 << 6
    RESISTANCE_LEVEL = 1 << 7
    STRIDE_COUNT = 1 << 8
    EXPENDED_ENERGY = 1 << 9
    HEART_RATE_MEASUREMENT = 1 << 10
    METABOLIC_EQUIVALENT = 1 << 11
    ELAPSED_TIME = 1 << 12
    REMAINING_TIME = 1 << 13
    POWER_MEASUREMENT = 1 << 14
    FORCE_ON_BELT_AND_POWER_OUTPUT = 1 << 15
    USER_DATA_RETENTION = 1 << 16


class TargetSettingFeature(IntFlag):
    SPEED = 1 << 0
    INCLINATION = 1 << 1
    RESISTANCE = 1 << 2
    POWER = 1 << 3
    HEART_RATE = 1 << 4
    EXPENDED_ENERGY = 1 << 5
    STEP_NUMBER = 1 << 6
    STRIDE_NUMBER = 1 << 7
    DISTANCE = 1 << 8
    TRAINING_TIME = 1 << 9
    TIME_IN_TWO_HR_ZONES = 1 << 10
    TIME_IN_THREE_HR_ZONES = 1 << 11
    TIME_IN_FIVE_HR_ZONES = 1 << 12
    INDOOR_BIKE_SIMULATION = 1 << 13
    WHEEL_CIRCUMFERENCE = 1 << 14
    SPIN_DOWN_CONTROL = 1 << 15
    TARGETED_CADENCE = 1 << 16


@dataclass(frozen=True, slots=True)
class FitnessMachineFeatures:
    machine: MachineFeature
    target: TargetSettingFeature

    @property
    def supports_simulation(self) -> bool:
        return bool(self.target & TargetSettingFeature.INDOOR_BIKE_SIMULATION)

    @property
    def supports_resistance_target(self) -> bool:
        return bool(self.target & TargetSettingFeature.RESISTANCE)

    @property
    def supports_power_target(self) -> bool:
        return bool(self.target & TargetSettingFeature.POWER)


def _flag_names(flags: IntFlag) -> list[str]:
    return [f.name for f in type(flags) if f in flags and f.name]


def describe_features(features: FitnessMachineFeatures) -> dict[str, list[str]]:
    return {"machine": _flag_names(features.machine), "target": _flag_names(features.target)}


def parse_features(data: bytes | bytearray) -> FitnessMachineFeatures:
    r = Reader(data)
    # Unknown (reserved or newer) bits are kept, so nothing reported by the device is lost.
    return FitnessMachineFeatures(MachineFeature(r.u32()), TargetSettingFeature(r.u32()))


# ---------------------------------------------------------------------------
# Supported ranges (0x2AD5, 0x2AD6, 0x2AD8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupportedRange:
    minimum: float
    maximum: float
    increment: float


def _parse_range(data: bytes | bytearray, resolution: float) -> SupportedRange:
    r = Reader(data)
    return SupportedRange(r.s16() * resolution, r.s16() * resolution, r.u16() * resolution)


def parse_inclination_range(data: bytes | bytearray) -> SupportedRange:
    """Percent grade."""
    return _parse_range(data, 0.1)


def parse_resistance_range(data: bytes | bytearray) -> SupportedRange:
    """Unitless resistance level."""
    return _parse_range(data, 0.1)


def parse_power_range(data: bytes | bytearray) -> SupportedRange:
    """Watts."""
    return _parse_range(data, 1.0)


# ---------------------------------------------------------------------------
# Fitness Machine Control Point (0x2AD9)
# ---------------------------------------------------------------------------


class OpCode(IntEnum):
    REQUEST_CONTROL = 0x00
    RESET = 0x01
    SET_TARGET_SPEED = 0x02
    SET_TARGET_INCLINATION = 0x03
    SET_TARGET_RESISTANCE_LEVEL = 0x04
    SET_TARGET_POWER = 0x05
    SET_TARGET_HEART_RATE = 0x06
    START_OR_RESUME = 0x07
    STOP_OR_PAUSE = 0x08
    SET_INDOOR_BIKE_SIMULATION = 0x11
    SET_WHEEL_CIRCUMFERENCE = 0x12
    SPIN_DOWN_CONTROL = 0x13
    SET_TARGETED_CADENCE = 0x14
    RESPONSE_CODE = 0x80


class ResultCode(IntEnum):
    SUCCESS = 0x01
    OP_CODE_NOT_SUPPORTED = 0x02
    INVALID_PARAMETER = 0x03
    OPERATION_FAILED = 0x04
    CONTROL_NOT_PERMITTED = 0x05


def _scaled(value: float, resolution: float, fmt: str, what: str) -> bytes:
    raw = round(value / resolution)
    try:
        return struct.pack(fmt, raw)
    except struct.error:
        raise CodecError(f"{what} {value} is out of range for the FTMS field") from None


def request_control() -> bytes:
    return bytes([OpCode.REQUEST_CONTROL])


def reset() -> bytes:
    return bytes([OpCode.RESET])


def start_or_resume() -> bytes:
    return bytes([OpCode.START_OR_RESUME])


def stop_or_pause(pause: bool = False) -> bytes:
    return bytes([OpCode.STOP_OR_PAUSE, 0x02 if pause else 0x01])


def set_target_power(watts: float) -> bytes:
    return bytes([OpCode.SET_TARGET_POWER]) + _scaled(watts, 1.0, "<h", "power")


def set_target_inclination(percent: float) -> bytes:
    return bytes([OpCode.SET_TARGET_INCLINATION]) + _scaled(percent, 0.1, "<h", "inclination")


def set_target_resistance_level(level: float, *, wide: bool = False) -> bytes:
    """FTMS 1.0 defines the parameter as uint8 with resolution 0.1. Later revisions of
    the spec use sint16; some trainers expect that form (``wide=True``)."""
    fmt = "<h" if wide else "<B"
    return bytes([OpCode.SET_TARGET_RESISTANCE_LEVEL]) + _scaled(level, 0.1, fmt, "resistance")


@dataclass(frozen=True, slots=True)
class SimulationParameters:
    grade_percent: float
    crr: float = 0.004
    cw_kg_per_m: float = 0.51  # 0.5 * rho * CdA
    wind_speed_mps: float = 0.0


def set_indoor_bike_simulation(params: SimulationParameters) -> bytes:
    return (
        bytes([OpCode.SET_INDOOR_BIKE_SIMULATION])
        + _scaled(params.wind_speed_mps, 0.001, "<h", "wind speed")
        + _scaled(params.grade_percent, 0.01, "<h", "grade")
        + _scaled(params.crr, 0.0001, "<B", "Crr")
        + _scaled(params.cw_kg_per_m, 0.01, "<B", "Cw")
    )


def _parse_simulation_parameters(r: Reader) -> SimulationParameters:
    wind = r.s16() * 0.001
    grade = r.s16() * 0.01
    crr = r.u8() * 0.0001
    cw = r.u8() * 0.01
    return SimulationParameters(grade, crr, cw, wind)


@dataclass(frozen=True, slots=True)
class ControlPointResponse:
    request_opcode: int
    result: int
    parameters: bytes = b""

    @property
    def ok(self) -> bool:
        return self.result == ResultCode.SUCCESS

    def __str__(self) -> str:
        try:
            op = OpCode(self.request_opcode).name
        except ValueError:
            op = f"0x{self.request_opcode:02x}"
        try:
            res = ResultCode(self.result).name
        except ValueError:
            res = f"0x{self.result:02x}"
        extra = f" params={self.parameters.hex()}" if self.parameters else ""
        return f"{op}: {res}{extra}"


def parse_control_point_response(data: bytes | bytearray) -> ControlPointResponse:
    r = Reader(data)
    if (code := r.u8()) != OpCode.RESPONSE_CODE:
        raise CodecError(f"not a control point response (op code 0x{code:02x})")
    return ControlPointResponse(r.u8(), r.u8(), r.rest())


# ---------------------------------------------------------------------------
# Fitness Machine Status (0x2ADA)
# ---------------------------------------------------------------------------


class StatusCode(IntEnum):
    RESET = 0x01
    STOPPED_OR_PAUSED_BY_USER = 0x02
    STOPPED_BY_SAFETY_KEY = 0x03
    STARTED_OR_RESUMED_BY_USER = 0x04
    TARGET_SPEED_CHANGED = 0x05
    TARGET_INCLINE_CHANGED = 0x06
    TARGET_RESISTANCE_LEVEL_CHANGED = 0x07
    TARGET_POWER_CHANGED = 0x08
    TARGET_HEART_RATE_CHANGED = 0x09
    INDOOR_BIKE_SIMULATION_CHANGED = 0x12
    WHEEL_CIRCUMFERENCE_CHANGED = 0x13
    SPIN_DOWN_STATUS = 0x14
    TARGETED_CADENCE_CHANGED = 0x15
    CONTROL_PERMISSION_LOST = 0xFF


@dataclass(frozen=True, slots=True)
class MachineStatus:
    code: int
    value: object = None  # decoded parameter for the codes we understand
    raw_parameters: bytes = b""

    def __str__(self) -> str:
        try:
            name = StatusCode(self.code).name
        except ValueError:
            name = f"0x{self.code:02x}"
        if self.value is not None:
            return f"{name}: {self.value}"
        if self.raw_parameters:
            return f"{name}: {self.raw_parameters.hex()}"
        return name


def parse_machine_status(data: bytes | bytearray) -> MachineStatus:
    r = Reader(data)
    code = r.u8()
    params = bytes(data[1:])
    value: object = None
    try:
        match code:
            case StatusCode.STOPPED_OR_PAUSED_BY_USER:
                value = {1: "stop", 2: "pause"}.get(r.u8(), "unknown")
            case StatusCode.TARGET_INCLINE_CHANGED:
                value = r.s16() * 0.1
            case StatusCode.TARGET_RESISTANCE_LEVEL_CHANGED:
                # uint8 in FTMS 1.0, sint16 in later revisions
                value = (r.u8() if len(params) == 1 else r.s16()) * 0.1
            case StatusCode.TARGET_POWER_CHANGED:
                value = r.s16()
            case StatusCode.INDOOR_BIKE_SIMULATION_CHANGED:
                value = _parse_simulation_parameters(r)
    except CodecError:
        value = None  # malformed parameter; keep the raw bytes for diagnosis
    return MachineStatus(code, value, params)
