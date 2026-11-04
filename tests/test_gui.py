import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QFontMetricsF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from crt.config import Settings  # noqa: E402
from crt.control.policy import ControlSettings, GradeCommand  # noqa: E402
from crt.devices.ftms.codec import SimulationParameters  # noqa: E402
from crt.gui.profile import grade_colour  # noqa: E402
from crt.gui.settings_dialog import SettingsDialog  # noqa: E402
from crt.gui.widgets import VALUE_REFERENCE, MetricTile, Rolling  # noqa: E402
from crt.gui.window import RideWindow, apply_font_scale  # noqa: E402
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
    assert t["grade"]._detail.text() == "trainer +1.0 % (min)"
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


def test_tile_value_grows_with_the_tile_and_fits(qtbot):
    tile = MetricTile("Distance")
    qtbot.addWidget(tile)
    tile.show()  # a hidden widget gets its resize events only when shown
    sizes = []
    for width, height in [(200, 100), (600, 300)]:
        tile.resize(width, height)
        font = tile._value.font()
        sizes.append(font.pixelSize())
        assert QFontMetricsF(font).horizontalAdvance(VALUE_REFERENCE) < width
    assert sizes[1] > 2.5 * sizes[0]


def test_text_zoom(window, tmp_path):
    try:
        normal = QApplication.font().pointSizeF()
        window.zoom_text(+1)
        window.zoom_text(+1)
        assert QApplication.font().pointSizeF() == pytest.approx(normal * 1.15**2, rel=0.01)
        saved = Settings.load(tmp_path / "settings.toml").font_scale
        assert saved == pytest.approx(1.3225, abs=0.001)
        for _ in range(20):
            window.zoom_text(+1)
        assert window.settings.font_scale == 3.0  # the upper limit
        window.zoom_text(0)
        assert QApplication.font().pointSizeF() == pytest.approx(normal)
    finally:
        apply_font_scale(1.0)


def test_tile_detail_does_not_widen_the_tile(qtbot):
    tile = MetricTile("Grade")
    qtbot.addWidget(tile)
    tile.show()
    tile.set("+9.0 %", "trainer +9.0 %")
    width = tile.minimumSizeHint().width()
    tile.set("+9.0 %", "trainer +1.5 % (min, 50 %) and a lot more text")
    QApplication.processEvents()
    assert tile.minimumSizeHint().width() == width


def test_profile_unit_needs_no_label_row(window):
    from crt.gui.profile import UnitAxis

    for plot in (window.profile.zoom, window.profile.overview):
        axis = plot.getAxis("bottom")
        assert isinstance(axis, UnitAxis) and not axis.label.isVisible()


def test_cadence_display_ignores_single_dips(window):
    for cadence in (85.0, 84.0, 20.0, 86.0, 85.0):
        window.show_snapshot(snapshot(cadence=cadence))
    assert window.tiles["cadence"]._value.text() == "85 rpm"


def test_difficulty_buttons(window, tmp_path):
    assert not window.harder_action.isEnabled()
    window.easier_action.trigger()
    window.easier_action.trigger()
    assert window.settings.control.difficulty == 0.8
    assert Settings.load(tmp_path / "settings.toml").control.difficulty == 0.8
    window.show_snapshot(snapshot(command=GradeCommand(SimulationParameters(2.4)),
                                  terrain_grade=3.0))  # fmt: skip
    assert window.tiles["grade"]._detail.text() == "trainer +2.4 % (80 %)"
    for _ in range(10):
        window.change_difficulty(-1)
    assert window.settings.control.difficulty == 0.0 and not window.easier_action.isEnabled()


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


async def test_course_manager(qtbot, tmp_path, monkeypatch):
    from test_route import SlopeProvider, north_gpx

    from crt.elevation import bridges
    from crt.gui.course_manager import CourseManager
    from crt.route import pipeline

    monkeypatch.setattr(bridges, "cache_dir", lambda *parts: tmp_path)
    monkeypatch.setattr(pipeline, "providers_for", lambda source: [SlopeProvider()])
    settings = Settings(courses=[str(tmp_path / "gone.crt.npz")])
    saves = []
    manager = CourseManager(settings, lambda: saves.append(1))
    qtbot.addWidget(manager)
    assert "not there" in manager.details.text() and not manager.ride_button.isEnabled()

    await manager.import_route(north_gpx(tmp_path / "north.gpx"))
    course_file = tmp_path / "north.crt.npz"
    assert settings.courses[0] == str(course_file) and saves
    assert manager.selected == course_file and manager.ride_button.isEnabled()
    assert "not checked" in manager.details.text()
    assert manager._query_url().startswith("https://overpass-turbo.eu/?Q=")

    bridge = {
        "type": "way",
        "tags": {"highway": "primary", "bridge": "yes", "name": "Most"},
        "geometry": [{"lat": 50.003, "lon": 14.0}, {"lat": 50.005, "lon": 14.0}],
    }
    QApplication.clipboard().setText(json.dumps({"elements": [bridge]}))
    with qtbot.waitSignal(manager.changed) as changed:
        manager.paste_button.click()
        await manager._task
    assert changed.args == [course_file] and not manager.busy
    assert "bridge Most at" in manager.details.text()

    with qtbot.waitSignal(manager.ride) as ride:
        manager.ride_button.click()
    assert ride.args == [course_file]


def test_window_reloads_a_rebuilt_course(window, make_course):
    window.course.save(window.course_file)
    make_course(length=500.0).save(window.course_file)  # prepared again elsewhere
    window._course_file_changed(window.course_file.with_name("other.crt.npz"))
    assert window.course.length == pytest.approx(2000.0)
    window._course_file_changed(window.course_file)
    assert window.course.length == pytest.approx(500.0)


def test_course_manager_uses_the_unscaled_font(window):
    from crt.gui.course_manager import CourseManager

    normal = QApplication.font().pointSizeF()
    try:
        window.zoom_text(+1)
        window.manage_courses()
        (manager,) = window.findChildren(CourseManager)
        assert manager.details.font().pointSizeF() == pytest.approx(normal)
        manager.close()
    finally:
        apply_font_scale(1.0)
