"""The live signal (GUI spec §4.5, D-053): 8 strips, each on its own scale, and click to enlarge.

Both read the RunBuffer; nothing here touches the event stream. The window's timer calls
refresh() at ``ui.plot_fps``, and a plot only redraws when the buffer has changed (D-043).
"""

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QToolButton, QVBoxLayout

from sensortest.core.models import SENSORS
from sensortest.ui.run_buffer import RunBuffer
from sensortest.ui.theme import BASE_SHADE, MUTED, SIGNAL_PEN, STATUS, STRIP_SECONDS, TONE_SHADE, chip


def label(
    text: str = "", name: str = "", size: float | None = None, bold: bool = False, color: str = ""
) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    style = (f"font-size: {size}pt;" if size else "") + ("font-weight: 700;" if bold else "")
    style += f"color: {color};" if color else ""
    if style:
        w.setStyleSheet(style)
    return w


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


class LiveStrips(QFrame):
    """8 strips (last 2 s), each with its channel name and result chip. Emits enlarge(channel)."""

    enlarge = pyqtSignal(str)

    def __init__(self, buffer: RunBuffer) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setFixedWidth(268)
        self._buf = buffer
        self._drawn = -1
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QVBoxLayout()  # title, then the subtitle on its own line (the card is narrow)
        head.setContentsMargins(12, 7, 12, 4)
        head.setSpacing(0)
        head.addWidget(label("Live signal", "cardtitle"))
        self.subtitle = label("click a strip to enlarge it", "muted", size=8)
        head.addWidget(self.subtitle)
        v.addLayout(head)
        grid = QGridLayout()
        grid.setContentsMargins(10, 2, 10, 8)
        grid.setVerticalSpacing(2)
        self.plots: dict[str, pg.PlotWidget] = {}
        self._curves: dict[str, pg.PlotDataItem] = {}
        self.chips: dict[str, QLabel] = {}
        for i, ch in enumerate(SENSORS):
            grid.addWidget(label(ch, bold=True), i, 0)
            p = pg.PlotWidget()
            p.setMinimumHeight(30)
            p.hideAxis("left")
            p.hideAxis("bottom")
            p.setMouseEnabled(False, False)
            p.hideButtons()
            p.setMenuEnabled(False)
            p.setCursor(Qt.CursorShape.PointingHandCursor)
            p.setToolTip(f"{ch}: click to enlarge")
            p.setXRange(0, STRIP_SECONDS, padding=0)
            self._curves[ch] = p.plot([], [], pen=pg.mkPen(SIGNAL_PEN, width=1.2))
            p.scene().sigMouseClicked.connect(lambda _ev, ch=ch: self.enlarge.emit(ch))
            grid.addWidget(p, i, 1)
            grid.setRowStretch(i, 1)
            c = label()
            c.setFixedSize(64, 22)
            c.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(c, i, 2, Qt.AlignmentFlag.AlignVCenter)
            self.plots[ch], self.chips[ch] = p, c
            self.set_chip(ch, "pend")
        v.addLayout(grid, 1)

    def set_chip(self, ch: str, kind: str, tooltip: str = "") -> None:
        fg, bg = STATUS[kind]
        c = self.chips[ch]
        c.setText(chip(kind))
        c.setToolTip(tooltip)
        c.setStyleSheet(
            f"font-size: 8.5pt; font-weight: 700; color: {fg}; background: {bg}; border-radius: 4px;"
            + (" border: 1px solid #d9dde3;" if kind == "pend" else "")
        )

    def refresh(self) -> None:
        if self._buf.version == self._drawn:
            return
        self._drawn = self._buf.version
        t, x = self._buf.last(STRIP_SECONDS)
        end = max(STRIP_SECONDS, float(t[-1]) if len(t) else 0.0)
        for i, ch in enumerate(SENSORS):
            self._curves[ch].setData(t, x[:, i] if len(x) else np.empty(0))
            self.plots[ch].setXRange(end - STRIP_SECONDS, end, padding=0)


class ZoomView(QFrame):
    """One channel over the whole body: the run so far, with each capture's measured window shaded
    and labelled. In test mode this is also the raw-window viewer (spec §7): every capture is here."""

    back = pyqtSignal()

    def __init__(self, buffer: RunBuffer) -> None:
        super().__init__()
        self.setObjectName("card")
        self._buf = buffer
        self._drawn = -1
        self.channel = SENSORS[0]
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 10)
        top = QHBoxLayout()
        self.title = label("", "cardtitle")
        top.addWidget(self.title)
        top.addSpacing(10)
        self.result = label("", size=10, bold=True)
        top.addWidget(self.result, 1)
        btn = QToolButton(text="✕  Back to all channels")
        btn.clicked.connect(self.back.emit)
        top.addWidget(btn)
        v.addLayout(top)
        self.plot = pg.PlotWidget()
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.setLabel("left", "value (a.u.)")
        self.plot.setLabel("bottom", "time in the run (s, line count ÷ 150 Hz; settle lines unshaded)")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self._curve = self.plot.plot([], [], pen=pg.mkPen(SIGNAL_PEN, width=1.3))
        self._marks: list[pg.GraphicsObject] = []
        v.addWidget(self.plot, 1)

    def show_channel(self, ch: str, result_text: str, kind: str) -> None:
        self.channel = ch
        self.title.setText(f"{ch}: the whole run")
        self.set_result(result_text, kind)
        self._drawn = -1
        self.refresh()

    def set_result(self, text: str, kind: str) -> None:
        self.result.setText(text)
        self.result.setStyleSheet(f"font-size: 10pt; font-weight: 700; color: {STATUS[kind][0]};")

    def refresh(self) -> None:
        if not self.isVisible() or self._buf.version == self._drawn:
            return
        self._drawn = self._buf.version
        i = SENSORS.index(self.channel)
        t, y = self._buf.times(), self._buf.data()[:, i]
        self._curve.setData(t, y)
        self.plot.getAxis("bottom").setLabel(
            f"time in the run (s, line count ÷ {self._buf.fs:g} Hz; settle lines unshaded)"
        )
        for m in self._marks:
            self.plot.removeItem(m)
        self._marks = []
        top = float(np.max(y)) if len(y) else 0.0
        for s in self._buf.spans():
            shade = TONE_SHADE if s.tone else BASE_SHADE
            region = pg.LinearRegionItem(
                (s.t0, s.t1), movable=False, brush=pg.mkBrush(*shade), pen=pg.mkPen(None)
            )
            region.setZValue(-10)
            text = pg.TextItem(
                f"{s.title} (actuator {'on' if s.tone else 'off'})", color=MUTED, anchor=(0, 0)
            )
            text.setPos(s.t0, top)
            for item in (region, text):
                self.plot.addItem(item)
                self._marks.append(item)
