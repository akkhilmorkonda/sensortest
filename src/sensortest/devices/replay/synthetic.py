"""Synthetic recordings from the device model measured in Part 1 (D-060): the recorded Teensy,
without the recordings.

It writes a recording set in the bench DOE format (``manifest.csv`` + ``sweepNN/<run>.tsv`` and
``.json``), which the replay link plays like real DOE-1 data. The tests and the ``synthetic`` fixture
use it, so neither needs bench data. It is never used to judge a real DUT.

The model (DOE-1; docs/device_protocol.md; TP-001 §3): each channel has its own DC offset and uniform
noise, and responds to one shared tone, scaled by its own gain and in phase with the others. The
four faulty channels behave as on the Teensy: ch1 barely responds (weak), ch4 is noisy and a little
strong, ch5 sits at a high offset, and ch8 responds at twice the frequency. Lines arrive at 150.01
per second of host time; the tone is exact in the device's own 150 Hz sample clock; values are
printed with 2 decimals, as the device prints them.
"""

import csv
import functools
import json
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_HZ = 150.0  # the device's clock: tones are exact in samples (D-025)
LINE_RATE_HZ = 150.01  # lines per second of host time, as measured (D-007)


@dataclass(frozen=True, slots=True)
class Channel:
    offset: float  # DC level, a.u.
    noise_sd: float  # uniform noise, standard deviation, a.u.
    gain: float  # tone amplitude, a.u.
    harmonic: int = 1  # 2: responds at twice the commanded frequency


# Rounded from DOE-1 (2 s windows): offsets, baseline noise SDs and tone amplitudes per channel.
DEVICE: tuple[Channel, ...] = (
    Channel(-50.0, 27.5, 10.0),  # ch1: weak response
    Channel(135.0, 20.1, 1000.0),
    Channel(45.0, 11.4, 900.0),
    Channel(376.0, 185.0, 1200.0),  # ch4: noisy, response a little strong
    Channel(1349.0, 14.5, 800.0),  # ch5: offset out of range
    Channel(0.0, 25.6, 930.0),
    Channel(14.0, 8.5, 1019.0),
    Channel(215.0, 23.1, 890.0, harmonic=2),  # ch8: responds at 2f (wrong frequency)
)


def _line(values: np.ndarray) -> bytes:
    """A data line as the Teensy prints it: 2 decimals, each followed by ", ", then CRLF."""
    return ("".join(f"{v:.2f}, " for v in values) + "\r\n").encode("ascii")


def write_run(path: Path, tone_hz: int, segment_s: float, rng: np.random.Generator) -> None:
    """One run, as in DOE-1: ``baseline``, then ``response:f``, then ``baseline`` again, one segment
    each. Writes ``path`` (.tsv: host time + raw line) and the matching .json (the commands)."""
    n_seg = round(segment_s * SAMPLE_HZ)
    n = 3 * n_seg
    k = np.arange(n)
    t = (k + 1) / LINE_RATE_HZ  # host arrival time of each line
    # Each command is sent between two lines, so the next segment starts on the next line (as on
    # the device: a command takes effect from the next data line).
    cmd_t = [(j * n_seg + 0.5) / LINE_RATE_HZ for j in range(3)]
    tone_on = (t > cmd_t[1]) & (t <= cmd_t[2])
    phase0 = rng.uniform(0, 2 * np.pi)
    x = np.empty((n, len(DEVICE)))
    for i, ch in enumerate(DEVICE):
        half = ch.noise_sd * np.sqrt(3.0)  # uniform on ±half has this SD
        tone = ch.gain * np.sin(2 * np.pi * tone_hz * ch.harmonic * k / SAMPLE_HZ + phase0)
        x[:, i] = ch.offset + rng.uniform(-half, half, n) + np.where(tone_on, tone, 0.0)
    rows = ["t_host_s\tline_repr", f"{0.0:.6f}\t{b'.31, 10.85, 189.32, ' + bytes([13, 10])!r}"]
    rows += [f"{ts:.6f}\t{_line(v)!r}" for ts, v in zip(t, x, strict=True)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    sent = [
        {"t_s": cmd_t[0], "cmd": "baseline"},
        {"t_s": cmd_t[1], "cmd": f"response:{tone_hz}"},
        {"t_s": cmd_t[2], "cmd": "baseline"},
    ]
    meta = {
        "label": path.stem,
        "synthetic": True,
        "model": "sensortest.devices.replay.synthetic",
        "sent": sent,
    }
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def write_recordings(
    root: Path,
    frequencies: Iterable[int] = range(1, 20),
    runs: int = 1,
    segment_s: float = 4.0,
    seed: int = 1,
) -> Path:
    """A recording set in ``root``: ``runs`` sweeps over ``frequencies``. Deterministic for a seed."""
    rng = np.random.default_rng(seed)
    root.mkdir(parents=True, exist_ok=True)
    manifest = []
    for sweep in range(1, runs + 1):
        folder = root / f"sweep{sweep:02d}"
        folder.mkdir(exist_ok=True)
        for pos, f in enumerate(frequencies, 1):
            name = f"synthetic_s{sweep:02d}p{pos:02d}_r{f:02d}"
            write_run(folder / f"{name}.tsv", f, segment_s, rng)
            manifest.append({"sweep": sweep, "pos": pos, "cmd": f, "file": name, "status": "ok"})
    with (root / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["sweep", "pos", "cmd", "file", "status"])
        w.writeheader()
        w.writerows(manifest)
    return root


@functools.cache
def default_set() -> Path:
    """The process-wide synthetic set (all tones 1-19 Hz, one run each), written once to a temp
    folder and removed when the process exits. What ``synthetic: true`` on a replay link plays."""
    import atexit

    root = Path(tempfile.mkdtemp(prefix="sensortest-synthetic-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    return write_recordings(root)
