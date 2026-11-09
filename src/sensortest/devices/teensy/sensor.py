"""Teensy sensor driver (D-049): the sensor role on the Teensy's USB serial link.

Counts data lines into windows (settle, then window, D-027) and checks the stream on the way:
an unreadable line is E105 in the first capture and E206 after; no line for stall_timeout_s is
E208; a device that disappears is E209; lines the host fell behind on are E205. Line rate and
idle checks happen in the engine (1000 CONNECT, 1900 RUN_INTEGRITY). It reads only the data lines
(with a comma); the actuator driver reads the replies from the same link.
"""

import threading
import time
from collections.abc import Mapping
from typing import Any

import numpy as np

from sensortest.core.errors import Aborted, TestError
from sensortest.core.models import Window
from sensortest.core.ports import ChunkFn
from sensortest.devices.linequeue import Line
from sensortest.devices.registry import sensor
from sensortest.devices.serial_link import SerialLink
from sensortest.devices.taps import CHUNK_LINES, DataThinner, TrafficFn, report
from sensortest.devices.teensy import protocol as tp

POLL_S = 0.05  # how often a wait checks the cancel event: bounds Abort latency (D-043)


@sensor("teensy")
class TeensySensor:
    link_types = frozenset({"serial"})

    def __init__(self, link: SerialLink, options: Mapping[str, Any]) -> None:
        self._link = link
        self._lines = link.subscribe(tp.is_data_line, maxlen=int(options.get("queue_lines", 4096)))
        self._stall = float(options.get("stall_timeout_s", 0.5))
        self._captures = 0
        self._thin = DataThinner()
        self.on_traffic: TrafficFn | None = None  # set by the device manager (terminal, D-053)

    def acquire(
        self,
        capture: str,
        settle_lines: int,
        window_lines: int,
        cancel: threading.Event | None = None,
        on_chunk: ChunkFn | None = None,
    ) -> Window:
        # Lines received before this call came before the command that started this capture.
        self._lines.discard_before(time.perf_counter())
        dropped = self._lines.dropped
        total = settle_lines + window_lines
        all_t = np.empty(total)
        all_x = np.empty((total, tp.CHANNELS))
        sent = 0  # lines already handed to on_chunk
        for i in range(total):
            ts, line = self._next(capture, cancel)
            values = tp.parse_data(line)
            if values is None:
                report(self.on_traffic, "rx", line.decode("ascii", "replace").strip())
                code = "E105" if self._captures == 0 else "E206"
                raise TestError(code, f"unreadable line {line!r}", where=None if code == "E105" else capture)
            all_t[i], all_x[i] = ts, values
            if self.on_traffic is not None and self._thin.due(ts):
                report(self.on_traffic, "data", line.decode("ascii", "replace").strip())
            if on_chunk is not None and (i + 1 - sent >= CHUNK_LINES or i + 1 == total):
                on_chunk(sent, all_t[sent : i + 1].copy(), all_x[sent : i + 1].copy())
                sent = i + 1
        t, x = all_t[settle_lines:].copy(), all_x[settle_lines:].copy()
        if self._lines.dropped != dropped:
            lost = self._lines.dropped - dropped
            raise TestError("E205", f"{lost} lines lost: the host fell behind", where=capture)
        self._captures += 1
        return Window(capture, x, t)

    def close(self) -> None:
        """Nothing to release: the link owns the port."""

    def _next(self, capture: str, cancel: threading.Event | None) -> Line:
        deadline = time.monotonic() + self._stall
        while True:
            if cancel is not None and cancel.is_set():
                raise Aborted()
            if self._link.lost:
                raise TestError("E209", f"{self._link.info.port}: {self._link.lost}", where=capture)
            left = deadline - time.monotonic()
            if left <= 0:
                raise TestError("E208", f"no line for {self._stall:g} s", where=capture)
            item = self._lines.get(min(POLL_S, left))
            if item is not None:
                return item
