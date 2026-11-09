"""Measurement methods M1–M4 and M6–M9 (TP-001 §6). Pure functions on numpy arrays.
Removed: M5 (phase vs the other channels, D-056: all channels are assumed in phase) and M10 (the
stimulus step, D-058: the actuator's tone is assumed never to jump).

These are the definitions the limits were derived with (benchtop_testing/tools/derive_limits_v0.py).
Section 03 adds unit tests that reproduce L-v0 from DOE-1 through these functions (D-019 risk 1).
x is one sensor's window, shape (N,); a "window" argument is (N, 8).
"""

import numpy as np

FS_NOM_HZ = 150.0  # D-025: fixed device time base; never the measured line rate


def sine_fit(x: np.ndarray, f_hz: float, fs_hz: float = FS_NOM_HZ) -> tuple[float, float, float, np.ndarray]:
    """M3: least squares x ≈ c + a·sin(2πfn/fs) + b·cos(2πfn/fs) at the commanded f.

    Returns (offset c, amplitude √(a²+b²), phase atan2(b, a) in radians, residual).
    """
    w = 2 * np.pi * f_hz * np.arange(len(x)) / fs_hz
    m = np.column_stack([np.ones_like(w), np.sin(w), np.cos(w)])
    c, *_ = np.linalg.lstsq(m, x, rcond=None)
    return float(c[0]), float(np.hypot(c[1], c[2])), float(np.arctan2(c[2], c[1])), x - m @ c


def dominant_frequency(x: np.ndarray, fs_hz: float = FS_NOM_HZ, pad: int = 8) -> tuple[float, float]:
    """M4: Hann window, zero-padded ×pad, largest peak above 0.5 Hz, parabolic interpolation.

    Returns (frequency Hz, peak amplitude in a.u.).
    """
    n = len(x)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft((x - x.mean()) * win, n * pad)) * 2 / win.sum()
    freqs = np.fft.rfftfreq(n * pad, 1 / fs_hz)
    k = int(np.argmax(np.where(freqs > 0.5, spec, 0)))
    d = 0.0
    if 0 < k < len(spec) - 1:
        a, b, c = np.log(spec[k - 1 : k + 2] + 1e-12)
        denom = a - 2 * b + c
        d = 0.5 * (a - c) / denom if denom else 0.0
    return float(freqs[k] + d * (freqs[1] - freqs[0])), float(spec[k])


def amplitude_at_response(x: np.ndarray, f_cmd_hz: float, present: float, fs_hz: float = FS_NOM_HZ) -> float:
    """M3 at the frequency the channel responds at (D-056).

    M4 finds the dominant peak first. If it is a tone (peak >= ``present``, the D-030 gate), fit M3 at
    that frequency; otherwise the peak is noise, so fit at the commanded ``f_cmd_hz``. A healthy
    channel reads the same as M3 at the commanded f (its peak is within a few mHz of it); a channel
    responding at the wrong frequency reads its real response size and fails only on frequency.
    """
    f, peak = dominant_frequency(x, fs_hz)
    return sine_fit(x, f if peak >= present else f_cmd_hz, fs_hz)[1]


def spur(residual: np.ndarray, f_hz: float, fs_hz: float = FS_NOM_HZ, pad: int = 4) -> float:
    """M6: largest Hann-spectrum peak of the residual above 0.5 Hz and more than 1 Hz from f."""
    win = np.hanning(len(residual))
    spec = np.abs(np.fft.rfft((residual - residual.mean()) * win, len(residual) * pad)) * 2 / win.sum()
    freqs = np.fft.rfftfreq(len(residual) * pad, 1 / fs_hz)
    mask = (freqs > 0.5) & (np.abs(freqs - f_hz) > 1.0)
    return float(spec[mask].max()) if mask.any() else 0.0


def longest_identical_run(x: np.ndarray) -> int:
    """M7: longest run of consecutive identical values."""
    if len(x) == 0:
        return 0
    change = np.flatnonzero(np.diff(x) != 0)
    edges = np.concatenate(([-1], change, [len(x) - 1]))
    return int(np.diff(edges).max())


def outlier_count(x: np.ndarray, k: float = 2.5) -> int:
    """M8: samples with |x - mean| > k·std (ddof 1). Valid for uniform noise only (TP-001 §15)."""
    sd = x.std(ddof=1)
    return int((np.abs(x - x.mean()) > k * sd).sum()) if sd > 0 else 0


def line_rate(t_host: np.ndarray) -> float:
    """M9: least-squares slope of line index vs host time (validity only, never used in the math)."""
    if len(t_host) < 2 or np.ptp(t_host) == 0:
        return float("nan")
    return float(np.polyfit(t_host, np.arange(len(t_host)), 1)[0])
