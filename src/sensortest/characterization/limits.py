"""The limit rule (D-019): from the metric table to a limit set.

Measured limits (offset, noise, amplitude, recovery shift): take the healthy population's
per-sensor means, widen their range by 10 % (ratio metrics scale by x0.9 / x1.1; offset-like
metrics add 10 % of the span), then add 3 x the largest per-sensor repeatability SD at the plan's
window length, and round outward to 0.1.

Design limits (frequency, stuck run, outliers, tone absent) are constants with their reason.
There is no phase limit since D-056 (all channels are assumed in phase).
The output is the limit-set YAML structure the plan compiler reads (D-045).
"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from sensortest.core.models import CompiledPlan

MARGIN = 0.10  # healthy range widened by 10 %
K_REP = 3  # plus 3 x repeatability SD at the plan's window length
TONE_PRESENT = 100.0  # a.u.: idle, tone absent, actuator stopped, and the frequency gate (D-030)
FREQ_TOL = 0.5  # Hz: one native FFT bin of a 2 s window
STUCK_MAX = 5  # samples: P(run >= 5 | 1000 noise levels, 300 samples) ~ 3e-10
REPLY_TIMEOUT_S = 0.5
# Healthy population per measured metric: every sensor except those faulty on it (D-019).
POPULATION: Mapping[str, tuple[int, ...]] = {
    "BL_OFFSET": (1, 2, 3, 4, 6, 7, 8),  # ch5 offset fault
    "BL_NOISE_STD": (1, 2, 3, 5, 6, 7, 8),  # ch4 noise fault
    # ch1 weak, ch4 high. ch8 reads its 2f response (~890) since D-056, but it is not known-healthy:
    # it fails frequency. Keeping the population fixed keeps L-v1's amplitude limits.
    "AMPLITUDE": (2, 3, 5, 6, 7),
    "REC_OFFSET_SHIFT": (1, 2, 3, 4, 5, 6, 7, 8),
}


def _vals(rows: Sequence[Mapping[str, Any]], key: str, ch: int, tone_only: bool) -> np.ndarray:
    return np.array(
        [r[key] for r in rows if r["ch"] == ch and key in r and (not tone_only or r["cmd"] != "control")],
        dtype=float,
    )


def _floor(v: float) -> float:
    return round(float(np.floor(v / 0.1) * 0.1), 1)


def _ceil(v: float) -> float:
    return round(float(np.ceil(v / 0.1) * 0.1), 1)


def range_margin_limit(
    rows: Sequence[Mapping[str, Any]], key: str, kind: str, tone_only: bool = False, window: str = "2 s"
) -> dict[str, Any]:
    """``kind``: "ratio" (amplitude, noise) or "offset" (can be ~0 or negative)."""
    pop = POPULATION[key]
    means = {ch: _vals(rows, key, ch, tone_only).mean() for ch in pop}
    sds = {ch: _vals(rows, key, ch, tone_only).std(ddof=1) for ch in pop}
    lo_m, hi_m = min(means.values()), max(means.values())
    if kind == "ratio":
        lo, hi = lo_m * (1 - MARGIN), hi_m * (1 + MARGIN)
        how = f"x{1 - MARGIN:g}/x{1 + MARGIN:g}"
    else:
        span = hi_m - lo_m
        lo, hi = lo_m - MARGIN * span, hi_m + MARGIN * span
        how = f"+/-{MARGIN:.0%} of span"
    rep = max(sds.values())
    return {
        "low": _floor(lo - K_REP * rep),
        "high": _ceil(hi + K_REP * rep),
        "method": f"healthy range {lo_m:.2f}..{hi_m:.2f} ({how}) +/- {K_REP} x {rep:.3f} "
        f"(max repeatability SD, {window})",
        "population": [f"ch{c}" for c in pop],
    }


def derive_limits(
    rows: Sequence[Mapping[str, Any]], plan: CompiledPlan, limit_set: str, status: str
) -> dict[str, Any]:
    """The limit set from the metric table. ``plan``: the ENG plan (timing and constants)."""
    caps = {c.id: c for c in plan.captures}
    fs = float(plan.constants["fs_nom_hz"])
    tol = float(plan.constants["line_rate_tolerance"])
    settle, win, rec = caps["BL"].settle_lines, caps["T10"].window_lines, caps["REC"].window_lines
    win_s, rec_s = f"{win / fs:g} s", f"{rec / fs:g} s"
    both, eng = ["PROD", "ENG"], ["ENG"]
    out: dict[str, Any] = {
        "plan": plan.plan,
        "limit_set": limit_set,
        "status": status,
        "source": f"DOE-1 raw captures re-cut to {plan.plan} timing "
        f"(settle {settle} lines, {win}-line windows; REC {rec})",
        "common_to_all_sensors": True,
        "steps": {},
    }
    s = out["steps"]
    s["2040 BL_OFFSET"] = range_margin_limit(rows, "BL_OFFSET", "offset", window=win_s) | {
        "unit": "a.u.",
        "sequences": both,
    }
    s["2050 BL_NOISE_STD"] = range_margin_limit(rows, "BL_NOISE_STD", "ratio", window=win_s) | {
        "unit": "a.u.",
        "sequences": both,
    }
    amp = range_margin_limit(rows, "AMPLITUDE", "ratio", tone_only=True, window=win_s)
    for sid in ("6040 T10_AMPLITUDE", "3040 T5_AMPLITUDE", "4040 T15_AMPLITUDE"):
        s[sid] = amp | {"unit": "a.u.", "sequences": both if sid.startswith("6") else eng}
    for sid, f in (("6050 T10_FREQUENCY", 10), ("3050 T5_FREQUENCY", 5), ("4050 T15_FREQUENCY", 15)):
        s[sid] = {
            "low": f - FREQ_TOL,
            "high": f + FREQ_TOL,
            "unit": "Hz",
            "method": f"commanded f +/- {FREQ_TOL} Hz (one native FFT bin at {win_s}); "
            f"judged only if the dominant peak >= {TONE_PRESENT:g} a.u. (tone present)",
            "sequences": both if sid.startswith("6") else eng,
        }
    s["2060 BL_STUCK_RUN"] = {
        "low": None,
        "high": STUCK_MAX,
        "unit": "samples",
        "sequences": eng,
        "method": f"design: P(run >= {STUCK_MAX}) ~ 3e-10 per {win}-sample window with 1000 noise levels",
    }
    s["2070 BL_OUTLIERS"] = {
        "low": None,
        "high": 0,
        "unit": "count",
        "sequences": eng,
        "method": "design (D-011); valid for uniform noise only (M8)",
    }
    s["5050 REC_TONE_ABSENT"] = {
        "low": None,
        "high": TONE_PRESENT,
        "unit": "a.u.",
        "sequences": eng,
        "method": "tone-present threshold",
    }
    s["5060 REC_OFFSET_SHIFT"] = range_margin_limit(
        rows, "REC_OFFSET_SHIFT", "offset", tone_only=True, window=rec_s
    ) | {"unit": "a.u.", "sequences": eng}
    s["5080 REC_STUCK_RUN"] = dict(s["2060 BL_STUCK_RUN"]) | {
        "method": f"design: P(run >= {STUCK_MAX}) ~ 2e-10 per {rec}-sample window with 1000 noise levels"
    }
    s["5090 REC_OUTLIERS"] = dict(s["2070 BL_OUTLIERS"])
    out["validity"] = {
        "1000 CONNECT": {
            "line_rate_hz": [round(fs * (1 - tol), 6), round(fs * (1 + tol), 6)],
            "idle_amplitude_max": TONE_PRESENT,
        },
        "1900 RUN_INTEGRITY": {
            "reply_timeout_s": REPLY_TIMEOUT_S,
            "line_rate_tolerance": tol,
        },
    }
    out["constants"] = {
        "tone_present_a.u.": TONE_PRESENT,
        "fs_nom_hz": fs,
        "settle_lines": settle,
        "window_lines": win,
        "rec_window_lines": rec,
    }
    return out
