"""M1–M9 on synthetic signals with known answers (section 03 adds the DOE-1 golden values)."""

import numpy as np
import pytest

from sensortest.core import methods as m

FS = 150.0
N = 300
n = np.arange(N)


def tone(f: float, a: float = 1000.0, phase_deg: float = 30.0, offset: float = 50.0) -> np.ndarray:
    return offset + a * np.sin(2 * np.pi * f * n / FS + np.radians(phase_deg))


@pytest.mark.parametrize("f", [5.0, 10.0, 15.0])
def test_sine_fit_recovers_offset_amplitude_phase(f: float) -> None:
    c, a, ph, res = m.sine_fit(tone(f), f)
    assert c == pytest.approx(50.0) and a == pytest.approx(1000.0)
    assert np.degrees(ph) == pytest.approx(30.0) and np.abs(res).max() < 1e-6


@pytest.mark.parametrize("f", [5.0, 10.0, 15.0, 20.0])
def test_dominant_frequency(f: float) -> None:
    got, peak = m.dominant_frequency(tone(f))
    assert got == pytest.approx(f, abs=0.02) and peak == pytest.approx(1000, rel=0.05)


def test_amplitude_at_the_response_frequency() -> None:
    """D-056: the fit follows the measured frequency when a tone is present, else the commanded f."""
    rng = np.random.default_rng(1)
    healthy = tone(10) + rng.uniform(-40, 40, N)
    assert m.amplitude_at_response(healthy, 10, 100) == pytest.approx(m.sine_fit(healthy, 10)[1], abs=0.5)
    doubled = tone(20, 890) + rng.uniform(-40, 40, N)  # like ch8: all response at 2f
    assert m.sine_fit(doubled, 10)[1] < 10  # the old M3 at the commanded f: ~nothing
    assert m.amplitude_at_response(doubled, 10, 100) == pytest.approx(890, abs=5)
    weak = tone(10, 10) + rng.uniform(-40, 40, N)  # like ch1: no tone above the noise
    assert m.amplitude_at_response(weak, 10, 100) == m.sine_fit(weak, 10)[1]  # falls back to f


def test_stuck_outliers_spur_line_rate() -> None:
    x = np.array([1, 2, 2, 2, 3, 3, 4], dtype=float)
    assert m.longest_identical_run(x) == 3
    rng = np.random.default_rng(0)
    u = rng.uniform(-1, 1, 3000)
    assert m.outlier_count(u) == 0  # uniform noise never exceeds 1.73 std
    u[100] = 50.0
    assert m.outlier_count(u) == 1
    _, _, _, res = m.sine_fit(tone(10) + 20 * np.sin(2 * np.pi * 25 * n / FS), 10)
    assert m.spur(res, 10) == pytest.approx(20, rel=0.1)
    assert m.line_rate(np.arange(300) / 150.0) == pytest.approx(150.0)
