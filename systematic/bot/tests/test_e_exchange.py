"""Group E: ``sim.exchange.SimExchange`` rules, research-mode fills, P&L and response schemas (DESIGN.md 9.1 E).

Collaborators from other groups are fakes defined here (and reused by the other ``test_e_*`` files):
``FakeConfig`` (``config/default.json`` plus dotted overrides, with ``get`` / ``path`` / ``sym``), ``FakeSchedule``
(the section 4.A ``Schedule`` semantics) and ``LinearCost`` (an analytic ``CostModel``: ``walk_bp = a + b * x``).
Market data are synthetic research seasons written under ``tests/_tmp``. Only the schema test uses group A's real
parsers, through ``testpaths.integration``.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import sys
import threading
import unittest
from typing import Any, Callable, Iterable, Mapping

sys.dont_write_bytecode = True
BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT not in sys.path:
    sys.path.insert(0, BOT)

import testpaths  # noqa: E402
from ctcbot.types import SYMBOLS, ApiKind, ApiRequest, ListLog, Phase, Side  # noqa: E402

from sim.exchange import VIOLATION_TYPES, SimExchange  # noqa: E402
from sim.research_market import ResearchMarket  # noqa: E402

_MISSING = object()

CASE: dict = {
    "limits": {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500, "MAT": 700,
               "GEN": 600, "HAVEN": 250, "REIN": 200, "ARCH": 50, "REBLD": 100},
    "etfs": {"ARCH": {"basket": {"PORT": 1, "ISLE": 1, "AQUA": 2, "GRID": 1, "FERRY": 2, "HAVEN": 1}},
             "REBLD": {"basket": {"RECON": 2, "MAT": 2, "GEN": 1}}},
    "timing": {"pre_storm": 60, "storm": 480, "aftermath": 240, "obs_every": 2, "track_every": 20,
               "advisory_first": 30, "advisory_every": 60, "prelim_report": 30, "final_report": 40},
}
BASE: dict[str, float] = {"PORT": 50.0, "ISLE": 60.0, "AQUA": 30.0, "GRID": 40.0, "FERRY": 20.0, "RECON": 55.0,
                          "MAT": 35.0, "GEN": 40.0, "HAVEN": 45.0, "REIN": 90.0, "ARCH": 300.0, "REBLD": 210.0}
DATES = {1: "1700-03-01", 2: "1700-03-02", 3: "1700-03-03"}


# ----------------------------------------------------------------------------------------- fakes
class FakeConfig:
    """``ctcbot.config.Config`` stand-in: default.json with dotted overrides."""

    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        with open(os.path.join(BOT, "config", "default.json"), encoding="utf-8") as f:
            self.d = json.load(f)
        self.d.pop("live_allowed", None)
        for k, v in (overrides or {}).items():
            cur = self.d
            parts = k.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = v
        self.root = BOT
        self.hash = "fakecfg"
        self.live_allowed = False

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        cur: Any = self.d
        for k in dotted.split("."):
            if not isinstance(cur, dict) or k not in cur:
                if default is _MISSING:
                    raise KeyError(dotted)
                return default
            cur = cur[k]
        return cur

    def path(self, p: str | None) -> str | None:
        if p is None:
            return None
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.root, p))

    def sym(self, symbol: str) -> dict:
        return self.d["symbols"][symbol]


class FakeSchedule:
    """The section 4.A ``Schedule`` on a case timing and the configured cost phases."""

    ANCHORS = ("open", "storm_start", "storm_end", "prelim", "final", "close")

    def __init__(self, timing: Mapping[str, float] | None = None, cost_phases: Iterable | None = None) -> None:
        tm = dict(timing or CASE["timing"])
        pre, storm, after = float(tm["pre_storm"]), float(tm["storm"]), float(tm["aftermath"])
        self.open = 0.0
        self.storm_start = pre
        self.storm_end = pre + storm
        self.prelim = self.storm_end + float(tm.get("prelim_report", 30))
        self.close = self.round_len = pre + storm + after
        self.final = self.close - float(tm.get("final_report", 40))
        self.speed = 23400.0 / self.round_len
        if cost_phases is None:
            cost_phases = FakeConfig().get("cost.phases")
        self.cost_phases = [(str(n), self.at(a), self.at(b)) for n, a, b in cost_phases]

    def at(self, spec: Any) -> float:
        if not isinstance(spec, (list, tuple)) or len(spec) != 2 or spec[0] not in self.ANCHORS:
            raise ValueError(f"bad anchor {spec!r}")
        return float(getattr(self, spec[0])) + float(spec[1])

    def phase(self, t: float | None) -> Phase:
        if t is None:
            return Phase.UNKNOWN
        if t < 0 or t >= self.close:
            return Phase.CLOSED
        if t < self.storm_start:
            return Phase.PRE_STORM
        if t < self.storm_end:
            return Phase.STORM
        return Phase.AFTERMATH

    def cost_phase(self, t: float) -> str:
        ph = self.cost_phases
        if not ph or t < ph[0][1]:
            return "open"
        for name, a, b in ph:
            if a <= t < b:
                return name
        return "after"

    @staticmethod
    def market_to_t(market_time: str, speed: float) -> tuple[str, float]:
        d = dt.datetime.fromisoformat(str(market_time).replace("Z", ""))
        return d.date().isoformat(), (d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6 - 34200) / speed


class LinearCost:
    """Analytic ``CostModel``: ``walk_bp = a + b * x`` (times ``buy_mult`` / ``sell_mult`` with a side)."""

    def __init__(self, a: float = 10.0, b: float = 100.0, depth: float = 0.5, rho: float = 0.5,
                 buy_mult: float = 1.0, sell_mult: float = 1.0) -> None:
        self.a, self.b, self._depth, self._rho = a, b, depth, rho
        self.buy_mult, self.sell_mult = buy_mult, sell_mult
        self.calls = 0

    def walk_bp(self, symbol: str, phase: str, x: float, side: Any = None) -> float:
        self.calls += 1
        if x <= 0:
            return 0.0
        c = self.a + self.b * min(x, 1.0)
        if side is not None:
            c *= self.buy_mult if str(side) == "buy" else self.sell_mult
        return c

    def half_spread_bp(self, symbol: str, phase: str) -> float:
        return self.a

    def depth(self, symbol: str, phase: str) -> float:
        return self._depth

    def rho(self, symbol: str) -> float:
        return self._rho


def market_time(r: int, tau: float) -> str:
    base = dt.datetime.fromisoformat(f"{DATES[r]}T09:30:00")
    return (base + dt.timedelta(seconds=30.0 * tau)).isoformat()


def write_season(root: str, n: int = 901, price: Callable[[int, int, str], float] | None = None,
                 feed: Iterable[dict] = (), settle: Mapping[str, float] | None = None) -> str:
    """Write a synthetic research season under ``root`` (a history dir) and return the history dir."""
    price = price or (lambda r, i, s: BASE[s])
    d = os.path.join(root, f"season-{n:03d}")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "market.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "round"] + list(SYMBOLS))
        for r in (1, 2, 3):
            for i in range(390):
                w.writerow([market_time(r, 2.0 * i), r] + [f"{price(r, i, s):.4f}" for s in SYMBOLS])
    with open(os.path.join(d, "feed.jsonl"), "w", encoding="utf-8") as f:
        for m in feed:
            f.write(json.dumps(m) + "\n")
    st = dict(settle or {s: BASE[s] * 1.1 for s in SYMBOLS})
    with open(os.path.join(d, "settlement.json"), "w", encoding="utf-8") as f:
        json.dump(st, f)
    return root


def make_market(overrides: Mapping[str, Any] | None = None, price: Callable | None = None, feed: Iterable[dict] = (),
                settle: Mapping[str, float] | None = None, cost: Any = None, **kw: Any
                ) -> tuple[ResearchMarket, FakeConfig, Any]:
    cfg = FakeConfig(overrides)
    hist = write_season(testpaths.tmpdir("e_season"), 901, price, feed, settle)
    cost = cost if cost is not None else LinearCost()
    m = ResearchMarket(hist, 901, cfg, cost, case=CASE, schedule=FakeSchedule(), **kw)
    return m, cfg, cost


def make_exchange(overrides: Mapping[str, Any] | None = None, fills: bool = True, **kw: Any
                  ) -> tuple[SimExchange, ResearchMarket, ListLog]:
    m, cfg, cost = make_market(overrides, **kw)
    log = ListLog()
    return SimExchange(m, cfg, cost, log, fills=fills), m, log


class Req:
    """Request factory with increasing req_ids."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, kind: ApiKind, **kw: Any) -> ApiRequest:
        self.n += 1
        return ApiRequest(req_id=self.n, kind=kind, **kw)

    def buy(self, sym: str, qty: int, price: float | None, **kw: Any) -> ApiRequest:
        return self(ApiKind.PLACE, symbol=sym, side=Side.BUY, qty=qty, price=price, **kw)

    def sell(self, sym: str, qty: int, price: float | None, **kw: Any) -> ApiRequest:
        return self(ApiKind.PLACE, symbol=sym, side=Side.SELL, qty=qty, price=price, **kw)


def at(ex: SimExchange, t: float) -> SimExchange:
    ex.t = t
    ex.advance(t)
    return ex


# ----------------------------------------------------------------------------------------- rules
class TestRules(unittest.TestCase):
    def setUp(self) -> None:
        self.ex, self.m, self.log = make_exchange()
        self.req = Req()

    def test_violation_schema_is_complete(self) -> None:
        self.assertEqual(set(self.ex.results()["violations"]), set(VIOLATION_TYPES))
        self.assertEqual(self.ex.results()["violations_total"], 0)

    def test_market_closed(self) -> None:
        for t in (780.0, 795.0, 2400.0, 3000.0):
            r = at(self.ex, t).execute(self.req.buy("PORT", 1, 40.0))
            self.assertEqual(r.status, 409, t)
            self.assertEqual(r.data["detail"], "market closed")
            self.assertIn("market closed", r.error)
            self.assertTrue(r.sent and not r.dry_run)
        self.assertEqual(self.ex.results()["violations"]["market_closed"], 4)
        self.assertEqual(at(self.ex, 810.0).execute(self.req.buy("PORT", 1, 40.0)).status, 200)   # round 2 open
        self.assertEqual(at(self.ex, 3000.0).execute(self.req(ApiKind.CANCEL_ALL)).status, 200)  # cancels allowed

    def test_team_rate_and_bot_cap(self) -> None:
        at(self.ex, 100.0)
        st = [self.ex.execute(self.req(ApiKind.CANCEL_ALL)).status for _ in range(181)]
        self.assertEqual(st[:180], [200] * 180)
        self.assertEqual(st[180], 429)
        res = self.ex.results()
        self.assertEqual(res["violations"]["rate"], 1)
        self.assertEqual(res["violations"]["bot_cap"], 30)
        self.assertEqual(res["rate"]["max_60s"], 180)
        self.assertEqual(res["rate"]["http_429"], 1)
        self.assertEqual(at(self.ex, 159.0).execute(self.req(ApiKind.CANCEL_ALL)).status, 429)
        self.assertEqual(at(self.ex, 160.0).execute(self.req(ApiKind.CANCEL_ALL)).status, 200)

    def test_bot_cap_uses_a_61s_window(self) -> None:
        for k in range(151):
            at(self.ex, 100.0 + k * 0.4).execute(self.req(ApiKind.CANCEL_ALL))   # 151 actions in 60.0 s
        self.assertEqual(self.ex.results()["violations"]["bot_cap"], 1)
        self.assertEqual(self.ex.results()["violations"]["rate"], 0)

    def test_requests_per_second(self) -> None:
        at(self.ex, 100.0)
        st = [self.ex.execute(self.req(ApiKind.GET_TIME)).status for _ in range(601)]
        self.assertEqual(st.count(200), 600)
        self.assertEqual(st[-1], 429)
        self.assertEqual(self.ex.results()["violations"]["requests"], 1)
        self.assertEqual(at(self.ex, 101.0).execute(self.req(ApiKind.GET_TIME)).status, 200)

    def test_limit_shrink_and_reject(self) -> None:
        at(self.ex, 100.0)
        r1 = self.ex.execute(self.req.buy("PORT", 150, 40.0))
        r2 = self.ex.execute(self.req.buy("PORT", 100, 40.0))
        r3 = self.ex.execute(self.req.buy("PORT", 10, 40.0))
        self.assertEqual((r1.status, r2.status, r3.status), (200, 200, 400))
        self.assertEqual(r2.data["remaining_quantity"], 50)
        self.assertEqual(r2.parsed.remaining, 50)
        self.assertEqual(r3.data["detail"], "position limit")
        self.assertEqual(self.ex.results()["violations"]["limit_shrink"], 2)
        self.assertEqual(self.ex.execute(self.req.sell("PORT", 200, 70.0)).status, 200)   # the sell side has room
        self.assertEqual(self.ex.results()["violations"]["limit_shrink"], 2)

    def test_limit_room_cancel_worst_price_first(self) -> None:
        at(self.ex, 100.0)
        worst = self.ex.execute(self.req.buy("PORT", 120, 40.0)).parsed.order_id
        keep = self.ex.execute(self.req.buy("PORT", 60, 45.0)).parsed.order_id
        r = self.ex.execute(self.req.buy("PORT", 100, 60.0))     # marketable: needs 100, room 20
        self.assertEqual(r.status, 200)
        self.assertEqual(r.parsed.remaining, 100)
        v = self.ex.results()["violations"]
        self.assertEqual((v["limit_room_cancel"], v["limit_shrink"]), (1, 0))
        open_ids = {row.order_id for row in self.ex.execute(self.req(ApiKind.GET_OPEN)).parsed}
        self.assertNotIn(worst, open_ids)
        self.assertIn(keep, open_ids)
        # a non-marketable order never cancels resting orders: it is shrunk instead
        r2 = self.ex.execute(self.req.buy("PORT", 100, 41.0))
        self.assertEqual(r2.parsed.remaining, 40)
        self.assertEqual(self.ex.results()["violations"]["limit_room_cancel"], 1)

    def test_self_trade_cancels_resting_order(self) -> None:
        at(self.ex, 100.0)
        s1 = self.ex.execute(self.req.sell("PORT", 10, 55.0)).parsed.order_id
        self.ex.execute(self.req.buy("PORT", 10, 54.0))            # does not cross
        self.assertEqual(self.ex.results()["violations"]["self_trade"], 0)
        self.ex.execute(self.req.buy("PORT", 10, 55.0))            # crosses our 55 sell
        self.assertEqual(self.ex.results()["violations"]["self_trade"], 1)
        ids = {row.order_id for row in self.ex.execute(self.req(ApiKind.GET_OPEN)).parsed}
        self.assertNotIn(s1, ids)
        self.assertEqual(len(ids), 2)

    def test_max_resting(self) -> None:
        ex, _, _ = make_exchange({"exchange.exchange_max_resting_per_symbol": 3})
        at(ex, 100.0)
        st = [ex.execute(self.req.buy("GRID", 1, 30.0 - k * 0.01)).status for k in range(4)]
        self.assertEqual(st, [200, 200, 200, 400])
        self.assertEqual(ex.results()["violations"]["max_resting"], 1)
        self.assertEqual(ex.execute(self.req.buy("PORT", 1, 30.0)).status, 200)   # per symbol

    def test_bad_orders(self) -> None:
        at(self.ex, 100.0)
        cases = [(self.req.buy("PORT", 0, 40.0), "bad_qty"), (self.req.buy("PORT", 1.5, 40.0), "bad_qty"),  # type: ignore[arg-type]
                 (self.req.buy("PORT", True, 40.0), "bad_qty"),  # type: ignore[arg-type]
                 (self.req.buy("PORT", 1, 40.1234567), "bad_price"), (self.req.buy("PORT", 1, -1.0), "bad_price"),
                 (self.req.buy("PORT", 1, float("nan")), "bad_price"),
                 (self.req.buy("NOPE", 1, 40.0), "bad_request"),
                 (self.req(ApiKind.PLACE, symbol="PORT", side=None, qty=1, price=40.0), "bad_request"),
                 (self.req(ApiKind.PLACE, symbol="PORT", side=Side.BUY, qty=1, price=40.0, order_type="stop"),
                  "bad_request"),
                 (self.req(ApiKind.PLACE, symbol="PORT", side=Side.BUY, qty=1, price=40.0, order_type="market"),
                  "bad_price")]
        for req, kind in cases:
            before = self.ex.results()["violations"][kind]
            r = self.ex.execute(req)
            self.assertEqual(r.status, 400, kind)
            self.assertEqual(self.ex.results()["violations"][kind], before + 1, kind)
        self.assertEqual(self.ex.execute(self.req.buy("PORT", 1, 40.123456)).status, 200)   # 6 decimals are fine

    def test_cancel_and_cancel_all(self) -> None:
        at(self.ex, 100.0)
        self.assertEqual(self.ex.execute(self.req(ApiKind.CANCEL, order_id="nope")).status, 404)
        oid = self.ex.execute(self.req.buy("PORT", 5, 40.0)).parsed.order_id
        self.ex.execute(self.req.buy("GRID", 5, 30.0))
        r = self.ex.execute(self.req(ApiKind.CANCEL, order_id=oid))
        self.assertEqual((r.status, r.data["status"]), (200, "cancelled"))
        self.assertEqual(self.ex.execute(self.req(ApiKind.CANCEL, order_id=oid)).status, 404)
        self.ex.execute(self.req.buy("PORT", 5, 40.0))
        r = self.ex.execute(self.req(ApiKind.CANCEL_ALL, symbol="PORT"))
        self.assertEqual(r.data, {"status": "cancelled", "cancelled": 1})
        left = self.ex.execute(self.req(ApiKind.GET_OPEN)).parsed
        self.assertEqual([row.symbol for row in left], ["GRID"])
        self.assertEqual(self.ex.execute(self.req(ApiKind.CANCEL_ALL)).data["cancelled"], 1)

    def test_close_cancels_resting_orders_and_close_all(self) -> None:
        at(self.ex, 700.0)
        self.ex.execute(self.req.buy("PORT", 5, 40.0))
        at(self.ex, 779.9)
        self.assertEqual(len(self.ex.execute(self.req(ApiKind.GET_OPEN)).parsed), 1)
        at(self.ex, 780.0)
        self.assertEqual(self.ex.execute(self.req(ApiKind.GET_OPEN)).parsed, [])
        self.assertEqual(self.ex.results()["exchange_cancels"].get("close"), 1)
        at(self.ex, 900.0)
        self.ex.execute(self.req.buy("PORT", 5, 40.0))
        self.ex.close_all(901.0)
        self.assertEqual(self.ex.results()["live_orders"], 0)
        self.assertEqual(self.ex.results()["orders"]["o000002"]["cancel_reason"], "close_all")
        # the market stays shut after close_all: no new orders, the server clock reads paused, no violation
        r = at(self.ex, 902.0).execute(self.req.buy("PORT", 5, 40.0))
        self.assertEqual(r.status, 409)
        self.assertEqual(self.ex.execute(self.req(ApiKind.GET_TIME)).parsed.state, "paused")
        res = self.ex.results()
        self.assertEqual((res["after_close_rejects"], res["violations"]["market_closed"]), (1, 0))

    def test_no_cancel_at_close_when_disabled(self) -> None:
        ex, _, _ = make_exchange({"sim.cancel_at_close": False})
        at(ex, 700.0)
        ex.execute(self.req.buy("PORT", 5, 40.0))
        at(ex, 800.0)
        self.assertEqual(len(ex.execute(self.req(ApiKind.GET_OPEN)).parsed), 1)

    def test_server_time(self) -> None:
        d = at(self.ex, 90.0).execute(self.req(ApiKind.GET_TIME))
        self.assertEqual(d.data["competition_id"], "sim-901")
        self.assertEqual(d.data["state"], "running")
        self.assertEqual(d.data["fictional_time"], "1700-03-01T10:15:00")
        self.assertEqual(d.parsed.elapsed_active_s, 90.0)
        p = at(self.ex, 795.0).execute(self.req(ApiKind.GET_TIME)).parsed
        self.assertEqual((p.state, p.fictional_time, p.elapsed_active_s), ("paused", "1700-03-01T16:00:00", 780.0))
        p = at(self.ex, 811.0).execute(self.req(ApiKind.GET_TIME)).parsed
        self.assertEqual((p.state, p.fictional_time), ("running", "1700-03-02T09:30:30"))

    def test_clock_callable_drives_time(self) -> None:
        self.ex.clock = lambda: 120.0
        self.assertEqual(self.ex.execute(self.req(ApiKind.GET_TIME)).sent_mono, 120.0)
        self.assertEqual(self.ex.t, 120.0)

    def test_orderbook_and_case(self) -> None:
        r = at(self.ex, 100.0).execute(self.req(ApiKind.GET_BOOK, symbol="PORT"))
        bids, asks = r.parsed
        self.assertEqual(len(bids), 5)
        self.assertLess(bids[0].price, asks[0].price)
        self.assertEqual(self.ex.execute(self.req(ApiKind.GET_BOOK, symbol="X")).status, 404)
        self.assertEqual(self.ex.execute(self.req(ApiKind.GET_CASE)).data["limits"]["PORT"], 200)

    def test_thread_safe_execute(self) -> None:
        at(self.ex, 100.0)
        errors: list[BaseException] = []

        def work(k: int) -> None:
            try:
                for j in range(20):
                    self.ex.execute(self.req.buy("PORT", 1, 30.0 + 0.01 * ((k * 20 + j) % 50)))
                    self.ex.execute(self.req(ApiKind.GET_OPEN))
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        ths = [threading.Thread(target=work, args=(k,)) for k in range(6)]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.ex.results()["rate"]["by_kind"]["place"], 120)


# ----------------------------------------------------------------------------------------- research fills
class TestResearchFills(unittest.TestCase):
    """LinearCost: c(x) = 10 + 100x bp, so a clip of q shares at X1 = 0 costs c(q/L); hs 10 bp, depth 0.5."""

    def setUp(self) -> None:
        self.ex, self.m, self.log = make_exchange()
        self.req = Req()

    def fills(self) -> list[dict]:
        return self.ex.results()["fills"]

    def test_clip_fills_on_the_next_row(self) -> None:
        at(self.ex, 80.0).execute(self.req.buy("PORT", 16, 60.0, local_id="L1"))
        at(self.ex, 81.9)
        self.assertEqual(self.fills(), [])
        at(self.ex, 82.0)
        f = self.fills()
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["qty"], f[0]["row"], f[0]["t"], f[0]["local_id"]), (16, 41, 82.0, "L1"))
        self.assertAlmostEqual(f[0]["price"], 50.0 * (1 + (10 + 100 * 16 / 200) * 1e-4))
        self.assertEqual(self.ex.positions()["PORT"], 16)
        self.assertEqual(self.ex.execute(self.req(ApiKind.GET_OPEN)).parsed, [])

    def test_sell_mirrors(self) -> None:
        at(self.ex, 80.0).execute(self.req.sell("PORT", 16, 40.0))
        at(self.ex, 82.0)
        self.assertAlmostEqual(self.fills()[0]["price"], 50.0 * (1 - 18e-4))
        self.assertEqual(self.ex.positions()["PORT"], -16)

    def test_follow_on_raises_the_next_clip(self) -> None:
        at(self.ex, 80.0).execute(self.req.buy("PORT", 16, 60.0))
        at(self.ex, 84.0).execute(self.req.buy("PORT", 16, 60.0))     # row 43, 4 s after the first fill
        at(self.ex, 86.0)
        p1, p2 = (f["price"] for f in self.fills())
        self.assertAlmostEqual(p2, 50.0 * (1 + (18 + 100 * 0.08) * 1e-4))
        self.assertGreater(p2, p1)

    def test_same_row_orders_walk_the_same_book(self) -> None:
        at(self.ex, 80.0).execute(self.req.buy("PORT", 20, 60.0))
        self.ex.execute(self.req.buy("PORT", 20, 60.0))
        at(self.ex, 82.0)
        p1, p2 = (f["price"] for f in self.fills())
        # increment from x = 0.1 to 0.2: (0.2 * 30 - 0.1 * 20) / 0.1 = 40 bp
        self.assertAlmostEqual(p1, 50.0 * (1 + 20e-4))
        self.assertAlmostEqual(p2, 50.0 * (1 + 40e-4))

    def test_replenishment_caps_and_the_remainder_rests(self) -> None:
        at(self.ex, 80.0).execute(self.req.buy("PORT", 150, 60.0))
        at(self.ex, 82.0)
        self.assertEqual([f["qty"] for f in self.fills()], [100])     # int(0.5 * 200)
        self.assertEqual(self.ex.results()["repl_hits"], 1)
        row = self.ex.execute(self.req(ApiKind.GET_OPEN)).parsed[0]
        self.assertEqual((row.remaining, row.status), (50, "partially_filled"))
        at(self.ex, 84.0)                     # the touch (50.05) is through the 60 limit: fills at its own price
        f = self.fills()[-1]
        self.assertEqual((f["qty"], f["price"], f["kind"]), (50, 60.0, "passive"))

    def test_limit_takes_the_largest_quantity_whose_marginal_price_is_inside(self) -> None:
        limit = 50.0 * (1 + 30.5e-4)                 # m(x) = 10 + 200x <= 30.5 bp -> q* = 20
        at(self.ex, 80.0).execute(self.req.buy("PORT", 150, round(limit, 6)))
        at(self.ex, 82.0)
        f = self.fills()
        self.assertEqual(f[0]["qty"], 20)
        self.assertAlmostEqual(f[0]["price"], 50.0 * (1 + 20e-4))
        at(self.ex, 84.0)                            # remainder rests at the limit; the 50.05 ask reaches it
        self.assertEqual([x["qty"] for x in self.fills()], [20, 100])  # depth cap of 100 per row
        at(self.ex, 86.0)
        self.assertEqual([x["qty"] for x in self.fills()], [20, 100, 30])
        self.assertEqual(self.ex.positions()["PORT"], 150)

    def test_resting_order_below_the_touch_never_fills(self) -> None:
        at(self.ex, 80.0).execute(self.req.buy("PORT", 10, 49.0))
        at(self.ex, 700.0)
        self.assertEqual(self.fills(), [])

    def test_order_after_the_last_row_never_fills(self) -> None:
        at(self.ex, 778.5).execute(self.req.buy("PORT", 10, 60.0))   # j = 390 + 1 (report window) >= 390
        at(self.ex, 779.9)
        self.assertEqual(self.fills(), [])
        at(self.ex, 780.0)
        self.assertEqual(self.ex.results()["live_orders"], 0)

    def test_report_window_lag(self) -> None:
        at(self.ex, 741.0).execute(self.req.buy("PORT", 10, 60.0))   # row 370 + 1 + 1 = 372
        at(self.ex, 743.9)
        self.assertEqual(self.fills(), [])
        at(self.ex, 744.0)
        self.assertEqual(self.fills()[0]["row"], 372)

    def test_market_order_remainder_is_cancelled(self) -> None:
        at(self.ex, 80.0).execute(self.req(ApiKind.PLACE, symbol="PORT", side=Side.BUY, qty=150, price=None,
                                           order_type="market"))
        at(self.ex, 82.0)
        self.assertEqual(self.fills()[0]["qty"], 100)
        self.assertEqual(self.ex.results()["live_orders"], 0)

    def test_fills_disabled(self) -> None:
        ex, _, _ = make_exchange(fills=False)
        at(ex, 80.0).execute(self.req.buy("PORT", 10, 60.0))
        at(ex, 300.0)
        self.assertEqual(ex.results()["fills"], [])
        self.assertEqual(len(ex.execute(self.req(ApiKind.GET_OPEN)).parsed), 1)
        self.assertEqual(ex.positions()["PORT"], 0)

    def test_cancel_before_the_match_prevents_the_fill(self) -> None:
        oid = at(self.ex, 80.0).execute(self.req.buy("PORT", 10, 60.0)).parsed.order_id
        self.ex.execute(self.req(ApiKind.CANCEL, order_id=oid))
        at(self.ex, 90.0)
        self.assertEqual(self.fills(), [])


# ----------------------------------------------------------------------------------------- P&L
class TestPnl(unittest.TestCase):
    def test_pnl_reconciles_cash_and_marks(self) -> None:
        price = (lambda r, i, s: BASE[s] * (1 + 0.001 * i) if r == 1 else BASE[s] * 1.2)
        settle = {s: BASE[s] * 0.9 for s in SYMBOLS}
        ex, m, _ = make_exchange(price=price, settle=settle)
        req = Req()
        at(ex, 80.0).execute(req.buy("PORT", 16, 80.0))
        at(ex, 100.0).execute(req.sell("PORT", 6, 10.0))
        at(ex, 120.0).execute(req.sell("GRID", 33, 10.0))
        at(ex, 200.0)
        rows = ex.execute(req(ApiKind.GET_POSITIONS))
        mark_pnl = sum(p.realized + p.unrealized for p in rows.parsed.values())
        cash = ex.results()["seasons"]["901"]["cash"]
        direct = cash + sum(p.qty * p.mark for p in rows.parsed.values())
        self.assertAlmostEqual(mark_pnl, direct, places=6)
        self.assertEqual(rows.parsed["PORT"].qty, 10)
        self.assertEqual(rows.parsed["GRID"].qty, -33)
        self.assertEqual(len(rows.data), 12)
        at(ex, 2400.0)
        s = ex.results()["seasons"]["901"]
        final = s["cash"] + 10 * settle["PORT"] - 33 * settle["GRID"]
        self.assertAlmostEqual(s["pnl"], final, places=6)
        self.assertEqual(s["marks"], "settlement")
        r1 = s["cash"] + 10 * m.rows[1][389][0] - 33 * m.rows[1][389][3]
        self.assertAlmostEqual(s["round_pnl"]["1"], r1, places=6)
        self.assertAlmostEqual(sum(s["round_pnl"].values()), final, places=6)
        cost = sum(f["qty"] * f["ref_px"] * f["cost_frac"] for f in ex.results()["fills"])
        self.assertAlmostEqual(s["cost_paid"], cost, places=9)
        self.assertEqual(s["checkpoints"]["1"], {"PORT": 10, "GRID": -33})

    def test_etf_create_and_redeem(self) -> None:
        ex, _, _ = make_exchange()
        req = Req()
        r = at(ex, 100.0).execute(req(ApiKind.ETF_CREATE, symbol="ARCH", qty=2))
        self.assertEqual(r.status, 200)
        pos = ex.positions()
        self.assertEqual((pos["ARCH"], pos["AQUA"], pos["PORT"]), (2, -4, -2))
        self.assertAlmostEqual(ex.results()["seasons"]["901"]["cash"], 0.0, places=6)
        self.assertEqual(ex.execute(req(ApiKind.ETF_REDEEM, symbol="ARCH", qty=2)).status, 200)
        self.assertTrue(all(q == 0 for q in ex.positions().values()))
        self.assertEqual(ex.execute(req(ApiKind.ETF_CREATE, symbol="PORT", qty=1)).status, 400)
        self.assertEqual(ex.execute(req(ApiKind.ETF_CREATE, symbol="ARCH", qty=51)).status, 400)  # ARCH limit 50
        self.assertEqual(at(ex, 790.0).execute(req(ApiKind.ETF_CREATE, symbol="ARCH", qty=1)).status, 409)


class TestSchemas(unittest.TestCase):
    def test_responses_parse_with_group_a_parsers(self) -> None:
        (rest,) = testpaths.integration("ctcbot.rest")
        ex, _, _ = make_exchange()
        req = Req()
        at(ex, 80.0)
        ack = ex.execute(req.buy("PORT", 16, 60.0))
        ex.execute(req.sell("GRID", 5, 50.0))
        at(ex, 82.0)
        self.assertEqual(rest.parse_order_ack(ack.data), ack.parsed)
        opn = ex.execute(req(ApiKind.GET_OPEN))
        self.assertEqual(rest.parse_open_orders(opn.data), opn.parsed)
        pos = ex.execute(req(ApiKind.GET_POSITIONS))
        self.assertEqual(rest.parse_positions(pos.data), pos.parsed)
        tm = ex.execute(req(ApiKind.GET_TIME))
        self.assertEqual(rest.parse_time(tm.data), tm.parsed)
        bk = ex.execute(req(ApiKind.GET_BOOK, symbol="PORT"))
        self.assertEqual(rest.parse_book(bk.data), bk.parsed)


# ----------------------------------------------------------------------------------------- shell source
class _ListMarket:
    def __init__(self, frames: list) -> None:
        self._frames = frames

    def frames(self) -> Any:
        return iter(self._frames)


class _AdvExchange:
    def __init__(self) -> None:
        self.clock = None
        self.advanced: list[float] = []

    def advance(self, t: float) -> None:
        self.advanced.append(t)


class _FastTime:
    """A scaled clock running 2000x real time."""

    speed = 2000.0

    def __init__(self) -> None:
        import time as _time
        self._time = _time
        self.t0 = _time.monotonic()

    def mono(self) -> float:
        return (self._time.monotonic() - self.t0) * self.speed

    def wall(self) -> float:
        return 1.8e9 + self.mono()


class TestSimSource(unittest.TestCase):
    def test_paces_frames_advances_the_exchange_and_reports_stats(self) -> None:
        import asyncio
        from sim.exchange import SimSource
        from sim.research_market import MarketFrame
        frames = [MarketFrame(0.0, {}, "rec#1", control="up"), MarketFrame(0.5, {"type": "quote"}, "rec#1"),
                  MarketFrame(3.0, {}, "rec#1", control="down"), MarketFrame(3.0, {}, "rec#2", control="up"),
                  MarketFrame(4.0, {"type": "trade"}, "rec#2")]
        ex = _AdvExchange()
        ts = _FastTime()
        src = SimSource(_ListMarket(frames), ex, ts, name="sim")   # type: ignore[arg-type]
        self.assertEqual(ex.clock, ts.mono)                        # the executor's clock follows the source
        got: list = []

        def emit(sf: Any) -> None:
            got.append(sf)
            if sf.frame.get("type") == "trade":
                src.stop()

        asyncio.run(asyncio.wait_for(src.run(emit), timeout=10.0))
        self.assertEqual([g.control for g in got], ["up", None, "down", "up", None, "down"])
        self.assertEqual([g.conn for g in got], ["sim#1", "sim#1", "sim#1", "sim#2", "sim#2", "sim#2"])
        for g, f in zip(got, frames):
            self.assertGreaterEqual(g.recv_mono, f.t)
        self.assertEqual(ex.advanced, [0.0, 0.5, 3.0, 3.0, 4.0])
        st = src.stats()
        self.assertEqual((st.name, st.frames, st.reconnects, st.connections_up, st.errors), ("sim", 5, 1, 0, 0))

    def test_ends_by_itself_after_the_tail(self) -> None:
        # integration (DESIGN.md §12 item 6): the shell's --sim run stops at the end of the market
        import asyncio
        from sim.exchange import SimSource
        from sim.research_market import MarketFrame
        frames = [MarketFrame(0.0, {}, "rec#1", control="up"), MarketFrame(1.0, {"type": "quote"}, "rec#1")]
        ts = _FastTime()
        src = SimSource(_ListMarket(frames), _AdvExchange(), ts, name="sim", tail_s=2.0)   # type: ignore[arg-type]
        got: list = []
        asyncio.run(asyncio.wait_for(src.run(got.append), timeout=10.0))
        self.assertEqual([g.control for g in got], ["up", None, "down"])
        self.assertGreaterEqual(got[-1].recv_mono, 3.0)


if __name__ == "__main__":
    unittest.main()
