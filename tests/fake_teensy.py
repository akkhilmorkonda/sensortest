"""An in-memory Teensy, for testing the Teensy drivers without hardware (D-049).

It implements the slice of pyserial's ``Serial`` that SerialLink uses, and speaks the protocol in
docs/device_protocol.md: data lines at ``rate`` per second of host time (offset + uniform noise +
one shared tone), ``N.00`` replies, and the firmware's rejections. Knobs make it misbehave.
"""

import math
import random
import threading
import time
from dataclasses import dataclass

import serial


@dataclass
class PortInfo:
    """What serial.tools.list_ports.comports() returns, reduced to the fields discovery reads."""

    device: str
    vid: int | None
    pid: int | None
    serial_number: str | None


TEENSY_PORT = PortInfo("COM4", 0x16C0, 0x0483, "20071290")
OTHER_PORT = PortInfo("COM3", 0x2341, 0x0043, "ARDUINO1")


class FakeTeensy:
    def __init__(
        self,
        rate: float = 1500.0,
        replies: str = "normal",  # normal | wrong_ack | no_reply | reject_baseline
        amplitude: float = 1000.0,
        offset: float = 100.0,
        noise: float = 20.0,
    ) -> None:
        self.rate, self.replies = rate, replies
        self.amplitude, self.offset, self.noise = amplitude, offset, noise
        self._rng = random.Random(1)
        self._lock = threading.Lock()
        self._out = bytearray(b"-32.83, 10.85, 189.32, \r\n")  # the port opens mid-line
        self._t0 = time.perf_counter()
        self._emitted = 0
        self.tone: int | None = None
        self.sent: list[bytes] = []
        self.streaming = True  # False = the device stops sending data (stall)
        self.unplugged = False
        self.closed = False

    # ---------------------------------------------------------------- pyserial surface ----
    def reset_input_buffer(self) -> None:
        self._emitted = int((time.perf_counter() - self._t0) * self.rate)  # drop the backlog

    @property
    def in_waiting(self) -> int:
        self._check()
        self._produce()
        return len(self._out)

    def read(self, n: int = 1) -> bytes:
        self._check()
        self._produce()
        if not self._out:
            time.sleep(min(0.05, 1 / self.rate))
            self._produce()
        with self._lock:
            chunk = bytes(self._out[:n])
            del self._out[:n]
        return chunk

    def write(self, data: bytes) -> int:
        self._check()
        self.sent.append(bytes(data))
        reply = self._respond(bytes(data).removesuffix(b"\n").decode("ascii"))
        with self._lock:
            self._out += reply
        return len(data)

    def flush(self) -> None:
        self._check()

    def close(self) -> None:
        self.closed = True

    # ---------------------------------------------------------------- test controls ----
    def inject(self, raw: bytes) -> None:
        """Put raw bytes in the stream, e.g. an unreadable line."""
        self._produce()
        with self._lock:
            self._out += raw

    # ---------------------------------------------------------------- the device ----
    def _check(self) -> None:
        if self.unplugged:
            raise serial.SerialException("ClearCommError failed (device disconnected)")

    def _respond(self, cmd: str) -> bytes:
        if cmd == "baseline":
            if self.replies == "reject_baseline":
                return b"command not recognized\r\n"
            self.tone = None
            return b""
        if cmd.startswith("response:"):
            n = int(cmd.removeprefix("response:"))
            if not 1 <= n <= 19:
                return f"response frequency invalid\r\n{self.tone or 0}.00\r\n".encode()
            self.tone = n
            if self.replies == "no_reply":
                return b""
            return b"19.00\r\n" if self.replies == "wrong_ack" else f"{n}.00\r\n".encode()
        return b"command not recognized\r\n"

    def _produce(self) -> None:
        if not self.streaming:
            return
        due = int((time.perf_counter() - self._t0) * self.rate)
        with self._lock:
            while self._emitted < due:
                t = self._emitted / self.rate
                tone = self.amplitude * math.sin(2 * math.pi * self.tone * t) if self.tone else 0.0
                values = [self.offset + self._rng.uniform(-self.noise, self.noise) + tone for _ in range(8)]
                self._out += ("".join(f"{v:.2f}, " for v in values) + "\r\n").encode()
                self._emitted += 1
