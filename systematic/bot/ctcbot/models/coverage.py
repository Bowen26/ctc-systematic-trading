"""Per-round storm observation coverage (DESIGN.md section 4.C, ``models/coverage.py``).

``StormCoverage`` watches the forwarded (merged) news stream and the released engine-relevant snapshots and
classifies each round's storm path as

* ``"missing"``: the round has no sample at all, or some stretch of its span longer than ``s1r3.long_gap_s``
  (10 s) has no position sample (an observation or a track point); every round below the first round seen is
  missing too (late join without the engine log, where the engine imputes the round's closure);
* ``"gapped"``: not missing, but some stretch longer than ``long_gap_s`` has no storm observation, so the
  intensity there is interpolated (a mid-storm join, the 31.7-s outage);
* ``"full"``: otherwise.

The span of a round is ``[0, end]``: ``end`` is the ``storm_dissipated`` age when that message was seen; else, once
the round is past its storm (a damage report, or an aftermath or closed snapshot of that round), the last
observation age; else the newest sample. S1-r3 uses the result for its coverage gate (f) and its wide epsilon.
Everything here is cheap and runs on the event-loop thread.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from ctcbot.config import Config
    from ctcbot.types import EventLog

#: Symbols whose settlement depends on closure time T (DESIGN.md section 4.C).
CLOSURE_SYMBOLS: tuple[str, ...] = ("PORT", "GRID", "FERRY", "GEN", "ARCH", "REBLD")
#: The packet's 30 market seconds per real second, used only when a round has too few timed samples.
DEFAULT_SPEED = 30.0
STORM_TYPES = frozenset({"storm", "track", "storm_formed", "storm_dissipated", "damage_report", "advisory", "impact"})
PAST_PHASES = frozenset({"aftermath", "closed"})


def as_round(v: Any) -> int | None:
    """Int-normalise a round field ("3" -> 3); anything else is None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def market_seconds(ts: Any) -> float | None:
    """Seconds since midnight of an ISO market time ("1802-05-10T10:15:00", optional fraction, zone stripped)."""
    if not isinstance(ts, str) or "T" not in ts:
        return None
    tp = ts.split("T", 1)[1]
    for sep in ("Z", "+"):
        if sep in tp:
            tp = tp.split(sep, 1)[0]
    if tp.count("-") >= 1:
        tp = tp.split("-", 1)[0]
    parts = tp.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = float(parts[2]) if len(parts) > 2 else 0.0
    except (ValueError, IndexError):
        return None
    return h * 3600.0 + m * 60.0 + s


def unwrap(msg: Any) -> dict | None:
    """The inner message of a ``{"type": "news", "message": {...}}`` frame, or the dict itself."""
    if not isinstance(msg, dict):
        return None
    inner = msg.get("message")
    if msg.get("type") == "news" and isinstance(inner, dict):
        return inner
    return msg


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


@dataclass
class RoundSamples:
    """Everything coverage needs about one round's storm."""

    obs: set[int] = field(default_factory=set)           # ages of storm observations (with intensity)
    pos: set[int] = field(default_factory=set)           # ages of position samples (observations + track points)
    timed: dict[int, float] = field(default_factory=dict)  # age -> market seconds of timed observations
    formed_ms: float | None = None                       # market seconds of storm_formed (age 0)
    dissipated_ms: float | None = None
    dissipated_seen: bool = False
    past_storm: bool = False

    def add_obs(self, age: float, ms: float | None) -> None:
        a = int(round(age))
        self.obs.add(a)
        self.pos.add(a)
        if ms is not None:
            self.timed[a] = ms

    def add_points(self, points: Any) -> None:
        if not isinstance(points, list):
            return
        for p in points:
            if isinstance(p, (list, tuple)) and p:
                a = _num(p[0])
                if a is not None and a >= 0:
                    self.pos.add(int(round(a)))

    def speed(self) -> float:
        """Market seconds per real second of storm age, from the timed observations."""
        if len(self.timed) >= 2:
            a0, a1 = min(self.timed), max(self.timed)
            if a1 > a0:
                sp = (self.timed[a1] - self.timed[a0]) / (a1 - a0)
                if sp > 0:
                    return sp
        return DEFAULT_SPEED

    def dissipated_age(self) -> float | None:
        """Storm age of the ``storm_dissipated`` message, from its market time and the round's time references."""
        if self.dissipated_ms is None:
            return None
        sp = self.speed()
        if self.timed:
            a_ref = max(self.timed)
            return a_ref + (self.dissipated_ms - self.timed[a_ref]) / sp
        if self.formed_ms is not None:
            return (self.dissipated_ms - self.formed_ms) / sp
        return None

    def end(self) -> float | None:
        """End of the span ``[0, end]`` (see the module docstring), or None without any sample."""
        if not self.pos and not self.obs:
            return None
        if self.dissipated_seen:
            d = self.dissipated_age()
            if d is not None:
                return max(0.0, d)
        if self.dissipated_seen or self.past_storm:
            return float(max(self.obs)) if self.obs else float(max(self.pos))
        return float(max(self.pos | self.obs))


def max_gap(ages: Iterable[int], end: float) -> float:
    """Longest stretch of ``[0, end]`` without a sample (the whole span when there is none)."""
    pts = sorted(a for a in ages if 0 <= a <= end)
    if not pts:
        return end
    gap = float(pts[0])
    for a, b in zip(pts, pts[1:]):
        gap = max(gap, float(b - a))
    return max(gap, end - pts[-1])


class StormCoverage:
    """Per-round observation coverage of the merged stream (see the module docstring)."""

    def __init__(self, cfg: "Config", log: "EventLog") -> None:
        self.cfg = cfg
        self.log = log
        self.long_gap_s = float(cfg.get("strategies.s1r3.long_gap_s", 10.0))
        self.reset()

    def reset(self) -> None:
        """Forget everything (a new season)."""
        self.rounds: dict[int, RoundSamples] = {}
        self.first_round: int | None = None
        self.max_round: int | None = None
        self._last: dict[int, str] = {}

    # ------------------------------------------------------------------ ingest
    def _rec(self, r: int) -> RoundSamples:
        rec = self.rounds.get(r)
        if rec is None:
            rec = self.rounds[r] = RoundSamples()
        return rec

    def _see(self, r: int) -> None:
        if r < 1:
            return
        self.first_round = r if self.first_round is None else min(self.first_round, r)
        self.max_round = r if self.max_round is None else max(self.max_round, r)

    def on_news(self, msg: dict) -> None:
        """Ingest one forwarded news message (storm, track, storm_formed, storm_dissipated, damage_report, ...)."""
        m = unwrap(msg)
        if m is None:
            return
        t = m.get("type")
        if t == "snapshot":
            self.on_snapshot(m)
            return
        if t not in STORM_TYPES:
            return
        r = as_round(m.get("round"))
        if r is None or r < 1:
            return
        self._see(r)
        rec = self._rec(r)
        ms = market_seconds(m.get("time"))
        if t == "storm":
            age = _num(m.get("age"))
            if age is not None and age >= 0:
                rec.add_obs(age, ms)
        elif t == "track":
            rec.add_points(m.get("points"))
        elif t == "storm_formed":
            if ms is not None and rec.formed_ms is None:
                rec.formed_ms = ms
        elif t == "storm_dissipated":
            rec.dissipated_seen = True
            if ms is not None:
                rec.dissipated_ms = ms
        elif t == "damage_report":
            rec.past_storm = True

    def on_snapshot(self, msg: dict) -> None:
        """Ingest one released snapshot: its obs, track, round, phase and damage list."""
        m = unwrap(msg)
        if m is None:
            return
        r = as_round(m.get("round"))
        for rep in m.get("damage") or []:
            if isinstance(rep, dict):
                rr = as_round(rep.get("round"))
                if rr is not None and rr >= 1:
                    self._rec(rr).past_storm = True
        if r is None or r < 1:
            return
        self._see(r)
        rec = self._rec(r)
        o = m.get("obs")
        if isinstance(o, dict):
            age = _num(o.get("age"))
            if age is not None and age >= 0:
                rec.add_obs(age, market_seconds(o.get("time")))
        rec.add_points(m.get("track"))
        if m.get("phase") in PAST_PHASES:
            rec.past_storm = True

    def rebuild(self, messages: Iterable[dict]) -> None:
        """Reset, then replay messages (an engine log: news messages and snapshots, in order)."""
        self.reset()
        for m in messages:
            mm = unwrap(m)
            if mm is None:
                continue
            if mm.get("type") == "snapshot":
                self.on_snapshot(mm)
            else:
                self.on_news(mm)

    # ------------------------------------------------------------------ result
    def round_status(self, r: int) -> str:
        """"full", "gapped" or "missing" for one round."""
        if self.first_round is None or r < self.first_round:
            return "missing"
        rec = self.rounds.get(r)
        if rec is None:
            return "missing"
        end = rec.end()
        if end is None:
            return "missing"
        if max_gap(rec.pos | rec.obs, end) > self.long_gap_s:
            return "missing"
        if max_gap(rec.obs, end) > self.long_gap_s:
            return "gapped"
        return "full"

    def coverage(self) -> dict[int, str]:
        """Round -> "full" | "gapped" | "missing", for rounds 1 .. highest seen."""
        if self.max_round is None:
            return {}
        out = {r: self.round_status(r) for r in range(1, self.max_round + 1)}
        if out != self._last:
            changed = {r: s for r, s in out.items() if self._last.get(r) != s}
            self._last = dict(out)
            try:
                self.log.event("coverage", changed=changed, coverage=out)
            except Exception:  # noqa: BLE001 - logging must never break the caller
                pass
        return out

    def any_round(self, status: str, rounds: Iterable[int] = (1, 2, 3)) -> bool:
        """True when any of ``rounds`` has ``status`` (rounds not seen yet count as missing)."""
        cov = self.coverage()
        return any(cov.get(r, "missing") == status for r in rounds)
