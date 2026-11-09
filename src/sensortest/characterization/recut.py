"""Re-cut DOE-1 recordings into the test plan's windows (D-019, D-050).

A DOE-1 tone run is `baseline` -> `response:f` -> `baseline`; a control run is `baseline` only.
That is the order of a test, so each run is cut the way the station cuts a capture: after the
command, discard the capture's settle lines, then take its window lines.

- BL after the first `baseline`;
- the tone window after `response:f`. It stands in for T10, measured at its own f (the response
  is flat over 1-19 Hz, DOE-1 chart 2);
- REC after the last `baseline`.

The settle and window lengths come from the plan's own captures, so the derivation can't disagree
with the station about timing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sensortest.core.models import CaptureDef, CompiledPlan, Window
from sensortest.devices.replay.recordings import Recording, read_manifest, read_recording

BL, TONE, REC = "BL", "T10", "REC"  # TONE: the PROD tone capture each DOE-1 tone run stands in for


@dataclass(frozen=True)
class DoeRun:
    sweep: int
    pos: int
    tone_hz: int | None  # None for a control run
    windows: Mapping[str, Window]  # BL always; T10 and REC for tone runs

    @property
    def cmd(self) -> str:
        """The manifest's command column: the tone in Hz, or "control"."""
        return "control" if self.tone_hz is None else str(self.tone_hz)


def cut(rec: Recording, command: int, capture: CaptureDef) -> Window:
    """The capture's window after the ``command``-th command: skip settle lines, take window lines."""
    i0 = rec.after(command) + capture.settle_lines
    i1 = i0 + capture.window_lines
    if i1 > len(rec.x):
        raise ValueError(
            f"{rec.path.name}: {len(rec.x) - i0} lines after command {command}, "
            f"{capture.id} needs {capture.window_lines}"
        )
    return Window(capture.id, rec.x[i0:i1].copy(), rec.t[i0:i1].copy())


def load_doe(root: Path, plan: CompiledPlan) -> list[DoeRun]:
    """Every run that passed its capture check, cut with ``plan``'s timing (use the ENG plan)."""
    caps = {c.id: c for c in plan.captures}
    missing = [c for c in (BL, TONE, REC) if c not in caps]
    if missing:
        raise ValueError(f"plan {plan.sequence} has no {missing} capture; use the ENG plan")
    runs = []
    for m in read_manifest(root):
        rec = read_recording(m.tsv)
        windows = {BL: cut(rec, 0, caps[BL])}
        if m.tone_hz is not None:
            windows[TONE] = cut(rec, 1, caps[TONE])
            windows[REC] = cut(rec, 2, caps[REC])
        runs.append(DoeRun(m.sweep, m.pos, m.tone_hz, windows))
    return runs
