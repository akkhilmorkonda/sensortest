"""The test steps table, layout A (GUI spec §4.4, D-053): the operator's main view.

Rows are the plan's steps in run order (1000 Connection, the limit steps by capture, 1900 Run
integrity), then a Channel result row. When step titles repeat (ENG runs the same three tone steps
at 5, 10 and 15 Hz) the limit steps are grouped under their capture's title. Columns are ID (test
mode only), Test, Unit, Low, High, ch1-ch8, Result, Code. Limits are common to all channels, so
each row has one Low/High.

The model is filled live from StepEvaluated and then, authoritatively, from the RunRecord.
"""

from dataclasses import dataclass, field

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from sensortest.core.models import (
    RUN,
    SENSORS,
    CompiledPlan,
    Outcome,
    Result,
    RunRecord,
    StepDef,
    StepResult,
    StepType,
)
from sensortest.core.operator import failure_message
from sensortest.ui.theme import MUTED, PANEL, STATUS, SYMBOL, chip, fmt, unit_label

HEAD = ["ID", "Test", "Unit", "Low", "High", *SENSORS, "Result", "Code"]
COL_ID, COL_TEST, COL_UNIT, COL_LOW, COL_HIGH = range(5)
COL_CH0 = 5
COL_RESULT = COL_CH0 + len(SENSORS)
COL_CODE = COL_RESULT + 1
WIDTHS = {COL_ID: 40, COL_UNIT: 38, COL_LOW: 52, COL_HIGH: 52, COL_RESULT: 72, COL_CODE: 72}
CH_WIDTH = 64  # fits "✗ 19.998" in bold

Cell = tuple[str, str, str]  # (text, kind, tooltip); kind: a STATUS key, "title", "span", ""
_EMPTY: Cell = ("", "", "")
_RUN_PASS = {
    1000: "{v} Hz line rate (a device check, not per channel)",
    1900: "all checks passed (replies, line rate)",
}


@dataclass
class _Row:
    kind: str  # "heading" | "run" | "limit" | "verdict"
    title: str
    step: StepDef | None = None
    capture: str = ""
    low: str = ""
    high: str = ""
    cells: dict[str, Cell] = field(default_factory=dict)  # channel -> cell (limit and verdict rows)
    span: Cell = _EMPTY  # run rows: one value across the channel columns
    result: Cell = _EMPTY
    code: str = ""


class StepTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[_Row] = []
        self._by_step: dict[int, int] = {}  # step id -> row
        self._running: str | None = None
        self.plan: CompiledPlan | None = None

    # ---------------------------------------------------------------- filling ----
    def load_plan(self, plan: CompiledPlan) -> None:
        """The plan's rows with empty cells: the preview before Start, and the start of every run."""
        self.beginResetModel()
        self.plan = plan
        self._running = None
        self._rows, self._by_step = [], {}
        limit_steps = [s for s in plan.steps if s.type is StepType.LIMIT]
        grouped = len({s.title or s.name for s in limit_steps}) < len(limit_steps)
        titles = {c.id: c.title or c.id for c in plan.captures}
        last_capture = None
        for s in plan.steps:
            if s.type is StepType.VALIDITY:
                row = _Row("run", s.title or s.name, s, s.capture)
                if s.id == 1000:  # the line-rate window 1000 CONNECT checks (D-008)
                    fs, tol = plan.constants["fs_nom_hz"], plan.constants["line_rate_tolerance"]
                    row.low, row.high = f"{fs * (1 - tol):.1f}", f"{fs * (1 + tol):.1f}"
                self._add(row)
                continue
            if grouped and s.capture != last_capture:
                self._rows.append(_Row("heading", titles.get(s.capture, s.capture), capture=s.capture))
                last_capture = s.capture
            lim = plan.limits[s.id]
            row = _Row("limit", s.title or s.name, s, s.capture, fmt(lim.low, s.unit), fmt(lim.high, s.unit))
            row.cells = {ch: ("", "pend", "") for ch in SENSORS}
            self._add(row)
        self._rows.append(
            _Row("verdict", "Channel result", cells={ch: (chip("pend"), "pend", "") for ch in SENSORS})
        )
        self.endResetModel()

    def _add(self, row: _Row) -> None:
        assert row.step is not None
        self._by_step[row.step.id] = len(self._rows)
        self._rows.append(row)

    def set_running(self, capture: str | None) -> None:
        """Highlight the rows of the capture being measured."""
        self._running = capture
        self._changed_all()

    def apply(self, r: StepResult) -> None:
        """One judged result (StepEvaluated): fills its cell and the row's Result."""
        k = self._by_step.get(r.step_id)
        if k is None:
            return
        row = self._rows[k]
        if r.sensor == RUN:
            self._apply_run(row, r, error_detail="")
        else:
            row.cells[r.sensor] = _cell(r)
            row.result = _row_result(row)
            row.code = "—" if row.result[1] != "pend" else ""
        self.dataChanged.emit(self.index(k, 0), self.index(k, len(HEAD) - 1))

    def apply_record(self, rec: RunRecord) -> None:
        """The finished run: every row from the record, which is what the CSV holds."""
        detail = str(rec.metadata.get("context", {}).get("error_detail", ""))  # type: ignore[union-attr]
        self._running = None
        for r in rec.results:
            k = self._by_step.get(r.step_id)
            if k is None:
                continue
            row = self._rows[k]
            if r.sensor == RUN:
                self._apply_run(row, r, detail, rec.error_code)
            else:
                row.cells[r.sensor] = _cell(r)
        for row in self._rows:
            if row.kind == "limit":
                row.result = _row_result(row)
                row.code = "—"
        verdict = self._rows[-1]
        prim = {r.sensor: r for r in rec.results if r.primary_failure}
        for ch in SENSORS:
            v = rec.sensor_verdicts.get(ch)
            if v is Result.PASS:
                verdict.cells[ch] = (chip("pass"), "pass", "all steps passed")
            elif v is Result.FAIL:
                p = prim.get(ch)
                tip = f"primary failure: {failure_message(p)} ({self._step_title(p.step_id)})" if p else ""
                verdict.cells[ch] = (chip("fail"), "fail", tip)
            else:
                why = (
                    "the run was aborted" if rec.outcome is Outcome.ABORTED else f"run ERROR {rec.error_code}"
                )
                verdict.cells[ch] = (chip("nt"), "nt", f"not judged: {why}")
        self._changed_all()

    def _apply_run(self, row: _Row, r: StepResult, error_detail: str, error_code: str = "") -> None:
        if r.result is Result.PASS:
            text = _RUN_PASS.get(r.step_id, "passed").format(
                v=f"{r.value:.2f}" if r.value is not None else ""
            )
            row.span, row.result, row.code = (text, "span", ""), (chip("pass"), "pass", ""), "—"
        elif r.result is Result.FAIL:
            row.span = (error_detail or r.not_tested_reason, "span", error_detail)
            row.result, row.code = (chip("fail"), "fail", ""), error_code or r.not_tested_reason
        else:
            reason = r.not_tested_reason
            row.span = (f"not tested: {reason}" if reason else "not tested", "span", "")
            row.result, row.code = (chip("nt"), "nt", f"not tested: {reason}"), "—"

    def _step_title(self, step_id: int) -> str:
        k = self._by_step.get(step_id)
        return self._rows[k].title if k is not None else str(step_id)

    def _changed_all(self) -> None:
        if self._rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, len(HEAD) - 1))

    # ---------------------------------------------------------------- for the view ----
    def spans(self) -> list[tuple[int, int, int]]:
        """(row, first column, column count) for QTableView.setSpan."""
        out = []
        for k, row in enumerate(self._rows):
            if row.kind == "heading":
                out.append((k, COL_TEST, len(HEAD) - COL_TEST))
            elif row.kind == "run":
                out.append((k, COL_CH0, len(SENSORS)))
        return out

    def row_kind(self, k: int) -> str:
        return self._rows[k].kind

    def cell(self, k: int, col: int) -> Cell:
        row = self._rows[k]
        if row.kind == "heading":
            return (row.title, "heading", "") if col == COL_TEST else _EMPTY
        if col == COL_ID:
            return (str(row.step.id), "", row.step.name) if row.step else _EMPTY
        if col == COL_TEST:
            tip = f"{row.step.id} {row.step.name}" if row.step else ""
            return (row.title, "title", tip)
        if col == COL_UNIT:
            return (unit_label(row.step.unit), "", row.step.unit) if row.step else _EMPTY
        if col == COL_LOW:
            return (row.low, "", "")
        if col == COL_HIGH:
            return (row.high, "", "")
        if COL_CH0 <= col < COL_RESULT:
            if row.kind == "run":
                return row.span if col == COL_CH0 else _EMPTY
            return row.cells.get(SENSORS[col - COL_CH0], _EMPTY)
        if col == COL_RESULT:
            return row.result
        if col == COL_CODE:
            return (row.code, "", "")
        return _EMPTY

    # ---------------------------------------------------------------- Qt model API ----
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008 (Qt API)
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(HEAD)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return HEAD[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            left = section == COL_TEST
            return (
                Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignCenter
            ) | Qt.AlignmentFlag.AlignVCenter
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        k, col = index.row(), index.column()
        row = self._rows[k]
        text, kind, tip = self.cell(k, col)
        if role == Qt.ItemDataRole.DisplayRole:
            return text
        if role == Qt.ItemDataRole.ToolTipRole:
            return tip or None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            left = col == COL_TEST or kind in ("span", "heading")
            h = Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignCenter
            return h | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.BackgroundRole:
            if kind in STATUS and kind != "pend":
                return QColor(STATUS[kind][1])
            if row.kind in ("heading", "verdict"):
                return QColor(PANEL)
            if self._running is not None and row.capture == self._running and row.kind != "heading":
                return QColor(STATUS["run"][1])
            return None
        if role == Qt.ItemDataRole.ForegroundRole:
            if kind in STATUS:
                return QColor(STATUS[kind][0])
            if kind in ("span", "heading") or col in (COL_ID, COL_UNIT):
                return QColor(MUTED)
            return None
        if role == Qt.ItemDataRole.FontRole:
            bold = kind in ("title", "fail", "heading") or (kind in STATUS and col == COL_RESULT)
            bold = bold or (row.kind == "verdict" and kind in STATUS)
            if bold:
                f = QFont()
                f.setBold(True)
                return f
        return None


def _cell(r: StepResult) -> Cell:
    if r.result is Result.NOT_TESTED or r.value is None:
        return ("–", "nt", f"not tested: {r.not_tested_reason or 'no value'}")
    kind = "pass" if r.result is Result.PASS else "fail"
    text = f"{SYMBOL[kind]} {fmt(r.value, r.unit)}"
    tip = ""
    if kind == "fail":
        side = "below Low" if r.low is not None and r.value < r.low else "above High"
        limits = f"{fmt(r.low, r.unit)} … {fmt(r.high, r.unit)}"
        tip = f"{fmt(r.value, r.unit)} {unit_label(r.unit)} is {side} ({limits})"
        if r.primary_failure:
            tip += f"; this channel's primary failure: {failure_message(r)}"
    return (text, kind, tip)


def _row_result(row: _Row) -> Cell:
    kinds = {c[1] for c in row.cells.values()}
    if "pend" in kinds:
        return ("", "pend", "")
    if "fail" in kinds:
        n = sum(c[1] == "fail" for c in row.cells.values())
        return (chip("fail"), "fail", f"{n} of {len(row.cells)} channels failed")
    if "pass" in kinds:
        return (chip("pass"), "pass", "")
    return (chip("nt"), "nt", "no channel was tested")


def make_step_view(model: StepTableModel) -> QTableView:
    """A read-only, no-selection table with fixed columns and a stretching Test column."""
    view = QTableView()
    view.setModel(model)
    view.verticalHeader().hide()
    view.setShowGrid(False)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    view.setWordWrap(False)
    view.setMouseTracking(True)  # tooltips on hover
    hh = view.horizontalHeader()
    hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    for c in range(len(HEAD)):
        view.setColumnWidth(c, WIDTHS.get(c, CH_WIDTH))
    hh.setSectionResizeMode(COL_TEST, QHeaderView.ResizeMode.Stretch)
    hh.setMinimumSectionSize(40)
    view.verticalHeader().setDefaultSectionSize(32)
    model.modelReset.connect(lambda: apply_spans(view, model))
    apply_spans(view, model)
    return view


def apply_spans(view: QTableView, model: StepTableModel) -> None:
    view.clearSpans()
    for row, col, n in model.spans():
        view.setSpan(row, col, 1, n)
