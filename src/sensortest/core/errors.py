"""Error and warning codes (TP-001 §9, D-023, D-036) and the exception that carries them.

Every external failure becomes a TestError with one of these codes. A code fixes whether the
operator may retry and what they are told to do; nothing else in the app decides that.
"""

from dataclasses import dataclass
from enum import StrEnum


class Action(StrEnum):
    RECONNECT_RETRY = "Reconnect the DUT, retry once"
    RETRY = "Retry once"
    CALL_ENGINEERING = "Call engineering"
    UNPLUG_DUT = "Unplug the DUT before the next one"
    CLOSE_FILE = "Close the file; tell engineering"
    NONE = "None"


@dataclass(frozen=True, slots=True)
class Code:
    code: str
    meaning: str
    retryable: bool
    action: Action

    @property
    def is_warning(self) -> bool:
        return self.code.startswith("W")


_E, _W = True, False  # readability in the table below: (retryable)

CODES: dict[str, Code] = {
    c.code: c
    for c in (
        # 1000 CONNECT
        Code("E101", "Device not found", _E, Action.RECONNECT_RETRY),
        Code("E102", "Port won't open (in use or busy)", _E, Action.RECONNECT_RETRY),
        Code("E103", "baseline rejected", _W, Action.CALL_ENGINEERING),
        Code("E104", "Line rate out of range", _W, Action.CALL_ENGINEERING),
        Code("E105", "Unreadable data in BL", _E, Action.RETRY),
        Code("E106", "Device not idle after baseline", _W, Action.CALL_ENGINEERING),
        # 1900 RUN_INTEGRITY
        Code("E201", "Wrong reply value", _W, Action.CALL_ENGINEERING),
        Code("E202", "response frequency invalid", _W, Action.CALL_ENGINEERING),
        Code("E203", "No reply within timeout", _W, Action.CALL_ENGINEERING),
        Code("E204", "command not recognized", _W, Action.CALL_ENGINEERING),
        Code("E205", "Dropped lines (line rate outside tolerance)", _E, Action.RETRY),
        Code("E206", "Unreadable lines", _E, Action.RETRY),
        # E207 (actuator didn't stop) was dropped by D-057 and is never reused.
        Code("E208", "Device stopped sending data", _W, Action.CALL_ENGINEERING),
        Code("E209", "Device lost mid-run", _E, Action.RECONNECT_RETRY),
        Code("E210", "Run watchdog exceeded", _W, Action.CALL_ENGINEERING),
        # E211 (stimulus discontinuity) was removed by D-058 and is never reused.
        # pre-run configuration
        Code("E301", "Test plan invalid", _W, Action.CALL_ENGINEERING),
        Code("E302", "Limit file missing or version mismatch", _W, Action.CALL_ENGINEERING),
        # software
        Code("E901", "Unexpected software error", _W, Action.CALL_ENGINEERING),
        # warnings: never change a verdict
        Code("W101", "Safe-state baseline failed on exit", _W, Action.UNPLUG_DUT),
        Code("W201", "Result file write failed; fallback CSV written", _W, Action.CLOSE_FILE),
        Code("W301", "Network upload failed; queued", _W, Action.NONE),
    )
}


class Aborted(Exception):
    """The operator pressed Abort; raised by a driver that sees the cancel event (D-038)."""


class TestError(Exception):
    """A failure with a catalogued code. ``where`` names the capture (e.g. "T10") when relevant."""

    __test__ = False  # not a pytest test class

    def __init__(self, code: str, detail: str = "", where: str | None = None) -> None:
        if code not in CODES:
            raise ValueError(f"uncatalogued error code {code!r}")
        self.code = CODES[code]
        self.detail = detail
        self.where = where
        super().__init__(self.label + (f": {detail}" if detail else ""))

    @property
    def label(self) -> str:
        """``E201@T10``-style label used in records and on the operator screen."""
        return f"{self.code.code}@{self.where}" if self.where else self.code.code
