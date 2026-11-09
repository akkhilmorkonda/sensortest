# sensortest: sensor line test station

Line test software for a simulated piezo sensor (8 output channels) and its actuator, running on a preflashed Teensy (Eight Sleep take-home). It drives the actuator, captures all 8 channels, judges each one against data-derived limits, and gives the operator one clear PASS / FAIL / ERROR, with a self-contained record for every run.

![The station screen after a PROD run on the Teensy](docs/gui_screens/teensy_prod_result.png)

**Status:** the station runs end to end on the Teensy and on recorded data, with its GUI (operator and test modes). Next: packaging (a prebuilt binary and CI) and the report.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows; on Linux/macOS use .venv/bin/python

.venv/Scripts/python -m sensortest                  # the GUI on synthetic recordings (no hardware, no data)
.venv/Scripts/python -m sensortest --fixture teensy # the GUI on the Teensy (found by USB 16C0:0483)
.venv/Scripts/python -m sensortest --headless --serial SN0001 --runs 3 --fast   # headless PROD runs
```

The default fixture, `synthetic`, plays recordings generated from the device model measured in Part 1, so the station runs on any machine (D-060). `--fixture replay` plays the real DOE-1 recordings instead, which are bench data kept on the bench machine (see Data). `--fast` replays without real-time pacing; `--sequence ENG` runs the full engineering plan headless. In the GUI, enter a DUT serial and press Enter. **Test** mode (top left) adds the PROD/ENG picker and the engineering panels.

## Repository map

```
config/              what the station loads: settings.yaml, sensor_tests.yaml, TP-001_limits_v2.yaml
  archive/           earlier limit sets, kept as records (the station can't load them)
src/sensortest/      the station (one package per layer, see Architecture)
tests/               the pytest suite, including offscreen GUI tests and an in-memory fake Teensy
tools/               developer scripts (screenshots of the real app)
docs/                PRD, test plan TP-001, GUI spec, device protocol; gui_screens/; archive/
benchtop_testing/    Part 1 bench work: captures, analyses, limit derivations and their tools (evidence)
DESIGN.md            how I used AI tools on this project (the brief's AI usage note)
```

## Architecture

Ports and adapters (`docs/software_prd.md` §6). The dependency rule is enforced by `tests/test_architecture.py`, and only `ui/` and `app/` may import Qt.

| Package | What it does |
|---|---|
| `core/` | Pure domain: models, error codes, ports (interfaces), measurement methods. Imports nothing internal |
| `events/` | Commands and events, and the in-process event bus |
| `plans/` | The plan compiler: sensor test file + settings + limit set → a runnable `CompiledPlan` |
| `steps/` | The metrics (`@metric`), one function per measured quantity |
| `engine/` | The test executive: the state machine (`states.py`, a transition table), the capture loop, evaluation |
| `devices/` | The device manager and one folder per device: `teensy/` (the real one) and `replay/` (the recorded Teensy, for work without hardware) |
| `sinks/` | Result outputs (`@sink`): one CSV per run plus its raw data |
| `characterization/` | The limit derivation from bench recordings, with the same math the station runs |
| `ui/` | The PyQt6 station screen; buttons (`@action`) and test-mode panels (`@panel`) |
| `app/` | The composition root: settings, wiring, the GUI and headless entry points |

**Threads:** the engine runs on its own thread; each serial link has a reader thread; Qt stays on the UI thread, fed by `ui/bridge.py`. Live data reaches the screen through buffers drained by one timer (`ui.plot_fps`).

## Common tasks

| Task | How |
|---|---|
| Run the tests | `.venv/Scripts/python -m pytest`: no hardware and no bench data needed (the replay runs on synthetic recordings); add `--hardware` to also run PROD + ENG on the Teensy |
| Lint and format | `.venv/Scripts/ruff check .` and `.venv/Scripts/ruff format .` |
| Add a test step | A function with `@metric("key")` in `steps/builtin.py`; the metric and a step (ID, capture, family, operator `title`) in `config/sensor_tests.yaml`; its limit in the limit set; its ID in a plan list in `config/settings.yaml` |
| Add a device | A folder `devices/<name>/` with an `@actuator` and/or `@sensor` driver (and an `@link` only for a new kind of connection); one import in `app/wiring.py`; a fixture in `config/settings.yaml` naming each role's driver and link |
| Change the limits | `benchtop_testing/tools/derive_limits.py` writes a new limit set into `config/`; point `limits:` in `config/settings.yaml` at it. Never edit a generated limit set by hand |
| Inject a fault | Set `fault:` on a replay link in `config/settings.yaml` (`wrong_ack`, `no_reply`, `stall`, `drop_lines`, `lost`, `reject_baseline`) |
| Regenerate the screenshots | `.venv/Scripts/python tools/capture_screens.py` (see `tools/README.md`) |

## Configuration and outputs

- **`config/settings.yaml`:**
  - the station name and the DUT serial format;
  - the PROD and ENG plans (lists of step IDs);
  - the fixtures, each naming a driver and a link for the actuator and the sensor; `active_fixture` picks one;
  - the UI refresh rate.
- **Records:** every run (aborted ones too) writes `output/<date>/<SERIAL>_<run_id>.csv` and a `_raw.csv` with the measured windows. The app log goes to `output/logs/`. `output/` is not in git.

## Data

**No bench or manufacturing data is in git (D-059, D-060).** The repository holds code, configuration, documents and the bench scripts; the data stays where it was made:

- **Manufacturing data:** the station's records and logs in `output/`.
- **Bench data:** the raw captures, the gauge run's records, metric tables, reports and figures in `benchtop_testing/<experiment>/`, on the bench machine. `benchtop_testing/README.md` describes each folder. Put a copy back there to re-run an analysis, the limit derivation, or the `replay` fixture on the real DOE-1 recordings.
- **Synthetic recordings:** generated on demand from the device model (`devices/replay/synthetic.py`), in the bench format. The tests and the default fixture use them, so a fresh clone works without any data.

## Documents

| Document | What it covers |
|---|---|
| `docs/software_prd.md` | Requirements, architecture, state machines, timing, how to extend it |
| `docs/test_plan.md` | Test plan TP-001: the procedure, every step and method, verdict rules, error codes, the limits |
| `docs/gui_spec.md` | The station screen: regions, states, visual rules, widgets |
| `docs/device_protocol.md` | The Teensy's serial protocol, as measured |
| `DESIGN.md` | How I used AI tools: where and how, how the work was checked, and where AI-assisted work may need review |
| `benchtop_testing/README.md` | The bench work behind the limits: what was run, what it found |

**Decision IDs** such as D-056 in the code and documents refer to my decision log (each decision's reasoning and the options not chosen). It's kept outside this repository, with my other planning notes.
