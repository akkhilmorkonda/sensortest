"""Invariants of the transition table (PRD §7.3, D-037–D-039)."""

import pytest

from sensortest.engine.states import TRANSITIONS, AppState, IllegalTransition, Trigger, allowed, next_state


def test_every_exit_from_running_goes_through_safe_state_or_completes() -> None:
    exits = {t: s for (src, t), s in TRANSITIONS.items() if src is AppState.RUNNING}
    assert exits == {
        Trigger.RUN_DONE: AppState.RESULT,
        Trigger.FAULT: AppState.SAFE_STATE,
        Trigger.ABORT: AppState.SAFE_STATE,
    }


def test_safe_state_always_leads_to_a_recorded_result() -> None:
    assert allowed(AppState.SAFE_STATE) == {Trigger.SAFE_DONE}
    assert next_state(AppState.SAFE_STATE, Trigger.SAFE_DONE) is AppState.RESULT


def test_start_only_from_idle_and_retry_only_from_result() -> None:
    assert {s for (s, t) in TRANSITIONS if t is Trigger.START} == {AppState.IDLE}
    assert {s for (s, t) in TRANSITIONS if t is Trigger.RETRY} == {AppState.RESULT}


def test_result_needs_next_before_another_start() -> None:
    assert Trigger.START not in allowed(AppState.RESULT)
    assert next_state(AppState.RESULT, Trigger.NEXT) is AppState.IDLE


def test_quit_never_leaves_running_directly() -> None:
    assert (AppState.RUNNING, Trigger.QUIT) not in TRANSITIONS


def test_every_state_reachable_from_starting() -> None:
    seen, frontier = {AppState.STARTING}, [AppState.STARTING]
    while frontier:
        s = frontier.pop()
        for (src, _), dst in TRANSITIONS.items():
            if src is s and dst not in seen:
                seen.add(dst)
                frontier.append(dst)
    assert seen == set(AppState)


def test_illegal_transition_raises() -> None:
    with pytest.raises(IllegalTransition):
        next_state(AppState.IDLE, Trigger.NEXT)
