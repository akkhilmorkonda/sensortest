"""Built-in buttons (D-033). A new button is one more ACTIONS.register(...) here, nothing else.

Each button's enabled state comes from the engine's transition table plus its guards, so a
button can never send a command the state machine would reject. One screen, both modes (D-053):
the control row holds Start / Abort / Next; Retry sits in the result banner beside the error it
retries, and Reload (test mode) beside the file error that made the station Not ready.
"""

from sensortest.engine.states import Trigger
from sensortest.events.messages import Abort, Next, Reload, Retry, StartRun
from sensortest.ui.registry import ACTIONS, ActionDef, Placement, View

ROW, BANNER = Placement.CONTROL_ROW, Placement.RESULT_BANNER

ACTIONS.register("start")(
    ActionDef(
        "Start", lambda c: StartRun(c.sequence, c.serial), Trigger.START, ROW, shortcut="Space", order=10
    )
)
ACTIONS.register("abort")(
    ActionDef("Abort", lambda _c: Abort(), Trigger.ABORT, ROW, shortcut="Esc", order=20)
)
ACTIONS.register("next")(ActionDef("Next", lambda _c: Next(), Trigger.NEXT, ROW, shortcut="N", order=30))
ACTIONS.register("retry")(ActionDef("Retry", lambda _c: Retry(), Trigger.RETRY, BANNER, order=40))
ACTIONS.register("reload")(
    ActionDef("Reload files", lambda _c: Reload(), Trigger.RELOAD, BANNER, order=50, view=View.TEST)
)
