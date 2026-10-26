"""Group D unit tests: BotCore with every A, B and C collaborator replaced by a fake (DESIGN.md §9.1 D core).

The fakes defined here (``make_core`` and the ``Fake*`` classes) are also used by the other ``test_d_*`` files.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

from ctcbot.types import (SYMBOLS, ApiKind, ApiRequest, ApiResult, BookView, CancelIntent,  # noqa: E402
                          ClockReading, Command, ConnEvent, EngineHealth, FairValueSnapshot, Fill, ListLog,
                          NewsEvent, OrderAck, Phase, PriceQuality, Priority, RestIntent, RiskMode,
                          SeasonResetEvent, ServerTime, Side, SnapshotEvent, SourceFrame, TargetIntent, Trigger)
from ctcbot.app.core import BotCore, default_factories  # noqa: E402
from ctcbot.app.status import format_status  # noqa: E402

DEFAULT_JSON = os.path.join(BOT, "config", "default.json")


# ============================================================================ fake A objects
class FakeConfig:
    """Config look-alike built from config/default.json (no dependency on group A's loader)."""

    def __init__(self, overrides: dict | None = None) -> None:
        with open(DEFAULT_JSON) as f:
            d = json.load(f)
        d.pop("live_allowed", None)
        for dotted, v in (overrides or {}).items():
            cur = d
            keys = dotted.split(".")
            for k in keys[:-1]:
                cur = cur.setdefault(k, {})
            cur[keys[-1]] = v
        self.d = d
        self.hash = "abcdef123456"
        self.root = BOT
        self.sources = (DEFAULT_JSON,)
        self.live_allowed = True

    _MISSING = object()

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        cur: Any = self.d
        for k in dotted.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                if default is FakeConfig._MISSING:
                    raise KeyError(dotted)
                return default
        return cur

    def path(self, p: str | None) -> str | None:
        if p is None:
            return None
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.root, p))

    def sym(self, symbol: str) -> dict:
        return self.d["symbols"][symbol]


LIMITS = {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500, "MAT": 700, "GEN": 600,
          "HAVEN": 250, "REIN": 200, "ARCH": 50, "REBLD": 100}
TIMING = {"pre_storm": 60, "storm": 480, "aftermath": 240, "prelim_report": 30, "final_report": 40}


@dataclass
class FakeCase:
    raw: dict = field(default_factory=lambda: {"timing": dict(TIMING), "limits": dict(LIMITS)})
    symbols: tuple = SYMBOLS
    limits: dict = field(default_factory=lambda: dict(LIMITS))
    baskets: dict = field(default_factory=dict)
    kinds: dict = field(default_factory=dict)
    timing: dict = field(default_factory=lambda: dict(TIMING))


@dataclass
class FakeCaseDiff:
    symbols: frozenset = frozenset()
    timing: bool = False
    lines: tuple = ()

    @property
    def clean(self) -> bool:
        return not self.symbols and not self.timing


class FakeSchedule:
    open = 0.0; storm_start = 60.0; storm_end = 540.0; prelim = 570.0; final = 740.0; close = 780.0
    round_len = 780.0; speed = 30.0

    def at(self, spec: Sequence) -> float:
        return float(getattr(self, spec[0])) + float(spec[1])

    def phase(self, t: float | None) -> Phase:
        if t is None:
            return Phase.UNKNOWN
        if 0 <= t < 60:
            return Phase.PRE_STORM
        if 60 <= t < 540:
            return Phase.STORM
        if 540 <= t < 780:
            return Phase.AFTERMATH
        return Phase.CLOSED

    def cost_phase(self, t: float) -> str:
        return "storm"


class FakeClock:
    """Market clock driven by the test: round, open mono, running and quality are plain attributes."""

    def __init__(self, schedule: FakeSchedule) -> None:
        self.schedule = schedule
        self.round: int | None = 1
        self.epoch = 0
        self.open_mono = 0.0
        self.running = True
        self.known = True
        self.quality = "server"
        self.pending: list[tuple[int, int]] = []
        self.feed_obs: list[tuple] = []
        self.server_obs: list[tuple] = []
        self.resets: list[tuple] = []

    def observe_feed(self, market_time, round_, recv_mono, kind, season_epoch, phase=None) -> None:
        self.feed_obs.append((market_time, round_, recv_mono, kind, season_epoch, phase))

    def observe_server(self, st, sent_mono, recv_mono) -> None:
        self.server_obs.append((st, sent_mono, recv_mono))

    def on_season_reset(self, epoch, comp_id) -> None:
        self.resets.append((epoch, comp_id))
        self.epoch = epoch

    def read(self, now: float) -> ClockReading:
        t = (now - self.open_mono) if self.known else None
        ph = self.schedule.phase(t)
        return ClockReading(known=self.known, season_epoch=self.epoch, comp_id=None, round=self.round,
                            date="1802-05-10", t=t, phase=ph, running=self.running and ph not in (Phase.CLOSED,),
                            quality=self.quality, anchor_age_s=0.1)

    def t_to_mono(self, t: float, now: float) -> float | None:
        return self.open_mono + t if self.known else None

    def round_events(self) -> list[tuple[int, int]]:
        out, self.pending = self.pending, []
        return out


class FakeNormalizer:
    """Pass-through normalizer: a frame carries its events as ``frame["_ev"]``."""

    def __init__(self) -> None:
        self.epoch = 0
        self.max_seq = -1
        self.wm = -1
        self.pending_reset: str | None = None
        self.poll_events: list = []
        self.hint: str | None = None
        self.calls: list[tuple] = []

    def ingest(self, sf: SourceFrame) -> list:
        evs = list(sf.frame.get("_ev", ())) if isinstance(sf.frame, dict) else []
        for ev in evs:
            if isinstance(ev, SeasonResetEvent):
                if ev.provisional:
                    self.pending_reset = ev.reason
                else:
                    self.epoch = ev.new_epoch
            if isinstance(ev, NewsEvent) and ev.seq is not None:
                self.max_seq = max(self.max_seq, ev.seq)
                self.wm = self.max_seq
        return evs

    def poll(self, now: float) -> list:
        out, self.poll_events = self.poll_events, []
        return out

    def force_season_reset(self, reason: str, now: float) -> SeasonResetEvent:
        self.calls.append(("force", reason))
        self.epoch += 1
        self.pending_reset = None
        return SeasonResetEvent(reason=reason, new_epoch=self.epoch, recv_mono=now)

    def confirm_season_reset(self, reason: str, now: float) -> SeasonResetEvent | None:
        self.calls.append(("confirm", reason))
        if self.pending_reset is None:
            return None
        self.pending_reset = None
        self.epoch += 1
        return SeasonResetEvent(reason=reason, new_epoch=self.epoch, recv_mono=now)

    def reject_season_reset(self, now: float) -> None:
        self.calls.append(("reject",))
        self.pending_reset = None

    def phase_hint(self) -> str | None:
        return self.hint

    def gaps(self) -> list:
        return []

    def stats(self) -> dict:
        return {"dup_news": 0, "seq_gaps": 0}


class FakeLimiter:
    def __init__(self, cap: int = 150) -> None:
        self.cap = cap
        self.used_n = 0
        self.floors = {Priority.KILL: 0, Priority.SNIPE: 1, Priority.HEDGE: 4, Priority.TAKE: 10}
        self.reservations: list[tuple] = []
        self.cleared = 0
        self.throttled: list[tuple] = []
        self.restored: list = []

    def available(self, now: float) -> int:
        return self.cap - self.used_n

    def used(self, now: float) -> int:
        return self.used_n

    def floor(self, p: Priority, now: float) -> int:
        f = self.floors[p]
        for s, e, fl in self.reservations:
            if s <= now < e and p in fl:
                f = max(f, fl[p])
        return f

    def try_acquire(self, p: Priority, n: int, now: float, tag: str = "") -> list:
        if self.available(now) - n >= self.floor(p, now):
            self.used_n += n
            return [object()] * n
        return []

    def reserve(self, start: float, end: float, floors: dict) -> None:
        self.reservations.append((start, end, dict(floors)))

    def reserved(self, now: float) -> bool:
        return any(s <= now < e for s, e, _ in self.reservations)

    def clear_reservations(self) -> None:
        self.cleared += 1
        self.reservations.clear()

    def on_throttled(self, now: float, freeze_s: float) -> None:
        self.throttled.append((now, freeze_s))

    def ledger(self) -> list:
        return [1.0, 2.0]

    def restore(self, ledger) -> None:
        self.restored = list(ledger)

    def stats(self, now: float) -> dict:
        return {"used": self.used_n, "cap": self.cap}


# ============================================================================ fake B objects
class FakeBooks:
    def __init__(self) -> None:
        self.mid: dict[str, float] = {s: 50.0 for s in SYMBOLS}
        self.events: list = []
        self.resets = 0

    def on_book(self, ev) -> None:
        self.events.append(ev)

    def on_quote(self, ev) -> None:
        self.events.append(ev)

    def on_trade(self, ev) -> None:
        self.events.append(ev)

    def view(self, symbol: str, now: float) -> BookView:
        m = self.mid.get(symbol)
        bid = None if m is None else m - 0.05
        ask = None if m is None else m + 0.05
        return BookView(symbol=symbol, bid=bid, ask=ask, bid_size=10, ask_size=10, bids=(), asks=(), mid=m,
                        half_spread_bp=None if m is None else 10.0, age_s=0.1, empty=m is None, wall_only=False,
                        server_ts=None)

    def views(self, now: float) -> dict:
        return {s: self.view(s, now) for s in SYMBOLS}

    def reset(self) -> None:
        self.resets += 1


class FakePositions:
    def __init__(self) -> None:
        self.pos = {s: 0 for s in SYMBOLS}
        self.cash = 0.0
        self.fills: list = []
        self.resets = 0
        self.ex_pnl: float | None = None

    def mtm(self, mids) -> float:
        return self.cash + sum(q * (mids.get(s) or 0.0) for s, q in self.pos.items())

    def exchange_pnl(self) -> float | None:
        return self.ex_pnl

    def reset(self) -> None:
        self.resets += 1
        self.pos = {s: 0 for s in SYMBOLS}
        self.cash = 0.0


@dataclass
class FakeRecord:
    local_id: str
    symbol: str
    side: Side
    qty: int
    price: float
    strategy: str = "s3"
    state: Any = "open"
    dry: bool = False
    remaining: int = 0
    key: str | None = None
    order_id: str | None = None


class FakeStore:
    def __init__(self, epoch: int) -> None:
        from ctcbot.types import OrderState
        self.epoch = epoch
        self.records: list[FakeRecord] = []
        self.loaded: dict | None = None
        self._OS = OrderState

    def live(self, symbol: str | None = None) -> list:
        return [r for r in self.records if r.state in self._OS.__members__.values() and r.state not in
                (self._OS.FILLED, self._OS.CANCELED, self._OS.REJECTED) and (symbol is None or r.symbol == symbol)]

    def state_dict(self) -> dict:
        return {"epoch": self.epoch, "n": len(self.records)}

    def load_state(self, d: dict) -> None:
        self.loaded = dict(d)


class FakeRisk:
    def __init__(self) -> None:
        self._holds: dict[str, dict] = {}
        self._killed = False
        self._disabled: set[str] = set()
        self.results: list = []
        self.fills: list = []
        self.mtms: list = []
        self.round_starts: list = []
        self.loaded: dict | None = None

    @property
    def mode(self) -> RiskMode:
        if self._killed:
            return RiskMode.FROZEN
        return RiskMode.NO_NEW_RISK if self._holds else RiskMode.NORMAL

    def hold(self, name, now, until=None, sticky=False, reason="", exempt=frozenset()) -> None:
        self._holds[name] = {"until": until, "sticky": sticky, "reason": reason, "exempt": set(exempt)}

    def release(self, name, now) -> None:
        self._holds.pop(name, None)

    def holds(self, now) -> dict:
        return {k: v for k, v in self._holds.items() if v["until"] is None or now < v["until"]}

    def kill(self, reason, now) -> None:
        self._killed = True

    def killed(self) -> bool:
        return self._killed

    def unkill(self, now) -> None:
        self._killed = False

    def on_result(self, res, now) -> None:
        self.results.append(res)
        if res.status in (401, 403):
            self.hold("auth", now, sticky=True, reason=str(res.status))

    def on_mtm(self, mtm, round_, now) -> None:
        self.mtms.append((mtm, round_, now))

    def on_fill(self, f, now) -> None:
        self.fills.append(f)

    def on_round_start(self, round_, mtm, now) -> None:
        self.round_starts.append((round_, mtm, now))

    def disabled(self) -> frozenset:
        return frozenset(self._disabled)

    def disable(self, sym, reason, now) -> None:
        self._disabled.add(sym)

    def enable(self, sym) -> None:
        self._disabled.discard(sym)

    def state_dict(self) -> dict:
        return {"holds": sorted(self._holds), "killed": self._killed}

    def load_state(self, d) -> None:
        self.loaded = dict(d)


class FakeOM:
    """Turns TargetIntents and RestIntents into PLACE requests; records every call."""

    def __init__(self, store: FakeStore, risk: FakeRisk, limiter: FakeLimiter, next_req_id: Callable[[], int]) -> None:
        self.store = store
        self.risk = risk
        self.limiter = limiter
        self.next_id = next_req_id
        self.intents: list = []
        self.cancel_alls: list[tuple] = []
        self.results: list = []
        self.kills = 0
        self.followups: list = []
        self.fills_for: dict[int, list] = {}
        self.dropped = 0
        self.loaded: dict | None = None
        self.steps = 0
        self.placed_keys: set = set()

    def set_intents(self, intents, now) -> None:
        self.intents.extend(intents)

    def _place(self, sym, side, qty, price, prio, now, strategy) -> ApiRequest:
        from ctcbot.types import OrderState
        rid = self.next_id()
        rec = FakeRecord(local_id=f"e{self.store.epoch}-{rid}", symbol=sym, side=side, qty=qty, price=price,
                         strategy=strategy, state=OrderState.SENDING, remaining=qty)
        self.store.records.append(rec)
        return ApiRequest(req_id=rid, kind=ApiKind.PLACE, priority=prio, symbol=sym, side=side, qty=qty, price=price,
                          local_id=rec.local_id, created_mono=now)

    def step(self, now, clock, feed) -> list:
        self.steps += 1
        if self.risk.killed():
            out, self.followups = self.followups, []
            return out
        out = []
        for i in self.intents:
            if isinstance(i, TargetIntent) and i.target:
                side = Side.BUY if i.target > 0 else Side.SELL
                px = i.limit_buy if side is Side.BUY else i.limit_sell
                out.append(self._place(i.symbol, side, min(abs(i.target), i.clip_qty), px, i.priority, now,
                                       i.strategy))
            elif isinstance(i, RestIntent) and i.key not in self.placed_keys:
                self.placed_keys.add(i.key)
                out.append(self._place(i.symbol, i.side, i.qty or 1, i.price, i.priority, now, i.strategy))
            elif isinstance(i, CancelIntent):
                out.append(ApiRequest(req_id=self.next_id(), kind=ApiKind.CANCEL_ALL,
                                      priority=min(i.priority, Priority.HEDGE), symbol=i.symbol, created_mono=now))
        self.intents = []
        return out

    def on_result(self, res, now) -> list:
        from ctcbot.types import OrderState
        self.results.append(res)
        for r in self.store.records:
            if r.local_id == res.req.local_id:
                if res.req.kind == ApiKind.PLACE:
                    r.state = OrderState.OPEN if res.ok else OrderState.REJECTED
                    r.dry = bool(res.dry_run)
        return list(self.fills_for.pop(res.req.req_id, []))

    def kill(self, now) -> list:
        self.kills += 1
        return [ApiRequest(req_id=self.next_id(), kind=ApiKind.CANCEL_ALL, priority=Priority.KILL, created_mono=now)]

    def kill_followup(self, open_rows, now) -> list:
        return [ApiRequest(req_id=self.next_id(), kind=ApiKind.CANCEL_ALL, priority=Priority.KILL,
                           created_mono=now)] if open_rows else []

    def kill_status(self) -> dict:
        return {"pending": 0, "actions": self.kills, "complete": True, "since": 0.0}

    def cancel_all(self, priority, now, symbol=None, reason="") -> list:
        self.cancel_alls.append((priority, symbol, reason))
        return [ApiRequest(req_id=self.next_id(), kind=ApiKind.CANCEL_ALL, priority=priority, symbol=symbol,
                           created_mono=now)]

    def working(self) -> dict:
        return {}

    def drop_dry(self) -> int:
        self.dropped += 1
        return 3

    def state_dict(self) -> dict:
        return {"om": True}

    def load_state(self, d) -> None:
        self.loaded = dict(d)


class FakeReconciler:
    def __init__(self, store, positions) -> None:
        self.store = store
        self.n = 0
        self.halves: dict[int, int] = {}
        self.age: float | None = None
        self.fails = 0
        self.rows: list | None = []
        self.results: list = []

    def begin_cycle(self, now) -> int:
        self.n += 1
        return self.n

    def on_result(self, res, now):
        self.results.append(res)
        c = res.req.cycle
        if not res.ok:
            self.fails += 1
            self.halves[c] = self.halves.get(c, 0) + 1
            return None
        self.halves[c] = self.halves.get(c, 0) + 1
        if self.halves[c] >= 2:
            self.fails = 0
            return []
        return None

    def unknown_age(self, now):
        return self.age

    def consecutive_failures(self) -> int:
        return self.fails

    def last_open_rows(self):
        return self.rows


# ============================================================================ fake C objects
class CallLog:
    """Shared ordered log of calls across fakes (checks cross-object ordering)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def add(self, *item) -> None:
        self.calls.append(item)

    def index(self, name: str) -> int:
        for i, c in enumerate(self.calls):
            if c[0] == name:
                return i
        return -1


class FakeCoverage:
    def __init__(self) -> None:
        self.news: list = []
        self.snaps: list = []
        self.rebuilt: list | None = None
        self.resets = 0

    def on_news(self, msg) -> None:
        self.news.append(msg)

    def on_snapshot(self, msg) -> None:
        self.snaps.append(msg)

    def coverage(self) -> dict:
        return {1: "full"}

    def rebuild(self, messages) -> None:
        self.rebuilt = list(messages)

    def reset(self) -> None:
        self.resets += 1
        self.news.clear()
        self.snaps.clear()


class FakeLaws(FakeCoverage):
    def values(self):
        return None


class FakeLawCheck:
    def __init__(self, calls: CallLog) -> None:
        self.calls = calls
        self.reports: list = []
        self.fvs: list = []
        self.ticks = 0
        self.resets = 0
        self.loaded = None

    def on_report(self, round_, stage, seq, fv_before, books, now) -> None:
        self.reports.append((round_, stage, seq))
        self.calls.add("law_check.on_report", round_, stage, seq)

    def on_fv(self, fv, now) -> None:
        self.fvs.append(fv)

    def on_tick(self, books, now) -> None:
        self.ticks += 1

    def reset(self) -> None:
        self.resets += 1

    def state_dict(self) -> dict:
        return {"lc": 1}

    def load_state(self, d) -> None:
        self.loaded = d


class FakeRunner:
    def __init__(self) -> None:
        self.ctxs: list = []
        self.commands: list = []
        self.policy: Callable[[Any], list] = lambda ctx: []
        self.frozen_set: frozenset = frozenset()
        self.resets = 0
        self.promoted = 0
        self.loaded = None

    def decide(self, ctx) -> list:
        self.ctxs.append(ctx)
        return list(self.policy(ctx))

    def on_command(self, cmd, ctx) -> list:
        self.commands.append(cmd)
        return []

    def frozen(self) -> frozenset:
        return self.frozen_set

    def leadlag_symbols(self) -> frozenset:
        return frozenset()

    def on_promotion(self) -> None:
        self.promoted += 1

    def reset_season(self) -> None:
        self.resets += 1

    def state_dict(self) -> dict:
        return {"runner": 1}

    def load_state(self, d) -> None:
        self.loaded = d

    def status(self) -> dict:
        return {}


class FakeEngine:
    def __init__(self, calls: CallLog) -> None:
        self.calls = calls
        self.submits: list[tuple[list, float]] = []
        self.fv: FairValueSnapshot | None = None
        self.listener = None
        self.adjust: list = []
        self.resets: list = []
        self.closed = 0
        self.alive = True

    def set_listener(self, cb) -> None:
        self.listener = cb

    def submit(self, msgs, recv_mono) -> None:
        self.submits.append((list(msgs), recv_mono))
        self.calls.add("engine.submit", len(msgs))

    def latest(self):
        return self.fv

    def health(self, now) -> EngineHealth:
        return EngineHealth(alive=self.alive, pending=0, oldest_pending_age_s=None, last_compute_ms=12.0, errors=0,
                            consecutive_errors=0 if self.alive else 3, snapshot_fix=True)

    def set_adjustment(self, symbol, mult=1.0, add=0.0) -> None:
        self.adjust.append((symbol, mult, add))

    def override(self, dotted, value) -> None:
        pass

    def reset_season(self, epoch, persist_path) -> None:
        self.resets.append((epoch, persist_path))
        self.calls.add("engine.reset_season", epoch)

    def close(self) -> None:
        self.closed += 1


# ============================================================================ building a core
@dataclass
class Fakes:
    calls: CallLog
    normalizer: FakeNormalizer
    clock: FakeClock
    books: FakeBooks
    positions: FakePositions
    limiter: FakeLimiter
    risk: FakeRisk
    coverage: FakeCoverage
    laws: FakeLaws
    law_check: FakeLawCheck
    runner: FakeRunner
    engine: FakeEngine
    log: ListLog
    stores: list = field(default_factory=list)
    oms: list = field(default_factory=list)
    reconcilers: list = field(default_factory=list)

    @property
    def om(self) -> FakeOM:
        return self.oms[-1]

    @property
    def reconciler(self) -> FakeReconciler:
        return self.reconcilers[-1]


def fake_factories(fk: Fakes, schedule: FakeSchedule | None = None) -> dict:
    sch = schedule or FakeSchedule()

    def store(*, epoch, **_):
        s = FakeStore(epoch)
        fk.stores.append(s)
        return s

    def om(*, store, risk, limiter, next_req_id, **_):
        o = FakeOM(store, risk, limiter, next_req_id)
        fk.oms.append(o)
        return o

    def reconciler(*, store, positions, **_):
        r = FakeReconciler(store, positions)
        fk.reconcilers.append(r)
        return r

    return {
        "schedule": lambda **_: sch,
        "limits": lambda **_: dict(LIMITS),
        "normalizer": lambda **_: fk.normalizer,
        "clock": lambda **_: fk.clock,
        "books": lambda **_: fk.books,
        "positions": lambda **_: fk.positions,
        "store": store,
        "limiter": lambda **_: fk.limiter,
        "risk": lambda **_: fk.risk,
        "om": om,
        "reconciler": reconciler,
        "coverage": lambda **_: fk.coverage,
        "laws": lambda **kw: None if kw.get("laws_module") is None else fk.laws,
        "law_check": lambda **_: fk.law_check,
        "runner": lambda **_: fk.runner,
        "engine": lambda **_: FakeEngine(fk.calls),
    }


class _Time:
    def __init__(self) -> None:
        self.t = 0.0

    def mono(self) -> float:
        return self.t

    def wall(self) -> float:
        return 1.8e9 + self.t


def make_core(*, cfg_over: dict | None = None, mode: str = "sim", run_dir: str | None = None,
              laws_module: Any = object(), case_diff: FakeCaseDiff | None = None, restore: dict | None = None,
              promotion: bool = False, live_label: bool = False, start: bool = True) -> tuple[BotCore, Fakes]:
    calls = CallLog()
    sch = FakeSchedule()
    fk = Fakes(calls=calls, normalizer=FakeNormalizer(), clock=FakeClock(sch), books=FakeBooks(),
               positions=FakePositions(), limiter=FakeLimiter(), risk=FakeRisk(), coverage=FakeCoverage(),
               laws=FakeLaws(), law_check=FakeLawCheck(calls), runner=FakeRunner(), engine=FakeEngine(calls),
               log=ListLog())
    core = BotCore(FakeConfig(cfg_over), FakeCase(), case_diff or FakeCaseDiff(), fk.engine, _Time(), fk.log,
                   mode=mode, run_dir=run_dir, run_id="testrun", laws_module=laws_module, cost=object(),
                   restore=restore, promotion=promotion, live_label=live_label, factories=fake_factories(fk, sch))
    if start:
        core.start(0.0)
    return core, fk


# ---------------------------------------------------------------------------- event builders
def frame(*events, conn: str = "ws0#1", mono: float = 0.0) -> SourceFrame:
    return SourceFrame(frame={"_ev": list(events)}, recv_wall=1.8e9 + mono, recv_mono=mono, conn=conn)


def news(mtype: str, seq: int, rnd: int = 1, mono: float = 0.0, epoch: int = 0, **fields) -> NewsEvent:
    msg = {"type": mtype, "seq": seq, "round": rnd, "time": "1802-05-10T10:00:00", **fields}
    return NewsEvent(msg=msg, mtype=mtype, seq=seq, round=rnd, market_time=msg["time"], season_epoch=epoch,
                     recv_mono=mono)


def snap(last_seq: int, stage: str, *, relevant: bool = False, rnd: int = 1, phase: str = "storm",
         mono: float = 0.0, epoch: int = 0, **fields) -> SnapshotEvent:
    msg = {"type": "snapshot", "round": rnd, "time": "1802-05-10T11:30:00", "phase": phase, "last_seq": last_seq,
           **fields}
    return SnapshotEvent(msg=msg, last_seq=last_seq, round=rnd, phase=phase, market_time=msg["time"], stage=stage,
                         engine_relevant=relevant, season_epoch=epoch, recv_mono=mono)


def result(req: ApiRequest, status: int | None = 200, data: Any = None, parsed: Any = None, now: float = 0.0,
           dry_run: bool = False, sent: bool = True) -> ApiResult:
    return ApiResult(req=req, status=status, data=data, parsed=parsed, error=None, sent_mono=now, done_mono=now,
                     dry_run=dry_run, sent=sent)


def answer_all(core: BotCore, reqs: Sequence[ApiRequest], now: float, comp_id: str = "comp-1",
               state: str = "running") -> None:
    """Answer GETs the way an exchange would (200), order actions with an OPEN ack."""
    for r in reqs:
        if r.kind == ApiKind.GET_TIME:
            core.on_api_result(result(r, parsed=ServerTime(comp_id, state, "1802-05-10T10:00:00", 10.0), now=now),
                               now)
        elif r.kind in (ApiKind.GET_OPEN, ApiKind.GET_POSITIONS):
            core.on_api_result(result(r, parsed=[] if r.kind == ApiKind.GET_OPEN else {}, now=now), now)
        else:
            core.on_api_result(result(r, parsed=OrderAck("x", "open", 0, r.qty, None), now=now), now)


def kinds(reqs) -> list:
    return [r.kind for r in reqs]


# ============================================================================ tests
class TestCoreBasics(unittest.TestCase):
    def test_imports_without_b_and_c(self):
        # default_factories builds nothing until a factory is called
        f = default_factories()
        self.assertTrue({"normalizer", "clock", "books", "positions", "store", "risk", "om", "reconciler",
                         "runner", "coverage", "laws", "law_check", "limiter", "schedule", "limits"} <= set(f))

    def test_start_emits_time_and_reconcile(self):
        core, fk = make_core()
        reqs = core.take_requests()
        self.assertEqual(kinds(reqs), [ApiKind.GET_TIME, ApiKind.GET_OPEN, ApiKind.GET_POSITIONS])
        self.assertEqual(reqs[1].cycle, reqs[2].cycle)
        self.assertEqual(len({r.req_id for r in reqs}), 3)

    def test_jobs(self):
        core, _ = make_core(mode="sim", run_dir="/nonexistent")
        self.assertEqual([j[0] for j in core.jobs()], ["reconcile", "clock_poll", "tick"])
        core2, _ = make_core(mode="host", run_dir=testpaths.tmpdir("core_jobs"))
        self.assertEqual([j[0] for j in core2.jobs()], ["reconcile", "clock_poll", "tick", "persist", "status"])
        self.assertEqual(dict(core2.jobs())["tick"], 0.25)

    def test_watchdog_by_phase(self):
        core, fk = make_core()
        core.take_requests()
        fk.clock.open_mono = 0.0
        core.run_job("tick", 100.0)            # storm
        self.assertEqual(core.watchdog_s(), 3.0)
        core.run_job("tick", 30.0)             # pre_storm
        self.assertEqual(core.watchdog_s(), 8.0)
        core.run_job("tick", 900.0)            # closed
        self.assertEqual(core.watchdog_s(), 35.0)
        fk.clock.known = False
        fk.normalizer.hint = "storm"
        core.run_job("tick", 901.0)
        self.assertEqual(core.watchdog_s(), 3.0)
        fk.normalizer.hint = None
        core.run_job("tick", 902.0)
        self.assertEqual(core.watchdog_s(), 35.0)

    def test_watchdog_paused_clock_uses_pause_value(self):
        # integration: a pre-season pause reads pre_storm (frozen 09:30:00) with running False; frames come every
        # ~30 s, so the pause value applies, not pre_storm's 8 s; it reverts once the clock runs
        core, fk = make_core()
        core.take_requests()
        fk.clock.open_mono = 0.0
        fk.clock.running = False
        core.run_job("tick", 30.0)             # pre_storm, paused
        self.assertEqual(core.watchdog_s(), 35.0)
        fk.clock.running = True
        core.run_job("tick", 30.25)
        self.assertEqual(core.watchdog_s(), 8.0)
        fk.clock.running = False
        core.run_job("tick", 100.0)            # a paused storm (server state "paused")
        self.assertEqual(core.watchdog_s(), 35.0)

    def test_bad_mode(self):
        with self.assertRaises(ValueError):
            make_core(mode="bogus")


class TestMiniRound(unittest.TestCase):
    def test_frames_to_intents_to_requests(self):
        core, fk = make_core()
        answer_all(core, core.take_requests(), 0.0)

        def policy(ctx):
            if ctx.trigger == Trigger.NEWS and ctx.news:
                return [TargetIntent("s3", "PORT", 16, 16, 2.0, 51.0, None, Priority.TAKE, 2.0, reason="test")]
            return []

        fk.runner.policy = policy
        core.on_frames([frame(news("storm", 5, mono=100.0, age=40, x=1, y=2), mono=100.0)], 100.0)
        reqs = core.take_requests()
        self.assertEqual(kinds(reqs), [ApiKind.PLACE])
        self.assertEqual(reqs[0].symbol, "PORT")
        self.assertEqual(reqs[0].priority, Priority.TAKE)
        ctx = fk.runner.ctxs[-1]
        self.assertEqual(ctx.trigger, Trigger.NEWS)
        self.assertEqual(len(ctx.news), 1)
        self.assertEqual(ctx.limits["PORT"], 200)
        self.assertEqual(ctx.budget_take, 150 - 10)
        # the engine got the batch's message, the clock a storm observation
        self.assertEqual(len(fk.engine.submits), 1)
        self.assertEqual(fk.clock.feed_obs[-1][3], "storm")
        self.assertEqual(fk.coverage.news[-1]["type"], "storm")
        self.assertEqual(fk.laws.news[-1]["type"], "storm")
        # the news is handed to the strategies once
        core.run_job("tick", 100.25)
        self.assertEqual(fk.runner.ctxs[-1].news, ())
        # an ack goes to the OM, then the OM steps once
        steps = fk.om.steps
        core.on_api_result(result(reqs[0], parsed=OrderAck("o1", "open", 0, 16, None), now=100.3), 100.3)
        self.assertEqual(fk.om.results[-1].req.req_id, reqs[0].req_id)
        self.assertEqual(fk.om.steps, steps + 1)
        self.assertIn(reqs[0], [r.req for r in fk.risk.results])

    def test_fills_reach_risk_ctx_and_log(self):
        core, fk = make_core()
        core.take_requests()
        fk.runner.policy = lambda ctx: ([TargetIntent("h1", "PORT", 50, 50, 2.0, 51.0, None, Priority.TAKE, 2.0)]
                                        if ctx.trigger == Trigger.NEWS else [])
        core.on_frames([frame(news("advisory", 1, mono=50.0), mono=50.0)], 50.0)
        req = core.take_requests()[0]
        f = Fill(local_id=req.local_id, order_id="o1", strategy="h1", key=None, symbol="PORT", side=Side.BUY,
                 qty=50, price=50.1, quality=PriceQuality.EXACT, mono=50.2, ref_mid=50.0, expected_cost_bp=20.0,
                 source="ack")
        fk.om.fills_for[req.req_id] = [f]
        fk.runner.policy = lambda ctx: []
        core.on_api_result(result(req, now=50.2), 50.2)
        self.assertEqual(fk.risk.fills, [f])
        self.assertEqual(len(fk.log.kinds("fill")), 1)
        core.run_job("tick", 50.25)
        self.assertEqual(fk.runner.ctxs[-1].fills, (f,))
        core.run_job("tick", 50.5)
        self.assertEqual(fk.runner.ctxs[-1].fills, ())
        self.assertEqual(core.report()["fills"], 1)

    def test_collaborator_exception_is_isolated(self):
        core, fk = make_core()
        core.take_requests()

        def boom(*a, **k):
            raise RuntimeError("coverage broke")

        fk.coverage.on_news = boom
        core.on_frames([frame(news("storm", 1, mono=70.0, age=10), mono=70.0)], 70.0)
        self.assertEqual(len(fk.engine.submits), 1)          # the rest of the batch still went through
        self.assertGreaterEqual(core.report()["exceptions"], 1)
        self.assertTrue(fk.log.kinds("exception"))


class TestReconcile(unittest.TestCase):
    def test_pairing_and_skip(self):
        core, fk = make_core()
        reqs = core.take_requests()
        go, gp = reqs[1], reqs[2]
        core.run_job("reconcile", 0.5)                     # previous cycle outstanding and young: skipped
        self.assertEqual(core.take_requests(), [])
        core.on_api_result(result(go, parsed=[], now=0.6), 0.6)
        self.assertIsNotNone(core._cycle)
        core.on_api_result(result(gp, parsed={}, now=0.7), 0.7)
        self.assertIsNone(core._cycle)
        core.run_job("reconcile", 1.0)
        nxt = core.take_requests()
        self.assertEqual(kinds(nxt), [ApiKind.GET_OPEN, ApiKind.GET_POSITIONS])
        self.assertEqual(nxt[0].cycle, nxt[1].cycle)
        self.assertNotEqual(nxt[0].cycle, go.cycle)
        self.assertEqual([r.req.req_id for r in fk.reconciler.results], [go.req_id, gp.req_id])

    def test_stale_cycle_dropped_after_2s(self):
        # hardening: a cycle is dropped only after timeout_get_s * (1 + get_retries) + 0.5 = 6.5 s (a 2-3 s GET
        # is a slow answer, not a lost one), and a dropped cycle counts as a failed one
        core, fk = make_core()
        core.take_requests()
        core.run_job("reconcile", 2.5)
        self.assertEqual(core.take_requests(), [])
        core.run_job("reconcile", 7.0)
        self.assertEqual(kinds(core.take_requests()), [ApiKind.GET_OPEN, ApiKind.GET_POSITIONS])
        self.assertTrue(fk.log.kinds("reconcile_cycle_dropped"))

    def test_reconcile_down_hold(self):
        core, fk = make_core()
        t = 0.0
        for _ in range(6):
            for r in core.take_requests():
                if r.kind in (ApiKind.GET_OPEN, ApiKind.GET_POSITIONS):
                    core.on_api_result(result(r, status=503, now=t), t)
            t += 3.0
            core.run_job("reconcile", t)
        self.assertIn("reconcile_down", fk.risk.holds(t))

    def test_killed_cycle_triggers_followup(self):
        core, fk = make_core()
        core.take_requests()
        core.on_command(Command("k"), 1.0)
        core.take_requests()
        core.take_control()
        fk.reconciler.rows = ["row"]
        core.run_job("reconcile", 7.0)
        reqs = core.take_requests()
        for r in reqs:
            core.on_api_result(result(r, parsed=[], now=7.1), 7.1)
        out = core.take_requests()
        self.assertEqual(kinds(out), [ApiKind.CANCEL_ALL])
        self.assertEqual(out[0].priority, Priority.KILL)


class TestRounds(unittest.TestCase):
    def test_round_start_once_and_flat_check(self):
        core, fk = make_core()
        core.take_requests()
        fk.clock.pending = [(0, 1)]
        core.on_frames([frame(news("storm_formed", 0, mono=60.0), mono=60.0)], 60.0)
        triggers = [c.trigger for c in fk.runner.ctxs]
        self.assertEqual(triggers.count(Trigger.ROUND_START), 1)
        self.assertNotIn(Trigger.NEWS, triggers)                 # one cycle for the batch
        fk.clock.pending = [(0, 1), (0, 1)]                      # repeated: still once
        core.run_job("tick", 61.0)
        self.assertEqual([c.trigger for c in fk.runner.ctxs].count(Trigger.ROUND_START), 1)
        # a closed snapshot dated the next day: no round event from the clock, no ROUND_START
        core.on_frames([frame(snap(300, "arrived", phase="closed", mono=900.0), mono=900.0)], 900.0)
        self.assertEqual([c.trigger for c in fk.runner.ctxs].count(Trigger.ROUND_START), 1)
        # round 2 with an open position: not_flat (REIN excluded)
        fk.positions.pos["PORT"] = 10
        fk.positions.pos["REIN"] = -50
        fk.clock.pending = [(0, 2)]
        core.run_job("tick", 1000.0)
        alerts = [a for a in fk.log.kinds("alert") if a["alert"] == "not_flat"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["positions"], {"PORT": 10})
        self.assertEqual([r[0] for r in fk.risk.round_starts], [1, 2])

    def test_reservation_and_prewarm_round3(self):
        core, fk = make_core()
        core.take_requests()
        fk.clock.round = 3
        fk.clock.open_mono = 1000.0
        core.run_job("tick", 1000.0 + 100.0)
        self.assertEqual(len(fk.limiter.reservations), 1)
        s, e, fl = fk.limiter.reservations[0]
        self.assertEqual((s, e), (1680.0, 1780.0))
        self.assertEqual(fl, {Priority.TAKE: 150, Priority.HEDGE: 150})
        core.run_job("tick", 1100.25)
        self.assertEqual(len(fk.limiter.reservations), 1)        # not re-reserved without a move
        fk.clock.open_mono = 1001.0                              # anchor moved > 0.5 s: re-reserve
        core.run_job("tick", 1100.5)
        self.assertEqual(fk.limiter.reservations[-1][:2], (1681.0, 1781.0))
        self.assertEqual(core.take_control(), [])
        # TAKE is blocked inside the window (fake limiter honours the floors)
        self.assertEqual(fk.limiter.try_acquire(Priority.TAKE, 1, 1700.0), [])
        self.assertTrue(fk.limiter.try_acquire(Priority.SNIPE, 1, 1700.0))
        core.run_job("tick", 1001.0 + 737.1)
        self.assertEqual(core.take_control(), ["prewarm_all"])
        core.run_job("tick", 1001.0 + 738.0)
        self.assertEqual(core.take_control(), [])
        # inside the reservation a season reset still cancels at KILL
        fk.stores[-1].records.append(FakeRecord("e0-1", "ISLE", Side.BUY, 5, 10.0, strategy="s1r3",
                                                state=_open()))
        core.on_frames([frame(SeasonResetEvent("snapshot_last_seq", 1, 1701.0), mono=1701.0)], 1701.0)
        self.assertEqual(fk.oms[0].cancel_alls[-1][0], Priority.KILL)
        self.assertEqual(fk.limiter.reservations, [])


def _open():
    from ctcbot.types import OrderState
    return OrderState.OPEN


class TestSeasons(unittest.TestCase):
    def test_provisional_confirmed_by_comp_id_replays_buffer(self):
        core, fk = make_core()
        answer_all(core, core.take_requests(), 0.0, comp_id="c1")
        fk.stores[-1].records.append(FakeRecord("e0-1", "PORT", Side.BUY, 5, 50.0, state=_open()))
        prov = SeasonResetEvent("storm_formed_r1", 1, 100.0, provisional=True)
        core.on_frames([frame(prov, news("storm_formed", 0, mono=100.0), news("storm", 1, mono=100.0, age=0),
                              mono=100.0)], 100.0)
        self.assertIn("season_suspect", fk.risk.holds(100.0))
        self.assertEqual(fk.oms[0].cancel_alls, [])                    # nothing cancelled
        self.assertEqual(fk.engine.resets, [])                         # engine kept
        reqs = core.take_requests()
        self.assertIn(ApiKind.GET_TIME, kinds(reqs))                   # GET_TIME at once
        self.assertEqual(sum(len(m) for m, _ in fk.engine.submits), 2)  # the engine keeps ingesting
        core.on_frames([frame(news("storm", 2, mono=102.0, age=2), mono=102.0)], 102.0)
        # a new comp_id confirms it
        t = [r for r in reqs if r.kind == ApiKind.GET_TIME][0]
        core.on_api_result(result(t, parsed=ServerTime("c2", "running", None, 1.0), now=103.0), 103.0)
        self.assertEqual(fk.normalizer.calls[-1], ("confirm", "comp_id"))
        self.assertEqual(fk.engine.resets[-1][0], 1)
        self.assertNotIn("season_suspect", fk.risk.holds(103.0))
        self.assertEqual(fk.oms[0].cancel_alls[-1][0], Priority.KILL)  # live orders cancelled at the change
        replay = fk.engine.submits[-1][0]
        self.assertEqual([m["seq"] for m in replay], [0, 1, 2])         # buffered messages replayed
        self.assertEqual(len(fk.oms), 2)                                # a new OM / store for the new epoch
        self.assertEqual(fk.stores[-1].epoch, 1)
        self.assertEqual(core.report()["resets"]["confirmed"], 1)
        self.assertEqual(core.report()["resets"]["provisional"], 1)

    def test_provisional_rejected_after_timeout(self):
        core, fk = make_core()
        answer_all(core, core.take_requests(), 0.0, comp_id="c1")
        prov = SeasonResetEvent("date_change", 1, 100.0, provisional=True)
        core.on_frames([frame(prov, news("storm", 9, mono=100.0, age=4), mono=100.0)], 100.0)
        core.run_job("tick", 150.0)
        self.assertNotIn(("reject",), fk.normalizer.calls)
        core.run_job("tick", 161.0)
        self.assertIn(("reject",), fk.normalizer.calls)
        self.assertEqual(fk.engine.resets, [])
        self.assertIn("season_suspect", fk.risk.holds(161.0))         # stays until the operator decides
        self.assertTrue([a for a in fk.log.kinds("alert") if a["alert"] == "season_reset_unconfirmed"])
        core.on_command(Command("season", ("keep",)), 162.0)
        self.assertNotIn("season_suspect", fk.risk.holds(162.0))

    def test_snapshot_reset_then_comp_id_is_one_reset(self):
        core, fk = make_core()
        answer_all(core, core.take_requests(), 0.0, comp_id="c1")
        core.on_frames([frame(SeasonResetEvent("snapshot_last_seq", 1, 10.0), mono=10.0)], 10.0)
        self.assertEqual(len(fk.engine.resets), 1)
        core.run_job("clock_poll", 50.0)
        for r in core.take_requests():
            if r.kind == ApiKind.GET_TIME:
                core.on_api_result(result(r, parsed=ServerTime("c2", "running", None, 1.0), now=50.0), 50.0)
        self.assertEqual(len(fk.engine.resets), 1)
        self.assertNotIn(("force", "comp_id"), fk.normalizer.calls)
        self.assertEqual(core.report()["resets"]["merged"], 1)
        # a later comp change (outside the merge rule) is a real reset
        for t in (60.0, 61.5):            # hardening: a lone new comp_id needs a second answer >= 1 s later
            core.run_job("clock_poll", t)
            for r in core.take_requests():
                if r.kind == ApiKind.GET_TIME:
                    core.on_api_result(result(r, parsed=ServerTime("c3", "running", None, 1.0), now=t), t)
            if t == 60.0:
                self.assertEqual(len(fk.engine.resets), 1)
                self.assertIn("comp_id_suspect", fk.risk.holds(60.0))
        self.assertEqual(len(fk.engine.resets), 2)

    def test_comp_id_then_snapshot_is_one_reset(self):
        core, fk = make_core()
        answer_all(core, core.take_requests(), 0.0, comp_id="c1")
        for t in (20.0, 21.0):            # hardening: confirmed by a second answer >= 1 s later
            core.run_job("clock_poll", t)
            for r in core.take_requests():
                if r.kind == ApiKind.GET_TIME:
                    core.on_api_result(result(r, parsed=ServerTime("c2", "running", None, 1.0), now=t), t)
        self.assertEqual(fk.normalizer.calls, [("force", "comp_id")])
        self.assertEqual(len(fk.engine.resets), 1)
        # the normalizer's state is cleared, so the following -1 snapshot yields no reset event
        core.on_frames([frame(snap(-1, "arrived", mono=22.0), mono=22.0)], 22.0)
        self.assertEqual(len(fk.engine.resets), 1)
        self.assertEqual(fk.clock.resets[-1], (1, "c2"))

    def test_season_new_operator(self):
        core, fk = make_core()
        core.take_requests()
        core.on_command(Command("season", ("new",)), 5.0)
        self.assertEqual(fk.normalizer.calls[-1], ("force", "operator"))
        self.assertEqual(len(fk.engine.resets), 1)

    def test_engine_log_name_per_epoch(self):
        d = testpaths.tmpdir("core_elog")
        core, fk = make_core(mode="host", run_dir=d)
        core.take_requests()
        core.on_frames([frame(SeasonResetEvent("snapshot_last_seq", 1, 10.0), mono=10.0)], 10.0)
        self.assertEqual(fk.engine.resets[-1], (1, os.path.join(d, "engine_state_testrun_e1.jsonl")))


class TestFeedDown(unittest.TestCase):
    def _storm_core(self):
        core, fk = make_core()
        answer_all(core, core.take_requests(), 0.0)
        fk.clock.open_mono = 0.0
        return core, fk

    def test_feed_down_one_snipe_cancel_and_release(self):
        core, fk = self._storm_core()
        core.on_frames([frame(ConnEvent("ws0#1", True, 100.0), mono=100.0)], 100.0)
        core.on_frames([frame(news("storm", 10, mono=100.0, age=40), mono=100.0)], 100.0)
        t = 100.0
        while t < 115.0:
            t += 0.25
            core.run_job("tick", t)
        snipes = [c for c in fk.om.cancel_alls if c[2] == "feed_down"]
        self.assertEqual(len(snipes), 1)
        self.assertEqual(snipes[0][0], Priority.SNIPE)
        self.assertIn("feed_down", fk.risk.holds(t))
        # the feed comes back: a released snapshot alone is not enough in the storm
        core.on_frames([frame(snap(50, "released", relevant=True, mono=t), mono=t)], t)
        core.run_job("tick", t + 0.25)
        self.assertIn("feed_down", fk.risk.holds(t))
        core.on_frames([frame(news("storm", 11, mono=t + 0.5, age=56), mono=t + 0.5)], t + 0.5)
        core.on_frames([frame(news("storm", 12, mono=t + 1.0, age=58), mono=t + 1.0)], t + 1.0)
        core.run_job("tick", t + 1.25)
        self.assertNotIn("feed_down", fk.risk.holds(t + 1.25))
        self.assertEqual(len([c for c in fk.om.cancel_alls if c[2] == "feed_down"]), 1)

    def test_feed_down_in_round3_after_s1r3_and_rests_return(self):
        core, fk = self._storm_core()
        fk.clock.round = 3
        fk.clock.open_mono = 0.0
        rest = [RestIntent("s1r3", "s1r3:ISLE:buy", "ISLE", Side.BUY, 9.5, Priority.SNIPE)]
        state = {"on": True}
        fk.runner.policy = lambda ctx: rest if state["on"] else []
        core.on_frames([frame(news("damage_report", 900, rnd=3, mono=741.0, stage="final"), mono=741.0)], 741.0)
        self.assertEqual([r.kind for r in core.take_requests()], [ApiKind.PLACE])
        t = 741.0
        while t < 759.5:                       # aftermath watchdog 8 s (down at 749.25), then 10 s down
            t += 0.25
            core.run_job("tick", t)
            if t < 759.25:
                self.assertEqual([c for c in fk.om.cancel_alls if c[2] == "feed_down"], [])
        fd = [c for c in fk.om.cancel_alls if c[2] == "feed_down"]
        self.assertEqual(len(fd), 1)
        self.assertEqual(fd[0][0], Priority.SNIPE)
        # aftermath: a released snapshot alone releases the hold; the sweep re-emits the rests
        core.on_frames([frame(snap(901, "released", relevant=True, rnd=3, phase="aftermath", mono=t), mono=t)], t)
        core.run_job("tick", t + 0.25)
        self.assertNotIn("feed_down", fk.risk.holds(t + 0.25))
        self.assertTrue(any(isinstance(i, RestIntent) for c in fk.runner.ctxs[-1:] for i in rest))

    def test_unknown_orders_hold(self):
        core, fk = self._storm_core()
        core.on_frames([frame(news("storm", 1, mono=100.0, age=40), mono=100.0)], 100.0)
        fk.reconciler.age = 3.5
        core.run_job("tick", 100.25)
        h = fk.risk.holds(100.25)
        self.assertIn("unknown_orders", h)
        self.assertEqual(h["unknown_orders"]["exempt"], {"s1r3"})
        fk.reconciler.age = None
        core.run_job("tick", 100.5)
        self.assertNotIn("unknown_orders", fk.risk.holds(100.5))


class TestCommands(unittest.TestCase):
    def test_kill_cancel_all_and_freeze_first_nothing_after(self):
        core, fk = make_core()
        answer_all(core, core.take_requests(), 0.0)
        fk.runner.policy = lambda ctx: ([TargetIntent("s3", "PORT", 16, 16, 2.0, 51.0, None, Priority.TAKE, 2.0)]
                                        if ctx.trigger == Trigger.NEWS else [])
        core.on_frames([frame(news("storm", 3, mono=100.0, age=40), mono=100.0)], 100.0)
        self.assertEqual(kinds(core._outbox), [ApiKind.PLACE])           # a PLACE not yet handed out
        core.on_command(Command("k"), 100.1)
        out = core.take_requests()
        self.assertEqual(out[0].kind, ApiKind.CANCEL_ALL)
        self.assertEqual(out[0].priority, Priority.KILL)
        self.assertNotIn(ApiKind.PLACE, kinds(out))
        self.assertEqual(core.take_control(), ["freeze"])
        purged = [r for r in fk.om.results if r.error == "purged"]
        self.assertEqual(len(purged), 1)
        self.assertFalse(purged[0].sent)
        self.assertTrue(core.killed())
        # FROZEN: ticks emit no PLACE
        core.run_job("tick", 100.25)
        self.assertNotIn(ApiKind.PLACE, kinds(core.take_requests()))
        core.on_command(Command("unfreeze-kill"), 101.0)
        self.assertEqual(core.take_control(), ["unfreeze"])
        self.assertFalse(core.killed())

    def test_cx_and_shutdown_at_kill(self):
        core, fk = make_core()
        core.take_requests()
        core.on_command(Command("cx"), 1.0)
        self.assertEqual(fk.om.cancel_alls[-1][0], Priority.KILL)
        self.assertEqual(core.take_requests()[0].priority, Priority.KILL)
        core.shutdown(2.0)
        self.assertEqual(core.take_requests(), [])                      # no live non-dry order: nothing to cancel
        fk.stores[-1].records.append(FakeRecord("e0-9", "PORT", Side.BUY, 5, 50.0, state=_open()))
        core.shutdown(3.0)
        out = core.take_requests()
        self.assertEqual(kinds(out), [ApiKind.CANCEL_ALL])
        self.assertEqual(out[0].priority, Priority.KILL)
        fk.stores[-1].records[-1].dry = True
        core.shutdown(4.0)
        self.assertEqual(core.take_requests(), [])                      # dry orders need no cancel

    def test_pause_resume_and_holds(self):
        core, fk = make_core()
        core.take_requests()
        core.on_command(Command("p"), 1.0)
        self.assertIn("operator_pause", fk.risk.holds(1.0))
        for h in ("drawdown", "restart", "feed_down", "auth", "season_suspect", "reconcile_down"):
            fk.risk.hold(h, 1.0, sticky=True)
        core.on_command(Command("r"), 2.0)
        self.assertEqual(set(fk.risk.holds(2.0)), {"reconcile_down"})

    def test_disable_enable_cap_adjust(self):
        core, fk = make_core()
        core.take_requests()
        core.on_command(Command("x", ("PORT",)), 1.0)
        self.assertIn("PORT", fk.risk.disabled())
        self.assertEqual(fk.om.cancel_alls[-1][1], "PORT")
        core.on_command(Command("e", ("PORT",)), 1.1)
        self.assertNotIn("PORT", fk.risk.disabled())
        core.on_command(Command("b", ("120",)), 1.2)
        self.assertEqual(fk.limiter.cap, 120)
        core.on_command(Command("f", ("PORT", "44.5")), 1.3)
        self.assertEqual(fk.engine.adjust[-1], ("PORT", 0.0, 44.5))
        core.on_command(Command("m", ("HAVEN", "1.1")), 1.4)
        core.on_command(Command("a", ("HAVEN", "-2")), 1.5)
        self.assertEqual(fk.engine.adjust[-1], ("HAVEN", 1.1, -2.0))
        core.on_command(Command("f", ("PORT", "off")), 1.6)
        self.assertEqual(fk.engine.adjust[-1], ("PORT", 1.0, 0.0))

    def test_caseok_and_lawok_routing(self):
        core, fk = make_core(case_diff=FakeCaseDiff(frozenset({"GRID", "ARCH"}), True, ("x",)))
        core.take_requests()
        self.assertEqual(core.season.case_diff, frozenset({"GRID", "ARCH"}))
        self.assertTrue(core.season.timing_diff)
        core.on_command(Command("caseok", ("GRID",)), 1.0)
        self.assertEqual(core.season.case_diff, frozenset({"ARCH"}))
        core.on_command(Command("caseok", ("timing",)), 1.1)
        self.assertFalse(core.season.timing_diff)
        core.on_command(Command("caseok", ("all",)), 1.2)
        self.assertEqual(core.season.case_diff, frozenset())
        core.on_command(Command("lawok", ("PORT",)), 1.3)
        self.assertEqual(fk.runner.commands[-1], Command("lawok", ("PORT",)))
        self.assertEqual(fk.runner.ctxs, [])                # a command does not consume the decision inputs
        core.on_command(Command("h1", ("0.5",)), 1.4)
        self.assertEqual(fk.runner.commands[-1].name, "h1")

    def test_bad_and_unknown_commands_never_raise(self):
        core, fk = make_core()
        core.take_requests()
        for c in (Command("x", ()), Command("x", ("NOPE",)), Command("b", ("abc",)), Command("f", ("PORT", "nan")),
                  Command("zzz", ("1",)), Command("season", ("maybe",)), Command("caseok", ())):
            core.on_command(c, 1.0)
        self.assertEqual(len(fk.log.kinds("command_error")), 7)
        self.assertEqual(fk.runner.commands, [])
        core.on_command("garbage", 1.0)                                  # not even a Command
        self.assertEqual(len(fk.log.kinds("command_error")), 8)

    def test_show_commands_produce_output(self):
        core, fk = make_core()
        core.take_requests()
        for n in ("st", "pos", "ord", "ann", "holds", "help"):
            core.on_command(Command(n), 1.0)
        out = core.take_output()
        self.assertEqual(len(out), 6)
        self.assertTrue(out[0].startswith("[DRY]"))

    def test_quit(self):
        core, fk = make_core()
        core.take_requests()
        core.on_command(Command("q"), 1.0)
        self.assertTrue(core.quit_requested)

    def test_eng_restart(self):
        d = testpaths.tmpdir("core_eng")
        core, fk = make_core(mode="host", run_dir=d)
        core.take_requests()
        cb = lambda: None  # noqa: E731
        core.set_engine_listener(cb)
        old = core.engine
        core.on_command(Command("eng", ("restart",)), 5.0)
        self.assertEqual(old.closed, 1)
        self.assertIsNot(core.engine, old)
        self.assertIs(core.engine.listener, cb)
        core_sim, _ = make_core(mode="sim")
        core_sim.on_command(Command("eng", ("restart",)), 1.0)
        self.assertTrue(_.log.kinds("command_error"))


class TestAuthAndThrottle(unittest.TestCase):
    def test_401_holds_auth_and_429_throttles(self):
        core, fk = make_core()
        core.take_requests()
        req = ApiRequest(req_id=999, kind=ApiKind.PLACE, symbol="PORT", side=Side.BUY, qty=1, price=1.0)
        core.on_api_result(result(req, status=401, now=1.0), 1.0)
        self.assertIn("auth", fk.risk.holds(1.0))
        req2 = ApiRequest(req_id=1000, kind=ApiKind.PLACE, symbol="PORT", side=Side.BUY, qty=1, price=1.0)
        core.on_api_result(result(req2, status=429, now=2.0), 2.0)
        self.assertEqual(fk.limiter.throttled, [(2.0, 5.0)])
        self.assertEqual(core.report()["rejects"], 2)
        # a 401 on a GET also reaches the risk manager
        fk.risk.release("auth", 3.0)
        core.run_job("clock_poll", 10.0)
        t = [r for r in core.take_requests() if r.kind == ApiKind.GET_TIME][0]
        core.on_api_result(result(t, status=403, now=10.0), 10.0)
        self.assertIn("auth", fk.risk.holds(10.0))


class TestLawCheckAndFV(unittest.TestCase):
    def test_on_report_before_engine_submit(self):
        core, fk = make_core()
        core.take_requests()
        core.on_frames([frame(news("storm", 280, rnd=1, mono=569.0, age=470),
                              news("damage_report", 289, rnd=1, mono=570.0, stage="preliminary",
                                   industry_loss_musd=166.3), mono=570.0)], 570.0)
        self.assertEqual(fk.law_check.reports, [(1, "prelim", 289)])
        self.assertLess(fk.calls.index("law_check.on_report"), fk.calls.index("engine.submit"))
        self.assertEqual(core.season.prelim[1]["seq"], 289)
        # round 3 reports do not run the law check, but they are season info
        core.on_frames([frame(news("damage_report", 990, rnd=3, mono=740.0, stage="final",
                                   industry_loss_musd=100.0), mono=740.0)], 740.0)
        self.assertEqual(len(fk.law_check.reports), 1)
        self.assertEqual(core.season.final_seq[3], 990)
        self.assertEqual(core.season.loss_known, 100.0)

    def test_snapshot_damage_list_fills_season(self):
        core, fk = make_core()
        core.take_requests()
        dmg = [{"round": 1, "stage": "final", "damage_pct": {}, "industry_loss_musd": 538.3},
               {"round": 2, "stage": "preliminary", "damage_pct": {}, "industry_loss_musd": 373.5}]
        core.on_frames([frame(snap(600, "arrived", rnd=3, damage=dmg, mono=5.0), mono=5.0)], 5.0)
        s = core.season
        self.assertEqual(s.final_seq, {1: -1})
        self.assertIn(2, s.prelim)
        self.assertAlmostEqual(s.loss_known, 538.3)

    def test_forward_snapshots_modes(self):
        for mode, expect in (("relevant", [True, False]), ("all", [True, True]), ("never", [False, False])):
            core, fk = make_core(cfg_over={"engine.forward_snapshots": mode})
            core.take_requests()
            core.on_frames([frame(snap(10, "released", relevant=True, mono=1.0), mono=1.0)], 1.0)
            n1 = len(fk.engine.submits)
            core.on_frames([frame(snap(10, "released", relevant=False, mono=2.0), mono=2.0)], 2.0)
            n2 = len(fk.engine.submits)
            self.assertEqual([n1 == 1, n2 - n1 == 1], expect, mode)
            self.assertEqual(len(fk.coverage.snaps), 1)                 # laws/coverage: relevant only, any mode
            self.assertEqual(len(fk.laws.snaps), 1)

    def test_on_fair_values_dedup_by_version(self):
        core, fk = make_core()
        core.take_requests()
        fv = FairValueSnapshot(version=1, values={s: (50.0, 1.0) for s in SYMBOLS}, season_epoch=0, round=1,
                               seq_hi=5, msg_time=None, included_mono=1.0, computed_mono=1.0, compute_ms=10.0,
                               ok=True)
        fk.engine.fv = fv
        core.on_fair_values(1.0)
        core.on_fair_values(1.1)
        self.assertEqual(len(fk.law_check.fvs), 1)
        self.assertEqual([c.trigger for c in fk.runner.ctxs].count(Trigger.FV), 1)
        self.assertIs(fk.runner.ctxs[-1].fv, fv)

    def test_engine_down_alert(self):
        core, fk = make_core()
        core.take_requests()
        core.run_job("tick", 1.0)
        fk.engine.alive = False
        core.run_job("tick", 1.25)
        self.assertTrue([a for a in fk.log.kinds("alert") if a["alert"] == "engine_down"])

    def test_laws_unavailable(self):
        core, fk = make_core(laws_module=None)
        self.assertIsNone(core.laws)
        self.assertTrue([a for a in fk.log.kinds("alert") if a["alert"] == "laws_unavailable"])
        core.on_frames([frame(news("storm", 1, mono=70.0, age=10), mono=70.0)], 70.0)
        self.assertEqual(len(fk.engine.submits), 1)


class TestRestoreAndPromotion(unittest.TestCase):
    def _saved(self, dry_run=True):
        return {"comp_id": "c1", "dry_run": dry_run, "engine_log": "engine_state_old_e0.jsonl",
                "orders": {"o": 1}, "om": {"m": 1}, "risk": {"r": 1}, "runner": {"x": 1}, "law_check": {"l": 1},
                "positions": {"PORT": 30}, "cash": -1500.0, "round_peak_mtm": 10.0,
                "limiter_ledger_wall": [1.8e9 - 10.0, 1.8e9 - 100.0],
                "season": {"final": {"1": {"type": "damage_report", "industry_loss_musd": 500.0}},
                           "final_seq": {"1": 290}, "caseok": ["GRID"], "timing_ack": True},
                "engine_messages": [{"type": "storm", "seq": 1, "round": 1, "age": 0},
                                    {"type": "announcement", "seq": 0, "round": 1, "headline": "h", "symbols": []}]}

    def test_restore_same_mode(self):
        core, fk = make_core(restore=self._saved(), case_diff=FakeCaseDiff(frozenset({"GRID", "ARCH"}), True))
        self.assertEqual(fk.stores[-1].loaded, {"o": 1})
        self.assertEqual(fk.om.loaded, {"m": 1})
        self.assertEqual(fk.risk.loaded, {"r": 1})
        self.assertEqual(fk.runner.loaded, {"x": 1})
        self.assertEqual(fk.law_check.loaded, {"l": 1})
        self.assertEqual(fk.positions.pos["PORT"], 30)
        self.assertEqual(fk.positions.cash, -1500.0)
        self.assertEqual(len(fk.coverage.rebuilt), 2)
        self.assertEqual(len(fk.laws.rebuilt), 2)
        self.assertIn("restart", fk.risk.holds(0.0))
        self.assertEqual(fk.limiter.restored, [-10.0])            # only entries younger than 61 s, wall -> mono
        s = core.season
        self.assertEqual(s.final_seq, {1: 290})
        self.assertEqual(s.case_diff, frozenset({"ARCH"}))
        self.assertFalse(s.timing_diff)
        self.assertEqual(len(s.announcements), 1)
        self.assertEqual(fk.om.dropped, 0)
        self.assertEqual(core.state_dict(0.0)["engine_log"], "engine_state_old_e0.jsonl")

    def test_promotion_drops_dry_and_asks_h1(self):
        core, fk = make_core(restore=self._saved(dry_run=True), promotion=True, live_label=True)
        self.assertEqual(fk.om.dropped, 1)
        self.assertEqual(fk.runner.promoted, 1)
        self.assertEqual(fk.positions.pos["PORT"], 0)              # rebuilt by the first reconcile
        self.assertEqual(fk.limiter.restored, [])                  # ledger not restored
        self.assertIn("restart", fk.risk.holds(0.0))
        self.assertTrue([a for a in fk.log.kinds("alert") if a["alert"] == "h1_size_needed"])
        self.assertEqual(core.season.final_seq, {1: 290})          # season info kept

    def test_auto_resume(self):
        core, fk = make_core(restore=self._saved(), cfg_over={"app.restart_auto_resume": True})
        answer_all(core, core.take_requests(), 0.0, comp_id="c1")
        core.run_job("tick", 0.25)
        self.assertIn("restart", fk.risk.holds(0.25))
        core.on_frames([frame(snap(5, "released", mono=1.0), mono=1.0)], 1.0)
        core.run_job("tick", 1.25)
        self.assertNotIn("restart", fk.risk.holds(1.25))


class TestStatusReportState(unittest.TestCase):
    def test_status_and_format(self):
        core, fk = make_core(live_label=False)
        answer_all(core, [r for r in core.take_requests() if r.kind != ApiKind.GET_TIME], 213.0)
        fk.positions.pos["PORT"] = 200
        fk.positions.pos["HAVEN"] = -120
        core.on_frames([frame(news("storm", 70, mono=213.0, age=153), mono=213.0)], 213.0)
        core.run_job("tick", 213.4)
        st = core.status(213.4)
        line = format_status(st)
        self.assertTrue(line.startswith("[DRY] NORMAL e0 r1 storm t=213.4"))
        self.assertIn("PORT+200 HAVEN-120", line)
        self.assertIn("act 0/150", line)
        self.assertIn("ws 1/1 0.4s wm70", line)
        self.assertIn("holds -", line)
        core2, _ = make_core(live_label=True)
        self.assertTrue(format_status(core2.status(1.0)).startswith("[LIVE]"))

    def test_persist_job_writes_state(self):
        from ctcbot.app.persist import load_state
        d = testpaths.tmpdir("core_persist")
        core, fk = make_core(mode="host", run_dir=d)
        answer_all(core, core.take_requests(), 0.0, comp_id="cX")
        core.run_job("persist", 5.0)
        st = load_state(os.path.join(d, "state.json"))
        for k in ("saved_wall", "run_id", "cfg_hash", "comp_id", "season_epoch", "round", "dry_run", "positions",
                  "cash", "round_peak_mtm", "orders", "risk", "runner", "law_check", "limiter_ledger_wall",
                  "engine_log", "season"):
            self.assertIn(k, st)
        self.assertEqual(st["comp_id"], "cX")
        self.assertTrue(st["dry_run"])
        self.assertEqual(st["engine_log"], "engine_state_testrun_e0.jsonl")
        self.assertEqual(st["limiter_ledger_wall"], [1.8e9 - 4.0, 1.8e9 - 3.0])     # wall 1.8e9 at mono 5

    def test_report_keys(self):
        core, fk = make_core()
        rep = core.report()
        for k in ("fills", "actions", "rejects", "alerts", "exceptions", "holds", "resets"):
            self.assertIn(k, rep)


class TestIntegrationDefaultFactories(unittest.TestCase):
    """The real A/B/C modules behind default_factories (skips until every group has delivered)."""

    def test_real_parts_construct_and_tick(self):
        mods = testpaths.integration("ctcbot.config", "ctcbot.case", "ctcbot.normalize", "ctcbot.clock",
                                     "ctcbot.ratelimit", "ctcbot.state.books", "ctcbot.state.positions",
                                     "ctcbot.state.orders", "ctcbot.exec.risk", "ctcbot.exec.order_manager",
                                     "ctcbot.exec.reconcile", "ctcbot.models.coverage", "ctcbot.models.laws",
                                     "ctcbot.strategies.runner", "ctcbot.strategies.base")
        config, case_mod = mods[0], mods[1]
        cfg = config.load_config()
        case = case_mod.CaseInfo.from_file(testpaths.require(testpaths.RESEARCH_CASE))
        cd = case_mod.diff_case(case.raw, case.raw)
        calls = CallLog()
        eng = FakeEngine(calls)
        log = ListLog()
        cost = None
        try:
            from ctcbot.models.cost_model import CostModel
            cost = CostModel(testpaths.require(testpaths.COST_MODEL), cfg)
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"cost model unavailable: {e}")
        core = BotCore(cfg, case, cd, eng, _Time(), log, mode="sim", run_dir=None, run_id="it", laws_module=None,
                       cost=cost)
        core.start(0.0)
        self.assertEqual(kinds(core.take_requests()), [ApiKind.GET_TIME, ApiKind.GET_OPEN, ApiKind.GET_POSITIONS])
        for t in (0.25, 0.5, 0.75, 1.0):
            core.run_job("tick", t)
        self.assertEqual(core.report()["exceptions"], 0, log.kinds("exception"))
        self.assertEqual(core.limits["PORT"], 200)


if __name__ == "__main__":
    unittest.main()
