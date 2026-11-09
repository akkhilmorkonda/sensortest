"""Build the apps with PyInstaller, and smoke-test them (the same script runs locally and in CI).

PyInstaller builds for the OS it runs on, so each OS builds its own apps. Two of them:

- **The station app:** Windows ``dist/sensortest.exe`` and Linux ``dist/sensortest`` (one file each),
  macOS ``dist/sensortest.app`` (an app bundle; PyInstaller builds macOS bundles as a folder). It
  holds the ``sensortest`` package and its dependencies, plus the config the station loads
  (settings, the sensor test file, the active limit set): nothing from ``tests/``, ``tools/``,
  ``benchtop_testing/`` or ``docs/``. Its settings default to the Teensy fixture; ``synthetic``
  is kept, so ``--fixture synthetic`` runs without hardware.
- **The self-test app:** ``dist/sensortest-selftest(.exe)``, one file on every OS, a console app.
  It runs the test suite (``tools/selftest.py``), so the tests can be run without Python. It holds
  the package, pytest, the tests, the config and the sources the tests read.

Usage (project .venv, from the repository root):
    python tools/build_app.py                    # build both, then smoke-test both
    python tools/build_app.py app                # only the station app (or: selftest)
    python tools/build_app.py --smoke-only       # smoke-test existing builds
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"
DIST = REPO / "dist"
BUILD = REPO / "build"
NAME = "sensortest"
SELFTEST = "sensortest-selftest"


def app_path() -> Path:
    """The built app's executable."""
    if sys.platform == "darwin":
        return DIST / f"{NAME}.app" / "Contents" / "MacOS" / NAME
    return DIST / (f"{NAME}.exe" if os.name == "nt" else NAME)


def selftest_path() -> Path:
    """The built self-test app's executable."""
    return DIST / (f"{SELFTEST}.exe" if os.name == "nt" else SELFTEST)


def packaged_config() -> Path:
    """build/app_config: what the app ships with. The Teensy is the default fixture; fixtures that
    play bench recordings from a folder are left out (bench data isn't shipped, D-060)."""
    out = BUILD / "app_config"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    s = yaml.safe_load((CONFIG / "settings.yaml").read_text(encoding="utf-8"))
    s["fixtures"] = {
        name: fx
        for name, fx in s["fixtures"].items()
        if not any(link.get("type") == "replay" and "data_dir" in link for link in fx["links"].values())
    }
    s["active_fixture"] = "teensy"
    header = "# Station settings (packaged app). Edit freely: this copy is yours. See the README.\n"
    (out / "settings.yaml").write_text(header + yaml.safe_dump(s, sort_keys=False), encoding="utf-8")
    for name in (s["sensor_tests"], s["limits"]):
        shutil.copy2(CONFIG / name, out / name)
    return out


def build_app() -> None:
    import PyInstaller.__main__

    config = packaged_config()
    args = [
        str(REPO / "src" / NAME / "__main__.py"),
        "--name", NAME,
        "--windowed",
        "--noconfirm",
        "--clean",
        "--paths", str(REPO / "src"),
        "--add-data", f"{config}{os.pathsep}config",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller"),
        "--specpath", str(BUILD),
    ]  # fmt: skip
    if sys.platform != "darwin":
        args.append("--onefile")  # macOS: an .app bundle (a folder), PyInstaller's supported form
    PyInstaller.__main__.run(args)
    print(f"built {app_path()}")


def selftest_data() -> Path:
    """build/selftest_data: the tests and what they read (config incl. the archived limit sets, the
    sources for the architecture test, pyproject.toml for pytest's settings), without caches."""
    out = BUILD / "selftest_data"
    shutil.rmtree(out, ignore_errors=True)
    skip = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")
    for name in ("tests", "config", "src"):
        shutil.copytree(REPO / name, out / name, ignore=skip)
    shutil.copy2(REPO / "pyproject.toml", out / "pyproject.toml")
    return out


def build_selftest() -> None:
    import PyInstaller.__main__
    import pytest

    data = selftest_data()
    args = [
        str(REPO / "tools" / "selftest.py"),
        "--name", SELFTEST,
        "--onefile",
        "--console",
        "--noconfirm",
        "--clean",
        "--paths", str(REPO / "src"),
        "--collect-submodules", "sensortest",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyinstaller-selftest"),
        "--specpath", str(BUILD),
    ]  # fmt: skip
    for name in ("tests", "config", "src", "pyproject.toml"):
        args += ["--add-data", f"{data / name}{os.pathsep}{'.' if name == 'pyproject.toml' else name}"]
    for module in pytest.freeze_includes():  # pytest's own modules, for a frozen app (pytest docs)
        args += ["--hidden-import", module]
    PyInstaller.__main__.run(args)
    print(f"built {selftest_path()}")


def smoke_selftest() -> None:
    """The self-test app runs the whole suite; it must pass."""
    exe = selftest_path()
    if not exe.exists():
        sys.exit(f"no build at {exe}")
    result = subprocess.run([str(exe), "--no-pause", "-q"], timeout=900, check=False)
    if result.returncode != 0:
        sys.exit(f"self-test app failed (exit {result.returncode})")
    print("self-test app passed")


def smoke() -> None:
    """Run the built app headless on synthetic recordings; it must exit 0 and write a record."""
    exe = app_path()
    if not exe.exists():
        sys.exit(f"no build at {exe}")
    home = Path(tempfile.mkdtemp(prefix="sensortest-smoke-"))
    env = {**os.environ, "SENSORTEST_HOME": str(home)}
    cmd = [str(exe), "--headless", "--fixture", "synthetic", "--fast", "--serial", "SMOKE-1", "--runs", "1"]
    result = subprocess.run(cmd, env=env, timeout=300, check=False)
    records = sorted((home / "output").glob("*/SMOKE-1_*.csv"))
    print(f"exit {result.returncode}; records: {[r.name for r in records]}")
    if result.returncode != 0 or not any(not r.name.endswith("_raw.csv") for r in records):
        sys.exit("smoke test failed")
    if not (home / "config" / "settings.yaml").exists():
        sys.exit("smoke test failed: the app didn't unpack its config")
    print("smoke test passed")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("target", nargs="?", default="all", choices=["all", "app", "selftest"])
    p.add_argument("--smoke-only", action="store_true", help="only smoke-test existing builds")
    p.add_argument("--no-smoke", action="store_true", help="build without the smoke tests")
    a = p.parse_args()
    if a.target in ("all", "app"):
        if not a.smoke_only:
            build_app()
        if not a.no_smoke:
            smoke()
    if a.target in ("all", "selftest"):
        if not a.smoke_only:
            build_selftest()
        if not a.no_smoke:
            smoke_selftest()


if __name__ == "__main__":
    main()
