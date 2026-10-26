"""Testnet-recording market for the simulator (DESIGN.md section 4.E, "Fills, recorded mode").

``RecordedMarket`` replays a ``record_feed.py`` recording (one JSON frame per line, each with ``received_at``):

* **Frames** in file order at virtual time ``t = received_at - first_received_at``, as ``MarketFrame``s with
  ``conn = "rec#k"``. Like ``ctcbot.sources.RecordingSource``, every ``subscription_ack`` after the first becomes a
  ``"down"`` then ``"up"`` control pair that bumps k (the full recording has 6 acks, so 5 pairs); the ack itself is
  passed through after the pair. An initial ``"up"`` for ``rec#1`` precedes the first frame (``initial_up``), as a
  live connection emits one after its subscribe.
* **Round opens** for ``/competitions/time``: per market date, the minimum over storm observations of
  ``received_at - t(market_time)``, refined with trading-phase snapshots whose market clock is visibly advancing
  (a snapshot with the same time as an earlier one is a paused clock and is never used). A date with no storm
  observation (season 226's pre-storm) is fitted from those snapshots alone. The server state is "running" in
  ``[open, open + round_len)``.
* **Seasons**: each market date maps to a research season and round through ``HISTORY/manifest.json`` (the round
  ``open`` dates); the comp_id is ``rec-<season>``. The comp_id switches at the first trading-phase frame of the
  new season (the season's first snapshot, usually ``last_seq`` -1).
* **Settlement** comes from ``HISTORY/season-NNN/settlement.json`` for a season whose round 3 closes inside the
  recording; a partial season is marked at the last mid (``settlement()`` returns None).
* **Books**: every ``orderbook`` frame per symbol, for the recorded-book fill model of ``SimExchange``.

Recordings are untrusted data: malformed lines are skipped and counted in ``stats``.
"""
from __future__ import annotations

import bisect
import json
import os
from typing import TYPE_CHECKING, Any, Iterator

from ctcbot.types import SYMBOLS

from sim.research_market import (MarketBase, MarketFrame, RoundSpec, TRADING_PHASES, case_parts, default_schedule,
                                 load_case_dict, market_to_t)

if TYPE_CHECKING:  # pragma: no cover
    from ctcbot.case import Schedule
    from ctcbot.config import Config

__all__ = ["RecordedMarket", "BookFrame"]


class BookFrame:
    """One recorded order book: levels as ``(price, qty)`` tuples, best first."""

    __slots__ = ("t", "idx", "bids", "asks", "timestamp")

    def __init__(self, t: float, idx: int, bids: tuple[tuple[float, int], ...], asks: tuple[tuple[float, int], ...],
                 timestamp: Any) -> None:
        self.t, self.idx, self.bids, self.asks, self.timestamp = t, idx, bids, asks, timestamp

    @property
    def mid(self) -> float | None:
        if self.bids and self.asks:
            return (self.bids[0][0] + self.asks[0][0]) / 2.0
        return None


def _levels(raw: Any) -> tuple[tuple[float, int], ...]:
    out = []
    if isinstance(raw, list):
        for lv in raw:
            if not isinstance(lv, dict):
                continue
            p, q = lv.get("price"), lv.get("quantity")
            if isinstance(p, (int, float)) and isinstance(q, (int, float)) and not isinstance(p, bool) and q > 0 \
                    and p > 0:
                out.append((float(p), int(q)))
    return tuple(out)


def _dated(m: dict) -> tuple[int, str] | None:
    """(round, market date) of a trading-phase news message or snapshot, else None (closed snapshots are undated)."""
    try:
        r = int(m.get("round"))
    except (TypeError, ValueError):
        return None
    t = m.get("time")
    if not isinstance(t, str) or len(t) < 10:
        return None
    if m.get("type") == "snapshot" and m.get("phase") not in TRADING_PHASES:
        return None
    return r, t[:10]


class RecordedMarket(MarketBase):
    """A testnet recording on the virtual timeline (see the module docstring).

    ``RecordedMarket(path, cfg, history=None, case=None, schedule=None, t_from=None, t_to=None)``; ``t_from`` and
    ``t_to`` (unix ``received_at``) restrict the frames that ``frames()`` emits, while round fitting, books and
    seasons always use the whole file.
    """

    kind = "recorded"

    def __init__(self, path: str, cfg: "Config", history: str | None = None, *, case: Any = None,
                 schedule: "Schedule | None" = None, t_from: float | None = None, t_to: float | None = None,
                 initial_up: bool = True) -> None:
        self.path = path
        self.cfg = cfg
        self.history = history
        self.case_raw, self.limits, self.baskets = case_parts(load_case_dict(cfg, case))
        self.schedule = schedule if schedule is not None else default_schedule(cfg, self.case_raw)
        self.round_len = float(self.schedule.close)
        self.t_from = t_from
        self.t_to = t_to
        self.initial_up = bool(initial_up)
        self.stats = {"lines": 0, "bad_lines": 0, "frames": 0, "acks": 0, "books": 0}
        self.first_received_at: float | None = None
        self.last_t = 0.0
        self._times: list[float] = []          # virtual time of every valid frame, file order
        self.books: dict[str, list[BookFrame]] = {s: [] for s in SYMBOLS}
        self._book_t: dict[str, list[float]] = {s: [] for s in SYMBOLS}
        self._settle_cache: dict[str, dict[str, float] | None] = {}
        self._scan()

    # -- loading
    def _iter_file(self) -> Iterator[tuple[float, dict]]:
        """(received_at, frame without received_at) per valid line, in file order."""
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    fr = json.loads(line)
                except ValueError:
                    self.stats["bad_lines"] += 1
                    continue
                if not isinstance(fr, dict):
                    self.stats["bad_lines"] += 1
                    continue
                ra = fr.pop("received_at", None)
                if not isinstance(ra, (int, float)) or isinstance(ra, bool):
                    self.stats["bad_lines"] += 1
                    continue
                yield float(ra), fr

    def _manifest(self) -> dict[str, tuple[str, int]]:
        """market date -> (season label, round) from HISTORY/manifest.json (empty without a history dir)."""
        out: dict[str, tuple[str, int]] = {}
        if not self.history:
            return out
        man = os.path.join(self.history, "manifest.json")
        if not os.path.exists(man):
            return out
        with open(man, encoding="utf-8") as f:
            d = json.load(f)
        for s in d.get("seasons", []) if isinstance(d, dict) else []:
            try:
                n = int(s["season"])
            except (KeyError, TypeError, ValueError):
                continue
            for st in s.get("storms", []) or []:
                op = st.get("open") if isinstance(st, dict) else None
                if isinstance(op, str) and len(op) >= 10:
                    out[op[:10]] = (str(n), int(st.get("round", 0) or 0))
        return out

    def _scan(self) -> None:
        speed = float(self.schedule.speed)
        storm_fit: dict[str, float] = {}
        snap_fit: dict[str, float] = {}
        snap_prev: dict[str, float] = {}
        date_round: dict[str, int] = {}
        date_storm: dict[str, str] = {}
        first_seen: dict[str, float] = {}       # date -> unix receipt of its first trading-phase frame
        reports: list[tuple[float, str, int, str, int | None]] = []
        n = 0
        for ra, fr in self._iter_file():
            self.stats["lines"] += 1
            if self.first_received_at is None:
                self.first_received_at = ra
            t = ra - self.first_received_at
            self.last_t = max(self.last_t, t)
            self._times.append(t)
            typ = fr.get("type")
            if typ == "orderbook":
                sym = fr.get("symbol")
                if sym in self.books:
                    bf = BookFrame(t, n, _levels(fr.get("bids")), _levels(fr.get("asks")), fr.get("timestamp"))
                    self.books[sym].append(bf)
                    self._book_t[sym].append(t)
                    self.stats["books"] += 1
            elif typ == "subscription_ack":
                self.stats["acks"] += 1
            elif typ == "news" and isinstance(fr.get("message"), dict):
                m = fr["message"]
                dr = _dated(m)
                if dr is not None:
                    r, date = dr
                    date_round.setdefault(date, r)
                    if m.get("storm"):
                        date_storm.setdefault(date, str(m.get("storm")))
                    first_seen.setdefault(date, ra)
                    if m.get("type") == "damage_report":
                        seq = m.get("seq")
                        reports.append((t, date, r, str(m.get("stage")),
                                        seq if isinstance(seq, int) and not isinstance(seq, bool) else None))
                    try:
                        _, tau = market_to_t(str(m["time"]), speed)
                    except (KeyError, ValueError):
                        tau = None
                    if tau is not None:
                        if m.get("type") == "storm":
                            off = ra - tau
                            storm_fit[date] = min(storm_fit.get(date, off), off)
                        elif m.get("type") == "snapshot":
                            prev = snap_prev.get(date)
                            if prev is not None and tau > prev + 1e-9:
                                off = ra - tau
                                snap_fit[date] = min(snap_fit.get(date, off), off)
                            snap_prev[date] = tau if prev is None else max(prev, tau)
            n += 1
        self.stats["frames"] = n
        if self.first_received_at is None:
            raise ValueError(f"recording {self.path} has no valid frames")
        manifest = self._manifest()
        opens: list[tuple[float, str]] = []
        for date in date_round:
            cands = [x for x in (storm_fit.get(date), snap_fit.get(date)) if x is not None]
            if cands:
                opens.append((min(cands), date))
        opens.sort()
        self.rounds = []
        self.unix_opens: dict[str, float] = {}
        for k, (op, date) in enumerate(opens):
            season, mround = manifest.get(date, (f"unknown-{date}", date_round[date]))
            self.unix_opens[date] = op
            t_open = op - self.first_received_at
            self.rounds.append(RoundSpec(index=k, season=season, comp_id=f"rec-{season}", round=date_round[date],
                                         date=date, open_t=t_open, close_t=t_open + self.round_len,
                                         storm=date_storm.get(date)))
        # season switches: first trading-phase frame of each season
        seen: dict[str, float] = {}
        for date, ra in first_seen.items():
            season = manifest.get(date, (f"unknown-{date}", 0))[0]
            seen[season] = min(seen.get(season, ra), ra)
        order = sorted(seen.items(), key=lambda kv: kv[1])
        self._season_first: list[tuple[float, str]] = [(ra - self.first_received_at, s) for s, ra in order]
        if not self._season_first and self.rounds:
            self._season_first = [(0.0, self.rounds[0].season)]
        # damage reports as received: (virtual t, season, round, stage, seq)
        self.reports: list[tuple[float, str, int, str, int | None]] = [
            (t, manifest.get(date, (f"unknown-{date}", 0))[0], r, stage, seq) for t, date, r, stage, seq in reports]

    # -- timeline
    def frames(self) -> Iterator[MarketFrame]:
        """Frames in file order with the reconnect control pairs (see the module docstring)."""
        assert self.first_received_at is not None
        k = 1
        acks = 0
        started = False
        for ra, fr in self._iter_file():
            if self.t_from is not None and ra < self.t_from:
                if fr.get("type") == "subscription_ack":
                    acks += 1
                    if acks > 1:
                        k += 1
                continue
            if self.t_to is not None and ra >= self.t_to:
                break
            t = ra - self.first_received_at
            if not started:
                started = True
                if self.initial_up:
                    yield MarketFrame(t, {}, f"rec#{k}", control="up", recv_wall=ra)
            if fr.get("type") == "subscription_ack":
                acks += 1
                if acks > 1:
                    yield MarketFrame(t, {}, f"rec#{k}", control="down", recv_wall=ra)
                    k += 1
                    yield MarketFrame(t, {}, f"rec#{k}", control="up", recv_wall=ra)
            yield MarketFrame(t, fr, f"rec#{k}", recv_wall=ra)

    def default_t_end(self) -> float:
        """The last frame inside the ``t_from`` / ``t_to`` window plus 2 s."""
        assert self.first_received_at is not None
        ts = self._times
        hi = len(ts) if self.t_to is None else bisect.bisect_left(ts, self.t_to - self.first_received_at)
        lo = 0 if self.t_from is None else bisect.bisect_left(ts, self.t_from - self.first_received_at)
        last = ts[hi - 1] if hi > lo else (ts[lo] if lo < len(ts) else self.last_t)
        return last + 2.0

    def to_virtual(self, unix: float) -> float:
        """Virtual time of a unix ``received_at``."""
        assert self.first_received_at is not None
        return float(unix) - self.first_received_at

    # -- seasons
    def season_at(self, t: float) -> str:
        cur = self._season_first[0][1] if self._season_first else "unknown"
        for ts, s in self._season_first:
            if ts <= t + 1e-9:
                cur = s
        return cur

    def comp_id_at(self, t: float) -> str:
        return f"rec-{self.season_at(t)}"

    def season_switches(self) -> list[tuple[float, str]]:
        return list(self._season_first[1:])

    def seasons(self) -> list[str]:
        return [s for _, s in self._season_first]

    def settlement(self, season: str) -> dict[str, float] | None:
        season = str(season)
        if season in self._settle_cache:
            return self._settle_cache[season]
        out = None
        complete = any(rs.season == season and rs.round == 3 and rs.close_t <= self.last_t for rs in self.rounds)
        if complete and self.history and season.isdigit():
            p = os.path.join(self.history, f"season-{int(season):03d}", "settlement.json")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                try:
                    out = {s: float(d[s]) for s in SYMBOLS}
                except (KeyError, TypeError, ValueError):
                    out = None
        self._settle_cache[season] = out
        return out

    # -- books and marks
    def book_at(self, symbol: str, t: float) -> BookFrame | None:
        """Latest recorded order book of ``symbol`` with receipt time <= t."""
        ts = self._book_t.get(symbol)
        if not ts:
            return None
        k = bisect.bisect_right(ts, t + 1e-12) - 1
        return self.books[symbol][k] if k >= 0 else None

    def books_between(self, t0: float, t1: float) -> list[tuple[float, int, str, BookFrame]]:
        """Every book frame with ``t0 < t <= t1`` as (t, file index, symbol, frame), in time order."""
        out = []
        for s in SYMBOLS:
            ts = self._book_t[s]
            a = bisect.bisect_right(ts, t0 + 1e-12)
            b = bisect.bisect_right(ts, t1 + 1e-12)
            for bf in self.books[s][a:b]:
                out.append((bf.t, bf.idx, s, bf))
        out.sort(key=lambda x: (x[0], x[1]))
        return out

    def mark(self, symbol: str, t: float) -> float | None:
        ts = self._book_t.get(symbol) or []
        k = bisect.bisect_right(ts, t + 1e-12) - 1
        while k >= 0:
            m = self.books[symbol][k].mid
            if m is not None:
                return m
            k -= 1
        return None

    def book(self, symbol: str, t: float) -> dict:
        bf = self.book_at(symbol, t)
        if bf is None:
            return {"symbol": symbol, "bids": [], "asks": [], "timestamp": None}
        return {"symbol": symbol, "bids": [{"price": p, "quantity": q} for p, q in bf.bids],
                "asks": [{"price": p, "quantity": q} for p, q in bf.asks], "timestamp": bf.timestamp}
