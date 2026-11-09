"""Build the one-file app with PyInstaller, and smoke-test it (the same script runs locally and in CI).

PyInstaller builds for the OS it runs on, so each OS builds its own app:

    Windows   dist/sensortest.exe     one file
    Linux     dist/sensortest         one file
    macOS     dist/sensortest.app     an app bundle (PyInstaller builds macOS bundles as a folder)

What goes in: the ``sensortest`` package and its dependencies, plus the config the station loads
(settings, the sensor test file, the active limit set). Nothing from ``tests/``, ``tools/``,
``benchtop_testing/`` or ``docs/``. The packaged settings default to the Teensy fixture; the
``synthetic`` fixture is kept, so ``--fixture synthetic`` runs without hardware.

Usage (project .venv, from the repository root):
    python tools/build_app.py              # build, then smoke-test
    python tools/build_app.py --smoke-only # smoke-test an existing build
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


def app_path() -> Path:
    """The built app's executable."""
    if sys.platform == "darwin":
        return DIST / f"{NAME}.app" / "Contents" / "MacOS" / NAME
    return DIST / (f"{NAME}.exe" if os.name == "nt" else NAME)


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


def build() -> None:
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
    p.add_argument("--smoke-only", action="store_true", help="only smoke-test an existing build")
    p.add_argument("--no-smoke", action="store_true", help="build without the smoke test")
    a = p.parse_args()
    if not a.smoke_only:
        build()
    if not a.no_smoke:
        smoke()


if __name__ == "__main__":
    main()
