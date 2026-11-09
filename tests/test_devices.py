"""The device layer (D-049): links, the actuator and sensor drivers, and the device manager.

The Teensy drivers run against FakeTeensy (an in-memory device on a patched pyserial), so every
reply and stream fault is tested without hardware. The real device is in test_teensy_hardware.py.
"""

import threading
import time
from pathlib import Path

import pytest
import serial
import yaml

from conftest import make_settings
from fake_teensy import OTHER_PORT, TEENSY_PORT, FakeTeensy
from sensortest.app.wiring import build
from sensortest.core.errors import Aborted, TestError
from sensortest.core.models import Outcome, Result, Role
from sensortest.devices import serial_link
from sensortest.devices.manager import DeviceManager
from sensortest.devices.registry import LinkInfo
from sensortest.devices.serial_link import SerialLink
from sensortest.devices.teensy.actuator import TeensyActuator
from sensortest.devices.teensy.sensor import TeensySensor
from sensortest.engine.states import AppState
from sensortest.events.messages import Next, RunCompleted, StartRun

INFO = LinkInfo("serial", "COM4", "20071290")
LINK_OPTS = {"type": "serial", "usb_id": "16C0:0483", "open_retry_backoff_s": [0.0, 0.0]}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTeensy:
    """A FakeTeensy behind serial.Serial, and the port list showing it next to another device."""
    dev = FakeTeensy()
    opened: list[dict] = []

    def open_port(port: str, **kw: object) -> FakeTeensy:
        opened.append({"port": port, **kw})
        return dev

    monkeypatch.setattr(serial, "Serial", open_port)
    monkeypatch.setattr(serial_link.list_ports, "comports", lambda: [OTHER_PORT, TEENSY_PORT])
    dev.opened = opened  # type: ignore[attr-defined]
    return dev


@pytest.fixture
def pair(fake: FakeTeensy):
    """One link with both Teensy drivers attached, as the device manager builds it."""
    link = SerialLink.open(INFO, LINK_OPTS)
    act = TeensyActuator(link, {"reply_timeout_s": 0.2})
    sen = TeensySensor(link, {"stall_timeout_s": 0.2})
    yield act, sen, link
    link.close()


# ---------------------------------------------------------------- serial link ----
def test_discovery_finds_the_teensy_by_usb_id_without_opening(fake: FakeTeensy) -> None:
    assert SerialLink.discover(LINK_OPTS) == [INFO]
    assert SerialLink.discover({**LINK_OPTS, "port": "COM9"}) == []
    assert fake.opened == []  # type: ignore[attr-defined]


def test_134_baud_is_refused_before_opening(fake: FakeTeensy) -> None:
    with pytest.raises(TestError) as e:
        SerialLink.open(INFO, {**LINK_OPTS, "baud": 134})
    assert e.value.code.code == "E301"
    assert fake.opened == []  # type: ignore[attr-defined]


def test_port_that_wont_open_is_retried_then_e102(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def busy(port: str, **kw: object) -> None:
        calls.append(port)
        raise serial.SerialException("Access is denied")

    monkeypatch.setattr(serial, "Serial", busy)
    with pytest.raises(TestError) as e:
        SerialLink.open(INFO, LINK_OPTS)
    assert e.value.code.code == "E102" and len(calls) == 3  # first try + 2 retries


def test_device_that_sends_nothing_is_e104(fake: FakeTeensy) -> None:
    fake.streaming = False
    fake._out.clear()
    with pytest.raises(TestError) as e:
        SerialLink.open(INFO, {**LINK_OPTS, "first_line_timeout_s": 0.2})
    assert e.value.code.code == "E104" and fake.closed


# ---------------------------------------------------------------- the two drivers on one link ----
def test_actuator_and_sensor_share_the_link(pair, fake: FakeTeensy) -> None:
    act, sen, _link = pair
    act.baseline()
    act.set_tone(10)
    w = sen.acquire("T10", 3, 30)
    act.baseline()
    assert fake.sent == [b"baseline\n", b"response:10\n", b"baseline\n"]  # LF only (D-007)
    assert w.data.shape == (30, 8) and (w.t_host[1:] >= w.t_host[:-1]).all()
    assert len(fake.opened) == 1  # type: ignore[attr-defined]
    assert fake.opened[0]["baudrate"] == 115200  # type: ignore[attr-defined]


def test_partial_first_line_is_discarded(pair) -> None:
    _, sen, _ = pair
    sen.acquire("BL", 0, 20)  # the fake opens mid-line; that line must not count as unreadable


@pytest.mark.parametrize("hz", [0, 20, 25, 10.5])
def test_tone_outside_1_19_is_never_sent(pair, fake: FakeTeensy, hz: float) -> None:
    act, _, _ = pair
    with pytest.raises(TestError) as e:
        act.set_tone(hz)
    assert e.value.code.code == "E202" and fake.sent == []


@pytest.mark.parametrize(("replies", "label"), [("wrong_ack", "E201@T10"), ("no_reply", "E203@T10")])
def test_bad_tone_replies(pair, fake: FakeTeensy, replies: str, label: str) -> None:
    act, _, _ = pair
    fake.replies = replies
    with pytest.raises(TestError) as e:
        act.set_tone(10)
    assert e.value.label == label


def test_rejected_baseline_is_e103_at_connect_then_e204(pair, fake: FakeTeensy) -> None:
    act, _, _ = pair
    fake.replies = "reject_baseline"
    with pytest.raises(TestError) as first:
        act.baseline()
    with pytest.raises(TestError) as later:
        act.baseline()
    assert (first.value.code.code, later.value.code.code) == ("E103", "E204")


def test_unreadable_line_is_e105_in_the_first_capture_then_e206(pair, fake: FakeTeensy) -> None:
    _, sen, _ = pair
    fake.inject(b"1.00, 2.00, \r\n")
    with pytest.raises(TestError) as first:
        sen.acquire("BL", 0, 30)
    assert first.value.label == "E105"
    sen._captures = 1  # as if BL had passed
    fake.inject(b"1.00, x, 3.00, 4.00, 5.00, 6.00, 7.00, 8.00, \r\n")
    with pytest.raises(TestError) as later:
        sen.acquire("T10", 0, 30)
    assert later.value.label == "E206@T10"


def test_stall_is_e208(pair, fake: FakeTeensy) -> None:
    _, sen, _ = pair
    fake.streaming = False
    t = time.monotonic()
    with pytest.raises(TestError) as e:
        sen.acquire("T10", 0, 30)
    assert e.value.label == "E208@T10" and time.monotonic() - t < 1.0


def test_unplugged_is_e209_for_both_roles(pair, fake: FakeTeensy) -> None:
    act, sen, _link = pair
    fake.unplugged = True
    with pytest.raises(TestError) as e:
        sen.acquire("T10", 0, 30)
    assert e.value.label == "E209@T10"
    with pytest.raises(TestError) as e:
        act.set_tone(10)
    assert e.value.code.code == "E209"


def test_abort_interrupts_acquire_quickly(pair, fake: FakeTeensy) -> None:
    """Abort is seen within a poll period (0.05 s), not after the stall timeout. The stall timeout is
    2 s here, so the bound of 0.5 s separates the two with plenty of margin for a busy CI machine."""
    _, _, link = pair
    sen = TeensySensor(link, {"stall_timeout_s": 2.0})
    fake.streaming = False
    cancel = threading.Event()
    threading.Timer(0.05, cancel.set).start()
    t = time.monotonic()
    with pytest.raises(Aborted):
        sen.acquire("T10", 0, 30, cancel)
    assert time.monotonic() - t < 0.5


def test_closing_the_actuator_sends_baseline(pair, fake: FakeTeensy) -> None:
    act, _, link = pair
    act.set_tone(10)
    act.close()
    link.close()
    assert fake.sent[-1] == b"baseline\n" and fake.closed


# ---------------------------------------------------------------- device manager ----
def _teensy_fixture() -> dict:
    return {
        "links": {"usb": LINK_OPTS},
        "actuator": {"driver": "teensy", "link": "usb", "reply_timeout_s": 0.2},
        "sensor": {"driver": "teensy", "link": "usb", "fs_nom_hz": 150.0, "line_rate_tolerance": 0.01},
    }


def _replay_fixture() -> dict:
    """A replay fixture on synthetic recordings (D-060): no bench data needed."""
    return {
        "links": {"sim": {"type": "replay", "synthetic": True, "realtime": False}},
        "actuator": {"driver": "replay", "link": "sim"},
        "sensor": {"driver": "replay", "link": "sim", "fs_nom_hz": 150.0, "line_rate_tolerance": 0.01},
    }


def test_session_has_two_drivers_on_one_link_opened_once(fake: FakeTeensy) -> None:
    seen = []
    mgr = DeviceManager(_teensy_fixture(), seen.append)
    mgr.poll_once()
    assert seen == [{Role.ACTUATOR: True, Role.SENSOR: True}]
    session = mgr.open_session(frozenset({Role.ACTUATOR, Role.SENSOR}))
    try:
        assert isinstance(session.stimulus, TeensyActuator) and isinstance(session.stream, TeensySensor)
        assert session.stimulus._link is session.stream._link
        assert len(fake.opened) == 1  # type: ignore[attr-defined]
        assert (session.info.port, session.info.serial) == ("COM4", "20071290")
        assert session.info.driver == "actuator=teensy;sensor=teensy"
    finally:
        session.close()
    assert fake.closed and fake.sent[-1] == b"baseline\n"


def test_each_role_follows_its_own_link(tmp_path: Path) -> None:
    fx = _replay_fixture()
    fx["links"]["empty"] = {"type": "replay", "data_dir": tmp_path}  # no recordings: not found
    fx["sensor"]["link"] = "empty"
    seen = []
    mgr = DeviceManager(fx, seen.append)
    mgr.poll_once()
    assert seen == [{Role.ACTUATOR: True, Role.SENSOR: False}]
    with pytest.raises(TestError) as e:
        mgr.open_session(frozenset({Role.ACTUATOR, Role.SENSOR}))
    assert e.value.code.code == "E101" and "sensor" in e.value.detail


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda fx: fx["actuator"].update(driver="nope"), "unknown actuator driver 'nope'"),
        (lambda fx: fx["sensor"].update(driver="teensy"), "can't use a 'replay' link"),
        (lambda fx: fx["links"]["sim"].update(type="usb3"), "unknown link 'usb3'"),
    ],
)
def test_bad_fixture_is_e301(change, message: str) -> None:
    fx = _replay_fixture()
    change(fx)
    with pytest.raises(TestError) as e:
        DeviceManager(fx, lambda _: None)
    assert e.value.code.code == "E301" and message in e.value.detail


# ---------------------------------------------------------------- end to end ----
def test_prod_run_on_the_simulated_teensy_passes(tmp_path: Path, fake: FakeTeensy) -> None:
    """The whole engine on the Teensy pair: a clean simulated device passes PROD (~5 s).

    The fake makes its lines on the host's clock and the link stamps them when read, so on a busy CI
    machine (macOS runners) a delayed reader thread skews the measured line rate by a few percent
    (147.5 Hz seen in CI). This test is about the drivers and the engine, so it allows ±5%; the
    station keeps ±1%, and the line-rate check is tested on replay (drop_lines → E205)."""
    fake.rate = 150.0
    path = make_settings(tmp_path)
    s = yaml.safe_load(path.read_text())
    s["fixtures"]["teensy"]["links"]["usb"]["open_retry_backoff_s"] = [0.0]
    s["fixtures"]["teensy"]["sensor"]["line_rate_tolerance"] = 0.05
    path.write_text(yaml.safe_dump(s))
    app = build(path, fixture="teensy")
    done = []
    app.bus.subscribe(RunCompleted, lambda e: done.append(e.record))
    app.start()
    try:
        deadline = time.monotonic() + 5
        while app.engine.state is not AppState.IDLE or not all(app.devices.roles_present().values()):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        app.engine.submit(StartRun("PROD", "SN-FAKE"))
        deadline = time.monotonic() + 15
        while not done:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        app.engine.submit(Next())
    finally:
        app.stop()
    rec = done[0]
    assert rec.outcome is Outcome.PASS, (rec.error_code, rec.metadata["context"]["error_detail"])
    assert all(v is Result.PASS for v in rec.sensor_verdicts.values())
    assert rec.device_serial == "20071290"
    assert rec.metadata["context"]["device"] == {"drivers": "actuator=teensy;sensor=teensy", "port": "COM4"}
    assert fake.sent == [b"baseline\n", b"response:10\n", b"baseline\n"]  # BL, T10, END


def test_close_after_an_accepted_baseline_sends_nothing_more(pair, fake: FakeTeensy) -> None:
    act, _, _ = pair
    act.set_tone(10)
    act.baseline()
    act.close()
    assert fake.sent == [b"response:10\n", b"baseline\n"]
