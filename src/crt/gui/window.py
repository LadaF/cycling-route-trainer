"""The ride window: course, live metrics, elevation profile and map.

The window never talks to devices directly. A ride is a ``RideRunner`` task on the
qasync event loop; the window shows its snapshots and calls pause/resume/stop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QWidget,
)

from crt.config import Settings
from crt.control.policy import GradeCommand, GradePolicy
from crt.gui.profile import ProfileView
from crt.gui.settings_dialog import SettingsDialog
from crt.gui.widgets import MetricTile, Rolling
from crt.route.course import Course, reversed_path
from crt.session.log import RideLog, default_log_path, ride_header
from crt.session.runner import Clock, RideRunner
from crt.session.session import RideSession, RideSnapshot, RideState, describe_command
from crt.session.summary import hms, summary_lines

log = logging.getLogger(__name__)

ROUTE_FILES = "Courses and routes (*.crt.npz *.gpx *.tcx *.fit *.geojson);;All files (*)"
MAP_UPDATE_S = 0.5


class RideWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        *,
        settings_file: Path | None = None,
        web_map: bool = True,
        mock_power: float | None = None,
        speedup: float = 1.0,
    ) -> None:
        super().__init__()
        self.settings = settings
        self._settings_file = settings_file
        self._web_map = web_map
        self._mock_power = mock_power
        self._speedup = speedup
        self.course: Course | None = None
        self.course_file: Path | None = None
        self.runner: RideRunner | None = None
        self._ride_task: asyncio.Task | None = None
        self._quit_after_ride = False
        self._power = Rolling(3.0)
        self._cadence = Rolling(5.0)  # the H3 cadence has single-sample dips
        self._map_shown_at = 0.0
        self.profile: ProfileView | None = None
        self.map = None

        self.setWindowTitle("Cycling Route Trainer")
        self._build_actions()
        self._build_body()
        self._trainer_status = QLabel()
        self._hr_status = QLabel()
        self.statusBar().addPermanentWidget(self._trainer_status)
        self.statusBar().addPermanentWidget(self._hr_status)
        self._update_controls(None)

    # -- layout ---------------------------------------------------------------

    def _build_actions(self) -> None:
        bar = self.addToolBar("Ride")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        def action(text: str, slot, shortcut: str | None = None) -> QAction:
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            bar.addAction(a)
            return a

        self.open_action = action("Open course…", self.choose_course, "Ctrl+O")
        self.reverse_action = action("Reverse", self.reverse_course, "Ctrl+R")
        self.settings_action = action("Settings…", self.edit_settings, "Ctrl+,")
        bar.addSeparator()
        self.start_action = action("Start", self.start_ride, "Ctrl+Return")
        self.pause_action = action("Pause", self.toggle_pause, "Space")
        self.stop_action = action("Stop", self.stop_ride, "Ctrl+.")
        bar.addSeparator()
        self.follow_action = action("Follow rider", self._follow_changed)
        self.follow_action.setCheckable(True)

    def _build_body(self) -> None:
        self.tiles = {
            key: MetricTile(caption)
            for key, caption in [
                ("power", "Power (3 s)"), ("cadence", "Cadence"), ("hr", "Heart rate"),
                ("speed", "Speed"), ("grade", "Grade"), ("distance", "Distance"),
                ("time", "Time"), ("elevation", "Elevation"),
            ]
        }  # fmt: skip
        metrics = QWidget()
        grid = QGridLayout(metrics)
        for i, tile in enumerate(self.tiles.values()):
            grid.addWidget(tile, i // 2, i % 2)

        self._top = QSplitter(Qt.Orientation.Horizontal)
        self._top.addWidget(metrics)
        self._top.addWidget(self._placeholder("The map appears here."))
        self._main = QSplitter(Qt.Orientation.Vertical)
        self._main.addWidget(self._top)
        self._main.addWidget(self._placeholder("Open a course to start (Ctrl+O)."))
        self._main.setSizes([500, 400])
        self.setCentralWidget(self._main)

    @staticmethod
    def _placeholder(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: palette(placeholder-text)")
        return label

    def _replace(self, splitter: QSplitter, index: int, widget: QWidget) -> None:
        # Call before dropping the attribute that holds the old widget: PySide leaves a
        # widget put in by replaceWidget owned by Python, so the old one would already
        # be deleted and gone from the splitter.
        old = splitter.widget(index)
        splitter.replaceWidget(index, widget)
        old.deleteLater()

    # -- course ---------------------------------------------------------------

    def choose_course(self) -> None:
        start = str(Path(self.settings.course).parent) if self.settings.course else ""
        name, _ = QFileDialog.getOpenFileName(self, "Open course", start, ROUTE_FILES)
        if name:
            asyncio.ensure_future(self.open_course(Path(name)))

    async def open_course(self, path: Path) -> None:
        """Open a course, or prepare a route and keep the course next to it."""
        from crt.route.pipeline import prepared_course

        self.statusBar().showMessage(f"opening {path.name} …")
        self.open_action.setEnabled(False)
        try:
            course, course_file = await asyncio.to_thread(prepared_course, path)
        except Exception as exc:
            log.exception("cannot open %s", path)
            QMessageBox.warning(self, "Open course", f"Cannot open {path.name}:\n{exc}")
            return
        finally:
            self.open_action.setEnabled(True)
            self.statusBar().clearMessage()
        self.set_course(course, course_file)

    def set_course(self, course: Course, path: Path) -> None:
        self.course, self.course_file = course, path
        self.setWindowTitle(f"{course.name} — Cycling Route Trainer")
        profile = ProfileView(course)
        self._replace(self._main, 1, profile)
        self.profile = profile
        self._set_map(self._make_map(course))
        self.settings.course = str(path)
        self._save_settings()
        self._update_controls(None)

    def reverse_course(self) -> None:
        """Switch to the course ridden the other way, kept next to the current one."""
        path = reversed_path(self.course_file)
        course = self.course.reversed()
        try:
            course.save(path)
        except OSError as exc:
            QMessageBox.warning(self, "Reverse", f"Cannot save {path.name}:\n{exc}")
            return
        self.set_course(course, path)

    def _make_map(self, course: Course):
        from crt.gui.map_view import MapView, TrackView

        if not self._web_map:
            return TrackView(course)
        view = MapView(course)
        view.failed.connect(lambda: self._set_map(TrackView(course)))
        return view

    def _set_map(self, view) -> None:
        self._replace(self._top, 1, view)
        self.map = view
        self._top.setSizes([450, 550])

    # -- settings -------------------------------------------------------------

    def edit_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.result_settings()
            self._save_settings()
            self._update_controls(None)

    def _save_settings(self) -> None:
        try:
            self.settings.save(self._settings_file)
        except OSError as exc:
            log.warning("cannot save settings: %s", exc)

    # -- ride -----------------------------------------------------------------

    @property
    def riding(self) -> bool:
        return self._ride_task is not None and not self._ride_task.done()

    def start_ride(self) -> None:
        if self.course is None or self.riding:
            return
        if self._mock_power is None and not self.settings.trainer:
            QMessageBox.information(self, "Start", "Set the trainer in the settings first.")
            self.edit_settings()
            if not self.settings.trainer:
                return
        self._ride_task = asyncio.ensure_future(self._ride())

    def toggle_pause(self) -> None:
        if self.runner is None:
            return
        if self.runner.session.state is RideState.PAUSED:
            self.runner.resume()
        else:
            self.runner.pause()
        self._update_controls(self.runner.session.snapshot())

    def stop_ride(self) -> None:
        if self.runner is not None:
            self.runner.stop()

    async def _ride(self) -> None:
        from crt.devices.connection import BleHeartRate, BleTrainer
        from crt.devices.mock import MockTrainer, constant_power

        assert self.course is not None and self.course_file is not None
        s = self.settings
        simulated = self._mock_power is not None
        clock = Clock(self._speedup if simulated else 1.0)
        log_path = default_log_path()
        header = ride_header(
            self.course_file, self.course.name, "simulated" if simulated else s.trainer, 0.0,
            s.rider, s.control,
        )  # fmt: skip
        ride_log = RideLog(log_path, header)
        session = RideSession(self.course, s.rider, GradePolicy(self.course, s.control, s.rider))
        runner = self.runner = RideRunner(session, clock, ride_log)
        if simulated:
            trainer = MockTrainer(
                constant_power(self._mock_power), clock.now, clock.sleep,
                runner.trainer_sample, runner.trainer_connection,
            )  # fmt: skip
        else:
            trainer = BleTrainer(s.trainer, clock.now, runner.trainer_sample,
                                 runner.trainer_connection)  # fmt: skip
        hr = (
            BleHeartRate(s.heart_rate, clock.now, runner.hr_sample, runner.hr_connection)
            if s.heart_rate and not simulated
            else None
        )
        self._power.clear()
        self._cadence.clear()
        try:
            snap = await runner.run(trainer, hr, on_update=self.show_snapshot, update_hz=10.0)
        except Exception as exc:
            log.exception("ride failed")
            QMessageBox.critical(self, "Ride", f"The ride stopped with an error:\n{exc}")
            snap = None
        finally:
            ride_log.close()
            self.runner = None
        if snap is not None:
            self.show_snapshot(snap)
        self._update_controls(snap)
        if snap is not None and snap.state is not RideState.READY:
            self._finish(snap, log_path)
        if self._quit_after_ride:
            self.close()

    def _finish(self, snap: RideSnapshot, log_path: Path) -> None:
        from crt.record.export import export_ride

        assert self.course is not None
        lines = summary_lines(snap, self.course.length)
        try:
            (fit,) = export_ride(log_path, ["fit"], self.course)
            lines += ["", f"FIT file: {fit}"]
        except ValueError:
            pass  # nothing ridden
        lines += [f"ride log: {log_path}"]
        if self._quit_after_ride:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Ride finished" if snap.state is RideState.FINISHED else "Ride stopped")
        box.setText(f"{self.course.name}\n\n" + "\n".join(lines))
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.open()  # not exec(): the event loop keeps running

    # -- display --------------------------------------------------------------

    def show_snapshot(self, snap: RideSnapshot) -> None:
        now = time.monotonic()
        self._power.add(now, snap.power)
        self._cadence.add(now, snap.cadence)
        t = self.tiles
        power = self._power.mean()
        t["power"].set(f"{power:.0f} W" if power is not None else "–", f"now {snap.power:.0f} W")
        cadence = self._cadence.median()
        t["cadence"].set(f"{cadence:.0f} rpm" if cadence is not None else "–")
        t["hr"].set(f"{snap.heart_rate} bpm" if snap.heart_rate is not None else "–")
        trainer_speed = (
            f"trainer {snap.trainer_speed_kmh:.1f} km/h"
            if snap.trainer_speed_kmh is not None
            else ""
        )
        t["speed"].set(f"{snap.v * 3.6:.1f} km/h", trainer_speed)
        clamped = self._clamped(snap)
        t["grade"].set(
            f"{snap.terrain_grade:+.1f} %",
            f"trainer {describe_command(snap.command)}" + (" (minimum)" if clamped else ""),
            highlight=clamped,
        )
        t["distance"].set(f"{snap.s / 1000:.2f} km", f"{snap.remaining / 1000:.2f} km to go")
        t["time"].set(hms(snap.elapsed), f"moving {hms(snap.moving_time)}")
        t["elevation"].set(f"{snap.elevation:.0f} m", f"+{snap.ascent:.0f} m")

        if self.profile is not None:
            self.profile.set_position(snap.s)
        if (
            self.map is not None
            and self.course is not None
            and now - self._map_shown_at >= MAP_UPDATE_S
        ):
            self._map_shown_at = now
            point = self.course.at(snap.s)
            self.map.set_position(point.lat, point.lon)
        self._update_controls(snap)

    def _clamped(self, snap: RideSnapshot) -> bool:
        """The trainer is held at the minimum grade, above the (scaled) terrain grade."""
        c = self.settings.control
        return (
            isinstance(snap.command, GradeCommand)
            and snap.command.params.grade_percent <= c.min_grade + 0.05
            and snap.terrain_grade * c.difficulty < c.min_grade
        )

    def _update_controls(self, snap: RideSnapshot | None) -> None:
        riding = self.riding
        self.open_action.setEnabled(not riding)
        self.reverse_action.setEnabled(self.course is not None and not riding)
        self.settings_action.setEnabled(not riding)
        self.start_action.setEnabled(self.course is not None and not riding)
        self.stop_action.setEnabled(riding)
        state = snap.state if snap is not None and riding else None
        self.pause_action.setEnabled(
            state in (RideState.RIDING, RideState.PAUSED) and snap.trainer_connected
        )
        self.pause_action.setText("Resume" if state is RideState.PAUSED else "Pause")

        if self._mock_power is not None:
            trainer = f"simulated rider, {self._mock_power:.0f} W"
        else:
            trainer = self.settings.trainer or "no trainer set"
        if riding and snap is not None:
            trainer += " — connected" if snap.trainer_connected else " — connecting …"
            if state is RideState.PAUSED:
                trainer += " — paused"
        self._trainer_status.setText(f"Trainer: {trainer}")
        hr = self.settings.heart_rate if self._mock_power is None else ""
        if hr and riding and snap is not None:
            hr += " — connected" if snap.hr_connected else " — connecting …"
        self._hr_status.setText(f"HR: {hr or 'none'}")

    def _follow_changed(self) -> None:
        if self.map is not None:
            self.map.set_follow(self.follow_action.isChecked())

    # -- closing --------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.riding:
            event.accept()
            return
        answer = QMessageBox.question(self, "Quit", "A ride is in progress. Stop it and quit?")
        if answer == QMessageBox.StandardButton.Yes:
            self._quit_after_ride = True
            self.stop_ride()
        event.ignore()  # closed again when the ride task ends
