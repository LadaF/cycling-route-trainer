import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crt.devices.codec import CodecError
from crt.devices.ftms import codec
from crt.devices.ftms.codec import (
    MachineFeature,
    SimulationParameters,
    StatusCode,
    TargetSettingFeature,
)


def test_bike_data_speed_cadence_power():
    # flags: cadence (bit 2) + power (bit 6); bit 0 clear, so speed is present
    data = bytes.fromhex("4400 c409 b400 c800")
    d = codec.parse_indoor_bike_data(data)
    assert not d.more_data
    assert d.instantaneous_speed_kmh == pytest.approx(25.0)
    assert d.instantaneous_cadence_rpm == pytest.approx(90.0)
    assert d.instantaneous_power_w == 200
    assert d.heart_rate_bpm is None
    assert d.present_fields().keys() == {
        "instantaneous_speed_kmh",
        "instantaneous_cadence_rpm",
        "instantaneous_power_w",
    }


def test_bike_data_more_data_has_no_speed():
    d = codec.parse_indoor_bike_data(bytes.fromhex("4100 9600"))
    assert d.more_data
    assert d.instantaneous_speed_kmh is None
    assert d.instantaneous_power_w == 150


def test_bike_data_all_fields_in_spec_order():
    data = struct.pack(
        "<HHHHHHBhhhHHBBBHH",
        0x1FFE,  # every field flag, bit 0 clear
        3000, 2900,  # speeds, 0.01 km/h
        170, 160,  # cadences, 0.5 rpm
        0x2345, 0x01,  # total distance, uint24 = 0x012345
        -5,  # resistance level
        -10, 210,  # instantaneous (negative allowed) and average power
        123, 456, 7,  # energy total, per hour, per minute
        142,  # heart rate
        35,  # MET, 0.1
        3600, 60,  # elapsed, remaining time
    )  # fmt: skip
    d = codec.parse_indoor_bike_data(data)
    assert d.instantaneous_speed_kmh == pytest.approx(30.0)
    assert d.average_speed_kmh == pytest.approx(29.0)
    assert d.instantaneous_cadence_rpm == pytest.approx(85.0)
    assert d.average_cadence_rpm == pytest.approx(80.0)
    assert d.total_distance_m == 0x012345
    assert d.resistance_level == -5
    assert d.instantaneous_power_w == -10
    assert d.average_power_w == 210
    assert (d.total_energy_kcal, d.energy_per_hour_kcal, d.energy_per_minute_kcal) == (123, 456, 7)
    assert d.heart_rate_bpm == 142
    assert d.metabolic_equivalent == pytest.approx(3.5)
    assert (d.elapsed_time_s, d.remaining_time_s) == (3600, 60)


def test_bike_data_truncated():
    with pytest.raises(CodecError):
        codec.parse_indoor_bike_data(bytes.fromhex("4400 c409 b4"))


def test_features():
    f = codec.parse_features(bytes.fromhex("82400000 0c200000"))
    assert f.machine == MachineFeature.CADENCE | MachineFeature.RESISTANCE_LEVEL | (
        MachineFeature.POWER_MEASUREMENT
    )
    assert f.supports_simulation
    assert f.supports_resistance_target
    assert f.supports_power_target
    assert codec.describe_features(f)["target"] == ["RESISTANCE", "POWER", "INDOOR_BIKE_SIMULATION"]


def test_features_keep_unknown_bits():
    f = codec.parse_features(bytes.fromhex("00000080 00000000"))
    assert int(f.machine) == 0x80000000
    assert not f.supports_simulation
    assert f.target == TargetSettingFeature(0)


def test_ranges():
    r = codec.parse_inclination_range(struct.pack("<hhH", -100, 200, 1))
    assert (r.minimum, r.maximum, r.increment) == pytest.approx((-10.0, 20.0, 0.1))
    p = codec.parse_power_range(struct.pack("<hhH", 0, 2000, 1))
    assert (p.minimum, p.maximum, p.increment) == (0, 2000, 1)


def test_encode_commands():
    assert codec.request_control() == b"\x00"
    assert codec.reset() == b"\x01"
    assert codec.start_or_resume() == b"\x07"
    assert codec.stop_or_pause() == b"\x08\x01"
    assert codec.stop_or_pause(pause=True) == b"\x08\x02"
    assert codec.set_target_power(250) == bytes.fromhex("05fa00")
    assert codec.set_target_inclination(-1.5) == bytes.fromhex("03f1ff")
    assert codec.set_target_resistance_level(5) == bytes.fromhex("0432")
    assert codec.set_target_resistance_level(5, wide=True) == bytes.fromhex("043200")


def test_encode_simulation():
    assert codec.set_indoor_bike_simulation(SimulationParameters(5.0)) == bytes.fromhex(
        "11 0000 f401 28 33"
    )
    assert codec.set_indoor_bike_simulation(
        SimulationParameters(-2.5, crr=0.005, cw_kg_per_m=0.3, wind_speed_mps=-1.2)
    ) == bytes.fromhex("11 50fb 06ff 32 1e")


def test_encode_out_of_range():
    with pytest.raises(CodecError):
        codec.set_indoor_bike_simulation(SimulationParameters(5.0, crr=0.03))
    with pytest.raises(CodecError):
        codec.set_target_resistance_level(-1)


def test_control_point_response():
    r = codec.parse_control_point_response(bytes.fromhex("801101"))
    assert r.ok
    assert str(r) == "SET_INDOOR_BIKE_SIMULATION: SUCCESS"
    r = codec.parse_control_point_response(bytes.fromhex("800505"))
    assert not r.ok
    assert str(r) == "SET_TARGET_POWER: CONTROL_NOT_PERMITTED"
    with pytest.raises(CodecError):
        codec.parse_control_point_response(bytes.fromhex("1101"))


def test_machine_status():
    s = codec.parse_machine_status(bytes.fromhex("08c800"))
    assert s.code == StatusCode.TARGET_POWER_CHANGED
    assert s.value == 200
    assert str(codec.parse_machine_status(b"\xff")) == "CONTROL_PERMISSION_LOST"
    assert codec.parse_machine_status(bytes.fromhex("0202")).value == "pause"
    unknown = codec.parse_machine_status(bytes.fromhex("4001"))
    assert unknown.value is None
    assert str(unknown) == "0x40: 01"


@given(
    grade=st.floats(-40, 40),
    crr=st.floats(0, 0.0255),
    cw=st.floats(0, 2.55),
    wind=st.floats(-30, 30),
)
def test_simulation_round_trip(grade, crr, cw, wind):
    payload = codec.set_indoor_bike_simulation(SimulationParameters(grade, crr, cw, wind))
    status = codec.parse_machine_status(
        bytes([StatusCode.INDOOR_BIKE_SIMULATION_CHANGED]) + payload[1:]
    )
    v = status.value
    assert v.grade_percent == pytest.approx(grade, abs=0.005 + 1e-9)
    assert v.crr == pytest.approx(crr, abs=0.00005 + 1e-9)
    assert v.cw_kg_per_m == pytest.approx(cw, abs=0.005 + 1e-9)
    assert v.wind_speed_mps == pytest.approx(wind, abs=0.0005 + 1e-9)
