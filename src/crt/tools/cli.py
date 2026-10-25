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
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )


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


if __name__ == "__main__":
    app()
