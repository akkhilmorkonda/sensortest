"""Entry point: ``python -m sensortest`` (GUI) or ``python -m sensortest --headless`` (D-034).

The packaged app runs the same entry point; see ``app/locations.py`` for where it keeps its settings
and records.
"""

import argparse
import os
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from sensortest.app.locations import default_settings

    p = argparse.ArgumentParser(prog="sensortest", description=__doc__)
    p.add_argument("--settings", type=Path, default=None, help="settings file (default: config/)")
    p.add_argument("--headless", action="store_true", help="run without the GUI")
    p.add_argument("--sequence", default="PROD", help="headless only: PROD or ENG")
    p.add_argument("--runs", type=int, default=1, help="headless only: number of runs")
    p.add_argument("--serial", default="", help="headless only: DUT serial for every run (required)")
    p.add_argument("--fast", action="store_true", help="replay without real-time pacing")
    p.add_argument("--fixture", default=None, help="override active_fixture from the settings, e.g. teensy")
    args = p.parse_args(argv)
    if args.settings is None:
        args.settings = default_settings()
    return args


def _console_streams() -> None:
    """A windowed build has no console (sys.stdout is None). For a headless run, write to the
    terminal it was started from (Windows), or discard the output; never crash on a print."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    if os.name == "nt":
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):  # the parent terminal, if there is one
            sys.stdout = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115 (lives for the process)
            sys.stderr = sys.stdout
            return
    sys.stdout = sys.stdout or open(os.devnull, "w")  # noqa: SIM115
    sys.stderr = sys.stderr or open(os.devnull, "w")  # noqa: SIM115


def main(argv: list[str] | None = None) -> int:
    _console_streams()
    args = parse_args(argv)
    if args.headless:
        from sensortest.app.headless import run_headless

        return run_headless(
            args.settings, args.sequence, args.runs, args.serial, fast=args.fast, fixture=args.fixture
        )
    from sensortest.app.gui import run_gui

    return run_gui(args.settings, fast=args.fast, fixture=args.fixture)


if __name__ == "__main__":
    sys.exit(main())
