"""Shared fakes for group B's unit tests (no test cases here).

Group B's tests must not depend on other groups' behaviour, so the collaborators from groups A and C are
replaced by the small fakes below: a dict-backed ``FakeConfig`` holding the default values B reads
(checked against ``config/default.json`` by ``test_b_books``), a ``FakeCase`` with the research limits, a
``FakeSchedule`` with the research timing, a sliding-window ``FakeLimiter`` and a stub for
``ctcbot.case.strategy_limits`` that ``BTestCase`` patches into the B modules for each test.
"""
from __future__ import annotations

import copy
import math
import os
import sys
import unittest
from typing import Any, Mapping
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.dirname(HERE)
if BOT not in sys.path:
    sys.path.insert(0, BOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

from ctcbot.types import (SYMBOLS, ApiKind, ApiRequest, ApiResult, BookEvent, BookLevel, ClockReading,  # noqa: E402
                          FeedHealth, ListLog, OpenOrderRow, OrderAck, Phase, PositionRow, Priority, QuoteEvent,
                          Side, Ticket)

CASE_LIMITS: dict[str, int] = {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500,
                               "MAT": 700, "GEN": 600, "HAVEN": 250, "REIN": 200, "ARCH": 50, "REBLD": 100}

# The values group B reads, as in DESIGN.md section 8 (test_b_books compares them with config/default.json).
B_CONFIG: dict[str, Any] = {
    "exchange": {"price_decimals": 2},
    "feed": {"storm_obs_stale_s": 6.0},
    "app": {"kill_followup_s": 0.25},
    "orders": {"target_ttl_s": 0.6, "rest_tolerance_frac": 0.05, "rest_max_orders_per_key": 2,
               "price_eq_tol": 0.005},
    "sizing": {"scale": 1.0},
    "rate": {"bot_cap_per_min": 150, "window_s": 61.0, "floors": {"KILL": 0, "SNIPE": 1, "HEDGE": 4, "TAKE": 10},
             "kill_overdraft": 20, "throttle_freeze_s": 5.0},
    "risk": {
        "drawdown_usd": 20000.0, "unknown_grace_s": 1.0, "fat_finger_frac": 0.25, "snipe_band_frac": 0.60,
        "max_marketable_from_mid": 0.015, "max_clip_frac": 0.5, "max_snipe_frac": 2.0,
        "max_resting_per_symbol": 8, "block_marketable_before": ["open", 3.0], "fill_sanity_n": 3,
        "fill_sanity_mult": 3.0, "reject_disable_n": 5, "reject_disable_window_s": 30.0, "http_5xx_streak": 3,
        "http_5xx_hold_s": 10.0, "pos_skip_alert_s": 5.0,
    },
    "guards": {"opening": {"max_half_spread_bp": 300.0}},
}


def _walk(d: Any, dotted: str) -> Any:
    cur = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(dotted)
    return cur


_MISSING = object()


class FakeConfig:
    """Duck-typed stand-in for ``ctcbot.config.Config``."""

    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        self.d: dict = copy.deepcopy(B_CONFIG)
        for k, v in (overrides or {}).items():
            cur = self.d
            parts = k.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = v
        self.hash, self.root, self.sources, self.live_allowed = "fakecfg00000", BOT, (), True

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        try:
            return _walk(self.d, dotted)
        except KeyError:
            if default is _MISSING:
                raise
            return default

    def sym(self, symbol: str) -> dict:
        return self.d.get("symbols", {}).get(symbol, {})

    def path(self, p: str | None) -> str | None:
        return p


class FakeCase:
    """Duck-typed stand-in for ``ctcbot.case.CaseInfo`` (only the fields B reads)."""

    def __init__(self, limits: Mapping[str, int] | None = None) -> None:
        self.limits: dict[str, int] = dict(limits or CASE_LIMITS)
        self.symbols: tuple[str, ...] = tuple(self.limits)
        self.raw: dict = {}
        self.baskets: dict = {}
        self.kinds: dict = {}
        self.timing: dict = {"pre_storm": 60, "storm": 480, "aftermath": 240}


class FakeSchedule:
    """Research timing: open 0, storm 60-540, prelim 570, final 740, close 780."""
    open, storm_start, storm_end, prelim, final, close = 0.0, 60.0, 540.0, 570.0, 740.0, 780.0
    round_len, speed = 780.0, 30.0

    def at(self, spec: Any) -> float:
        anchor, off = spec
        return float(getattr(self, anchor)) + float(off)

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


def fake_strategy_limits(case: Any, cfg: Any) -> dict[str, int]:
    scale = float(cfg.get("sizing.scale"))
    return {s: int(math.floor(case.limits.get(s, 0) * scale + 1e-9)) for s in case.limits}


class FakeLimiter:
    """Sliding-window action limiter with the semantics of section 4.A (enough for B's tests)."""

    def __init__(self, cap: int = 150, window_s: float = 61.0, floors: Mapping[Priority, int] | None = None,
                 kill_overdraft: int = 20) -> None:
        self._cap = cap
        self.window_s = window_s
        self.floors = dict(floors if floors is not None else
                           {Priority.KILL: 0, Priority.SNIPE: 1, Priority.HEDGE: 4, Priority.TAKE: 10})
        self.kill_overdraft = kill_overdraft
        self.granted: list[tuple[float, Priority, str]] = []
        self.denied: list[tuple[float, Priority, str]] = []
        self.reserve_window: tuple[float, float] | None = None
        self.reserve_floors: dict[Priority, int] = {}

    @property
    def cap(self) -> int:
        return self._cap

    @cap.setter
    def cap(self, v: int) -> None:
        self._cap = max(0, min(int(v), 170))

    def used(self, now: float) -> int:
        return sum(1 for t, _, _ in self.granted if t > now - self.window_s)

    def available(self, now: float) -> int:
        return self._cap - self.used(now)

    def reserved(self, now: float) -> bool:
        return self.reserve_window is not None and self.reserve_window[0] <= now < self.reserve_window[1]

    def floor(self, priority: Priority, now: float) -> int:
        f = self.floors.get(priority, 0)
        if self.reserved(now):
            f = max(f, self.reserve_floors.get(priority, 0))
        return f

    def try_acquire(self, priority: Priority, n: int, now: float, tag: str = "") -> list[Ticket]:
        if priority == Priority.KILL:
            ok = self.used(now) + n <= self._cap + self.kill_overdraft
        else:
            ok = self.available(now) - n >= self.floor(priority, now)
        if not ok:
            self.denied.append((now, priority, tag))
            return []
        for _ in range(n):
            self.granted.append((now, priority, tag))
        return [Ticket(priority, now, tag) for _ in range(n)]

    def max_in_window(self, include_kill: bool = False) -> int:
        ts = sorted(t for t, p, _ in self.granted if include_kill or p != Priority.KILL)
        best, j = 0, 0
        for i, t in enumerate(ts):
            while ts[j] <= t - self.window_s:
                j += 1
            best = max(best, i - j + 1)
        return best


class Counter:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> int:
        self.n += 1
        return self.n


def clock(t: float = 100.0, phase: Phase | None = None, running: bool = True, round_: int = 1,
          known: bool = True, quality: str = "feed") -> ClockReading:
    ph = phase if phase is not None else FakeSchedule().phase(t)
    return ClockReading(known=known, season_epoch=0, comp_id="c1", round=round_, date="1802-05-10", t=t,
                        phase=ph, running=running, quality=quality, anchor_age_s=0.1)


def feed(storm_age: float | None = 0.5, up: bool = True) -> FeedHealth:
    return FeedHealth(up=up, connections_up=2, connections=2, last_frame_age_s=0.1, last_news_age_s=0.5,
                      last_storm_obs_age_s=storm_age, round_max_age_gap_s=2.0, down_since_mono=None, dup_news=0,
                      seq_gaps=0, wm=10, max_seq=10)


def book_ev(sym: str, bid: float | None, ask: float | None, ts: str = "2026-10-23T10:00:00.000000",
            recv: float = 0.0, bq: int = 100, aq: int = 100) -> BookEvent:
    bids = (BookLevel(bid, bq), BookLevel(round(bid - 0.1, 2), bq * 2)) if bid is not None else ()
    asks = (BookLevel(ask, aq), BookLevel(round(ask + 0.1, 2), aq * 2)) if ask is not None else ()
    return BookEvent(symbol=sym, bids=bids, asks=asks, server_ts=ts, recv_mono=recv)


def quote_ev(sym: str, bid: float | None, ask: float | None, ts: str, recv: float = 0.0) -> QuoteEvent:
    return QuoteEvent(symbol=sym, bid=bid, ask=ask, bid_size=10 if bid is not None else None,
                      ask_size=10 if ask is not None else None, server_ts=ts, recv_mono=recv)


def result(req: ApiRequest, status: int | None = 200, parsed: Any = None, data: Any = None, sent: bool = True,
           dry: bool = False, error: str | None = None, t0: float = 0.0, t1: float | None = None) -> ApiResult:
    return ApiResult(req=req, status=status, data=data if data is not None else {}, parsed=parsed, error=error,
                     sent_mono=t0, done_mono=t0 if t1 is None else t1, dry_run=dry, sent=sent)


def ack(order_id: str | None, status: str = "open", filled: int | None = 0, remaining: int | None = None,
        avg: float | None = None) -> OrderAck:
    return OrderAck(order_id=order_id, status=status, filled_qty=filled, remaining=remaining, avg_fill_price=avg)


def get_req(kind: ApiKind, cycle: int, rid: int = 10_000) -> ApiRequest:
    return ApiRequest(req_id=rid, kind=kind, cycle=cycle)


def prow(sym: str, qty: int, avg: float | None = None, realized: float | None = None) -> PositionRow:
    return PositionRow(symbol=sym, qty=qty, avg_price=avg, mark=None, realized=realized, unrealized=None)


def orow(oid: str, sym: str, side: Side, price: float, qty: int, remaining: int) -> OpenOrderRow:
    return OpenOrderRow(order_id=oid, symbol=sym, side=side, price=price, qty=qty, remaining=remaining, status="open")


class BTestCase(unittest.TestCase):
    """Patches ``strategy_limits`` (group A) out of the B modules for the duration of each test."""

    def setUp(self) -> None:
        super().setUp()
        from ctcbot.exec import order_manager, risk
        for mod in (risk, order_manager):
            p = mock.patch.object(mod, "_scaled_limits", fake_strategy_limits)
            p.start()
            self.addCleanup(p.stop)
        self.log = ListLog()

    def alerts(self, kind: str | None = None) -> list[dict]:
        return [f for f in self.log.kinds("alert") if kind is None or f.get("alert") == kind]


class Rig:
    """A full group-B stack (store, positions, books, risk, OM, reconciler) with fake A/C collaborators."""

    def __init__(self, log: ListLog, cfg: FakeConfig | None = None, limiter: FakeLimiter | None = None,
                 risk: Any = None, symbols: tuple[str, ...] = SYMBOLS) -> None:
        from ctcbot.exec.order_manager import OrderManager
        from ctcbot.exec.reconcile import Reconciler
        from ctcbot.exec.risk import RiskManager
        from ctcbot.state.books import BookStore
        from ctcbot.state.orders import OrderStore
        from ctcbot.state.positions import PositionBook
        self.log = log
        self.cfg = cfg or FakeConfig()
        self.case = FakeCase()
        self.schedule = FakeSchedule()
        self.store = OrderStore(1)
        self.positions = PositionBook(symbols)
        self.books = BookStore(symbols, self.cfg)
        self.risk = risk if risk is not None else RiskManager(self.cfg, self.case, self.schedule, log)
        self.limiter = limiter or FakeLimiter()
        self.ids = Counter()
        self.om = OrderManager(self.cfg, self.case, self.store, self.positions, self.books, self.risk, self.limiter,
                               log, self.ids)
        self.rec = Reconciler(self.store, self.positions, self.cfg, log)

    _book_n = 0

    def set_book(self, sym: str, bid: float | None, ask: float | None, now: float = 0.0, qty: int = 100) -> None:
        """Install a two-level book with a strictly newer server timestamp than the previous one."""
        import datetime as dt
        Rig._book_n += 1
        ts = (dt.datetime(2026, 10, 23, 10, 0, 0) + dt.timedelta(milliseconds=Rig._book_n)).isoformat()
        self.books.on_book(book_ev(sym, bid, ask, ts=ts, recv=now, bq=qty, aq=qty))


class PassRisk:
    """A RiskManager stand-in that approves every order at its quantity clamped to the room."""

    def __init__(self) -> None:
        self._killed = False
        self.calls: list[tuple] = []

    def check(self, rec, book, position, room, clock, feed, now, live=None):  # noqa: ANN001
        self.calls.append((rec.symbol, rec.side, rec.qty, rec.price, rec.priority))
        if self._killed:
            return 0, "frozen"
        return (min(rec.qty, room), "ok") if room >= 1 else (0, "no_room")

    def killed(self) -> bool:
        return self._killed

    def kill(self, reason: str, now: float) -> None:
        self._killed = True

    def unkill(self, now: float) -> None:
        self._killed = False
