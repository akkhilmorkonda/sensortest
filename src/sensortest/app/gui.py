"""GUI entry point: build the app, attach the Qt adapter, run the event loop, shut down cleanly."""

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import QApplication

from sensortest.app.wiring import build
from sensortest.ui import theme
from sensortest.ui.bridge import QtBridge
from sensortest.ui.main_window import MainWindow, StationInfo


def run_gui(settings_path: Path, fast: bool = False, fixture: str | None = None) -> int:
    qt = QApplication.instance() or QApplication(sys.argv)
    theme.apply(qt)  # type: ignore[arg-type]
    app = build(settings_path, fixture=fixture, fast=fast)
    bridge = QtBridge(app.bus)  # subscribe before start, so the first device status is seen
    window = MainWindow(app.engine, bridge, station_info(app.settings))
    app.start()
    window.show()
    try:
        return qt.exec()
    finally:
        app.stop()  # Quit → abort a running test (safe state) → stop polling → flush records


def station_info(settings) -> StationInfo:
    """What the title bar shows about the station and its fixture, from the settings."""
    return StationInfo(
        station=settings.station,
        operator_sequence=settings.operator_sequence,
        fixture=settings.active_fixture,
        fixture_where=fixture_where(settings.fixture),
        plot_fps=settings.plot_fps,
    )


def fixture_where(fixture: Mapping[str, Any]) -> str:
    """Where the fixture's devices are, before a run reports the actual port."""
    places = []
    for link in fixture["links"].values():
        if link["type"] == "serial":
            port = str(link.get("port", "auto"))
            places.append(port if port != "auto" else f"USB {link.get('usb_id', 'any')}")
        elif link["type"] == "replay" and link.get("synthetic"):
            places.append("device model")
        elif link["type"] == "replay":
            places.append("recorded DOE-1 data")
        else:
            places.append(str(link["type"]))
    return ", ".join(places)
