"""DOE-1 analysis: repeatability across 1-19 Hz (D-012, section 04).

Two stages:
  1. extract  - one row per run x channel with every D-011 metric -> analysis/runs.csv
                (slow: parses ~900k lines; cached, use --reextract to redo)
  2. report   - tests hypotheses H1-H8, prints/writes analysis/report.md, draws charts

Windows per run (each skips its first SETTLE_S after the command):
  pre   baseline window before the tone      (0.1 s .. 10.1 s)
  resp  tone window                          (10.1 s .. 20.1 s)
  rec   recovery window after `baseline`    (20.1 s .. end)
Control runs use the same nominal windows with no tone.

Usage:
  python benchtop_testing/tools/analyze_doe.py      # benchtop_testing/03_doe1_repeatability
  python benchtop_testing/tools/analyze_doe.py --reextract
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.ticker
import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
BENCH = Path(__file__).resolve().parents[1]  # benchtop_testing/; defaults work from any cwd
from analyze_sweep import CH_COLORS, INK2, MUTED, WASH, plt  # noqa: E402  (sets the chart style)
from bringup_inspect import load                             # noqa: E402
from bringup_segments import peak                            # noqa: E402
from serial_terminal import classify                         # noqa: E402

SETTLE_S = 0.5
REF_CH = [1, 2, 4, 5, 6]            # 0-based ch2, ch3, ch5, ch6, ch7: the phase reference set
HEALTHY = [2, 3, 5, 6, 7]           # 1-based, for population inputs to limits
SPUR_GUARD_HZ = 1.0
DC_HZ = 0.5
H1_BAND = (0.6, 1.5)                # pass band for observed / predicted SD (planned criterion)


# ------------------------------------------------------------------ extract ----

def sine_fit(y: np.ndarray, n: np.ndarray, f: float, fs: float):
    w = 2 * np.pi * f * n / fs
    M = np.column_stack([np.ones_like(w), np.sin(w), np.cos(w)])
    c, *_ = np.linalg.lstsq(M, y, rcond=None)
    return c[0], float(np.hypot(c[1], c[2])), float(np.arctan2(c[2], c[1])), y - M @ c


def joint_amp_at_f(y: np.ndarray, n: np.ndarray, f: float, fs: float) -> float:
    w = 2 * np.pi * f * n / fs
    M = np.column_stack([np.ones_like(w), np.sin(w), np.cos(w), np.sin(2 * w), np.cos(2 * w)])
    c, *_ = np.linalg.lstsq(M, y, rcond=None)
    return float(np.hypot(c[1], c[2]))


def mean_pair_corr(z: np.ndarray) -> float:
    C = np.corrcoef(z.T)
    return float(C[np.triu_indices_from(C, 1)].mean())


def spectrum(y: np.ndarray, fs: float, pad: int = 4):
    w = np.hanning(len(y))
    spec = np.abs(np.fft.rfft((y - y.mean()) * w, len(y) * pad)) * 2 / w.sum()
    return np.fft.rfftfreq(len(y) * pad, 1 / fs), spec


def base_metrics(y: np.ndarray, fs: float, prefix: str) -> dict:
    d = np.diff(y)
    run, longest = 1, 1
    for v in d:
        run = run + 1 if v == 0 else 1
        longest = max(longest, run)
    f, s = spectrum(y, fs)
    return {
        f"{prefix}_n": len(y), f"{prefix}_offset": y.mean(), f"{prefix}_std": y.std(ddof=1),
        f"{prefix}_min": y.min(), f"{prefix}_max": y.max(), f"{prefix}_p2p": np.ptp(y),
        f"{prefix}_equal_pairs": int((d == 0).sum()), f"{prefix}_longest_run": longest,
        f"{prefix}_noise_peak": float(s[f > DC_HZ].max()),
    }


def extract_run(tsv: Path, manifest_row: dict, t_doe0: float) -> list[dict]:
    meta = json.loads(tsv.with_suffix(".json").read_text())
    rows = [r for r in load(tsv)[1:] if r[1].endswith(b"\n")]
    data = [(t, v) for t, line in rows for kind, v in [classify(line)] if kind == "data"]
    t = np.array([d[0] for d in data])
    x = np.array([d[1] for d in data])
    n = np.arange(len(t))
    fs = float(np.polyfit(t, n, 1)[0])
    sent = meta["sent"]
    t0 = sent[0]["t_s"]
    control = manifest_row["cmd"] == "control"
    t_on, t_off = (t0 + 10.0, t0 + 20.0) if control else (sent[1]["t_s"], sent[2]["t_s"])
    w = {"pre": (t >= t0 + SETTLE_S) & (t < t_on),
         "resp": (t >= t_on + SETTLE_S) & (t < t_off),
         "rec": t >= t_off + SETTLE_S}
    early = np.where((t > t_off) & (t < t_off + SETTLE_S))[0]
    f_cmd = None if control else float(manifest_row["cmd"])

    # phase reference: per-sample median of the de-meaned reference channels, leaving the channel
    # itself out (a reference channel is compared with the median of the other four)
    xp = x[w["pre"]][:, REF_CH]
    xp = xp - xp.mean(0)
    pre_corr = mean_pair_corr(xp)
    refs = {}
    for ch in range(8):
        keep = [i for i, c in enumerate(REF_CH) if c != ch]
        refs[ch] = {"pre_std": np.median(xp[:, keep], axis=1).std(ddof=1)}
    if not control:
        xr = x[w["resp"]][:, REF_CH]
        xr = xr - xr.mean(0)
        for ch in range(8):
            keep = [i for i, c in enumerate(REF_CH) if c != ch]
            _, a_, p_, r_ = sine_fit(np.median(xr[:, keep], axis=1), n[w["resp"]], f_cmd, fs)
            refs[ch].update(amp=a_, ph=p_, res_std=r_.std(ddof=3))
        # is the extra noise while excited common to all channels? correlate the residuals
        resid = np.column_stack([sine_fit(x[w["resp"], c], n[w["resp"]], f_cmd, fs)[3] for c in REF_CH])
        res_corr = mean_pair_corr(resid)

    start = meta["started"]
    out = []
    for ch in range(8):
        r = {"sweep": int(manifest_row["sweep"]), "pos": int(manifest_row["pos"]),
             "cmd": manifest_row["cmd"], "file": tsv.stem, "fs": fs, "ch": ch + 1,
             "t_min": (t_doe0_offset(start, t_doe0))}
        y = x[:, ch]
        r.update(base_metrics(y[w["pre"]], fs, "pre"))
        r.update(base_metrics(y[w["rec"]], fs, "rec"))
        # early recovery: samples in the first SETTLE_S after `baseline` outside the pre band
        lo, hi = r["pre_min"], r["pre_max"]
        tol = 0.01 * (hi - lo)
        bad = np.where((y[early] < lo - tol) | (y[early] > hi + tol))[0]
        r["rec_early_out"] = len(bad)
        r["rec_early_last_out"] = int(bad.max()) if len(bad) else -1
        ys, ns = y[w["resp"]], n[w["resp"]]
        if control:
            r.update(base_metrics(ys, fs, "resp"))      # a no-tone window: should look like baseline
        else:
            tone = f_cmd * (2 if ch == 7 else 1)
            off, amp, ph, res = sine_fit(ys, ns, tone, fs)
            half = len(ys) // 2
            a1 = sine_fit(ys[:half], ns[:half], tone, fs)[1]
            a2 = sine_fit(ys[half:], ns[half:], tone, fs)[1]
            ff, s = spectrum(res, fs)       # spurs measured on the residual, tone removed
            guard = (ff > DC_HZ) & (np.abs(ff - tone) > SPUR_GUARD_HZ)
            k = int(np.argmax(np.where(guard, s, 0)))
            f_dom = peak(ys - ys.mean(), fs, pad=8)[0]
            r.update({
                "resp_n": len(ys), "resp_offset": off, "amp": amp, "tone_hz": tone,
                "f_dom": f_dom, "res_std": res.std(ddof=3), "res_maxabs": float(np.abs(res).max()),
                "half_ratio_pct": (a2 / a1 - 1) * 100 if a1 > 0 else np.nan,
                "spur_amp": float(s[k]), "spur_hz": float(ff[k]),
                # component at the commanded f; for ch8 fitted jointly with its 2f tone so the
                # tone does not leak into it (a single-frequency fit leaks when cycles are non-integer)
                "amp_at_f": (joint_amp_at_f(ys, ns, f_cmd, fs) if ch == 7 else amp),
                "phase_deg": np.degrees(ph) if ch != 7 else np.nan,
                "dphase_deg": (np.degrees((ph - refs[ch]["ph"] + np.pi) % (2 * np.pi) - np.pi)
                               if ch != 7 else np.nan),
                "ref_amp": refs[ch]["amp"], "ref_res_std": refs[ch]["res_std"], "ref_res_corr": res_corr,
            })
        r["ref_pre_std"] = refs[ch]["pre_std"]
        r["ref_pre_corr"] = pre_corr
        out.append(r)
    return out


def t_doe0_offset(started: str, t_doe0: float) -> float:
    from datetime import datetime
    return (datetime.strptime(started, "%Y%m%d_%H%M%S").timestamp() - t_doe0) / 60


def extract(root: Path, out_csv: Path) -> None:
    from datetime import datetime
    with (root / "manifest.csv").open(newline="") as f:
        manifest = [m for m in csv.DictReader(f) if m["status"] == "ok"]
    t_doe0 = datetime.fromisoformat(json.loads((root / "run_info.json").read_text())["started"]).timestamp()
    rows = []
    for i, m in enumerate(manifest, 1):
        tsv = root / f"sweep{int(m['sweep']):02d}" / f"{m['file']}.tsv"
        rows += extract_run(tsv, m, t_doe0)
        if i % 20 == 0:
            print(f"  extracted {i}/{len(manifest)} runs", flush=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with out_csv.open("w", newline="") as f:
        wr = csv.DictWriter(f, keys)
        wr.writeheader()
        wr.writerows(rows)


# ------------------------------------------------------------------- report ----

def load_rows(path: Path) -> list[dict]:
    out = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            d = {}
            for k, v in r.items():
                if k in ("cmd", "file"):
                    d[k] = v
                else:
                    try:
                        d[k] = float(v) if v != "" else np.nan
                    except ValueError:
                        d[k] = v
            d["ch"] = int(d["ch"])
            d["sweep"] = int(d["sweep"])
            d["pos"] = int(d["pos"])
            d["f"] = np.nan if d["cmd"] == "control" else float(d["cmd"])
            out.append(d)
    return out


def pooled_within(values: np.ndarray, groups: np.ndarray):
    """Pooled within-group SD, its dof, group means, and a one-way ANOVA F test across groups."""
    ug = np.unique(groups)
    ss, dof, means, sizes = 0.0, 0, [], []
    for g in ug:
        v = values[groups == g]
        ss += ((v - v.mean()) ** 2).sum()
        dof += len(v) - 1
        means.append(v.mean())
        sizes.append(len(v))
    sd = np.sqrt(ss / dof)
    means, sizes = np.array(means), np.array(sizes)
    grand = values.mean()
    ms_between = (sizes * (means - grand) ** 2).sum() / (len(ug) - 1)
    F = ms_between / sd ** 2
    p = stats.f.sf(F, len(ug) - 1, dof)
    return sd, dof, dict(zip(ug, means)), F, p


def sd_ci(ratio: float, dof: int):
    lo = ratio * np.sqrt(dof / stats.chi2.ppf(0.975, dof))
    hi = ratio * np.sqrt(dof / stats.chi2.ppf(0.025, dof))
    return lo, hi


def trend(t: np.ndarray, y: np.ndarray):
    res = stats.linregress(t, y)
    return res.slope, res.stderr, res.pvalue


def report(rows: list[dict], out: Path) -> dict:
    by = defaultdict(list)
    for r in rows:
        by[r["ch"]].append(r)
    tone = {ch: [r for r in rs if r["cmd"] != "control"] for ch, rs in by.items()}
    ctrl = {ch: [r for r in rs if r["cmd"] == "control"] for ch, rs in by.items()}
    L = []
    res = {"h1": {}, "flat": {}, "drift": {}, "carry": {}, "jitter": {}, "rec": {}, "const": {}, "faults": {}}

    def col(rs, k):
        return np.array([r[k] for r in rs], dtype=float)

    # ---- jitter model (needed by H1's predictions; reported under H7) ---------------
    # Extra residual while excited, per run: V = σ_res² − σ_pre². Model it as amplitude jitter
    # (y = A(1+ε)sin) plus timing jitter (y = A sin(ω(t+δ))): V = A²(σ_a² + ω²σ_t²)/2.
    # In % of A: excess%² = a0 + b0·f², with σ_a = √(2 a0) % and σ_t = √(2 b0)/(2π·100) s.
    for ch in range(1, 9):
        ts = tone[ch]
        ex = np.sqrt(np.clip(col(ts, "res_std") ** 2 - col(ts, "pre_std") ** 2, 0, None)) / col(ts, "amp") * 100
        f = col(ts, "f")
        s, se, p = trend(f, ex)
        res["jitter"][ch] = dict(f=f, ex=ex, mean=ex.mean(), s=s, p=p)
    fx = np.concatenate([res["jitter"][c]["f"] for c in (2, 3, 5, 6, 7)])
    ey = np.concatenate([res["jitter"][c]["ex"] for c in (2, 3, 5, 6, 7)])
    (a0, b0), *_ = np.linalg.lstsq(np.column_stack([np.ones_like(fx), fx ** 2]), ey ** 2, rcond=None)
    jm = res["jitter_model"] = dict(a0=a0, b0=b0, sig_a=np.sqrt(2 * a0),
                                    sig_t=np.sqrt(2 * b0) / (2 * np.pi) / 100)

    def split_jitter(rs, ftone, amp_key="amp", res_key="res_std", pre_key="pre_std"):
        """Per run: (V_a, V_t) = amplitude- and timing-jitter parts of the extra residual variance."""
        A = col(rs, amp_key)
        V = np.clip(col(rs, res_key) ** 2 - col(rs, pre_key) ** 2, 0, None)
        Vt = np.minimum(A ** 2 * b0 * ftone ** 2 / 1e4, V)
        return V - Vt, Vt

    # ---- H1: repeatability vs noise-only prediction ---------------------------------
    L.append("## H1: observed vs predicted run-to-run SD\n")
    L.append("Observed SD: pre-window offset and noise over all 200 runs; tone metrics pooled within "
             "frequency (171 dof). Predicted SD from each run's own noise:\n")
    L.append("- offset σ/√N; noise std σ·√(0.2/N) (uniform noise);")
    L.append("- amplitude √((2σ² + 3V_a + V_t)/N), where σ is the baseline noise and V_a, V_t are the "
             "amplitude- and timing-jitter parts of the extra noise while excited (H7). Amplitude jitter "
             "moves an amplitude estimate 1.5× as much as white noise of the same size; timing jitter "
             "half as much;")
    L.append("- phase vs reference (reference = median of the other reference channels): own "
             "(2σ² + V_a)/(N·A²) plus the reference's equivalent. The timing jitter is common to all "
             "channels (H7), so it shifts the channel and the reference together and cancels.\n")
    L.append("| Ch | Metric | Mean | Observed SD | Predicted SD | Ratio (95% CI) | Planned check 0.6–1.5 |")
    L.append("|---|---|---|---|---|---|---|")
    for ch in range(1, 9):
        rs, ts = by[ch], tone[ch]
        N = col(rs, "pre_n")
        sig = col(rs, "pre_std")
        f = col(ts, "f")
        entries = []
        off = col(rs, "pre_offset")
        entries.append(("offset", off.mean(), off.std(ddof=1), np.sqrt(np.mean(sig ** 2 / N)), len(off) - 1))
        entries.append(("noise std", sig.mean(), sig.std(ddof=1), np.sqrt(np.mean(sig ** 2 * 0.2 / N)), len(sig) - 1))
        amp = col(ts, "amp")
        Nr = col(ts, "resp_n")
        s_pre = col(ts, "pre_std")
        ftone = f * (2 if ch == 8 else 1)
        Va, Vt = split_jitter(ts, ftone)
        sd, dof, *_ = pooled_within(amp, f)
        entries.append(("amplitude", amp.mean(), sd, np.sqrt(np.mean((2 * s_pre ** 2 + 3 * Va + Vt) / Nr)), dof))
        if ch != 8:
            dph = col(ts, "dphase_deg")
            sd, dof, *_ = pooled_within(dph, f)
            Ra, Rt = split_jitter(ts, f, "ref_amp", "ref_res_std", "ref_pre_std")
            A_ref, r_pre = col(ts, "ref_amp"), col(ts, "ref_pre_std")
            common = (2 * s_pre ** 2 + Va) / (Nr * amp ** 2) + (2 * r_pre ** 2 + Ra) / (Nr * A_ref ** 2)
            indep = common + 3 * Vt / (Nr * amp ** 2) + 3 * Rt / (Nr * A_ref ** 2)
            res["h1_phase_alt"] = res.get("h1_phase_alt", {})
            res["h1_phase_alt"][ch] = sd / np.degrees(np.sqrt(np.mean(indep)))
            entries.append(("phase vs ref (°)", dph.mean(), sd, np.degrees(np.sqrt(np.mean(common))), dof))
        for name, mean, obs, pred, dof in entries:
            ratio = obs / pred
            lo, hi = sd_ci(ratio, dof)
            ok = H1_BAND[0] <= ratio <= H1_BAND[1]
            res["h1"][(ch, name)] = dict(mean=mean, obs=obs, pred=pred, ratio=ratio, lo=lo, hi=hi, ok=ok)
            L.append(f"| ch{ch} | {name} | {mean:.2f} | {obs:.3f} | {pred:.3f} | {ratio:.2f} ({lo:.2f}–{hi:.2f}) | "
                     f"{'pass' if ok else '**FAIL**'} |")
    alt = res["h1_phase_alt"]
    L.append("\nIf the timing jitter were *not* common (no cancellation), the phase ratios would be: "
             + ", ".join(f"ch{c} {alt[c]:.2f}" for c in sorted(alt)) + ".")
    # does run-to-run amplitude variation move all channels together (a common gain change)?
    devs = []
    for ch in [c + 1 for c in REF_CH]:
        ts = sorted(tone[ch], key=lambda r: (r["sweep"], r["pos"]))
        amp, f = col(ts, "amp"), col(ts, "f")
        _, _, means, *_ = pooled_within(amp, f)
        devs.append((amp - np.array([means[v] for v in f])) / amp.mean())
    C = np.corrcoef(np.array(devs))
    pc = C[np.triu_indices_from(C, 1)]
    res["amp_corr"] = pc
    L.append(f"\nAmplitude deviations (from each frequency's mean) correlated between the reference channels "
             f"across the 190 runs: mean r = {pc.mean():+.2f} (range {pc.min():+.2f} … {pc.max():+.2f}; "
             f"independent ≈ 0 ± {2 / np.sqrt(190):.2f}). A common run-to-run gain change would show as r > 0.\n")

    # ---- flatness across frequency (justifies pooling) ------------------------------
    L.append("## Is the response flat across 1–19 Hz? (one-way ANOVA on amplitude by frequency)\n")
    L.append("| Ch | Amplitude mean | Spread of the 19 frequency means (max−min) | As % of mean | F | p |")
    L.append("|---|---|---|---|---|---|")
    for ch in range(1, 9):
        ts = tone[ch]
        amp, f = col(ts, "amp"), col(ts, "f")
        sd, dof, means, F, p = pooled_within(amp, f)
        mv = np.array(list(means.values()))
        res["flat"][ch] = dict(means=means, F=F, p=p, sd=sd)
        L.append(f"| ch{ch} | {amp.mean():.2f} | {np.ptp(mv):.2f} | {np.ptp(mv) / amp.mean() * 100:.3f}% | {F:.2f} | {p:.2g} |")
    L.append("")

    # ---- H2: known faults constant -------------------------------------------------
    L.append("## H2: are the known faults constant in every run?\n")
    ch1a = col(tone[1], "amp")
    ch4n = col(by[4], "pre_std")
    ch8f = col(tone[8], "f_dom") / col(tone[8], "f")
    ch8_at_f = col(tone[8], "amp_at_f")
    ch5o = col(by[5], "pre_offset")
    res["const"] = dict(ch1=(ch1a.min(), ch1a.max()), ch4=(ch4n.min(), ch4n.max()),
                        ch8=(ch8f.min(), ch8f.max()), ch5=(ch5o.min(), ch5o.max()))
    L.append(f"- ch1 amplitude at f, 190 runs: {ch1a.min():.1f} – {ch1a.max():.1f} (mean {ch1a.mean():.1f}); "
             f"healthy channels are ≥ 800.")
    L.append(f"- ch4 noise std, 200 runs: {ch4n.min():.1f} – {ch4n.max():.1f}; the next noisiest channel is "
             f"{max(col(by[c], 'pre_std').max() for c in (1, 2, 3, 5, 6, 7, 8)):.1f}.")
    L.append(f"- ch8 dominant frequency ÷ commanded: {ch8f.min():.4f} – {ch8f.max():.4f} in all 190 runs. "
             f"ch8's component at the commanded f: {ch8_at_f.mean():.2f} ± {ch8_at_f.std():.2f} "
             f"(noise-only expectation ≈ {np.mean(col(tone[8], 'res_std') * np.sqrt(np.pi / (2 * col(tone[8], 'resp_n')))):.2f}).")
    L.append(f"- ch5 offset: {ch5o.min():.1f} – {ch5o.max():.1f}.\n")

    # ---- H3: new faults -----------------------------------------------------------
    L.append("## H3: any new faults? (outliers, stuck values, spurs)\n")
    L.append("Expected with 1,000 equally likely noise levels: ~N/1000 equal neighbour pairs per window, "
             "and runs of 3+ identical values almost never. Outliers: values outside the channel's "
             "pooled baseline band by more than 1% of the band. Spurs: largest residual spectrum peak "
             "(outside ±1 Hz of the tone) normalised by the window's noise, compared with the same "
             "statistic in the pre-baseline window (ratio ≈ 1 if there is no spur).\n")
    L.append("| Ch | Band (pooled pre) | Samples outside band (all windows) | Equal pairs per window (exp.) | Longest identical run | Spur ratio median (max) | Control 'resp' window peak ratio (max) |")
    L.append("|---|---|---|---|---|---|---|")
    for ch in range(1, 9):
        rs, ts, cs = by[ch], tone[ch], ctrl[ch]
        lo = np.median(col(rs, "pre_min"))
        hi = np.median(col(rs, "pre_max"))
        tol = 0.01 * (hi - lo)
        outside = int(sum((r["pre_min"] < lo - tol) + (r["pre_max"] > hi + tol)
                          + (r["rec_min"] < lo - tol) + (r["rec_max"] > hi + tol) for r in rs))
        eq = np.concatenate([col(rs, "pre_equal_pairs"), col(rs, "rec_equal_pairs")])
        exp_eq = np.mean(col(rs, "pre_n")) / 1000
        longest = int(max(col(rs, "pre_longest_run").max(), col(rs, "rec_longest_run").max()))
        spur_norm = (col(ts, "spur_amp") / col(ts, "res_std")) / (
            np.array([r["pre_noise_peak"] for r in ts]) / col(ts, "pre_std"))
        cpk = col(cs, "resp_noise_peak") / col(cs, "pre_noise_peak")
        res["faults"][ch] = dict(outside=outside, eq=eq.mean(), longest=longest,
                                 spur_med=np.median(spur_norm), spur_max=spur_norm.max(), ctrl_max=cpk.max())
        L.append(f"| ch{ch} | {lo:.2f} … {hi:.2f} | {outside} windows | {eq.mean():.2f} ({exp_eq:.2f}) | {longest} | "
                 f"{np.median(spur_norm):.2f} ({spur_norm.max():.2f}) | {cpk.max():.2f} |")
    L.append("\n(The 'samples outside band' column counts windows whose min or max sits outside the pooled band.)\n")

    # ---- H4: recovery --------------------------------------------------------------
    L.append("## H4: recovery after `baseline` (rec window vs pre window, same run)\n")
    L.append("| Ch | Offset change rec−pre: mean (SD) | z-score SD (exp. 1) | Max abs z | Noise ratio rec/pre: mean (min–max) | Runs with a sample outside band in first 0.5 s | Latest such sample |")
    L.append("|---|---|---|---|---|---|---|")
    for ch in range(1, 9):
        ts = tone[ch]
        d = col(ts, "rec_offset") - col(ts, "pre_offset")
        se = np.sqrt(col(ts, "pre_std") ** 2 / col(ts, "pre_n") + col(ts, "rec_std") ** 2 / col(ts, "rec_n"))
        z = d / se
        nr = col(ts, "rec_std") / col(ts, "pre_std")
        eo = col(ts, "rec_early_out")
        last = col(ts, "rec_early_last_out")
        res["rec"][ch] = dict(z_sd=z.std(), z_max=np.abs(z).max(), early_runs=int((eo > 0).sum()), last=int(last.max()))
        L.append(f"| ch{ch} | {d.mean():+.3f} ({d.std():.3f}) | {z.std():.2f} | {np.abs(z).max():.2f} | "
                 f"{nr.mean():.3f} ({nr.min():.3f}–{nr.max():.3f}) | {int((eo > 0).sum())}/190 | "
                 f"{'sample #' + str(int(last.max())) if last.max() >= 0 else '—'} |")
    L.append("\n(Sample # counts from the first line received after `baseline` was sent; #0–1 can still "
             "be lines the device produced before it processed the command.)\n")

    # ---- H5: drift ----------------------------------------------------------------
    L.append("## H5: drift over the 101 min run\n")
    L.append("Slope of (value − its frequency's mean) against time; total change = slope × 101 min.\n")
    L.append("| Ch | Offset slope (/h) | Offset change over run | p | Amplitude slope (/h) | Amplitude change over run (% of A) | p | Control-run offsets (10): range |")
    L.append("|---|---|---|---|---|---|---|---|")
    dur = max(r["t_min"] for r in rows) / 60
    for ch in range(1, 9):
        rs, ts, cs = by[ch], tone[ch], ctrl[ch]
        so, seo, po = trend(col(rs, "t_min") / 60, col(rs, "pre_offset"))
        amp, f = col(ts, "amp"), col(ts, "f")
        _, _, means, *_ = pooled_within(amp, f)
        dev = amp - np.array([means[v] for v in f])
        sa, sea, pa = trend(col(ts, "t_min") / 60, dev)
        co = col(cs, "pre_offset")
        res["drift"][ch] = dict(so=so, po=po, sa=sa, pa=pa, dur=dur)
        L.append(f"| ch{ch} | {so:+.3f} | {so * dur:+.3f} | {po:.2g} | {sa:+.3f} | {sa * dur / amp.mean() * 100:+.3f}% | "
                 f"{pa:.2g} | {co.min():.2f} … {co.max():.2f} |")
    L.append("")
    # a straight-line fit misses wander that goes up and down; compare the 10 sweep means (~10 min blocks)
    L.append("Non-linear wander: one-way ANOVA of the amplitude deviation by sweep (10 blocks of ~10 min). "
             "Between-sweep SD is the wander left after removing what noise explains.\n")
    L.append("| Ch | F | p | Between-sweep SD (% of A) | Within-sweep SD (% of A) |")
    L.append("|---|---|---|---|---|")
    for ch in range(1, 9):
        ts = tone[ch]
        amp, f, sw = col(ts, "amp"), col(ts, "f"), col(ts, "sweep")
        _, _, means, *_ = pooled_within(amp, f)
        dev = (amp - np.array([means[v] for v in f])) / amp.mean() * 100
        F, p = stats.f_oneway(*[dev[sw == s] for s in np.unique(sw)])
        within = np.mean([dev[sw == s].var(ddof=1) for s in np.unique(sw)])
        between = np.var([dev[sw == s].mean() for s in np.unique(sw)], ddof=1) - within / (len(dev) / len(np.unique(sw)))
        res["drift"][ch].update(sweep_F=F, sweep_p=p, between_sd=np.sqrt(max(between, 0)))
        L.append(f"| ch{ch} | {F:.2f} | {p:.2g} | {np.sqrt(max(between, 0)):.4f}% | {np.sqrt(within):.4f}% |")
    L.append("\n(8 tests: p < 0.006 to count.)\n")

    # ---- H6: carryover ------------------------------------------------------------
    L.append("## H6: carryover from the previous run's frequency\n")
    L.append("One-way ANOVA of each run's deviation (from its own frequency's mean) grouped by the previous "
             "run's command (control = 0). The first run of the DOE has no predecessor and is left out.\n")
    order = sorted({(r["sweep"], r["pos"]) for r in rows})
    prev_cmd = {}
    cmd_of = {(r["sweep"], r["pos"]): r["cmd"] for r in rows}
    for a, b in zip(order, order[1:]):
        prev_cmd[b] = 0.0 if cmd_of[a] == "control" else float(cmd_of[a])
    L.append("| Ch | Amplitude: F (p) | Pre-baseline offset: F (p) |")
    L.append("|---|---|---|")
    for ch in range(1, 9):
        ts = [r for r in tone[ch] if (r["sweep"], r["pos"]) in prev_cmd]
        amp, f = col(ts, "amp"), col(ts, "f")
        _, _, means, *_ = pooled_within(amp, f)
        dev = amp - np.array([means[v] for v in f])
        g = np.array([prev_cmd[(r["sweep"], r["pos"])] for r in ts])
        _, _, _, Fa, pa = pooled_within(dev, g)
        rs = [r for r in by[ch] if (r["sweep"], r["pos"]) in prev_cmd]
        g2 = np.array([prev_cmd[(r["sweep"], r["pos"])] for r in rs])
        _, _, _, Fo, po = pooled_within(col(rs, "pre_offset"), g2)
        res["carry"][ch] = dict(Fa=Fa, pa=pa, Fo=Fo, po=po)
        L.append(f"| ch{ch} | {Fa:.2f} ({pa:.2g}) | {Fo:.2f} ({po:.2g}) |")
    L.append("\n(With 16 tests, one p < 0.05 is expected by chance; look for p < 0.003, the Bonferroni level.)\n")

    # ---- H7: amplitude jitter ----------------------------------------------------
    L.append("## H7: extra noise while excited (jitter), vs frequency\n")
    L.append("Excess = √(σ_res² − σ_pre²) as % of the tone amplitude. Slope vs frequency tests whether it "
             "grows with f (timing jitter) or stays flat (amplitude jitter).\n")
    L.append("| Ch | Excess, % of A: mean (SD) | At 1–3 Hz | At 17–19 Hz | Slope (%/Hz) | p |")
    L.append("|---|---|---|---|---|---|")
    for ch in range(1, 9):
        j = res["jitter"][ch]
        ex, f = j["ex"], j["f"]
        L.append(f"| ch{ch} | {ex.mean():.2f} ({ex.std():.2f}) | {ex[f <= 3].mean():.2f} | {ex[f >= 17].mean():.2f} | "
                 f"{j['s']:+.4f} | {j['p']:.2g} |")
    L.append("\n(ch1's tone is below its noise and ch4's noise is ~190, so their excess is not meaningful.)\n")
    j8 = res["jitter"][8]
    f8 = j8["f"] * 2
    ch8_pred = np.sqrt(a0 + b0 * f8 ** 2)
    L.append(f"**Jitter model**, fitted on ch2, ch3, ch5, ch6, ch7 (tones 1–19 Hz): excess%² = a0 + b0·f², "
             f"i.e. **amplitude jitter σ_a = {jm['sig_a']:.2f}% rms** and **timing jitter σ_t = "
             f"{jm['sig_t'] * 1e6:.0f} µs rms** per sample (the residual of a jittered sine carries half of "
             f"each, hence the √2). Prediction for ch8, whose tone is at 2f = 2–38 Hz and was not used in "
             f"the fit: {ch8_pred.mean():.2f}% mean vs {j8['ex'].mean():.2f}% measured; at 34–38 Hz "
             f"{ch8_pred[f8 >= 34].mean():.2f}% vs {j8['ex'][f8 >= 34].mean():.2f}%.\n")
    allr = sorted([r for r in rows if r["ch"] == 2 and r["cmd"] != "control"], key=lambda r: r["f"])
    rc = col(allr, "ref_res_corr")
    fr = col(allr, "f")
    pcorr = col([r for r in rows if r["ch"] == 2], "ref_pre_corr")

    # expected residual correlation between two reference channels a, b under two hypotheses:
    #  all jitter shared:           r = √V_a·√V_b / (σres_a σres_b) with V the whole excess
    #  only timing jitter shared:   r = √Vt_a·√Vt_b / (σres_a σres_b)
    def expected_r(fsel, timing_only):
        out = []
        chs = [c + 1 for c in REF_CH]
        for i, a in enumerate(chs):
            for b in chs[i + 1:]:
                ra = [r for r in tone[a] if r["f"] in fsel]
                rb = [r for r in tone[b] if r["f"] in fsel]
                Aa, Ab = col(ra, "amp").mean(), col(rb, "amp").mean()
                sa, sb = col(ra, "res_std").mean(), col(rb, "res_std").mean()
                ff = np.mean([v ** 2 for v in fsel])
                if timing_only:
                    va, vb = Aa ** 2 * b0 * ff / 1e4, Ab ** 2 * b0 * ff / 1e4
                else:
                    va, vb = Aa ** 2 * (a0 + b0 * ff) / 1e4, Ab ** 2 * (a0 + b0 * ff) / 1e4
                out.append(np.sqrt(va * vb) / (sa * sb))
        return float(np.mean(out))

    lo_f, hi_f = {1.0, 2.0, 3.0}, {17.0, 18.0, 19.0}
    res["res_corr"] = dict(resp=rc.mean(), pre=pcorr.mean(),
                           lo=rc[np.isin(fr, list(lo_f))].mean(), hi=rc[np.isin(fr, list(hi_f))].mean(),
                           exp_all_lo=expected_r(lo_f, False), exp_all_hi=expected_r(hi_f, False),
                           exp_t_lo=expected_r(lo_f, True), exp_t_hi=expected_r(hi_f, True))
    c = res["res_corr"]
    L.append("**Is the jitter common to all channels?** Mean pairwise correlation of the reference channels' "
             "residuals while excited (baseline noise: r = " f"{pcorr.mean():+.3f}):\n")
    L.append("| Tone frequency | Measured r | Expected if all jitter is shared | Expected if only the timing jitter is shared |")
    L.append("|---|---|---|---|")
    L.append(f"| 1–3 Hz | {c['lo']:+.3f} | {c['exp_all_lo']:.3f} | {c['exp_t_lo']:.3f} |")
    L.append(f"| 17–19 Hz | {c['hi']:+.3f} | {c['exp_all_hi']:.3f} | {c['exp_t_hi']:.3f} |")
    L.append("")

    # ---- inputs for limits --------------------------------------------------------
    L.append("## Inputs for limits (not limits)\n")
    L.append("Per channel: the mean and repeatability SD (pooled within frequency). Limit choice stays an open "
             "decision (D-011).\n")
    L.append("| Ch | Offset | Noise std | Amplitude at its tone | Amplitude repeatability SD | Residual std | Phase vs ref (°) |")
    L.append("|---|---|---|---|---|---|---|")
    for ch in range(1, 9):
        h = res["h1"]
        ph = h.get((ch, "phase vs ref (°)"))
        L.append(f"| ch{ch} | {h[(ch, 'offset')]['mean']:.2f} | {h[(ch, 'noise std')]['mean']:.2f} | "
                 f"{h[(ch, 'amplitude')]['mean']:.2f}{' (2f)' if ch == 8 else ''} | {h[(ch, 'amplitude')]['obs']:.3f} | "
                 f"{col(tone[ch], 'res_std').mean():.2f} | "
                 f"{(f'{ph['mean']:+.3f} ± {ph['obs']:.3f}') if ph else 'n/a'} |")
    hp = [res["h1"][(c, "amplitude")]["mean"] for c in HEALTHY]
    hn = [res["h1"][(c, "noise std")]["mean"] for c in HEALTHY]
    L.append(f"\nHealthy population (ch{', ch'.join(map(str, HEALTHY))}): amplitude {min(hp):.1f} – {max(hp):.1f}; "
             f"noise std {min(hn):.1f} – {max(hn):.1f}.\n")

    (out / "report.md").write_text("# DOE-1 analysis (generated by tools/analyze_doe.py)\n\n" + "\n".join(L),
                                   encoding="utf-8")
    print("\n".join(L))
    return res


# ------------------------------------------------------------------- charts ----

def chart_sd_ratio(res: dict, path: Path) -> None:
    metrics = ["offset", "noise std", "amplitude", "phase vs ref (°)"]
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.6), sharey=True)
    for ax, m in zip(axes, metrics):
        ax.axvspan(*H1_BAND, color=WASH, zorder=0)
        ax.axvline(1, color=MUTED, lw=0.8, zorder=1)
        for ch in range(1, 9):
            e = res["h1"].get((ch, m))
            if not e:
                continue
            y = 9 - ch
            ax.plot([e["lo"], e["hi"]], [y, y], color=CH_COLORS[ch - 1], lw=2, solid_capstyle="round", zorder=2)
            ax.plot(e["ratio"], y, "o", ms=7, color=CH_COLORS[ch - 1], mec="white", mew=1.5, zorder=3)
        ax.set_title(m, fontsize=10, loc="left")
        ax.set_xscale("log")
        ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        ax.set_xticks([0.6, 0.8, 1, 1.25, 1.5])
        ax.set_xticklabels(["0.6", "0.8", "1", "1.25", "1.5"])
        ax.set_xlim(0.5, 1.8)
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(range(1, 9))
    axes[0].set_yticklabels([f"ch{9 - i}" for i in range(1, 9)])
    fig.supxlabel("Observed SD ÷ predicted SD (dot), with 95% interval; shaded = planned pass band 0.6–1.5",
                  fontsize=9, color=INK2)
    fig.suptitle("H1 · Is each channel as repeatable as its noise and jitter predict?", x=0.01, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_drift(rows: list[dict], res: dict, path: Path) -> None:
    fig, axes = plt.subplots(2, 4, figsize=(13, 5.6), sharex=True)
    for ch, ax in zip(range(1, 9), axes.flat):
        ts = [r for r in rows if r["ch"] == ch and r["cmd"] != "control"]
        amp = np.array([r["amp"] for r in ts])
        f = np.array([r["f"] for r in ts])
        means = res["flat"][ch]["means"]
        dev = (amp - np.array([means[v] for v in f])) / amp.mean() * 100
        t = np.array([r["t_min"] for r in ts])
        pred = res["h1"][(ch, "amplitude")]["pred"] / amp.mean() * 100
        ax.axhspan(-2 * pred, 2 * pred, color=WASH, zorder=0)
        ax.plot(t, dev, "o", ms=3.2, color=CH_COLORS[ch - 1], alpha=0.85, zorder=2)
        d = res["drift"][ch]
        tt = np.array([0, t.max()])
        ax.plot(tt, dev.mean() + d["sa"] * (tt / 60 - t.mean() / 60) / amp.mean() * 100, color=INK2, lw=1, zorder=3)
        ax.set_title(f"ch{ch}{' (at 2f)' if ch == 8 else ''}   p = {d['pa']:.2g}", fontsize=9.5, loc="left")
    for ax in axes[1]:
        ax.set_xlabel("Time since DOE start (min)")
    for ax in axes[:, 0]:
        ax.set_ylabel("Amplitude − its frequency's mean\n(% of amplitude)")
    fig.suptitle("H5 · Amplitude over the 101 min run (shaded = ±2 predicted SD; line = fitted trend)",
                 x=0.01, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_offset_drift(rows: list[dict], path: Path) -> None:
    fig, axes = plt.subplots(2, 4, figsize=(13, 5.6), sharex=True)
    for ch, ax in zip(range(1, 9), axes.flat):
        rs = [r for r in rows if r["ch"] == ch]
        t = np.array([r["t_min"] for r in rs])
        o = np.array([r["pre_offset"] for r in rs])
        c = np.array([r["cmd"] == "control" for r in rs])
        se = np.sqrt(np.mean([r["pre_std"] ** 2 / r["pre_n"] for r in rs]))
        ax.axhspan(o.mean() - 2 * se, o.mean() + 2 * se, color=WASH, zorder=0)
        ax.plot(t[~c], o[~c], "o", ms=3.2, color=CH_COLORS[ch - 1], alpha=0.85, zorder=2)
        ax.plot(t[c], o[c], "D", ms=5.5, mfc="white", mec=CH_COLORS[ch - 1], mew=1.5, zorder=3)
        ax.set_title(f"ch{ch}", fontsize=9.5, loc="left")
    for ax in axes[1]:
        ax.set_xlabel("Time since DOE start (min)")
    for ax in axes[:, 0]:
        ax.set_ylabel("Pre-baseline offset (a.u.)")
    fig.suptitle("H5 · Baseline offset over the run (dots = tone runs, open diamonds = control runs; "
                 "shaded = ±2 predicted SD)", x=0.01, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_flatness(rows: list[dict], res: dict, path: Path) -> None:
    fig, axes = plt.subplots(2, 4, figsize=(13, 5.6), sharex=True)
    for ch, ax in zip(range(1, 9), axes.flat):
        ts = [r for r in rows if r["ch"] == ch and r["cmd"] != "control"]
        amp = np.array([r["amp"] for r in ts])
        f = np.array([r["f"] for r in ts])
        A = amp.mean()
        for fr in range(1, 20):
            v = (amp[f == fr] - A) / A * 100
            ax.plot(np.full(len(v), fr), v, "o", ms=2.6, color=CH_COLORS[ch - 1], alpha=0.35, zorder=2)
        m = np.array([res["flat"][ch]["means"][fr] for fr in range(1, 20)])
        ax.plot(range(1, 20), (m - A) / A * 100, "-", color=CH_COLORS[ch - 1], lw=2, zorder=3)
        fl = res["flat"][ch]
        ax.set_title(f"ch{ch}{' (at 2f)' if ch == 8 else ''}   F = {fl['F']:.1f}, p = {fl['p']:.2g}",
                     fontsize=9.5, loc="left")
        ax.set_xticks([1, 5, 10, 15, 19])
    for ax in axes[1]:
        ax.set_xlabel("Commanded frequency (Hz)")
    for ax in axes[:, 0]:
        ax.set_ylabel("Amplitude vs channel mean (%)")
    fig.suptitle("Is the response flat across 1–19 Hz? (dots = 10 repeats; line = mean per frequency)",
                 x=0.01, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_phase(rows: list[dict], path: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), gridspec_kw={"width_ratios": [2, 1, 1]})

    def series(ch):
        ts = [r for r in rows if r["ch"] == ch and r["cmd"] != "control"]
        return np.array([r["f"] for r in ts]), np.array([r["dphase_deg"] for r in ts])

    ax = axes[0]
    for ch in (2, 3, 5, 6, 7):
        f, d = series(ch)
        m = [d[f == fr].mean() for fr in range(1, 20)]
        s = [d[f == fr].std(ddof=1) for fr in range(1, 20)]
        off = (ch - 4.5) * 0.08
        ax.errorbar(np.arange(1, 20) + off, m, yerr=s, fmt="o-", ms=4, lw=1.2, color=CH_COLORS[ch - 1],
                    capsize=0, label=f"ch{ch}", zorder=2)
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.set_ylabel("Phase vs reference (°)")
    ax.set_ylim(-0.2, 0.2)
    ax.legend(ncol=5, loc="upper left", fontsize=8.5)
    ax.set_title("Healthy channels: mean ± SD of 10 per frequency", fontsize=10, loc="left")
    for ax, ch, title in ((axes[1], 4, "ch4 (noisy): each run"), (axes[2], 1, "ch1 (tone below its noise): each run")):
        f, d = series(ch)
        ax.plot(f, d, "o", ms=3, color=CH_COLORS[ch - 1], alpha=0.6)
        ax.axhline(0, color=MUTED, lw=0.8)
        ax.set_title(title, fontsize=10, loc="left")
    for ax in axes:
        ax.set_xlabel("Commanded frequency (Hz)")
        ax.set_xticks([1, 5, 10, 15, 19])
    fig.suptitle("Phase vs the reference (median of the other reference channels among ch2, ch3, ch5, ch6, ch7) "
                 "— note the different y scales", x=0.01, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def chart_recovery_jitter(rows: list[dict], res: dict, path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    ax = axes[0]
    for ch in range(1, 9):
        ts = [r for r in rows if r["ch"] == ch and r["cmd"] != "control"]
        z = np.array([(r["rec_offset"] - r["pre_offset"]) /
                      np.sqrt(r["pre_std"] ** 2 / r["pre_n"] + r["rec_std"] ** 2 / r["rec_n"]) for r in ts])
        jit = (np.random.default_rng(ch).random(len(z)) - 0.5) * 0.5
        ax.plot(ch + jit, z, "o", ms=2.6, color=CH_COLORS[ch - 1], alpha=0.6)
    ax.axhspan(-3, 3, color=WASH, zorder=0)
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.set_xticks(range(1, 9))
    ax.set_xticklabels([f"ch{c}" for c in range(1, 9)])
    ax.grid(axis="x", visible=False)
    ax.set_ylabel("(recovery offset − pre offset) ÷ its noise SE")
    ax.set_title("H4 · Recovery: offset after the tone vs before (shaded ±3)", fontsize=10, loc="left")
    ax = axes[1]
    jm = res["jitter_model"]
    fm = np.linspace(0, 40, 200)
    ax.plot(fm, np.sqrt(jm["a0"] + jm["b0"] * fm ** 2), color=INK2, lw=1.2, ls="--", zorder=1,
            label=f"model: {jm['sig_a']:.2f}% amplitude + {jm['sig_t'] * 1e6:.0f} µs timing jitter")
    for ch in (2, 3, 5, 6, 7, 8):
        j = res["jitter"][ch]
        m = [j["ex"][j["f"] == fr].mean() for fr in range(1, 20)]
        x = np.arange(1, 20) * (2 if ch == 8 else 1)
        ax.plot(x, m, "o", ms=4, color=CH_COLORS[ch - 1], label=f"ch{ch}" + (" (tone at 2f)" if ch == 8 else ""),
                zorder=2)
    ax.set_xlabel("Actual tone frequency (Hz)  —  ch8's tone is at twice the commanded frequency")
    ax.set_ylabel("Excess noise while excited (% of A)")
    ax.set_xlim(0, 40)
    ax.set_ylim(0, 2)
    ax.legend(ncol=2, loc="lower right", fontsize=8.5)
    ax.set_title("H7 · Extra noise while excited (mean of 10; model fitted on ch2, ch3, ch5, ch6, ch7)", fontsize=10, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("root", type=Path, nargs="?", default=BENCH / "03_doe1_repeatability")
    p.add_argument("--reextract", action="store_true")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")       # report uses σ, ±, ° (Windows consoles default to cp1252)
    out = a.root / "analysis"
    out.mkdir(exist_ok=True)
    runs_csv = out / "runs.csv"
    if a.reextract or not runs_csv.exists():
        print("extracting metrics from captures ...", flush=True)
        extract(a.root, runs_csv)
    rows = load_rows(runs_csv)
    res = report(rows, out)
    chart_sd_ratio(res, out / "1_sd_ratio.png")
    chart_flatness(rows, res, out / "2_flatness.png")
    chart_drift(rows, res, out / "3_amplitude_drift.png")
    chart_offset_drift(rows, out / "4_offset_drift.png")
    chart_phase(rows, out / "5_phase.png")
    chart_recovery_jitter(rows, res, out / "6_recovery_jitter.png")
    print(f"\nwrote {out}/report.md, runs.csv and 6 charts")


if __name__ == "__main__":
    main()
