"""Headless runs (D-034): the same engine, driven by commands from the command line.

Used for batch runs, the ~20-run gauge check (L-v1), CI on the replay driver and debugging.
Exit codes: 0 = all runs completed (whatever their verdicts), 2 = Not ready (bad files),
3 = devices not connected, 4 = a run timed out, 5 = invalid DUT serial.
"""

import queue
import sys
import time
from pathlib import Path

from sensortest.app.wiring import build
from sensortest.core.models import Outcome, Result
from sensortest.core.operator import failure_message
from sensortest.engine.states import AppState
from sensortest.events.messages import AppStateChanged, Event, Next, RunCompleted, RunStarted, StartRun


def run_headless(
    settings_path: Path,
    sequence: str,
    runs: int,
    serial: str,
    fast: bool = False,
    out=sys.stdout,
    fixture: str | None = None,
) -> int:
    app = build(settings_path, fixture=fixture, fast=fast)
    events: queue.Queue[Event] = queue.Queue()
    for t in (AppStateChanged, RunStarted, RunCompleted):
        app.bus.subscribe(t, events.put)
    if not app.engine.serial_ok(serial):
        print(
            f"invalid DUT serial {serial!r} (settings serial_pattern: {app.settings.serial_pattern})",
            file=out,
        )
        return 5
    app.start()
    try:
        state = _wait_state(events, {AppState.IDLE, AppState.NOT_READY}, 10)
        if state is not AppState.IDLE:
            print(f"NOT READY: {state}", file=out)
            return 2
        if not _wait(lambda: all(app.devices.roles_present().values()), 5):
            print(f"devices not connected: {dict(app.devices.roles_present())}", file=out)
            return 3
        for i in range(1, runs + 1):
            app.engine.submit(StartRun(sequence, serial))
            started = _next(events, RunStarted, 10)
            done = _next(events, RunCompleted, 3 * started.expected_s + 15) if started else None
            if done is None:
                print(f"run {i}: timed out", file=out)
                return 4
            print(_summary(i, done.record), file=out)
            app.engine.submit(Next())
            _wait_state(events, {AppState.IDLE}, 5)
        return 0
    finally:
        app.stop()


def _summary(i: int, rec) -> str:
    head = f"run {i}: {rec.dut_serial} {rec.run_id} {rec.sequence} {rec.outcome.value}"
    if rec.outcome in (Outcome.ERROR, Outcome.ABORTED):
        return f"{head} {rec.error_code}".rstrip()
    prim = {r.sensor: failure_message(r) for r in rec.results if r.primary_failure}
    parts = [
        f"{s}:{'PASS' if v is Result.PASS else prim.get(s, 'FAIL')}" for s, v in rec.sensor_verdicts.items()
    ]
    return f"{head} | " + " | ".join(parts)


def _wait(cond, timeout_s: float) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def _next(events: queue.Queue, kind: type, timeout_s: float):
    end = time.monotonic() + timeout_s
    while (left := end - time.monotonic()) > 0:
        try:
            e = events.get(timeout=left)
        except queue.Empty:
            return None
        if isinstance(e, kind):
            return e
    return None


def _wait_state(events: queue.Queue, wanted: set[AppState], timeout_s: float) -> AppState | str | None:
    end = time.monotonic() + timeout_s
    while (left := end - time.monotonic()) > 0:
        try:
            e = events.get(timeout=left)
        except queue.Empty:
            return None
        if isinstance(e, AppStateChanged) and AppState(e.state) in wanted:
            return AppState(e.state) if not e.detail else f"{e.state}: {e.detail}"
    return None
