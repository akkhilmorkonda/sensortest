"""The Teensy's line protocol (docs/device_protocol.md; D-007, D-009).

Shared by the Teensy actuator and sensor drivers, and by the replay pair, which plays back the
same device. The one place that knows the command text, the reply text and the line format.

Telling lines apart on the shared port: a data line has 8 comma-separated values (with a trailing
comma); a reply (``N.00``, ``response frequency invalid``, ``command not recognized``) has no
comma. So the sensor driver takes the lines with a comma and the actuator driver the rest.
"""

from sensortest.core.errors import TestError

VALID_TONES = range(1, 20)  # the firmware accepts 1-19 Hz; the brief's 20-25 are rejected (D-009)
BASELINE = "baseline"
TERMINATOR = b"\n"  # LF only: with CR the firmware does not recognize `baseline` (D-007)
REJECT_FREQ = "response frequency invalid"
REJECT_CMD = "command not recognized"
CHANNELS = 8


def tone_command(hz: float) -> str:
    """``response:N`` for a valid tone. Anything outside 1-19 Hz is refused before sending (E202)."""
    f = int(hz)
    if f != hz or f not in VALID_TONES:
        raise TestError("E202", f"response:{hz:g} refused before sending (valid 1-19 Hz)", where=f"T{hz:g}")
    return f"response:{f}"


def encode(command: str) -> bytes:
    return command.encode("ascii") + TERMINATOR


def is_data_line(line: bytes) -> bool:
    return b"," in line


def is_reply_line(line: bytes) -> bool:
    return b"," not in line


def parse_data(line: bytes) -> list[float] | None:
    """The 8 values of a data line, or None if it doesn't parse."""
    fields = [f.strip() for f in line.decode("ascii", "replace").strip().split(",")]
    if fields and fields[-1] == "":
        fields.pop()  # trailing comma
    if len(fields) != CHANNELS:
        return None
    try:
        return [float(f) for f in fields]
    except ValueError:
        return None


def check_tone_reply(f: int, reply: str | None) -> None:
    """Accept only ``f.00`` (TP-001 1900: E201 wrong value, E202/E204 rejected, E203 none)."""
    where = f"T{f}"
    if reply is None:
        raise TestError("E203", f"no reply to response:{f}", where=where)
    if reply == REJECT_FREQ:
        raise TestError("E202", f"response:{f}: {REJECT_FREQ}", where=where)
    if reply == REJECT_CMD:
        raise TestError("E204", f"response:{f}: {REJECT_CMD}", where=where)
    try:
        value = float(reply)
    except ValueError:
        raise TestError("E201", f"reply {reply!r} to response:{f}, expected {f}.00", where=where) from None
    if value != f:
        raise TestError("E201", f"reply {reply}, expected {f}.00", where=where)


def check_baseline_reply(reply: str | None, first: bool) -> None:
    """`baseline` gets no reply. A rejection is E103 at connect (first command), E204 later."""
    if reply is None:
        return
    if reply == REJECT_CMD:
        raise TestError("E103" if first else "E204", f"baseline: {REJECT_CMD}")
    raise TestError("E201", f"unexpected reply {reply!r} to baseline (expected none)")
