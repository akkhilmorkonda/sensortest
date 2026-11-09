"""End to end on the replay driver: the whole stack from StartRun to the CSV record.

The TP-001 expected verdicts become assertions (TP-001 §8), and every injected fault must end in
the right outcome and error code with the device safe-stated.
"""

import csv
import json
import queue
import time
from pathlib import Path

import pytest

from conftest import make_settings
from sensortest.app.wiring import App, build
from sensortest.core.models import Outcome, Result, RunRecord
from sensortest.core.operator import failure_message
from sensortest.engine.states import AppState, Trigger
from sensortest.events.messages import Abort, Next, Retry, RunCompleted, StartRun

SERIAL = "SN-0001"
EXPECTED = {
    "ch1": "Weak response",
    "ch2": "PASS",
    "ch3": "PASS",
    "ch4": "Noisy",
    "ch5": "Offset out of range",
    "ch6": "PASS",
    "ch7": "PASS",
    "ch8": "Wrong frequency",
}


def _start(settings: Path) -> tuple[App, queue.Queue]:
    app = build(settings)
    done: queue.Queue = queue.Queue()
    app.bus.subscribe(RunCompleted, lambda e: done.put(e.record))
    app.start()
    deadline = time.monotonic() + 5
    while not (app.engine.state is AppState.IDLE and app.engine.can(Trigger.START)):
        assert time.monotonic() < deadline, f"not ready: {app.engine.state}"
        time.sleep(0.02)
    return app, done


def _run(app: App, done: queue.Queue, cmd=None) -> RunRecord:
    app.engine.submit(cmd or StartRun("PROD", SERIAL))
    return done.get(timeout=20)


def _verdicts(rec: RunRecord) -> dict[str, str]:
    prim = {r.sensor: failure_message(r) for r in rec.results if r.primary_failure}
    return {s: ("PASS" if v is Result.PASS else prim[s]) for s, v in rec.sensor_verdicts.items()}


@pytest.mark.parametrize("sequence", ["PROD", "ENG"])
def test_expected_verdicts_and_csv_record(tmp_path: Path, sequence: str) -> None:
    app, done = _start(make_settings(tmp_path))
    try:
        rec = _run(app, done, StartRun(sequence, SERIAL))
    finally:
        app.stop()
    assert rec.outcome is Outcome.FAIL
    assert _verdicts(rec) == EXPECTED
    assert rec.dut_serial == SERIAL and rec.test_plan == "TP-001"
    day = next((tmp_path / "out").glob("20*"))
    path = day / f"{SERIAL}_{rec.run_id}.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    n_limits = 4 if sequence == "PROD" else 14  # no phase steps (D-056)
    assert len(rows) == len(rec.results) == 2 + n_limits * 8
    assert {r["serial"] for r in rows} == {SERIAL} and {r["test_plan"] for r in rows} == {"TP-001"}
    assert {r["run_id"] for r in rows} == {rec.run_id} and {r["outcome"] for r in rows} == {"FAIL"}
    run_rows = [r for r in rows if r["sensor"] == "RUN"]
    assert [r["step_id"] for r in run_rows] == ["1000", "1900"]
    assert all(r["metadata"] for r in run_rows) and not any(
        r["metadata"] for r in rows if r["sensor"] != "RUN"
    )
    assert json.loads(run_rows[0]["metadata"])["raw"]["path"] == f"{SERIAL}_{rec.run_id}_raw.csv"
    assert (day / f"{SERIAL}_{rec.run_id}_raw.csv").exists()


def test_each_run_gets_its_own_file_even_for_the_same_serial(tmp_path: Path) -> None:
    app, done = _start(make_settings(tmp_path))
    try:
        first = _run(app, done)
        app.engine.submit(Next())
        second = _run(app, done)
    finally:
        app.stop()
    files = sorted(p.name for p in (tmp_path / "out").glob("20*/*.csv") if not p.name.endswith("_raw.csv"))
    assert files == sorted([f"{SERIAL}_{first.run_id}.csv", f"{SERIAL}_{second.run_id}.csv"])


@pytest.mark.parametrize("bad", ["", "  ", "SN 0001", "SN/0001", "x" * 33])
def test_start_is_refused_without_a_valid_serial(tmp_path: Path, bad: str) -> None:
    app, done = _start(make_settings(tmp_path))
    try:
        assert not app.engine.serial_ok(bad)
        app.engine.submit(StartRun("PROD", bad))
        with pytest.raises(queue.Empty):
            done.get(timeout=0.5)
        assert app.engine.state is AppState.IDLE
    finally:
        app.stop()


def test_abort_mid_run_records_aborted(tmp_path: Path) -> None:
    app, done = _start(make_settings(tmp_path, realtime=True))
    try:
        app.engine.submit(StartRun("PROD", SERIAL))
        time.sleep(0.5)
        t = time.monotonic()
        app.engine.submit(Abort())
        rec = done.get(timeout=5)
        assert time.monotonic() - t < 1.0  # Abort within ~0.5 s (D-043)
    finally:
        app.stop()
    assert rec.outcome is Outcome.ABORTED
    assert all(r.result is Result.NOT_TESTED for r in rec.results if r.step_id not in (1000, 2040, 2050))


@pytest.mark.parametrize(
    ("fault", "code", "retryable"),
    [
        ("wrong_ack", "E201@T10", False),
        ("no_reply", "E203@T10", False),
        ("stall", "E208@T10", False),
        ("drop_lines", "E205@T10", True),
        ("lost", "E209@T10", True),
        ("reject_baseline", "E103", False),
    ],
)
def test_faults_end_in_error_with_the_right_code(
    tmp_path: Path, fault: str, code: str, retryable: bool
) -> None:
    app, done = _start(make_settings(tmp_path, fault=fault))
    try:
        rec = _run(app, done)
        assert rec.outcome is Outcome.ERROR and rec.error_code == code
        assert not rec.sensor_verdicts  # no verdicts on ERROR
        run_rows = {r.step_id: r.result for r in rec.results if r.sensor == "RUN"}
        assert run_rows[1000 if code.startswith("E1") else 1900] is Result.FAIL
        assert app.engine.state is AppState.RESULT
        assert app.engine.can(Trigger.RETRY) is retryable
        if retryable:
            rec2 = _run(app, done, Retry())
            assert rec2.attempt == 2 and rec2.retest_of == rec.run_id
            assert not app.engine.can(Trigger.RETRY)  # only once (D-026)
        app.engine.submit(Next())
    finally:
        app.stop()


def test_start_needs_next_after_a_result(tmp_path: Path) -> None:
    app, done = _start(make_settings(tmp_path))
    try:
        _run(app, done)
        assert not app.engine.can(Trigger.START)
        app.engine.submit(StartRun("PROD", SERIAL))  # ignored in RESULT
        with pytest.raises(queue.Empty):
            done.get(timeout=0.5)
        app.engine.submit(Next())
        _run(app, done)
    finally:
        app.stop()


def test_bad_files_go_to_not_ready(tmp_path: Path) -> None:
    import yaml

    path = make_settings(tmp_path)
    s = yaml.safe_load(path.read_text())
    s["plans"]["PROD"] = [2040, 9999]
    path.write_text(yaml.safe_dump(s))
    app = build(path)
    app.start()
    try:
        deadline = time.monotonic() + 5
        while app.engine.state is not AppState.NOT_READY:
            assert time.monotonic() < deadline
            time.sleep(0.02)
    finally:
        app.stop()
