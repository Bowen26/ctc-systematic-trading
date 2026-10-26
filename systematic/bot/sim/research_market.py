"""Research-season market for the simulator (DESIGN.md section 4.E, "Fills, research mode").

``ResearchMarket`` turns one research season (``HISTORY/season-NNN/{feed.jsonl, market.csv, settlement.json}``)
into the stream a live connection would deliver, in virtual time:

* **Rounds** open at ``T_r = (r - 1) * (round_len + sim.pause_between_rounds_s)``; the server state is "running"
  only within ``[T_r, T_r + round_len)``.
* **Market data**: at each consensus row time ``T_r + 2i`` a ``quote`` and an ``orderbook`` frame per symbol. The
  touch is ``rows[i] * (1 -+ cost.half_spread_bp(sym, ph) / 1e4)``; the five levels sit at the cumulative
  ``sim.book_levels_x`` and are priced at the walk cost of their slice of the book (the average marginal walk
  cost ``walk_increment`` over the slice, so walking the synthetic book to a level boundary costs exactly
  ``walk_bp`` there), with ``qty = L * dx``. Published prices are rounded to 2 decimals (bids down, asks up);
  the fill model uses the unrounded touch.
* **News**: every ``feed.jsonl`` message at ``T_r + market_to_t(time)``, in seq order, wrapped as
  ``{"type": "news", "message": m}``. ``drop_seqs`` removes messages from delivery (chaos), but the server-side
  snapshot state still sees them.
* **Snapshots** (testnet schema, built from everything the server has published): ``snapshots="all"`` (default)
  emits one every 30 s in every phase, as the testnet exchange does (median spacing 30.11 s); during pauses they
  carry phase "closed". ``"pause"`` emits only the closed ones during pauses (the literal section 4.E timeline),
  ``"none"`` none. Without drops no snapshot is engine-relevant, so they only feed the clock and the phase.

The module also defines the types both markets share (``MarketFrame``, ``RoundSpec``, ``MarketBase``) and the
testnet-schema ``SnapshotBuilder``. It imports only ``ctcbot.types`` at module level; the default ``Schedule`` is
built lazily from ``ctcbot.case`` when the caller does not pass one.
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import math
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Mapping, Sequence

from ctcbot.types import SYMBOLS, Side

from sim.fillmodel import FillParams, walk_increment

if TYPE_CHECKING:  # pragma: no cover
    from ctcbot.case import Schedule
    from ctcbot.config import Config
    from ctcbot.models.cost_model import CostModel

__all__ = ["MarketFrame", "RoundSpec", "MarketBase", "SnapshotBuilder", "ResearchMarket", "case_parts",
           "market_time_iso", "market_to_t", "load_case_dict", "default_schedule", "WALL0_ISO"]

TRADING_PHASES = ("pre_storm", "storm", "aftermath")
_SYM_INDEX = {s: k for k, s in enumerate(SYMBOLS)}
WALL0_ISO = "2026-11-10T10:00:00"   # base of the synthetic exchange timestamps (no DST change nearby)
_WALL0 = _dt.datetime.fromisoformat(WALL0_ISO)


# ----------------------------------------------------------------------------------------------- shared types
@dataclass(frozen=True, slots=True)
class MarketFrame:
    """One frame of the simulated stream at virtual time ``t`` (``control`` "up"/"down" for connection changes)."""

    t: float
    frame: dict
    conn: str
    control: str | None = None
    recv_wall: float | None = None


@dataclass(frozen=True)
class RoundSpec:
    """One market round on the virtual timeline."""

    index: int            # position in the timeline (0-based)
    season: str           # research season label, e.g. "401"
    comp_id: str          # what /competitions/time reports while this round's season is current
    round: int            # 1..3
    date: str             # market date "YYYY-MM-DD"
    open_t: float         # virtual time of the 09:30 open
    close_t: float        # open_t + round_len
    storm: str | None = None


def market_to_t(market_time: str, speed: float) -> tuple[str, float]:
    """(date, real seconds since 09:30) of an ISO market time (a trailing Z or offset is ignored)."""
    s = str(market_time).strip()
    if s.endswith("Z"):
        s = s[:-1]
    if len(s) > 19 and ("+" in s[19:] or "-" in s[19:]):
        cut = max(s.rfind("+", 19), s.rfind("-", 19))
        s = s[:cut]
    d = _dt.datetime.fromisoformat(s)
    sec = d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6
    return d.date().isoformat(), (sec - 34200.0) / float(speed)


def market_time_iso(date: str, tau: float, speed: float, truncate: bool = False) -> str:
    """ISO market time of round second ``tau`` on ``date`` (09:30 + speed * tau); ``truncate`` drops sub-seconds
    of the real clock (snapshot times have 1-real-second resolution)."""
    if truncate:
        tau = math.floor(tau + 1e-9)
    base = _dt.datetime.fromisoformat(f"{date}T09:30:00")
    out = base + _dt.timedelta(seconds=speed * tau)
    return out.isoformat(timespec="microseconds") if out.microsecond else out.isoformat(timespec="seconds")


def wall_iso(t: float) -> str:
    """Synthetic exchange timestamp for virtual time ``t`` (strictly increasing in t)."""
    return (_WALL0 + _dt.timedelta(seconds=t)).isoformat(timespec="microseconds")


def load_case_dict(cfg: Any, case: Any = None) -> dict:
    """The case as a plain dict: ``case`` (a dict or a CaseInfo) or the research case named in the config."""
    if case is not None:
        raw = getattr(case, "raw", case)
        if not isinstance(raw, dict):
            raise TypeError("case must be a dict or a CaseInfo")
        return raw
    path = cfg.path(cfg.get("paths.research_case"))
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"case file {path} is not a JSON object")
    return raw


def case_parts(case: Any) -> tuple[dict, dict[str, int], dict[str, dict[str, int]]]:
    """(raw dict, limits, ETF baskets) from a case dict or a ``CaseInfo``."""
    raw = getattr(case, "raw", case)
    limits = getattr(case, "limits", None)
    baskets = getattr(case, "baskets", None)
    if limits is None:
        limits = {s: int(v) for s, v in (raw.get("limits") or {}).items()}
    if baskets is None:
        baskets = {e: {c: int(w) for c, w in (v.get("basket") or {}).items()}
                   for e, v in (raw.get("etfs") or {}).items()}
    missing = [s for s in SYMBOLS if s not in limits]
    if missing:
        raise ValueError(f"case has no limit for {missing}")
    return raw, {s: int(limits[s]) for s in SYMBOLS}, {e: dict(b) for e, b in baskets.items()}


def default_schedule(cfg: Any, case_raw: dict) -> "Schedule":
    """``ctcbot.case.Schedule`` from the case timing and ``cost.phases`` (imported lazily)."""
    from ctcbot.case import Schedule   # A module; lazy so sim imports while A is unfinished
    return Schedule(case_raw["timing"], cfg.get("cost.phases"))


def _cfg_get(cfg: Any, key: str, default: Any) -> Any:
    try:
        v = cfg.get(key, default)
    except Exception:  # noqa: BLE001
        return default
    return default if v is None else v


# ----------------------------------------------------------------------------------------------- snapshot builder
class SnapshotBuilder:
    """Server-side feed state of one season, rendered as a testnet-schema snapshot (TESTNET_FINDINGS 1.2).

    Fields: ``round, storm, time, phase, last_seq``, ``obs`` (latest storm observation of the round), ``track``
    (``[age, x, y, radius]`` every 8 s of age plus the latest observation), ``advisory`` (latest official advisory
    without ``storm``, ``seq``, ``round`` or ``type``), ``impacts``, ``damage`` (every report of the season) and
    ``headlines`` (the last 8 messages with a headline).
    """

    TRACK_EVERY = 8

    def __init__(self, storm_names: Mapping[int, str] | None = None) -> None:
        self.storm_names = dict(storm_names or {})
        self.last_seq = -1
        self.rounds: dict[int, dict] = {}
        self.damage: list[dict] = []
        self.headlines: list[dict] = []

    def _round(self, r: int) -> dict:
        return self.rounds.setdefault(r, {"obs": None, "grid": [], "latest": None, "advisory": None,
                                          "impacts": []})

    def ingest(self, m: Mapping[str, Any]) -> None:
        """Add one published feed message (any type; snapshots are ignored)."""
        typ = m.get("type")
        if typ == "snapshot":
            return
        seq = m.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            self.last_seq = max(self.last_seq, seq)
        try:
            r: int | None = int(m.get("round"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            r = None
        if r is not None and m.get("storm") and r not in self.storm_names:
            self.storm_names[r] = str(m.get("storm"))
        if r is not None:
            st = self._round(r)
            if typ == "storm":
                st["obs"] = {k: m.get(k) for k in ("time", "age", "x", "y", "vx", "vy", "radius", "intensity")}
                age = m.get("age")
                if isinstance(age, (int, float)) and not isinstance(age, bool):
                    pt = [age, m.get("x"), m.get("y"), m.get("radius")]
                    grid = st["grid"]
                    if abs(age / self.TRACK_EVERY - round(age / self.TRACK_EVERY)) < 1e-9:
                        if not grid or age > grid[-1][0]:
                            grid.append(pt)
                    st["latest"] = pt
            elif typ == "advisory" and m.get("source") == "official":
                st["advisory"] = {k: v for k, v in m.items() if k not in ("type", "round", "storm", "seq")}
            elif typ == "impact":
                st["impacts"].append({"island": m.get("island"), "age": m.get("age"), "time": m.get("time")})
            elif typ == "damage_report":
                self.damage.append({k: m.get(k) for k in ("round", "storm", "time", "stage", "damage_pct",
                                                          "industry_loss_musd")})
        if m.get("headline") is not None:
            h = {"seq": seq, "type": typ, "time": m.get("time"), "round": m.get("round"), "headline": m.get("headline")}
            if "source" in m:
                h["source"] = m.get("source")
            self.headlines.append(h)
            if len(self.headlines) > 8:
                del self.headlines[:-8]

    def snapshot(self, round_: int, phase: str, market_time: str) -> dict:
        """The snapshot the server would send now for ``round_`` in ``phase``."""
        st = self.rounds.get(round_) or self._round(round_)
        track = [list(p) for p in st["grid"]]
        latest = st["latest"]
        if latest is not None and (not track or latest[0] > track[-1][0]):
            track.append(list(latest))
        return {
            "type": "snapshot", "round": round_, "storm": self.storm_names.get(round_), "time": market_time,
            "phase": phase, "last_seq": self.last_seq,
            "obs": dict(st["obs"]) if st["obs"] else None,
            "track": track,
            "advisory": dict(st["advisory"]) if st["advisory"] else None,
            "impacts": [dict(x) for x in st["impacts"]],
            "damage": [dict(x) for x in self.damage],
            "headlines": [dict(x) for x in self.headlines],
        }


# ----------------------------------------------------------------------------------------------- base
class MarketBase:
    """What ``SimExchange``, ``SimSource`` and the harness need from a market (research or recorded)."""

    kind: str = "base"
    schedule: Any
    limits: dict[str, int]
    baskets: dict[str, dict[str, int]]
    case_raw: dict
    rounds: list[RoundSpec]
    symbols: tuple[str, ...] = SYMBOLS

    # -- timeline
    def frames(self) -> Iterator[MarketFrame]:  # pragma: no cover - abstract
        raise NotImplementedError

    def default_t_end(self) -> float:  # pragma: no cover - abstract
        raise NotImplementedError

    def round_at(self, t: float) -> RoundSpec | None:
        """The round whose market is open (running) at virtual time t, or None."""
        for rs in self.rounds:
            if rs.open_t <= t < rs.close_t:
                return rs
        return None

    def last_round_before(self, t: float) -> RoundSpec | None:
        """The latest round that opened at or before t."""
        best = None
        for rs in self.rounds:
            if rs.open_t <= t:
                best = rs
        return best

    def next_round_after(self, t: float) -> RoundSpec | None:
        for rs in self.rounds:
            if rs.open_t > t:
                return rs
        return None

    def season_at(self, t: float) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def comp_id_at(self, t: float) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def season_switches(self) -> list[tuple[float, str]]:
        """(virtual time, new season label) of every season change after the first season."""
        return []

    def settlement(self, season: str) -> dict[str, float] | None:  # pragma: no cover - abstract
        raise NotImplementedError

    def mark(self, symbol: str, t: float) -> float | None:  # pragma: no cover - abstract
        raise NotImplementedError

    def book(self, symbol: str, t: float) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- server clock
    def server_time(self, t: float) -> dict:
        """The ``/competitions/time`` body at virtual time t."""
        speed = float(self.schedule.speed)
        rs = self.round_at(t)
        elapsed = 0.0
        for x in self.rounds:
            if x.open_t <= t:
                elapsed += max(0.0, min(t, x.close_t) - x.open_t)
        if rs is not None:
            state = "running"
            ft: str | None = market_time_iso(rs.date, t - rs.open_t, speed)
        else:
            # Paused: the clock stands at the last close while that season is current, else at the next open.
            state = "paused"
            nxt = self.next_round_after(t)
            prev = self.last_round_before(t)
            if prev is not None and self.season_at(t) == prev.season:
                ft = market_time_iso(prev.date, prev.close_t - prev.open_t, speed)
            elif nxt is not None:
                ft = market_time_iso(nxt.date, 0.0, speed)
            else:
                ft = None
        return {"competition_id": self.comp_id_at(t), "state": state, "fictional_time": ft,
                "elapsed_active_seconds": round(elapsed, 6)}


# ----------------------------------------------------------------------------------------------- research
class ResearchMarket(MarketBase):
    """One research season on the virtual timeline (see the module docstring).

    ``ResearchMarket(history, season, cfg, cost, case=None, schedule=None, drop_seqs=(), snapshots="all")``;
    ``cost`` is group C's ``CostModel`` (``walk_bp``, ``half_spread_bp``, ``depth``, ``rho``). ``case`` defaults to
    ``paths.research_case`` of the config and ``schedule`` to ``ctcbot.case.Schedule`` on its timing.
    """

    kind = "research"
    NROWS = 390

    def __init__(self, history: str, season: int | str, cfg: "Config", cost: "CostModel", *, case: Any = None,
                 schedule: "Schedule | None" = None, drop_seqs: Iterable[int] = (),
                 snapshots: str | None = None, inject_snapshots: bool | None = None,
                 snapshot_every_s: float = 30.0, snapshot_offset_s: float = 0.5,
                 pause_s: float | None = None) -> None:
        if isinstance(season, str):
            season = int(season.split(":")[-1])
        self.season = int(season)
        self.cfg = cfg
        self.cost = cost
        self.case_raw, self.limits, self.baskets = case_parts(load_case_dict(cfg, case))
        self.schedule = schedule if schedule is not None else default_schedule(cfg, self.case_raw)
        self.params = FillParams.from_config(cfg)
        if snapshots is None:
            snapshots = "all" if inject_snapshots is None or inject_snapshots else "pause"
        if snapshots not in ("all", "pause", "none"):
            raise ValueError("snapshots must be 'all', 'pause' or 'none'")
        self.snapshots = snapshots
        self.snapshot_every_s = float(snapshot_every_s)
        self.snapshot_offset_s = float(snapshot_offset_s)
        if self.snapshot_every_s <= 0:
            raise ValueError("snapshot_every_s must be positive")
        self.drop_seqs = frozenset(int(s) for s in drop_seqs)
        self.round_len = float(self.schedule.close)
        self.pause_s = float(pause_s if pause_s is not None else _cfg_get(cfg, "sim.pause_between_rounds_s", 30.0))
        self.comp_id = f"sim-{self.season:03d}"
        self.dir = self._season_dir(history, self.season)
        self._load()
        self.rounds = [RoundSpec(index=r - 1, season=str(self.season), comp_id=self.comp_id, round=r,
                                 date=self.dates[r], open_t=self.T(r), close_t=self.T(r) + self.round_len,
                                 storm=self.storm_names.get(r)) for r in (1, 2, 3)]
        self._book_cache: dict[tuple[str, str], tuple] = {}

    # -- loading
    @staticmethod
    def _season_dir(history: str, n: int) -> str:
        d = os.path.join(history, f"season-{n:03d}")
        man = os.path.join(history, "manifest.json")
        if not os.path.isdir(d) and os.path.exists(man):
            with open(man, encoding="utf-8") as f:
                for s in json.load(f).get("seasons", []):
                    if int(s.get("season", -1)) == n and s.get("dir"):
                        d = os.path.join(history, str(s["dir"]))
        if not os.path.isdir(d):
            raise FileNotFoundError(f"research season {n} not found under {history}")
        return d

    def _load(self) -> None:
        self.rows: dict[int, list[tuple[float, ...]]] = {1: [], 2: [], 3: []}
        self.dates: dict[int, str] = {}
        with open(os.path.join(self.dir, "market.csv"), encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                r = int(row["round"])
                if r not in self.rows:
                    continue
                self.rows[r].append(tuple(float(row[s]) for s in SYMBOLS))
                self.dates.setdefault(r, str(row["time"])[:10])
        for r in (1, 2, 3):
            if len(self.rows[r]) != self.NROWS:
                raise ValueError(f"season {self.season} round {r}: {len(self.rows[r])} rows, expected {self.NROWS}")
        with open(os.path.join(self.dir, "settlement.json"), encoding="utf-8") as f:
            st = json.load(f)
        self.settle = {s: float(st[s]) for s in SYMBOLS}
        msgs: list[dict] = []
        with open(os.path.join(self.dir, "feed.jsonl"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                m = json.loads(line)
                if isinstance(m, dict):
                    msgs.append(m)
        speed = float(self.schedule.speed)
        self.news: list[tuple[float, int, dict]] = []   # (virtual t, seq, message)
        self.storm_names: dict[int, str] = {}
        for k, m in enumerate(msgs):
            try:
                r = int(m["round"])
                _, tau = self.schedule.market_to_t(str(m["time"]), speed)
            except (KeyError, TypeError, ValueError):
                continue
            if r not in (1, 2, 3):
                continue
            if m.get("storm") and r not in self.storm_names:
                self.storm_names[r] = str(m["storm"])
            seq = m.get("seq")
            seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else 10 ** 9 + k
            self.news.append((self.T(r) + tau, seq, m))
        self.news.sort(key=lambda x: (x[0], x[1]))

    # -- timeline helpers
    def T(self, r: int) -> float:
        """Virtual time of round r's open."""
        return (r - 1) * (self.round_len + self.pause_s)

    def locate(self, t: float) -> tuple[int, float]:
        """(round, round second) of virtual time t; pauses belong to the round that just closed."""
        r = 1
        for k in (2, 3):
            if t >= self.T(k) - 1e-9:
                r = k
        return r, t - self.T(r)

    def row_index(self, tau: float) -> int:
        return max(0, min(self.NROWS - 1, int(math.floor(tau / 2.0 + 1e-9))))

    def row_px(self, r: int, i: int, symbol: str) -> float:
        return self.rows[r][i][_SYM_INDEX[symbol]]

    def phase_of_row(self, i: int) -> str:
        return self.schedule.cost_phase(2.0 * i)

    def touch(self, r: int, i: int, symbol: str) -> tuple[float, float]:
        """Unrounded synthetic (bid, ask) of row i: ``rows[i] * (1 -+ hs / 1e4)``."""
        px = self.row_px(r, i, symbol)
        hs = float(self.cost.half_spread_bp(symbol, self.phase_of_row(i)))
        return px * (1.0 - hs * 1e-4), px * (1.0 + hs * 1e-4)

    def _levels(self, symbol: str, phase: str) -> tuple:
        """Per (symbol, phase): (bid multipliers, ask multipliers, quantities) of the five synthetic levels."""
        key = (symbol, phase)
        got = self._book_cache.get(key)
        if got is not None:
            return got
        L = self.limits[symbol]
        hs = float(self.cost.half_spread_bp(symbol, phase))
        xs = self.params.book_levels_x
        bm, am, qs = [1.0 - hs * 1e-4], [1.0 + hs * 1e-4], []
        prev = 0.0
        for k, x in enumerate(xs):
            qs.append(max(1, int(round(L * (x - prev)))))
            if k > 0:
                cb = walk_increment(lambda z: float(self.cost.walk_bp(symbol, phase, z, Side.BUY)), prev, x)
                cs = walk_increment(lambda z: float(self.cost.walk_bp(symbol, phase, z, Side.SELL)), prev, x)
                am.append(max(am[-1], 1.0 + cb * 1e-4))
                bm.append(min(bm[-1], 1.0 - cs * 1e-4))
            prev = x
        got = (tuple(bm), tuple(am), tuple(qs))
        self._book_cache[key] = got
        return got

    def book_levels(self, r: int, i: int, symbol: str) -> tuple[list[dict], list[dict]]:
        """Published (bids, asks) of row i, rounded to cents (bids down, asks up)."""
        px = self.row_px(r, i, symbol)
        bm, am, qs = self._levels(symbol, self.phase_of_row(i))
        bids = [{"price": math.floor(px * m * 100.0 + 1e-6) / 100.0, "quantity": q} for m, q in zip(bm, qs)]
        asks = [{"price": math.ceil(px * m * 100.0 - 1e-6) / 100.0, "quantity": q} for m, q in zip(am, qs)]
        return bids, asks

    def _row_frames(self, r: int, i: int, t: float) -> list[dict]:
        ts = wall_iso(t)
        out = []
        for s in SYMBOLS:
            bids, asks = self.book_levels(r, i, s)
            out.append({"type": "quote", "symbol": s, "bid": bids[0]["price"], "ask": asks[0]["price"],
                        "bid_size": bids[0]["quantity"], "ask_size": asks[0]["quantity"], "timestamp": ts})
            out.append({"type": "orderbook", "symbol": s, "bids": bids, "asks": asks, "timestamp": ts})
        return out

    def _snapshot_times(self) -> list[tuple[float, int, str]]:
        """(virtual t, round, phase) of every snapshot the server sends."""
        if self.snapshots == "none":
            return []
        out = []
        end = self.T(3) + self.round_len + self.pause_s
        k = 0
        while True:
            t = k * self.snapshot_every_s + self.snapshot_offset_s
            k += 1
            if t >= end:
                break
            r, tau = self.locate(t)
            if tau >= self.round_len:
                phase = "closed"
            else:
                if self.snapshots == "pause":
                    continue
                ph = self.schedule.phase(tau)
                phase = str(getattr(ph, "value", ph))
                if phase not in TRADING_PHASES:
                    phase = "closed"
            out.append((t, r, phase))
        return out

    def _events(self) -> list[tuple[float, int, int, str, Any]]:
        ev: list[tuple[float, int, int, str, Any]] = []
        for r in (1, 2, 3):
            for i in range(self.NROWS):
                ev.append((self.T(r) + 2.0 * i, 0, i, "row", (r, i)))
        for k, (t, seq, m) in enumerate(self.news):
            ev.append((t, 1, k, "news", m))
        for k, (t, r, phase) in enumerate(self._snapshot_times()):
            ev.append((t, 2, k, "snap", (r, phase)))
        ev.sort(key=lambda e: (e[0], e[1], e[2]))
        return ev

    def frames(self) -> Iterator[MarketFrame]:
        """Every frame of the season in time order (rows, then news, then snapshots at one instant)."""
        builder = SnapshotBuilder(self.storm_names)
        speed = float(self.schedule.speed)
        conn = "sim#1"
        yield MarketFrame(0.0, {}, conn, control="up")
        for t, _, _, kind, payload in self._events():
            if kind == "row":
                r, i = payload
                for fr in self._row_frames(r, i, t):
                    yield MarketFrame(t, fr, conn)
            elif kind == "news":
                m = payload
                builder.ingest(m)
                seq = m.get("seq")
                if isinstance(seq, int) and seq in self.drop_seqs:
                    continue
                yield MarketFrame(t, {"type": "news", "message": dict(m)}, conn)
            else:
                r, phase = payload
                tau = t - self.T(r)
                mt = market_time_iso(self.dates[r], tau, speed, truncate=True)
                yield MarketFrame(t, {"type": "news", "message": builder.snapshot(r, phase, mt)}, conn)

    def default_t_end(self) -> float:
        """The last frame (or row) plus 2 s."""
        ev = self._events()
        return (ev[-1][0] if ev else self.T(3) + 2.0 * (self.NROWS - 1)) + 2.0

    # -- exchange-facing queries
    def season_at(self, t: float) -> str:
        return str(self.season)

    def comp_id_at(self, t: float) -> str:
        return self.comp_id

    def settlement(self, season: str) -> dict[str, float] | None:
        return dict(self.settle) if str(season) == str(self.season) else None

    def mark(self, symbol: str, t: float) -> float | None:
        r, tau = self.locate(t)
        return self.row_px(r, self.row_index(tau), symbol)

    def book(self, symbol: str, t: float) -> dict:
        r, tau = self.locate(t)
        i = self.row_index(tau)
        bids, asks = self.book_levels(r, i, symbol)
        return {"symbol": symbol, "bids": bids, "asks": asks, "timestamp": wall_iso(self.T(r) + 2.0 * i)}

    def round_marks(self, r: int) -> dict[str, float]:
        """Marks for round r's P&L: the last consensus row for rounds 1-2, settlement for round 3."""
        if r >= 3:
            return dict(self.settle)
        last = self.rows[r][self.NROWS - 1]
        return {s: last[k] for k, s in enumerate(SYMBOLS)}

    def rows_for(self, r: int) -> Sequence[tuple[float, ...]]:
        return self.rows[r]
