"""Where the app finds its settings, from source and as a packaged (PyInstaller) app."""

import sys
from pathlib import Path

import pytest

from conftest import CONFIG
from sensortest.app import locations


def test_from_source_the_repo_config_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert locations.default_settings() == CONFIG / "settings.yaml"


def test_a_packaged_app_unpacks_its_config_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "bundle" / "config"
    bundled.mkdir(parents=True)
    (bundled / "settings.yaml").write_text("bundled", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setenv("SENSORTEST_HOME", str(tmp_path / "home"))

    path = locations.default_settings()
    assert path == tmp_path / "home" / "config" / "settings.yaml"
    assert path.read_text(encoding="utf-8") == "bundled"  # first run: copied out of the bundle
    path.write_text("edited", encoding="utf-8")
    assert locations.default_settings().read_text(encoding="utf-8") == "edited"  # never overwritten


def test_the_home_defaults_to_a_folder_in_the_user_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENSORTEST_HOME", raising=False)
    assert locations.app_home() == Path.home() / "sensortest"
