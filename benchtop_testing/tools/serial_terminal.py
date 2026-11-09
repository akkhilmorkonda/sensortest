"""Interactive serial terminal for the sensor Teensy.

Type a device command (for example `baseline` or `response:10`) and press
Enter to send it. Replies from the device always print. Data lines arrive at
150/s, so they are hidden by default; use :data to show them.

Terminal commands (start with ':'):
  :help                  show this help
  :data off|on|N         hide data lines (default), show all, or show every Nth
  :stats                 per-channel mean and peak-to-peak, and line rate, over the last second
  :eol lf|crlf|cr|none   terminator added to sent commands (default lf; see D-007)
  :raw TEXT              send TEXT exactly, escapes allowed (e.g. :raw baseline\\r\\n)
  :log PATH | :log off   append everything sent and received to a file
  :quit                  exit (sends `baseline` first unless started with --leave-state)

Firmware safety: the port is never opened at 134 baud, which would start the
Teensy bootloader.

Usage:
  python benchtop_testing/tools/serial_terminal.py              # auto-detect the Teensy
  python benchtop_testing/tools/serial_terminal.py --port COM4
"""

from __future__ import annotations

import argparse
import codecs
import sys
import threading
import time
from collections import deque

import serial
from serial.tools import list_ports

FORBIDDEN_BAUD = 134
TEENSY_VID_PID = (0x16C0, 0x0483)
EOLS = {"lf": "\n", "crlf": "\r\n", "cr": "\r", "none": ""}


def find_teensy() -> str | None:
    for p in list_ports.comports():
        if (p.vid, p.pid) == TEENSY_VID_PID:
            return p.device
    return None


def classify(line: bytes):
    """Return ('data', [8 floats]) | ('ack', float) | ('msg', text)."""
    text = line.decode("ascii", errors="replace").strip()
    fields = [f.strip() for f in text.split(",")]
    if fields and fields[-1] == "":
        fields = fields[:-1]
    try:
        values = [float(f) for f in fields]
    except ValueError:
        return "msg", text
    if len(values) == 8:
        return "data", values
    if len(values) == 1:
        return "ack", values[0]
    return "msg", text


class Terminal:
    def __init__(self, ser: serial.Serial):
        self.ser = ser
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.eol = "lf"
        self.data_every = 0          # 0 = hidden, 1 = all, N = every Nth
        self.data_count = 0
        self.recent: deque[tuple[float, list[float]]] = deque(maxlen=600)
        self.log = None

    # -- output -------------------------------------------------------------
    def out(self, text: str) -> None:
        with self.lock:
            print(text, flush=True)

    def write_log(self, direction: str, payload: bytes) -> None:
        if self.log:
            self.log.write(f"{time.time():.6f}\t{direction}\t{payload!r}\n")
            self.log.flush()

    # -- receiving ----------------------------------------------------------
    def reader(self) -> None:
        while not self.stop.is_set():
            try:
                line = self.ser.readline()
            except serial.SerialException as e:
                self.out(f"!! serial error: {e} (device unplugged?)")
                self.stop.set()
                return
            if not line:
                continue
            self.write_log("rx", line)
            kind, value = classify(line)
            if kind == "data":
                self.recent.append((time.perf_counter(), value))
                self.data_count += 1
                if self.data_every and self.data_count % self.data_every == 0:
                    self.out("   " + " ".join(f"{v:9.2f}" for v in value))
            elif kind == "ack":
                self.out(f"<< ack  {value:g} Hz now in effect")
            else:
                self.out(f"<< msg  {value}")

    # -- sending ------------------------------------------------------------
    def send(self, payload: bytes) -> None:
        self.ser.write(payload)
        self.ser.flush()
        self.write_log("tx", payload)
        self.out(f">> {payload!r}")

    # -- terminal commands --------------------------------------------------
    def stats(self) -> None:
        now = time.perf_counter()
        window = [v for t, v in self.recent if now - t <= 1.0]
        if len(window) < 2:
            self.out("   (not enough data in the last second)")
            return
        cols = list(zip(*window))
        self.out(f"   last 1 s: {len(window)} lines/s")
        self.out("   ch " + " ".join(f"{i:>9}" for i in range(1, 9)))
        self.out("   mean" + " ".join(f"{sum(c) / len(c):9.1f}" for c in cols))
        self.out("   p2p " + " ".join(f"{max(c) - min(c):9.1f}" for c in cols))

    def local(self, cmd: str) -> bool:
        """Handle a ':' command. Returns False to quit."""
        name, _, arg = cmd[1:].partition(" ")
        arg = arg.strip()
        if name in ("q", "quit", "exit"):
            return False
        if name == "help":
            self.out(__doc__.split("Firmware safety")[0].strip())
        elif name == "data":
            if arg == "on":
                self.data_every = 1
            elif arg == "off":
                self.data_every = 0
            elif arg.isdigit() and int(arg) > 0:
                self.data_every = int(arg)
            else:
                self.out("   usage: :data off|on|N")
                return True
            self.out(f"   data lines: {'hidden' if not self.data_every else f'every {self.data_every}'}")
        elif name == "stats":
            self.stats()
        elif name == "eol":
            if arg in EOLS:
                self.eol = arg
                self.out(f"   terminator: {arg} ({EOLS[arg]!r})")
            else:
                self.out(f"   usage: :eol {'|'.join(EOLS)}   (now {self.eol})")
        elif name == "raw":
            self.send(codecs.decode(arg, "unicode_escape").encode("latin-1"))
        elif name == "log":
            if self.log:
                self.log.close()
                self.log = None
            if arg and arg != "off":
                self.log = open(arg, "a", encoding="utf-8")
                self.out(f"   logging to {arg}")
            else:
                self.out("   logging off")
        else:
            self.out(f"   unknown command :{name}; try :help")
        return True

    # -- main loop ----------------------------------------------------------
    def run(self) -> None:
        threading.Thread(target=self.reader, daemon=True).start()
        while not self.stop.is_set():
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith(":"):
                if not self.local(line):
                    break
            else:
                self.send((line + EOLS[self.eol]).encode("ascii", errors="replace"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="serial port (default: auto-detect the Teensy by USB ID)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--leave-state", action="store_true",
                   help="do not send `baseline` on exit (leaves any tone running)")
    a = p.parse_args()

    if a.baud == FORBIDDEN_BAUD:
        sys.exit("Refusing to open at 134 baud: it puts the Teensy into its bootloader.")
    port = a.port or find_teensy()
    if not port:
        ports = ", ".join(p.device for p in list_ports.comports()) or "none"
        sys.exit(f"No Teensy (USB {TEENSY_VID_PID[0]:04X}:{TEENSY_VID_PID[1]:04X}) found. Ports: {ports}. Use --port.")

    with serial.Serial(port, baudrate=a.baud, timeout=0.1) as ser:
        term = Terminal(ser)
        term.out(f"Connected to {port}. Type a command (e.g. response:10), :help for options, :quit to exit.")
        try:
            term.run()
        finally:
            if not a.leave_state and not term.stop.is_set():
                term.send(b"baseline\n")
                time.sleep(0.05)
            term.stop.set()
            if term.log:
                term.log.close()
    print("Disconnected.")


if __name__ == "__main__":
    main()
