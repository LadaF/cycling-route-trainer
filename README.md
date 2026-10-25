# Cycling Route Trainer

Ride real routes on an FTMS smart trainer, with resistance from the terrain grade and
speed from your own physics model. See [docs/DESIGN.md](docs/DESIGN.md).

Status: phase 1. The BLE diagnostic tools (`crt-tool`) are implemented.

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
