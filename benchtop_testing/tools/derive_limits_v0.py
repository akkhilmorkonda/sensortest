"""Derive TP-001 v0 limits from DOE-1 raw data, re-cut to the test plan's exact timing.

v0 = "exploration math": this script implements the test plan's methods M1-M8
directly. When the measurement core (03) exists, limits are recomputed with it and
compared (D-019 risk 1), then frozen as v1.

Re-cut (TP-001 §4-5, M1): after each command, discard 30 data lines, take the next N.
  BL  : after the run's first `baseline`       N = 300   (all 200 runs, incl. controls)
  Tone: after `response:f`                     N = 300   (190 runs, f = 1..19 Hz)
  REC : after the run's last `baseline`        N = 150   (190 tone runs)
DOE-1 runs are baseline -> tone -> baseline, the same order as PROD (BL -> T10 -> END).
The response is flat over 1-19 Hz (DOE-1 chart 2), so tone metrics pool all 19 frequencies,
each measured at its own commanded f.

Usage:
  python benchtop_testing/tools/derive_limits_v0.py
      reads  benchtop_testing/03_doe1_repeatability/
      writes benchtop_testing/04_limits_v0_derivation/ and config/TP-001_limits_v0.yaml
      (that file now lives in config/archive/; this tool is kept as the record and isn't re-run)
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
BENCH = Path(__file__).resolve().parents[1]  # benchtop_testing/; defaults work from any cwd
REPO = BENCH.parent
from bringup_inspect import load          # noqa: E402
from serial_terminal import classify       # noqa: E402

FS_NOM = 150.0      # D-025: fixed time base
SETTLE = 30         # lines
N_WIN = 300         # lines (2 s)
N_REC = 150         # lines (1 s)
OUTLIER_K = 2.5     # M8
REF_MIN = 3         # M5: reference set size (excluding self)


# ---------------------------------------------------------------- methods M2-M8 ----
def m3_fit(x: np.ndarray, f: float):
    n = np.arange(len(x))
    w = 2 * np.pi * f * n / FS_NOM
    M = np.column_stack([np.ones_like(w), np.sin(w), np.cos(w)])
    c, *_ = np.linalg.lstsq(M, x, rcond=None)
    return c[0], float(np.hypot(c[1], c[2])), float(np.arctan2(c[2], c[1])), x - M @ c


def m4_dominant(x: np.ndarray, pad: int = 8):
    n = len(x)
    w = np.hanning(n)
    spec = np.abs(np.fft.rfft((x - x.mean()) * w, n * pad)) * 2 / w.sum()
    freqs = np.fft.rfftfreq(n * pad, 1 / FS_NOM)
    valid = freqs > 0.5
    k = int(np.argmax(np.where(valid, spec, 0)))
    if 0 < k < len(spec) - 1:
        a, b, c = np.log(spec[k - 1:k + 2] + 1e-12)
        d = 0.5 * (a - c) / (a - 2 * b + c)
    else:
        d = 0.0
    return float(freqs[k] + d * (freqs[1] - freqs[0])), float(spec[k])


def m7_longest_run(x: np.ndarray) -> int:
    best = run = 1
    for v in np.diff(x):
        run = run + 1 if v == 0 else 1
        best = max(best, run)
    return best


def m8_outliers(x: np.ndarray) -> int:
    return int((np.abs(x - x.mean()) > OUTLIER_K * x.std(ddof=1)).sum())


def wrap180(d):
    return (d + 180) % 360 - 180


# ---------------------------------------------------------------- re-cut ----
def windows(tsv: Path, cmd: str):
    meta = json.loads(tsv.with_suffix(".json").read_text())
    rows = [r for r in load(tsv)[1:] if r[1].endswith(b"\n")]
    t, x = [], []
    for tt, line in rows:
        kind, val = classify(line)
        if kind == "data":
            t.append(tt)
            x.append(val)
    t, x = np.array(t), np.array(x)
    sent = meta["sent"]

    def cut(t_cmd, n):
        i0 = int(np.searchsorted(t, t_cmd, side="right")) + SETTLE
        w = x[i0:i0 + n]
        return w if len(w) == n else None

    out = {"BL": cut(sent[0]["t_s"], N_WIN)}
    if cmd != "control":
        out["TONE"] = cut(sent[1]["t_s"], N_WIN)
        out["REC"] = cut(sent[2]["t_s"], N_REC)
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    root = BENCH / "03_doe1_repeatability"
    out_dir = BENCH / "04_limits_v0_derivation"
    out_dir.mkdir(exist_ok=True)
    with (root / "manifest.csv").open(newline="") as f:
        manifest = [m for m in csv.DictReader(f) if m["status"] == "ok"]

    rows = []   # one row per run x sensor
    for m in manifest:
        tsv = root / f"sweep{int(m['sweep']):02d}" / f"{m['file']}.tsv"
        w = windows(tsv, m["cmd"])
        f_cmd = None if m["cmd"] == "control" else float(m["cmd"])
        bl = w["BL"]
        tone_metrics = {}
        if f_cmd is not None:
            x = w["TONE"]
            fits = [m3_fit(x[:, ch], f_cmd) for ch in range(8)]
            doms = [m4_dominant(x[:, ch]) for ch in range(8)]
            tone_metrics = {"fits": fits, "doms": doms, "x": x}
        for ch in range(8):
            b = bl[:, ch]
            r = {"sweep": int(m["sweep"]), "pos": int(m["pos"]), "cmd": m["cmd"], "ch": ch + 1,
                 "BL_OFFSET": b.mean(), "BL_NOISE_STD": b.std(ddof=1), "BL_P2P": np.ptp(b),
                 "BL_STUCK_RUN": m7_longest_run(b), "BL_OUTLIERS": m8_outliers(b),
                 "BL_IDLE_AMP_10": m3_fit(b, 10.0)[1], "BL_IDLE_AMP_5": m3_fit(b, 5.0)[1],
                 "BL_IDLE_AMP_15": m3_fit(b, 15.0)[1]}
            if f_cmd is not None:
                c, A, ph, res = tone_metrics["fits"][ch]
                fd, pk = tone_metrics["doms"][ch]
                r.update({"f": f_cmd, "AMPLITUDE": A, "FREQUENCY": fd, "FREQ_ERR": fd - f_cmd,
                          "PEAK_AMP": pk, "RESIDUAL": res.std(ddof=3), "OFFSET_SHIFT": c - b.mean()})
                rec = w["REC"][:, ch]
                r.update({"REC_TONE_ABSENT": m3_fit(rec, f_cmd)[1],
                          "REC_OFFSET_SHIFT": rec.mean() - b.mean(),
                          "REC_NOISE_STD": rec.std(ddof=1), "REC_STUCK_RUN": m7_longest_run(rec),
                          "REC_OUTLIERS": m8_outliers(rec), "_phase": ph})
            rows.append(r)
        # phase vs reference (M5): reference set = sensors passing amplitude + frequency.
        # For deriving limits we use the known-healthy set as the candidate pool;
        # the line test uses each run's own pass results.
        if f_cmd is not None:
            x = tone_metrics["x"]
            run_rows = rows[-8:]
            pool = [1, 2, 4, 5, 6]          # 0-based ch2, ch3, ch5, ch6, ch7
            xm = x - x.mean(0)
            for ch in range(8):
                ref_ch = [c for c in pool if c != ch]
                if len(ref_ch) < REF_MIN:
                    continue
                ref = np.median(xm[:, ref_ch], axis=1)
                _, _, ph_ref, _ = m3_fit(ref, f_cmd)
                run_rows[ch]["PHASE"] = wrap180(np.degrees(run_rows[ch]["_phase"] - ph_ref))

    keys = sorted({k for r in rows for k in r if not k.startswith("_")},
                  key=lambda k: (k not in ("sweep", "pos", "cmd", "ch", "f"), k))
    with (out_dir / "recut_metrics.csv").open("w", newline="") as f:
        wr = csv.DictWriter(f, keys, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    print(f"wrote {out_dir / 'recut_metrics.csv'} ({len(rows)} rows)")

    limits = derive(rows)
    (REPO / "config").mkdir(exist_ok=True)
    out = REPO / "config" / "TP-001_limits_v0.yaml"
    header = (
        "# TP-001 limit set L-v0 (provisional). GENERATED by tools/derive_limits_v0.py from DOE-1;\n"
        "# edit the derivation, not this file. Thresholds only (D-044). YAML for all inputs (D-045).\n"
    )
    out.write_text(header + yaml.safe_dump(limits, sort_keys=False, allow_unicode=True), encoding="utf-8")
    report = evaluate(rows, limits)
    (out_dir / "limits_v0_report.md").write_text(report, encoding="utf-8")
    print(report)


# ---------------------------------------------------------------- limits (D-019) ----
MARGIN = 0.10        # healthy range widened by 10 %
K_REP = 3            # plus 3 x repeatability SD at the plan's window length
TONE_PRESENT = 100.0  # a.u.: one threshold for idle, tone-absent, actuator-stopped and the frequency gate
FREQ_TOL = 0.5        # Hz: one native FFT bin of a 2 s window
PHASE_TOL = 5.0       # deg (D-016)
STUCK_MAX = 5         # samples: P(run >= 5 | 1000 noise levels, 300 samples) ~ 3e-10
# Healthy population per metric = all sensors except the one(s) faulty on that metric (D-019)
POP = {"BL_OFFSET": [1, 2, 3, 4, 6, 7, 8],          # ch5 offset fault
       "BL_NOISE_STD": [1, 2, 3, 5, 6, 7, 8],       # ch4 noise fault
       "AMPLITUDE": [2, 3, 5, 6, 7],                # ch1 weak, ch4 high, ch8 ~0 at f
       "REC_OFFSET_SHIFT": [1, 2, 3, 4, 5, 6, 7, 8]}


def _vals(rows, key, ch, tone_only=False):
    return np.array([r[key] for r in rows if r["ch"] == ch and key in r
                     and (not tone_only or r["cmd"] != "control")], dtype=float)


def _floor(v, step=0.1):
    return round(float(np.floor(v / step) * step), 1)


def _ceil(v, step=0.1):
    return round(float(np.ceil(v / step) * step), 1)


def range_margin_limit(rows, key, kind, tone_only=False, window="2 s"):
    pop = POP[key]
    means = {ch: _vals(rows, key, ch, tone_only).mean() for ch in pop}
    sds = {ch: _vals(rows, key, ch, tone_only).std(ddof=1) for ch in pop}
    lo_m, hi_m = min(means.values()), max(means.values())
    if kind == "ratio":                      # amplitude, noise: scale by 10 %
        lo, hi = lo_m * (1 - MARGIN), hi_m * (1 + MARGIN)
    else:                                    # offset-like (can be ~0 or negative): 10 % of the span
        span = hi_m - lo_m
        lo, hi = lo_m - MARGIN * span, hi_m + MARGIN * span
    rep = max(sds.values())
    return {"low": _floor(lo - K_REP * rep), "high": _ceil(hi + K_REP * rep),
            "method": f"healthy range {lo_m:.2f}..{hi_m:.2f} "
                      f"({'x0.9/x1.1' if kind == 'ratio' else '+/-10% of span'}) +/- 3 x {rep:.3f} (max repeatability SD, {window})",
            "population": [f"ch{c}" for c in pop]}


def derive(rows):
    L = {"plan": "TP-001", "limit_set": "L-v0", "status": "provisional (exploration math, D-019)",
         "source": "DOE-1 raw captures re-cut to TP-001 timing (settle 30 lines, 300-line windows; REC 150)",
         "common_to_all_sensors": True, "steps": {}}
    S = L["steps"]
    S["2040 BL_OFFSET"] = range_margin_limit(rows, "BL_OFFSET", "offset") | {"unit": "a.u.", "sequences": ["PROD", "ENG"]}
    S["2050 BL_NOISE_STD"] = range_margin_limit(rows, "BL_NOISE_STD", "ratio") | {"unit": "a.u.", "sequences": ["PROD", "ENG"]}
    amp = range_margin_limit(rows, "AMPLITUDE", "ratio", tone_only=True)
    for sid in ("6040 T10_AMPLITUDE", "3040 T5_AMPLITUDE", "4040 T15_AMPLITUDE"):
        S[sid] = amp | {"unit": "a.u.", "sequences": ["PROD", "ENG"] if sid.startswith("6") else ["ENG"]}
    for sid, f in (("6050 T10_FREQUENCY", 10), ("3050 T5_FREQUENCY", 5), ("4050 T15_FREQUENCY", 15)):
        S[sid] = {"low": f - FREQ_TOL, "high": f + FREQ_TOL, "unit": "Hz",
                  "method": f"commanded f +/- {FREQ_TOL} Hz (one native FFT bin at 2 s); "
                            f"judged only if the dominant peak >= {TONE_PRESENT:g} a.u. (tone present)",
                  "sequences": ["PROD", "ENG"] if sid.startswith("6") else ["ENG"]}
    for sid in ("6060 T10_PHASE", "3060 T5_PHASE", "4060 T15_PHASE"):
        S[sid] = {"low": -PHASE_TOL, "high": PHASE_TOL, "unit": "deg",
                  "method": "design limit (D-016); judged only if amplitude passed and the reference set has >= 3 sensors",
                  "sequences": ["PROD", "ENG"] if sid.startswith("6") else ["ENG"]}
    S["2060 BL_STUCK_RUN"] = {"low": None, "high": STUCK_MAX, "unit": "samples", "sequences": ["ENG"],
                              "method": "design: P(run >= 5) ~ 3e-10 per 300-sample window with 1000 noise levels"}
    S["2070 BL_OUTLIERS"] = {"low": None, "high": 0, "unit": "count", "sequences": ["ENG"],
                             "method": "design (D-011); valid for uniform noise only (M8)"}
    S["5050 REC_TONE_ABSENT"] = {"low": None, "high": TONE_PRESENT, "unit": "a.u.", "sequences": ["ENG"],
                                 "method": "tone-present threshold"}
    S["5060 REC_OFFSET_SHIFT"] = range_margin_limit(rows, "REC_OFFSET_SHIFT", "offset", tone_only=True, window="1 s") | {
        "unit": "a.u.", "sequences": ["ENG"]}
    S["5080 REC_STUCK_RUN"] = dict(S["2060 BL_STUCK_RUN"]) | {
        "method": "design: P(run >= 5) ~ 2e-10 per 150-sample window with 1000 noise levels"}
    S["5090 REC_OUTLIERS"] = dict(S["2070 BL_OUTLIERS"])
    L["validity"] = {"1000 CONNECT": {"line_rate_hz": [148.5, 151.5],
                                      "idle_amplitude_max": TONE_PRESENT},
                     "1900 RUN_INTEGRITY": {"reply_timeout_s": 0.5, "line_rate_tolerance": 0.01,
                                            "actuator_stopped_max": TONE_PRESENT}}
    L["constants"] = {"tone_present_a.u.": TONE_PRESENT, "fs_nom_hz": FS_NOM, "settle_lines": SETTLE,
                      "window_lines": N_WIN, "rec_window_lines": N_REC}
    return L


# ---------------------------------------------------------------- evaluation ----
def evaluate(rows, L):
    S = L["steps"]
    out = ["# TP-001 limits v0: derivation check", "",
           "Source: DOE-1 re-cut to TP-001 timing. Each tone run stands in for T10 "
           "(the response is flat over 1-19 Hz), measured at its own commanded f.", ""]

    def lim(sid):
        return S[sid]["low"], S[sid]["high"]

    def inside(v, lo, hi):
        return (lo is None or v >= lo) and (hi is None or v <= hi)

    # PROD verdicts per tone run (BL from the same run)
    out += ["## PROD verdicts over 190 simulated runs (BL + one tone each)", "",
            "| Sensor | PASS runs | FAIL runs | Primary failure (count) | Steps failed (count) |", "|---|---|---|---|---|"]
    rank = {"stuck": 1, "glitch": 2, "frequency": 3, "noise": 4, "offset": 5, "gain": 6, "timing": 7}
    msg = {"frequency": "Wrong frequency", "noise": "Noisy", "offset": "Offset out of range",
           "gain": "Weak response / too strong", "timing": "Polarity / timing"}
    tone_rows = [r for r in rows if r["cmd"] != "control"]
    by_run = {}
    for r in tone_rows:
        by_run.setdefault((r["sweep"], r["pos"]), {})[r["ch"]] = r
    stats = {ch: {"pass": 0, "fail": 0, "prim": {}, "steps": {}} for ch in range(1, 9)}
    for run in by_run.values():
        amp_ok = {ch: inside(run[ch]["AMPLITUDE"], *lim("6040 T10_AMPLITUDE")) for ch in run}
        freq_judged = {ch: run[ch]["PEAK_AMP"] >= TONE_PRESENT for ch in run}
        freq_ok = {ch: (not freq_judged[ch]) or inside(run[ch]["FREQ_ERR"] + 10, *lim("6050 T10_FREQUENCY"))
                   for ch in run}
        ref_ok = [ch for ch in run if amp_ok[ch] and freq_judged[ch] and freq_ok[ch]]
        for ch, r in run.items():
            fails = []
            if not inside(r["BL_OFFSET"], *lim("2040 BL_OFFSET")):
                fails.append(("2040", "offset"))
            if not inside(r["BL_NOISE_STD"], *lim("2050 BL_NOISE_STD")):
                fails.append(("2050", "noise"))
            if not amp_ok[ch]:
                fails.append(("6040", "gain"))
            if freq_judged[ch] and not freq_ok[ch]:
                fails.append(("6050", "frequency"))
            others = [c for c in ref_ok if c != ch]
            if amp_ok[ch] and len(others) >= REF_MIN and "PHASE" in r:
                if not inside(r["PHASE"], *lim("6060 T10_PHASE")):
                    fails.append(("6060", "timing"))
            st = stats[ch]
            if fails:
                st["fail"] += 1
                p = min(fails, key=lambda x: rank[x[1]])
                st["prim"][msg[p[1]]] = st["prim"].get(msg[p[1]], 0) + 1
                for sid, _ in fails:
                    st["steps"][sid] = st["steps"].get(sid, 0) + 1
            else:
                st["pass"] += 1
    for ch, st in stats.items():
        out.append(f"| ch{ch} | {st['pass']} | {st['fail']} | "
                   f"{', '.join(f'{k} ({v})' for k, v in st['prim'].items()) or '—'} | "
                   f"{', '.join(f'{k} ({v})' for k, v in sorted(st['steps'].items())) or '—'} |")

    # margins: distance from each sensor's mean to the nearest limit, in its own SDs
    out += ["", "## Margins: distance from each sensor's mean to the nearest limit (in its own run-to-run SDs)", "",
            "Positive = inside the limits, negative = outside. Healthy sensors should sit far inside; "
            "faulty ones far outside.", "",
            "| Sensor | 2040 offset | 2050 noise | 6040 amplitude | 6050 frequency (judged runs) | 6060 phase |",
            "|---|---|---|---|---|---|"]
    for ch in range(1, 9):
        cells = []
        for key, sid, tone in (("BL_OFFSET", "2040 BL_OFFSET", False), ("BL_NOISE_STD", "2050 BL_NOISE_STD", False),
                               ("AMPLITUDE", "6040 T10_AMPLITUDE", True)):
            v = _vals(rows, key, ch, tone)
            lo, hi = lim(sid)
            m, s = v.mean(), v.std(ddof=1)
            z = min(m - lo, hi - m) / s
            cells.append(f"{z:+.1f}")
        fe = np.array([r["FREQ_ERR"] for r in tone_rows if r["ch"] == ch and r["PEAK_AMP"] >= TONE_PRESENT])
        cells.append(f"{(FREQ_TOL - np.abs(fe.mean())) / fe.std(ddof=1):+.1f} ({len(fe)} judged)" if len(fe) > 1
                     else "not judged (no tone)")
        ph = np.array([r["PHASE"] for r in tone_rows if r["ch"] == ch and "PHASE" in r])
        cells.append(f"{(PHASE_TOL - np.abs(ph.mean())) / ph.std(ddof=1):+.1f}" if ch not in (1, 8) else "not judged (amp fails)")
        out.append(f"| ch{ch} | " + " | ".join(cells) + " |")

    out += ["", "## Limits (L-v0)", "", "| Step | Low | High | Unit | Method | Sequences |", "|---|---|---|---|---|---|"]
    for sid, d in S.items():
        out.append(f"| {sid} | {d['low']} | {d['high']} | {d['unit']} | {d['method']} | {', '.join(d['sequences'])} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
