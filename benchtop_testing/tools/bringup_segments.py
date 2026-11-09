"""Split a scripted bring-up capture at each command and summarize each segment.

For every command in the capture's .json, reports:
  * latency: time from the write to the first line where the response visibly
    changes (ch7 leaves its idle band, or returns to it), host clock
  * per channel: dominant frequency and its amplitude, measured over the
    steady part of the segment (skipping the first 0.5 s)

Usage:
  python benchtop_testing/tools/bringup_segments.py benchtop_testing/01_bringup/<file>.tsv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from bringup_inspect import load, parse

SETTLE_S = 0.5
IDLE_CH = 6            # ch7 (0-based 6): idle band about -1..29, clean when excited
IDLE_LO, IDLE_HI = -5.0, 35.0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tsv", type=Path)
    a = p.parse_args()

    meta = json.loads(a.tsv.with_suffix(".json").read_text())
    rows = [(t, parse(b)) for t, b in load(a.tsv)]
    rows = [(t, v) for t, v in rows if v is not None and len(v) == 8]
    t = np.array([r[0] for r in rows])
    x = np.array([r[1] for r in rows])
    fs = np.polyfit(t, np.arange(len(t)), 1)[0]
    print(f"eol={meta['eol']!r}  fs={fs:.2f} Hz  lines={len(t)}")

    cmds = meta["sent"]
    bounds = [c["t_s"] for c in cmds] + [t[-1]]
    for i, c in enumerate(cmds):
        t_cmd, t_end = bounds[i], bounds[i + 1]
        seg = (t >= t_cmd) & (t < t_end)
        idle = (x[:, IDLE_CH] > IDLE_LO) & (x[:, IDLE_CH] < IDLE_HI)
        # latency: first sample after the command whose idle/excited state differs
        # from the state just before the command
        before = idle[(t < t_cmd)][-15:]
        was_idle = before.mean() > 0.5 if len(before) else True
        changed = np.where(seg & (idle != was_idle))[0]
        lat = f"{(t[changed[0]] - t_cmd) * 1e3:7.1f} ms" if len(changed) else "no change"
        steady = (t >= t_cmd + SETTLE_S) & (t < t_end)
        xs = x[steady] - x[steady].mean(axis=0)
        print(f"\n[{t_cmd:6.2f}s] {c['cmd']!r:16} state change after: {lat}   steady pts={len(xs)}")
        if len(xs) < 32:
            continue
        cells = []
        for ch in range(8):
            f_pk, amp, ratio = peak(xs[:, ch], fs)
            cells.append(f"ch{ch+1} {f_pk:5.2f}Hz {amp:7.1f}" + ("" if ratio > 20 else " (noise)"))
        for row in range(0, 8, 4):
            print("   " + " | ".join(cells[row:row + 4]))


def peak(sig: np.ndarray, fs: float, pad: int = 8):
    """Dominant frequency (zero-padded FFT + parabolic interpolation), amplitude, peak/floor ratio."""
    n = len(sig)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(sig * win, n * pad)) * 2 / win.sum()
    freqs = np.fft.rfftfreq(n * pad, 1 / fs)
    k = int(np.argmax(spec[pad:])) + pad  # skip DC region
    if 0 < k < len(spec) - 1:
        a, b, c = np.log(spec[k - 1:k + 2] + 1e-12)
        d = 0.5 * (a - c) / (a - 2 * b + c)
    else:
        d = 0.0
    f_pk = freqs[k] + d * (freqs[1] - freqs[0])
    return f_pk, spec[k], spec[k] / np.median(spec[pad:])


if __name__ == "__main__":
    main()
