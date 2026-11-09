# Benchtop testing (Part 1: understanding the device)

Everything here is **exploration and characterization**, not the product. It's kept because it is the evidence behind the protocol facts (D-007–D-010), the test plan (TP-001) and the limits (L-v0). The product lives in `src/` (the app), `config/` (its input files) and `docs/`.

**Only the scripts (`tools/`) and the READMEs are in git (D-059).** The data and results (raw captures, the gauge run's records, metric tables, reports, figures) stay on the bench machine, in the folders this README describes. Put a copy of the bench data back in these folders to re-run the analyses, the limit derivation or the replay fixture on recorded data.

**Rule:** nothing in `src/` imports from here, and no test reads it (D-060: the tests run on synthetic recordings). The station's code once reproduced `04_limits_v0_derivation/` exactly in golden tests (D-019 risk 1, D-050); they were removed when the data left the repository.

## Experiments, in order

| Folder | What it is | Roadmap | Key findings | Written up in |
|---|---|---|---|---|
| `01_bringup/` | 15 raw captures from first contact: passive stream, baseline, `response:10`, on/off, zero-padding, direct switching, line-ending tests (LF, CRLF, CR, none) | 02 Hardware bring-up | 150 Hz (not 100); 1–19 Hz valid (not 25); LF only; state survives reconnect | `docs/device_protocol.md`; Obsidian *02 Hardware Bring-up*; D-007–D-010 |
| `02_sweep_1-25hz/` | 25 captures, one per frequency 1–25 Hz (10 s baseline, then 10 s tone), plus `analysis/` (transition plots, summary, spectra) | 04 pre-work | Signal model; per-channel faults (ch1 weak, ch4 noisy, ch5 offset, ch8 at 2f); 20–25 Hz replay the last valid frequency | Obsidian *04 Sensor Characterization* (pre-work A–D) |
| `03_doe1_repeatability/` | DOE-1: 10 sweeps × (19 frequencies + control) = 200 runs of 30 s (`sweepNN/`, `precheck/`, `manifest.csv`, `run_info.json` with seed 1794018417, `console.log`), plus `analysis/` (report + 6 charts) | 04 | Noise-limited, flat over 1–19 Hz, no drift or carryover, instant recovery; two-part jitter (1.44% amplitude + 86 µs timing) | Obsidian *04*, DOE-1 §6–7; D-012 |
| `04_limits_v0_derivation/` | DOE-1 re-cut to TP-001's timing (`recut_metrics.csv`) and the derivation report | 04 (limits) | L-v0; 190/190 simulated PROD runs give the expected verdicts; D-030 frequency gate | `docs/test_plan.md` §16; D-019, D-030 |
| `05_gauge_run_l_v1/` | Gauge run before freezing L-v1: 20 PROD + 20 ENG on the Teensy through the station's own runner (`records/`, `console.log`, `settings_gauge.yaml`, `report.md`), plus `phase_jump.png` | 03 (L-v1) | Zero ERRORs, zero false fails. Drift check failed as run because of one run in which the actuator's tone jumped +118° mid-window (counter wrap, ~every 71.6 min); passed with that run excluded | D-051; `docs/test_plan.md` §16 |
| `06_limits_v1_derivation/` | L-v1 generated with `derive_limits.py`: metric table and derivation report | 03 (L-v1) | L-v1 = L-v0's values; same PROD verdicts over 190 runs | D-051; `docs/test_plan.md` §16 |
| `07_gui_line_rate/` | Line rate with the station screen drawing vs headless: 5 PROD runs each on the Teensy (`results.txt`, `README.md`) | 07–09 (GUI) | Antialiased drawing biased the host-timed line rate by up to -0.27% (BL 149.58-149.76 Hz); with antialiasing off the GUI reads 149.98-150.01 Hz, like headless (150.01-150.02). Plots now draw without antialiasing | D-054 |
| `08_limits_v2_derivation/` | L-v2 generated with `derive_limits.py` after D-056 (no phase; amplitude at the response frequency): metric table and derivation report | 03/04 (L-v2) | L-v2 = L-v1 without the phase steps; amplitude high 1128.3 → 1128.5 (the 1 Hz runs, 2 cycles per window, read +1.9 a.u. at the measured frequency). Same PROD verdicts and primary failures over 190 runs; ch8 fails 6050 only | D-056; `docs/test_plan.md` §16 |

Capture file format: each capture is three files with the same stem. `.bin` holds the raw bytes, `.tsv` holds host time plus each line's repr, and `.json` holds the metadata, including every command sent with its timestamp.

## Tools (`tools/`)

The scripts share helpers, so they live together. Default paths are resolved from the script's own location, so they run from any folder. Use the project `.venv` for the capture tools and global Python (it has matplotlib/scipy) for the analysis tools.

| Script | Purpose | Produces |
|---|---|---|
| `serial_terminal.py` | Interactive terminal for the Teensy (auto-detect, reply classification, `baseline` on exit) | — |
| `bringup_capture.py` | Timed raw capture with scripted commands. **Refuses 134 baud** | `01_bringup/` (default) |
| `bringup_inspect.py`, `bringup_segments.py`, `bringup_timeline.py` | Summaries of one capture: line rate, per-channel stats, per-command segments, sliding state | stdout |
| `analyze_sweep.py` | Sweep plots and summary | `02_sweep_1-25hz/analysis/` |
| `doe_run.py` | The DOE-1 runner: shuffled order, per-run checks, retries, stop rules, manifest | `03_doe1_repeatability/` |
| `analyze_doe.py` | DOE-1 metrics, hypothesis tests H1–H8, charts | `03_doe1_repeatability/analysis/` |
| `derive_limits_v0.py` | Re-cut DOE-1 to TP-001 timing, derive L-v0, simulate 190 PROD runs. **Kept unchanged as the record of how L-v0 was made** (exploration math); its outputs are the golden files for `tests/test_golden_lv0.py` (D-050). Don't re-run it over them | `04_limits_v0_derivation/` and **`config/TP-001_limits_v0.yaml`** |
| `derive_limits.py` | The same derivation through the product's `sensortest.characterization` (the station's timing, metrics, rule and evaluation, D-050). Generated L-v1 (D-051) and L-v2 (D-056; regenerated for D-057 without the E207 constant). Refuses to overwrite | `--out` folder (metric table, report) and `--limits-file` |
| `gauge_run.py` | `run`: 20 PROD + 20 ENG on the Teensy via the headless runner; `analyze`: the three gauge rules (errors, false fails, drift vs DOE-1). Runs listed in `EXCLUDED` (with their reason) are left out of the drift rule only, and the report shows both results. Refuses to re-run over existing records | `05_gauge_run_l_v1/` |
| `phase_jump.py` | Finds and measures the actuator's tone phase jump in the gauge records (vs the counter-wrap prediction), re-scans DOE-1, draws the figure. Global Python | `05_gauge_run_l_v1/phase_jump.png` (Evidence: the station has assumed the jump away since D-058.) |
| `gui_line_rate.py` | N PROD runs on the Teensy through the real app, with the GUI drawing (optionally antialiased) or headless; prints each capture's line rate | `07_gui_line_rate/` |

Re-run examples (from the repo root):

```bash
python benchtop_testing/tools/analyze_doe.py
.venv/Scripts/python benchtop_testing/tools/derive_limits.py --limit-set L-v0 --status check --out output/lv0_check --limits-file output/lv0_check/limits.yaml  # product-backed check; output/ is gitignored
.venv/Scripts/python benchtop_testing/tools/doe_run.py --dry-run
.venv/Scripts/python benchtop_testing/tools/gauge_run.py analyze   # re-check the gauge rules from the saved records
python benchtop_testing/tools/phase_jump.py                       # re-measure the phase jump, redraw the figure
```

**Device safety:** only `serial_terminal.py`, `bringup_capture.py` and `doe_run.py` touch the device. They never use 134 baud, send only `baseline` and `response:1`–`19`, and leave the device in `baseline`.
