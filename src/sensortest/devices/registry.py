"""The device plugin points (D-033, D-049): links, actuator drivers and sensor drivers.

A **link** owns one physical connection: a serial port, or a recording. The device manager opens
each link once per run and closes it at the end, so a port always has exactly one owner.

An **actuator driver** (``@actuator``) implements the Stimulus role, and a **sensor driver**
(``@sensor``) implements the SensorStream role. Each attaches to a link named in the settings.
On this fixture both roles share the Teensy's one USB port, so both drivers attach to the same
link. A fixture with a separate shaker and DAQ would give each driver its own link; the drivers
would not change.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from sensortest.core.ports import SensorStream, Stimulus
from sensortest.core.registry import Registry


@dataclass(frozen=True, slots=True)
class LinkInfo:
    """What discovery knows about a connection, without opening it."""

    type: str  # link registry name, e.g. "serial"
    port: str  # e.g. "COM4"
    serial: str  # USB serial number, or a recording label


class Link(Protocol):
    """One physical connection, opened once per run and shared by the drivers attached to it."""

    info: LinkInfo

    @classmethod
    def discover(cls, options: Mapping[str, Any]) -> list[LinkInfo]:
        """List matching connections (USB enumeration for hardware). Must not open a port (D-037)."""
        ...

    @classmethod
    def open(cls, info: LinkInfo, options: Mapping[str, Any]) -> "Link":
        """Open the connection. Raises TestError (E102 won't open, E104 no data)."""
        ...

    def close(self) -> None:
        """Release the connection. Must be safe to call twice."""
        ...


class ActuatorDriver(Stimulus, Protocol):
    """The actuator role on a link. ``baseline()`` / ``set_tone()`` return only once accepted."""

    link_types: ClassVar[frozenset[str]]  # the link types this driver can attach to

    def __init__(self, link: Any, options: Mapping[str, Any]) -> None: ...

    def close(self) -> None:
        """Leave the actuator safe (best-effort baseline, D-010). Never raises."""
        ...


class SensorDriver(SensorStream, Protocol):
    """The sensor role on a link: line-counted windows with integrity checks."""

    link_types: ClassVar[frozenset[str]]

    def __init__(self, link: Any, options: Mapping[str, Any]) -> None: ...

    def close(self) -> None: ...


LINKS: Registry[type[Link]] = Registry("link")
ACTUATORS: Registry[type[ActuatorDriver]] = Registry("actuator driver")
SENSOR_DRIVERS: Registry[type[SensorDriver]] = Registry("sensor driver")

link = LINKS.register
actuator = ACTUATORS.register
sensor = SENSOR_DRIVERS.register
