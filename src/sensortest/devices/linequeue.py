"""A thread-safe queue of timestamped lines, one per driver subscribed to a link (D-049).

The link's reader thread puts lines in; a driver takes them out. It is bounded, so a driver that
stops reading can never grow memory without limit: the oldest line is dropped and counted.
``wake()`` lets the link interrupt a waiting driver at once (device lost, link closed).
"""

import threading
from collections import deque

Line = tuple[float, bytes]  # (host time from time.perf_counter(), raw line including b"\n")


class LineQueue:
    def __init__(self, maxlen: int = 4096) -> None:
        self._lines: deque[Line] = deque()
        self._maxlen = maxlen
        self._cv = threading.Condition()
        self.dropped = 0  # lines lost because the queue was full

    def put(self, t: float, line: bytes) -> None:
        with self._cv:
            if len(self._lines) >= self._maxlen:
                self._lines.popleft()
                self.dropped += 1
            self._lines.append((t, line))
            self._cv.notify_all()

    def get(self, timeout: float) -> Line | None:
        """The oldest line, waiting up to ``timeout`` s; None if nothing came (or on wake())."""
        with self._cv:
            if not self._lines:
                self._cv.wait(timeout)
            return self._lines.popleft() if self._lines else None

    def discard_before(self, t: float) -> int:
        """Drop lines received before host time ``t``. Returns how many were dropped."""
        n = 0
        with self._cv:
            while self._lines and self._lines[0][0] < t:
                self._lines.popleft()
                n += 1
        return n

    def clear(self) -> None:
        with self._cv:
            self._lines.clear()

    def wake(self) -> None:
        with self._cv:
            self._cv.notify_all()
