"""Every line of the current run, as it arrives, for the live strips and the enlarged plot (D-053).

No Qt here, so it is tested directly. Time is the line count divided by the nominal rate, with the
captures laid end to end in the order they ran: replayed captures come from different recordings
with unrelated host clocks, and the ~10 ms between captures (the command) is not data. The
measured window of each capture (after its settle lines) is kept as a span, so the enlarged plot
can shade exactly what the steps judged.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from sensortest.core.models import CaptureDef

CHANNELS = 8


@dataclass(frozen=True, slots=True)
class Span:
    """One capture's measured window, in run seconds."""

    capture: str
    title: str
    tone: bool
    t0: float
    t1: float


class RunBuffer:
    def __init__(self, fs_hz: float = 150.0) -> None:
        self.fs = fs_hz
        self._captures: dict[str, CaptureDef] = {}
        self._order: list[str] = []  # captures in the order their first line arrived
        self._base: dict[str, int] = {}  # capture -> run line index of its first line
        self._chunks: list[np.ndarray] = []
        self._n = 0
        self._cache: tuple[int, np.ndarray] | None = None
        self.version = 0  # bumped on every change; the redraw timer compares it

    def reset(self, captures: Sequence[CaptureDef] = (), fs_hz: float | None = None) -> None:
        """Start a new run with the plan's captures (their titles and settle lines)."""
        if fs_hz:
            self.fs = fs_hz
        self._captures = {c.id: c for c in captures}
        self._order, self._base, self._chunks, self._n = [], {}, [], 0
        self._cache = None
        self.version += 1

    def add(self, capture: str, start: int, x: np.ndarray) -> None:
        """Lines ``start`` .. ``start + len(x)`` of ``capture`` (settle lines first)."""
        if len(x) == 0:
            return
        if capture not in self._base:
            self._base[capture] = self._n - start
            self._order.append(capture)
        self._chunks.append(np.asarray(x, dtype=float).reshape(-1, CHANNELS))
        self._n += len(x)
        self.version += 1

    @property
    def lines(self) -> int:
        return self._n

    @property
    def latest_capture(self) -> str | None:
        return self._order[-1] if self._order else None

    def data(self) -> np.ndarray:
        """All lines so far, shape (n, 8)."""
        if self._cache is None or self._cache[0] != self._n:
            x = np.vstack(self._chunks) if self._chunks else np.empty((0, CHANNELS))
            self._cache = (self._n, x)
        return self._cache[1]

    def times(self) -> np.ndarray:
        return np.arange(self._n) / self.fs

    def last(self, seconds: float) -> tuple[np.ndarray, np.ndarray]:
        """The last ``seconds`` of the run: (times, values (n, 8))."""
        k = max(0, self._n - round(seconds * self.fs))
        return self.times()[k:], self.data()[k:]

    def spans(self) -> list[Span]:
        """The measured window of every capture that has started, clipped to the lines received."""
        out = []
        end = self._n
        for cid in self._order:
            c = self._captures.get(cid)
            settle = c.settle_lines if c else 0
            window = c.window_lines if c else end
            a = self._base[cid] + settle
            b = min(a + window, end)
            if b > a:
                title = c.title if c and c.title else cid
                out.append(Span(cid, title, bool(c and c.tone_hz is not None), a / self.fs, b / self.fs))
        return out

    def title(self, capture: str | None) -> str:
        c = self._captures.get(capture or "")
        return (c.title or c.id) if c else (capture or "")
