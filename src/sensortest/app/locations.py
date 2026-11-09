"""Where the app finds its settings and writes its records, from source and as a packaged app.

- **From source:** ``config/settings.yaml`` in the repository; records go to ``output/`` beside it.
- **Packaged (PyInstaller):** the bundled files live in a temp folder that disappears when the app
  exits, so on first run the bundled ``config/`` is copied to the app's home folder, where it can be
  edited and is kept between runs. Its ``data_dir: ../output`` then puts the records in
  ``<home>/output``. The home is ``~/sensortest``, or ``$SENSORTEST_HOME`` if set.
"""

import os
import shutil
import sys
from pathlib import Path

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config"


def frozen() -> bool:
    """True inside a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def app_home() -> Path:
    """The packaged app's folder for its settings and records."""
    return Path(os.environ.get("SENSORTEST_HOME") or Path.home() / "sensortest")


def default_settings() -> Path:
    """The settings file to use when ``--settings`` isn't given."""
    if not frozen():
        return REPO_CONFIG / "settings.yaml"
    config = app_home() / "config"
    if not (config / "settings.yaml").exists():  # first run: unpack the bundled config, once
        shutil.copytree(Path(getattr(sys, "_MEIPASS", "")) / "config", config, dirs_exist_ok=True)
    return config / "settings.yaml"
