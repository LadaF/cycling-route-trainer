"""Course manager: the list of courses, importing routes, and preparing a course again
with another elevation source or with the bridges and tunnels fetched in the browser.

The public Overpass server turns applications away when it is busy, but answers
Overpass Turbo in the browser. So the manager opens the query there, and the answer
saved from Turbo is loaded back with a file dialog; it is cached for the route.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from crt.config import Settings
from crt.route.course import Course

log = logging.getLogger(__name__)

ROUTES = "Routes and courses (*.gpx *.tcx *.fit *.geojson *.crt.npz);;All files (*)"
ANSWERS = "Overpass answers (*.json *.geojson);;All files (*)"
SOURCE_NAMES = {
    "auto": "automatic (DMR 5G, RGE ALTI, else Copernicus)",
    "dmr5g": "ČÚZK DMR 5G (Czechia)",
    "rgealti": "IGN RGE ALTI (France)",
    "copernicus": "Copernicus GLO-30",
    "file": "the route file",
}
STEPS = (
    "1. Open the query in Overpass Turbo and wait until the map shows the answer.<br>"
    "2. Choose <i>Export → Data → raw OSM data</i> (or GeoJSON): download or copy it.<br>"
    "3. Load the saved file, or paste the copied answer (not the query). Needed only once."
)


def describe(course: Course, path: Path) -> str:
    """The course details, as rich text."""
    from crt.route.pipeline import route_of

    meta = course.meta
    route = route_of(course)
    source = meta.get("source_file")
    coverage = ", ".join(
        f"{name} {share * 100:.0f} %"
        for name, share in meta.get("elevation_coverage", {}).items()
        if share > 0
    )
    if not meta.get("structures_checked"):
        structures = "<b>not checked</b>"
    elif not meta.get("structures"):
        structures = "checked, none on the route"
    else:
        structures = "<br>".join(
            f"{html.escape(iv['kind'])} {html.escape(iv['name'] or '')} at "
            f"{iv['s0'] / 1000:.2f}–{iv['s1'] / 1000:.2f} km"
            for iv in meta["structures"]
        )
    rows = [
        ("Course file", html.escape(str(path))),
        (
            "Route",
            html.escape(source or "unknown") + ("" if route or not source else " (missing)"),
        ),
        ("Length", f"{course.length / 1000:.2f} km"),
        (
            "Elevation",
            f"{course.z.min():.0f}–{course.z.max():.0f} m, ascent {course.total_ascent:.0f} m",
        ),
        ("Elevation from", html.escape(coverage or "unknown")),
        ("Bridges and tunnels", structures),
    ]
    if meta.get("reversed"):
        rows.insert(2, ("Direction", "reversed"))
    cells = "".join(
        f"<tr><td style='padding-right:1em'><b>{k}</b></td><td>{v}</td></tr>" for k, v in rows
    )
    return f"<h3>{html.escape(course.name)}</h3><table>{cells}</table>"


class CourseManager(QDialog):
    """The course list and the course details, with the actions on the selected course.

    ``ride`` is emitted with the course file to ride; ``changed`` when a course file
    was prepared again, so a window showing it can reload it.
    """

    ride = Signal(Path)
    changed = Signal(Path)

    def __init__(self, settings: Settings, save: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Courses")
        self.resize(1000, 600)
        self._settings = settings
        self._save = save
        self._course: Course | None = None
        self._task: asyncio.Future | None = None

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda _: self._ride())
        import_button = QPushButton("Import route…")
        import_button.clicked.connect(self.choose_route)
        self.remove_button = QPushButton("Remove from list")
        self.remove_button.setToolTip("The files are kept.")
        self.remove_button.clicked.connect(self._remove)
        left = QWidget()
        column = QVBoxLayout(left)
        column.addWidget(self.list)
        buttons = QHBoxLayout()
        buttons.addWidget(import_button)
        buttons.addWidget(self.remove_button)
        column.addLayout(buttons)

        self.details = QLabel()
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.details.setWordWrap(True)
        details = QScrollArea()
        details.setWidget(self.details)
        details.setWidgetResizable(True)
        details.setFrameShape(QScrollArea.Shape.NoFrame)

        self.source = QComboBox()
        for key, name in SOURCE_NAMES.items():
            self.source.addItem(name, key)
        self.source.currentIndexChanged.connect(self._update_buttons)
        self.prepare_button = QPushButton("Prepare again")
        self.prepare_button.setToolTip("Prepare the course from its route with this elevation.")
        self.prepare_button.clicked.connect(self._prepare_again)
        elevation = QGroupBox("Elevation")
        row = QHBoxLayout(elevation)
        row.addWidget(self.source, 1)
        row.addWidget(self.prepare_button)

        self.query_button = QPushButton("Open the query in the browser")
        self.query_button.clicked.connect(self._open_query)
        self.copy_button = QPushButton("Copy the link")
        self.copy_button.clicked.connect(self._copy_query)
        self.answer_button = QPushButton("Load the saved answer…")
        self.answer_button.clicked.connect(self.choose_answer)
        self.paste_button = QPushButton("Paste the answer")
        self.paste_button.setToolTip("Use the Overpass answer in the clipboard.")
        self.paste_button.clicked.connect(self.paste_answer)
        bridges = QGroupBox("Bridges and tunnels from OpenStreetMap")
        column = QVBoxLayout(bridges)
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        column.addWidget(steps)
        grid = QGridLayout()  # the query in the first row, the answer in the second
        for i, button in enumerate(self._bridge_buttons):
            grid.addWidget(button, i // 2, i % 2)
        column.addLayout(grid)

        right = QWidget()
        column = QVBoxLayout(right)
        column.addWidget(details, 1)
        column.addWidget(elevation)
        column.addWidget(bridges)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([350, 650])

        self.status = QLabel()
        self.ride_button = QPushButton("Ride this course")
        self.ride_button.setDefault(True)
        self.ride_button.clicked.connect(self._ride)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.ride_button)
        bottom.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)
        self._fill_list(Path(settings.course) if settings.course else None)

    # -- list -----------------------------------------------------------------

    def _fill_list(self, select: Path | None) -> None:
        self.list.clear()
        paths = list(self._settings.courses)
        if self._settings.course and self._settings.course not in paths:
            paths.insert(0, self._settings.course)
        for name in paths:
            path = Path(name)
            item = QListWidgetItem(path.name.removesuffix(".crt.npz"))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(str(path))
            if not path.exists():
                item.setText(item.text() + " (missing)")
                item.setForeground(self.palette().placeholderText())
            self.list.addItem(item)
            if path == select:
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self._update_buttons()

    @property
    def selected(self) -> Path | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _selected(self, *_) -> None:
        path = self.selected
        self._course = None
        if path is None:
            self.details.setText("Import a route (GPX, TCX, FIT) to start.")
        elif not path.exists():
            self.details.setText(f"{html.escape(str(path))} is not there any more.")
        else:
            try:
                self._course = Course.load(path)
            except Exception as exc:
                log.warning("cannot load %s: %s", path, exc)
                self.details.setText(f"Cannot load {html.escape(str(path))}:<br>{exc}")
            else:
                from crt.route.pipeline import PipelineOptions

                self.details.setText(describe(self._course, path))
                index = self.source.findData(PipelineOptions.of(self._course).source)
                self.source.setCurrentIndex(max(index, 0))
        self._update_buttons()

    def _remove(self) -> None:
        path = self.selected
        if path is None:
            return
        self._settings.courses = [c for c in self._settings.courses if Path(c) != path]
        if self._settings.course and Path(self._settings.course) == path:
            self._settings.course = ""
        self._save()
        self._fill_list(None)

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def reject(self) -> None:
        if self.busy:
            self.status.setText("Please wait until the course is prepared.")
        else:
            super().reject()

    def _update_buttons(self) -> None:
        from crt.route.pipeline import route_of

        idle = not self.busy
        course = self._course
        rebuildable = idle and course is not None and route_of(course) is not None
        bridges = rebuildable and self.source.currentData() != "file"
        self.remove_button.setEnabled(idle and self.selected is not None)
        self.ride_button.setEnabled(idle and course is not None)
        self.prepare_button.setEnabled(rebuildable)
        for button in self._bridge_buttons:
            button.setEnabled(bridges)

    # -- actions --------------------------------------------------------------

    def _ride(self) -> None:
        if self._course is not None and self.ride_button.isEnabled():
            self.ride.emit(self.selected)
            self.accept()

    def choose_route(self) -> None:
        start = str(self.selected.parent) if self.selected else ""
        name, _ = QFileDialog.getOpenFileName(self, "Import route", start, ROUTES)
        if name:
            self._run(f"preparing {Path(name).name} …", self.import_route(Path(name)))

    async def import_route(self, path: Path) -> None:
        """Prepare a route (or reuse its prepared course) and add it to the list."""
        from crt.route.pipeline import prepared_course

        _, course_file = await asyncio.to_thread(prepared_course, path)
        courses = self._settings.courses
        self._settings.courses = [str(course_file)] + [c for c in courses if c != str(course_file)]
        self._save()
        self._fill_list(course_file)

    def _query_url(self) -> str:
        from crt.route.pipeline import (
            PipelineOptions,
            overpass_turbo_url,
            route_of,
            structure_query,
        )

        assert self._course is not None
        query = structure_query(route_of(self._course), PipelineOptions.of(self._course))
        return overpass_turbo_url(query)

    def _open_query(self) -> None:
        if not QDesktopServices.openUrl(QUrl(self._query_url())):
            self._copy_query()
            self.status.setText("Cannot open the browser; the link is in the clipboard.")

    def _copy_query(self) -> None:
        QGuiApplication.clipboard().setText(self._query_url())
        self.status.setText("The link is in the clipboard.")

    def choose_answer(self) -> None:
        start = str(Path.home() / "Downloads")
        name, _ = QFileDialog.getOpenFileName(self, "Overpass answer", start, ANSWERS)
        if name:
            self._rebuild("adding the bridges and tunnels …",
                          fix_bridges=True, structures_file=name)  # fmt: skip

    def paste_answer(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if not text:
            QMessageBox.information(self, "Courses", "The clipboard has no text.")
            return
        self._rebuild("adding the bridges and tunnels …", answer=text)

    @property
    def _bridge_buttons(self) -> tuple[QPushButton, ...]:
        return (self.query_button, self.copy_button, self.answer_button, self.paste_button)

    def _prepare_again(self) -> None:
        self._rebuild("preparing the course again …", source=self.source.currentData())

    def _rebuild(self, message: str, **changes) -> None:
        path = self.selected
        if path is not None:
            self._run(message, self.rebuild(path, **changes))

    async def rebuild(self, path: Path, **changes) -> None:
        from crt.route.pipeline import rebuild_course

        await asyncio.to_thread(rebuild_course, path, **changes)
        self._fill_list(path)
        self.changed.emit(path)

    def _run(self, message: str, work) -> None:
        """Run a preparation in the background; errors are shown, not raised."""

        async def wrapped() -> None:
            try:
                await work
            except Exception as exc:
                log.exception("course preparation failed")
                QMessageBox.warning(self, "Courses", str(exc))
                self.status.clear()
            else:
                self.status.setText("Done.")
            finally:
                self._task = None  # still running here, but finished as far as buttons go
                self._update_buttons()

        self.status.setText(message)
        self._task = asyncio.ensure_future(wrapped())
        self._update_buttons()
