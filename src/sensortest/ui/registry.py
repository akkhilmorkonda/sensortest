"""UI plugin points (D-033): buttons (@action) and panels (@panel).

Adding a button = register an ActionDef + choose a placement. Its enabled state comes from the
engine's transition table, so a button can never fire a command the state machine would reject.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from sensortest.core.registry import Registry
from sensortest.engine.states import Trigger
from sensortest.events.messages import Command


class Placement(StrEnum):
    """Where a button goes on the one station screen (D-053)."""

    CONTROL_ROW = "control.row"  # beside the DUT serial: Start / Abort / Next
    RESULT_BANNER = "result.banner"  # in the banner, beside the error it acts on: Retry, Reload


@dataclass(frozen=True, slots=True)
class ActionContext:
    """What a button's command may use from the view that holds it."""

    sequence: str  # the active sequence (operator mode: always the operator sequence, PROD)
    serial: str  # the DUT serial entered for this run (D-047)


class View(StrEnum):
    """The two modes of the one screen (D-053): test mode is the engineering view."""

    OPERATOR = "operator"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class ActionDef:
    label: str
    command: Callable[[ActionContext], Command]  # e.g. lambda c: StartRun(c.sequence, c.serial)
    trigger: Trigger | None  # enabled only when this trigger is allowed in the current state
    placement: Placement
    icon: str = ""
    shortcut: str = ""
    order: int = 100  # left to right within its placement
    view: View | None = None  # None = both modes; View.TEST = test mode only


@dataclass(frozen=True, slots=True)
class PanelDef:
    """An extra panel: a tab beside the serial terminal, shown in its view (test mode, D-053)."""

    title: str
    factory: Callable[..., object]  # builds the QWidget; receives the event bridge
    view: View
    order: int = 100


ACTIONS: Registry[ActionDef] = Registry("UI action")
PANELS: Registry[PanelDef] = Registry("UI panel")
