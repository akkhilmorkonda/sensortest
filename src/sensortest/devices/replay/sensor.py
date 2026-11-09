"""Replay sensor driver (D-031, D-049): the sensor role on the replay link.

Reads settle + window recorded lines per capture and returns the window. A recording that stops
early (fault ``stall``, or the end of the file) is E208; a lost device is E209. Line rate is
checked in the engine, as for the Teensy. Live chunks and thinned data lines are reported the
same way as on the Teensy (D-053), with data lines printed the way the device prints them.
"""

import threading
from collections.abc import Mapping
from typing import Any

import numpy as np

from sensortest.core.errors import TestError
from sensortest.core.models import Window
from sensortest.core.ports import ChunkFn
from sensortest.devices.registry import sensor
from sensortest.devices.replay.link import LinkLost, ReplayLink
from sensortest.devices.taps import DataThinner, TrafficFn, report


def device_line(values: np.ndarray) -> str:
    """A data line as the Teensy prints it: 2 decimals, each followed by ", " (docs/device_protocol.md)."""
    return "".join(f"{v:.2f}, " for v in values).rstrip()


@sensor("replay")
class ReplaySensor:
    link_types = frozenset({"replay"})

    def __init__(self, link: ReplayLink, options: Mapping[str, Any]) -> None:
        self._link = link
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
        n = settle_lines + window_lines

        def chunk(start: int, t: np.ndarray, x: np.ndarray) -> None:
            if self.on_traffic is not None:
                for ts, row in zip(t, x, strict=True):
                    if self._thin.due(float(ts)):
                        report(self.on_traffic, "data", device_line(row))
            if on_chunk is not None:
                on_chunk(start, t, x)

        try:
            t, x = self._link.read(n, cancel, chunk)
        except LinkLost as e:
            raise TestError("E209", str(e), where=capture) from e
        if len(t) < n:
            raise TestError("E208", f"device stopped sending after {len(t)} of {n} lines", where=capture)
        return Window(capture, x[settle_lines:], t[settle_lines:])

    def close(self) -> None:
        """Nothing to release: the link owns the recording."""
