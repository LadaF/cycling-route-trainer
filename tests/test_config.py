from crt.config import Settings
from crt.control.policy import ControlSettings
from crt.physics.model import RiderParams


def test_settings_round_trip(tmp_path):
    path = tmp_path / "sub" / "settings.toml"
    settings = Settings(
        trainer="Hammer 50414", heart_rate="fenix", course="/x/ride.crt.npz",
        rider=RiderParams(rider_mass=80.0), control=ControlSettings(min_grade=1.0),
    )  # fmt: skip
    settings.save(path)
    assert Settings.load(path) == settings


def test_missing_unknown_and_broken_settings(tmp_path):
    assert Settings.load(tmp_path / "none.toml") == Settings()
    path = tmp_path / "settings.toml"
    path.write_text('trainer = "H3"\nfuture = 1\n[rider]\nrider_mass = 70.0\nshoe = 2\n')
    loaded = Settings.load(path)
    assert loaded.trainer == "H3" and loaded.rider.rider_mass == 70.0
    path.write_text("not = [toml")
    assert Settings.load(path) == Settings()
