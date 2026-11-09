"""The composition root: builds every module from the settings and wires them together.

This is the only place that names concrete classes. Plugins register by import: the three
imports below are the whole plugin list for this build (D-033).
"""

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import sensortest.devices.replay  # registers the recorded Teensy (link + drivers)
import sensortest.devices.serial_link  # registers the serial link
import sensortest.devices.teensy  # registers the Teensy drivers
import sensortest.sinks.csv_sink  # registers sinks
import sensortest.steps.builtin  # noqa: F401  (registers metrics)
from sensortest import __version__
from sensortest.app.settings import Settings, load_settings
from sensortest.core.errors import TestError
from sensortest.core.models import CompiledPlan, StepType
from sensortest.devices.manager import DeviceManager
from sensortest.engine.engine import Engine
from sensortest.events.bus import EventBus
from sensortest.events.messages import DeviceStatusChanged, TrafficSeen
from sensortest.plans.compiler import compile_plans
from sensortest.sinks.registry import SINKS
from sensortest.sinks.writer import SinkWriter
from sensortest.steps.registry import METRICS


@dataclass
class App:
    settings: Settings
    bus: EventBus
    devices: DeviceManager
    engine: Engine
    writer: SinkWriter

    def start(self) -> None:
        self.writer.start()
        self.devices.start()
        self.engine.start()

    def stop(self) -> None:
        """Ordered shutdown (D-043): engine (safe state) → device manager → flush sinks."""
        self.engine.stop()
        self.devices.stop()
        self.writer.stop()


def build(settings_path: Path, *, fixture: str | None = None, fast: bool = False) -> App:
    """``fixture`` overrides active_fixture; ``fast`` turns off real-time pacing on replay links."""
    settings = load_settings(settings_path)
    if fixture is not None:
        if fixture not in settings.fixtures:
            raise TestError("E301", f"fixture {fixture!r} is not one of {sorted(settings.fixtures)}")
        settings = replace(settings, active_fixture=fixture)
    _setup_logging(settings.data_dir / "logs")
    hw = _fixture(settings.fixture, fast)
    bus = EventBus()
    devices = DeviceManager(
        hw,
        lambda roles: bus.publish(DeviceStatusChanged(dict(roles))),
        settings.device_poll_s,
        on_traffic=lambda direction, text: bus.publish(TrafficSeen(direction, text, time.time())),
    )

    def load_plans() -> dict[str, CompiledPlan]:
        plans = compile_plans(
            settings.sensor_tests,
            settings.limits,
            settings.plans,
            settings.plan_constants,
        )
        for plan in plans.values():
            used = {s.metric for s in plan.steps if s.type is StepType.LIMIT} | {
                d.metric for d in plan.diagnostics
            }
            missing = sorted(k for k in used if k not in METRICS)
            if missing:
                raise TestError("E301", f"{plan.sequence}: no @metric registered for {missing}")
        return plans

    engine = Engine(
        bus,
        devices,
        load_plans,
        METRICS.get,
        station=settings.station,
        app_version=__version__,
        max_operator_retries=settings.max_operator_retries,
        serial_pattern=settings.serial_pattern,
    )
    sinks = [
        SINKS.get(name)({**opts, "data_dir": settings.data_dir}) for name, opts in settings.sinks.items()
    ]
    retries = settings.sinks.get("csv", {}).get("write_retries_s", (0.2, 0.4, 0.8))
    writer = SinkWriter(bus, sinks, settings.data_dir / "fallback", tuple(retries))
    return App(settings, bus, devices, engine, writer)


def _fixture(fx: Mapping[str, Any], fast: bool) -> dict[str, Any]:
    """The active fixture, with real-time pacing off on replay links when ``fast``."""
    links = {
        name: {**opts, "realtime": False} if fast and opts["type"] == "replay" else dict(opts)
        for name, opts in fx["links"].items()
    }
    return {**fx, "links": links}


def _setup_logging(folder: Path) -> None:
    """Application log (NFR-09): rotating file, separate from the test records."""
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
