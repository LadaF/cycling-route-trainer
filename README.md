# Cycling Route Trainer

Ride real routes on an FTMS smart trainer, with resistance from the terrain grade and
speed from your own physics model. See [docs/DESIGN.md](docs/DESIGN.md).

Status: phase 5. Rides work in the GUI and headless, on the Saris H3, with FIT
export to Garmin Connect.

## Setup

```sh
python3.12 -m venv .venv          # Windows: py -3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]' # Windows: .venv\Scripts\pip install -e .[dev]
.venv/bin/pytest
```

## Riding (GUI)

```sh
crt-gui                       # opens the last course
crt-gui ride.crt.npz          # or a GPX/TCX/FIT route: prepared once, kept as ride.crt.npz
crt-gui --mock-power 220 --speedup 10   # no hardware
crt-gui --no-web-map          # plain track instead of the OpenStreetMap map
```

Set the trainer (e.g. `Hammer 50414`), the heart rate sensor and your mass in
*Settings*. They are kept in `settings.toml` in the user config directory. *Start*
connects and the ride starts as soon as the trainer is connected; *Space* pauses and
resumes; *Stop* ends the ride. The FIT file is written next to the ride log.
*Easier* / *Harder* (Ctrl+[ / Ctrl+]) change the difficulty, the share of the grade
the trainer gets, by 10 %, also during a ride; the speed still follows the true grade.
The new grade is sent at once, the change is logged and kept in the settings.
*Courses…* (Ctrl+K) lists the courses opened before, with their details. There a
route can be imported, prepared again with another elevation source, or given its
bridges and tunnels: *Open the query in the browser* runs it in Overpass Turbo,
*Export → Data → raw OSM data* downloads or copies the answer, and *Load the saved
answer…* or *Paste the answer* uses it.
*Reverse* (Ctrl+R) switches to the course ridden the other way, kept next to it as
`ride.reverse.crt.npz`; reversing again goes back to `ride.crt.npz`.
The big values fill their tiles, so they grow with the window (maximize it, or drag
the dividers). *A+* / *A−* (Ctrl++ / Ctrl+−, Ctrl+0 for normal) make the other text
larger or smaller; the size is kept in the settings.

The window shows power (3 s average), cadence (5 s median, for display only; the log
and the FIT keep the sensor values), heart rate, simulated speed, terrain grade with
the grade sent to the trainer (marked *min* when the minimum grade holds it up, and
with the difficulty when below 100 %), distance, time and elevation. The profile shows the next 2 km coloured by grade. Map tiles come
from OpenStreetMap and are cached; without network a plain track is shown.

## Diagnostic tools

`TARGET` is a device address or a part of its advertised name (e.g. `H3`).

```sh
crt-tool scan                          # nearby FTMS / power / heart rate devices
crt-tool probe TARGET --json h3.json   # device info, FTMS features and ranges, GATT table
crt-tool monitor --trainer H3 --hr TARGET --csv ride.csv   # live data, rates, dropouts
crt-tool monitor --trainer H3 --raw    # every packet in hex (for test fixtures)
crt-tool control H3                    # interactive control point console
```

In `control`, start with `request`, then `start`, then for example `grade 5`,
`grade 0`, `erg 200` or `level 5`. `help` lists all commands. Each command prints
the trainer's response, for example `SET_INDOOR_BIKE_SIMULATION: SUCCESS`.

Only one app at a time can control a trainer. Close Zwift or phone apps first. On
Windows, do not pair the trainer in Windows Settings.

## Route preparation

```sh
crt-tool route ride.gpx --plot                 # writes ride.crt.npz next to the GPX
crt-tool route ride.gpx --plot-file ride.png --compare   # also plot Copernicus
crt-tool route ride.gpx --structures export.json   # bridges and tunnels, see below
crt-tool route ride.gpx --reverse              # the other way: ride.reverse.crt.npz
```

* **Elevation:** ČÚZK DMR 5G (lidar terrain, 2 m) inside Czechia, IGN RGE ALTI
  (terrain, 1–5 m) inside France, Copernicus GLO-30 elsewhere. Copernicus is a
  surface model: on forested roads it can show bumps of 10–30 m that are not there.
  `--source file` uses the elevations in the route file. Downloaded data is cached
  in the user cache directory (`~/.cache/crt` on Linux).
* **Bridges and tunnels:** taken from OpenStreetMap. The public Overpass server
  answers only browser tools when it is busy, which is nearly always, so the answer
  is fetched in the browser and the application does not query the server itself.
  In `crt-gui` this is done in *Courses…*; on the command line:
  ```sh
  crt-tool bridges-query ride.gpx    # prints the query and an Overpass Turbo link
  # open the link; when the answer is shown: Export → Data → raw OSM data or GeoJSON
  crt-tool route ride.gpx --structures export.json
  ```
  The answer is cached, so the route needs it only once. Without it the course is
  prepared without this correction and marked `not checked`.
  Opening a route in `crt-gui` or `crt-tool ride` keeps the prepared course next to
  it and reuses it while it is newer than the route.
* **Smoothing:** a 20 m Gaussian removes the steps that appear where the GPS track
  strays from the road onto a cutting or embankment.

Elevation data: © ČÚZK (CC BY 4.0); Copernicus DEM © DLR e.V., © Airbus Defence and
Space GmbH, provided under COPERNICUS by the European Union and ESA. Bridges and
tunnels: © OpenStreetMap contributors (ODbL).

## Riding (headless)

```sh
crt-tool ride ride.crt.npz --trainer "Hammer 50414" --hr fenix --min-grade 1
crt-tool ride ride.crt.npz --mock-power 200 --speedup 20   # no hardware
crt-tool ride ride.crt.npz --reverse --trainer "Hammer 50414"   # the other way
crt-tool replay ~/.local/share/crt/rides/2026-10-25_215225.csv
crt-tool export ~/.local/share/crt/rides/2026-10-25_215225.csv --format both
```

* The trainer gets the terrain grade 1 s ahead (`--lookahead`), scaled by
  `--difficulty`, never below `--min-grade`, at most twice a second.
* Speed comes only from the power and the physics (`--rider-mass`, `--bike-mass`,
  `--cda`, `--crr`, `--temperature`); `--max-speed` (default 60 km/h) acts as
  virtual braking.
* If the trainer disconnects, the ride pauses and resumes after it reconnects.
  Power older than 3 s counts as zero. Ctrl-C ends the ride.
* Every ride writes a log (user data directory, or `--log`): all samples, commands
  and the state at 1 Hz. `replay` re-runs the physics over the recorded power.
* After the ride a FIT file is written next to the log, as a virtual ride (Garmin
  Connect and Strava do not count it as outdoor). Upload it to Garmin Connect by
  hand. `export` writes the FIT or a GPX track from any ride log.

## Hardware checklist (phase 1)

1. `crt-tool scan` lists the H3 with `FTMS` and the strap with `HRS`.
2. `crt-tool probe H3 --json h3.json` shows `simulation mode (grade): yes`.
3. `crt-tool monitor --trainer H3 --hr <strap>` while pedalling. Power and cadence
   should update about 1–4 times per second, with no dropouts. HR should update at
   the same time over the second connection.
4. `crt-tool control H3`: run `request`, `start`, `grade 0`, `grade 8`, `grade 0`.
   The resistance should change noticeably within a couple of seconds.
5. Try `level 5` and `levelw 5` to see which resistance encoding the H3 accepts.
   The H3 reports a resistance range of 0–10. This only matters for the fallback
   mode.

## License

MIT, see [LICENSE](LICENSE). The GUI uses Qt through PySide6, which is under the LGPL v3.
