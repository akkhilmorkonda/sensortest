"""What drivers report while they work, for the live views (D-053). Never part of a verdict.

- **Sample chunks:** a sensor driver hands every line it reads to ``acquire``'s ``on_chunk``
  callback, ``CHUNK_LINES`` at a time, so the live plot moves while a capture runs.
- **Traffic:** each driver has an ``on_traffic`` hook, set by the device manager. Actuator drivers
  report every command ("tx") and reply ("rx"); sensor drivers report data lines ("data"),
  thinned to one per ``DATA_INTERVAL_S``, and any unreadable line ("rx") always. The thinning is
  done here, at the source, so the 150 Hz stream never floods the event bus or the UI.
"""

import logging
import math
from collections.abc import Callable

log = logging.getLogger(__name__)
CHUNK_LINES = 15  # ~0.1 s at 150 Hz: the live plot updates about 10 times a second
DATA_INTERVAL_S = 0.2  # the terminal shows about 5 data lines a second

TrafficFn = Callable[[str, str], None]  # (direction: "tx" | "rx" | "data", text)


class DataThinner:
    """Says whether a data line at host time ``t`` should be shown (one per interval)."""

    def __init__(self, interval_s: float = DATA_INTERVAL_S) -> None:
        self._interval = interval_s
        self._last = -math.inf

    def due(self, t: float) -> bool:
        if t - self._last >= self._interval:
            self._last = t
            return True
        return False


def report(fn: TrafficFn | None, direction: str, text: str) -> None:
    """Call a traffic hook if one is set. A failing hook never breaks the driver."""
    if fn is None:
        return
    try:
        fn(direction, text)
    except Exception:  # the terminal is a view; it must never stop a test
        log.debug("traffic hook failed", exc_info=True)
