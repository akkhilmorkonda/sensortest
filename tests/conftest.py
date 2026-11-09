"""Shared fixtures: a settings file pointing at the real config, with output in a temp folder.

No test needs bench data (D-060): the replay runs on synthetic recordings made from the device model.
"""

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"


def make_settings(tmp: Path, **replay: object) -> Path:
    """Copy config/settings.yaml with absolute paths, output in ``tmp``, on the synthetic fixture.

    ``replay`` overrides the replay link's options (fault, run_index, realtime).
    """
    s = yaml.safe_load((CONFIG / "settings.yaml").read_text(encoding="utf-8"))
    s["data_dir"] = str(tmp / "out")
    s["sensor_tests"] = str(CONFIG / "sensor_tests.yaml")
    s["limits"] = str(CONFIG / s["limits"])  # the station's active limit set
    s["active_fixture"] = "synthetic"
    s["fixtures"]["synthetic"]["links"]["sim"].update({"realtime": False, **replay})
    s["device_poll_s"] = 0.05
    path = tmp / "settings.yaml"
    path.write_text(yaml.safe_dump(s), encoding="utf-8")
    return path


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    return make_settings(tmp_path)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--hardware", action="store_true", help="also run the tests on the real Teensy")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--hardware"):
        return
    skip = pytest.mark.skip(reason="needs the Teensy on USB: run pytest --hardware")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)
