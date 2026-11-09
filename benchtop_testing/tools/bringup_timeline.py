"""Sliding-window view of a capture: when did the device actually change state?

Prints, every STEP seconds, ch7's dominant frequency and RMS (ch7 is clean in
both states), so the true switch time after each command is visible.

Usage:
  python benchtop_testing/tools/bringup_timeline.py benchtop_testing/01_bringup/<file>.tsv [--win 0.4] [--step 0.1]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from bringup_inspect import load, parse
from bringup_segments import peak

CH = 6  # ch7


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tsv", type=Path)
    p.add_argument("--win", type=float, default=0.4)
    p.add_argument("--step", type=float, default=0.1)
    a = p.parse_args()

    meta = json.loads(a.tsv.with_suffix(".json").read_text())
    rows = [(t, parse(b)) for t, b in load(a.tsv)]
    rows = [(t, v) for t, v in rows if v is not None and len(v) == 8]
    t = np.array([r[0] for r in rows])
    x = np.array([r[1][CH] for r in rows])
    fs = np.polyfit(t, np.arange(len(t)), 1)[0]
    cmds = {round(c["t_s"], 1): c["cmd"] for c in meta["sent"]}

    prev = None
    for start in np.arange(0, t[-1] - a.win, a.step):
        m = (t >= start) & (t < start + a.win)
        seg = x[m] - x[m].mean()
        rms = seg.std()
        state = "idle" if rms < 50 else f"{peak(seg, fs)[0]:.0f} Hz"
        mark = next((f"  <- sent {cmd!r} at {tc:.2f}s" for tc, cmd in cmds.items()
                     if start - a.step < tc <= start), "")
        if state != prev or mark:
            print(f"{start:6.2f}-{start + a.win:5.2f}s  {state:>6}  rms {rms:7.1f}{mark}")
        prev = state


if __name__ == "__main__":
    main()
