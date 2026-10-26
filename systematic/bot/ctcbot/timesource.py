"""Time sources (DESIGN.md section 4.A ``timesource.py``).

Every component reads time through a :class:`ctcbot.types.TimeSource`: ``mono()`` for intervals and
deadlines, ``wall()`` for unix timestamps in logs and saved state.

- :class:`RealTime` is the production clock.
- :class:`ScaledTime` runs ``speed`` times faster than real time from ``start``; shell replays use it so a
  recording can be replayed at, say, 50x through the real asyncio shell.
- :class:`VirtualTime` is set explicitly by the discrete-event simulator; it never moves by itself.
"""
from __future__ import annotations

import math
import time


class RealTime:
    """``time.monotonic`` and ``time.time``."""

    speed: float = 1.0

    def mono(self) -> float:
        return time.monotonic()

    def wall(self) -> float:
        return time.time()

    def __repr__(self) -> str:
        return "RealTime()"


class ScaledTime:
    """A clock that runs ``speed`` times faster than real time.

    ``mono = start + (time.monotonic() - t0) * speed`` and ``wall = start + (time.time() - w0) * speed``,
    where ``t0`` and ``w0`` are read at construction.
    """

    def __init__(self, speed: float, start: float = 0.0) -> None:
        if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not math.isfinite(speed) or speed <= 0:
            raise ValueError(f"ScaledTime speed must be a positive number, got {speed!r}")
        self.speed: float = float(speed)
        self.start: float = float(start)
        self._t0 = time.monotonic()
        self._w0 = time.time()

    def mono(self) -> float:
        return self.start + (time.monotonic() - self._t0) * self.speed

    def wall(self) -> float:
        return self.start + (time.time() - self._w0) * self.speed

    def real_seconds(self, dt: float) -> float:
        """Real seconds that pass while this clock advances by ``dt``."""
        return dt / self.speed

    def __repr__(self) -> str:
        return f"ScaledTime(speed={self.speed}, start={self.start})"


class VirtualTime:
    """A clock set by the simulator: ``mono = t`` and ``wall = wall0 + t``."""

    speed: float = 1.0

    def __init__(self, start: float = 0.0, wall0: float = 1.8e9) -> None:
        self.t: float = float(start)
        self.wall0: float = float(wall0)

    def set(self, t: float) -> None:
        """Jump to ``t``. The simulator is responsible for keeping time monotone."""
        self.t = float(t)

    def advance(self, dt: float) -> None:
        """Move by ``dt`` seconds."""
        self.t += float(dt)

    def mono(self) -> float:
        return self.t

    def wall(self) -> float:
        return self.wall0 + self.t

    def __repr__(self) -> str:
        return f"VirtualTime(t={self.t})"
