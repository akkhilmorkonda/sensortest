"""Run the station's test suite: the entry point of the packaged self-test app (sensortest-selftest).

The packaged app carries the test suite, the config and the sources the tests read, and runs them
with pytest, exactly as ``python -m pytest`` does from source: no hardware and no bench data needed
(the GUI tests run offscreen). With a Teensy plugged in, ``--hardware`` also runs the two on-device
tests. Any other pytest option is passed through. The exit code is pytest's (0 = all passed).

Double-clicked, it waits for Enter before closing so the results stay on screen; ``--no-pause``
(or running in CI) skips that.

    sensortest-selftest                 # the whole suite
    sensortest-selftest --hardware      # also PROD + ENG on the Teensy
    sensortest-selftest -k gui          # only the GUI tests
    python tools/selftest.py            # the same, from source
"""

import os
import sys
from pathlib import Path


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # the GUI tests need no display
    import pytest

    args = [a for a in sys.argv[1:] if a != "--no-pause"]
    pause = "--no-pause" not in sys.argv[1:] and not os.environ.get("CI") and sys.stdin.isatty()
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    print("sensortest self-test: the station's test suite (no hardware or bench data needed).")
    print("Add --hardware to also run PROD and ENG on a connected Teensy.\n", flush=True)
    os.chdir(base)  # as from the repo root, so results read tests/<file>, not a temp-folder path
    code = int(pytest.main(["tests", "-p", "no:cacheprovider", "-ra", *args]))
    print("\nResult:", "ALL TESTS PASSED" if code == 0 else f"FAILURES (pytest exit code {code})")
    if pause:
        input("Press Enter to close.")
    return code


if __name__ == "__main__":
    sys.exit(main())
