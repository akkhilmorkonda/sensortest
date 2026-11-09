"""Built-in metrics: the metric dictionary's keys, implemented on top of core.methods.

Each takes a capture window and returns one value per sensor (Measured). A sensor that can't be
judged gets NaN plus a reason, and its step becomes NOT TESTED (e.g. no tone for frequency).
Importing this module registers them; app/ imports it once at startup.
"""

import numpy as np

from sensortest.core import methods as m
from sensortest.core.models import Window
from sensortest.core.ports import Measured, MetricContext
from sensortest.steps.registry import metric

N_SENSORS = 8


def _per_sensor(window: Window, fn) -> Measured:
    return Measured(np.array([fn(window.data[:, i]) for i in range(N_SENSORS)], dtype=float))


def _fs(ctx: MetricContext) -> float:
    return float(ctx.plan.constants.get("fs_nom_hz", m.FS_NOM_HZ))


def _bl(ctx: MetricContext) -> Window:
    return ctx.windows[ctx.plan.captures[0].id]


# ---------------------------------------------------------------- baseline-type ----
@metric("offset")
def offset(window: Window, ctx: MetricContext) -> Measured:
    return Measured(window.data.mean(axis=0))


@metric("noise_std")
def noise_std(window: Window, ctx: MetricContext) -> Measured:
    return Measured(window.data.std(axis=0, ddof=1))


@metric("p2p")
def p2p(window: Window, ctx: MetricContext) -> Measured:
    return Measured(np.ptp(window.data, axis=0).astype(float))


@metric("stuck_run")
def stuck_run(window: Window, ctx: MetricContext) -> Measured:
    return _per_sensor(window, m.longest_identical_run)


@metric("outliers")
def outliers(window: Window, ctx: MetricContext) -> Measured:
    return _per_sensor(window, m.outlier_count)


@metric("recovery_shift")
def recovery_shift(window: Window, ctx: MetricContext) -> Measured:
    return Measured(window.data.mean(axis=0) - _bl(ctx).data.mean(axis=0))


@metric("tone_absent")
def tone_absent(window: Window, ctx: MetricContext) -> Measured:
    if ctx.last_tone_hz is None:
        return Measured(np.full(N_SENSORS, np.nan), {i: "no tone ran before this capture" for i in range(8)})
    return _per_sensor(window, lambda x: m.sine_fit(x, ctx.last_tone_hz, _fs(ctx))[1])


# ---------------------------------------------------------------- tone ----
def _tone(ctx: MetricContext) -> float:
    if ctx.tone_hz is None:
        raise ValueError("tone metric used on a baseline capture")
    return ctx.tone_hz


@metric("tone_amplitude")
def tone_amplitude(window: Window, ctx: MetricContext) -> Measured:
    """D-056: the response at the frequency the channel actually responds at.

    With a tone present (dominant peak >= tone_present, the D-030 gate), fit at the measured dominant
    frequency, so a channel responding at the wrong frequency fails frequency only, not both. With
    no tone (a dead or weak channel), the peak is noise: fit at the commanded f instead.
    """
    present, f_cmd, fs = ctx.plan.constants["tone_present"], _tone(ctx), _fs(ctx)
    return _per_sensor(window, lambda x: m.amplitude_at_response(x, f_cmd, present, fs))


def _amplitude_at_tone(window: Window, ctx: MetricContext) -> np.ndarray:
    """M3 at the commanded f: the diagnostics describe the response to the tone that was driven."""
    return _per_sensor(window, lambda x: m.sine_fit(x, _tone(ctx), _fs(ctx))[1]).values


@metric("dominant_frequency")
def dominant_frequency(window: Window, ctx: MetricContext) -> Measured:
    """D-030: judged only when a tone is present (dominant peak >= tone_present)."""
    present = ctx.plan.constants["tone_present"]
    values, not_tested = np.empty(N_SENSORS), {}
    for i in range(N_SENSORS):
        f, peak = m.dominant_frequency(window.data[:, i], _fs(ctx))
        if peak < present:
            values[i] = np.nan
            not_tested[i] = f"no tone (peak {peak:.1f} < {present:g} a.u.)"
        else:
            values[i] = f
    return Measured(values, not_tested)


@metric("residual_std")
def residual_std(window: Window, ctx: MetricContext) -> Measured:
    return _per_sensor(window, lambda x: m.sine_fit(x, _tone(ctx), _fs(ctx))[3].std(ddof=3))


@metric("spur")
def spur(window: Window, ctx: MetricContext) -> Measured:
    return _per_sensor(window, lambda x: m.spur(m.sine_fit(x, _tone(ctx), _fs(ctx))[3], _tone(ctx), _fs(ctx)))


@metric("half_stability")
def half_stability(window: Window, ctx: MetricContext) -> Measured:
    def ratio(x: np.ndarray) -> float:
        h = len(x) // 2
        a1 = m.sine_fit(x[:h], _tone(ctx), _fs(ctx))[1]
        a2 = m.sine_fit(x[h:], _tone(ctx), _fs(ctx))[1]
        return (a2 / a1 - 1) * 100 if a1 > 0 else float("nan")

    return _per_sensor(window, ratio)


@metric("offset_shift")
def offset_shift(window: Window, ctx: MetricContext) -> Measured:
    bl = _bl(ctx).data.mean(axis=0)
    fit = _per_sensor(window, lambda x: m.sine_fit(x, _tone(ctx), _fs(ctx))[0])
    return Measured(fit.values - bl)


@metric("snr")
def snr(window: Window, ctx: MetricContext) -> Measured:
    noise = _bl(ctx).data.std(axis=0, ddof=1)
    amp = _amplitude_at_tone(window, ctx)
    with np.errstate(divide="ignore", invalid="ignore"):
        return Measured(20 * np.log10((amp / np.sqrt(2)) / noise))
