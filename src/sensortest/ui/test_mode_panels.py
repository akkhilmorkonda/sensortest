"""Test-mode panels (GUI spec §7, D-053), registered as @panel plug-ins (D-033).

Each is a tab beside the serial terminal in test mode, built from the event bridge and filled
from the finished RunRecord. Read-only: nothing here can change a result or a verdict (D-029).
"""

from collections.abc import Mapping

from PyQt6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem

from sensortest.core.models import SENSORS, RunRecord
from sensortest.events.messages import Event, PlansLoaded, RunCompleted
from sensortest.ui.bridge import QtBridge
from sensortest.ui.registry import PANELS, PanelDef, View
from sensortest.ui.theme import unit_label


class RunHeaderPanel(QTreeWidget):
    """The run's header and context: IDs, versions, device, line rate per capture, the commands
    with their times, warnings and the error detail."""

    def __init__(self, bridge: QtBridge) -> None:
        super().__init__()
        self.setHeaderLabels(["Field", "Value"])
        self.setColumnWidth(0, 220)
        self.setRootIsDecorated(True)
        self._empty()
        bridge.event.connect(self._on_event)

    def _empty(self) -> None:
        self.clear()
        QTreeWidgetItem(self, ["No run yet", "The header of the last run appears here"])

    def _on_event(self, e: Event) -> None:
        if isinstance(e, RunCompleted):
            self.show_record(e.record)

    def show_record(self, rec: RunRecord) -> None:
        self.clear()
        ctx: Mapping = rec.metadata.get("context", {})  # type: ignore[assignment]
        device = ctx.get("device", {})

        def add(field: str, value: object, parent: QTreeWidget | QTreeWidgetItem = self) -> QTreeWidgetItem:
            return QTreeWidgetItem(parent, [field, str(value)])

        add("Run ID", rec.run_id)
        add("Timestamp", rec.timestamp)
        add("Outcome", f"{rec.outcome.value} {rec.error_code}".strip())
        if ctx.get("error_detail"):
            add("Error detail", ctx["error_detail"])
        add("DUT serial", rec.dut_serial)
        add("Attempt", f"{rec.attempt}" + (f" (retest of {rec.retest_of})" if rec.retest_of else ""))
        add("Sequence", rec.sequence)
        add("Test plan", f"{rec.test_plan} {rec.plan_version}")
        add("Limit set", rec.limit_set)
        add("App version", rec.app_version)
        add("Station", rec.station)
        add(
            "Device",
            f"{device.get('drivers', '')}  ·  {device.get('port', '')}  ·  USB serial {rec.device_serial}",
        )
        rates = add("Line rate per capture (Hz)", "")
        for cap, hz in ctx.get("line_rate_hz", {}).items():
            add(cap, f"{hz:.3f}", rates)
        durations = add("Durations (s)", "")
        for cap, s in ctx.get("duration_s", {}).items():
            add(cap, f"{s:.3f}", durations)
        cmds = add("Commands (s from run start)", f"{len(ctx.get('commands', []))} sent")
        for c in ctx.get("commands", []):
            add(f"{c['t']:.3f}", c["cmd"], cmds)
        add("Warnings", ", ".join(ctx.get("warnings", [])) or "none")
        self.expandAll()


class DiagnosticsPanel(QTableWidget):
    """Diagnostics (D-029): capture → metric → the 8 channel values. Measured, never judged."""

    def __init__(self, bridge: QtBridge) -> None:
        super().__init__(0, 3 + len(SENSORS))
        self.setHorizontalHeaderLabels(["Capture", "Diagnostic", "Unit", *SENSORS])
        self.verticalHeader().hide()
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._units: dict[str, str] = {}
        bridge.event.connect(self._on_event)

    def _on_event(self, e: Event) -> None:
        if isinstance(e, PlansLoaded):
            self._units = {d.key: d.unit for p in e.plans.values() for d in p.diagnostics}
        elif isinstance(e, RunCompleted):
            self.show_record(e.record)

    def show_record(self, rec: RunRecord) -> None:
        diags: Mapping = rec.metadata.get("diagnostics", {})  # type: ignore[assignment]
        rows = [(cap, key, vals) for cap, keyed in diags.items() for key, vals in keyed.items()]
        self.setRowCount(len(rows))
        for r, (cap, key, vals) in enumerate(rows):
            cells = [cap, key, unit_label(self._units.get(key, ""))]
            cells += ["–" if vals.get(ch) is None else f"{vals[ch]:.4g}" for ch in SENSORS]
            for c, text in enumerate(cells):
                self.setItem(r, c, QTableWidgetItem(text))


PANELS.register("run_header")(PanelDef("Run header", RunHeaderPanel, View.TEST, order=10))
PANELS.register("diagnostics")(PanelDef("Diagnostics", DiagnosticsPanel, View.TEST, order=20))
