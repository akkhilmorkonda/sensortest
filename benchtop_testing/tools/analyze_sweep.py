"""Plot and tabulate a baseline -> response sweep (bring-up exploration).

Input: a folder of captures from bringup_capture.py, each scripted as
  baseline, ~10 s, response:XX, ~10 s, baseline
(see benchtop_testing/02_sweep_1-25hz/).

Output, written to <folder>/analysis/:
  summary.csv                one row per (command, channel): the table view of every chart
  sweep_summary.png          measured frequency, response amplitude and SNR vs commanded frequency
  transitions/rXX.png        raw data of every channel, full 20 s plus a zoom on the switch
  spectrum_rXX.png           baseline vs response spectrum per channel, for --spectrum frequencies

Exploration only: the production metrics are built and unit-tested in
section 03 (measurement core). Here, amplitude = Hann-windowed, 8x zero-padded
FFT peak; SNR = 20*log10((amplitude/sqrt 2) / baseline std).

Usage:
  python benchtop_testing/tools/analyze_sweep.py benchtop_testing/02_sweep_1-25hz [--spectrum 10 22]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from bringup_inspect import load, parse  # noqa: E402
from bringup_segments import peak  # noqa: E402

SETTLE_S = 0.5          # skip this much after each command before measuring
TONE_RATIO = 20.0       # a peak must be this many times the median spectrum to count as a tone

# Reference palette (dataviz skill), light mode. Channel n always gets slot n.
SURFACE, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS, WASH = "#e1e0d9", "#c3c2b7", "#f0efec"
CH_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"], "font.size": 9,
    "text.color": INK, "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "grid.linestyle": "-",
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
})


# ---------------------------------------------------------------- loading ----

def load_capture(tsv: Path) -> dict:
    meta = json.loads(tsv.with_suffix(".json").read_text())
    rows = load(tsv)
    data, other = [], []
    for i, (t, b) in enumerate(rows):
        v = parse(b)
        if v is not None and len(v) == 8:
            data.append((t, v))
        elif i != len(rows) - 1:                       # last line may be cut off
            other.append((t, b.decode("ascii", "replace").strip()))
    t = np.array([d[0] for d in data])
    x = np.array([d[1] for d in data])
    sent = meta["sent"]
    t_base, t_resp, t_end = sent[0]["t_s"], sent[1]["t_s"], sent[2]["t_s"]
    cmd = sent[1]["cmd"]
    replies = [s for tt, s in other if t_resp <= tt < t_resp + 0.5]
    acks = [float(s) for s in replies if re.fullmatch(r"-?\d+(\.\d+)?", s)]
    return {
        "file": tsv.name, "cmd": cmd, "commanded": int(cmd.split(":")[1]),
        "replies": replies, "effective": acks[-1] if acks else None,
        "fs": np.polyfit(t, np.arange(len(t)), 1)[0],
        "t": t - t_resp, "x": x,                      # time is relative to the response command
        "base": (t >= t_base + SETTLE_S) & (t < t_resp),
        "resp": (t >= t_resp + SETTLE_S) & (t < t_end),
        "t_base": t_base - t_resp, "t_end": t_end - t_resp,
    }


def amp_at(sig: np.ndarray, fs: float, f: float) -> float:
    """Amplitude of the component at exactly f Hz (Hann window, single DFT bin)."""
    w = np.hanning(len(sig))
    n = np.arange(len(sig))
    return float(2 * abs(np.sum(w * sig * np.exp(-2j * np.pi * f * n / fs))) / w.sum())


def measure(cap: dict) -> list[dict]:
    fs, x = cap["fs"], cap["x"]
    out = []
    for ch in range(8):
        b = x[cap["base"], ch]
        r = x[cap["resp"], ch]
        f_pk, a_pk, ratio = peak(r - r.mean(), fs)
        eff = cap["effective"]
        a_eff = amp_at(r - r.mean(), fs, eff) if eff else float("nan")
        b_std = b.std()
        out.append({
            "command": cap["cmd"], "commanded_hz": cap["commanded"],
            "device_reply": " | ".join(cap["replies"]), "effective_hz": eff,
            "channel": ch + 1,
            "baseline_mean": round(b.mean(), 2), "baseline_std": round(b_std, 2),
            "baseline_min": round(b.min(), 2), "baseline_max": round(b.max(), 2),
            "response_mean": round(r.mean(), 2),
            "dominant_hz": round(f_pk, 3), "dominant_amp": round(a_pk, 1),
            "dominant_is_tone": bool(ratio > TONE_RATIO),
            "amp_at_effective_hz": round(a_eff, 1),
            "snr_db": round(20 * np.log10((a_pk / np.sqrt(2)) / b_std), 1) if ratio > TONE_RATIO else "",
            "sample_rate_hz": round(fs, 3), "file": cap["file"],
        })
    return out


# ---------------------------------------------------------------- figures ----

def end_labels(ax, items: list[tuple[str, float, float]], min_gap_frac: float = 0.045) -> None:
    """Direct-label line ends at the right edge, nudged apart so they never collide.

    items: (text, x, y) in data coordinates.
    """
    lo, hi = ax.get_ylim()
    gap = (hi - lo) * min_gap_frac
    items = sorted(items, key=lambda i: i[2])
    # Group labels that would collide, then spread each group evenly around its
    # own mean, so labels stay as close to their lines as the gap allows.
    groups = [[it] for it in items]

    def span(g):  # (bottom, top) of a group's labels once spread around its mean
        centre = np.mean([y for _, _, y in g])
        start = centre - gap * (len(g) - 1) / 2
        return start, start + gap * (len(g) - 1)

    merged = True
    while merged:  # merge neighbouring groups until no two collide
        merged = False
        for i in range(len(groups) - 1):
            if span(groups[i + 1])[0] - span(groups[i])[1] < gap:
                groups[i:i + 2] = [groups[i] + groups[i + 1]]
                merged = True
                break
    for g in groups:
        start, _ = span(g)
        for k, (text, x, _) in enumerate(g):
            ax.text(x + 0.6, start + k * gap, text, va="center", color=INK2, fontsize=8, clip_on=False)

def plot_transition(cap: dict, path: Path) -> None:
    t, x = cap["t"], cap["x"]
    fig, axes = plt.subplots(8, 2, figsize=(13, 14), sharex="col",
                             gridspec_kw={"width_ratios": [3, 1.3], "hspace": 0.35, "wspace": 0.12})
    zoom = (t >= -0.25) & (t <= 0.5)
    for ch in range(8):
        for col, (sel, lw) in enumerate([(np.ones_like(t, bool), 0.6), (zoom, 1.4)]):
            ax = axes[ch, col]
            ax.axvspan(0, cap["t_end"] if col == 0 else 0.5, color=WASH, lw=0, zorder=0)
            ax.axvline(0, color=INK2, lw=0.8, zorder=1)
            ax.plot(t[sel], x[sel, ch], color=CH_COLORS[ch], lw=lw, zorder=2)
            if col == 1:
                ax.plot(t[sel], x[sel, ch], "o", color=CH_COLORS[ch], ms=2.5, zorder=3)
                ax.set_yticklabels([])
            ax.margins(x=0)
        lo, hi = x[:, ch].min(), x[:, ch].max()
        pad = 0.08 * (hi - lo)
        for col in range(2):
            axes[ch, col].set_ylim(lo - pad, hi + pad)
        axes[ch, 0].set_ylabel(f"ch{ch + 1}", rotation=0, ha="right", va="center", color=INK, fontsize=10)
    axes[0, 0].set_title(f"Full capture: baseline, then {cap['cmd']}  (raw values, a.u.)", loc="left", fontsize=10)
    axes[0, 1].set_title("Zoom on the switch (each dot = one sample)", loc="left", fontsize=10)
    axes[0, 0].annotate(f"sent {cap['cmd']}", xy=(0, 1), xycoords=("data", "axes fraction"),
                        xytext=(4, -2), textcoords="offset points", va="top", color=INK2, fontsize=8)
    axes[0, 0].annotate("baseline", xy=(cap["t_base"], 1), xycoords=("data", "axes fraction"),
                        xytext=(4, -2), textcoords="offset points", va="top", color=INK2, fontsize=8)
    axes[7, 0].set_xlabel("seconds from the response command")
    axes[7, 1].set_xlabel("seconds from the response command")
    reply = " | ".join(cap["replies"]) or "(none)"
    fig.subplots_adjust(top=0.94)
    fig.suptitle(f"{cap['cmd']}  ·  device reply: {reply}  ·  {cap['fs']:.2f} samples/s  ·  shaded = response",
                 x=0.06, y=0.975, ha="left", fontsize=12, color=INK)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def plot_summary(rows: list[dict], path: Path) -> None:
    cmds = sorted({r["commanded_hz"] for r in rows})
    by = {(r["commanded_hz"], r["channel"]): r for r in rows}
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2), gridspec_kw={"wspace": 0.28})

    # (a) measured frequency vs commanded
    ax = axes[0]
    ax.plot(cmds, cmds, color=AXIS, lw=1, zorder=1)
    ax.annotate("commanded", xy=(cmds[-1], cmds[-1]), xytext=(-4, 4), textcoords="offset points",
                ha="right", color=MUTED, fontsize=8)
    no_tone = []
    for ch in range(1, 9):
        pts = [(c, by[c, ch]["dominant_hz"]) for c in cmds if by[c, ch]["dominant_is_tone"]]
        if not pts:
            no_tone.append(f"ch{ch}")
            continue
        xs, ys = zip(*pts)
        ax.plot(xs, ys, "-o", color=CH_COLORS[ch - 1], lw=2 if ch == 8 else 1.2, ms=4, label=f"ch{ch}", zorder=2)
    ax.annotate("ch8 at 2×", xy=(8, 16), xytext=(-58, 4), textcoords="offset points", color=INK2, fontsize=8)
    ax.annotate("ch2–ch7 on the line", xy=(14, 14), xytext=(8, -12), textcoords="offset points", color=INK2, fontsize=8)
    ax.axvspan(19.5, cmds[-1] + 0.5, color=WASH, lw=0, zorder=0)
    ax.annotate("device rejects 20–25,\nplays 19 Hz", xy=(19.7, 2), color=INK2, fontsize=8)
    ax.set_title("Measured tone frequency", loc="left", fontsize=11)
    ax.set_xlabel("commanded frequency (Hz)")
    ax.set_ylabel("dominant frequency in response (Hz)")
    if no_tone:
        ax.text(0.01, -0.2, f"Not shown (no tone above the noise floor): {', '.join(no_tone)}",
                transform=ax.transAxes, color=MUTED, fontsize=8)

    # (b) response amplitude vs commanded
    ax = axes[1]
    ax.axvspan(19.5, cmds[-1] + 0.5, color=WASH, lw=0, zorder=0)
    labels = []
    for ch in range(1, 9):
        ys = [by[c, ch]["dominant_amp"] for c in cmds]
        ax.plot(cmds, ys, "-o", color=CH_COLORS[ch - 1], lw=1.2, ms=3.5, label=f"ch{ch}", zorder=2)
        labels.append((f"ch{ch}", cmds[-1], ys[-1]))
    ax.set_ylim(bottom=0)
    end_labels(ax, labels)
    ax.set_title("Response amplitude (peak, a.u.)", loc="left", fontsize=11)
    ax.set_xlabel("commanded frequency (Hz)")
    ax.set_ylabel("amplitude of the dominant tone")

    # (c) SNR vs commanded
    ax = axes[2]
    ax.axvspan(19.5, cmds[-1] + 0.5, color=WASH, lw=0, zorder=0)
    labels = []
    for ch in range(1, 9):
        pts = [(c, by[c, ch]["snr_db"]) for c in cmds if by[c, ch]["snr_db"] != ""]
        if not pts:
            continue
        xs, ys = zip(*pts)
        ax.plot(xs, ys, "-o", color=CH_COLORS[ch - 1], lw=1.2, ms=3.5, label=f"ch{ch}", zorder=2)
        labels.append((f"ch{ch}", xs[-1], ys[-1]))
    end_labels(ax, labels)
    ax.set_title("Signal vs baseline noise (dB)", loc="left", fontsize=11)
    ax.set_xlabel("commanded frequency (Hz)")
    ax.set_ylabel("20·log10(tone RMS / baseline std)")

    handles, labels = axes[1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=8, bbox_to_anchor=(0.5, 1.02), fontsize=9)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def plot_spectrum(cap: dict, path: Path) -> None:
    fs, x = cap["fs"], cap["x"]
    fig, axes = plt.subplots(2, 4, figsize=(15, 6.5), sharex=True, sharey=True,
                             gridspec_kw={"hspace": 0.4, "wspace": 0.12})
    for ch, ax in enumerate(axes.flat):
        # baseline underneath, response on top slightly translucent so both noise floors show
        for sel, color, label, lw, alpha in [(cap["base"], MUTED, "baseline", 0.8, 1.0),
                                             (cap["resp"], CH_COLORS[ch], cap["cmd"], 1.0, 0.75)]:
            s = x[sel, ch] - x[sel, ch].mean()
            w = np.hanning(len(s))
            spec = np.abs(np.fft.rfft(s * w)) * 2 / w.sum()
            ax.semilogy(np.fft.rfftfreq(len(s), 1 / fs), spec, color=color, lw=lw, label=label, alpha=alpha)
        ax.set_title(f"ch{ch + 1}", loc="left", fontsize=10)
        ax.set_xlim(0, fs / 2)
        if ch >= 4:
            ax.set_xlabel("frequency (Hz)")
        if ch % 4 == 0:
            ax.set_ylabel("amplitude (a.u.)")
    axes[0, 0].legend(loc="upper right", fontsize=8)
    fig.suptitle(f"Spectrum per channel: baseline (gray) vs {cap['cmd']} (color)  ·  0 to {fs / 2:.0f} Hz (Nyquist)  ·  shared scale",
                 x=0.06, ha="left", fontsize=12)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)


# ------------------------------------------------------------------- main ----

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", type=Path)
    p.add_argument("--spectrum", type=int, nargs="*", default=[10, 22], help="commanded frequencies to plot spectra for")
    a = p.parse_args()

    out = a.folder / "analysis"
    (out / "transitions").mkdir(parents=True, exist_ok=True)
    caps = sorted((load_capture(f) for f in a.folder.glob("*.tsv")), key=lambda c: c["commanded"])
    rows = [r for c in caps for r in measure(c)]

    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    plot_summary(rows, out / "sweep_summary.png")
    for c in caps:
        plot_transition(c, out / "transitions" / f"r{c['commanded']:02d}.png")
        if c["commanded"] in a.spectrum:
            plot_spectrum(c, out / f"spectrum_r{c['commanded']:02d}.png")
    print(f"{len(caps)} captures -> {out}")


if __name__ == "__main__":
    main()
