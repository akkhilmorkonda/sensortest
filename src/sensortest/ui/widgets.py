"""The regions of the station screen around the table (GUI spec §4): title bar, sequence bar,
actuator lamp, result banner and serial terminal. Each is styled by a dynamic property and the
stylesheet in theme.py, and is driven by the main window from engine events.
"""

import html
import time
from collections import deque
from collections.abc import Mapping
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sensortest.core.errors import CODES
from sensortest.core.models import SENSORS, CompiledPlan, Outcome, Result, Role, RunRecord
from sensortest.events.messages import TrafficSeen
from sensortest.ui.live_plot import label
from sensortest.ui.theme import INK, STATUS, TERMINAL_LINES


def restyle(w: QWidget, **props: object) -> None:
    """Set dynamic properties and re-apply the stylesheet (Qt does not re-polish on its own)."""
    for k, v in props.items():
        w.setProperty(k, v)
    w.style().unpolish(w)
    w.style().polish(w)


# ---------------------------------------------------------------- title bar (§4.1) ----
class TitleBar(QFrame):
    """App name, the mode switch, plan / limits / station, and the fixture status (D-037)."""

    def __init__(self, station: str, fixture: str) -> None:
        super().__init__()
        self.setObjectName("titlebar")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 7, 14, 7)
        row.addWidget(label("Sensor line test", "app"))
        row.addSpacing(10)
        self.modes = QButtonGroup(self)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        self.operator_btn, self.test_btn = QPushButton("Operator"), QPushButton("Test")
        for k, b in enumerate((self.operator_btn, self.test_btn)):
            b.setProperty("seg", True)
            b.setCheckable(True)
            b.setChecked(k == 0)
            self.modes.addButton(b, k)
            seg.addWidget(b)
        row.addLayout(seg)
        row.addSpacing(16)
        self._station = station
        self.plan = label(f"Loading the test plan…  ·  Station {station}", "muted")
        row.addWidget(self.plan)
        row.addStretch(1)
        self.fixture = label(f"Fixture {fixture}", "muted")
        row.addWidget(self.fixture)
        row.addSpacing(12)
        self.roles = {Role.ACTUATOR: label(), Role.SENSOR: label()}
        for w in self.roles.values():
            row.addWidget(w)
            row.addSpacing(8)
        self.set_roles({})

    def set_plan(self, plan: CompiledPlan) -> None:
        self.plan.setText(
            f"Plan {plan.plan} {plan.sequence} {plan.plan_version}  ·  Limits {plan.limit_set}  ·  "
            f"Station {self._station}"
        )

    def set_fixture(self, text: str) -> None:
        self.fixture.setText(f"Fixture {text}")

    def roles_text(self) -> str:
        """The fixture status as shown (rich text), e.g. for tests."""
        return "  ".join(w.text() for w in self.roles.values())

    def set_roles(self, roles: Mapping[Role, bool]) -> None:
        for role, w in self.roles.items():
            ok = bool(roles.get(role))
            colour = STATUS["pass" if ok else "fail"][0]
            w.setText(
                f"<span style='color:{colour}'>●</span> {role.value.capitalize()} "
                f"{'connected' if ok else 'not found'}"
            )


# ---------------------------------------------------------------- sequence bar (§4.2) ----
class SequenceBar(QWidget):
    """Connect › one box per capture › End. Pending as a preview; lit and counted down live."""

    def __init__(self) -> None:
        super().__init__()
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(8)
        self._boxes: list[tuple[str, QFrame, QLabel, QLabel, str]] = []  # (id, frame, name, sub, hint)
        self._now: int | None = None
        self._compact = False
        self._now_since = 0.0
        self._now_len = 0.0
        self._dur: dict[str, float] = {}

    def load(self, plan: CompiledPlan) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        fs = plan.constants["fs_nom_hz"]
        # Compact boxes when the plan has more than 3 captures (ENG has 5), so the row still fits
        # the 1280 px minimum: tones by frequency only, short hints, no arrows.
        compact = len(plan.captures) > 3
        self._compact = compact
        items = [("CONNECT", "Connect", "device check")]
        for c in plan.captures:
            secs = (c.settle_lines + c.window_lines) / fs
            self._dur[c.id] = secs
            on = "on" if c.tone_hz is not None else "off"
            if compact:
                name = f"{c.tone_hz:g} Hz" if c.tone_hz is not None else (c.title or c.id)
                items.append((c.id, name, f"{secs:.1f} s · {on}"))
            else:
                items.append((c.id, c.title or c.id, f"{secs:.1f} s · actuator {on}"))
        items.append(("END", "End", "actuator off" if not compact else "off"))
        self._boxes = []
        pad = 6 if compact else 10
        for k, (bid, name, hint) in enumerate(items):
            f = QFrame()
            f.setProperty("seq", "pend")
            f.setToolTip(f"{name}: {hint}")
            v = QVBoxLayout(f)
            v.setContentsMargins(pad, 5, pad, 5)
            v.setSpacing(0)
            n = label(name, bold=True, size=9 if compact else None)
            sub = label(hint, "muted", size=8 if compact else 9)
            for w in (n, sub):  # clip rather than widen the window
                w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                w.setMinimumWidth(20)
            v.addWidget(n)
            v.addWidget(sub)
            self._row.addWidget(f, 1)
            if k < len(items) - 1 and not compact:
                self._row.addWidget(label("›", "muted", size=14))
            self._boxes.append((bid, f, n, sub, hint))
        self._row.setSpacing(6 if compact else 8)
        self._now = None

    @property
    def count(self) -> int:
        return len(self._boxes)

    def preview(self) -> None:
        for k in range(len(self._boxes)):
            self._set(k, "pend")
        self._now = None

    def run_started(self) -> None:
        self.preview()
        self._go(0)

    def capture_started(self, capture: str) -> int:
        """Marks the capture's box as current; returns its 1-based position."""
        k = next((i for i, b in enumerate(self._boxes) if b[0] == capture), None)
        if k is None:
            return 0
        if self._now is not None:
            self._set(self._now, "done")
        self._go(k)
        return k + 1

    def finish(self, rec: RunRecord) -> None:
        warnings = rec.metadata.get("context", {}).get("warnings", [])  # type: ignore[union-attr]
        if rec.outcome in (Outcome.PASS, Outcome.FAIL):
            for k in range(len(self._boxes)):
                self._set(k, "done")
        else:
            stop = self._now if self._now is not None else 0
            for k in range(stop):
                self._set(k, "done")
            if rec.outcome is Outcome.ERROR:
                self._set(stop, "err", f"⚠ stopped: {rec.error_code.split('@')[0]}")
            else:
                self._set(stop, "err", "■ aborted")
        end = len(self._boxes) - 1
        if "W101" in warnings:
            self._set(end, "err", "⚠ W101: baseline not confirmed")
        else:
            self._set(end, "done", "✓ off" if self._compact else "✓ actuator off")
        self._now = None

    def tick(self) -> None:
        """Counts the current box down (called by the window timer)."""
        if self._now is None or not self._now_len:
            return
        left = max(0.0, self._now_len - (time.monotonic() - self._now_since))
        self._boxes[self._now][3].setText(f"{left:.1f} s left")

    def _go(self, k: int) -> None:
        self._now, self._now_since = k, time.monotonic()
        self._now_len = self._dur.get(self._boxes[k][0], 0.0)
        self._set(k, "now", "measuring…" if self._now_len else "opening the device…")

    def _set(self, k: int, state: str, sub: str | None = None) -> None:
        _bid, f, _n, s, hint = self._boxes[k]
        restyle(f, seq=state)
        if sub is None:
            sub = {"pend": hint, "done": "✓", "now": "…", "err": "⚠"}[state]
        s.setText(sub)


# ---------------------------------------------------------------- actuator lamp (§4.2, FR-07) ----
class ActuatorLamp(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("actuator")
        self.setFixedWidth(236)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 6, 12, 6)
        self.lamp = QLabel()
        self.lamp.setObjectName("lamp")
        self.lamp.setFixedSize(30, 30)
        h.addWidget(self.lamp)
        v = QVBoxLayout()
        v.setSpacing(0)
        self.state = label("", size=15, bold=True)
        self.detail = label("", "muted", size=9)
        v.addWidget(self.state)
        v.addWidget(self.detail)
        h.addLayout(v, 1)
        self.unknown()

    def unknown(self) -> None:
        restyle(self, on=False)
        restyle(self.lamp, on=False)
        self.state.setText("Actuator —")
        self.detail.setText("no command sent yet")

    def set(self, on: bool, tone_hz: float | None, command: str, reply: str) -> None:
        restyle(self, on=on)
        restyle(self.lamp, on=on)
        if on:
            self.state.setText("Actuator ON")
            ack = f" · ack {reply}" if reply else ""
            self.detail.setText(f"{command} · {tone_hz:g} Hz{ack}")
        else:
            self.state.setText("Actuator OFF")
            self.detail.setText(f"{command or 'baseline'} · safe state")


# ---------------------------------------------------------------- result banner (§4.3) ----
class ResultBanner(QWidget):
    """One line: icon + word, the reason, and the code / action. Holds the banner buttons."""

    def __init__(self) -> None:
        super().__init__()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 0, 14, 0)
        self.frame = QFrame()
        self.frame.setObjectName("banner")
        outer.addWidget(self.frame)
        h = QHBoxLayout(self.frame)
        h.setContentsMargins(16, 8, 16, 8)
        h.setSpacing(18)
        self.big = label("", size=22, bold=True)
        self.why = label("", size=11)
        self.why.setWordWrap(True)
        self.meta = label("", size=9)
        self.meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.big)
        h.addWidget(self.why, 1)
        h.addWidget(self.meta)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        h.addLayout(self.buttons)
        self._kind = ""  # nothing painted yet
        self.ready()

    @property
    def kind(self) -> str:
        return self._kind

    def show_state(self, kind: str, big: str, why: str, meta: str = "") -> None:
        if kind != self._kind:  # restyle only on a change (the frame timer calls this)
            self._kind = kind
            restyle(self.frame, kind=kind)
            fg = INK if kind == "idle" else STATUS["nt" if kind == "abort" else kind][0]
            self.big.setStyleSheet(f"font-size: 22pt; font-weight: 700; color: {fg};")
            self.why.setStyleSheet(f"font-size: 11pt; color: {fg};")
            self.meta.setStyleSheet(f"font-size: 9pt; color: {fg};")
        self.big.setText(big)
        self.why.setText(why)
        self.meta.setText(meta)

    def ready(self) -> None:
        self.show_state("idle", "Ready", "Scan or type the DUT serial, then press Enter or Start")

    def starting(self) -> None:
        self.show_state("idle", "Starting…", "Loading the test plan and limits")

    def not_ready(self, detail: str) -> None:
        self.show_state("err", "⚠ NOT READY", detail, "Call engineering")

    def testing(self, step: int, total: int, title: str, elapsed: float, expected: float) -> None:
        self.show_state(
            "run",
            "TESTING…",
            f"Step {step} of {total} · {title} · do not remove the DUT",
            f"elapsed {elapsed:.1f} s of ≈ {expected:.1f} s",
        )

    def safe_state(self) -> None:
        self.show_state("run", "STOPPING…", "Turning the actuator off (safe state)")

    def result(self, rec: RunRecord) -> None:
        nxt = "Press Next for the next DUT"
        if rec.outcome is Outcome.PASS:
            self.show_state(
                "pass", "✓ PASS", f"all {len(SENSORS)} channels passed", f"Error code: none\n{nxt}"
            )
        elif rec.outcome is Outcome.FAIL:
            # Which channels, not why (the user's call): the reasons are in the table's red cells,
            # the Channel result and chip tooltips, and the enlarged plot.
            failed = [ch for ch in SENSORS if rec.sensor_verdicts.get(ch) is Result.FAIL]
            why = f"{len(failed)} of {len(SENSORS)} channels failed:   " + "   ·   ".join(failed)
            self.show_state("fail", "✗ FAIL", why, f"Error code: none\n{nxt}")
        elif rec.outcome is Outcome.ERROR:
            code = CODES.get(rec.error_code.split("@")[0])
            meaning = code.meaning if code else ""
            action = code.action.value if code else ""
            self.show_state(
                "err", f"⚠ ERROR  {rec.error_code}", f"{meaning}. Not a DUT result.", f"Action: {action}"
            )
        else:
            self.show_state("abort", "■ ABORTED", "The actuator is off. The DUT was not judged.", nxt)


# ---------------------------------------------------------------- serial terminal (§4.6) ----
_TERM_COLOUR = {"tx": "#7fc4ff", "rx": "#d7b8ff", "data": "#9aa4af", "note": "#6f7883"}
_ARROW = {"tx": "→", "rx": "←", "data": " ", "note": " "}


class SerialTerminal(QWidget):
    """Read-only: → commands and ← replies always, data lines thinned at the driver, Pause.

    There is no input field: the operator can never send a command (device safety)."""

    def __init__(self) -> None:
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 5, 8, 5)
        self.title = label("Serial terminal", "cardtitle")
        head.addWidget(self.title)
        head.addSpacing(8)
        head.addWidget(
            label(
                "read-only  ·  → commands and ← replies always  ·  data lines thinned to ~5/s",
                "muted",
                size=8,
            )
        )
        head.addStretch(1)
        self.pause = QToolButton(text="Pause")
        self.pause.setCheckable(True)
        self.pause.toggled.connect(lambda on: self.pause.setText("Resume" if on else "Pause"))
        self.collapse = QToolButton(text="▾ Collapse")
        self.collapse.setCheckable(True)
        self.collapse.toggled.connect(self._collapse)
        head.addWidget(self.pause)
        head.addWidget(self.collapse)
        v.addLayout(head)
        self.view = QPlainTextEdit()
        self.view.setObjectName("term")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(TERMINAL_LINES)
        self.view.setFixedHeight(92)  # ~4 lines (spec §4.6)
        v.addWidget(self.view)
        self._pending: deque[str] = deque(maxlen=TERMINAL_LINES)

    def add(self, e: TrafficSeen) -> None:
        self._line(e.direction, e.text, e.t)

    def note(self, text: str) -> None:
        self._line("note", text, time.time())

    def _line(self, kind: str, text: str, t: float) -> None:
        ts = datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-3]
        colour = _TERM_COLOUR.get(kind, _TERM_COLOUR["data"])
        weight = "font-weight:600;" if kind in ("tx", "rx") else ""
        self._pending.append(
            f"<span style='color:{colour};{weight}'>{ts}  {_ARROW.get(kind, ' ')} {html.escape(text)}</span>"
        )

    def flush(self) -> None:
        """Appends what arrived since the last call (the window timer), unless paused."""
        if self.pause.isChecked() or not self._pending:
            return
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        while self._pending:
            self.view.appendHtml(self._pending.popleft())
        if at_end:
            bar.setValue(bar.maximum())

    def set_height(self, px: int) -> None:
        self.view.setFixedHeight(px)

    def _collapse(self, on: bool) -> None:
        self.view.setVisible(not on)
        self.collapse.setText("▴ Expand" if on else "▾ Collapse")
