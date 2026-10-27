"""``crt-gui``: the ride window on a qasync event loop."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer


def main(
    course: Annotated[
        Path | None, typer.Argument(exists=True, dir_okay=False, help="Course or route file.")
    ] = None,
    mock_power: Annotated[
        float | None, typer.Option(help="No hardware: simulated rider at this power [W].")
    ] = None,
    speedup: Annotated[float, typer.Option(help="Time acceleration (simulated rider only).")] = 1.0,
    web_map: Annotated[
        bool, typer.Option(help="OpenStreetMap map (needs network); otherwise a plain track.")
    ] = True,
) -> None:
    """Ride a course on the smart trainer, with live metrics, profile and map."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if sys.platform == "win32":
        # Qt initialises COM as single-threaded; WinRT Bluetooth needs it multi-threaded.
        from bleak.backends.winrt.util import allow_sta

        allow_sta()
    if web_map:
        import PySide6.QtWebEngineWidgets  # noqa: F401  (must be imported before QApplication)

    import pyqtgraph as pg
    import qasync
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    from crt.config import Settings
    from crt.gui.window import RideWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Cycling Route Trainer")
    palette = app.palette()
    pg.setConfigOptions(
        antialias=True,
        background=palette.color(QPalette.ColorRole.Base),
        foreground=palette.color(QPalette.ColorRole.Text),
    )
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    settings = Settings.load()
    window = RideWindow(settings, web_map=web_map, mock_power=mock_power, speedup=speedup)
    window.resize(1400, 900)
    window.show()
    course = course or (Path(settings.course) if settings.course else None)
    if course is not None and course.exists():
        asyncio.ensure_future(window.open_course(course))

    closed = asyncio.Event()
    app.aboutToQuit.connect(closed.set)
    with loop:
        loop.run_until_complete(closed.wait())
    # The web pages must go before their profile and the application, not in whatever
    # order Python happens to release them.
    delete(window)


def run() -> None:
    typer.run(main)


if __name__ == "__main__":
    run()
