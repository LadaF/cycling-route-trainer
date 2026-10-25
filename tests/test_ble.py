from crt.devices.ble import name_matches


def test_name_matches_ignores_case_and_accents():
    assert name_matches("fenix", "fēnix 7 Pro")
    assert name_matches("FĒNIX 7", "fenix 7 pro")
    assert name_matches("hammer", "Hammer 50414")
    assert not name_matches("fenix", "Forerunner 965")
    assert not name_matches("fenix", None)
