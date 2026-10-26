"""Order-action budget and HTTP request pacing (DESIGN.md section 4.A ``ratelimit.py``).

:class:`ActionLimiter` keeps a ledger of the mono times of granted order actions. An action is counted while
``now - t < window_s`` (61 s), so a sequence that never has more than ``cap`` actions in any 61-s window also
satisfies a fixed-minute counter, a sliding 60-s window and a token bucket.

Priority floors instead of separate buckets: a request of priority P for n actions is granted iff
``available - n >= floor(P)``, so low priorities stop early and always leave room for higher ones. KILL
ignores the floors and has an absolute ceiling, ``used + n <= max_cap + kill_overdraft``, which does not move
with the operator's ``b N`` (hardening: a lowered cap must never block the kill). The core builds the limiter
with ``max_cap = team - 10 - kill_overdraft``, so the bot alone never passes ``team - 10`` actions per window.
A result that provably never reached the exchange (``sent=False``) gives its ticket back with :meth:`refund`.
Reservations raise floors inside a time window (round 3 from 680 s: TAKE and HEDGE blocked). A 429 freezes
every priority except KILL for ``freeze_s`` and pads the ledger so TAKE stays blocked until real actions age
out of the window.

The limiter is owned by the single-threaded core; it takes ``now`` on every call and never reads a clock.
:class:`RequestLimiter` is the thread-safe 1-s window that gates every HTTP request of the REST workers.
"""
from __future__ import annotations

import bisect
import collections
import math
import threading
import time
from typing import Callable, Mapping, Sequence

from ctcbot.types import Priority, Ticket


def _prio(p: Priority | int | str) -> Priority:
    if isinstance(p, Priority):
        return p
    if isinstance(p, str):
        return Priority[p.upper()]
    return Priority(int(p))


class ActionLimiter:
    """Sliding-window order-action budget with priority floors, reservations, KILL overdraft and 429 freeze."""

    def __init__(self, cap: int, window_s: float, floors: Mapping[Priority, int], kill_overdraft: int,
                 max_cap: int) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        self.window_s: float = float(window_s)
        self.max_cap: int = max(0, int(max_cap))
        self.kill_overdraft: int = max(0, int(kill_overdraft))
        self._base: dict[Priority, int] = {p: 0 for p in Priority}
        for k, v in floors.items():
            self._base[_prio(k)] = max(0, int(v))
        self._cap = 0
        self.cap = cap
        self._ledger: list[float] = []                       # sorted mono times of counted actions
        self._kinds: list[bool] = []                         # parallel: True for KILL grants (stats only)
        self._reservations: list[tuple[float, float, dict[Priority, int]]] = []
        self._frozen_until: float = -math.inf
        self.granted: collections.Counter = collections.Counter()
        self.denied: collections.Counter = collections.Counter()
        self.throttles: int = 0
        self.padded: int = 0
        self.max_used: int = 0
        self.refunded: int = 0

    # ------------------------------------------------------------------------------------- cap
    @property
    def cap(self) -> int:
        return self._cap

    @cap.setter
    def cap(self, value: int) -> None:
        self._cap = min(self.max_cap, max(0, int(value)))

    @property
    def kill_ceiling(self) -> int:
        """Most actions KILL may bring the window to: ``max_cap + kill_overdraft``, whatever the current cap."""
        return self.max_cap + self.kill_overdraft

    # ------------------------------------------------------------------------------------- accounting
    def _prune(self, now: float) -> None:
        cut = bisect.bisect_right(self._ledger, now - self.window_s)
        if cut:
            del self._ledger[:cut]
            del self._kinds[:cut]

    def _used_at(self, t: float) -> int:
        """Actions counted at time ``t`` (entries with t - window_s < entry <= t)."""
        lo = bisect.bisect_right(self._ledger, t - self.window_s)
        hi = bisect.bisect_right(self._ledger, t)
        return hi - lo

    def used(self, now: float) -> int:
        """Granted actions still inside the window at ``now``."""
        self._prune(now)
        return self._used_at(now)

    def available(self, now: float) -> int:
        """``cap - used`` (negative while a KILL overdraft is outstanding)."""
        return self._cap - self.used(now)

    def floor(self, priority: Priority, now: float) -> int:
        """The floor of ``priority`` at ``now``: the base floor, raised by any reservation containing ``now``."""
        p = _prio(priority)
        f = self._base.get(p, 0)
        for start, end, floors in self._reservations:
            if start <= now < end and p in floors:
                f = max(f, floors[p])
        return f

    def _grantable(self, p: Priority, n: int, t: float) -> bool:
        used = self._used_at(t)
        if p is Priority.KILL:
            return used + n <= self.kill_ceiling
        if t < self._frozen_until:
            return False
        return (self._cap - used) - n >= self.floor(p, t)

    def try_acquire(self, priority: Priority, n: int, now: float, tag: str = "") -> list[Ticket]:
        """``n`` tickets if granted, else ``[]``. All-or-nothing: a denial records nothing."""
        p = _prio(priority)
        n = int(n)
        if n <= 0:
            return []
        self._prune(now)
        if not self._grantable(p, n, now):
            self.denied[p] += n
            return []
        i = bisect.bisect_right(self._ledger, now)
        self._ledger[i:i] = [now] * n
        self._kinds[i:i] = [p is Priority.KILL] * n
        self.granted[p] += n
        self.max_used = max(self.max_used, self._used_at(now))
        return [Ticket(p, now, tag) for _ in range(n)]

    def refund(self, ticket: Ticket | None, now: float) -> bool:
        """Give back the action of a request that provably never reached the exchange (``sent=False``).

        Removes one ledger entry at ``ticket.t`` if it is still in the window. Returns True when one was removed.
        """
        if ticket is None:
            return False
        try:
            t = float(ticket.t)
        except (TypeError, ValueError, AttributeError):
            return False
        self._prune(now)
        i = bisect.bisect_left(self._ledger, t)
        if i < len(self._ledger) and self._ledger[i] == t:
            del self._ledger[i]
            del self._kinds[i]
            self.refunded += 1
            return True
        return False

    def seconds_until(self, priority: Priority, n: int, now: float) -> float:
        """Seconds from ``now`` until ``try_acquire(priority, n)`` would be granted; ``inf`` if never."""
        p = _prio(priority)
        n = max(1, int(n))
        self._prune(now)
        cands = {now}
        if self._frozen_until > now and p is not Priority.KILL:
            cands.add(self._frozen_until)
        for t in self._ledger:
            e = t + self.window_s
            if e > now:
                cands.add(e)
        for start, end, _ in self._reservations:
            for e in (start, end):
                if e > now:
                    cands.add(e)
        for t in sorted(cands):
            if self._grantable(p, n, t):
                return max(0.0, t - now)
        return math.inf

    # ------------------------------------------------------------------------------------- reservations
    def reserve(self, start: float, end: float, floors: Mapping[Priority, int]) -> None:
        """Raise the floors in ``floors`` during ``[start, end)``. Overlapping reservations take the maximum."""
        if not (end > start):
            return
        self._reservations.append((float(start), float(end), {_prio(k): max(0, int(v)) for k, v in floors.items()}))
        if len(self._reservations) > 16:          # keep the newest windows; the core re-reserves at most per round
            self._reservations.sort(key=lambda r: r[1])
            del self._reservations[:-16]

    def reserved(self, now: float) -> bool:
        """True while a reservation window contains ``now``."""
        return any(start <= now < end for start, end, _ in self._reservations)

    def clear_reservations(self) -> None:
        self._reservations = []

    def reservations(self) -> list[tuple[float, float, dict[Priority, int]]]:
        """The current reservation windows (a copy)."""
        return [(s, e, dict(f)) for s, e, f in self._reservations]

    # ------------------------------------------------------------------------------------- 429 feedback
    def on_throttled(self, now: float, freeze_s: float) -> None:
        """A 429: freeze everything except KILL for ``freeze_s`` and pad the ledger with entries at ``now``
        until ``available == floor(TAKE)`` (the base TAKE floor), so only higher priorities act until real
        actions age out of the window."""
        self.throttles += 1
        self._frozen_until = max(self._frozen_until, now + float(freeze_s))
        self._prune(now)
        keep = self._base.get(Priority.TAKE, 0)
        pad = (self._cap - self._used_at(now)) - keep
        if pad > 0:
            i = bisect.bisect_right(self._ledger, now)
            self._ledger[i:i] = [now] * pad
            self._kinds[i:i] = [False] * pad
            self.padded += pad

    def frozen(self, now: float) -> bool:
        """True while a 429 freeze is in force (KILL is still granted)."""
        return now < self._frozen_until

    # ------------------------------------------------------------------------------------- persistence
    def ledger(self) -> list[float]:
        """Mono times of the counted actions (oldest first)."""
        return list(self._ledger)

    def restore(self, ledger: Sequence[float]) -> None:
        """Replace the ledger (mono times; entries outside the window simply age out)."""
        vals = sorted(float(t) for t in ledger if isinstance(t, (int, float)) and math.isfinite(t))
        self._ledger = vals
        self._kinds = [False] * len(vals)

    def stats(self, now: float) -> dict:
        used = self.used(now)
        return {
            "cap": self._cap, "max_cap": self.max_cap, "window_s": self.window_s, "used": used,
            "available": self._cap - used, "kill_in_window": sum(1 for t, k in zip(self._ledger, self._kinds)
                                                                  if k and t > now - self.window_s),
            "floors": {p.name: self.floor(p, now) for p in Priority},
            "reserved": self.reserved(now), "frozen_s": max(0.0, self._frozen_until - now),
            "granted": {p.name: int(self.granted[p]) for p in Priority},
            "denied": {p.name: int(self.denied[p]) for p in Priority},
            "granted_total": int(sum(self.granted.values())), "throttles": self.throttles,
            "padded": self.padded, "max_used": self.max_used, "kill_ceiling": self.kill_ceiling,
            "refunded": self.refunded,
        }


class RequestLimiter:
    """At most ``per_s`` HTTP requests in any rolling second, across every worker thread."""

    def __init__(self, per_s: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_s: int = max(1, int(per_s))
        self._clock = clock
        self._ev: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def wait_needed(self) -> float:
        """0.0 means go (and the request is recorded); otherwise the seconds to wait before asking again."""
        with self._lock:
            now = self._clock()
            ev = self._ev
            while ev and ev[0] <= now - 1.0:
                ev.popleft()
            if len(ev) < self.per_s:
                ev.append(now)
                return 0.0
            return max(1e-4, ev[0] + 1.0 - now)

    def acquire(self, sleep: Callable[[float], None] = time.sleep) -> float:
        """Block until a request may go (and record it); returns the seconds waited."""
        waited = 0.0
        while True:
            w = self.wait_needed()
            if w <= 0.0:
                return waited
            sleep(w)
            waited += w
