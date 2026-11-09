# Developer tools

Scripts for working on the station software. Not part of the app or its package. Run them with the
project's virtual environment from the repository root.

| Script | What it does |
|---|---|
| `build_app.py` | Builds the one-file app with PyInstaller for the OS it runs on (Windows `.exe`, Linux binary, macOS `.app`), bundling only the package and the config the station loads, then smoke-tests it headless on synthetic recordings. CI runs it on all three OSes |
| `capture_screens.py` | Drives the real app through its own buttons (any fixture, any sequence, an optional replay fault) and saves the screen in each state to `docs/gui_screens/`: idle, running, result, one channel enlarged, test mode. Records go to a temp folder |

Bench work (talking to the device, DOE runs, limit derivation, the gauge run) has its own tools in
`benchtop_testing/tools/`.
