"""In-process publish/subscribe (PRD §7.2, D-043).

publish() calls subscribers synchronously on the publisher's thread, so subscribers must only
enqueue (the Qt bridge re-emits as a queued signal; sinks push to their own queue). A failing
subscriber is isolated and reported; it never breaks the publisher or other subscribers.
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable

from sensortest.events.messages import Event

log = logging.getLogger(__name__)

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[type[Event], list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, event_type: type[Event], handler: Handler) -> None:
        """Subscribe to one event type and its subclasses (use Event for everything)."""
        with self._lock:
            self._subs[event_type].append(handler)

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = [h for t, hs in self._subs.items() if isinstance(event, t) for h in hs]
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                log.exception("subscriber %r failed on %s", handler, type(event).__name__)
