"""The sink-writer thread (D-043): writes every completed run to every sink, off the engine thread.

Each write is retried with backoff (settings). If a sink still fails, the run goes to a fallback
CSV folder and W201 is raised; a record is never lost and a slow disk never stalls a run.
"""

import logging
import queue
import threading
import time
from collections.abc import Sequence

from sensortest.core.models import RunRecord
from sensortest.core.ports import ResultSink
from sensortest.events.bus import EventBus
from sensortest.events.messages import Event, RunCompleted, WarningRaised
from sensortest.sinks.csv_sink import CsvSink

log = logging.getLogger(__name__)
_STOP = object()


class SinkWriter:
    def __init__(self, bus: EventBus, sinks: Sequence[ResultSink], fallback_dir, retries_s=(0.2, 0.4, 0.8)):
        self._bus = bus
        self._sinks = list(sinks)
        self._fallback = CsvSink({"data_dir": fallback_dir})
        self._retries = tuple(retries_s)
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        bus.subscribe(RunCompleted, self._enqueue)

    def _enqueue(self, event: Event) -> None:
        assert isinstance(event, RunCompleted)
        self._queue.put(event.record)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="sink-writer", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 10.0) -> None:
        """Flush everything queued, then stop."""
        self._queue.put(_STOP)
        if self._thread:
            self._thread.join(timeout_s)

    def _loop(self) -> None:
        while (item := self._queue.get()) is not _STOP:
            for s in self._sinks:
                self._write(s, item)

    def _write(self, s: ResultSink, record: RunRecord) -> None:
        for delay in (0.0, *self._retries):
            time.sleep(delay)
            try:
                s.write_run(record)
                return
            except Exception as e:
                log.warning("sink %s failed for %s: %s", s.name, record.run_id, e)
        try:
            self._fallback.write_run(record)
            detail = f"{s.name} failed; run {record.run_id} saved to {self._fallback.root}"
        except Exception:
            log.exception("fallback write failed for %s", record.run_id)
            detail = f"{s.name} and the fallback failed for run {record.run_id}"
        self._bus.publish(WarningRaised("W201", detail))
