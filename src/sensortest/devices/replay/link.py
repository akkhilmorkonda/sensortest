"""Replay link (D-031, D-049): a recorded Teensy, played back from a recording set.

The set is either a bench recording folder (``data_dir``, e.g. the DOE-1 captures, kept on the
bench machine and not in git) or, with ``synthetic: true``, one generated from the device model
(``replay/synthetic.py``, D-060), so the station and its tests run anywhere.

It stands in for the USB port and the device behind it. It takes the same command text as the
real device and answers the way the real firmware does (D-007, D-009):
- ``baseline`` before any tone serves the reference 10 Hz run's pre-baseline lines; after a tone,
  that run's recovery lines. No reply.
- ``response:N`` with N in 1-19 serves the tone segment of the run_index-th DOE-1 run at N and
  replies ``N.00``. Outside 1-19 it replies ``response frequency invalid`` and keeps the old tone.
- anything else replies ``command not recognized``.
Lines keep their recorded host times, so line-rate checks see real timing. With ``realtime`` on,
reading takes as long as on the device and checks the cancel event every 50 ms (Abort, D-043).

``fault`` makes the recorded device misbehave, to test every error path through the real
drivers' checks: wrong_ack (replies 19.00), no_reply, reject_baseline (command not recognized),
stall (stops sending after the first capture), drop_lines (5% fewer lines per second after the
first capture), lost (gone once a tone has been set). The phase_jump fault went with E211 (D-058).
"""

import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from sensortest.core.errors import Aborted, TestError
from sensortest.core.ports import ChunkFn
from sensortest.devices.registry import LinkInfo, link
from sensortest.devices.replay import synthetic
from sensortest.devices.replay.recordings import Recording, read_manifest, read_recording
from sensortest.devices.taps import CHUNK_LINES

FAULTS = {"none", "wrong_ack", "no_reply", "stall", "drop_lines", "reject_baseline", "lost"}
REFERENCE_TONE = 10  # the run whose pre-baseline serves the first baseline capture
LINE_RATE_HZ = 150.0  # pacing when realtime is on (the device's measured rate, D-007)


class LinkLost(Exception):
    """The recorded device disappeared (fault ``lost``). Drivers turn this into E209."""


@link("replay")
class ReplayLink:
    def __init__(self, info: LinkInfo, data_dir: Path, run_index: int, realtime: bool, fault: str) -> None:
        self.info = info
        self.fault = fault
        self._idx, self._realtime = run_index, realtime
        self._files: dict[int, list[Path]] = {}
        for run in read_manifest(data_dir):  # the shared recording reader (D-050)
            if run.tone_hz is not None:
                self._files.setdefault(run.tone_hz, []).append(run.tsv)
        self._cache: dict[int, Recording] = {}
        self._segment: tuple[Recording, int] | None = None  # (recording, next line index)
        self._last_tone: int | None = None
        self._reads = 0
        self._closed = False

    # ---------------------------------------------------------------- discovery ----
    @classmethod
    def discover(cls, options: Mapping[str, Any]) -> list[LinkInfo]:
        """Found when its recording set is there. A synthetic set is always there (it is made on
        first use). Never generates anything for a bench folder."""
        run = int(options.get("run_index", 0))
        if options.get("synthetic"):
            return [LinkInfo("replay", "replay", f"REPLAY-SYNTHETIC-{run}")]
        data_dir = Path(str(options.get("data_dir", "")))
        if not (data_dir / "manifest.csv").exists():
            return []
        return [LinkInfo("replay", "replay", f"REPLAY-{data_dir.name}-{run}")]

    @classmethod
    def open(cls, info: LinkInfo, options: Mapping[str, Any]) -> "ReplayLink":
        fault = str(options.get("fault", "none"))
        if fault not in FAULTS:
            raise TestError("E301", f"replay fault {fault!r} not in {sorted(FAULTS)}")
        data_dir = synthetic.default_set() if options.get("synthetic") else Path(str(options["data_dir"]))
        return cls(
            info,
            data_dir,
            int(options.get("run_index", 0)),
            bool(options.get("realtime", True)),
            fault,
        )

    def close(self) -> None:
        self._closed = True

    # ---------------------------------------------------------------- the recorded device ----
    def command(self, text: str) -> str | None:
        """Send one command; returns the device's reply line, or None if it sends none."""
        if self._closed or (self.fault == "lost" and self._last_tone is not None):
            raise LinkLost("replay fault: device lost")
        if text == "baseline":
            if self.fault == "reject_baseline":
                return "command not recognized"
            ref = self._last_tone if self._last_tone is not None else REFERENCE_TONE
            run = self._run(ref)
            self._segment = (run, run.after(0 if self._last_tone is None else 2))
            return None
        if text.startswith("response:"):
            try:
                f = int(text.removeprefix("response:"))
            except ValueError:
                return "command not recognized"
            if not 1 <= f <= 19:
                return "response frequency invalid"  # the device then repeats the old tone; not needed
            if self.fault == "no_reply":
                return None
            run = self._run(f)
            self._segment = (run, run.after(1))
            self._last_tone = f
            return "19.00" if self.fault == "wrong_ack" else f"{f}.00"
        return "command not recognized"

    def read(
        self, n: int, cancel: threading.Event | None = None, on_chunk: ChunkFn | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """The next ``n`` data lines as (host times, values). Fewer if the device stops sending.

        ``on_chunk`` gets the lines CHUNK_LINES at a time, as they would arrive (D-053)."""
        if self._closed or (self.fault == "lost" and self._last_tone is not None):
            raise LinkLost("replay fault: device lost")
        if self._segment is None or (self.fault == "stall" and self._reads >= 1):
            self._pace(n, cancel)
            return np.empty(0), np.empty((0, 8))
        run, i0 = self._segment
        src = np.arange(i0, i0 + n)  # the recorded lines whose values are served
        src = src[src < len(run.x)]
        self._segment = (run, int(src[-1]) + 1 if len(src) else i0)
        t = run.t[i0 : i0 + len(src)].copy()  # stamps of consecutive recorded lines
        if self.fault == "drop_lines" and self._reads >= 1 and len(t):
            t = t[0] + (t - t[0]) * 1.05  # 5% fewer lines per second, like dropped lines
        self._reads += 1
        x = run.x[src].copy()
        for a in range(0, n, CHUNK_LINES):  # served at the device's pace, one chunk at a time
            b = min(a + CHUNK_LINES, n)
            self._pace(b - a, cancel)
            if on_chunk is not None and a < len(t):
                on_chunk(a, t[a:b], x[a:b])
        return t, x

    def _run(self, f: int) -> Recording:
        if f not in self._cache:
            files = self._files.get(f)
            if not files:
                raise TestError("E202", f"replay has no DOE-1 run at {f} Hz")
            self._cache[f] = read_recording(files[self._idx % len(files)])
        return self._cache[f]

    def _pace(self, lines: int, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise Aborted()
        if not self._realtime:
            return
        end = time.monotonic() + lines / LINE_RATE_HZ
        while (left := end - time.monotonic()) > 0:
            if cancel is not None and cancel.wait(min(0.05, left)):
                raise Aborted()
            if cancel is None:
                time.sleep(min(0.05, left))
