"""Each DOE-1 tone run through the engine's own evaluation, as a PROD run (D-050).

A tone run stands in for T10 at its own frequency, so the plan is re-pointed first: T10's tone
becomes f and the T10 frequency limit moves with it (f +/- the same tolerance). Then the run goes
through exactly what the engine does: 1000 CONNECT on BL, the BL and T10 limit steps (each judged on
its own channel, D-056) with the D-030 frequency gate, and the primary failure by fault-family rank.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

import sensortest.steps.builtin  # noqa: F401  (registers the metrics)
from sensortest.characterization.recut import BL, TONE, DoeRun
from sensortest.core.models import SENSORS, CompiledPlan, Limit, Result, StepResult, StepType
from sensortest.core.ports import MetricContext
from sensortest.engine import evaluate as ev
from sensortest.steps.registry import METRICS


def at_tone(plan: CompiledPlan, capture: str, f: float) -> CompiledPlan:
    """``plan`` with ``capture`` re-pointed to f Hz; frequency limits on it move by the same amount."""
    old = next(c for c in plan.captures if c.id == capture).tone_hz
    if old is None:
        raise ValueError(f"{capture} is not a tone capture")
    shift = f - old
    captures = tuple(
        replace(c, tone_hz=f, command=f"response:{f:g}") if c.id == capture else c for c in plan.captures
    )
    limits = dict(plan.limits)
    for st in plan.steps:
        if st.type is StepType.LIMIT and st.capture == capture and st.metric == "dominant_frequency":
            lim = limits[st.id]
            limits[st.id] = Limit(
                None if lim.low is None else lim.low + shift, None if lim.high is None else lim.high + shift
            )
    return replace(plan, captures=captures, limits=limits)


def prod_run(plan: CompiledPlan, run: DoeRun) -> tuple[list[StepResult], dict[str, Result]]:
    """``plan``: the PROD plan. Returns the run's step results (primary failures marked) and verdicts."""
    if run.tone_hz is None:
        raise ValueError("a control run has no tone capture")
    f = float(run.tone_hz)
    p = at_tone(plan, TONE, f)
    bl, t = run.windows[BL], run.windows[TONE]
    results = [ev.check_connect(p, bl)]  # raises TestError on a connection fault
    bl_rows, _ = ev.evaluate_capture(p, bl, MetricContext(p, None, None, {BL: bl}), METRICS.get)
    t_rows, _ = ev.evaluate_capture(p, t, MetricContext(p, f, None, {BL: bl, TONE: t}), METRICS.get)
    final, verdicts, _ = ev.finalize([*results, *bl_rows, *t_rows])
    return final, verdicts


@dataclass
class SensorSummary:
    passed: int = 0
    failed: int = 0
    primary: Counter = field(default_factory=Counter)  # fault family -> runs where it was primary
    steps: Counter = field(default_factory=Counter)  # step id -> runs where it failed


def summarize(
    runs: Iterable[tuple[list[StepResult], Mapping[str, Result]]],
) -> dict[str, SensorSummary]:
    """Per sensor: PASS / FAIL runs, primary failure families and failed steps (the L-v0 report table)."""
    out = {s: SensorSummary() for s in SENSORS}
    for results, verdicts in runs:
        for s in SENSORS:
            if verdicts[s] is Result.PASS:
                out[s].passed += 1
            else:
                out[s].failed += 1
        for r in results:
            if r.sensor in out and r.result is Result.FAIL:
                out[r.sensor].steps[r.step_id] += 1
                if r.primary_failure and r.family is not None:
                    out[r.sensor].primary[r.family.name.lower()] += 1
    return out
