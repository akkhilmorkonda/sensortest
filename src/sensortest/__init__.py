"""Sensor line test software.

Layout (ports and adapters, PRD §6):
    core/     pure domain: models, error codes, ports, measurement methods. Imports nothing internal.
    events/   commands and events, and the in-process bus.
    plans/    the plan compiler: sensor test file + settings + limits -> CompiledPlan.
    steps/    the metric registry (@metric) and the built-in metric functions.
    engine/   the test executive: the state machine and the capture loop.
    devices/  the device manager, the plugin points (@link, @actuator, @sensor) and one folder per
              device: teensy/ (the real one) and replay/ (the recorded Teensy, no hardware needed).
    sinks/    result outputs (@sink), e.g. local CSV.
    characterization/  the limit derivation from bench recordings (the same math as the station).
    ui/       the PyQt6 adapter: the station screen, actions (@action), test-mode panels (@panel).
    app/      the composition root: settings, wiring, GUI and headless entry points.
"""

__version__ = "0.1.0"
