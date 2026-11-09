"""The state machines as data (PRD §7.3, D-037–D-039).

The transition table is the single source of truth: the engine looks transitions up here, the
UI enables actions from it, and tests check its invariants (e.g. every exit from RUNNING goes
through SAFE_STATE). Changing behaviour means editing this table, not scattered if-statements.
"""

from enum import StrEnum


class AppState(StrEnum):
    STARTING = "Starting"
    NOT_READY = "Not ready"
    IDLE = "Idle"
    RUNNING = "Running"
    SAFE_STATE = "Safe state"
    RESULT = "Result"
    EXITING = "Exiting"


class Trigger(StrEnum):
    FILES_VALID = "files valid"
    FILES_INVALID = "files invalid"  # E301 / E302
    RELOAD = "Reload"
    START = "Start"  # guard: every required role connected (D-037)
    RUN_DONE = "run done"  # all captures + end baseline OK -> PASS / FAIL
    FAULT = "fault"  # any error code, incl. device lost (E209)
    ABORT = "Abort"
    SAFE_DONE = "safe state done"  # -> Result (ERROR or ABORTED)
    NEXT = "Next"  # D-039
    RETRY = "Retry"  # guard: retryable ERROR, attempt 1, roles connected (D-026)
    QUIT = "Quit"


class CapturePhase(StrEnum):
    """(c) The capture loop, run once per capture in the plan."""

    SEND = "Send command"
    AWAIT_REPLY = "Await reply"
    SETTLE = "Settle"
    ACQUIRE = "Acquire"
    EVALUATE = "Evaluate"


S, T = AppState, Trigger

TRANSITIONS: dict[tuple[AppState, Trigger], AppState] = {
    (S.STARTING, T.FILES_VALID): S.IDLE,
    (S.STARTING, T.FILES_INVALID): S.NOT_READY,
    (S.NOT_READY, T.RELOAD): S.STARTING,
    (S.IDLE, T.START): S.RUNNING,
    (S.RUNNING, T.RUN_DONE): S.RESULT,
    (S.RUNNING, T.FAULT): S.SAFE_STATE,
    (S.RUNNING, T.ABORT): S.SAFE_STATE,
    (S.SAFE_STATE, T.SAFE_DONE): S.RESULT,
    (S.RESULT, T.NEXT): S.IDLE,
    (S.RESULT, T.RETRY): S.RUNNING,
    (S.STARTING, T.QUIT): S.EXITING,
    (S.NOT_READY, T.QUIT): S.EXITING,
    (S.IDLE, T.QUIT): S.EXITING,
    (S.RESULT, T.QUIT): S.EXITING,
    # Quit while RUNNING is not a transition: the engine converts it to ABORT first.
}


class IllegalTransition(RuntimeError):
    pass


def next_state(state: AppState, trigger: Trigger) -> AppState:
    """Look up a transition; anything not in the table is a programming error."""
    try:
        return TRANSITIONS[(state, trigger)]
    except KeyError:
        raise IllegalTransition(f"{trigger!s} is not allowed in {state!s}") from None


def allowed(state: AppState) -> frozenset[Trigger]:
    """Triggers valid in a state (the UI uses this to enable buttons)."""
    return frozenset(t for (s, t) in TRANSITIONS if s is state)
