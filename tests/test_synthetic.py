"""Synthetic recordings (D-060): the device model in the bench DOE format, so no test needs bench data.

The model must give the Teensy's verdicts through the same code that judges a real run: the limit
derivation's re-cut and evaluation (sensortest.characterization) as well as the engine (the replay
tests in test_engine_replay.py and test_gui_smoke.py run on it too).
"""

from pathlib import Path

import numpy as np
import pytest

from conftest import CONFIG
from sensortest.app.settings import load_settings
from sensortest.characterization.recut import load_doe
from sensortest.characterization.verdicts import at_tone, prod_run, summarize
from sensortest.core.models import CompiledPlan
from sensortest.devices.replay.recordings import read_manifest, read_recording
from sensortest.devices.replay.synthetic import DEVICE, LINE_RATE_HZ, write_recordings
from sensortest.plans.compiler import compile_plans


@pytest.fixture(scope="module")
def plans() -> dict[str, CompiledPlan]:
    s = load_settings(CONFIG / "settings.yaml")
    return compile_plans(s.sensor_tests, s.limits, s.plans, s.plan_constants)


def test_a_set_has_the_bench_format_and_is_reproducible(tmp_path: Path) -> None:
    a = write_recordings(tmp_path / "a", frequencies=(10,), segment_s=2.0, seed=7)
    b = write_recordings(tmp_path / "b", frequencies=(10,), segment_s=2.0, seed=7)
    runs = read_manifest(a)
    assert [(r.sweep, r.pos, r.tone_hz) for r in runs] == [(1, 1, 10)]
    rec = read_recording(runs[0].tsv)
    assert rec.x.shape == (3 * 300, len(DEVICE))  # the first, partial line is dropped as on the device
    assert [c for _, c in rec.commands] == ["baseline", "response:10", "baseline"]
    assert rec.after(1) == 300 and rec.after(2) == 600  # each command starts the next segment
    assert np.polyfit(rec.t, np.arange(len(rec.t)), 1)[0] == pytest.approx(LINE_RATE_HZ)
    np.testing.assert_array_equal(rec.x, read_recording(read_manifest(b)[0].tsv).x)


def test_the_model_gives_the_teensys_verdicts(tmp_path: Path, plans: dict[str, CompiledPlan]) -> None:
    runs = load_doe(write_recordings(tmp_path, frequencies=(5, 10, 15), runs=2), plans["ENG"])
    summary = summarize(prod_run(plans["PROD"], r) for r in runs if r.tone_hz is not None)
    got = {ch: (s.failed, dict(s.primary)) for ch, s in summary.items()}
    assert got == {
        "ch1": (6, {"gain": 6}),  # weak response
        "ch2": (0, {}),
        "ch3": (0, {}),
        "ch4": (6, {"noise": 6}),  # noisy (also too strong)
        "ch5": (6, {"offset": 6}),  # offset out of range
        "ch6": (0, {}),
        "ch7": (0, {}),
        "ch8": (6, {"frequency": 6}),  # responds at 2f
    }
    assert dict(summary["ch8"].steps) == {6050: 6}  # D-056: frequency only, amplitude reads its 2f response


def test_a_tone_run_moves_the_frequency_limit_with_it(plans: dict[str, CompiledPlan]) -> None:
    p = at_tone(plans["PROD"], "T10", 7)
    t10 = next(c for c in p.captures if c.id == "T10")
    assert (t10.tone_hz, t10.command) == (7, "response:7")
    assert (p.limits[6050].low, p.limits[6050].high) == (6.5, 7.5)
    assert p.limits[6040] == plans["PROD"].limits[6040]  # amplitude doesn't depend on f
