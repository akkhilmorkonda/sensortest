"""Compile the sensor test file + the settings' plan lists + a limit set into CompiledPlans.

All inputs are YAML (D-045), read with yaml.safe_load and validated here.

Rules (D-040, D-044, D-027): a plan is an explicit list of step IDs; the captures it needs are
derived from those steps (the first, baseline capture always runs: 1000 CONNECT reads it);
validity steps 1000/1900 are always included; steps defined on a capture that runs but not listed
become diagnostics; every window is a multiple of 30 lines; tones are whole numbers 1-19 (D-009).
Any violation raises TestError E301 (test file / plan) or E302 (limits), naming file and field.
"""

import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from sensortest.core.errors import TestError
from sensortest.core.models import (
    CaptureDef,
    CompiledPlan,
    DiagnosticDef,
    FaultFamily,
    Limit,
    StepDef,
    StepType,
)
from sensortest.core.yamlio import Fields, load_yaml

TESTS_SCHEMA = "tp001-tests-v2"  # v2: no step dependencies (D-056)
CONNECT = StepDef(1000, "CONNECT", StepType.VALIDITY, capture="BL", metric="", unit="Hz", title="Connection")
RUN_INTEGRITY = StepDef(
    1900, "RUN_INTEGRITY", StepType.VALIDITY, capture="", metric="", unit="", title="Run integrity"
)
_TONE = re.compile(r"response:(\d+)")


def compile_plans(
    sensor_tests: Path,
    limits: Path,
    plan_lists: Mapping[str, tuple[int, ...]],
    constants: Mapping[str, float],
) -> dict[str, CompiledPlan]:
    """Return one CompiledPlan per sequence in ``plan_lists`` (e.g. PROD, ENG)."""
    captures, metrics, steps, diagnostics, plan_version, plan_id = _read_tests(sensor_tests)
    limit_set, step_limits, limit_constants = _read_limits(limits, steps)
    all_constants = {**limit_constants, **constants}
    if "tone_present" not in all_constants:
        raise TestError("E302", f"{limits.name}: constants.tone_present_a.u. missing")
    return {
        name: replace(
            _compile_one(
                name, ids, captures, metrics, steps, diagnostics, plan_version, limit_set, step_limits,
                all_constants,
            ),
            plan=plan_id,
        )
        for name, ids in plan_lists.items()
    }  # fmt: skip


# ---------------------------------------------------------------- sensor test file ----
def _read_tests(path: Path):
    f = Fields(load_yaml(path), path.name)
    if f.text("schema") != TESTS_SCHEMA:
        raise f.error("schema", f"expected {TESTS_SCHEMA!r}")

    captures: list[CaptureDef] = []
    for i, raw in enumerate(f.items("captures")):
        c = Fields(raw, f"{path.name}.captures[{i}]")
        cid, command = c.text("id"), c.text("command")
        settle, window = c.integer("settle_lines"), c.integer("window_lines")
        tone = c.raw("tone_hz", None)
        if command == "baseline":
            if tone is not None:
                raise c.error("tone_hz", "a baseline capture has no tone")
            tone_hz = None
        else:
            m = _TONE.fullmatch(command)
            if not m:
                raise c.error("command", f"expected 'baseline' or 'response:N', got {command!r}")
            n = int(m.group(1))
            if not 1 <= n <= 19:
                raise c.error("command", f"tone {n} Hz outside 1-19 (D-009)")
            if c.integer("tone_hz") != n:
                raise c.error("tone_hz", f"must equal the command's {n}")
            tone_hz = float(n)
        if window <= 0 or window % 30:
            raise c.error("window_lines", f"{window} is not a positive multiple of 30 (D-027)")
        if settle < 0:
            raise c.error("settle_lines", "must be >= 0")
        if any(x.id == cid for x in captures):
            raise c.error("id", f"duplicate capture {cid!r}")
        default_title = cid if tone_hz is None else f"Tone {tone_hz:g} Hz"
        captures.append(CaptureDef(cid, command, tone_hz, settle, window, c.text("title", default_title)))
    if not captures or captures[0].tone_hz is not None:
        raise f.error("captures", "the first capture must be a baseline (1000 CONNECT reads it)")
    cap_ids = {c.id for c in captures}

    metrics_f = f.section("metrics")
    metrics = {k: metrics_f.section(k).text("unit") for k in metrics_f}

    steps: dict[int, StepDef] = {}
    for i, raw in enumerate(f.items("steps")):
        s = Fields(raw, f"{path.name}.steps[{i}]")
        sid, name, cap, metric = s.integer("id"), s.text("name"), s.text("capture"), s.text("metric")
        fam = s.text("family")
        if sid in steps or sid in (CONNECT.id, RUN_INTEGRITY.id):
            raise s.error("id", f"duplicate or reserved step id {sid}")
        if cap not in cap_ids:
            raise s.error("capture", f"unknown capture {cap!r}")
        if metric not in metrics:
            raise s.error("metric", f"{metric!r} is not in the metric dictionary")
        try:
            family = FaultFamily[fam.upper()]
        except KeyError:
            raise s.error("family", f"unknown family {fam!r}") from None
        if s.raw("depends_on", None) is not None:
            raise s.error("depends_on", "steps are judged independently (D-056); remove it")
        title = s.text("title", name)  # the operator name; falls back to the step name (D-053)
        steps[sid] = StepDef(sid, name, StepType.LIMIT, cap, metric, metrics[metric], family, title)

    diagnostics = []
    for i, raw in enumerate(f.items("diagnostics", [])):
        d = Fields(raw, f"{path.name}.diagnostics[{i}]")
        key, cap, metric = d.text("key"), d.text("capture"), d.text("metric")
        if cap not in cap_ids:
            raise d.error("capture", f"unknown capture {cap!r}")
        if metric not in metrics:
            raise d.error("metric", f"{metric!r} is not in the metric dictionary")
        diagnostics.append(DiagnosticDef(key, cap, metric, metrics[metric]))

    return captures, metrics, steps, diagnostics, f.text("plan_version"), f.text("plan")


# ---------------------------------------------------------------- limit set ----
def _read_limits(path: Path, steps: Mapping[int, StepDef]):
    f = Fields(load_yaml(path, "E302"), path.name, "E302")
    limits: dict[int, Limit] = {}
    steps_f = f.section("steps")
    for key in steps_f:
        parts = key.split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit():
            raise steps_f.error(key, "keys must look like '2040 BL_OFFSET'")
        sid, name = int(parts[0]), parts[1]
        if sid not in steps:
            raise steps_f.error(key, f"step {sid} is not in the sensor test file")
        if steps[sid].name != name:
            raise steps_f.error(key, f"name mismatch: the sensor test file calls {sid} {steps[sid].name!r}")
        e = steps_f.section(key)
        low, high = e.raw("low", None), e.raw("high", None)
        for k, v in (("low", low), ("high", high)):
            if v is not None and (isinstance(v, bool) or not isinstance(v, int | float)):
                raise e.error(k, f"expected a number or null, got {v!r}")
        limits[sid] = Limit(None if low is None else float(low), None if high is None else float(high))
    consts = f.section("constants")
    constants = {"tone_present": consts.number("tone_present_a.u.")}
    return f.text("limit_set"), limits, constants


# ---------------------------------------------------------------- one sequence ----
def _compile_one(
    name, ids, captures, metrics, steps, diagnostics, plan_version, limit_set, step_limits, constants
) -> CompiledPlan:
    where = f"settings.plans.{name}"
    unknown = [i for i in ids if i not in steps]
    if unknown:
        raise TestError("E301", f"{where}: unknown step IDs {unknown}")
    if len(set(ids)) != len(ids):
        raise TestError("E301", f"{where}: duplicate step IDs")
    chosen = {i: steps[i] for i in ids}
    for st in chosen.values():
        if st.id not in step_limits:
            raise TestError("E302", f"limit set has no limit for step {st.id} {st.name} (used by {name})")

    used = {st.capture for st in chosen.values()} | {captures[0].id}
    plan_caps = tuple(c for c in captures if c.id in used)
    order = {c.id: k for k, c in enumerate(plan_caps)}
    limit_steps = sorted(chosen.values(), key=lambda s: (order[s.capture], s.id))
    # Steps on a running capture but not in this plan are still measured, as diagnostics (D-029).
    extra = [
        DiagnosticDef(s.name, s.capture, s.metric, s.unit)
        for s in steps.values()
        if s.capture in used and s.id not in chosen
    ]
    diags = tuple(d for d in (*extra, *diagnostics) if d.capture in used)

    return CompiledPlan(
        sequence=name,
        plan_version=plan_version,
        limit_set=limit_set,
        captures=plan_caps,
        steps=(CONNECT, *limit_steps, RUN_INTEGRITY),
        diagnostics=diags,
        limits={sid: step_limits[sid] for sid in chosen},
        constants=dict(constants),
    )
