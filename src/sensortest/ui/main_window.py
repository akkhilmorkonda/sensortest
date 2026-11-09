"""The station screen (sections 07-09, D-053; docs/gui_spec.md): one screen, two modes.

Top to bottom: title bar (mode switch, plan, fixture status) · control row (DUT serial, buttons,
sequence bar, actuator lamp) · result banner · body (8 live strips + the test steps table, or one
channel enlarged) · serial terminal (test mode adds the @panel tabs beside it).

The window only sends commands (engine.submit) and renders events from the Qt bridge (D-043).
Buttons come from the ACTIONS registry and are enabled from the engine's transition table.
Streams (sample chunks, terminal lines) go into buffers that one timer drains at ui.plot_fps.
"""

import time
from dataclasses import dataclass

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import sensortest.ui.actions
import sensortest.ui.test_mode_panels  # noqa: F401  (registers the test-mode panels)
from sensortest.core.models import SENSORS, CompiledPlan, Outcome, Result, RunRecord
from sensortest.core.operator import failure_message
from sensortest.engine.engine import Engine
from sensortest.engine.states import AppState, Trigger
from sensortest.events.messages import (
    ActuatorChanged,
    AppStateChanged,
    CaptureStarted,
    DeviceStatusChanged,
    Event,
    PlansLoaded,
    RunCompleted,
    RunStarted,
    SamplesArrived,
    StepEvaluated,
    TrafficSeen,
    WarningRaised,
)
from sensortest.ui.bridge import QtBridge
from sensortest.ui.live_plot import LiveStrips, ZoomView, card, label
from sensortest.ui.registry import ACTIONS, PANELS, ActionContext, ActionDef, Placement, View
from sensortest.ui.run_buffer import RunBuffer
from sensortest.ui.step_table import COL_ID, StepTableModel, make_step_view
from sensortest.ui.theme import chip, fmt, unit_label
from sensortest.ui.widgets import ActuatorLamp, ResultBanner, SequenceBar, SerialTerminal, TitleBar, restyle

PRIMARY = {Trigger.START, Trigger.NEXT, Trigger.RETRY}
TERMINAL_PX = 92  # ~4 terminal lines (spec §4.6)
TEST_PANEL_PX = 170  # test mode: room for the run header and diagnostics tabs
STRIPS_PX, STRIPS_TEST_PX = 268, 240
BUSY = {AppState.RUNNING, AppState.SAFE_STATE}


@dataclass(frozen=True, slots=True)
class StationInfo:
    """What the screen shows about the station, from the settings."""

    station: str
    operator_sequence: str
    fixture: str  # the active fixture's name, e.g. "teensy"
    fixture_where: str = ""  # e.g. "USB 16C0:0483" until a run reports the port
    plot_fps: float = 30.0


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine, bridge: QtBridge, info: StationInfo) -> None:
        super().__init__()
        self._engine = engine
        self._bridge = bridge  # keep the bridge alive as long as the window (engine threads emit on it)
        self._info = info
        self._plans: dict[str, CompiledPlan] = {}
        self._mode = View.OPERATOR
        self._buffer = RunBuffer()
        self._last: RunRecord | None = None
        self._run_t0: float | None = None
        self._expected = 0.0
        self._step = (0, 0, "")
        self._buttons: list[tuple[QPushButton, ActionDef]] = []
        self.setWindowTitle(f"Sensor line test - {info.station}")
        self.resize(1280, 800)

        central = QWidget()
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 10)
        root.setSpacing(10)
        where = f" ({info.fixture_where})" if info.fixture_where else ""
        self.titlebar = TitleBar(info.station, f"{info.fixture}{where}")
        self.titlebar.modes.idClicked.connect(self._set_mode)
        root.addWidget(self.titlebar)
        self.banner = ResultBanner()
        root.addLayout(self._controls())
        root.addWidget(self.banner)
        root.addWidget(self._body(), 1)
        root.addWidget(self._bottom())
        self.setCentralWidget(central)
        self._add_buttons()

        bridge.event.connect(self._on_event)
        self._frame = QTimer(self)  # drains the live buffers (D-043)
        self._frame.timeout.connect(self._tick)
        self._frame.start(max(10, int(1000 / max(1.0, info.plot_fps))))
        self._guard = QTimer(self)  # roles can change without an event reaching us first
        self._guard.timeout.connect(self._refresh_buttons)
        self._guard.start(250)
        self._set_mode(0)

    # ---------------------------------------------------------------- layout ----
    def _controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(12)
        self.seq_box = QWidget()
        sv = QVBoxLayout(self.seq_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(3)
        sv.addWidget(label("Sequence", "muted", size=9))
        self.seq_combo = QComboBox()
        self.seq_combo.currentTextChanged.connect(self._sequence_changed)
        sv.addWidget(self.seq_combo)
        row.addWidget(self.seq_box)

        ser = QVBoxLayout()
        ser.setSpacing(3)
        ser.addWidget(label("DUT serial (scan or type, Enter starts)", "muted", size=9))
        self.row_buttons = QHBoxLayout()
        self.serial = QLineEdit()
        self.serial.setPlaceholderText("Scan or type")
        self.serial.setFixedWidth(190)
        self.serial.textChanged.connect(self._serial_changed)
        self.serial.returnPressed.connect(self._start_from_serial)
        self.row_buttons.addWidget(self.serial)
        ser.addLayout(self.row_buttons)
        row.addLayout(ser)

        self.seqbar = SequenceBar()
        row.addWidget(self.seqbar, 1)
        self.lamp = ActuatorLamp()
        row.addWidget(self.lamp)
        return row

    def _body(self) -> QStackedWidget:
        """Page 0: strips + table. Page 1: one channel enlarged over the whole body."""
        self.pages = QStackedWidget()
        body = QWidget()
        h = QHBoxLayout(body)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(12)
        self.strips = LiveStrips(self._buffer)
        self.strips.enlarge.connect(self._enlarge)
        h.addWidget(self.strips)
        table_card = card()
        v = QVBoxLayout(table_card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 7, 12, 7)
        head.addWidget(label("Test steps", "cardtitle"))
        head.addSpacing(8)
        head.addWidget(
            label("Low / High apply to every channel  ·  – = not tested (hover for why)", "muted", size=8), 1
        )
        v.addLayout(head)
        self.model = StepTableModel()
        self.table = make_step_view(self.model)
        v.addWidget(self.table, 1)
        h.addWidget(table_card, 1)
        self.pages.addWidget(body)

        zoom_page = QWidget()
        z = QVBoxLayout(zoom_page)
        z.setContentsMargins(14, 0, 14, 0)
        self.zoom = ZoomView(self._buffer)
        self.zoom.back.connect(lambda: self.pages.setCurrentIndex(0))
        z.addWidget(self.zoom)
        self.pages.addWidget(zoom_page)
        return self.pages

    def _bottom(self) -> QWidget:
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(14, 0, 14, 0)
        box = card()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        self.bottom = QTabWidget()
        self.bottom.setDocumentMode(True)
        self.terminal = SerialTerminal()
        self.terminal.collapse.toggled.connect(lambda _on: self._fit_bottom())
        self.bottom.addTab(self.terminal, "Serial terminal")
        self._panel_tabs: list[tuple[int, View]] = []
        for name in sorted(PANELS, key=lambda n: PANELS.get(n).order):
            p = PANELS.get(name)
            k = self.bottom.addTab(p.factory(self._bridge), p.title)  # type: ignore[arg-type]
            self._panel_tabs.append((k, p.view))
        v.addWidget(self.bottom)
        outer.addWidget(box)
        return wrap

    def _add_buttons(self) -> None:
        for a in sorted((ACTIONS.get(n) for n in ACTIONS), key=lambda d: d.order):
            b = QPushButton(a.label)
            b.setMinimumWidth(72)
            b.clicked.connect(lambda _=False, a=a: self._send(a))
            if a.placement is Placement.CONTROL_ROW:
                self.row_buttons.addWidget(b)
            else:
                self.banner.buttons.addWidget(b)
            if a.shortcut:
                QShortcut(QKeySequence(a.shortcut), self, activated=lambda b=b: self._press(b))
            self._buttons.append((b, a))

    # ---------------------------------------------------------------- modes ----
    def _set_mode(self, k: int) -> None:
        """Operator (0) or test (1): the same screen; test mode adds the extras (spec §7)."""
        self._mode = View.TEST if k == 1 else View.OPERATOR
        test = self._mode is View.TEST
        (self.titlebar.test_btn if test else self.titlebar.operator_btn).setChecked(True)
        self.seq_box.setVisible(test)
        self.table.setColumnHidden(COL_ID, not test)
        for i, view in self._panel_tabs:
            self.bottom.setTabVisible(i, view is self._mode)
        self.bottom.tabBar().setVisible(test)
        self.terminal.title.setVisible(not test)  # the tab already names it
        self.strips.setFixedWidth(STRIPS_TEST_PX if test else STRIPS_PX)  # room for the ID column
        if not test:
            self.bottom.setCurrentIndex(0)
        self._fit_bottom()
        if self._engine.state is AppState.IDLE:
            self._preview()
        self._refresh_buttons()

    def _fit_bottom(self) -> None:
        collapsed = self.terminal.collapse.isChecked()
        test = self._mode is View.TEST
        body = TEST_PANEL_PX if test else TERMINAL_PX
        self.terminal.set_height(body)
        h = 34 + (0 if collapsed else body) + (30 if test else 0)  # header (+ tab bar in test mode)
        self.bottom.setFixedHeight(h)

    def active_sequence(self) -> str:
        if self._mode is View.TEST and self.seq_combo.currentText():
            return self.seq_combo.currentText()
        return self._info.operator_sequence

    def _sequence_changed(self, _text: str) -> None:
        if self._engine.state is AppState.IDLE:
            self._preview()

    def _preview(self) -> None:
        """Before Start: the active plan's steps and captures, all pending (spec §4.2, §4.4)."""
        plan = self._plans.get(self.active_sequence())
        if plan is None:
            return
        self.model.load_plan(plan)
        self.seqbar.load(plan)
        self.titlebar.set_plan(plan)
        for ch in SENSORS:
            self.strips.set_chip(ch, "pend")

    # ---------------------------------------------------------------- events ----
    def _on_event(self, e: Event) -> None:
        match e:
            case SamplesArrived():
                self._buffer.add(e.capture, e.start, e.x)
                return  # ~10 a second: drawn by the frame timer; no button change
            case TrafficSeen():
                self.terminal.add(e)
                return
            case AppStateChanged(state=state, detail=detail):
                self._state_changed(AppState(state), detail)
            case PlansLoaded():
                self._plans = dict(e.plans)
                self.seq_combo.blockSignals(True)
                self.seq_combo.clear()
                self.seq_combo.addItems(list(e.sequences))
                self.seq_combo.setCurrentText(self._info.operator_sequence)
                self.seq_combo.blockSignals(False)
                self._preview()
            case DeviceStatusChanged(roles=roles):
                self.titlebar.set_roles(roles)
            case RunStarted():
                self._run_started(e)
            case CaptureStarted(capture=cap):
                k = self.seqbar.capture_started(cap)
                self._step = (k, self.seqbar.count, self._buffer.title(cap))
                self.model.set_running(cap)
            case ActuatorChanged():
                self.lamp.set(e.on, e.tone_hz, e.command, e.reply)
            case StepEvaluated(result=r):
                self.model.apply(r)
            case RunCompleted(record=rec):
                self._finish(rec)
            case WarningRaised(code=code, detail=detail):
                self.terminal.note(f"⚠ {code}: {detail}")
        self._refresh_buttons()

    def _state_changed(self, state: AppState, detail: str) -> None:
        if state is AppState.STARTING:
            self.banner.starting()
        elif state is AppState.NOT_READY:
            self.banner.not_ready(detail)
        elif state is AppState.IDLE:
            self._preview()
            self.banner.ready()
            self.serial.clear()  # next DUT (D-047)
            self.serial.setFocus()
        elif state is AppState.SAFE_STATE:
            self.banner.safe_state()

    def _run_started(self, e: RunStarted) -> None:
        plan = self._plans.get(e.sequence)
        if plan is not None:
            self.model.load_plan(plan)
            self.seqbar.load(plan)
            self.titlebar.set_plan(plan)
            self._buffer.reset(plan.captures, plan.constants["fs_nom_hz"])
        self.seqbar.run_started()
        self._last = None
        for ch in SENSORS:
            self.strips.set_chip(ch, "pend", "testing…")
        self.strips.subtitle.setText("live, last 2 s · click to enlarge")
        self.zoom.set_result("testing…", "run")
        self._run_t0, self._expected = time.monotonic(), e.expected_s
        self._step = (1, self.seqbar.count, "Connect")
        self.banner.testing(*self._step, 0.0, self._expected)
        self.terminal.note(f"── run {e.run_id} · DUT {e.serial} · {e.sequence} ──")

    def _finish(self, rec: RunRecord) -> None:
        self._run_t0 = None
        self._last = rec
        self.model.apply_record(rec)
        self.seqbar.finish(rec)
        self.banner.result(rec)
        for ch in SENSORS:
            kind, text = self.channel_status(ch)
            self.strips.set_chip(ch, kind, text)
        latest = self._buffer.title(self._buffer.latest_capture)
        self.strips.subtitle.setText(f"end of {latest} · click to enlarge" if latest else "click to enlarge")
        kind, text = self.channel_status(self.zoom.channel)
        self.zoom.set_result(text, kind)
        port = rec.metadata.get("context", {}).get("device", {}).get("port")  # type: ignore[union-attr]
        if port and self._info.fixture_where.startswith("USB"):  # an auto-detected port: now known
            self.titlebar.set_fixture(f"{self._info.fixture} ({port})")

    def channel_status(self, ch: str) -> tuple[str, str]:
        """(kind, text) for a channel: its verdict and primary failure, for the chip and zoom."""
        rec = self._last
        if rec is None:
            return ("run", "testing…") if self._run_t0 is not None else ("pend", "")
        v = rec.sensor_verdicts.get(ch)
        if v is Result.PASS:
            return "pass", f"{chip('pass')} · all steps passed"
        if v is Result.FAIL:
            p = next((r for r in rec.results if r.sensor == ch and r.primary_failure), None)
            if p is None:
                return "fail", chip("fail")
            plan = self._plans.get(rec.sequence)
            title = next((s.title for s in plan.steps if s.id == p.step_id), p.name) if plan else p.name
            u = unit_label(p.unit)
            return "fail", (
                f"{chip('fail')} · {failure_message(p)}: {title} {fmt(p.value, p.unit)} {u} "
                f"(limits {fmt(p.low, p.unit)} … {fmt(p.high, p.unit)})"
            )
        why = "the run was aborted" if rec.outcome is Outcome.ABORTED else f"run ERROR {rec.error_code}"
        return "nt", f"{chip('nt')} · not judged: {why}"

    def _enlarge(self, ch: str) -> None:
        self.pages.setCurrentIndex(1)
        kind, text = self.channel_status(ch)
        self.zoom.show_channel(ch, text, kind)

    # ---------------------------------------------------------------- timer ----
    def _tick(self) -> None:
        self.strips.refresh()
        self.zoom.refresh()
        self.terminal.flush()
        self.seqbar.tick()
        if self._run_t0 is not None and self._engine.state is AppState.RUNNING:
            self.banner.testing(*self._step, time.monotonic() - self._run_t0, self._expected)

    # ---------------------------------------------------------------- buttons ----
    def _send(self, a: ActionDef) -> None:
        self._engine.submit(a.command(ActionContext(self.active_sequence(), self._serial())))

    def _press(self, b: QPushButton) -> None:
        """A shortcut or Enter: clicks the button only if it is shown in this state and enabled."""
        if b.isVisibleTo(self) and b.isEnabled():
            b.click()

    def button(self, label_text: str) -> QPushButton:
        """The button with this label (for tests and tooling)."""
        return next(b for b, a in self._buttons if a.label == label_text)

    def _refresh_buttons(self) -> None:
        state = self._engine.state
        busy = state in BUSY
        serial_ok = self._engine.serial_ok(self._serial())
        for b, a in self._buttons:
            can = a.trigger is None or self._engine.can(a.trigger)
            if a.trigger is Trigger.START:
                can = can and serial_ok
            # Control row: Abort replaces Start / Next while running. Banner: only when it applies.
            visible = (a.trigger is Trigger.ABORT) == busy if a.placement is Placement.CONTROL_ROW else can
            visible = visible and (a.view is None or a.view is self._mode)
            if b.isVisibleTo(self) != visible:
                b.setVisible(visible)
            b.setEnabled(can)
            want = can and a.trigger in PRIMARY
            if bool(b.property("primary")) != want:
                restyle(b, primary=want)
        # The serial can only change between DUTs, never during a run or while a result is shown.
        self.serial.setReadOnly(state is not AppState.IDLE)
        self.titlebar.operator_btn.setEnabled(not busy)
        self.titlebar.test_btn.setEnabled(not busy)
        self.seq_combo.setEnabled(state is AppState.IDLE)

    def _serial(self) -> str:
        return self.serial.text().strip()

    def _serial_changed(self, _text: str) -> None:
        bad = bool(self._serial()) and not self._engine.serial_ok(self._serial())
        restyle(self.serial, bad=bad)
        self._refresh_buttons()

    def _start_from_serial(self) -> None:
        """Enter in the serial field starts the test (scanner-friendly), if Start is allowed."""
        self._press(self.button("Start"))
