# Developer tools

Scripts for working on the station software. Not part of the app or its package. Run them with the
project's virtual environment from the repository root.

| Script | What it does |
|---|---|
| `build_app.py` | Builds two apps with PyInstaller for the OS it runs on, then smoke-tests each. **The station app** (Windows `.exe`, Linux binary, macOS `.app`) bundles only the package and the config the station loads; its smoke test runs it headless on synthetic recordings. **The self-test app** (`sensortest-selftest`) bundles the test suite; its smoke test is the suite passing. `app` or `selftest` builds just one. CI runs it on all three OSes |
| `selftest.py` | The self-test app's entry point: runs the bundled test suite with pytest and prints the result, then waits for Enter so a double-clicked window stays open. Passes pytest options through (`--hardware`, `-k`). `python tools/selftest.py` does the same from source |
| `capture_screens.py` | Drives the real app through its own buttons (any fixture, any sequence, an optional replay fault) and saves the screen in each state to `docs/gui_screens/`: idle, running, result, one channel enlarged, test mode. Records go to a temp folder |

Bench work (talking to the device, DOE runs, limit derivation, the gauge run) has its own tools in
`benchtop_testing/tools/`.
