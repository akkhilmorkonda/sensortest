"""Summarize a bring-up capture (.tsv from bringup_capture.py).

Reports line format, malformed lines, the true line rate (least-squares fit
of line index vs host time, robust to USB batching), and per-channel stats.

Usage:
  python benchtop_testing/tools/bringup_inspect.py benchtop_testing/01_bringup/<file>.tsv [--from S] [--to S]
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from pathlib import Path

import numpy as np


def load(path: Path):
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines()[1:]:
        t, rep = ln.split("\t", 1)
        rows.append((float(t), ast.literal_eval(rep)))
    return rows


def parse(line: bytes):
    text = line.decode("ascii", errors="replace").strip()
    fields = [f.strip() for f in text.split(",")]
    if fields and fields[-1] == "":
        fields = fields[:-1]
    try:
        return [float(f) for f in fields]
    except ValueError:
        return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("tsv", type=Path)
    p.add_argument("--from", dest="t_from", type=float, default=None)
    p.add_argument("--to", dest="t_to", type=float, default=None)
    p.add_argument("--fft", action="store_true", help="print the dominant frequency per channel")
    a = p.parse_args()

    rows = load(a.tsv)
    if a.t_from is not None:
        rows = [r for r in rows if r[0] >= a.t_from]
    if a.t_to is not None:
        rows = [r for r in rows if r[0] < a.t_to]

    endings = Counter(r[1][-2:] for r in rows)
    parsed = [(t, parse(b)) for t, b in rows]
    counts = Counter(len(v) if v is not None else "unparseable" for _, v in parsed)
    print(f"lines: {len(rows)}   line endings: {dict(endings)}   fields per line: {dict(counts)}")

    bad = [(t, b) for (t, b), (_, v) in zip(rows, parsed) if v is None or len(v) != 8]
    for t, b in bad[:10]:
        print(f"  non-8-field line at t={t:.3f}s: {b!r}")

    good = [(t, v) for t, v in parsed if v is not None and len(v) == 8]
    if len(good) < 3:
        return
    t = np.array([g[0] for g in good])
    x = np.array([g[1] for g in good])
    idx = np.arange(len(t))
    slope, _ = np.polyfit(t, idx, 1)
    dt = np.diff(t)
    print(f"line rate (fit): {slope:.2f} lines/s   span {t[0]:.3f}-{t[-1]:.3f}s")
    print(f"host inter-line gap: median {np.median(dt)*1e3:.2f} ms, p5 {np.percentile(dt,5)*1e3:.2f}, p95 {np.percentile(dt,95)*1e3:.2f}, max {dt.max()*1e3:.2f}")
    print("ch   mean       std      min       max     p2p")
    for c in range(8):
        col = x[:, c]
        print(f"{c+1:>2} {col.mean():9.2f} {col.std():9.2f} {col.min():9.2f} {col.max():9.2f} {np.ptp(col):8.2f}")
    if a.fft:
        fs = slope
        xd = x - x.mean(axis=0)
        win = np.hanning(len(xd))[:, None]
        spec = np.abs(np.fft.rfft(xd * win, axis=0)) * 2 / win.sum()
        freqs = np.fft.rfftfreq(len(xd), 1 / fs)
        print(f"spectrum (fs={fs:.2f} Hz, {len(xd)} pts): top peak per channel, amplitude, peak/median floor")
        for c in range(8):
            k = int(np.argmax(spec[1:, c])) + 1
            floor = np.median(spec[1:, c])
            print(f"  ch{c+1}: {freqs[k]:6.2f} Hz  amp {spec[k, c]:8.2f}  ratio {spec[k, c]/floor:6.1f}")
    decimals = Counter(len(s.split(".")[1]) if "." in s else 0
                       for _, b in rows[:200]
                       for s in b.decode("ascii", "replace").strip().rstrip(",").split(", "))
    print(f"decimal places seen: {dict(decimals)}")


if __name__ == "__main__":
    main()
