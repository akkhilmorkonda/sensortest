"""Replay actuator driver (D-031, D-049): the actuator role on the replay link.

Sends the same command text as the Teensy actuator and checks the recorded device's replies with
the same rules, so every reply fault (wrong value, none, rejected) is tested without hardware.
"""

import logging
from collections.abc import Mapping
from typing import Any

from sensortest.core.errors import TestError
from sensortest.devices.registry import actuator
from sensortest.devices.replay.link import LinkLost, ReplayLink
from sensortest.devices.taps import TrafficFn, report
from sensortest.devices.teensy import protocol as tp

log = logging.getLogger(__name__)


@actuator("replay")
class ReplayActuator:
    link_types = frozenset({"replay"})

    def __init__(self, link: ReplayLink, options: Mapping[str, Any]) -> None:
        self._link = link
        self._sent = 0
        self._off = False  # True after an accepted `baseline`, until the next command
        self.on_traffic: TrafficFn | None = None  # set by the device manager (terminal, D-053)

    def baseline(self) -> None:
        first = self._sent == 0
        tp.check_baseline_reply(self._send(tp.BASELINE), first)
        self._off = True

    def set_tone(self, hz: float) -> str:
        command = tp.tone_command(hz)  # E202 before sending if outside 1-19
        reply = self._send(command)
        tp.check_tone_reply(int(hz), reply)
        assert reply is not None  # check_tone_reply raises E203 on no reply
        return reply

    def close(self) -> None:
        """Best-effort baseline, like the Teensy actuator (D-010). Never raises."""
        if self._off:
            return
        try:
            report(self.on_traffic, "tx", tp.BASELINE)
            self._link.command(tp.BASELINE)
        except Exception as e:
            log.warning("closing actuator: baseline not sent: %s", e)

    def _send(self, command: str) -> str | None:
        self._sent += 1
        self._off = False
        report(self.on_traffic, "tx", command)
        try:
            reply = self._link.command(command)
        except LinkLost as e:
            raise TestError("E209", str(e)) from e
        if reply is not None:
            report(self.on_traffic, "rx", reply)
        return reply
