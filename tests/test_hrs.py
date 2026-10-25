import pytest

from crt.devices.codec import CodecError
from crt.devices.hrs import parse_heart_rate_measurement


def test_uint8_with_contact_and_rr():
    # contact supported + detected, RR intervals present
    m = parse_heart_rate_measurement(bytes.fromhex("16 48 0004 0003"))
    assert m.bpm == 72
    assert m.contact is True
    assert m.energy_expended_kj is None
    assert m.rr_intervals_s == pytest.approx((1.0, 0.75))


def test_uint16_with_energy_no_contact_support():
    m = parse_heart_rate_measurement(bytes.fromhex("09 2c01 e803"))
    assert m.bpm == 300
    assert m.contact is None
    assert m.energy_expended_kj == 1000


def test_contact_supported_but_lost():
    assert parse_heart_rate_measurement(bytes.fromhex("0450")).contact is False


def test_truncated():
    with pytest.raises(CodecError):
        parse_heart_rate_measurement(bytes.fromhex("01"))
