"""On the real Teensy (D-049): skipped unless ``pytest --hardware`` with the device on USB.

Runs PROD and ENG through the Teensy actuator and sensor drivers and expects the same verdicts as
the DOE-1 replay (TP-001 §8). The drivers only ever send `baseline` and `response:1`-`19`, and the
serial link refuses 134 baud, so the firmware is never at risk.
"""

import time
from pathlib import Path

import pytest

from conftest import make_settings
from sensortest.app.wiring import build
from sensortest.core.models import Outcome
from sensortest.engine.states import AppState, Trigger
from sensortest.events.messages import Next, RunCompleted, StartRun
from test_engine_replay import EXPECTED, _verdicts

pytestmark = pytest.mark.hardware


@pytest.mark.parametrize("sequence", ["PROD", "ENG"])
def test_expected_verdicts_on_the_device(tmp_path: Path, sequence: str) -> None:
    app = build(make_settings(tmp_path), fixture="teensy")
    done = []
    app.bus.subscribe(RunCompleted, lambda e: done.append(e.record))
    app.start()
    try:
        deadline = time.monotonic() + 5
        while not (app.engine.state is AppState.IDLE and app.engine.can(Trigger.START)):
            assert time.monotonic() < deadline, f"Teensy not found: {dict(app.devices.roles_present())}"
            time.sleep(0.05)
        app.engine.submit(StartRun(sequence, "SN-HW-0001"))
        deadline = time.monotonic() + 30
        while not done:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        app.engine.submit(Next())
    finally:
        app.stop()
    rec = done[0]
    assert rec.outcome is Outcome.FAIL, (rec.error_code, rec.metadata["context"]["error_detail"])
    assert _verdicts(rec) == EXPECTED
    assert rec.metadata["context"]["device"]["drivers"] == "actuator=teensy;sensor=teensy"
