"""The settings YAML (D-041): station, files, fixtures (D-049), plan lists, policies (D-044).

Relative paths resolve from the settings file's own folder, so the app works from any cwd and
a packaged app can ship config/ next to the executable. Problems raise TestError E301.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sensortest.core.yamlio import Fields, load_yaml

SCHEMA = "tp001-settings-v2"  # v2: fixtures with per-role drivers (D-049)
# Fixture numbers the plans' validity checks use (1000 CONNECT, 1900 RUN_INTEGRITY), by role.
# The actuator's stimulus-step limits went with E211 (D-058).
PLAN_CONSTANTS = {
    "sensor": ("fs_nom_hz", "line_rate_tolerance"),
}


@dataclass(frozen=True, slots=True)
class Settings:
    path: Path
    station: str
    data_dir: Path
    sensor_tests: Path
    limits: Path
    operator_sequence: str
    plans: Mapping[str, tuple[int, ...]]  # "PROD" / "ENG" -> step IDs (D-040)
    active_fixture: str
    fixtures: Mapping[str, Mapping[str, Any]]  # name -> {links, actuator, sensor} (D-049)
    device_poll_s: float
    max_operator_retries: int
    sinks: Mapping[str, Mapping[str, Any]]
    plot_fps: float
    serial_pattern: str = r"^[A-Za-z0-9_-]{1,32}$"  # DUT serial format (D-047)

    @property
    def fixture(self) -> Mapping[str, Any]:
        return self.fixtures[self.active_fixture]

    @property
    def plan_constants(self) -> dict[str, float]:
        """The active fixture's numbers for compile_plans (PLAN_CONSTANTS)."""
        fx = self.fixture
        return {k: float(fx[role][k]) for role, keys in PLAN_CONSTANTS.items() for k in keys}


def load_settings(path: Path) -> Settings:
    path = path.resolve()
    f = Fields(load_yaml(path), path.name)
    if f.text("schema") != SCHEMA:
        raise f.error("schema", f"expected {SCHEMA!r}")
    base = path.parent

    plans_f = f.section("plans")
    plans = {}
    for name in plans_f:
        ids = plans_f.items(name)
        if not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise plans_f.error(name, "expected a non-empty list of step IDs")
        plans[name] = tuple(ids)
    operator_sequence = f.text("operator_sequence")
    if operator_sequence not in plans:
        raise f.error("operator_sequence", f"{operator_sequence!r} is not one of the plans {sorted(plans)}")

    fixtures_f = f.section("fixtures")
    fixtures = {name: _fixture(fixtures_f.section(name), base) for name in fixtures_f}
    active = f.text("active_fixture")
    if active not in fixtures:
        raise f.error("active_fixture", f"{active!r} is not one of the fixtures {sorted(fixtures)}")

    sinks_f = f.section("sinks", {})
    return Settings(
        path=path,
        station=f.text("station"),
        data_dir=(base / f.text("data_dir")).resolve(),
        sensor_tests=(base / f.text("sensor_tests")).resolve(),
        limits=(base / f.text("limits")).resolve(),
        operator_sequence=operator_sequence,
        plans=plans,
        active_fixture=active,
        fixtures=fixtures,
        device_poll_s=f.number("device_poll_s", 1.0),
        max_operator_retries=f.section("run_policy", {}).integer("max_operator_retries", 1),
        sinks={k: sinks_f.section(k).as_dict() for k in sinks_f},
        plot_fps=f.section("ui", {}).number("plot_fps", 30.0),
        serial_pattern=f.text("serial_pattern", r"^[A-Za-z0-9_-]{1,32}$"),
    )


def _fixture(fx: Fields, base: Path) -> dict[str, Any]:
    """One fixture: its links, and a driver + link for each role (D-049). Names are checked here;
    driver and link types are checked against the registries by the device manager."""
    links_f = fx.section("links")
    links: dict[str, dict[str, Any]] = {}
    for name in links_f:
        lf = links_f.section(name)
        lf.text("type")
        d = lf.as_dict()
        if isinstance(d.get("data_dir"), str):
            d["data_dir"] = (base / d["data_dir"]).resolve()
        links[name] = d
    if not links:
        raise fx.error("links", "define at least one link")
    roles: dict[str, dict[str, Any]] = {}
    for role in ("actuator", "sensor"):
        rf = fx.section(role)
        rf.text("driver")
        if rf.text("link") not in links:
            raise rf.error("link", f"{rf.text('link')!r} is not one of this fixture's links {sorted(links)}")
        roles[role] = rf.as_dict()
    for role, keys in PLAN_CONSTANTS.items():
        for k in keys:
            fx.section(role).number(k)
    return {"links": links, **roles}
