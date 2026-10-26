"""Order-action budget: sliding-window limiter with priority floors.

*** UNTESTED SKELETON (logic is exchange-independent and is exercised offline by replay.py). ***

Rules being enforced (packet section 8):
  * 180 order actions per minute per TEAM (all bot keys + browser share it); 300/min per key is moot.
  * An order action = place, cancel, cancel-all, ETF create, ETF redeem.
  * 600 HTTP requests per second per team for the whole API (GETs included).

Design
  * A ledger of action timestamps over the last LIMITER_WINDOW_S (61 s). A sequence that never has
    more than CAP actions inside any 61-s window satisfies a fixed-minute counter, a sliding
    60-s window and a 180-token / 3-per-second bucket, so we do not need to know which one the
    exchange uses (ask anyway).
  * Priority floors instead of separate buckets: a request of priority P for n actions is granted
    only if available - n >= FLOOR[P]. Low priorities therefore stop early and always leave room
    for higher ones. With floors {KILL 0, SNIPE 2, HEDGE 12, TAKE 35, QUOTE 70}, at least 35
    actions are always free for snipes and hedges (a full 12-symbol snipe is 13 actions).
  * Scheduled reservations: before a scheduled event (prelim / final damage report) the QUOTE
    floor is raised to CAP (quote refresh stops) and the TAKE floor is raised, so the window
    drains and the snipe has the full budget.
  * Every action is charged when it is SENT (failures, timeouts and rejects included).
    Exchange-initiated cancels (limit room, self-trade prevention) cost nothing.
  * One process owns the budget. If the team runs more than one bot, give each a static share
    of the cap (e.g. 110 + 40 + 15 for humans + margin); do not try to coordinate dynamically.
"""
import collections
import enum
import time


class Priority(enum.IntEnum):
    KILL = 0      # kill switch, cancel-all in an emergency
    SNIPE = 1     # damage reports, advisories, announcements, final-40-s settlement trades
    HEDGE = 2     # risk-reducing: complete an ETF leg, pull a quote that fair moved through
    TAKE = 3      # continuous fundamental taker
    QUOTE = 4     # passive quote refresh


class Ticket:
    """Proof that one order action was budgeted. rest.py refuses order actions without one."""
    __slots__ = ("priority", "t", "tag", "used")

    def __init__(self, priority, t, tag):
        self.priority, self.t, self.tag, self.used = priority, t, tag, False

    def consume(self):
        if self.used:
            raise RuntimeError("ticket already used")
        self.used = True


class ActionLimiter:
    def __init__(self, cap, window_s, floors, clock=time.monotonic):
        self.cap = int(cap)
        self.window = float(window_s)
        self.base_floors = {Priority(k): int(v) for k, v in floors.items()}
        self.clock = clock
        self._events = collections.deque()
        self._reservations = []          # (start, end, {priority: floor})
        self._frozen_until = 0.0
        self.granted = collections.Counter()
        self.denied = collections.Counter()

    # -- accounting ------------------------------------------------------------------------
    def _purge(self, now):
        cutoff = now - self.window
        ev = self._events
        while ev and ev[0] <= cutoff:
            ev.popleft()

    def used(self, now=None):
        now = self.clock() if now is None else now
        self._purge(now)
        return len(self._events)

    def available(self, now=None):
        return self.cap - self.used(now)

    def floor(self, priority, now=None):
        now = self.clock() if now is None else now
        f = self.base_floors[Priority(priority)]
        for start, end, floors in self._reservations:
            if start <= now < end and priority in floors:
                f = max(f, floors[priority])
        return f

    # -- requests ---------------------------------------------------------------------------
    def try_acquire(self, priority, n=1, tag="", allow_partial=False):
        """Return a list of Tickets (len n, or fewer if allow_partial) or [] if denied."""
        now = self.clock()
        priority = Priority(priority)
        if now < self._frozen_until and priority != Priority.KILL:
            self.denied[priority] += n
            return []
        avail = self.available(now)
        room = avail - self.floor(priority, now)
        k = n if room >= n else (max(0, room) if allow_partial else 0)
        if k <= 0:
            self.denied[priority] += n
            return []
        for _ in range(k):
            self._events.append(now)
        self.granted[priority] += k
        if k < n:
            self.denied[priority] += n - k
        return [Ticket(priority, now, tag) for _ in range(k)]

    def charge_external(self, n=1):
        """Charge actions we did not budget (e.g. a human using the bot's console)."""
        now = self.clock()
        for _ in range(n):
            self._events.append(now)

    def seconds_until(self, priority, n=1):
        """Earliest time (s from now) at which n actions of `priority` would be granted."""
        now = self.clock()
        need = n + self.floor(priority, now) - self.available(now)
        if need <= 0:
            return 0.0
        if need > len(self._events):
            return float("inf")
        return max(0.0, self._events[need - 1] + self.window - now)

    # -- schedule / feedback ------------------------------------------------------------------
    def reserve(self, start, end, floors):
        """Raise floors for priorities in [start, end) (monotonic clock seconds)."""
        self._reservations.append((start, end, {Priority(k): v for k, v in floors.items()}))
        now = self.clock()
        self._reservations = [r for r in self._reservations if r[1] > now]

    def clear_reservations(self):
        self._reservations = []

    def on_throttled(self, retry_after_s=5.0):
        """Server said 429 / rate limited: freeze non-KILL work for retry_after_s, then assume our
        ledger under-counted: pad it so only SNIPE/HEDGE may act until the padding expires."""
        now = self.clock()
        self._frozen_until = max(self._frozen_until, now + retry_after_s)
        self._purge(now)
        keep_free = self.base_floors[Priority.TAKE]
        while self.cap - len(self._events) > keep_free:
            self._events.append(now)

    def stats(self):
        return {"used": self.used(), "cap": self.cap, "granted": dict(self.granted), "denied": dict(self.denied)}


class RequestLimiter:
    """All HTTP requests (GET included): simple 1-s sliding window. Returns the wait in seconds."""

    def __init__(self, per_s, clock=time.monotonic):
        self.per_s = int(per_s)
        self.clock = clock
        self._ev = collections.deque()

    def wait_needed(self):
        now = self.clock()
        while self._ev and self._ev[0] <= now - 1.0:
            self._ev.popleft()
        if len(self._ev) < self.per_s:
            self._ev.append(now)
            return 0.0
        return self._ev[0] + 1.0 - now
