"""The plumbing the station screen needs (D-053, GUI spec §9): plans before Start, step titles,
live sample chunks, serial traffic for the terminal, and the actuator's accepted reply.

None of it may change a result: the chunks must add up to exactly the window that was judged.
"""

import queue
import time
from pathlib import Path

import numpy as np
import pytest
import yaml

from conftest import CONFIG, make_settings
from fake_teensy import FakeTeensy
from sensortest.app.wiring import build
from sensortest.core.models import Outcome
from sensortest.devices.taps import CHUNK_LINES, DATA_INTERVAL_S, DataThinner
from sensortest.engine.states import AppState, Trigger
from sensortest.events.messages import (
    ActuatorChanged,
    Event,
    PlansLoaded,
    RunCompleted,
    SamplesArrived,
    StartRun,
    TrafficSeen,
)
from sensortest.plans.compiler import compile_plans
from test_devices import INFO, LINK_OPTS, fake  # noqa: F401  (the fake fixture)


def _run(settings: Path, sequence: str = "PROD") -> tuple[list[Event], object]:
    app = build(settings)
    seen: queue.Queue[Event] = queue.Queue()
    app.bus.subscribe(Event, seen.put)
    app.start()
    try:
        end = time.monotonic() + 5
        while not (app.engine.state is AppState.IDLE and app.engine.can(Trigger.START)):
            assert time.monotonic() < end
            time.sleep(0.02)
        app.engine.submit(StartRun(sequence, "SN-0001"))
        events: list[Event] = []
        end = time.monotonic() + 30
        while True:
            e = seen.get(timeout=max(0.1, end - time.monotonic()))
            events.append(e)
            if isinstance(e, RunCompleted):
                return events, e.record
    finally:
        app.stop()


@pytest.fixture(scope="module")
def prod_run(tmp_path_factory: pytest.TempPathFactory):
    return _run(make_settings(tmp_path_factory.mktemp("live")))


def test_plans_loaded_carries_the_compiled_plans(prod_run) -> None:
    events, _ = prod_run
    loaded = next(e for e in events if isinstance(e, PlansLoaded))
    assert set(loaded.plans) == {"PROD", "ENG"} == set(loaded.sequences)
    prod = loaded.plans["PROD"]
    assert [s.id for s in prod.steps] == [1000, 2040, 2050, 6040, 6050, 1900]
    assert [s.title for s in prod.steps] == [
        "Connection",
        "Baseline offset",
        "Baseline noise",
        "Response amplitude",
        "Response frequency",
        "Run integrity",
    ]
    assert [c.title for c in prod.captures] == ["Baseline", "Tone 10 Hz"]


def test_sample_chunks_add_up_to_the_judged_windows(prod_run) -> None:
    events, rec = prod_run
    assert rec.outcome is Outcome.FAIL
    plan = next(e for e in events if isinstance(e, PlansLoaded)).plans["PROD"]
    for cap in plan.captures:
        chunks = [e for e in events if isinstance(e, SamplesArrived) and e.capture == cap.id]
        assert all(0 < len(c.x) <= CHUNK_LINES for c in chunks)
        assert [c.start for c in chunks] == list(range(0, cap.settle_lines + cap.window_lines, CHUNK_LINES))
        x = np.vstack([c.x for c in chunks])
        t = np.concatenate([c.t for c in chunks])
        assert x.shape == (cap.settle_lines + cap.window_lines, 8)
        np.testing.assert_array_equal(x[cap.settle_lines :], rec.windows[cap.id].data)
        np.testing.assert_array_equal(t[cap.settle_lines :], rec.windows[cap.id].t_host)


def test_traffic_has_every_command_and_reply_and_thinned_data(prod_run) -> None:
    events, _ = prod_run
    traffic = [e for e in events if isinstance(e, TrafficSeen)]
    tx = [e.text for e in traffic if e.direction == "tx"]
    rx = [e.text for e in traffic if e.direction == "rx"]
    data = [e.text for e in traffic if e.direction == "data"]
    assert tx == ["baseline", "response:10", "baseline"]
    assert rx == ["10.00"]  # `baseline` has no reply
    # 2 captures of 330 lines at 150 Hz = 4.4 s of data, shown at one line per 0.2 s
    assert 15 <= len(data) <= 4.4 / DATA_INTERVAL_S + 2
    assert all(len(d.rstrip(",").split(",")) == 8 for d in data)  # printed as the device prints


def test_actuator_changed_carries_the_command_and_accepted_reply(prod_run) -> None:
    events, _ = prod_run
    changes = [e for e in events if isinstance(e, ActuatorChanged)]
    assert changes[0] == ActuatorChanged(False, None, "baseline", "")
    assert changes[1] == ActuatorChanged(True, 10.0, "response:10", "10.00")
    assert changes[-1].on is False


def test_titles_fall_back_to_the_step_name_and_tone(tmp_path: Path) -> None:
    raw = yaml.safe_load((CONFIG / "sensor_tests.yaml").read_text(encoding="utf-8"))
    for item in (*raw["steps"], *raw["captures"]):
        item.pop("title", None)
    path = tmp_path / "tests.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    plans = compile_plans(
        path,
        CONFIG / "TP-001_limits_v2.yaml",
        {"PROD": (2040, 6040)},
        {
            "fs_nom_hz": 150,
            "line_rate_tolerance": 0.01,
        },
    )
    p = plans["PROD"]
    assert [s.title for s in p.steps] == ["Connection", "BL_OFFSET", "T10_AMPLITUDE", "Run integrity"]
    assert [c.title for c in p.captures] == ["BL", "Tone 10 Hz"]


def test_thinner_lets_one_line_through_per_interval() -> None:
    thin = DataThinner(0.2)
    shown = [t for t in np.arange(0, 2.0, 1 / 150) if thin.due(float(t))]
    assert len(shown) == 10 and np.allclose(np.diff(shown), 0.2, atol=1 / 150)


def test_teensy_drivers_report_chunks_and_traffic(fake: FakeTeensy) -> None:  # noqa: F811
    from sensortest.devices.serial_link import SerialLink
    from sensortest.devices.teensy.actuator import TeensyActuator
    from sensortest.devices.teensy.sensor import TeensySensor

    link = SerialLink.open(INFO, LINK_OPTS)
    try:
        act = TeensyActuator(link, {"reply_timeout_s": 0.2})
        sen = TeensySensor(link, {"stall_timeout_s": 0.2})
        seen: list[tuple[str, str]] = []
        act.on_traffic = sen.on_traffic = lambda d, text: seen.append((d, text))
        act.baseline()
        assert act.set_tone(10) == "10.00"
        chunks: list[tuple[int, np.ndarray, np.ndarray]] = []
        w = sen.acquire("T10", 5, 40, on_chunk=lambda s, t, x: chunks.append((s, t, x)))
    finally:
        link.close()
    assert [s for s, _, _ in chunks] == [0, 15, 30]
    x = np.vstack([c[2] for c in chunks])
    np.testing.assert_array_equal(x[5:], w.data)
    assert seen[:3] == [("tx", "baseline"), ("tx", "response:10"), ("rx", "10.00")]
    data = [text for d, text in seen if d == "data"]
    assert 1 <= len(data) <= 2  # 45 lines at the fake's 1500 lines/s span 0.03 s: one shown
    assert data[0].count(",") == 8  # the raw device line, trailing comma included
