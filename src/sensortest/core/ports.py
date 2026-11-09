"""Ports: the interfaces the core defines and the edge implements (PRD §6.0).

Drivers implement Stimulus / SensorStream / DeviceSession; sinks implement ResultSink;
metric plugins match MetricFn. The engine depends only on these, never on a concrete adapter.
"""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np

from sensortest.core.models import CompiledPlan, Role, RunRecord, Window


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """What the device manager knows about a connected device, without opening it."""

    driver: str  # registry name, e.g. "teensy"
    port: str  # e.g. "COM4"
    serial: str
    roles: frozenset[Role]


@runtime_checkable
class Stimulus(Protocol):
    """Actuator role. Commands return only once accepted; any protocol fault raises TestError."""

    def baseline(self) -> None: ...

    def set_tone(self, hz: float) -> str:
        """Start the tone; returns the device's accepted reply (e.g. "10.00")."""
        ...


# A chunk of lines as it is read: (index of its first line in the capture, host times, values).
ChunkFn = Callable[[int, np.ndarray, np.ndarray], None]


@runtime_checkable
class SensorStream(Protocol):
    """Sensor role. Line-count based acquisition (D-027); stalls and drops raise TestError."""

    def acquire(
        self,
        capture: str,
        settle_lines: int,
        window_lines: int,
        cancel: threading.Event | None = None,
        on_chunk: ChunkFn | None = None,
    ) -> Window:
        """Discard settle_lines, return the next window_lines. Raises Aborted if cancel is set.

        ``on_chunk`` gets every line read (settle lines too) in chunks of about 0.1 s, as they
        arrive, for the live plot (D-053). It runs on the caller's thread and must only enqueue.
        """
        ...


class DeviceSession(Protocol):
    """An opened device for one run. Closing must leave the device safe (D-010)."""

    info: DeviceInfo
    stimulus: Stimulus
    stream: SensorStream

    def close(self) -> None: ...


class DeviceProvider(Protocol):
    """What the engine needs from the device manager (implemented in devices/)."""

    def roles_present(self) -> Mapping[Role, bool]: ...

    def open_session(self, required: frozenset[Role]) -> DeviceSession: ...


class ResultSink(Protocol):
    """An output adapter (CSV now; AWS, MES/STDF later). Must not change results."""

    name: str

    def write_run(self, record: RunRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class MetricContext:
    """Everything a metric function may read besides its own window."""

    plan: CompiledPlan
    tone_hz: float | None  # this capture's tone (None for baseline captures)
    last_tone_hz: float | None  # the most recent tone before this capture (for recovery checks)
    windows: Mapping[str, Window]  # every capture acquired so far, by id


@dataclass(frozen=True, slots=True)
class Measured:
    """A metric's output: one value per sensor (shape (8,)); NaN plus a reason = NOT TESTED."""

    values: np.ndarray
    not_tested: Mapping[int, str] = field(default_factory=dict)  # sensor index -> reason


class MetricFn(Protocol):
    """A registered metric (@metric): one window in, one value per sensor out."""

    def __call__(self, window: Window, ctx: MetricContext) -> Measured: ...
