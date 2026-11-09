"""The actuator's tone phase jump (found in the L-v1 gauge run, 2026-11-08): measure it and draw it.

Hypothesis: the firmware computes the tone as sin(2*pi*f*t) with t from a 32-bit microsecond
counter, which wraps every 2^32 us = 4294.967296 s (71 min 35 s). At a wrap, t drops by that
much, so the tone's phase jumps by -360 * frac(f * 4294.967296) degrees: +117.7 at 10 Hz,
-124.5 at 20 Hz (ch8 runs at 2f).

This script:
  1. scans every tone capture in the gauge run and every DOE-1 tone segment for a jump
     (a 30-line block whose fitted amplitude drops >10 % below the segment's median);
  2. measures each jump's phase step on the healthy channels (and ch8 at 2f) vs the prediction;
  3. draws 05_gauge_run_l_v1/phase_jump.png: the jump itself, and why it reads as low amplitude.

Usage (global Python with matplotlib, like the other analysis tools):
  python benchtop_testing/tools/phase_jump.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH.parent / "src"))
from analyze_sweep import CH_COLORS, INK2, MUTED, WASH, plt  # noqa: E402  (sets the chart style)

from sensortest.core.methods import sine_fit  # noqa: E402  (the station's M3)
from sensortest.devices.replay.recordings import read_manifest, read_recording  # noqa: E402

WRAP_S = 2**32 / 1e6
FS = 150.0
HEALTHY = [1, 2, 4, 5, 6]  # ch2, ch3, ch5, ch6, ch7 (0-based)
GAUGE = BENCH / "05_gauge_run_l_v1"
LOW_LIMIT = 712.8  # 6040 T10_AMPLITUDE low limit (L-v0)


def predicted(f: float) -> float:
    return (-360 * ((f * WRAP_S) % 1) + 180) % 360 - 180


def dips(w: np.ndarray, f: float, block: int = 30) -> list[int]:
    ref = (w[:, HEALTHY] - w[:, HEALTHY].mean(0)).mean(1)
    amps = [sine_fit(ref[k:k + block], f)[1] for k in range(0, len(ref) - block + 1, block)]
    med = np.median(amps)
    return [k * block for k, a in enumerate(amps) if a < 0.9 * med]


def jump_line(w: np.ndarray, k0: int) -> int:
    """First line after the biggest sample-to-sample step of the healthy mean inside the block."""
    ref = w[:, HEALTHY].mean(1)
    return k0 + 1 + int(np.argmax(np.abs(np.diff(ref[k0:k0 + 31]))))


def phase_step(x: np.ndarray, f: float, k: int, pre: int = 90, post: int = 30) -> float:
    pb = np.degrees(sine_fit(x[k - pre:k], f)[2])
    pa = np.degrees(sine_fit(x[k:k + post], f)[2]) - 360 * f * pre / FS
    return ((pa - pb) + 180) % 360 - 180


def reading(p: np.ndarray, theta_deg: float) -> np.ndarray:
    """Best single-sine amplitude / true amplitude when the phase steps by theta at fraction p."""
    return np.sqrt(1 - 2 * p * (1 - p) * (1 - np.cos(np.radians(theta_deg))))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"predicted phase step at a wrap: 5 Hz {predicted(5):+.1f}, 10 Hz {predicted(10):+.1f}, "
          f"15 Hz {predicted(15):+.1f}, 20 Hz {predicted(20):+.1f} deg")
    hit = None
    n = 0
    for f in sorted((GAUGE / "records").rglob("*_raw.csv")):
        raw = list(csv.DictReader(f.open(newline="", encoding="utf-8")))
        for cap in ("T5", "T10", "T15"):
            sel = [r for r in raw if r["capture"] == cap]
            if not sel:
                continue
            n += 1
            w = np.array([[float(r[f"ch{i}"]) for i in range(1, 9)] for r in sel])
            hz = float(cap[1:])
            for k0 in dips(w, hz):
                k = jump_line(w, k0)
                steps = [phase_step(w[:, c], hz, k) for c in HEALTHY]
                ch8 = phase_step(w[:, 7], 2 * hz, k)
                print(f"gauge {f.name.removesuffix('_raw.csv')} {cap}: jump at line {k} of {len(w)} "
                      f"(p = {k / len(w):.3f}); healthy {min(steps):+.1f}..{max(steps):+.1f} deg "
                      f"(predicted {predicted(hz):+.1f}); ch8 at {2 * hz:g} Hz {ch8:+.1f} "
                      f"(predicted {predicted(2 * hz):+.1f})")
                hit = hit or (w, hz, k, f.name)
    print(f"gauge tone captures scanned: {n}")
    m = 0
    for run in read_manifest(BENCH / "03_doe1_repeatability"):
        if run.tone_hz is None:
            continue
        rec = read_recording(run.tsv)
        seg = rec.x[rec.after(1) + 30:rec.after(2)]
        m += 1
        for k0 in dips(seg, float(run.tone_hz)):
            print(f"DOE-1 {run.tsv.name}: dip at line {k0}")
    print(f"DOE-1 tone segments scanned: {m}")
    if hit:
        draw(*hit)


def draw(w: np.ndarray, hz: float, k: int, name: str) -> None:
    fig, (a, b) = plt.subplots(2, 1, figsize=(9, 7.2), gridspec_kw={"height_ratios": [1.1, 1]})
    lo, hi = k - 90, min(len(w), k + 35)
    t = np.arange(lo, hi) / FS
    c, amp, ph, _ = sine_fit(w[lo:k, 6], hz)
    n = np.arange(hi - lo)
    a.plot(t, w[lo:hi, 6], color=CH_COLORS[0], lw=2, label="ch7 as measured")
    a.plot(t, c + amp * np.sin(2 * np.pi * hz * n / FS + ph), "--", color=MUTED, lw=1.6,
           label="the same tone with no jump (fit to the samples before it)")
    tj = (k - 0.5) / FS
    a.axvline(tj, color=INK2, lw=0.8, ls=":")
    a.annotate(f"counter wraps: {w[k - 1, 6]:.0f} → {w[k, 6]:+.0f} in one sample\n"
               f"phase +{phase_step(w[:, 6], hz, k):.0f}° (predicted {predicted(hz):+.1f}°)",
               xy=(tj, 1080), xytext=(-8, 18), textcoords="offset points", ha="right", va="bottom",
               color=INK2, fontsize=8.5, arrowprops={"arrowstyle": "-", "color": INK2, "lw": 0.6})
    a.set_ylim(-1700, 1700)
    a.set_xlabel("time in the T10 window (s)")
    a.set_ylabel("ch7 (a.u.)")
    a.set_title(f"A. The jump (gauge run, ENG T10: {name.split('_')[1].removesuffix('_raw.csv')})",
                loc="left", fontsize=10.5)
    a.legend(loc="lower left", fontsize=8, frameon=False, ncol=2)

    p = np.linspace(0, 1, 401)
    b.axhspan(0, LOW_LIMIT / 800, color=WASH, lw=0, zorder=0)
    b.axhline(LOW_LIMIT / 800, color=INK2, lw=0.8, ls=":")
    b.annotate("a sensor at 800 a.u. fails below this line", (0.5, LOW_LIMIT / 800),
               xytext=(0, 4), textcoords="offset points", ha="center", color=INK2, fontsize=8)
    for f, style, lw, color in ((5, "--", 1.3, MUTED), (15, ":", 1.6, MUTED), (10, "-", 2.2, CH_COLORS[0])):
        b.plot(p, reading(p, predicted(f)), style, color=color, lw=lw)
    label = {5: (0.5, (0, -13), "center"), 10: (0.5, (0, 6), "center"), 15: (0.3, (-8, -3), "right")}
    for f, (x0, off, ha) in label.items():
        b.annotate(f"{f} Hz (jump {predicted(f):+.0f}°)", (x0, reading(np.array([x0]), predicted(f))[0]),
                   xytext=off, textcoords="offset points", ha=ha, color=INK2, fontsize=8)
    pk = k / len(w)
    b.plot([pk], [reading(np.array([pk]), predicted(hz))[0]], "o", color=CH_COLORS[0], ms=7,
           markeredgecolor="white", markeredgewidth=1.5, zorder=3)
    ratio = reading(np.array([pk]), predicted(hz))[0]
    b.annotate(f"this run: wrap at {pk:.0%} of the window\n→ reads {ratio:.0%} of the true amplitude",
               xy=(pk, reading(np.array([pk]), predicted(hz))[0]), xytext=(0.985, 0.42), textcoords="data",
               ha="right", color=INK2, fontsize=8, arrowprops={"arrowstyle": "-", "color": INK2, "lw": 0.6})
    b.set_xlim(0, 1)
    b.set_ylim(0, 1.05)
    b.set_xlabel("where in the 2 s window the wrap lands (fraction of the window)")
    b.set_ylabel("amplitude reading ÷ true amplitude")
    b.set_title("B. Why a phase jump reads as low amplitude: one sine fit across two phases",
                loc="left", fontsize=10.5)
    fig.tight_layout()
    out = GAUGE / "phase_jump.png"
    fig.savefig(out, dpi=150)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
