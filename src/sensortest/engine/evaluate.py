"""Turn capture windows into step results, diagnostics, verdicts and primary failures.

Pure logic (no threads, no I/O), so it is tested directly on recorded data. The engine calls
evaluate_capture() after each capture and finalize() once at the end (TP-001 §8, D-026, D-030).
"""

from collections.abc import Callable
from dataclasses import replace

import numpy as np

from sensortest.core import methods as m
from sensortest.core.errors import TestError
from sensortest.core.models import (
    RUN,
    SENSORS,
    CompiledPlan,
    Outcome,
    Result,
    StepDef,
    StepResult,
    StepType,
    Window,
)
from sensortest.core.ports import Measured, MetricContext, MetricFn

MetricLookup = Callable[[str], MetricFn]


def check_connect(plan: CompiledPlan, bl: Window) -> StepResult:
    """1000 CONNECT on the baseline window (D-023, D-024): line rate (E104) and idle (E106).

    Device found / port / baseline-accepted / parse problems are raised earlier by the driver.
    """
    fs, tol = plan.constants["fs_nom_hz"], plan.constants["line_rate_tolerance"]
    rate = m.line_rate(bl.t_host)
    if not abs(rate - fs) <= fs * tol:
        raise TestError("E104", f"line rate {rate:.2f} Hz, expected {fs:g} ± {tol:.0%}", where=bl.capture)
    present = plan.constants["tone_present"]
    for c in plan.captures:
        if c.tone_hz is None:
            continue
        for i in range(bl.data.shape[1]):
            amp = m.sine_fit(bl.data[:, i], c.tone_hz, fs)[1]
            if amp >= present:
                raise TestError(
                    "E106", f"{SENSORS[i]} shows {amp:.0f} a.u. at {c.tone_hz:g} Hz", where=bl.capture
                )
    return StepResult(
        1000, "CONNECT", RUN, bl.capture, rate, "Hz", fs * (1 - tol), fs * (1 + tol), Result.PASS
    )


def check_line_rate(plan: CompiledPlan, window: Window) -> None:
    """Part of 1900 RUN_INTEGRITY for every capture after the first (E205: dropped lines)."""
    fs, tol = plan.constants["fs_nom_hz"], plan.constants["line_rate_tolerance"]
    rate = m.line_rate(window.t_host)
    if not abs(rate - fs) <= fs * tol:
        raise TestError("E205", f"line rate {rate:.2f} Hz", where=window.capture)


def evaluate_capture(
    plan: CompiledPlan, window: Window, ctx: MetricContext, metric: MetricLookup
) -> tuple[list[StepResult], dict[str, dict[str, float | None]]]:
    """Limit steps and diagnostics for one capture. Returns (results, diagnostics)."""
    results: list[StepResult] = []
    for step in (s for s in plan.steps if s.type is StepType.LIMIT and s.capture == window.capture):
        results.extend(_judge(plan, step, metric(step.metric)(window, ctx)))

    diags: dict[str, dict[str, float | None]] = {}
    for d in (d for d in plan.diagnostics if d.capture == window.capture):
        out = metric(d.metric)(window, ctx)
        diags[d.key] = {
            s: (None if np.isnan(v) else float(v)) for s, v in zip(SENSORS, out.values, strict=True)
        }
    return results, diags


def _judge(plan: CompiledPlan, step: StepDef, out: Measured) -> list[StepResult]:
    """One step's 8 results. Each channel is judged on its own value alone (D-056); a NaN value is
    NOT TESTED with the metric's reason (e.g. frequency with no tone, D-030)."""
    limit = plan.limits[step.id]
    rows = []
    for i, sensor in enumerate(SENSORS):
        value = float(out.values[i])
        if np.isnan(value):
            res, reason, value_out = Result.NOT_TESTED, out.not_tested.get(i, "not computed"), None
        else:
            res = Result.PASS if limit.passes(value) else Result.FAIL
            reason, value_out = "", value
        rows.append(
            StepResult(
                step.id,
                step.name,
                sensor,
                step.capture,
                value_out,
                step.unit,
                limit.low,
                limit.high,
                res,
                reason,
                step.family,
            )
        )
    return rows


def finalize(results: list[StepResult]) -> tuple[list[StepResult], dict[str, Result], Outcome]:
    """Mark each sensor's primary failure (lowest fault-family rank, then execution order) and
    compute verdicts: a sensor FAILs if any of its limit steps FAILed (D-026)."""
    final = list(results)
    verdicts: dict[str, Result] = {}
    for sensor in SENSORS:
        idx = [k for k, r in enumerate(final) if r.sensor == sensor and r.result is Result.FAIL]
        verdicts[sensor] = Result.FAIL if idx else Result.PASS
        if idx:
            primary = min(idx, key=lambda k: (final[k].family or 99, k))
            final[primary] = replace(final[primary], primary_failure=True)
    outcome = Outcome.FAIL if Result.FAIL in verdicts.values() else Outcome.PASS
    return final, verdicts, outcome


def not_tested_remaining(plan: CompiledPlan, done: set[int], reason: str) -> list[StepResult]:
    """After an ERROR or ABORT: every limit step not yet evaluated, NOT TESTED with the reason."""
    rows = []
    for step in (s for s in plan.steps if s.type is StepType.LIMIT and s.id not in done):
        lim = plan.limits[step.id]
        rows += [
            StepResult(
                step.id,
                step.name,
                s,
                step.capture,
                None,
                step.unit,
                lim.low,
                lim.high,
                Result.NOT_TESTED,
                reason,
                step.family,
            )
            for s in SENSORS
        ]
    return rows


def with_run_rows(results: list[StepResult], error: TestError | None, outcome: Outcome) -> list[StepResult]:
    """Every run record has both run-level rows, 1000 CONNECT and 1900 RUN_INTEGRITY, whatever the
    outcome. A connection fault (E1xx) fails 1000; any other fault fails 1900; an abort or an
    earlier stop leaves the missing row NOT TESTED."""
    have = {r.step_id for r in results if r.sensor == RUN}
    reason = error.label if error else ("ABORTED" if outcome is Outcome.ABORTED else "")
    connect_fault = bool(error and error.code.code.startswith("E1"))
    head, tail = [], []
    if 1000 not in have:
        res = Result.FAIL if connect_fault else Result.NOT_TESTED
        head.append(StepResult(1000, "CONNECT", RUN, "BL", None, "Hz", None, None, res, reason))
    if 1900 not in have:
        res = Result.FAIL if (error and not connect_fault) else Result.NOT_TESTED
        tail.append(StepResult(1900, "RUN_INTEGRITY", RUN, "", None, "", None, None, res, reason))
    return head + list(results) + tail
