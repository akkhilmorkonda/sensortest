"""Does the station screen disturb the line-rate measurement? (D-054)

Runs N PROD tests on the Teensy through the real app, with the GUI drawing (window shown off
screen) or headless, and prints the host-timed line rate of each capture (M9, the number the 1%
rule in 1000 CONNECT and 1900 RUN_INTEGRITY judges, D-008). Optional: antialiased drawing, to
reproduce the bias it caused.

Usage (project .venv, Teensy on USB; sends only `baseline` and `response:10`):
  python benchtop_testing/tools/gui_line_rate.py --mode gui --runs 4
  python benchtop_testing/tools/gui_line_rate.py --mode gui --runs 4 --antialias
  python benchtop_testing/tools/gui_line_rate.py --mode headless --runs 4
"""

import argparse
import queue
import statistics
import sys
import tempfile
import time
from pathlib import Path

import pyqtgraph as pg
import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from sensortest.app.gui import station_info
from sensortest.app.wiring import build
from sensortest.engine.states import AppState, Trigger
from sensortest.events.messages import Next, RunCompleted, StartRun
from sensortest.ui import theme
from sensortest.ui.bridge import QtBridge
from sensortest.ui.main_window import MainWindow


CONFIG = Path(__file__).resolve().parents[2] / "config"


def settings_copy(folder: Path, fixture: str) -> Path:
    """config/settings.yaml with absolute paths, records in ``folder``, on ``fixture``."""
    s = yaml.safe_load((CONFIG / "settings.yaml").read_text(encoding="utf-8"))
    for key in ("sensor_tests", "limits"):
        s[key] = str(CONFIG / s[key])
    s["data_dir"] = str(folder / "out")
    s["active_fixture"] = fixture
    for link in s["fixtures"]["replay"]["links"].values():
        link["data_dir"] = str((CONFIG / link["data_dir"]).resolve())
    path = folder / "settings.yaml"
    path.write_text(yaml.safe_dump(s), encoding="utf-8")
    return path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["gui", "headless"], default="gui")
    p.add_argument("--runs", type=int, default=4)
    p.add_argument("--antialias", action="store_true", help="draw antialiased (the old setting)")
    p.add_argument("--fixture", default="teensy")
    a = p.parse_args()

    qt = QApplication(sys.argv)
    theme.apply(qt)
    if a.antialias:
        pg.setConfigOptions(antialias=True)
    app = build(settings_copy(Path(tempfile.mkdtemp(prefix="sensortest-linerate-")), a.fixture))
    done: queue.Queue = queue.Queue()
    app.bus.subscribe(RunCompleted, lambda e: done.put(e.record))
    if a.mode == "gui":
        win = MainWindow(app.engine, QtBridge(app.bus), station_info(app.settings))
        win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        win.resize(1280, 800)
        win.show()
    app.start()

    def pump(cond, timeout_s: float = 30.0) -> None:
        end = time.monotonic() + timeout_s
        while not cond():
            if time.monotonic() > end:
                raise TimeoutError(f"state {app.engine.state}")
            qt.processEvents()
            time.sleep(0.005)

    rates: dict[str, list[float]] = {}
    label = f"{a.mode}{' antialiased' if a.antialias else ''}"
    try:
        pump(lambda: app.engine.state is AppState.IDLE and app.engine.can(Trigger.START))
        for i in range(a.runs):
            app.engine.submit(StartRun("PROD", f"LR-{i + 1}"))
            pump(lambda: not done.empty())
            rec = done.get()
            line_rate = rec.metadata["context"]["line_rate_hz"]
            for cap, hz in line_rate.items():
                rates.setdefault(cap, []).append(hz)
            print(f"{label} run {i + 1}: {rec.outcome.value} line rate {line_rate}")
            app.engine.submit(Next())
            pump(lambda: app.engine.state is AppState.IDLE)
    finally:
        app.stop()
    for cap, values in rates.items():
        median = statistics.median(values)
        print(f"{label} {cap}: {min(values):.2f}-{max(values):.2f} Hz (median {median:.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
