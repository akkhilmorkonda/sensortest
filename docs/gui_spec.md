# GUI specification (sections 07–09)

| | |
|---|---|
| **Document** | GUI spec for the sensor line test station |
| **Version** | v0.2, 2026-11-08: **built** (D-054). v0.1 was the agreed draft (D-053) |
| **Decisions** | D-053 (this spec), D-054 (the build), D-055 (the FAIL banner names channels, not reasons), D-002 (reversed: one DUT with 8 channels), D-003, D-035, D-037–D-040, D-047 (my decision log, kept outside the repository) |
| **Mockups** (archived) | `docs/archive/gui_mockups/operator_layouts.html`: the three HTML layouts; **A** was chosen, and B and C are kept for the record. **PyQt render of A:** `docs/archive/gui_mockups/operator_qt_mockup.py` builds the screen with the real widgets from a real PROD record and writes `qt_operator_{run,fail,err,zoom}.png` (approved with no changes; kept as the record) |
| **Screens of the built app** | `docs/gui_screens/`, made by `tools/capture_screens.py` from the real app: `teensy_prod_*` (PROD on the Teensy), `replay_prod_drop_lines_*` (an ERROR: E205, retryable once; the E211 screens went with D-058), `replay_eng_*` (test mode, ENG) |
| **Code** | `src/sensortest/ui/`: `main_window.py`, `step_table.py`, `live_plot.py`, `run_buffer.py`, `widgets.py`, `test_mode_panels.py`, `theme.py`, `actions.py` |
| **Built with** | PyQt6 + pyqtgraph (D-001). Every element below names the Qt widget it maps to (§8) |

**Who decided what.** The regions, the step-table layout, the plot behaviour, the terminal content and test mode's scope are the user's choices (D-053). The user approved the PyQt render with no changes. Sizes, colours, formats, widget choices and the new events (§9) are Claude's proposals, marked *[Proposed]*; where the build settled one, it says *[Built]*, and D-054 lists them for review.

---

## 1. The DUT and what the screen judges

- **The DUT is one sensor with 8 output channels** (D-002 reversed). It **passes only if all 8 channels pass**.
- Each channel gets its own result and primary failure, because the operator and engineer need to know which channel failed and why.
- The run outcome already works this way: one FAIL makes the run FAIL. The screen says **channel**, never "sensor". Record columns stay as they are (`sensor` = channel ID).

## 2. Two modes, one screen

| Mode | Who | Sequence | Shows |
|---|---|---|---|
| **Operator** (default) | Line operator | Locked to PROD (D-040) | Everything in §3–§6 |
| **Test** | Test / manufacturing engineer | PROD or ENG picker | The same screen **plus** the extras in §7 |

Switching modes never interrupts a run; the switch is disabled while running *[Proposed]*.

## 3. Screen layout (operator mode)

```
┌ Title bar ─────────────────────────────────────────────────────────────────────────────┐
│ Sensor line test  [Operator|Test]  Plan TP-001 PROD v2 · Limits L-v2 · BENCH-01   ● Fixture teensy (COM4) ● Actuator ● Sensor │
├ Control row ───────────────────────────────────────────────────────────────────────────┤
│ DUT serial [__________] [Start]  │ Connect › Baseline › Tone 10 Hz › End │ (●) Actuator ON  │
│                                  │   ✓          ✓        measuring 1.4 s   │ response:10·10 Hz│
├ Result banner ─────────────────────────────────────────────────────────────────────────┤
│ ✗ FAIL   4 of 8 channels failed: ch1 · ch4 · ch5 · ch8                Error code: none │
├ Live signal ────────────┬ Test steps ──────────────────────────────────────────────────┤
│ ch1 ∿∿∿∿∿∿∿∿∿  ✗ FAIL   │ Test        Low    High   ch1   ch2  …  ch8   Result  Code  │
│ ch2 ∿∿∿∿∿∿∿∿∿  ✓ PASS   │ Connection  148.5  151.5  150.01 Hz (device check)  ✓ PASS  —  │
│ …  (8 strips)           │ Baseline offset −127.1 455.8 ✓−50.7 ✓134.3 … ✓214.5 ✗ FAIL —  │
│                         │ …                                                         │
│                         │ Channel result          ✗FAIL ✓PASS …  ✗FAIL               │
├ Serial terminal (collapsible) ─────────────────────────────────────────────────────────┤
│ 02:11:59.351 → response:10   02:11:59.358 ← 10.00   02:11:59.364  -48,1132,947,…      │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

Minimum window **1280 × 800** *[Built: the layout's own minimum is about 1,130 px wide on Windows, ENG in test mode included]*. The table and strips share the spare height; the terminal shows 4 lines (test mode: 170 px for its tabs) and can be collapsed to its header.

## 4. Regions

### 4.1 Title bar
- App name, the **mode switch**, then the plan, plan version, limit set and station (from `PlansLoaded` and the settings).
- **Fixture status**, always visible (D-037): the fixture name and port, then **Actuator** and **Sensor**, each ● green "connected" or ● red "not found" with the word, never colour alone.

### 4.2 Control row
- **DUT serial field**, with the rules unchanged from D-047: Start is disabled until the serial is valid, Enter starts, the field locks during the run and clears on Next.
- **Buttons from the ACTIONS registry**, shown by state:

  | State | Control row | Result banner |
  |---|---|---|
  | Idle | Start (enabled once the serial is valid), Next (disabled) | — |
  | Running / safe state | Abort | — |
  | Result | Start (disabled), **Next** | **Retry**, when the error is retryable and not yet retried (D-026) |
  | Not ready | — | **Reload files** (test mode only) |

  *[Built]* Retry sits beside the error it retries, as in the render; there is one Next, in the control row. The primary (blue) button is whichever of Start / Next / Retry is enabled.

- **Sequence bar:** one box for **Connect**, one per capture in the plan (PROD: Baseline, Tone 10 Hz; ENG: Baseline, Tone 5/10/15 Hz, Recovery), and one for **End**.
  - **Before the run:** the boxes are pending, as a preview.
  - **During the run:** the current box is highlighted, with "n.n s left". Finished boxes show ✓. A box that raised an error shows its code ("⚠ stopped: E205"), and an aborted one shows "■ aborted". End shows "✓ actuator off" once the safe state is confirmed, or "⚠ W101" if it isn't.
  - *[Built]* **Compact** when the plan has more than 3 captures (ENG): tones by frequency ("5 Hz"), short hints ("2.2 s · on"), no arrows, so the row fits 1280 px.
- **Actuator indicator** (FR-07): a large lamp and text.
  - **ON:** an amber lamp, "Actuator ON", and below it the command, the frequency and the reply ("response:10 · 10 Hz · ack 10.00").
  - **OFF:** a grey lamp, "Actuator OFF · baseline · safe state".
  - **Before any command:** "Actuator —", "no command sent yet" *[Built]*: the station can't know the state until it commands one.
  - It is driven by `ActuatorChanged`, which carries the command and the device's accepted reply, so it shows exactly what the station last commanded and had accepted.

### 4.3 Result banner
One line, large: the verdict word is 22 pt bold, as in the approved render (the draft said ≥ 28 pt). Every state has an icon, a word and a colour:

| State | Look | Text |
|---|---|---|
| Ready | neutral | "Scan or type the DUT serial" |
| Testing | blue | "TESTING… step n of N · do not remove the DUT", elapsed / expected time |
| PASS | green ✓ | "PASS · all 8 channels" |
| FAIL | red ✗ | "FAIL · n of 8 channels failed: ch1 · ch4 · …": **which channels, not why** (D-055, the user's call). Each channel's primary failure (TP-001 §8) is in the table, the Channel result and chip tooltips, and the enlarged plot |
| ERROR | amber ⚠ | "ERROR E205@T10 · Dropped lines · Retry once" (the code, its meaning and the action from TP-001 §9) |
| ABORTED | grey | "ABORTED · the actuator is off · press Next" |
| NOT READY | amber ⚠ | the file error (E301/E302) and "Call engineering" |
| Safe state | blue | "STOPPING… Turning the actuator off" |

The banner also shows the **error code** ("Error code: none" on PASS/FAIL) and "Press Next for the next DUT".

### 4.4 Test steps table (layout A, the operator's main view)
**Rows** are the plan's steps in run order, so the table is also the step preview before the run:
- **1000 Connection** first;
- the limit steps by capture;
- **1900 Run integrity**;
- a final **Channel result** row.

In ENG, rows are grouped under capture headings (Baseline, Tone 5 Hz, …, Recovery). *[Built]* The rule is "group when step titles repeat", so it follows the data, not the sequence name.

**Columns:**

| Column | Content |
|---|---|
| **Test** | The operator name (e.g. "Baseline offset") and its unit. Step IDs appear in test mode only |
| **Low**, **High** | The limits. They are common to all channels (D-002), so one pair per row |
| **ch1 … ch8** | The measurement, formatted by unit *[Built: a.u., % and dB 1 decimal (a.u. ≥ 1000 as an integer), Hz 3, ° 2, samples and counts 0]* |
| **Result** | The step result: FAIL if any channel failed |
| **Code** | The error code raised on that step (E104 on Connection, E205@T10 on Run integrity); "—" otherwise |

**Cell states** (always a symbol and a word or value, never colour alone):

| State | Look |
|---|---|
| Pass | green background, "✓ 134.3" |
| Fail | red background, bold, "✗ 1349" |
| Not tested | grey background, "–"; the tooltip gives the reason ("no tone", "E205@T10") |
| Pending | empty |
| Running capture | its rows are highlighted |

**Run-level rows** (Connection, Run integrity) have one value spanning the channel columns: "150.01 Hz", or "all checks passed", or the error detail.

**Channel result row:** per channel, ✓ PASS or ✗ FAIL. The primary failure goes in this row's tooltip, the strip chip's tooltip and the enlarged plot's header; the banner names the failed channels only (D-055).

**Live updates:** each `StepEvaluated` fills its cell as soon as it's judged.

### 4.5 Live signal (FR-06, revises D-035)
- **8 strips, one per channel, each on its own y-scale.** A shared axis would let ch5's ~1350 a.u. offset flatten the others.
- Each strip shows the last 2 s, with the channel name and its result chip ("✓ PASS" / "✗ FAIL" / "…") beside it.
- **Click a strip to enlarge it:** that channel over the whole body area (strips and table), with axes (time in s, value in a.u.), the run's windows shaded and labelled (Baseline, Tone 10 Hz), its result, and "Back to all channels". *[Proposed, in the PyQt render]*
- Refresh at `ui.plot_fps` (30 fps, settings), decoupled from the 150 Hz stream (D-043). A plot redraws only when new data has arrived.
- Data arrives in chunks of 15 lines (~0.1 s) during a capture (§9). Between runs the strips show the last 2 s of the last run, labelled "end of Tone 10 Hz".
- *[Built]* **Time axis:** line count ÷ 150 Hz, captures laid end to end (settle lines included). Replayed captures come from different recordings with unrelated clocks, and the ~10 ms command between captures isn't data. The enlarged plot shades each capture's **measured window** (tone amber, baseline grey) and leaves the settle lines unshaded.
- *[Built]* **No antialiasing.** Antialiased drawing biased the host-timed line rate by up to −0.27% on the Teensy (D-054, `benchtop_testing/07_gui_line_rate/`).

### 4.6 Serial terminal (read-only)
- Collapsible; it starts collapsed to 4 lines, in a monospaced font, newest last.
- **Commands** (→, light blue) and **replies** (←, lavender, because green is reserved for PASS) always appear, in bold. **Data lines are thinned to one per 0.2 s at the sensor driver**, printed exactly as the device prints them. The measured windows are in the run's `_raw.csv`.
- A muted line marks the start of each run (run ID, DUT serial, sequence). Warnings (W101) appear as lines too.
- A **Pause** button freezes the view. Keeps the last 1,000 lines *[Proposed]*.
- No input field: the operator can never send a command (device safety).

## 5. Behaviour by state (D-037 to D-039)

| State | Enabled | Screen |
|---|---|---|
| Starting / Not ready | nothing (Not ready: the file error and Reload in test mode) | banner explains the file problem (E301/E302) |
| Idle | serial field; Start once the serial is valid and both roles are connected | table shows the plan with empty cells; sequence bar pending |
| Running | Abort | the sequence bar and table fill in live; the actuator indicator follows the commands |
| Result | Next; Retry if the error is retryable | banner, filled table, last windows in the strips |
| Safe state | nothing | "Turning the actuator off…" |

## 6. Visual rules *[Proposed]*
- **Reserved status colours:** green = PASS, red = FAIL, amber = ERROR and actuator ON, blue = running, grey = not tested or pending. They're never used for anything else.
- **Never colour alone:** every status carries ✓ / ✗ / ⚠ / – and a word, so it reads on a monochrome screen or for a colour-blind operator.
- Light theme, high contrast. Text is 10 pt (Segoe UI), the banner verdict 22 pt bold and the actuator state 15 pt bold, as in the approved render.
- **Readable at arm's length:** the banner, the actuator indicator and the Channel result row are the three things an operator reads from a distance.

## 7. Test mode extras (same screen, D-053)
- **Sequence picker:** PROD or ENG (D-040).
- **Step IDs** as the first table column (2040, 6050, …).
- **Run header panel:** run ID, timestamp, plan version, limit set, app version, device serial, line rate per capture, commands with times.
- **Diagnostics panel (read-only, D-029):** capture → metric → 8 channel values. Never editable, never affects a verdict.
- **Raw window viewer:** the enlarged plot shows every capture's full window from the last run, not just the live strip. *[Built: the same enlarged plot, which already holds the whole run]*
- *[Built]* The run header and diagnostics are `@panel` plug-ins (D-033), shown as tabs beside the serial terminal ("Serial terminal | Run header | Diagnostics").

## 8. PyQt6 / pyqtgraph mapping *[Proposed]*

| Element | Widget | Notes |
|---|---|---|
| Mode switch | `QTabBar`, or two checkable `QPushButton`s in a `QButtonGroup` | Disabled while running |
| Fixture status | `QLabel`s with a coloured ● and a word | Updated from `DeviceStatusChanged` |
| Serial field, buttons | `QLineEdit` + `QPushButton`s from the ACTIONS registry | Exists today (D-047) |
| Sequence bar | one `QFrame` per box in a `QHBoxLayout` | Built from the plan's captures; state set through a dynamic property + stylesheet |
| Actuator indicator | `QFrame` with a round `QLabel` lamp (`border-radius`) and two `QLabel`s | `ActuatorChanged` |
| Result banner | `QFrame` + `QLabel`s, style by dynamic property | `RunStarted`, `RunCompleted`, `AppStateChanged` |
| Test steps table | `QTableView` + a `QAbstractTableModel` | `DisplayRole` text, `BackgroundRole`/`ForegroundRole` status colours, `ToolTipRole` for NOT TESTED reasons, `setSpan` for run-level rows; read-only. Plain coloured cells, not the mockup's rounded chips |
| Live strips | *[Built]* 8 pyqtgraph `PlotWidget`s in a grid with the names and `QLabel` chips (axes hidden, mouse disabled, y auto-range). One widget per strip kept the click and the alignment simple; a single `GraphicsLayoutWidget` stays an option if drawing gets heavy | A `QTimer` at `plot_fps` copies from the run buffer into `PlotDataItem.setData` |
| Enlarged plot | pyqtgraph `PlotWidget` in a panel stacked over the strips (`QStackedWidget`) | Opened from the strip's click signal |
| Serial terminal | `QPlainTextEdit` (read-only, `setMaximumBlockCount(1000)`) in a collapsible frame | Lines appended in batches by the same timer |
| Diagnostics, run header | *[Built]* `QTableWidget` / `QTreeWidget`, registered with `@panel` | Test mode only, as tabs beside the terminal |

**Threading:** unchanged (D-043). Engine events cross to the UI thread through the existing Qt bridge signal. Sample chunks and terminal lines go into ring buffers that the UI timer drains, so the 150 Hz stream never floods the event loop.

## 9. The plumbing the screen needed *[Built, D-054]*
1. **The plan up front:** `PlansLoaded.plans` carries the compiled plans (steps, captures, limits), so the table and sequence bar preview the run before Start.
2. **Operator names:** a `title` per step and per capture in the sensor test file ("Baseline offset" for `BL_OFFSET`, "Tone 10 Hz" for `T10`). This is data (D-040). A missing step title falls back to the step name; a missing capture title falls back to "Tone N Hz" or the capture ID.
3. **Live sample chunks:** `SensorStream.acquire(…, on_chunk)` hands every line it reads (settle lines too) to the callback in chunks of 15 lines, and the engine publishes `SamplesArrived(capture, start, t, x)`. The replay link serves lines chunk by chunk at the device's pace. A test checks that the chunks add up exactly to the judged windows.
4. **Serial traffic:** each driver's `on_traffic` hook (set by the device manager) becomes `TrafficSeen(direction, text, t)`. "tx" commands and "rx" replies always appear; "data" lines are thinned at the sensor driver to one per 0.2 s. `ActuatorChanged` also carries the command and the accepted reply (`Stimulus.set_tone` returns it).
5. **The error's step:** the engine already labels errors with their capture (`E205@T10`); the table puts them on 1000 for E1xx codes and on 1900 for the rest, as the record already does.

## 10. Open for later
- Should test mode need a password or key on a production station?
- A sound on PASS/FAIL?
- A run history list of today's DUTs?
- Export the chart as PNG (P1, FR-15).
