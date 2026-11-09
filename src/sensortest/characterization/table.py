"""The metric table: one row per DOE-1 run x sensor, from the station's own metrics (D-004, D-050).

Same columns as benchtop_testing/04_limits_v0_derivation/recut_metrics.csv, the table L-v0 was
derived from. Each value comes from the registered @metric the station runs for that step, or
from the core method the engine calls directly (the 1000 CONNECT idle amplitude, the frequency
and its peak before the D-030 tone-present gate).

AMPLITUDE is the station's own tone_amplitude: since D-056, fitted at the measured dominant
frequency when a tone is present. There is no phase column: all channels are assumed in phase.
"""

from collections.abc import Sequence
from typing import Any

import numpy as np

import sensortest.steps.builtin  # noqa: F401  (registers the metrics)
from sensortest.characterization.recut import BL, REC, TONE, DoeRun
from sensortest.core import methods as m
from sensortest.core.models import SENSORS, CompiledPlan
from sensortest.core.ports import MetricContext
from sensortest.steps.registry import METRICS

BL_METRICS = (
    ("BL_OFFSET", "offset"),
    ("BL_NOISE_STD", "noise_std"),
    ("BL_P2P", "p2p"),
    ("BL_STUCK_RUN", "stuck_run"),
    ("BL_OUTLIERS", "outliers"),
)
TONE_METRICS = (
    ("AMPLITUDE", "tone_amplitude"),
    ("RESIDUAL", "residual_std"),
    ("OFFSET_SHIFT", "offset_shift"),
)
REC_METRICS = (
    ("REC_TONE_ABSENT", "tone_absent"),
    ("REC_OFFSET_SHIFT", "recovery_shift"),
    ("REC_NOISE_STD", "noise_std"),
    ("REC_STUCK_RUN", "stuck_run"),
    ("REC_OUTLIERS", "outliers"),
)
COUNTS = {"BL_STUCK_RUN", "BL_OUTLIERS", "REC_STUCK_RUN", "REC_OUTLIERS"}  # written as whole numbers


def metric_rows(runs: Sequence[DoeRun], plan: CompiledPlan) -> list[dict[str, Any]]:
    """``plan``: the ENG plan (all captures)."""
    fs = float(plan.constants["fs_nom_hz"])
    tones = [float(c.tone_hz) for c in plan.captures if c.tone_hz is not None]
    rows: list[dict[str, Any]] = []
    for run in runs:
        bl = run.windows[BL]
        cols: dict[str, np.ndarray] = {
            name: METRICS.get(key)(bl, MetricContext(plan, None, None, {BL: bl})).values
            for name, key in BL_METRICS
        }
        for f in tones:  # what 1000 CONNECT's idle check measures (D-024)
            cols[f"BL_IDLE_AMP_{f:g}"] = np.array([m.sine_fit(bl.data[:, i], f, fs)[1] for i in range(8)])
        if run.tone_hz is not None:
            f = float(run.tone_hz)
            t, rec = run.windows[TONE], run.windows[REC]
            wins = {BL: bl, TONE: t, REC: rec}
            tone_ctx = MetricContext(plan, f, None, wins)
            rec_ctx = MetricContext(plan, None, f, wins)
            cols |= {name: METRICS.get(key)(t, tone_ctx).values for name, key in TONE_METRICS}
            cols |= {name: METRICS.get(key)(rec, rec_ctx).values for name, key in REC_METRICS}
            dom = [m.dominant_frequency(t.data[:, i], fs) for i in range(8)]
            cols["FREQUENCY"] = np.array([d[0] for d in dom])
            cols["PEAK_AMP"] = np.array([d[1] for d in dom])
            cols["FREQ_ERR"] = cols["FREQUENCY"] - f
        for i in range(len(SENSORS)):
            row: dict[str, Any] = {"sweep": run.sweep, "pos": run.pos, "cmd": run.cmd, "ch": i + 1}
            if run.tone_hz is not None:
                row["f"] = float(run.tone_hz)
            for name, values in cols.items():
                row[name] = int(values[i]) if name in COUNTS else float(values[i])
            rows.append(row)
    return rows
