"""The domain model shared by every module (PRD §6.2). Frozen dataclasses; no I/O.

Definitions come from the data files (sensor test file, settings, limits) via the plan compiler.
Results flow out as StepResult and RunRecord, which every sink and view consumes.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

import numpy as np

SENSORS: tuple[str, ...] = tuple(f"ch{i}" for i in range(1, 9))
RUN = "RUN"  # the "sensor" value for run-level (validity) results


# ---------------------------------------------------------------- enums ----
class StepType(StrEnum):
    VALIDITY = "validity"  # failure -> run ERROR
    LIMIT = "limit"  # failure -> that sensor FAILs


class Result(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_TESTED = "NOT TESTED"


class Outcome(StrEnum):
    """Run-level outcome (D-038)."""

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    ABORTED = "ABORTED"


class FaultFamily(IntEnum):
    """Diagnostic rank: the lowest-ranked failure is the operator's primary failure (D-026)."""

    STUCK = 1
    GLITCH = 2
    FREQUENCY = 3
    NOISE = 4
    OFFSET = 5
    GAIN = 6
    RECOVERY = 8  # 7 was TIMING (phase), removed by D-056; ranks keep their numbers


class Role(StrEnum):
    """Roles a device can provide (D-037). The device manager tracks roles, not boxes."""

    ACTUATOR = "actuator"
    SENSOR = "sensor"


# ---------------------------------------------------------------- definitions (from files) ----
@dataclass(frozen=True, slots=True)
class CaptureDef:
    """One data capture: a command, then settle + window lines (sensor test file, D-044)."""

    id: str  # "BL", "T10", ...
    command: str  # "baseline" or "response:10"
    tone_hz: float | None  # None for baseline captures
    settle_lines: int
    window_lines: int  # must be a multiple of 30 (D-027)
    title: str = ""  # operator name, e.g. "Tone 10 Hz" (D-053)


@dataclass(frozen=True, slots=True)
class Limit:
    """Inclusive limits compared against unrounded values (D-027). None = unbounded."""

    low: float | None = None
    high: float | None = None

    def passes(self, value: float) -> bool:
        return (self.low is None or value >= self.low) and (self.high is None or value <= self.high)


@dataclass(frozen=True, slots=True)
class StepDef:
    """One limit or validity step (sensor test file, D-040). Measures exactly one thing (D-022) and
    is judged on its own channel's data alone (D-056: no step depends on another)."""

    id: int  # stable 4-digit ID, never reused
    name: str  # stable metric key, e.g. "BL_OFFSET"
    type: StepType
    capture: str  # CaptureDef.id it reads
    metric: str  # key into the @metric registry
    unit: str
    family: FaultFamily | None = None
    title: str = ""  # operator name, e.g. "Baseline offset" (D-053); the screen shows it, records use name


@dataclass(frozen=True, slots=True)
class DiagnosticDef:
    """A measured-but-never-judged quantity, saved in metadata (D-029)."""

    key: str  # same key a promoted limit step would use
    capture: str
    metric: str
    unit: str


@dataclass(frozen=True, slots=True)
class CompiledPlan:
    """A runnable plan: what the plan compiler hands the engine. Immutable for the whole run."""

    sequence: str  # "PROD" or "ENG"
    plan_version: str
    limit_set: str
    captures: tuple[CaptureDef, ...]
    steps: tuple[StepDef, ...]
    diagnostics: tuple[DiagnosticDef, ...]
    limits: Mapping[int, Limit]  # step id -> limit
    constants: Mapping[str, float]  # e.g. fs_nom_hz, tone_present
    plan: str = ""  # test plan id from the sensor test file, e.g. "TP-001"


# ---------------------------------------------------------------- data in flight ----
@dataclass(frozen=True, slots=True, eq=False)
class Window:
    """One capture's measured lines: data[n, sensor] in a.u., plus host arrival times."""

    capture: str
    data: np.ndarray  # shape (N, 8)
    t_host: np.ndarray  # shape (N,), seconds


# ---------------------------------------------------------------- results ----
@dataclass(frozen=True, slots=True)
class StepResult:
    step_id: int
    name: str
    sensor: str  # "ch1".."ch8" or RUN
    capture: str
    value: float | None  # unrounded
    unit: str
    low: float | None
    high: float | None
    result: Result
    not_tested_reason: str = ""
    family: FaultFamily | None = None
    primary_failure: bool = False


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Everything a sink writes for one run (D-029, D-032). Produced for every outcome."""

    run_id: str
    timestamp: str  # ISO 8601
    station: str
    sequence: str
    plan_version: str
    limit_set: str
    app_version: str
    device_serial: str  # the device's own USB serial (automatic)
    outcome: Outcome
    sensor_verdicts: Mapping[str, Result]  # empty for ERROR / ABORTED
    error_code: str = ""  # e.g. "E201@T10"
    attempt: int = 1
    retest_of: str = ""
    results: tuple[StepResult, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)  # versioned JSON (context, diagnostics, raw)
    windows: Mapping[str, Window] = field(default_factory=dict)  # raw data for the sink; not in the CSV row
    dut_serial: str = ""  # entered by the operator for every run (D-047)
    test_plan: str = ""  # e.g. "TP-001"
