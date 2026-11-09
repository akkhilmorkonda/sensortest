"""Raw serial capture for hardware bring-up (roadmap section 02).

Records every line the device sends, with a host timestamp, so protocol
questions can be answered from saved files rather than from memory.

Firmware safety:
  * A Teensy enters its bootloader when its USB serial port is opened at
    134 baud. This tool refuses that baud rate outright.
  * Nothing is written to the device except the commands passed with --send.

Usage:
  python benchtop_testing/tools/bringup_capture.py --port COM4 --seconds 5 --label passive
  python benchtop_testing/tools/bringup_capture.py --port COM4 --seconds 8 --send "baseline" --label baseline
  python benchtop_testing/tools/bringup_capture.py --port COM4 --seconds 8 --send "response:10" --eol "\\n" --label r10
  python benchtop_testing/tools/bringup_capture.py --port COM4 --seconds 10 --script "2=response:10;6=baseline" --label onoff
"""

from __future__ import annotations

import argparse
import codecs
import json
import time
from datetime import datetime
from pathlib import Path

import serial

BENCH = Path(__file__).resolve().parents[1]  # benchtop_testing/; defaults work from any cwd
FORBIDDEN_BAUD = 134  # opens the Teensy bootloader; never use


def capture(port: str, baud: int, seconds: float, send: str | None, eol: str,
            send_after: float, out_dir: Path, label: str,
            script: list[tuple[float, str]] | None = None) -> Path:
    if baud == FORBIDDEN_BAUD:
        raise SystemExit("Refusing to open at 134 baud: it puts the Teensy into its bootloader.")

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = out_dir / f"{stamp}_{label}"

    lines: list[tuple[float, bytes]] = []
    raw = bytearray()
    pending = sorted(script or [])
    if send is not None:
        pending = sorted(pending + [(send_after, send)])
    sent: list[dict] = []

    with serial.Serial(port, baudrate=baud, timeout=0.05) as ser:
        ser.reset_input_buffer()
        t0 = time.perf_counter()
        buf = b""
        while (now := time.perf_counter() - t0) < seconds:
            while pending and now >= pending[0][0]:
                _, cmd = pending.pop(0)
                ser.write(cmd.encode("ascii") + eol.encode("ascii"))
                ser.flush()
                sent.append({"t_s": now, "cmd": cmd})
            chunk = ser.read(ser.in_waiting or 1)
            if not chunk:
                continue
            raw += chunk
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                lines.append((time.perf_counter() - t0, line + b"\n"))
        if buf:
            lines.append((time.perf_counter() - t0, buf))

    base.with_suffix(".bin").write_bytes(bytes(raw))
    with base.with_suffix(".tsv").open("w", encoding="utf-8") as f:
        f.write("t_host_s\tline_repr\n")
        for t, line in lines:
            f.write(f"{t:.6f}\t{line!r}\n")
    meta = {
        "port": port, "baud": baud, "seconds": seconds, "label": label,
        "eol": eol, "sent": sent,
        "bytes": len(raw), "lines": len(lines), "started": stamp,
    }
    base.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    return base


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", required=True)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--send", default=None, help="ASCII command to send once")
    p.add_argument("--eol", default="\\n", help='terminator appended to --send, e.g. "\\n", "\\r\\n" or ""')
    p.add_argument("--send-after", type=float, default=1.0, help="seconds of listening before sending")
    p.add_argument("--script", default=None,
                   help='timed commands, e.g. "2=response:10;6=baseline" (seconds=command)')
    p.add_argument("--out", type=Path, default=BENCH / "01_bringup")
    p.add_argument("--label", default="capture")
    a = p.parse_args()
    eol = codecs.decode(a.eol, "unicode_escape")
    script = None
    if a.script:
        script = [(float(t), cmd) for t, cmd in (item.split("=", 1) for item in a.script.split(";") if item)]
    base = capture(a.port, a.baud, a.seconds, a.send, eol, a.send_after, a.out, a.label, script)
    print(f"saved {base}.bin / .tsv / .json")


if __name__ == "__main__":
    main()
