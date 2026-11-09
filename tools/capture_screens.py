"""Screenshots of the real station screen (D-053), driven through its own buttons.

It runs the app exactly as `python -m sensortest` does (same wiring, engine, drivers and window)
on a copy of config/settings.yaml whose records go to a temp folder, starts one run, and saves the
window at each state: idle, running (mid-tone), the result, one channel enlarged, and test mode.

Usage (project .venv):
  python tools/capture_screens.py                      # synthetic recordings, PROD (runs anywhere)
  python tools/capture_screens.py --fixture replay     # the real DOE-1 recordings (bench data, local)
  python tools/capture_screens.py --fixture teensy     # the Teensy on USB, PROD
  python tools/capture_screens.py --fault drop_lines   # an ERROR state (E205, retryable once)
  python tools/capture_screens.py --sequence ENG       # test mode, ENG

Screenshots go to docs/gui_screens/ (or --out).

On the Teensy it sends only `baseline` and `response:10` (PROD) or the ENG tones, like any run.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from sensortest.app.gui import station_info
from sensortest.app.wiring import build
from sensortest.engine.states import AppState
from sensortest.ui import theme
from sensortest.ui.bridge import QtBridge
from sensortest.ui.main_window import MainWindow

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"
SCREENS = REPO / "docs" / "gui_screens"
SIZE = (1280, 800)  # the spec's minimum window (GUI spec §3)


def settings_copy(folder: Path, fixture: str, fault: str) -> Path:
    s = yaml.safe_load((CONFIG / "settings.yaml").read_text(encoding="utf-8"))
    for key in ("sensor_tests", "limits"):
        s[key] = str(CONFIG / s[key])
    s["data_dir"] = str(folder / "out")
    s["active_fixture"] = fixture
    for fx in s["fixtures"].values():
        for link in fx["links"].values():
            if link["type"] != "replay":
                continue
            if "data_dir" in link:  # bench recordings (local only); synthetic sets need no folder
                link["data_dir"] = str((CONFIG / link["data_dir"]).resolve())
            link["fault"] = fault
    path = folder / "settings.yaml"
    path.write_text(yaml.safe_dump(s), encoding="utf-8")
    return path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fixture", default="synthetic", choices=["synthetic", "replay", "teensy"])
    p.add_argument("--fault", default="none", help="replay only, e.g. phase_jump")
    p.add_argument("--sequence", default="PROD", choices=["PROD", "ENG"])
    p.add_argument("--serial", default="SN-0001")
    p.add_argument("--channel", default="ch4", help="the channel to enlarge")
    p.add_argument("--out", type=Path, default=SCREENS)
    a = p.parse_args()
    tag = f"{a.fixture}_{a.sequence.lower()}" + (f"_{a.fault}" if a.fault != "none" else "")

    qt = QApplication(sys.argv)
    theme.apply(qt)
    tmp = Path(tempfile.mkdtemp(prefix="sensortest-screens-"))
    app = build(settings_copy(tmp, a.fixture, a.fault))
    win = MainWindow(app.engine, QtBridge(app.bus), station_info(app.settings))
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    win.resize(*SIZE)
    win.show()
    app.start()

    def pump(cond, timeout_s: float = 30.0) -> None:
        end = time.monotonic() + timeout_s
        while not cond():
            if time.monotonic() > end:
                raise TimeoutError(f"state {app.engine.state}")
            qt.processEvents()
            time.sleep(0.01)

    def settle(s: float) -> None:
        end = time.monotonic() + s
        pump(lambda: time.monotonic() > end)

    def grab(state: str) -> None:
        win.resize(*SIZE)
        qt.processEvents()
        out = a.out / f"{tag}_{state}.png"
        win.grab().save(str(out))
        print(f"wrote {out.name}  (window {win.width()}x{win.height()}, min {win.minimumSizeHint().width()})")

    try:
        pump(lambda: app.engine.state is AppState.IDLE and all(app.devices.roles_present().values()), 15)
        if a.sequence != "PROD":
            win.titlebar.test_btn.click()
            win.seq_combo.setCurrentText(a.sequence)
        settle(0.5)
        grab("idle")
        win.serial.setText(a.serial)
        settle(0.2)
        win.serial.returnPressed.emit()
        pump(lambda: app.engine.state is AppState.RUNNING)
        pump(lambda: win.seqbar._now is not None and win.seqbar._now >= 2)  # the first tone
        settle(1.2)
        grab("running")
        pump(lambda: app.engine.state is AppState.RESULT and win._last is not None, 60)
        settle(0.6)
        grab("result")
        win._enlarge(a.channel)
        settle(0.4)
        grab("zoom")
        win.zoom.back.emit()
        win.titlebar.test_btn.click()
        win.bottom.setCurrentIndex(1)  # Run header
        settle(0.4)
        grab("test_mode")
        rec = win._last
        print(f"run {rec.run_id}: {rec.outcome.value} {rec.error_code}".rstrip())
    finally:
        app.stop()
        win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
