"""The station screen (offscreen, D-053): runs through its own buttons, end to end on replay.

Checks what the operator reads: the banner, the step table cells, the channel chips, the live
buffer, the terminal and the actuator lamp; then ERROR + Retry, test mode and click-to-enlarge.
"""

import os
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import make_settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt6.QtWidgets")

from sensortest.app.gui import station_info  # noqa: E402
from sensortest.app.wiring import build  # noqa: E402
from sensortest.core.models import CaptureDef  # noqa: E402
from sensortest.engine.states import AppState  # noqa: E402
from sensortest.ui import theme  # noqa: E402
from sensortest.ui.bridge import QtBridge  # noqa: E402
from sensortest.ui.main_window import MainWindow  # noqa: E402
from sensortest.ui.run_buffer import RunBuffer  # noqa: E402
from sensortest.ui.step_table import COL_CH0, COL_CODE, COL_ID, COL_RESULT, COL_TEST  # noqa: E402


def _pump(qt, cond, timeout_s: float = 15.0) -> None:
    end = time.monotonic() + timeout_s
    while not cond():
        assert time.monotonic() < end, "timed out"
        qt.processEvents()
        time.sleep(0.01)


@pytest.fixture
def station(tmp_path: Path, request: pytest.FixtureRequest):
    """The window on the replay fixture, started and idle with both roles connected."""
    fault = getattr(request, "param", "none")
    qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(qt)
    app = build(make_settings(tmp_path, fault=fault))
    win = MainWindow(app.engine, QtBridge(app.bus), station_info(app.settings))
    app.start()
    try:
        _pump(
            qt, lambda: app.engine.state is AppState.IDLE and "Sensor connected" in win.titlebar.roles_text()
        )
        yield qt, app, win
    finally:
        app.stop()
        win.close()


def _row(win: MainWindow, step_id: int) -> int:
    m = win.model
    return next(k for k in range(m.rowCount()) if m.cell(k, COL_ID)[0] == str(step_id))


def _start(qt, win: MainWindow, serial: str = "SN-0001") -> None:
    win.serial.setText(serial)
    _pump(qt, win.button("Start").isEnabled)
    win.serial.returnPressed.emit()  # scanner-style Enter starts the run


def test_operator_runs_prod_from_the_buttons(station) -> None:
    qt, app, win = station
    start, nxt = win.button("Start"), win.button("Next")
    assert win.model.rowCount() == 7  # the PROD preview: 6 steps + Channel result, before Start
    assert win.model.cell(_row(win, 2040), COL_CH0)[1] == "pend"
    assert win.seqbar.count == 4  # Connect, Baseline, Tone 10 Hz, End
    assert not start.isEnabled()  # no serial yet (D-047)
    win.serial.setText("SN 1")  # invalid: space
    qt.processEvents()
    assert not start.isEnabled()
    _start(qt, win)
    _pump(qt, lambda: win.banner.kind == "fail")

    assert win.banner.big.text() == "✗ FAIL"
    why = win.banner.why.text()
    assert why == "4 of 8 channels failed:   ch1   ·   ch4   ·   ch5   ·   ch8"  # which, not why
    assert "Wrong frequency" not in why and "Noisy" not in why
    m = win.model
    text, kind, _ = m.cell(_row(win, 2040), COL_CH0 + 4)  # ch5 offset, ~1349 a.u.
    assert kind == "fail" and text.startswith("✗ 13")
    assert m.cell(_row(win, 6050), COL_CH0)[1] == "nt"  # ch1: no tone, frequency not tested
    # ch8 responds at 20 Hz: it fails frequency only; its amplitude is its real response (D-056)
    assert m.cell(_row(win, 6050), COL_CH0 + 7)[1] == "fail"
    assert m.cell(_row(win, 6040), COL_CH0 + 7)[1] == "pass"
    assert m.cell(_row(win, 6040), COL_RESULT)[1] == "fail"  # ch1 and ch4 still fail amplitude
    assert "not tested" in m.cell(_row(win, 6050), COL_CH0)[2]
    assert m.cell(_row(win, 1000), COL_CH0)[0].startswith("150.01 Hz line rate")
    assert m.cell(_row(win, 1900), COL_RESULT)[1] == "pass"
    verdict = m.rowCount() - 1
    assert m.cell(verdict, COL_TEST)[0] == "Channel result"
    assert m.cell(verdict, COL_CH0 + 1)[:2] == ("✓ PASS", "pass")
    assert win.strips.chips["ch8"].text() == "✗ FAIL" and win.strips.chips["ch2"].text() == "✓ PASS"
    assert "Wrong frequency" in win.strips.chips["ch8"].toolTip()
    assert win._buffer.lines == 660  # every line of both captures, settle included
    win._tick()
    log = win.terminal.view.toPlainText()
    assert "→ response:10" in log and "← 10.00" in log and log.count("→ baseline") == 2
    assert win.lamp.state.text() == "Actuator OFF"

    assert not start.isVisibleTo(win) or not start.isEnabled()  # Start waits for Next (D-039)
    assert nxt.isEnabled()
    nxt.click()
    _pump(qt, lambda: app.engine.state is AppState.IDLE)
    qt.processEvents()
    assert win.serial.text() == "" and not start.isEnabled()  # cleared for the next DUT
    assert win.model.cell(_row(win, 2040), COL_CH0)[1] == "pend"  # the preview again
    assert win.banner.kind == "idle"


@pytest.mark.parametrize("station", ["drop_lines"], indirect=True)
def test_error_offers_retry_in_the_banner_once(station) -> None:
    qt, app, win = station
    retry = win.button("Retry")
    assert not retry.isVisibleTo(win)
    _start(qt, win)
    _pump(qt, lambda: win.banner.kind == "err")
    assert win.banner.big.text() == "⚠ ERROR  E205@T10"
    assert "Retry once" in win.banner.meta.text()
    m = win.model
    assert m.cell(_row(win, 1900), COL_CODE)[0] == "E205@T10"
    assert m.cell(_row(win, 1900), COL_CH0)[0].startswith("line rate")
    assert m.cell(_row(win, 6040), COL_CH0)[1] == "nt"
    assert retry.isVisibleTo(win) and retry.isEnabled()
    assert win.strips.chips["ch1"].text() == "– N/T"

    retry.click()  # the replayed fault comes back on the retry: ERROR again, and no third try
    _pump(qt, lambda: win._last is not None and win._last.attempt == 2)
    assert app.engine.state is AppState.RESULT and win.banner.kind == "err"
    win._refresh_buttons()
    assert not retry.isVisibleTo(win)
    assert win.button("Next").isEnabled()


def test_test_mode_adds_ids_panels_and_the_eng_preview(station) -> None:
    qt, _app, win = station
    assert win.table.isColumnHidden(COL_ID) and not win.bottom.tabBar().isVisibleTo(win)
    win.titlebar.test_btn.click()
    qt.processEvents()
    assert not win.table.isColumnHidden(COL_ID)
    assert [win.bottom.tabText(i) for i in range(win.bottom.count())] == [
        "Serial terminal",
        "Run header",
        "Diagnostics",
    ]
    assert win.bottom.tabBar().isVisibleTo(win) and win.seq_box.isVisibleTo(win)
    win.seq_combo.setCurrentText("ENG")
    qt.processEvents()
    kinds = [win.model.row_kind(k) for k in range(win.model.rowCount())]
    assert kinds.count("heading") == 5 and kinds.count("limit") == 14  # grouped by capture
    assert win.seqbar.count == 7 and win.active_sequence() == "ENG"
    win.titlebar.operator_btn.click()
    qt.processEvents()
    assert win.active_sequence() == "PROD" and win.model.rowCount() == 7


def test_click_to_enlarge_shows_one_channel_over_the_body(station) -> None:
    qt, _app, win = station
    _start(qt, win)
    _pump(qt, lambda: win.banner.kind == "fail")
    win._enlarge("ch4")
    qt.processEvents()
    assert win.pages.currentIndex() == 1
    assert win.zoom.title.text() == "ch4: the whole run"
    assert "Noisy" in win.zoom.result.text() and "Baseline noise" in win.zoom.result.text()
    assert [s.title for s in win._buffer.spans()] == ["Baseline", "Tone 10 Hz"]
    win.zoom.back.emit()
    assert win.pages.currentIndex() == 0


def test_run_buffer_lays_captures_end_to_end() -> None:
    buf = RunBuffer(150.0)
    caps = (
        CaptureDef("BL", "baseline", None, 30, 300, "Baseline"),
        CaptureDef("T10", "response:10", 10.0, 30, 300),
    )
    buf.reset(caps)
    for start in range(0, 330, 15):
        buf.add("BL", start, np.full((15, 8), 1.0))
    buf.add("T10", 0, np.full((15, 8), 2.0))
    assert buf.lines == 345 and buf.latest_capture == "T10"
    spans = buf.spans()
    assert [(s.capture, s.tone) for s in spans] == [("BL", False)]  # T10's window hasn't started yet
    assert spans[0].t0 == pytest.approx(30 / 150) and spans[0].t1 == pytest.approx(330 / 150)
    t, x = buf.last(0.2)
    assert len(t) == 30 and (x[-15:] == 2.0).all() and t[-1] == pytest.approx(344 / 150)
    assert buf.title("T10") == "T10" and buf.title("BL") == "Baseline"
