"""Entry point: ``python -m sensortest`` (GUI) or ``python -m sensortest --headless`` (D-034)."""

import argparse
import sys
from pathlib import Path

DEFAULT_SETTINGS = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sensortest", description=__doc__)
    p.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    p.add_argument("--headless", action="store_true", help="run without the GUI")
    p.add_argument("--sequence", default="PROD", help="headless only: PROD or ENG")
    p.add_argument("--runs", type=int, default=1, help="headless only: number of runs")
    p.add_argument("--serial", default="", help="headless only: DUT serial for every run (required)")
    p.add_argument("--fast", action="store_true", help="replay without real-time pacing")
    p.add_argument("--fixture", default=None, help="override active_fixture from the settings, e.g. teensy")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
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
