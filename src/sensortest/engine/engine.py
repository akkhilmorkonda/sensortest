"""The engine: one worker thread that owns the state machine (D-043).

It takes commands from a queue, runs the capture loop through the device ports, evaluates steps
through the metric lookup, and publishes events. It never imports Qt or a concrete driver, so the
same engine runs under the GUI and headless (D-034). Abort sets a cancel event immediately (from
the caller's thread) so a capture in progress stops within ~0.05 s.
"""

import logging
import queue
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from sensortest.core import methods
from sensortest.core.errors import CODES, Aborted, TestError
from sensortest.core.models import (
    RUN,
    CompiledPlan,
    Outcome,
    Result,
    Role,
    RunRecord,
    StepResult,
    Window,
)
from sensortest.core.ports import ChunkFn, DeviceProvider, DeviceSession, MetricContext
from sensortest.engine import evaluate as ev
from sensortest.engine.states import AppState, Trigger, allowed, next_state
from sensortest.events.bus import EventBus
from sensortest.events.messages import (
    Abort,
    ActuatorChanged,
    AppStateChanged,
    CaptureStarted,
    Command,
    Next,
    PlansLoaded,
    Quit,
    Reload,
    Retry,
    RunCompleted,
    RunStarted,
    SamplesArrived,
    SelectSequence,
    StartRun,
    StepEvaluated,
    WarningRaised,
)

log = logging.getLogger(__name__)
REQUIRED_ROLES = frozenset({Role.ACTUATOR, Role.SENSOR})
METADATA_SCHEMA = "tp001-meta-v2"  # v2: no stimulus_step_deg (D-058)


@dataclass
class _LastRun:
    run_id: str
    sequence: str
    serial: str
    outcome: Outcome
    error_code: str
    retryable: bool
    attempt: int


@dataclass
class _Context:
    """Per-run bookkeeping, built up as the run progresses."""

    commands: list[dict] = field(default_factory=list)
    line_rate_hz: dict[str, float] = field(default_factory=dict)
    durations_s: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class Engine:
    def __init__(
        self,
        bus: EventBus,
        devices: DeviceProvider,
        load_plans: Callable[[], dict[str, CompiledPlan]],
        metric: ev.MetricLookup,
        *,
        station: str,
        app_version: str,
        max_operator_retries: int = 1,
        serial_pattern: str = r"^[A-Za-z0-9_-]{1,32}$",
    ) -> None:
        self._bus = bus
        self._devices = devices
        self._load_plans = load_plans
        self._metric = metric
        self._station = station
        self._app_version = app_version
        self._max_retries = max_operator_retries
        self._serial_re = re.compile(serial_pattern)
        self._plans: dict[str, CompiledPlan] = {}
        self._commands: queue.Queue[Command] = queue.Queue()
        self._cancel = threading.Event()
        self._state = AppState.STARTING
        self._last: _LastRun | None = None
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- public API ----
    @property
    def state(self) -> AppState:
        return self._state

    @property
    def sequences(self) -> tuple[str, ...]:
        return tuple(self._plans)

    def can(self, trigger: Trigger) -> bool:
        """Whether a trigger is allowed now, including guards (the UI uses this for buttons)."""
        if trigger not in allowed(self._state):
            return False
        if trigger in (Trigger.START, Trigger.RETRY) and not self._roles_ok():
            return False
        if trigger is Trigger.RETRY:
            last = self._last
            return bool(
                last
                and last.outcome is Outcome.ERROR
                and last.retryable
                and last.attempt <= self._max_retries
            )
        return True

    def serial_ok(self, serial: str) -> bool:
        """Whether a DUT serial matches the settings' serial_pattern (D-047)."""
        return bool(self._serial_re.fullmatch(serial.strip()))

    def submit(self, command: Command) -> None:
        """Thread-safe, never blocks. Abort also sets the cancel event right away."""
        if isinstance(command, Abort | Quit):
            self._cancel.set()
        self._commands.put(command)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Abort if running (safe state), then join the worker thread with a timeout."""
        self.submit(Quit())
        if self._thread:
            self._thread.join(timeout_s)

    # ---------------------------------------------------------------- loop ----
    def _loop(self) -> None:
        self._load()
        while self._state is not AppState.EXITING:
            try:
                cmd = self._commands.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._handle(cmd)
            except Exception:
                log.exception("engine failed handling %s", type(cmd).__name__)

    def _set(self, trigger: Trigger, detail: str = "") -> None:
        self._state = next_state(self._state, trigger)
        self._bus.publish(AppStateChanged(self._state.value, detail))

    def _load(self) -> None:
        try:
            self._plans = self._load_plans()
        except TestError as e:
            self._set(Trigger.FILES_INVALID, str(e))
            return
        any_plan = next(iter(self._plans.values()))
        self._bus.publish(
            PlansLoaded(tuple(self._plans), any_plan.plan_version, any_plan.limit_set, dict(self._plans))
        )
        self._set(Trigger.FILES_VALID)

    def _handle(self, cmd: Command) -> None:
        """Runs are synchronous here, so a Quit or Abort sent mid-run is seen through the cancel
        event first (the run ends ABORTED), and the queued Quit is handled once in RESULT."""
        match cmd:
            case Quit() if self.can(Trigger.QUIT):
                self._set(Trigger.QUIT)
            case Reload() if self._state is AppState.NOT_READY:
                self._set(Trigger.RELOAD)
                self._load()
            case StartRun(sequence=seq, serial=serial) if self.can(Trigger.START):
                if seq not in self._plans or not self.serial_ok(serial):
                    log.warning("StartRun refused: sequence %r, serial %r", seq, serial)
                    return
                self._cancel.clear()
                self._set(Trigger.START)
                self._run(self._plans[seq], serial.strip(), attempt=1, retest_of="")
            case Retry() if self.can(Trigger.RETRY):
                last = self._last
                assert last is not None
                self._cancel.clear()
                self._set(Trigger.RETRY)
                self._run(
                    self._plans[last.sequence], last.serial, attempt=last.attempt + 1, retest_of=last.run_id
                )
            case Next() if self._state is AppState.RESULT:
                self._set(Trigger.NEXT)
            case Abort() | SelectSequence():
                pass  # Abort is handled through the cancel event; sequence choice is a UI concern
            case _:
                log.info("ignored %s in %s", type(cmd).__name__, self._state.value)

    def _roles_ok(self) -> bool:
        present = self._devices.roles_present()
        return all(present.get(r, False) for r in REQUIRED_ROLES)

    # ---------------------------------------------------------------- one run ----
    def _run(self, plan: CompiledPlan, serial: str, attempt: int, retest_of: str) -> None:
        run_id = f"R{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        fs = plan.constants["fs_nom_hz"]
        expected = sum((c.settle_lines + c.window_lines) / fs for c in plan.captures)
        self._bus.publish(RunStarted(run_id, plan.sequence, expected, serial))
        t0, started = time.monotonic(), datetime.now().isoformat(timespec="seconds")
        ctx = _Context()
        results: list[StepResult] = []
        windows: dict[str, Window] = {}
        diagnostics: dict[str, dict] = {}
        session: DeviceSession | None = None
        outcome, error = Outcome.PASS, None
        verdicts: dict[str, Result] = {}
        try:
            session = self._devices.open_session(REQUIRED_ROLES)
            last_tone: float | None = None
            for k, cap in enumerate(plan.captures, 1):
                if time.monotonic() - t0 > 2 * expected + 5:
                    raise TestError("E210", f"run exceeded {2 * expected + 5:.0f} s", where=cap.id)
                self._bus.publish(CaptureStarted(cap.id, k, len(plan.captures)))
                self._command(session, ctx, cap.command, cap.tone_hz, t0, where=cap.id)
                tc = time.monotonic()
                w = session.stream.acquire(
                    cap.id, cap.settle_lines, cap.window_lines, self._cancel, self._live(cap.id)
                )
                ctx.durations_s[cap.id] = round(time.monotonic() - tc, 3)
                windows[cap.id] = w
                if k == 1:
                    results.append(self._emit(ev.check_connect(plan, w)))
                else:
                    ev.check_line_rate(plan, w)
                ctx.line_rate_hz[cap.id] = round(methods.line_rate(w.t_host), 4)
                mctx = MetricContext(plan, cap.tone_hz, last_tone, windows)
                rows, diags = ev.evaluate_capture(plan, w, mctx, self._metric)
                results.extend(self._emit(r) for r in rows)
                diagnostics[cap.id] = diags
                if cap.tone_hz is not None:
                    last_tone = cap.tone_hz
            self._command(session, ctx, "baseline", None, t0, where="END")  # actuator off (D-010)
            results.append(
                self._emit(StepResult(1900, "RUN_INTEGRITY", RUN, "", 0.0, "", None, None, Result.PASS))
            )
            results, verdicts, outcome = ev.finalize(results)
        except Aborted:
            outcome = Outcome.ABORTED
        except TestError as e:
            outcome, error = Outcome.ERROR, e
        except Exception as e:  # never let a bug leave the actuator running
            log.exception("unexpected error during run %s", run_id)
            outcome, error = Outcome.ERROR, TestError("E901", f"{type(e).__name__}: {e}")
        finally:
            if outcome in (Outcome.ERROR, Outcome.ABORTED):
                self._set(Trigger.ABORT if outcome is Outcome.ABORTED else Trigger.FAULT)
                self._safe_state(session, ctx)
                done = {r.step_id for r in results}
                reason = error.label if error else "ABORTED"
                results += ev.not_tested_remaining(plan, done, reason)
            results = ev.with_run_rows(results, error, outcome)
            if session is not None:
                try:
                    session.close()
                except Exception:
                    log.exception("closing the device session failed")

        record = RunRecord(
            run_id=run_id,
            timestamp=started,
            station=self._station,
            sequence=plan.sequence,
            plan_version=plan.plan_version,
            limit_set=plan.limit_set,
            app_version=self._app_version,
            device_serial=session.info.serial if session else "",
            dut_serial=serial,
            test_plan=plan.plan,
            outcome=outcome,
            sensor_verdicts=verdicts,
            error_code=error.label if error else "",
            attempt=attempt,
            retest_of=retest_of,
            results=tuple(results),
            windows=windows,
            metadata={
                "schema": METADATA_SCHEMA,
                "context": {
                    "fs_nom_hz": fs,
                    "line_rate_hz": ctx.line_rate_hz,
                    "commands": ctx.commands,
                    "duration_s": {**ctx.durations_s, "total": round(time.monotonic() - t0, 3)},
                    "error_detail": error.detail if error else "",
                    "device": {"drivers": session.info.driver, "port": session.info.port} if session else {},
                    "warnings": ctx.warnings,
                },
                "diagnostics": diagnostics,
            },
        )
        self._last = _LastRun(
            run_id,
            plan.sequence,
            serial,
            outcome,
            record.error_code,
            bool(error and error.code.retryable),
            attempt,
        )
        self._set(Trigger.SAFE_DONE if self._state is AppState.SAFE_STATE else Trigger.RUN_DONE)
        self._bus.publish(RunCompleted(record))

    def _command(
        self,
        session: DeviceSession,
        ctx: _Context,
        command: str,
        tone: float | None,
        t0: float,
        where: str,
    ) -> None:
        ctx.commands.append({"t": round(time.monotonic() - t0, 3), "cmd": command})
        try:
            if tone is None:
                session.stimulus.baseline()
                self._bus.publish(ActuatorChanged(False, None, command))
            else:
                reply = session.stimulus.set_tone(tone)
                self._bus.publish(ActuatorChanged(True, tone, command, reply or ""))
        except TestError as e:
            # Drivers don't know the capture; 1900 codes carry it (E204@END, TP-001 §9).
            if e.where is None and e.code.code.startswith("E2"):
                e.where = where
            raise

    def _safe_state(self, session: DeviceSession | None, ctx: _Context) -> None:
        """Send baseline, one retry (D-010); W101 if it still fails."""
        if session is None:
            return
        for attempt in (1, 2):
            try:
                session.stimulus.baseline()
                self._bus.publish(ActuatorChanged(False, None, "baseline"))
                return
            except Exception as e:
                log.warning("safe-state baseline attempt %d failed: %s", attempt, e)
        ctx.warnings.append("W101")
        self._bus.publish(WarningRaised("W101", CODES["W101"].meaning))

    def _live(self, capture: str) -> ChunkFn:
        """The on_chunk callback for one capture: publishes SamplesArrived for the live plot."""

        def chunk(start: int, t: np.ndarray, x: np.ndarray) -> None:
            self._bus.publish(SamplesArrived(capture, start, t, x))

        return chunk

    def _emit(self, result: StepResult) -> StepResult:
        self._bus.publish(StepEvaluated(result))
        return result
