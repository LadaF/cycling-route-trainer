import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from crt.config import Settings  # noqa: E402
from crt.control.policy import ControlSettings, GradeCommand  # noqa: E402
from crt.devices.ftms.codec import SimulationParameters  # noqa: E402
from crt.gui.profile import grade_colour  # noqa: E402
from crt.gui.settings_dialog import SettingsDialog  # noqa: E402
from crt.gui.widgets import Rolling  # noqa: E402
from crt.gui.window import RideWindow  # noqa: E402
from crt.session.session import RideSnapshot, RideState  # noqa: E402


def snapshot(**kw) -> RideSnapshot:
    values = dict(
        state=RideState.RIDING, elapsed=95.0, moving_time=90.0, s=500.0, remaining=1500.0,
        v=5.0, power=200.0, cadence=85.0, trainer_speed_kmh=17.5, heart_rate=140,
        elevation=205.0, terrain_grade=-2.0, command=None, ascent=5.0, work=19_000.0,
        braking=0.0, trainer_connected=True, hr_connected=True,
    )  # fmt: skip
    return RideSnapshot(**{**values, **kw})


def test_rolling_window():
    r = Rolling(3.0)
    assert r.mean() is None
    for t, v in [(0, 100), (1, 0), (2, 200), (3, 200), (4.5, 50)]:
        r.add(t, v)
    r.add(4.6, None)  # ignored, but still ages the window
    assert r.median() == 200 and r.mean() == pytest.approx(450 / 3)


def test_grade_colours():
    assert grade_colour(-5) != grade_colour(1) != grade_colour(12)
    assert grade_colour(50) == grade_colour(12)


@pytest.fixture
def window(qtbot, make_course, tmp_path):
    settings = Settings(control=ControlSettings(min_grade=1.0))
    win = RideWindow(settings, settings_file=tmp_path / "settings.toml", web_map=False)
    qtbot.addWidget(win)
    win.set_course(make_course(), tmp_path / "course.crt.npz")
    return win


def test_window_shows_snapshot(window, tmp_path):
    assert window.start_action.isEnabled() and not window.stop_action.isEnabled()
    assert Settings.load(tmp_path / "settings.toml").course.endswith("course.crt.npz")
    command = GradeCommand(SimulationParameters(1.0))
    window.show_snapshot(snapshot(command=command))
    t = window.tiles
    assert t["power"]._value.text() == "200 W"
    assert t["speed"]._value.text() == "18.0 km/h"
    assert t["speed"]._detail.text() == "trainer 17.5 km/h"
    assert t["distance"]._detail.text() == "1.50 km to go"
    assert t["time"]._value.text() == "0:01:35"
    # Terrain at -2 % with a 1 % minimum: the clamp is shown.
    assert t["grade"]._detail.text() == "trainer +1.0 % (minimum)"
    window.show_snapshot(snapshot(command=GradeCommand(SimulationParameters(3.0)),
                                  terrain_grade=3.0))  # fmt: skip
    assert t["grade"]._detail.text() == "trainer +3.0 %"


def test_window_opens_another_course(window, make_course, tmp_path):
    window.set_course(make_course(), tmp_path / "other.crt.npz")
    assert window._main.widget(1) is window.profile
    assert window._top.widget(1) is window.map


def test_window_reverses_course(window, tmp_path):
    window.course.save(window.course_file)
    first = window.course
    window.reverse_action.trigger()
    assert window.course_file == tmp_path / "course.reverse.crt.npz"
    assert window.course_file.exists() and window.course.name.endswith("(reversed)")
    assert window.course.z[0] == first.z[-1]
    assert Settings.load(tmp_path / "settings.toml").course.endswith("course.reverse.crt.npz")
    window.reverse_action.trigger()
    assert window.course_file == tmp_path / "course.crt.npz" and window.course.name == first.name


def test_cadence_display_ignores_single_dips(window):
    for cadence in (85.0, 84.0, 20.0, 86.0, 85.0):
        window.show_snapshot(snapshot(cadence=cadence))
    assert window.tiles["cadence"]._value.text() == "85 rpm"


def test_settings_dialog(qtbot):
    dialog = SettingsDialog(Settings())
    qtbot.addWidget(dialog)
    dialog.trainer.setText(" Hammer 50414 ")
    dialog.max_speed.setValue(50)
    dialog.difficulty.setValue(80)
    result = dialog.result_settings()
    assert result.trainer == "Hammer 50414"
    assert result.rider.max_speed == pytest.approx(50 / 3.6)
    assert result.control.difficulty == pytest.approx(0.8)
