"""Teensy actuator driver (D-049): the actuator role on the Teensy's USB serial link.

Writes ``baseline`` and ``response:N`` and checks each reply. It only ever sends those two
commands, with N checked to be 1-19 before anything is written (D-009), so nothing can reach the
firmware that could change it. It reads only the reply lines (no comma); the sensor driver reads
the data lines from the same link.
"""

import logging
import time
from collections.abc import Mapping
from typing import Any

from sensortest.core.errors import TestError
from sensortest.devices.registry import actuator
from sensortest.devices.serial_link import SerialLink
from sensortest.devices.taps import TrafficFn, report
from sensortest.devices.teensy import protocol as tp

log = logging.getLogger(__name__)


@actuator("teensy")
class TeensyActuator:
    link_types = frozenset({"serial"})

    def __init__(self, link: SerialLink, options: Mapping[str, Any]) -> None:
        self._link = link
        self._replies = link.subscribe(tp.is_reply_line, maxlen=64)
        self._reply_timeout = float(options.get("reply_timeout_s", 0.5))
        # `baseline` has no reply, so it counts as accepted if no rejection arrives in this time.
        # Replies come within ~1 ms (bring-up); 50 ms leaves a wide margin.
        self._reject_window = float(options.get("reject_window_s", 0.05))
        self._sent = 0
        self._off = False  # True after an accepted `baseline`, until the next command
        self.on_traffic: TrafficFn | None = None  # set by the device manager (terminal, D-053)

    def baseline(self) -> None:
        first = self._sent == 0
        tp.check_baseline_reply(self._send(tp.BASELINE, self._reject_window), first)
        self._off = True

    def set_tone(self, hz: float) -> str:
        command = tp.tone_command(hz)  # E202 before sending if outside 1-19
        reply = self._send(command, self._reply_timeout)
        tp.check_tone_reply(int(hz), reply)
        assert reply is not None  # check_tone_reply raises E203 on no reply
        return reply

    def close(self) -> None:
        """Best-effort baseline so the actuator is never left running (D-010). Never raises.

        Skipped when the last command was an accepted `baseline` (the run's END step).
        """
        if self._off:
            return
        try:
            report(self.on_traffic, "tx", tp.BASELINE)
            self._link.write(tp.encode(tp.BASELINE))
        except Exception as e:
            log.warning("closing actuator: baseline not sent: %s", e)

    def _send(self, command: str, wait_s: float) -> str | None:
        """Write one command; return the first non-empty reply within ``wait_s``, else None."""
        self._replies.clear()  # drop leftovers, e.g. the `M.00` that follows a rejection
        self._sent += 1
        self._off = False
        report(self.on_traffic, "tx", command)
        self._link.write(tp.encode(command))
        deadline = time.monotonic() + wait_s
        while (left := deadline - time.monotonic()) > 0:
            item = self._replies.get(left)
            if self._link.lost:
                raise TestError("E209", f"{self._link.info.port}: {self._link.lost}")
            if item is not None and (text := item[1].decode("ascii", "replace").strip()):
                report(self.on_traffic, "rx", text)
                return text
        return None
