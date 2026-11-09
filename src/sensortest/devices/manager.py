"""The device manager (D-037, D-049): knows at all times which roles are connected, and builds
the session for a run from one actuator driver and one sensor driver.

The fixture settings name the links and, for each role, a driver and the link it attaches to:

    links:    {usb: {type: serial, usb_id: "16C0:0483"}}
    actuator: {driver: teensy, link: usb, ...}
    sensor:   {driver: teensy, link: usb, ...}

A poller thread (every device_poll_s) runs each link's discover() (USB enumeration for hardware;
it never opens a port). A role is present when its link is found; on_change is called whenever
that changes. At run start, open_session() opens each link the roles use exactly once, attaches
both drivers, and returns them as one DeviceSession. Liveness is then checked by 1000 CONNECT.
Each driver that has an ``on_traffic`` hook gets ``on_traffic`` from here, for the terminal (D-053).
"""

import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any

from sensortest.core.errors import TestError
from sensortest.core.models import Role
from sensortest.core.ports import DeviceInfo
from sensortest.core.registry import RegistryError
from sensortest.devices.registry import (
    ACTUATORS,
    LINKS,
    SENSOR_DRIVERS,
    ActuatorDriver,
    Link,
    LinkInfo,
    SensorDriver,
)
from sensortest.devices.taps import TrafficFn

log = logging.getLogger(__name__)
ALL_ROLES = (Role.ACTUATOR, Role.SENSOR)
_ROLE_KEY = {Role.ACTUATOR: "actuator", Role.SENSOR: "sensor"}


class _Session:
    """One run's devices: the two role drivers and the links they share. Implements DeviceSession."""

    def __init__(
        self, info: DeviceInfo, actuator: ActuatorDriver, sensor: SensorDriver, links: list[Link]
    ) -> None:
        self.info = info
        self.stimulus = actuator
        self.stream = sensor
        self._links = links

    def close(self) -> None:
        """Actuator first (best-effort baseline, D-010), then the sensor, then the links."""
        parts: list[tuple[str, Any]] = [("actuator", self.stimulus), ("sensor", self.stream)]
        parts += [(f"link {k.info.port}", k) for k in self._links]
        for name, part in parts:
            try:
                part.close()
            except Exception:
                log.exception("closing the %s failed", name)


class DeviceManager:
    """Implements core.ports.DeviceProvider for one fixture (D-049)."""

    def __init__(
        self,
        fixture: Mapping[str, Any],
        on_change: Callable[[Mapping[Role, bool]], None],
        poll_s: float = 1.0,
        on_traffic: TrafficFn | None = None,
    ) -> None:
        self._link_opts: dict[str, Mapping[str, Any]] = dict(fixture["links"])
        try:
            self._link_types = {n: LINKS.get(str(o["type"])) for n, o in self._link_opts.items()}
            self._drivers: dict[Role, tuple[type, Mapping[str, Any]]] = {
                Role.ACTUATOR: (ACTUATORS.get(str(fixture["actuator"]["driver"])), fixture["actuator"]),
                Role.SENSOR: (SENSOR_DRIVERS.get(str(fixture["sensor"]["driver"])), fixture["sensor"]),
            }
        except RegistryError as e:
            raise TestError("E301", str(e)) from None
        for role, (cls, opts) in self._drivers.items():
            name = str(opts["link"])
            if name not in self._link_opts:
                raise TestError("E301", f"{_ROLE_KEY[role]}: link {name!r} is not defined")
            kind = str(self._link_opts[name]["type"])
            if kind not in cls.link_types:
                raise TestError(
                    "E301",
                    f"{_ROLE_KEY[role]} driver {opts['driver']!r} can't use a {kind!r} link "
                    f"(it supports {sorted(cls.link_types)})",
                )
        self._on_change = on_change
        self._on_traffic = on_traffic
        self._poll_s = poll_s
        self._found: dict[str, list[LinkInfo]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reported = False

    # ---------------------------------------------------------------- polling ----
    def start(self) -> None:
        self.poll_once()
        self._thread = threading.Thread(target=self._loop, name="device-manager", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout_s)

    def _loop(self) -> None:
        while not self._stop.wait(self._poll_s):
            self.poll_once()

    def poll_once(self) -> None:
        found: dict[str, list[LinkInfo]] = {}
        for name, cls in self._link_types.items():
            try:
                found[name] = cls.discover(self._link_opts[name])
            except Exception:  # discovery must never kill the poller
                log.exception("discovery failed for link %s", name)
                found[name] = []
        with self._lock:
            before = self._presence(self._found)
            self._found = found
            after = self._presence(found)
        if after != before or not self._reported:
            self._reported = True
            self._on_change(after)

    def _presence(self, found: Mapping[str, list[LinkInfo]]) -> dict[Role, bool]:
        return {r: bool(found.get(str(self._drivers[r][1]["link"]))) for r in ALL_ROLES}

    def roles_present(self) -> Mapping[Role, bool]:
        with self._lock:
            return self._presence(self._found)

    # ---------------------------------------------------------------- one run ----
    def open_session(self, required: frozenset[Role]) -> _Session:
        """Open the links once each and attach both drivers (E101 a role missing, E102 won't open)."""
        self.poll_once()
        with self._lock:
            found = dict(self._found)
        missing = [r for r in ALL_ROLES if r in required and not found.get(str(self._drivers[r][1]["link"]))]
        if missing:
            detail = ", ".join(
                f"{_ROLE_KEY[r]} (link {self._drivers[r][1]['link']!r} not found)" for r in missing
            )
            raise TestError("E101", detail)
        opened: dict[str, Link] = {}
        try:
            for role in ALL_ROLES:
                name = str(self._drivers[role][1]["link"])
                if name not in opened:  # one owner per link, even when both roles share it
                    opened[name] = self._open_link(name, found[name][0])
            actuator = self._attach(Role.ACTUATOR, opened)
            sensor = self._attach(Role.SENSOR, opened)
        except BaseException:
            for k in opened.values():
                try:
                    k.close()
                except Exception:
                    log.exception("closing link %s after a failed open", k.info.port)
            raise
        infos = [k.info for k in opened.values()]
        info = DeviceInfo(
            driver=f"actuator={self._drivers[Role.ACTUATOR][1]['driver']};"
            f"sensor={self._drivers[Role.SENSOR][1]['driver']}",
            port="+".join(i.port for i in infos),
            serial="+".join(i.serial for i in infos),
            roles=frozenset(ALL_ROLES),
        )
        return _Session(info, actuator, sensor, list(opened.values()))

    def _open_link(self, name: str, info: LinkInfo) -> Link:
        try:
            return self._link_types[name].open(info, self._link_opts[name])
        except TestError:
            raise
        except Exception as e:
            raise TestError("E102", f"{info.port}: {e}") from e

    def _attach(self, role: Role, opened: Mapping[str, Link]) -> Any:
        cls, opts = self._drivers[role]
        try:
            driver = cls(opened[str(opts["link"])], opts)
        except TestError:
            raise
        except Exception as e:
            raise TestError("E102", f"{_ROLE_KEY[role]} driver {opts['driver']!r}: {e}") from e
        if self._on_traffic is not None and hasattr(driver, "on_traffic"):
            driver.on_traffic = self._on_traffic
        return driver
