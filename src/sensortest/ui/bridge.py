"""The only door between the engine's threads and Qt (D-043).

Bus subscribers run on whichever thread published; this bridge re-emits every event as a Qt
signal, which Qt delivers on the UI thread (queued connection). Widgets connect to `event`.
"""

from PyQt6.QtCore import QObject, pyqtSignal

from sensortest.events.bus import EventBus
from sensortest.events.messages import Event


class QtBridge(QObject):
    event = pyqtSignal(object)

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        bus.subscribe(Event, self.event.emit)
