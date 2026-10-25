# Cycling Route Trainer

Ride real routes on an FTMS smart trainer, with resistance from the terrain grade and
speed from your own physics model. See [docs/DESIGN.md](docs/DESIGN.md).

Status: phase 3. BLE diagnostic tools, route preparation and headless rides work.

## Setup

```sh
python3.12 -m venv .venv          # Windows: py -3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]' # Windows: .venv\Scripts\pip install -e .[dev]
.venv/bin/pytest
```

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
crt-tool route ride.gpx --no-bridges           # skip the OpenStreetMap bridge query
```

* **Elevation:** ČÚZK DMR 5G (lidar terrain, 2 m) inside Czechia, Copernicus GLO-30
  elsewhere. `--source file` uses the elevations in the route file. Downloaded data
  is cached in the user cache directory (`~/.cache/crt` on Linux).
* **Bridges and tunnels:** taken from OpenStreetMap through the public Overpass API,
  one attempt per route. If it fails, the course is prepared without this correction
  and the summary says `not checked`.
* **Smoothing:** a 20 m Gaussian removes the steps that appear where the GPS track
  strays from the road onto a cutting or embankment.

Elevation data: © ČÚZK (CC BY 4.0); Copernicus DEM © DLR e.V., © Airbus Defence and
Space GmbH, provided under COPERNICUS by the European Union and ESA. Bridges and
tunnels: © OpenStreetMap contributors (ODbL).

## Riding (headless)

```sh
crt-tool ride ride.crt.npz --trainer "Hammer 50414" --hr fenix --min-grade 1
crt-tool ride ride.crt.npz --mock-power 200 --speedup 20   # no hardware
crt-tool replay ~/.local/share/crt/rides/2026-10-25_215225.csv
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
