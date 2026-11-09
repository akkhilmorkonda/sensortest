"""PyQt6 mockup of operator mode, layout A (D-053): the real widgets, filled from a real PROD record.

Not product code: nothing here is wired to the engine. It renders the screen the GUI spec describes
(`docs/gui_spec.md`) with the widgets the build will use (QTableView + model, pyqtgraph strips,
styled QFrames), so the layout can be reviewed and changed before sections 07-09 are built.

Data: a PROD run on the Teensy from the L-v1 gauge run (its record and raw windows).

Usage (project .venv):
  python docs/archive/gui_mockups/operator_qt_mockup.py            # writes qt_operator_<state>.png next to this file
  python docs/archive/gui_mockups/operator_qt_mockup.py --show fail   # opens the window instead (run | fail | err | zoom)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RECORD = REPO / "benchtop_testing/05_gauge_run_l_v1/records/2026-11-08/TEENSY-20071290_R20261108-002324-46be.csv"
CH = [f"ch{i}" for i in range(1, 9)]
# Operator names (the spec's proposed `title` per step in the sensor test file)
TITLE = {1000: "Connection", 2040: "Baseline offset", 2050: "Baseline noise", 6040: "Response amplitude",
         6050: "Response frequency", 6060: "Response phase", 1900: "Run integrity"}
DECIMALS = {2040: 1, 2050: 1, 6040: 1, 6050: 3, 6060: 2}
UNIT = {"deg": "°"}
# status -> (text colour, background); reserved for status only (spec §6)
COL = {"pass": ("#1b6e34", "#e3f3e7"), "fail": ("#b42318", "#fde8e6"), "nt": ("#5f6670", "#eceef1"),
       "pend": ("#8a929c", "#ffffff"), "err": ("#8a5300", "#fff2d6"), "run": ("#1d5fbf", "#e6effc")}
SYMBOL = {"pass": "✓", "fail": "✗", "nt": "–", "pend": "", "err": "⚠"}
WORD = {"pass": "PASS", "fail": "FAIL", "nt": "N/T", "pend": "…", "err": "ERROR"}

QSS = """
QWidget { font-family: 'Segoe UI'; font-size: 10pt; color: #1d2127; }
QMainWindow, #central { background: #ffffff; }
#titlebar { background: #f4f6f8; border-bottom: 1px solid #d9dde3; }
#app { font-weight: 600; font-size: 11pt; }
#muted { color: #555d68; }
QPushButton { padding: 5px 14px; border: 1px solid #c9ced6; border-radius: 5px; background: #ffffff; font-weight: 600; }
QPushButton:disabled { color: #a3a9b1; }
QPushButton#primary { background: #1d5fbf; color: white; border-color: #1d5fbf; }
QPushButton[seg="true"] { border-radius: 0; padding: 3px 14px; font-weight: 400; }
QPushButton[seg="true"]:checked { background: #1d2127; color: white; border-color: #1d2127; }
QLineEdit { font-family: Consolas; font-size: 11pt; padding: 4px 6px; border: 1px solid #c9ced6; border-radius: 5px; background: #fafbfc; }
QFrame[seq="pend"] { border: 1px solid #d9dde3; border-radius: 6px; background: #ffffff; }
QFrame[seq="done"] { border: 1px solid #b9dcc4; border-radius: 6px; background: #e3f3e7; }
QFrame[seq="now"]  { border: 2px solid #1d5fbf; border-radius: 6px; background: #e6effc; }
QFrame[seq="err"]  { border: 2px solid #c27c00; border-radius: 6px; background: #fff2d6; }
QFrame#actuator[on="true"]  { border: 2px solid #e0a100; border-radius: 8px; background: #fff7dc; }
QFrame#actuator[on="false"] { border: 2px solid #d9dde3; border-radius: 8px; background: #ffffff; }
QLabel#lamp[on="true"]  { background: #f2b600; border: 3px solid #ffe08a; border-radius: 15px; }
QLabel#lamp[on="false"] { background: #c9ced6; border: 3px solid #e7eaee; border-radius: 15px; }
QFrame#banner[kind="fail"] { background: #fde8e6; border: 2px solid #b42318; border-radius: 8px; }
QFrame#banner[kind="pass"] { background: #e3f3e7; border: 2px solid #1b6e34; border-radius: 8px; }
QFrame#banner[kind="run"]  { background: #e6effc; border: 2px solid #1d5fbf; border-radius: 8px; }
QFrame#banner[kind="err"]  { background: #fff2d6; border: 2px solid #c27c00; border-radius: 8px; }
QFrame#card { border: 1px solid #d9dde3; border-radius: 8px; background: #ffffff; }
QLabel#cardtitle { font-weight: 600; }
QTableView { border: none; gridline-color: #eef0f3; selection-background-color: transparent; }
QHeaderView::section { background: #f4f6f8; border: none; border-bottom: 1px solid #d9dde3; padding: 4px 6px; color: #555d68; font-weight: 600; }
QPlainTextEdit#term { background: #1f242b; color: #cfd6df; font-family: Consolas; font-size: 9pt; border: none; }
QToolButton { border: 1px solid #c9ced6; border-radius: 4px; padding: 2px 8px; background: #ffffff; }
"""


# ---------------------------------------------------------------- data ----
def load(state: str) -> dict:
    rows = list(csv.DictReader(RECORD.open(newline="", encoding="utf-8")))
    raw = list(csv.DictReader(RECORD.with_name(RECORD.stem + "_raw.csv").open(newline="", encoding="utf-8")))
    win = {c: np.array([[float(r[k]) for k in CH] for r in raw if r["capture"] == c]) for c in ("BL", "T10")}
    meta = json.loads(next(r["metadata"] for r in rows if r["metadata"]))
    steps: dict[int, dict] = {}
    for r in rows:
        sid = int(r["step_id"])
        s = steps.setdefault(sid, {"id": sid, "capture": r["capture"], "unit": UNIT.get(r["unit"], r["unit"]),
                                   "low": r["low"], "high": r["high"], "cells": {}, "run": r["sensor"] == "RUN"})
        if r["sensor"] == "RUN":
            s["value"] = r["value"]
        else:
            s["cells"][r["sensor"]] = r
    return {"steps": [steps[k] for k in steps], "win": win, "meta": meta, "rows": rows, "state": state}


def message(r: dict) -> str:
    """The operator message for a primary failure (core.operator.failure_message, TP-001 §8)."""
    low = r["value"] and r["low"] and float(r["value"]) < float(r["low"])
    return {"noise": "No signal" if low else "Noisy", "gain": "Weak response" if low else "Response too strong",
            "frequency": "Wrong frequency", "offset": "Offset out of range"}.get(r["fault_family"], "Out of limits")


def fmt(v: float, d: int) -> str:
    return f"{v:.0f}" if abs(v) >= 1000 else f"{v:.{d}f}"


# ---------------------------------------------------------------- the step table ----
class StepModel(QAbstractTableModel):
    """Layout A: rows = steps in run order (+ Channel result), columns = Test, Low, High, ch1-8, Result, Code."""

    HEAD = ["Test", "Unit", "Low", "High", *CH, "Result", "Code"]

    def __init__(self, data: dict) -> None:
        super().__init__()
        st = data["state"]
        self.rows: list[dict] = []
        self.spans: list[int] = []
        for s in data["steps"]:
            pending = st == "run" and s["capture"] != "BL"
            err_skip = st == "err" and s["capture"] == "T10"
            title = TITLE.get(s["id"], s["id"])
            if s["run"]:
                if s["id"] == 1000:
                    val, res, code = f"{float(s['value']):.2f} Hz   line rate (a device check, not per channel)", "pass", "—"
                elif st == "run":
                    val, res, code = "", "pend", ""
                elif st == "err":
                    val, res, code = "stimulus jump +120° near 1.0 s into the T10 window, on 6 of 6 channels", "fail", "E211@T10"
                else:
                    val, res, code = "all checks passed (replies, line rate, stimulus continuity)", "pass", "—"
                self.spans.append(len(self.rows))
                self.rows.append({"cells": [(title, None, ""), ("Hz" if s["id"] == 1000 else "", None, ""),
                                            (s["low"], None, ""), (s["high"], None, ""),
                                            (val, "span", ""), *[("", None, "")] * 7, self._chip(res), (code, None, "")]})
                continue
            cells, worst = [], "pass"
            for ch in CH:
                r = s["cells"][ch]
                if pending:
                    cells.append(("", "pend", ""))
                    worst = "pend"
                elif err_skip:
                    cells.append(("–", "nt", "not tested: E211@T10 (the run stopped)"))
                    worst = "nt"
                elif r["result"] == "NOT_TESTED" or r["value"] == "":
                    cells.append(("–", "nt", f"not tested: {r['not_tested_reason']}"))
                else:
                    stt = "pass" if r["result"] == "PASS" else "fail"
                    worst = "fail" if stt == "fail" else worst
                    cells.append((f"{SYMBOL[stt]} {fmt(float(r['value']), DECIMALS[s['id']])}", stt, ""))
            self.rows.append({"cells": [(title, "title", ""), (s["unit"], None, ""), (s["low"], None, ""),
                                        (s["high"], None, ""), *cells, self._chip(worst), ("" if pending else "—", None, "")],
                              "running": st == "run" and s["capture"] == "T10"})
        verdict = []
        failed = {r["sensor"] for r in data["rows"] if r["result"] == "FAIL" and r["sensor"] != "RUN"}
        for ch in CH:
            stt = "pend" if st == "run" else "nt" if st == "err" else ("fail" if ch in failed else "pass")
            verdict.append(self._chip(stt))
        self.rows.append({"cells": [("Channel result", "title", ""), ("", None, ""), ("", None, ""), ("", None, ""), *verdict,
                                    ("", None, ""), ("", None, "")], "verdict": True})

    @staticmethod
    def _chip(stt: str) -> tuple:
        return (f"{SYMBOL[stt]} {WORD[stt]}".strip(), stt, "")

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 (Qt API)
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(self.HEAD)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEAD[section]
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignLeft if section == 0 else Qt.AlignmentFlag.AlignCenter
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        row = self.rows[index.row()]
        text, kind, tip = row["cells"][index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return text
        if role == Qt.ItemDataRole.ToolTipRole:
            return tip or None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            left = index.column() == 0 or kind == "span"
            return (Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignCenter) | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.BackgroundRole:
            if kind in COL and kind != "pend":
                return QColor(COL[kind][1])
            if row.get("running"):
                return QColor(COL["run"][1])
            if row.get("verdict"):
                return QColor("#f4f6f8")
        if role == Qt.ItemDataRole.ForegroundRole:
            if kind in COL:
                return QColor(COL[kind][0])
            if kind == "span":
                return QColor("#555d68")
        if role == Qt.ItemDataRole.FontRole and (kind in ("title", "fail") or (kind in COL and index.column() >= 12)):
            f = QFont()
            f.setBold(True)
            return f
        return None


# ---------------------------------------------------------------- the window ----
def label(text: str, name: str = "", size: float | None = None, bold: bool = False, color: str = "") -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    style = ""
    if size:
        style += f"font-size: {size}pt;"
    if bold:
        style += "font-weight: 700;"
    if color:
        style += f"color: {color};"
    if style:
        w.setStyleSheet(style)
    return w


def frame(name: str = "", **props) -> QFrame:
    f = QFrame()
    if name:
        f.setObjectName(name)
    for k, v in props.items():
        f.setProperty(k, v)
    return f


class Mockup(QMainWindow):
    def __init__(self, data: dict, zoom: str | None = None) -> None:
        super().__init__()
        self.data, st = data, data["state"]
        self.setWindowTitle("Sensor line test (mockup)")
        self.resize(1280, 800)
        central = QWidget(objectName="central")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 10)
        root.setSpacing(10)
        root.addWidget(self._titlebar())
        root.addLayout(self._controls(st))
        root.addWidget(self._banner(st))
        body_w = QWidget()
        body = QHBoxLayout(body_w)
        body.setContentsMargins(14, 0, 14, 0)
        body.setSpacing(12)
        body.addWidget(self._strips(st, zoom), 0)
        body.addWidget(self._table(), 1)
        pages = QStackedWidget()  # page 0: strips + table; page 1: one channel enlarged
        pages.addWidget(body_w)
        if zoom:
            pages.addWidget(self._zoom(zoom))
            pages.setCurrentIndex(1)
        root.addWidget(pages, 1)
        root.addWidget(self._terminal(st))
        self.setCentralWidget(central)

    def _titlebar(self) -> QWidget:
        bar = frame("titlebar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 7, 14, 7)
        row.addWidget(label("Sensor line test", "app"))
        group = QButtonGroup(bar)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for k, name in enumerate(("Operator", "Test")):
            b = QPushButton(name)
            b.setProperty("seg", True)
            b.setCheckable(True)
            b.setChecked(k == 0)
            group.addButton(b)
            seg.addWidget(b)
        row.addSpacing(10)
        row.addLayout(seg)
        row.addSpacing(16)
        row.addWidget(label("Plan TP-001 PROD v0  ·  Limits L-v1  ·  Station BENCH-01", "muted"))
        row.addStretch(1)
        for text in ("Fixture: teensy (COM4)", "Actuator connected", "Sensor connected"):
            row.addWidget(label(f"<span style='color:#1f9d4a'>●</span> {text}"))
            row.addSpacing(8)
        return bar

    def _controls(self, st: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(12)
        ser = QVBoxLayout()
        ser.setSpacing(3)
        ser.addWidget(label("DUT serial (scan or type, Enter starts)", "muted", size=9))
        line = QHBoxLayout()
        edit = QLineEdit("TEENSY-20071290")
        edit.setReadOnly(st != "idle")
        edit.setFixedWidth(190)
        line.addWidget(edit)
        if st == "run":
            line.addWidget(QPushButton("Abort"))
        else:
            start = QPushButton("Start")
            start.setEnabled(False)
            line.addWidget(start)
            nxt = QPushButton("Next")
            nxt.setObjectName("primary")
            line.addWidget(nxt)
        ser.addLayout(line)
        row.addLayout(ser)

        boxes = [("Connect", "device check"), ("Baseline", "2.2 s · actuator off"),
                 ("Tone 10 Hz", "2.2 s · actuator on"), ("End", "actuator off")]
        states = {"run": ["done", "done", "now", "pend"], "err": ["done", "done", "err", "done"]}.get(st, ["done"] * 4)
        subs = {"run": ["✓", "✓", "measuring…  0.7 s left", "next"],
                "err": ["✓", "✓", "⚠ stopped: E211", "✓ actuator off"]}.get(st, ["✓"] * 4)
        for k, ((name, hint), stt) in enumerate(zip(boxes, states, strict=True)):
            f = frame(seq=stt)
            v = QVBoxLayout(f)
            v.setContentsMargins(10, 5, 10, 5)
            v.setSpacing(0)
            v.addWidget(label(name, bold=True))
            v.addWidget(label(subs[k] if stt != "pend" else hint, "muted", size=9))
            row.addWidget(f, 1)
            if k < 3:
                row.addWidget(label("›", "muted", size=14))

        on = st == "run"
        act = frame("actuator", on=on)
        act.setFixedWidth(250)
        h = QHBoxLayout(act)
        h.setContentsMargins(12, 6, 12, 6)
        lamp = QLabel(objectName="lamp")
        lamp.setProperty("on", on)
        lamp.setFixedSize(30, 30)
        h.addWidget(lamp)
        v = QVBoxLayout()
        v.setSpacing(0)
        v.addWidget(label("Actuator ON" if on else "Actuator OFF", size=15, bold=True))
        v.addWidget(label("response:10 · 10 Hz · ack 10.00" if on else "baseline · safe state", "muted", size=9))
        h.addLayout(v, 1)
        row.addWidget(act)
        return row

    def _banner(self, st: str) -> QWidget:
        rows = self.data["rows"]
        prim = sorted((r for r in rows if r["primary_failure"] == "True"), key=lambda r: CH.index(r["sensor"]))
        b = frame("banner", kind={"run": "run", "err": "err"}.get(st, "fail"))
        outer = QHBoxLayout()
        outer.setContentsMargins(14, 0, 14, 0)
        h = QHBoxLayout(b)
        h.setContentsMargins(16, 8, 16, 8)
        h.setSpacing(18)
        if st == "run":
            fg, big, why, meta = COL["run"][0], "TESTING…", "Step 3 of 4 · Tone 10 Hz · do not remove the DUT", "elapsed 3.9 s of ≈ 4.6 s"
        elif st == "err":
            fg, big = COL["err"][0], "⚠ ERROR  E211@T10"
            why, meta = "Stimulus discontinuity: the tone jumped mid-window. Not a DUT result.", ""
        else:
            fg, big = COL["fail"][0], "✗ FAIL"
            why = f"{len(prim)} of 8 channels failed:   " + "   ·   ".join(f"{r['sensor']} {message(r)}" for r in prim)
            meta = "Error code: none\nPress Next for the next DUT"
        h.addWidget(label(big, size=22, bold=True, color=fg))
        h.addWidget(label(why, size=11, color=fg), 1)
        if st == "err":
            h.addWidget(label("Retry once", size=11, bold=True, color=fg))
            retry = QPushButton("Retry")
            retry.setObjectName("primary")
            h.addWidget(retry)
            h.addWidget(QPushButton("Next"))
        else:
            m = label(meta, size=9, color=fg)
            m.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(m)
        wrap = QWidget()
        outer.addWidget(b)
        wrap.setLayout(outer)
        return wrap

    def _signal(self, st: str) -> np.ndarray:
        bl, t10 = self.data["win"]["BL"], self.data["win"]["T10"]
        if st == "run":
            return np.vstack([bl[-195:], t10[:105]])  # the last 2 s: end of baseline, 0.7 s of tone
        if st == "err":  # the jump the E211 check caught: the tone skips 5 lines (+120°) at line 150
            return np.vstack([t10[:150], t10[155:], t10[-5:]])
        return t10

    def _strips(self, st: str, zoom: str | None) -> QWidget:
        card = frame("card")
        card.setFixedWidth(270)
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 7, 12, 7)
        head.addWidget(label("Live signal", "cardtitle"))
        head.addSpacing(8)
        head.addWidget(label("last 2 s · click to enlarge", "muted", size=8))
        head.addStretch(1)
        v.addLayout(head)
        x = self._signal(st)
        t = np.arange(len(x)) / 150.0
        failed = {r["sensor"] for r in self.data["rows"] if r["result"] == "FAIL" and r["sensor"] != "RUN"}
        stack = QStackedWidget()  # the build adds the click-to-enlarge page here (see _zoom)
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(10, 2, 10, 6)
        grid.setVerticalSpacing(2)
        for i, ch in enumerate(CH):
            grid.addWidget(label(ch, bold=True), i, 0)
            p = pg.PlotWidget()
            p.setFixedHeight(46)
            p.hideAxis("left")
            p.hideAxis("bottom")
            p.setMouseEnabled(False, False)
            p.hideButtons()
            p.setMenuEnabled(False)
            p.plot(t, x[:, i], pen=pg.mkPen("#3d5a80", width=1.2))
            grid.addWidget(p, i, 1)
            stt = "pend" if st == "run" else "nt" if st == "err" else ("fail" if ch in failed else "pass")
            chip = label(f" {SYMBOL[stt]} {WORD[stt]} ".replace("  ", " "), size=8.5, bold=True, color=COL[stt][0])
            chip.setStyleSheet(chip.styleSheet() + f"background:{COL[stt][1]}; border-radius:4px; padding:1px 4px;")
            chip.setFixedSize(62, 22)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(chip, i, 2, Qt.AlignmentFlag.AlignVCenter)
        stack.addWidget(grid_w)
        v.addWidget(stack, 1)
        return card

    def _zoom(self, ch: str) -> QWidget:
        """Click-to-enlarge: one channel over the whole body, both windows of the run, with axes."""
        i = CH.index(ch)
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(14, 0, 14, 0)
        card = frame("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 10)
        top = QHBoxLayout()
        top.addWidget(label(f"{ch}: the whole run", "cardtitle"))
        top.addSpacing(10)
        top.addWidget(label("✗ FAIL · Noisy: baseline noise 182.2 a.u. (limits 5.6 … 33.9)", size=10, bold=True,
                            color=COL["fail"][0]))
        top.addStretch(1)
        top.addWidget(QToolButton(text="✕  Back to all channels"))
        v.addLayout(top)
        bl, t10 = self.data["win"]["BL"][:, i], self.data["win"]["T10"][:, i]
        x = np.concatenate([bl, t10])
        t = np.arange(len(x)) / 150.0
        p = pg.PlotWidget()
        p.getAxis("left").enableAutoSIPrefix(False)
        p.setLabel("left", "value (a.u.)")
        p.setLabel("bottom", "time in the run's windows (s)")
        p.showGrid(x=True, y=True, alpha=0.2)
        region = pg.LinearRegionItem((2.0, 4.0), movable=False, brush=pg.mkBrush(255, 214, 102, 45), pen=pg.mkPen(None))
        p.addItem(region)
        p.plot(t, x, pen=pg.mkPen("#3d5a80", width=1.3))
        for x0, text in ((0.05, "Baseline (actuator off)"), (2.05, "Tone 10 Hz (actuator on)")):
            ti = pg.TextItem(text, color="#555d68", anchor=(0, 0))
            ti.setPos(x0, float(x.max()))
            p.addItem(ti)
        v.addWidget(p, 1)
        outer.addWidget(card)
        return wrap

    def _table(self) -> QWidget:
        card = frame("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 7, 12, 7)
        head.addWidget(label("Test steps", "cardtitle"))
        head.addSpacing(8)
        head.addWidget(label("Low / High apply to every channel  ·  – = not tested (hover for why)", "muted", size=8))
        head.addStretch(1)
        v.addLayout(head)
        model = StepModel(self.data)
        view = QTableView()
        view.setModel(model)
        view.verticalHeader().hide()
        view.setShowGrid(False)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.setWordWrap(False)
        hh = view.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        widths = [0, 40, 56, 56, *[64] * 8, 76, 72]
        for c, w in enumerate(widths):
            if w:
                view.setColumnWidth(c, w)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        view.verticalHeader().setDefaultSectionSize(34)
        for r in model.spans:
            view.setSpan(r, 4, 1, 8)
        v.addWidget(view, 1)
        return card

    def _terminal(self, st: str) -> QWidget:
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(14, 0, 14, 0)
        card = frame("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 5, 8, 5)
        head.addWidget(label("Serial terminal", "cardtitle"))
        head.addSpacing(8)
        head.addWidget(label("read-only  ·  → commands and ← replies always  ·  data lines thinned to ~5/s", "muted", size=8))
        head.addStretch(1)
        head.addWidget(QToolButton(text="Pause"))
        head.addWidget(QToolButton(text="▾ Collapse"))
        v.addLayout(head)
        term = QPlainTextEdit(objectName="term")
        term.setReadOnly(True)
        term.setFixedHeight(92)
        bl, t10 = self.data["win"]["BL"], self.data["win"]["T10"]

        def data_line(ts: str, row: np.ndarray) -> str:
            return f"{ts}   " + ",".join(f"{x:.2f}" for x in row)

        lines = [("tx", "00:23:24.812  → baseline"), ("", data_line("00:23:25.214", bl[60])),
                 ("", data_line("00:23:26.415", bl[240])), ("tx", "00:23:27.031  → response:10"),
                 ("rx", "00:23:27.038  ← 10.00"), ("", data_line("00:23:27.443", t10[30]))]
        if st != "run":
            lines += [("", data_line("00:23:28.643", t10[210])), ("tx", "00:23:29.252  → baseline")]
        colour = {"tx": "#7fc4ff", "rx": "#9be29b", "": "#cfd6df"}
        for kind, text in lines:
            term.appendHtml(f"<span style='color:{colour[kind]}'>{text}</span>")
        v.addWidget(term)
        outer.addWidget(card)
        return wrap


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--show", choices=["run", "fail", "err", "zoom"], help="open the window instead of saving PNGs")
    a = p.parse_args()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    pg.setConfigOptions(antialias=True, background="w", foreground="#555d68")
    if a.show:
        w = Mockup(load("fail" if a.show == "zoom" else a.show), zoom="ch4" if a.show == "zoom" else None)
        w.show()
        sys.exit(app.exec())
    for state in ("run", "fail", "err", "zoom"):
        w = Mockup(load("fail" if state == "zoom" else state), zoom="ch4" if state == "zoom" else None)
        w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        w.show()
        app.processEvents()
        out = HERE / f"qt_operator_{state}.png"
        w.grab().save(str(out))
        w.close()
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
