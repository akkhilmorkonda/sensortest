"""Serial link (D-049): one line-based serial port with exactly one owner.

It opens the port once per run, runs one reader thread that splits the byte stream into lines and
stamps each line with the host time, and gives every line to each subscriber whose filter accepts
it. Writes go through one lock. It knows nothing about the device protocol; the drivers attached
to it do. That is what lets the actuator driver and the sensor driver share the Teensy's one port.

Port ownership: the device manager opens each link once per run and both drivers use that one
object, so nothing in the app opens the port twice. Windows also gives a COM port to one process
at a time. A production fixture would add a real device lock on top: each port held by exactly one
owner across processes (exclusive open on Linux/macOS plus a station-level lock), and each
instrument on its own port, so two drivers never share one (noted in D-049, not built).

Firmware safety: 134 baud starts a Teensy's bootloader, so it is refused outright.
"""

import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import serial
from serial.tools import list_ports

from sensortest.core.errors import TestError
from sensortest.devices.linequeue import LineQueue
from sensortest.devices.registry import LinkInfo, link

log = logging.getLogger(__name__)
FORBIDDEN_BAUD = 134  # opens the Teensy bootloader; never used (a project constraint)
READ_TIMEOUT_S = 0.05  # reader-thread poll period: bounds how fast close() and a lost device are seen


def _usb_id(text: str) -> tuple[int, int]:
    vid, pid = text.split(":")
    return int(vid, 16), int(pid, 16)


@link("serial")
class SerialLink:
    def __init__(self, info: LinkInfo, port: Any) -> None:
        self.info = info
        self._port = port
        self._subs: list[tuple[Callable[[bytes], bool], LineQueue]] = []
        self._subs_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()  # set by the first complete line, or by losing the device
        self.lost = ""  # why the device was lost; empty while it is fine
        self.unclaimed = 0  # complete lines that no driver wanted
        self._thread = threading.Thread(target=self._read_loop, name=f"link-{info.port}", daemon=True)

    # ---------------------------------------------------------------- discovery ----
    @classmethod
    def discover(cls, options: Mapping[str, Any]) -> list[LinkInfo]:
        """USB enumeration only; never opens a port (D-037)."""
        want_port = str(options.get("port", "auto"))
        want_id = _usb_id(str(options["usb_id"])) if options.get("usb_id") else None
        found = []
        for p in list_ports.comports():
            if want_port != "auto" and p.device != want_port:
                continue
            if want_id and (p.vid, p.pid) != want_id:
                continue
            found.append(LinkInfo("serial", p.device, p.serial_number or ""))
        return found

    # ---------------------------------------------------------------- open / close ----
    @classmethod
    def open(cls, info: LinkInfo, options: Mapping[str, Any]) -> "SerialLink":
        baud = int(options.get("baud", 115200))
        if baud == FORBIDDEN_BAUD:
            raise TestError("E301", f"{info.port}: {FORBIDDEN_BAUD} baud is refused (Teensy bootloader)")
        delays = [0.0, *(float(d) for d in options.get("open_retry_backoff_s", (0.5, 1.0)))]
        last: Exception | None = None
        for attempt, delay in enumerate(delays, 1):
            time.sleep(delay)
            try:
                port = serial.Serial(info.port, baudrate=baud, timeout=READ_TIMEOUT_S, write_timeout=0.5)
                break
            except (serial.SerialException, OSError) as e:
                last = e
                log.warning("%s: open attempt %d of %d failed: %s", info.port, attempt, len(delays), e)
        else:
            raise TestError("E102", f"{info.port}: {last} ({len(delays)} attempts)")
        opened = cls(info, port)
        opened._start()
        wait = float(options.get("first_line_timeout_s", 2.0))
        if not opened._ready.wait(wait) or opened.lost:
            reason = opened.lost or f"no complete line within {wait:g} s of opening"
            opened.close()
            raise TestError("E104", f"{info.port}: {reason}")
        log.info("%s opened (USB serial %s, %d baud)", info.port, info.serial, baud)
        return opened

    def _start(self) -> None:
        self._port.reset_input_buffer()
        self._thread.start()

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(1.0)
        try:
            self._port.close()
        except Exception:
            log.exception("%s: close failed", self.info.port)
        self._wake_all()
        if self.unclaimed:
            log.warning("%s: %d lines matched no driver", self.info.port, self.unclaimed)
        log.info("%s closed", self.info.port)

    # ---------------------------------------------------------------- driver API ----
    def subscribe(self, accept: Callable[[bytes], bool], maxlen: int = 4096) -> LineQueue:
        """A new queue that receives every complete line for which ``accept(line)`` is true."""
        q = LineQueue(maxlen)
        with self._subs_lock:
            self._subs.append((accept, q))
        return q

    def write(self, payload: bytes) -> None:
        """Send bytes. A device that can't be written to is lost (E209)."""
        if self.lost:
            raise TestError("E209", f"{self.info.port}: {self.lost}")
        with self._write_lock:
            try:
                self._port.write(payload)
                self._port.flush()
            except (serial.SerialException, OSError) as e:
                self._mark_lost(f"write failed: {e}")
                raise TestError("E209", f"{self.info.port}: write failed: {e}") from e
        log.info("%s tx %r", self.info.port, payload)

    # ---------------------------------------------------------------- reader thread ----
    def _read_loop(self) -> None:
        buf = b""
        synced = False  # the first bytes may start mid-line: drop everything up to the first b"\n"
        while not self._stop.is_set():
            try:
                chunk = self._port.read(self._port.in_waiting or 1)
            except (serial.SerialException, OSError, TypeError, AttributeError) as e:
                # pyserial can raise TypeError/AttributeError when the port vanishes mid-read
                if not self._stop.is_set():
                    self._mark_lost(f"read failed: {e}")
                return
            if not chunk:
                continue
            now = time.perf_counter()
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not synced:
                    synced = True  # partial first line: discard
                    continue
                self._deliver(now, line + b"\n")

    def _deliver(self, t: float, line: bytes) -> None:
        self._ready.set()
        with self._subs_lock:
            subs = list(self._subs)
        if not subs:
            return  # before the drivers attach: nobody is listening yet, nothing is lost
        claimed = False
        for accept, q in subs:
            if accept(line):
                q.put(t, line)
                claimed = True
        if not claimed:
            self.unclaimed += 1

    def _mark_lost(self, reason: str) -> None:
        if not self.lost:
            self.lost = reason
            log.error("%s lost: %s", self.info.port, reason)
        self._ready.set()
        self._wake_all()

    def _wake_all(self) -> None:
        with self._subs_lock:
            for _, q in self._subs:
                q.wake()
