"""Values read from a real Saris H3 (model 321, firmware 31.059) with ``crt-tool probe``."""

import pytest

from crt.devices.ftms import codec
from crt.devices.ftms.codec import MachineFeature, TargetSettingFeature

FEATURES = bytes.fromhex("86400000 0ce00000")
RESISTANCE_RANGE = bytes.fromhex("0000 6400 0100")
POWER_RANGE = bytes.fromhex("0000 b80b 0100")


def test_features():
    f = codec.parse_features(FEATURES)
    M, T = MachineFeature, TargetSettingFeature
    assert f.machine == M.CADENCE | M.TOTAL_DISTANCE | M.RESISTANCE_LEVEL | M.POWER_MEASUREMENT
    assert f.target == (
        T.RESISTANCE
        | T.POWER
        | T.INDOOR_BIKE_SIMULATION
        | T.WHEEL_CIRCUMFERENCE
        | T.SPIN_DOWN_CONTROL
    )
    assert f.supports_simulation


def test_ranges():
    r = codec.parse_resistance_range(RESISTANCE_RANGE)
    assert (r.minimum, r.maximum, r.increment) == pytest.approx((0.0, 10.0, 0.1))
    p = codec.parse_power_range(POWER_RANGE)
    assert (p.minimum, p.maximum, p.increment) == (0, 3000, 1)


def test_resistance_range_fits_uint8_encoding():
    # The maximum level 10.0 is raw 100, which fits the FTMS 1.0 uint8 parameter.
    assert codec.set_target_resistance_level(10.0) == bytes.fromhex("04 64")
