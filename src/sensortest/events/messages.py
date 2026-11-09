"""Every message that crosses a module boundary. Immutable, plain data, no logic.

Commands are what the GUI and CLI may ask for; both send exactly the same ones, so the core
can't tell which interface asked (PRD §6.0). Events are what the engine and device manager report.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from sensortest.core.models import CompiledPlan, Role, RunRecord, StepResult


class Command:
    """Base class for commands (adapter -> engine)."""


class Event:
    """Base class for events (engine / device manager -> subscribers)."""


# ---------------------------------------------------------------- commands ----
@dataclass(frozen=True, slots=True)
class StartRun(Command):
    sequence: str = "PROD"
    serial: str = ""  # DUT serial entered by the operator (D-047)


@dataclass(frozen=True, slots=True)
class Abort(Command):
    pass


@dataclass(frozen=True, slots=True)
class Next(Command):
    """Operator acknowledges the result (D-039)."""


@dataclass(frozen=True, slots=True)
class Retry(Command):
    """Once, after a retryable ERROR (D-026)."""


@dataclass(frozen=True, slots=True)
class SelectSequence(Command):
    """Engineering view only; the operator view is locked to PROD (D-040)."""

    sequence: str


@dataclass(frozen=True, slots=True)
class Reload(Command):
    """Re-read settings, sensor test file and limits (from Not ready)."""


@dataclass(frozen=True, slots=True)
class Quit(Command):
    pass


# ---------------------------------------------------------------- events ----
@dataclass(frozen=True, slots=True)
class AppStateChanged(Event):
    state: str  # engine.states.AppState value
    detail: str = ""


@dataclass(frozen=True, slots=True)
class DeviceStatusChanged(Event):
    roles: Mapping[Role, bool]  # drives the connections panel ✓/✗ (D-037)


@dataclass(frozen=True, slots=True)
class PlansLoaded(Event):
    sequences: tuple[str, ...]
    plan_version: str
    limit_set: str
    # The compiled plans, so a view can preview the steps and captures before Start (D-053).
    plans: Mapping[str, CompiledPlan] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunStarted(Event):
    run_id: str
    sequence: str
    expected_s: float
    serial: str = ""


@dataclass(frozen=True, slots=True)
class CaptureStarted(Event):
    capture: str
    index: int
    total: int


@dataclass(frozen=True, slots=True)
class ActuatorChanged(Event):
    """Published after the actuator accepted a command, so it shows what the device is doing."""

    on: bool
    tone_hz: float | None = None
    command: str = ""  # the command text sent, e.g. "response:10"
    reply: str = ""  # the device's accepted reply, e.g. "10.00" ("" for baseline: it has none)


@dataclass(frozen=True, slots=True, eq=False)
class SamplesArrived(Event):
    """A chunk of data lines read during a capture, for the live plot (D-053). Never judged:
    the steps are evaluated on the capture's finished window only."""

    capture: str
    start: int  # index of the chunk's first line in the capture (settle lines first, then window)
    t: np.ndarray  # host times, shape (n,)
    x: np.ndarray  # values, shape (n, 8)


@dataclass(frozen=True, slots=True)
class TrafficSeen(Event):
    """One line on the device connection, for the read-only terminal (D-053).

    direction: "tx" a command sent, "rx" a reply or unreadable line (always reported), "data" a
    data line (thinned by the sensor driver to about 5 per second; the windows are in the raw CSV).
    """

    direction: str
    text: str
    t: float  # wall-clock time (time.time())


@dataclass(frozen=True, slots=True)
class StepEvaluated(Event):
    result: StepResult


@dataclass(frozen=True, slots=True)
class RunCompleted(Event):
    """Every outcome (PASS, FAIL, ERROR, ABORTED) ends with exactly one of these."""

    record: RunRecord


@dataclass(frozen=True, slots=True)
class WarningRaised(Event):
    code: str  # "W101", "W201", ...
    detail: str = ""
