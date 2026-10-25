# Cycling Route Trainer — Design Plan

Status: v0.3, agreed (2026-10-25)

Target setup: Saris H3 (Hammer) smart trainer over BLE FTMS, a separate BLE heart
rate strap, Linux and Windows. Rides are exported as FIT and uploaded to Garmin Connect, which
forwards them to Strava. GPX is the fallback format.

## 1. Goal

A desktop GUI application that lets a rider "ride" a recorded route (GPX/TCX/FIT)
on a smart trainer:

* The route is loaded and its elevation profile is rebuilt from a high-quality DEM
  (ČÚZK DMR 5G in Czechia, Copernicus GLO-30 / SRTM elsewhere).
* The app reads **power** (plus cadence and trainer speed) from the trainer, and
  **heart rate** from a separate BLE HR sensor.
* The app integrates the **equations of motion** along the route from that power
  using the rider's own parameters (masses, CdA, Crr, …). The trainer's own speed is
  logged but never used.
* The app sets the trainer's resistance from the current terrain grade over the
  open **FTMS** protocol (Bluetooth LE Fitness Machine Service). Resistance never
  goes below a user-set **minimum**, even on flat ground or downhill.
* Command-line utilities to test the connection and the device's capabilities.

Non-goals for v1: video or 3D rendering, multiplayer, structured ERG workouts, and
iOS or Android support.

## 2. Key design decisions

| Decision | Choice | Reason |
|---|---|---|
| Language | Python ≥ 3.11, in a dedicated virtual environment | All the work is I/O-bound or small-array numerics; no compiled parts are needed (see §11). 3.11 has everything required (`tomllib`, `asyncio.TaskGroup`) |
| Concurrency | One `asyncio` event loop, shared with Qt through `qasync` | `bleak` is asyncio-native; avoids threads and locks |
| Trainer protocol | FTMS over BLE (primary); ANT+ FE-C (optional, later) | FTMS is the open Bluetooth SIG standard and is supported by all current smart trainers |
| Resistance control | FTMS *Indoor Bike Simulation Parameters* (grade), clamped at a minimum | The trainer handles flywheel and speed dynamics itself; we only shape the grade |
| Physics input | Trainer-reported power only | As required; speed comes from our own model |
| Route preparation | Done offline, before the ride, and cached as a "processed course" file | No network access or heavy computation while riding |
| GUI | PySide6 + pyqtgraph (+ QtWebEngine/Leaflet for the map) | Mature, LGPL, fast live plotting |
| Core vs GUI | The core engine is GUI-agnostic and can run headless | Testable; the CLI ride mode reuses it |

## 3. Architecture

```
                         ┌─────────────────────────── GUI (PySide6) ───────────────────────────┐
                         │ Route/Profile view │ Map │ Live metrics │ Settings │ Device dialog  │
                         └───────────▲──────────────────────────────────────────┬──────────────┘
                                     │ state snapshots (Qt signals, ~10 Hz)     │ commands
                         ┌───────────┴──────────────────────────────────────────▼──────────────┐
                         │                       RideSession (orchestrator)                    │
                         │  state machine: IDLE → CONNECTING → READY → RIDING ⇄ PAUSED → DONE  │
                         └──┬──────────────┬───────────────────┬───────────────────┬───────────┘
                            │              │                   │                   │
               ┌────────────▼───┐  ┌───────▼────────┐  ┌───────▼─────────┐  ┌──────▼──────┐
               │ Trainer backend│  │ PhysicsEngine  │  │ ResistanceCtrl  │  │  Recorder   │
               │ (FTMS / mock / │  │ 20 Hz integr.  │  │ 1–2 Hz, grade → │  │ FIT/TCX +   │
               │  ANT+ FE-C)    │  │ s, v, z, …     │  │ clamp, rate lim │  │ raw log     │
               └──────┬─────────┘  └───────▲────────┘  └───────▲─────────┘  └─────────────┘
                      │ power, cadence,     │ grade(s), ρ(z)    │
                      │ speed, HR           │                   │
                      ▼               ┌─────┴───────────────────┴────┐
                   BLE (bleak)        │  Course (processed route)    │◄── Route pipeline (offline):
                                      │  s, lat, lon, x, y, z, grade │    parse → project → resample
                                      └──────────────────────────────┘    → DEM sample → fix bridges
                                                                          → smooth → grade
```

Data flows one way. The trainer backend publishes samples. The physics engine uses
the latest power sample to advance the position *s*. The resistance controller reads
the course at *s* and sends commands. The GUI and recorder only observe.

## 4. Package layout

```
cycling-route-trainer/
  pyproject.toml
  src/crt/
    config/        settings model, TOML profiles (rider, bike, devices, app)
    route/         parsers (GPX/TCX/FIT/GeoJSON), resampling, Course model, course file I/O
    elevation/     DEM providers, tile cache, bridge/tunnel fix, smoothing
    physics/       parameters, force model, integrator, air density
    devices/
      base.py      Trainer protocol (abstract interface) + sample dataclasses
      ftms/        codec (pure bytes ↔ dataclasses), BLE client
      cps.py       Cycling Power Service (read-only power source)
      hrs.py       Heart Rate Service
      mock.py      simulated trainer (scripted or keyboard-driven power)
      replay.py    replays a raw log as if it were a device
      antfec.py    (later) ANT+ FE-C via openant
    control/       ResistancePolicy implementations, lookahead, rate limiting
    session/       RideSession, state machine, event bus
    record/        FIT/TCX writer, raw Parquet/CSV log
    gui/           Qt windows, widgets, plots, map
    tools/         CLI utilities (§9)
  tests/
    fixtures/      captured FTMS byte packets, small GPX files, DEM tiles
```

## 5. Route and elevation pipeline (offline)

1. **Parse** with `gpxpy` (GPX), `lxml` (TCX), `fitdecode` (FIT courses), and the
   standard `json` module (GeoJSON). Take only the geometry; ignore the file's own
   elevations (they can be kept for comparison).
2. **Project** to a metric CRS with `pyproj`: S-JTSK/Krovak East North (EPSG:5514)
   for Czech data, which is ČÚZK's native CRS, otherwise the local UTM zone.
3. **Clean and resample** to a uniform spacing (default 2–5 m) along the distance
   *s*. Remove GPS spikes, duplicate points and zero-length segments. Compute
   bearing and curvature.
4. **Sample the DEM** with bilinear interpolation, through an `ElevationProvider`
   interface:
   * `CuzkDmr5g`: ČÚZK DMR 5G (terrain model from airborne lidar, decimetre accuracy).
     The data has been open (CC BY 4.0) since 2023. It can be obtained through
     ČÚZK download (ATOM) and web services such as the ArcGIS ImageServer. The exact
     endpoints must be verified when this module is implemented. Tiles are cached
     locally.
   * `CuzkDmr4g` (5 m raster grid): a simpler fallback.
   * ZABAGED altimetry (contours and spot heights) is coarser than DMR 5G. It is only
     worth adding as an independent cross-check.
   * `CopernicusGlo30`: Copernicus GLO-30 Cloud Optimized GeoTIFFs (COGs) on AWS Open
     Data. Global coverage, generally better than SRTM.
   * `Srtm`: SRTM 1″ as a last resort.
   * The design is open to other national lidar DEMs (Slovak ZBGIS DMR 5.0, Austrian
     and German state DGM1).
   * Raster reading uses `rasterio`, with windowed reads from COGs. The cache lives in
     `platformdirs.user_cache_dir("crt")`.
5. **Fix bridges and tunnels.** A *terrain* model (DMR) has no bridges, so a viaduct
   reads as a dive into the valley and a tunnel as a climb over the hill. Fix:
   * Fetch the OSM ways `bridge=*` and `tunnel=*` along the route (Overpass API,
     with `httpx`), or use the corresponding ZABAGED road objects.
   * Map them to intervals of *s* and replace the elevation inside each interval with
     a linear interpolation between its end points. Allow manual intervals in the
     course editor as a fallback.
6. **Smooth** the profile. Use a distance-based filter (Savitzky–Golay or Gaussian
   from `scipy.signal`, about 20–50 m window) and an optional cap on the maximum
   grade. Compute the grade as `dz/ds` from the smoothed profile.
7. **Save a processed course** (`.crt.npz` or Parquet, plus a JSON metadata block
   recording the source, DEM provider, smoothing parameters and bridge intervals).
   Riding only ever loads this file.

`Course` API, vectorised with `numpy`:
`course.at(s) -> (lat, lon, z, grade, bearing)` and
`course.grade_window(s0, s1) -> mean grade`. Both use
`np.searchsorted`/`np.interp` on the uniform grid.

## 6. Physics model

State: distance along the route *s* [m] and speed *v* [m/s]. Integrate the kinetic
energy, which avoids the `P/v` singularity at a standstill:

```
E = ½ m_eff v²
dE/dt = η·P  −  v · [ m g sinθ  +  Crr m g cosθ  +  ½ ρ(z) CdA (v − w_∥)|v − w_∥|  +  F_bearing ]
ds/dt = v
```

* `m = m_rider + m_bike`, `m_eff = m + I_wheels / r²` (rotational inertia, ≈ +1–1.5 kg).
* `θ = atan(grade)` from the **true** (unclamped) course grade.
* `η` is the drivetrain efficiency (≈ 0.97–0.98). It applies only if the trainer
  reports power at the crank. Trainers measure at the flywheel or hub, so the
  default is η = 1, configurable per device.
* `ρ(z)` is the air density from the barometric formula with a user-set temperature
  and sea-level pressure.
* `w_∥` is the wind component along the track bearing (optional constant wind).
* **Maximum speed (v1).** A configurable cap `v_max` (default 60 km/h) acts as
  virtual braking. In the energy formulation it is simply a clamp,
  `E ← min(E, ½ m_eff v_max²)`. The energy removed is logged as braking.
  The cap only changes the simulated speed, not the resistance sent to the trainer.
* Optional extras for later:
  * Crr per surface type (from the OSM `surface` tag).
  * Speed limit in curves, `v ≤ √(a_lat,max / κ)`, using the curvature κ that the
    route pipeline already computes. It plugs in as a speed cap that varies along
    *s*, so the clamp above already provides the mechanism.
* Integration: fixed-step RK4 or semi-implicit Euler at 20 Hz, driven by
  `time.monotonic()`, so GUI jitter does not change the results. Power is held
  constant between trainer notifications (zero-order hold). If no power sample has
  arrived for more than 3 s, P = 0.
* Default parameters: rider 75 kg, bike 9 kg, CdA 0.32 m², Crr 0.004,
  T = 20 °C, no wind.

The engine is a pure function `step(state, P, dt, course, params) -> state`, so it can
be unit-tested against analytic steady-state speeds and against a reference
`scipy.integrate.solve_ivp` solution.

## 7. Resistance control and the minimum-resistance setting

FTMS control point (characteristic 0x2AD9) sequence:

1. Request Control (0x00).
2. Start or Resume (0x07).
3. Repeatedly send Set Indoor Bike Simulation Parameters (0x11). Its fields are:
   * wind speed (sint16, 0.001 m/s)
   * grade (sint16, 0.01 %)
   * Crr (uint8, 0.0001)
   * Cw = ½ρCdA (uint8, 0.01 kg/m)

Every write is answered by an indication (0x80, opcode, result code). Check it and
surface any errors.

`ResistancePolicy` interface with these implementations:

* **`SimulationGradePolicy`** (default), applied in this order:
  1. *Lookahead*: `g_look = course.grade_window(s, s + v·t_lag)`. The value of
     `t_lag` (≈ 1–2 s) compensates for the time the trainer's brake takes to respond.
  2. *Difficulty scaling* (optional, as in other apps): `g = k · g_look`, with k ∈ [0, 1].
  3. **Minimum**: `g_cmd = max(g, g_min)`. The default `g_min` is 0 %. Setting it to,
     for example, +1.5 % keeps a firm feel on flats and descents.
  4. Clamp to the trainer's supported range (read from the device; FTMS typically
     allows −40 … +40 %, but real limits are often around −10 … +20 %).
  5. *Rate limiting*: send at most 2 Hz, and only if the change is ≥ 0.1 % or 5 s
     have passed since the last send (keep-alive).
  6. Send Crr and Cw from the rider profile so the trainer's own flat-road feel
     matches.
  The minimum is set in the GUI as a grade in %. This is the policy used with the
  Saris H3. Before relying on it, confirm with `crt-tool probe` that the H3's
  firmware reports the simulation feature bit, and with `steptest` how quickly its
  brake responds. On the H3 the sensible lookahead is probably around 1–2 s.
* **`ResistanceLevelPolicy`**: a fallback, used automatically if the trainer does
  not report simulation support. The minimum then becomes a minimum resistance
  level.
  `level = a + b·g`, clamped to `[level_min, level_max]`. The coefficients are
  calibrated with the step-test utility (§9). Note that the parameter's size changed
  between FTMS spec revisions (uint8 vs sint16, 0.1 resolution); check this against
  the actual device.

The minimum affects **only the command sent to the trainer**. The physics always uses
the true grade, so on a descent the rider pushes against the minimum resistance while
the virtual speed still rises under gravity. The GUI shows both the terrain grade
and the commanded grade.

## 8. Devices layer

```python
class Trainer(Protocol):
    async def connect(self) -> DeviceInfo: ...
    async def disconnect(self) -> None: ...
    def samples(self) -> AsyncIterator[TrainerSample]   # t, power, cadence, speed, (hr)
    capabilities: TrainerCapabilities                   # decoded FTMS feature bits and ranges
    async def set_simulation(self, grade, crr, cw, wind) -> None: ...
    async def set_resistance_level(self, level) -> None: ...
    async def set_target_power(self, watts) -> None: ...  # used by test tools only
```

* **FTMS codec.** A pure-Python, fully unit-tested module that turns bytes into
  dataclasses and back. It parses Indoor Bike Data (0x2AD2) with its variable flag
  layout; note that bit 0 is "More Data" and is **inverted** with respect to the
  speed field. It also handles Fitness Machine Feature (0x2ACC), the supported range
  characteristics (0x2AD5/0x2AD6/0x2AD8), Machine Status (0x2ADA) and control point
  encoding. `pycycling` is a useful reference; a codec of our own (~300 lines) gives
  full control over error handling.
* **BLE client** with `bleak` (BlueZ on Linux). Reconnects automatically with
  backoff. While the trainer is disconnected the session auto-pauses, so physics keeps
  running with P = 0. After reconnecting, the client requests control again.
* **Heart rate sensor (v1).** A second, independent BLE connection to the Heart Rate
  Service (0x180D) of the HR strap. It has its own reconnect loop. HR is only
  displayed and recorded; losing it never pauses the ride. The HR sample stream is
  merged with the trainer samples in the session by timestamp.
* **Also usable:** the Cycling Power Service (0x1818) as a power-only source, for
  example a power meter driving the physics while the trainer is controlled
  separately.
* **Mock trainer.** Power comes from a script (constant, steps, a CSV file) or from
  the keyboard in the GUI. It also emulates a first-order brake response to the
  commands it receives. This makes the whole application usable without hardware.
* **Later:**
  * ANT+ FE-C through `openant` with a USB ANT stick.
  * A virtual BLE FTMS *peripheral* (`bless`) for end-to-end tests over real radio
    from a second machine.

## 9. Test and diagnostic utilities

All utilities are subcommands of `crt-tool` (built with `typer` and `rich`) and share
the device layer with the app.

| Command | Purpose |
|---|---|
| `crt-tool scan` | List BLE devices advertising FTMS / CPS / HRS, with RSSI and address |
| `crt-tool probe ADDR` | Connect and dump all services and characteristics. Decode Device Information (manufacturer, model, firmware), FTMS feature bits (simulation, resistance and power targets) and the supported grade, resistance and power ranges. Save as JSON |
| `crt-tool monitor ADDR` | Live decoded table of power, cadence, speed and HR. Measure notification rate, jitter and dropouts. Optional CSV/Parquet logging |
| `crt-tool control ADDR` | Interactive REPL: `request`, `start`, `grade 5`, `level 30`, `erg 200`, `reset`. Prints each control-point response with its result code |
| `crt-tool steptest ADDR` | Scripted sweep (grade −5 → +15 % in steps, or resistance levels) while the rider holds a constant cadence. Logs power, speed and response time. Used to calibrate `ResistanceLevelPolicy` and to measure `t_lag` |
| `crt-tool route FILE` | Run the route pipeline. Plot the profile and grade (matplotlib), compare DEM sources against each other and against the GPX elevations, list bridge and tunnel fixes, write the processed course |
| `crt-tool ride COURSE --device ADDR\|mock` | Headless ride in the terminal: the full session without the GUI |
| `crt-tool replay LOG` | Feed a recorded raw log through the session, for regression tests and debugging |

Linux notes:
* BlueZ ≥ 5.6x is required, and the user must have permission to use Bluetooth.
* A trainer accepts only one controlling connection at a time; close Zwift or other
  phone apps first.
* `btmon` and nRF Connect are useful for low-level debugging.

## 10. GUI

PySide6 with a `qasync` event loop. Main window:

* **Top bar:**
  * course name
  * device status (connected, RSSI, control granted)
  * buttons: Start / Pause / Stop
* **Live metrics panel:**
  * power (instant and 3 s average), cadence, HR
  * **simulated** speed (trainer speed in small print, for reference)
  * terrain grade vs **commanded** grade (the minimum clamp is highlighted when it
    is active)
  * distance done and remaining, elapsed time, current elevation, elevation gain
* **Elevation profile** (`pyqtgraph`): the whole route with a position marker, plus a
  zoomed panel of the next 1–2 km coloured by grade.
* **Map:** `QWebEngineView` with Leaflet and OSM tiles, showing the route polyline and
  current position. Fallback without a network: a plain x/y track in pyqtgraph.
* **Settings dialogs:**
  * rider/bike profiles (masses, CdA, Crr, η, wheel inertia)
  * environment (temperature, wind)
  * control (minimum grade, difficulty, lookahead, policy)
  * elevation sources
  * device pairing (reuses `scan`/`probe`)
* **Course manager:** import a route, choose the DEM, preview and adjust the
  smoothing, edit bridge and tunnel intervals, save the processed course.
* **Post-ride:** summary, then export.

GUI updates are throttled to about 10 Hz from session snapshots. The GUI never calls
device code directly; it sends commands to the `RideSession`.

## 11. Performance and compiled code

The physics step is about 20 floating-point operations at 20 Hz, and course lookups
are O(log n). BLE traffic is a few packets per second. Plain Python with `numpy` has a
very large margin here, so **no compiled parts are planned**. If DEM processing of
very long routes or point clouds ever becomes slow, the candidates are vectorised
`numpy`/`rasterio` first, then `numba`.

## 12. Recording and export

* **FIT (primary),** written with `fit-tool`:
  * Messages: `file_id` (type activity), `event` start/stop and pause timer events,
    1 Hz `record` messages, `lap`, `session`, `activity`.
  * Each `record` holds position, elevation, *simulated* speed and distance,
    power, cadence and heart rate.
  * `sport = cycling`, `sub_sport = virtual_activity`, so Garmin Connect and Strava
    show it as a virtual ride and do not count it as an outdoor ride.
  * Pauses are written as timer stop/start events, so moving time is correct.
  * Every generated file is checked in tests by decoding it back with `fitdecode`
    (and, manually, with Garmin's FIT SDK `FitCSVTool`).
* **GPX (fallback),** written with `gpxpy`: track points with elevation and time,
  heart rate and cadence in the Garmin `TrackPointExtension`, and power in a
  `<power>` extension, which Strava reads.
* **Upload:** v1 only writes the files; you upload the FIT to Garmin Connect, which
  syncs to Strava. Possible later automation:
  * Strava has an official upload API (OAuth).
  * Garmin Connect has no public upload API for individuals; only unofficial
    libraries (`garminconnect`, `garth`) exist, so this stays optional.
* **Raw log:** every trainer sample, every command sent with its response, and the
  physics state, all with monotonic timestamps. Written as Parquet (`pyarrow`) or
  CSV, and usable as input for `replay`.
* **Crash safety:** files are written incrementally, so a crash leaves a usable file.

## 13. Configuration

TOML files in `platformdirs.user_config_dir("crt")`, read with `tomllib` and written
with `tomli-w`, validated with `pydantic`:

* `rider.toml` / `bikes/*.toml`
* `devices.toml` (last-used addresses, per-device η and calibration)
* `app.toml` (DEM preferences, cache paths, units)

## 14. Libraries

| Area | Libraries |
|---|---|
| BLE | `bleak`; optional `bless` (virtual peripheral), `openant` (ANT+) |
| GUI | `PySide6` (incl. QtWebEngine), `qasync`, `pyqtgraph` |
| Numerics | `numpy`, `scipy` |
| Geo | `pyproj`, `rasterio` (GDAL), `shapely` (bridge interval matching), `httpx` |
| Route formats | `gpxpy`, `fitdecode`, `lxml`; `fit-tool` for writing FIT, `gpxpy` for writing GPX |
| Data / config | `pydantic`, `tomli-w`, `pyarrow`, `platformdirs` |
| CLI | `typer`, `rich`, `matplotlib` (route plots) |
| Dev | `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `hypothesis` (codec fuzzing) |

Environment: a dedicated virtual environment for this project, Python 3.11 or 3.12.
Reusing the existing `python311-ml` environment is possible, but the GUI and geo
stacks (PySide6, GDAL/rasterio) bring large binary dependencies that can clash with
ML packages, so a separate environment is safer. `rasterio` wheels include GDAL, so no
system GDAL is needed.

## 15. Testing strategy

* **Unit tests:**
  * FTMS codec against byte fixtures captured from real trainers with
    `monitor --raw`, plus round-trip property tests.
  * Physics against analytic steady-state speeds. On the flat, solve
    `P = v(Crr m g + ½ρCdA v²)`; on a climb, check that the time to cover a known
    distance at constant power matches.
  * Policy clamping, rate limiting and lookahead.
  * Route resampling and bridge interpolation.
* **Integration tests:** a full `RideSession` with the mock trainer on a synthetic
  course in accelerated simulated time.
* **Hardware tests (manual):** follow the checklist built on the §9 tools — scan,
  probe, monitor, control, step test, then a short real ride.

## 16. Implementation phases

1. **Foundation and connection tools.** Project skeleton, FTMS and HRS codecs, BLE
   client, `scan`/`probe`/`monitor`/`control`. *This validates the Saris H3 and the
   HR strap early, including both connected at the same time.*
2. **Route pipeline.** Parsers, resampling, Copernicus/SRTM provider, ČÚZK DMR 5G
   provider, bridge/tunnel fix, smoothing, `crt-tool route`.
3. **Physics and session.** Engine, policies, mock trainer, headless `crt-tool ride`,
   raw log, `replay`.
4. **Real-trainer ride.** Headless ride on hardware, `steptest`, tuning of `t_lag` and
   the minimum grade.
5. **GUI.** Main ride window, then settings, course manager and device pairing.
6. **Export and polish.** FIT export with a test upload to Garmin Connect, GPX
   fallback, profiles, reconnect handling, packaging.
7. **Optional extras.** Curve braking, Strava API upload, ANT+ FE-C,
   surface-dependent Crr, virtual BLE peripheral, CPS power meter.

## 17. Decisions and open questions

Decided:
* Trainer: Saris H3 over FTMS.
* Minimum resistance: a minimum grade in %, falling back to a resistance level only
  if the trainer lacks simulation mode.
* Speed: a constant maximum speed in v1; braking in curves later.
* Export: FIT (virtual ride) to Garmin Connect, which syncs to Strava; GPX fallback.
  HR from a separate BLE strap is recorded.

* Downhill grades are passed through when they are above the minimum (rare in
  practice, since `g_min` will usually be ≥ 0).
* Platforms: Linux first, Windows 10/11 supported (§18). macOS is not targeted but
  should mostly work, because the same libraries support it.

## 18. Windows support

The chosen libraries are all cross-platform, so Windows needs care in only a few
places:

* **BLE:** `bleak` uses the WinRT backend on Windows 10 and later. FTMS and HR
  devices connect without pairing in Windows Settings. Pairing them there can cause
  conflicts, so the tools tell the user not to.
* **Qt with bleak on Windows:** Qt initialises COM on the main thread as
  single-threaded (STA), but WinRT Bluetooth callbacks need multi-threaded COM, so
  without a fix notifications never arrive. Bleak provides
  `bleak.backends.winrt.util.allow_sta()` (or `uninitialize_sta()`) for this. Call it
  at GUI start-up and cover it with a smoke test on Windows.
* **Event loop:** `qasync` works with Qt on Windows; the headless CLI tools use the
  default Proactor event loop.
* **Paths:** use only `pathlib` and `platformdirs`; never hard-code paths or
  separators.
* **Geo stack:** the `rasterio` and `pyproj` wheels include GDAL and PROJ on Windows.
* **Packaging:** PyInstaller one-folder builds for Windows (and optionally an AppImage
  for Linux). QtWebEngine makes the bundle large (~200 MB), which is acceptable.
* **CI:** GitHub Actions running the unit tests on `ubuntu-latest` and
  `windows-latest`. BLE hardware tests remain manual on both systems.
