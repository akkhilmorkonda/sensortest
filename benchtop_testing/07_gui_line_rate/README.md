# 07 GUI line rate: does the station screen disturb the measurement? (D-054)

**Date:** 2026-11-08 · **Device:** Teensy on COM4 (USB serial 20071290) · **Tool:** `benchtop_testing/tools/gui_line_rate.py` · **Raw output:** `results.txt`

## Why
The first PROD run through the new station screen read a line rate of **149.78 Hz** on 1000 CONNECT. Every headless run so far had read 150.01 Hz, including bring-up, DOE-1 and the L-v1 gauge run. The line rate is measured on host time (M9). It is the number the 1% rule judges (D-008: E104 on the first capture, E205 after it), so anything in the app that shifts it eats into that margin.

## What was run
- Five PROD runs in each of three set-ups, through the real app on the Teensy:
  - **GUI, antialiased:** the window drawing with pyqtgraph antialiasing on (the first build's setting).
  - **GUI, antialias off:** the setting the build now uses.
  - **Headless:** the same engine, with no window.
- The window was shown off screen at 1280 × 800, refreshing at `ui.plot_fps` = 30.
- A shorter GIL switch interval (1 ms instead of 5 ms) was also tried with antialiasing on. It made no difference (BL 149.70–149.76 Hz over 4 runs; not in `results.txt`).

## Result

| Set-up | BL line rate (Hz) | T10 line rate (Hz) |
|---|---|---|
| GUI, antialiased | 149.58–149.76 (median 149.61, **−0.27%**) | 149.92–150.08 (median 149.93) |
| GUI, antialias off | 149.98–150.00 (median 149.99) | 149.98–150.01 (median 150.01) |
| Headless | 150.01–150.02 (median 150.01) | 150.01–150.02 (median 150.01) |

## What it means
- **The device didn't change. The host's timestamps did.** The serial reader thread stamps each line when it reads it. Antialiased drawing holds Python's GIL in the GUI thread long enough to delay the reader in a way that stretches the window's time span. The bias was largest on the first capture, where the screen does the most work as the run starts (it reloads the table and the sequence bar, and starts the strips).
- **With antialiasing off, the GUI reads the same rate as headless** to within 0.02%, so the 1% rule keeps its full margin.
- Even antialiased, the worst run (149.58 Hz) was inside the 148.5–151.5 Hz window, so no verdict or error code changed. It used about a quarter of the margin, though, and the cause would get worse on a slower station PC.

## Decision (D-054, Claude's call during the build)
- The station's plots draw **without antialiasing** (`sensortest/ui/theme.py`).
- **Revisit** if the screen gains heavier drawing, or if a run's line rate drifts from 150.01 Hz by more than 0.05%. Re-run this tool to check.
- A sturdier fix would stamp lines from a source the GUI cannot delay: a C-level reader, or a separate process. That isn't needed at this margin.
