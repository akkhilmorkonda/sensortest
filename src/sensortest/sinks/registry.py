"""The sink plugin point (D-033). A sink class registers with ``@sink("csv")``."""

from collections.abc import Callable, Mapping

from sensortest.core.ports import ResultSink
from sensortest.core.registry import Registry

SinkFactory = Callable[[Mapping[str, object]], ResultSink]  # built from its settings section

SINKS: Registry[SinkFactory] = Registry("sink")
sink = SINKS.register
