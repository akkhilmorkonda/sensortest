"""Small contracts the rest of the app relies on: registries, error codes, limits."""

import pytest

from sensortest.core.errors import CODES, TestError
from sensortest.core.models import Limit
from sensortest.core.registry import Registry, RegistryError


def test_registry_rejects_duplicates_and_names_unknowns() -> None:
    reg: Registry[int] = Registry("thing")
    reg.register("a")(1)
    with pytest.raises(RegistryError, match="already registered"):
        reg.register("a")(2)
    with pytest.raises(RegistryError, match=r"unknown thing 'b' \(registered: a\)"):
        reg.get("b")


def test_error_labels_and_catalogue() -> None:
    err = TestError("E201", "got 19.00", where="T10")
    assert err.label == "E201@T10" and not err.code.retryable
    assert CODES["E209"].retryable and CODES["W201"].is_warning
    with pytest.raises(ValueError, match="uncatalogued"):
        TestError("E999")


@pytest.mark.parametrize(("value", "ok"), [(712.8, True), (1128.3, True), (712.79, False), (1128.31, False)])
def test_limits_are_inclusive(value: float, ok: bool) -> None:
    assert Limit(712.8, 1128.3).passes(value) is ok


def test_a_step_dependency_is_refused(tmp_path) -> None:
    """D-056: every step is judged on its own channel alone, so the sensor test file has no depends_on."""
    import yaml

    from conftest import CONFIG
    from sensortest.core.errors import TestError
    from sensortest.plans.compiler import compile_plans

    raw = yaml.safe_load((CONFIG / "sensor_tests.yaml").read_text(encoding="utf-8"))
    raw["steps"][4]["depends_on"] = [raw["steps"][0]["id"]]
    path = tmp_path / "tests.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    consts = {
        "fs_nom_hz": 150,
        "line_rate_tolerance": 0.01,
    }
    with pytest.raises(TestError) as e:
        compile_plans(path, CONFIG / "TP-001_limits_v2.yaml", {"PROD": (2040,)}, consts)
    assert e.value.code.code == "E301" and "depends_on" in str(e.value)
