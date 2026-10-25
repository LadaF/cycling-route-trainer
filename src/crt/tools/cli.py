"""``crt-tool``: BLE diagnostics for trainers and heart rate sensors.

    crt-tool scan                     list nearby FTMS / power / heart rate devices
    crt-tool probe TARGET             dump device info, FTMS capabilities and GATT table
    crt-tool monitor --trainer T --hr H   live data, notification rate and gaps
    crt-tool control TARGET           interactive FTMS control point console

TARGET is a device address or a part of its advertised name.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import logging
import shlex
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

import typer
from bleak import BleakClient
from bleak.exc import BleakError
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.table import Table

from crt.devices import uuids
from crt.devices.ble import dump_gatt, find_device, read_device_info, scan
from crt.devices.codec import CodecError
from crt.devices.ftms import codec
from crt.devices.ftms.client import FtmsClient, FtmsCommandError
from crt.devices.ftms.codec import IndoorBikeData, MachineStatus, SimulationParameters
from crt.devices.hrs import HeartRateClient, HeartRateMeasurement

app = typer.Typer(
    no_args_is_help=True,
    help="BLE diagnostics for trainers and heart rate sensors. "
    "TARGET is a device address or a part of its advertised name.",
    rich_markup_mode=None,
)
console = Console()

# Gap between notifications that counts as a dropout
DROPOUT_S = 2.0


@app.callback()
def _main(
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Debug logging.")] = False,
) -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    # Debug output only from this application: libraries (httpx, rasterio,
    # matplotlib) are far too chatty at debug level.
    logging.getLogger("crt").setLevel(logging.DEBUG if verbose else logging.INFO)


def _run(coro) -> None:
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("[dim]interrupted[/]")
    except (BleakError, FtmsCommandError, TimeoutError, OSError) as exc:
        console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(1) from None


async def _connect(stack: contextlib.AsyncExitStack, target: str, label: str) -> BleakClient:
    console.print(f"looking for {label} [bold]{target}[/] …")
    device = await find_device(target)

    closing = False

    def on_disconnect(_client: BleakClient) -> None:
        if not closing:
            console.print(f"[red]{label} {device.address} disconnected[/]")

    def mark_closing() -> None:
        nonlocal closing
        closing = True

    client = BleakClient(device, disconnected_callback=on_disconnect)
    await stack.enter_async_context(client)
    # Exit callbacks run in reverse order, so this runs before the client disconnects.
    stack.callback(mark_closing)
    console.print(f"connected to {label} {device.name or ''} ({device.address})")
    return client


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@app.command("scan")
def scan_cmd(
    timeout: Annotated[float, typer.Option(help="Scan duration in seconds.")] = 8.0,
    all_devices: Annotated[
        bool, typer.Option("--all", help="Also list devices without FTMS/CPS/HRS.")
    ] = False,
    name: Annotated[
        str | None,
        typer.Option(help="Only devices whose name contains this (ignores case and accents)."),
    ] = None,
) -> None:
    """List nearby BLE trainers, power meters and heart rate sensors."""

    async def run() -> None:
        with console.status(f"scanning for {timeout:g} s …"):
            results = await scan(timeout, include_all=all_devices, name=name)
        if not results:
            console.print("no devices found. Is the device awake and not connected to another app?")
            return
        table = Table("name", "address", "RSSI", "services")
        for r in results:
            table.add_row(r.name or "?", r.address, str(r.rssi), " ".join(r.services))
        console.print(table)

    _run(run())


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def _range_str(r: codec.SupportedRange | None, unit: str) -> str:
    if r is None:
        return "not reported"
    return f"{r.minimum:g} … {r.maximum:g} {unit} (step {r.increment:g})"


@app.command("probe")
def probe_cmd(
    target: Annotated[str, typer.Argument(help="Device address or part of its name.")],
    json_path: Annotated[
        Path | None, typer.Option("--json", help="Save the full report as JSON.")
    ] = None,
) -> None:
    """Show device information, FTMS capabilities and the complete GATT table."""

    async def run() -> None:
        async with contextlib.AsyncExitStack() as stack:
            client = await _connect(stack, target, "device")
            info = await read_device_info(client)
            gatt = await dump_gatt(client)
            ftms = None
            if client.services.get_service(uuids.FTMS_SERVICE) is not None:
                ftms = FtmsClient(client)
                await ftms.read_capabilities()
            has_hrs = client.services.get_service(uuids.HEART_RATE_SERVICE) is not None

        table = Table("service / characteristic", "handle", "properties", "value")
        for service in gatt:
            table.add_row(f"[bold]{service.description}[/] {service.uuid}", "", "", "")
            for c in service.characteristics:
                value = c.value_hex if c.value_hex is not None else (c.read_error or "")
                table.add_row(
                    f"  {c.description} {c.uuid[4:8]}", str(c.handle), ",".join(c.properties), value
                )
        console.print(table)

        console.print("\n[bold]Device information[/]")
        for key, value in info.items():
            console.print(f"  {key}: {value}")

        report: dict = {"address": client.address, "info": info, "heart_rate_service": has_hrs}
        if ftms is not None and ftms.features is not None:
            f = ftms.features
            names = codec.describe_features(f)
            console.print("\n[bold]FTMS[/]")
            console.print(f"  machine features: {', '.join(names['machine']) or '-'}")
            console.print(f"  target settings:  {', '.join(names['target']) or '-'}")
            yes = {True: "[green]yes[/]", False: "[red]no[/]"}
            console.print(f"  simulation mode (grade): {yes[f.supports_simulation]}")
            console.print(f"  resistance level target: {yes[f.supports_resistance_target]}")
            console.print(f"  power target (ERG):      {yes[f.supports_power_target]}")
            console.print(f"  inclination range: {_range_str(ftms.inclination_range, '%')}")
            console.print(f"  resistance range:  {_range_str(ftms.resistance_range, '')}")
            console.print(f"  power range:       {_range_str(ftms.power_range, 'W')}")
            report["ftms"] = {
                "features": names,
                "inclination_range": ftms.inclination_range and asdict(ftms.inclination_range),
                "resistance_range": ftms.resistance_range and asdict(ftms.resistance_range),
                "power_range": ftms.power_range and asdict(ftms.power_range),
            }
        else:
            console.print("\n[yellow]no Fitness Machine Service on this device[/]")
        if has_hrs:
            console.print("[bold]Heart Rate Service[/] present")

        if json_path:
            report["gatt"] = [asdict(s) for s in gatt]
            json_path.write_text(json.dumps(report, indent=2))
            console.print(f"\nreport saved to {json_path}")

    _run(run())


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------


@dataclass
class StreamStats:
    count: int = 0
    first: float | None = None
    last: float | None = None
    max_gap: float = 0.0
    dropouts: int = 0

    def add(self, t: float) -> None:
        if self.last is not None:
            gap = t - self.last
            self.max_gap = max(self.max_gap, gap)
            if gap > DROPOUT_S:
                self.dropouts += 1
        else:
            self.first = t
        self.last = t
        self.count += 1

    @property
    def rate_hz(self) -> float:
        if self.count < 2 or self.first is None or self.last is None or self.last == self.first:
            return 0.0
        return (self.count - 1) / (self.last - self.first)


CSV_COLUMNS = ["t_s", "source", "power_w", "cadence_rpm", "speed_kmh", "hr_bpm", "rr_s", "raw_hex"]


@app.command("monitor")
def monitor_cmd(
    trainer: Annotated[str | None, typer.Option(help="Trainer address or name.")] = None,
    hr: Annotated[str | None, typer.Option(help="Heart rate sensor address or name.")] = None,
    duration: Annotated[float | None, typer.Option(help="Stop after this many seconds.")] = None,
    csv_path: Annotated[
        Path | None, typer.Option("--csv", help="Log every notification to a CSV file.")
    ] = None,
    raw: Annotated[
        bool, typer.Option(help="Print every packet in hex instead of a table.")
    ] = False,
) -> None:
    """Show live trainer and heart rate data with notification statistics.

    Read-only: does not take control of the trainer."""
    if trainer is None and hr is None:
        raise typer.BadParameter("give --trainer, --hr or both")

    async def run() -> None:
        latest: dict[str, float | int | str] = {}
        stats = {"trainer": StreamStats(), "hr": StreamStats()}
        t0 = time.monotonic()

        with contextlib.ExitStack() as files:
            writer = None
            if csv_path:
                f = files.enter_context(csv_path.open("w", newline=""))
                writer = csv.DictWriter(f, CSV_COLUMNS)
                writer.writeheader()

            def on_bike(t: float, data: IndoorBikeData, raw_bytes: bytes) -> None:
                stats["trainer"].add(t)
                latest.update(data.present_fields())
                if raw:
                    console.print(
                        f"{t - t0:9.3f} trainer {raw_bytes.hex()}  {data.present_fields()}"
                    )
                if writer:
                    writer.writerow({
                        "t_s": f"{t - t0:.3f}", "source": "trainer",
                        "power_w": data.instantaneous_power_w,
                        "cadence_rpm": data.instantaneous_cadence_rpm,
                        "speed_kmh": data.instantaneous_speed_kmh,
                        "raw_hex": raw_bytes.hex(),
                    })  # fmt: skip

            def on_status(t: float, status: MachineStatus, raw_bytes: bytes) -> None:
                console.print(f"{t - t0:9.3f} trainer status: {status}")

            def on_hr(t: float, m: HeartRateMeasurement, raw_bytes: bytes) -> None:
                stats["hr"].add(t)
                latest["heart_rate_bpm (HR sensor)"] = m.bpm
                latest["sensor_contact"] = "n/a" if m.contact is None else str(m.contact)
                if raw:
                    console.print(f"{t - t0:9.3f} hr      {raw_bytes.hex()}  {m}")
                if writer:
                    writer.writerow({
                        "t_s": f"{t - t0:.3f}", "source": "hr", "hr_bpm": m.bpm,
                        "rr_s": " ".join(f"{x:.4f}" for x in m.rr_intervals_s),
                        "raw_hex": raw_bytes.hex(),
                    })  # fmt: skip

            def render() -> Table:
                table = Table("value", "", title=f"{time.monotonic() - t0:.0f} s")
                for key in sorted(latest):
                    v = latest[key]
                    table.add_row(key, f"{v:.2f}" if isinstance(v, float) else str(v))
                for name, s in stats.items():
                    if (name == "trainer" and trainer) or (name == "hr" and hr):
                        table.add_row(
                            f"[dim]{name} notifications[/]",
                            f"[dim]{s.count}, {s.rate_hz:.2f} Hz, max gap {s.max_gap:.2f} s, "
                            f"dropouts >{DROPOUT_S:g} s: {s.dropouts}[/]",
                        )
                return table

            async with contextlib.AsyncExitStack() as stack:
                # Sequential connects: concurrent ones are unreliable on some BLE stacks.
                if trainer:
                    client = await _connect(stack, trainer, "trainer")
                    ftms = FtmsClient(client, on_bike_data=on_bike, on_status=on_status)
                    await ftms.start(control=False)
                if hr:
                    client = await _connect(stack, hr, "heart rate sensor")
                    await HeartRateClient(client, on_hr).start()

                deadline = None if duration is None else time.monotonic() + duration
                live = contextlib.nullcontext() if raw else Live(render(), console=console)
                with live:
                    while deadline is None or time.monotonic() < deadline:
                        await asyncio.sleep(0.25)
                        if not raw:
                            live.update(render())

        if not raw:
            console.print(render())

    _run(run())


# ---------------------------------------------------------------------------
# control
# ---------------------------------------------------------------------------

CONTROL_HELP = """\
commands:
  request                     request control (needed before any other command)
  start | stop | pause | reset
  grade G [crr] [cw] [wind]   simulation mode: grade %, Crr, Cw kg/m, wind m/s
  level L                     target resistance level (uint8 encoding, FTMS 1.0)
  levelw L                    target resistance level (sint16 encoding)
  erg W                       target power in watts
  incline P                   target inclination %
  raw HEX                     send raw control point bytes, e.g. raw 1100002c010000
  caps                        show features and supported ranges
  watch [on|off]              print trainer data packets
  quit"""


def _stdin_lines(loop: asyncio.AbstractEventLoop) -> asyncio.Queue[str | None]:
    """Read stdin lines in a daemon thread, so a pending input() never blocks exit."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def reader() -> None:
        for line in sys.stdin:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=reader, daemon=True).start()
    return queue


def _show_caps(ftms: FtmsClient) -> None:
    if ftms.features:
        names = codec.describe_features(ftms.features)
        console.print(f"target settings: {', '.join(names['target']) or '-'}")
    console.print(f"inclination range: {_range_str(ftms.inclination_range, '%')}")
    console.print(f"resistance range:  {_range_str(ftms.resistance_range, '')}")
    console.print(f"power range:       {_range_str(ftms.power_range, 'W')}")


async def _control_command(ftms: FtmsClient, cmd: str, args: list[str]) -> str | None:
    """Run one console command. Returns a message to print, or None."""
    f = [float(a) for a in args] if cmd not in ("raw", "watch") else []
    match cmd:
        case "request":
            resp = await ftms.command(codec.request_control(), check=False)
        case "start":
            resp = await ftms.command(codec.start_or_resume(), check=False)
        case "stop" | "pause":
            resp = await ftms.command(codec.stop_or_pause(cmd == "pause"), check=False)
        case "reset":
            resp = await ftms.command(codec.reset(), check=False)
        case "grade":
            params = SimulationParameters(*f)
            resp = await ftms.command(codec.set_indoor_bike_simulation(params), check=False)
        case "level" | "levelw":
            payload = codec.set_target_resistance_level(f[0], wide=cmd == "levelw")
            resp = await ftms.command(payload, check=False)
        case "erg":
            resp = await ftms.command(codec.set_target_power(f[0]), check=False)
        case "incline":
            resp = await ftms.command(codec.set_target_inclination(f[0]), check=False)
        case "raw":
            resp = await ftms.command(bytes.fromhex("".join(args)), check=False)
        case _:
            return f"unknown command {cmd!r}, type 'help'"
    colour = "green" if resp.ok else "red"
    return f"[{colour}]{resp}[/]"


@app.command("control")
def control_cmd(
    target: Annotated[str, typer.Argument(help="Trainer address or part of its name.")],
) -> None:
    """Interactive FTMS control point console for testing trainer commands."""

    async def run() -> None:
        watch = False
        t0 = time.monotonic()

        def on_bike(t: float, data: IndoorBikeData, _raw: bytes) -> None:
            if watch:
                console.print(f"[dim]{t - t0:8.2f} {data.present_fields()}[/]")

        def on_status(t: float, status: MachineStatus, _raw: bytes) -> None:
            console.print(f"[cyan]{t - t0:8.2f} status: {status}[/]")

        async with contextlib.AsyncExitStack() as stack:
            client = await _connect(stack, target, "trainer")
            ftms = FtmsClient(client, on_bike_data=on_bike, on_status=on_status)
            await ftms.start(control=True)
            _show_caps(ftms)
            console.print(CONTROL_HELP)

            lines = _stdin_lines(asyncio.get_running_loop())
            while True:
                console.print("ftms> ", end="")
                line = await lines.get()
                if line is None:
                    break
                try:
                    words = shlex.split(line)
                except ValueError as exc:
                    console.print(f"[red]{exc}[/]")
                    continue
                if not words:
                    continue
                cmd, args = words[0].lower(), words[1:]
                if cmd in ("quit", "exit", "q"):
                    break
                if cmd in ("help", "?"):
                    console.print(CONTROL_HELP)
                elif cmd == "caps":
                    _show_caps(ftms)
                elif cmd == "watch":
                    watch = not args or args[0] == "on"
                else:
                    try:
                        message = await _control_command(ftms, cmd, args)
                    except (ValueError, IndexError, TypeError, CodecError) as exc:
                        message = f"[red]bad arguments: {exc}[/]"
                    except TimeoutError:
                        message = "[red]no response from trainer[/]"
                    if message:
                        console.print(message)

    _run(run())


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------


@app.command("route")
def route_cmd(
    route_file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="GPX, TCX or FIT.")
    ],
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Course file (default: next to the route, .crt.npz)."),
    ] = None,
    source: Annotated[
        str,
        typer.Option(help="Elevation: auto (DMR 5G, else Copernicus), dmr5g, copernicus, file."),
    ] = "auto",
    spacing: Annotated[float, typer.Option(help="Course grid spacing [m].")] = 2.0,
    smoothing: Annotated[float, typer.Option(help="Profile smoothing, Gaussian sigma [m].")] = 20.0,
    fix_bridges: Annotated[
        bool,
        typer.Option(
            "--bridges/--no-bridges",
            help="Correct bridges and tunnels from OpenStreetMap (skipped if Overpass fails).",
        ),
    ] = True,
    plot: Annotated[bool, typer.Option(help="Show the elevation and grade profile.")] = False,
    plot_file: Annotated[
        Path | None, typer.Option(help="Save the profile plot (PNG, PDF, SVG).")
    ] = None,
    compare: Annotated[
        bool, typer.Option(help="Also plot the Copernicus GLO-30 profile, for comparison.")
    ] = False,
) -> None:
    """Prepare a route for riding: elevation from a DEM, bridge fix, smoothing, grade."""
    # Imported here: numpy, pyproj and rasterio are slow to load for the BLE tools.
    import numpy as np

    from crt.route.pipeline import SOURCES, PipelineOptions, build_course

    if source not in SOURCES:
        raise typer.BadParameter(f"use one of {', '.join(SOURCES)}", param_hint="--source")
    options = PipelineOptions(
        source=source, spacing=spacing, smoothing=smoothing, fix_bridges=fix_bridges
    )
    with console.status("preparing the course (first run downloads elevation data) …"):
        course = build_course(route_file, options)
    output = output or route_file.with_suffix(".crt.npz")
    course.save(output)

    s, z = course.s, course.z
    ahead = s <= course.length - 50
    grade50 = (np.interp(s[ahead] + 50, s, z) - z[ahead]) / 50 * 100

    def ascent(profile: np.ndarray) -> float:
        d = np.diff(profile)
        return float(d[d > 0].sum())

    console.print(f"[bold]{course.name}[/]")
    console.print(
        f"  length:    {course.length / 1000:.2f} km ({len(s)} points every {spacing:g} m)"
    )
    other = f"raw DEM {ascent(course.z_raw):.0f} m"
    if not np.isnan(course.z_file).all():
        other += f", route file {ascent(course.z_file):.0f} m"
    console.print(
        f"  elevation: {z.min():.0f}–{z.max():.0f} m, ascent {course.total_ascent:.0f} m ({other})"
    )
    if grade50.size:
        console.print(
            f"  grade:     {grade50.max():+.1f} % / {grade50.min():+.1f} % (steepest 50 m)"
        )
    coverage = ", ".join(
        f"{name} {share * 100:.1f} %"
        for name, share in course.meta["elevation_coverage"].items()
        if share > 0
    )
    console.print(f"  elevation sources: {coverage or 'route file only'}")
    structures = course.meta["structures"]
    if not course.meta["structures_checked"]:
        console.print("  bridges/tunnels: [yellow]not checked[/]")
    elif not structures:
        console.print("  bridges/tunnels: none on the route")
    for st in structures:
        console.print(
            f"  {st['kind']} {st['name'] or ''} at {st['s0'] / 1000:.2f}–{st['s1'] / 1000:.2f} km"
        )
    console.print(f"saved to {output}")

    if plot or plot_file:
        from crt.route.plot import plot_course

        extra = {}
        if compare:
            from crt.elevation.copernicus import CopernicusGlo30

            with console.status("sampling Copernicus GLO-30 …"):
                extra["Copernicus GLO-30"] = CopernicusGlo30().sample(course.lat, course.lon)
        plot_course(course, extra, plot_file)
        if plot_file:
            console.print(f"plot saved to {plot_file}")


# ---------------------------------------------------------------------------
# ride and replay
# ---------------------------------------------------------------------------


def _hms(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def _load_course(path: Path):
    from crt.route.course import Course
    from crt.route.pipeline import build_course

    if path.name.endswith(".crt.npz"):
        return Course.load(path)
    with console.status("preparing the course with default settings …"):
        return build_course(path)


def _ride_table(snap, course_name: str) -> Table:
    from crt.session.session import describe_command

    table = Table(title=f"{course_name} — {snap.state.value}", show_header=False, min_width=48)
    table.add_column(style="dim")
    table.add_column(justify="right", style="bold")
    trainer = "[green]connected[/]" if snap.trainer_connected else "[red]connecting …[/]"
    table.add_row("trainer", trainer)
    table.add_row("time", _hms(snap.elapsed))
    table.add_row("distance", f"{snap.s / 1000:.2f} km  ({snap.remaining / 1000:.2f} km to go)")
    table.add_row("speed", f"{snap.v * 3.6:.1f} km/h")
    table.add_row("power", f"{snap.power:.0f} W")
    if snap.cadence is not None:
        table.add_row("cadence", f"{snap.cadence:.0f} rpm")
    if snap.heart_rate is not None or snap.hr_connected:
        table.add_row("heart rate", "–" if snap.heart_rate is None else f"{snap.heart_rate} bpm")
    table.add_row("terrain grade", f"{snap.terrain_grade:+.1f} %")
    table.add_row("trainer setting", describe_command(snap.command))
    table.add_row("elevation", f"{snap.elevation:.0f} m  (+{snap.ascent:.0f} m)")
    if snap.trainer_speed_kmh is not None:
        table.add_row("[dim]trainer speed[/]", f"[dim]{snap.trainer_speed_kmh:.1f} km/h[/]")
    return table


def _summary(snap, course) -> None:
    avg_speed = snap.s / snap.moving_time * 3.6 if snap.moving_time > 0 else 0.0
    avg_power = snap.work / snap.elapsed if snap.elapsed > 0 else 0.0
    console.print(f"[bold]{course.name}[/]: {snap.state.value}")
    console.print(f"  distance {snap.s / 1000:.2f} of {course.length / 1000:.2f} km")
    console.print(f"  time {_hms(snap.elapsed)}, moving {_hms(snap.moving_time)}")
    console.print(f"  average speed {avg_speed:.1f} km/h, average power {avg_power:.0f} W")
    console.print(
        f"  ascent {snap.ascent:.0f} m, work {snap.work / 1000:.0f} kJ"
        + (f", braked {snap.braking / 1000:.0f} kJ" if snap.braking > 0 else "")
    )


@app.command("ride")
def ride_cmd(
    course_file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="Course (.crt.npz) or route file.")
    ],
    trainer: Annotated[str | None, typer.Option(help="Trainer address or name.")] = None,
    hr: Annotated[str | None, typer.Option(help="Heart rate sensor address or name.")] = None,
    mock_power: Annotated[
        float | None, typer.Option(help="No hardware: simulated rider at this power [W].")
    ] = None,
    mock_csv: Annotated[
        Path | None, typer.Option(help="No hardware: power profile CSV (time_s,power_w).")
    ] = None,
    speedup: Annotated[float, typer.Option(help="Time acceleration (simulated rider only).")] = 1.0,
    min_grade: Annotated[float, typer.Option(help="Minimum grade sent to the trainer [%].")] = 0.0,
    difficulty: Annotated[float, typer.Option(help="Scale of the terrain grade, 0–1.")] = 1.0,
    lookahead: Annotated[float, typer.Option(help="Grade lookahead [s].")] = 1.0,
    rider_mass: Annotated[float, typer.Option(help="Rider mass [kg].")] = 75.0,
    bike_mass: Annotated[float, typer.Option(help="Bike mass [kg].")] = 9.0,
    cda: Annotated[float, typer.Option(help="Drag area CdA [m²].")] = 0.32,
    crr: Annotated[float, typer.Option(help="Rolling resistance coefficient.")] = 0.004,
    temperature: Annotated[float, typer.Option(help="Air temperature [°C].")] = 20.0,
    max_speed: Annotated[float, typer.Option(help="Virtual braking above this [km/h].")] = 60.0,
    start_km: Annotated[float, typer.Option(help="Start this far along the course [km].")] = 0.0,
    log_path: Annotated[
        Path | None, typer.Option("--log", help="Ride log CSV (default: user data directory).")
    ] = None,
) -> None:
    """Ride a course: trainer resistance from the terrain, speed from the physics."""
    import datetime
    from dataclasses import asdict

    import platformdirs

    from crt.control.policy import ControlSettings, GradePolicy, LevelPolicy
    from crt.devices.connection import BleHeartRate, BleTrainer
    from crt.devices.mock import MockTrainer, constant_power, power_from_csv
    from crt.physics.model import RiderParams
    from crt.session.log import RideLog
    from crt.session.runner import Clock, RideRunner
    from crt.session.session import RideSession

    simulated = mock_power is not None or mock_csv is not None
    if simulated == (trainer is not None):
        raise typer.BadParameter("give either --trainer or --mock-power / --mock-csv")
    if speedup != 1.0 and not simulated:
        raise typer.BadParameter(
            "only a simulated rider can be accelerated", param_hint="--speedup"
        )

    course = _load_course(course_file)
    rider = RiderParams(
        rider_mass=rider_mass, bike_mass=bike_mass, cda=cda, crr=crr,
        temperature_c=temperature, max_speed=max_speed / 3.6,
    )  # fmt: skip
    settings = ControlSettings(min_grade=min_grade, difficulty=difficulty, lookahead_s=lookahead)
    start_s = start_km * 1000

    if log_path is None:
        rides = Path(platformdirs.user_data_dir("crt")) / "rides"
        rides.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        log_path = rides / f"{stamp}.csv"
    header = {
        "course": str(course_file.resolve()), "course_name": course.name,
        "trainer": trainer or "simulated", "started": datetime.datetime.now().isoformat(),
        "start_s": start_s, "rider": asdict(rider), "control": asdict(settings),
    }  # fmt: skip

    async def run() -> None:
        clock = Clock(speedup)
        ride_log = RideLog(log_path, header)
        session = RideSession(course, rider, GradePolicy(course, settings, rider), start_s=start_s)
        runner = RideRunner(session, clock, ride_log)
        if simulated:
            power = constant_power(mock_power) if mock_csv is None else power_from_csv(mock_csv)
            device = MockTrainer(
                power, clock.now, clock.sleep, runner.trainer_sample, runner.trainer_connection
            )
        else:
            device = BleTrainer(
                trainer, clock.now, runner.trainer_sample, runner.trainer_connection
            )
        hr_device = (
            BleHeartRate(hr, clock.now, runner.hr_sample, runner.hr_connection) if hr else None
        )

        def update(snap) -> None:
            # Switch to the fallback policy once the trainer reports no simulation mode.
            if not device.supports_simulation and not isinstance(session.policy, LevelPolicy):
                session.policy = LevelPolicy(course, settings, rider)
                console.print("[yellow]trainer has no simulation mode: using resistance levels[/]")
            live.update(_ride_table(snap, course.name))

        try:
            with Live(_ride_table(session.snapshot(), course.name), console=console) as live:
                snap = await runner.run(device, hr_device, on_update=update)
        finally:
            ride_log.close()
            console.print(f"ride log: {log_path}")
        _summary(snap, course)

    _run(run())


@app.command("replay")
def replay_cmd(
    log_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Ride log CSV.")],
    course_file: Annotated[
        Path | None, typer.Option("--course", help="Course, if not the one in the log.")
    ] = None,
) -> None:
    """Re-run the physics over the power recorded in a ride log."""
    from crt.control.policy import ControlSettings, GradePolicy
    from crt.physics.model import RiderParams
    from crt.session.log import read_log
    from crt.session.runner import replay
    from crt.session.session import RideSession

    header, samples, states = read_log(log_file)
    if not samples:
        console.print("the log has no trainer samples")
        raise typer.Exit(1)
    course = _load_course(course_file or Path(header["course"]))
    rider = RiderParams(**header["rider"])
    settings = ControlSettings(**header["control"])
    session = RideSession(
        course, rider, GradePolicy(course, settings, rider), start_s=header.get("start_s", 0.0)
    )
    snap = replay(session, samples, until=float(states[-1]["t"]) if states else None)
    _summary(snap, course)
    if states:
        logged = float(states[-1]["s_m"])
        console.print(
            f"  logged distance {logged / 1000:.3f} km, replayed {snap.s / 1000:.3f} km "
            f"(difference {snap.s - logged:+.1f} m)"
        )


if __name__ == "__main__":
    app()
