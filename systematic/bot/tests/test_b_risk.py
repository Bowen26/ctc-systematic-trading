"""Group B: RiskManager (the twelve check rules, every breaker, mode precedence, persistence)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_b_support import BTestCase, FakeCase, FakeConfig, FakeSchedule, clock, feed, result  # noqa: E402

from ctcbot.exec.risk import RiskManager  # noqa: E402
from ctcbot.types import (ApiKind, ApiRequest, BookView, Fill, OrderRecord, OrderState, Phase,  # noqa: E402
                          PriceQuality, Priority, RiskMode, Side)


def bv(bid: float | None = 99.9, ask: float | None = 100.1, wall_only: bool = False) -> BookView:
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    hs = (ask - bid) / 2 / mid * 1e4 if mid else None
    return BookView(symbol="PORT", bid=bid, ask=ask, bid_size=10, ask_size=10, bids=(), asks=(), mid=mid,
                    half_spread_bp=hs, age_s=0.1, empty=bid is None and ask is None, wall_only=wall_only,
                    server_ts=None)


def rec(side: Side = Side.BUY, qty: int = 10, price: float = 100.0, priority: Priority = Priority.TAKE,
        strategy: str = "s3", symbol: str = "PORT", reduce_only: bool = False, lid: str = "cand",
        state: OrderState = OrderState.NEW) -> OrderRecord:
    return OrderRecord(local_id=lid, strategy=strategy, key=None, style="clip", symbol=symbol, side=side, qty=qty,
                       price=price, priority=priority, reduce_only=reduce_only, state=state, remaining=qty)


_FILL_N = [0]


def fill(px: float, side: Side = Side.BUY, quality: PriceQuality = PriceQuality.EXACT, strategy: str = "s3",
         ref: float = 100.0, exp: float = 50.0, lid: str | None = None) -> Fill:
    """One fill of its own clip (a fresh local_id) unless ``lid`` names the clip."""
    if lid is None:
        _FILL_N[0] += 1
        lid = f"e0-{_FILL_N[0]}"
    return Fill(local_id=lid, order_id="o" + lid, strategy=strategy, key=None, symbol="PORT", side=side, qty=5,
                price=px, quality=quality, mono=0.0, ref_mid=ref, expected_cost_bp=exp, source="ack")


class RiskBase(BTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.r = RiskManager(FakeConfig(), FakeCase(), FakeSchedule(), self.log)

    def chk(self, r: OrderRecord, book: BookView | None = None, position: int = 0, room: int = 1000,
            ck=None, fd=None, now: float = 100.0, live=None) -> tuple[int, str]:
        return self.r.check(r, book or bv(), position, room, ck or clock(100.0), fd or feed(), now, live=live)


class CheckRules(RiskBase):
    def test_ok(self) -> None:
        self.assertEqual(self.chk(rec()), (10, "ok"))

    def test_rule1_frozen(self) -> None:
        self.r.kill("test", 1.0)
        self.assertEqual(self.chk(rec(side=Side.SELL), position=50)[1], "frozen")
        self.assertEqual(self.r.mode, RiskMode.FROZEN)

    def test_rule2_holds_and_exemptions(self) -> None:
        self.r.hold("operator_pause", 1.0)
        self.assertEqual(self.chk(rec())[1], "hold:operator_pause")
        # reducing passes, clamped to |position|
        self.assertEqual(self.chk(rec(side=Side.SELL, qty=30), position=20), (20, "ok"))
        self.assertEqual(self.chk(rec(side=Side.SELL, qty=10, reduce_only=True), position=0)[0], 0)
        # exempt strategy passes an exempting hold, but not another hold
        self.r.release("operator_pause", 2.0)
        self.r.hold("unknown_orders", 2.0, exempt=frozenset({"s1r3"}))
        self.assertEqual(self.chk(rec(strategy="s1r3", priority=Priority.SNIPE))[0], 10)
        self.assertEqual(self.chk(rec(strategy="s3"))[1], "hold:unknown_orders")
        self.r.hold("feed_down", 2.0)
        self.assertEqual(self.chk(rec(strategy="s1r3", priority=Priority.SNIPE))[1], "hold:feed_down")

    def test_rule3_disabled_symbol(self) -> None:
        self.r.disable("PORT", "test", 1.0)
        self.assertEqual(self.chk(rec())[1], "disabled")
        self.assertEqual(self.chk(rec(side=Side.BUY, qty=50), position=-30), (30, "ok"))
        self.r.enable("PORT")
        self.assertEqual(self.chk(rec())[0], 10)

    def test_rule4_market_closed(self) -> None:
        self.assertEqual(self.chk(rec(), ck=clock(100.0, running=False))[1], "market_closed")
        self.assertEqual(self.chk(rec(), ck=clock(800.0, phase=Phase.CLOSED))[1], "market_closed")
        self.assertEqual(self.chk(rec(), ck=clock(100.0, phase=Phase.UNKNOWN))[1], "market_closed")

    def test_rule5_size_caps_against_scaled_limit(self) -> None:
        self.assertEqual(self.chk(rec(qty=500))[0], 100)                         # 0.5 x L_s(200)
        self.assertEqual(self.chk(rec(qty=500, priority=Priority.HEDGE))[0], 100)
        self.assertEqual(self.chk(rec(qty=500, priority=Priority.SNIPE))[0], 400)  # 2 x L_s
        self.assertEqual(self.chk(rec(qty=0))[1], "qty")
        r = RiskManager(FakeConfig({"sizing.scale": 0.25}), FakeCase(), FakeSchedule(), self.log)
        self.assertEqual(r.check(rec(qty=500), bv(), 0, 1000, clock(), feed(), 100.0)[0], 25)   # 0.5 x 50
        self.assertEqual(r.check(rec(qty=500, symbol="ARCH"), bv(), 0, 1000, clock(), feed(), 100.0)[0], 6)

    def test_rule6_room(self) -> None:
        self.assertEqual(self.chk(rec(qty=50), room=7), (7, "ok"))
        self.assertEqual(self.chk(rec(qty=50), room=0)[1], "no_room")

    def test_rule7_mid(self) -> None:
        self.assertEqual(self.chk(rec(), book=bv(None, 100.1))[1], "no_mid")

    def test_rule8_bands(self) -> None:
        # marketable TAKE beyond 1.5% from mid
        self.assertEqual(self.chk(rec(price=101.6))[1], "marketable_far")
        self.assertEqual(self.chk(rec(price=101.4))[0], 10)
        self.assertEqual(self.chk(rec(side=Side.SELL, price=98.4), position=0)[1], "marketable_far")
        # non-marketable far order: fat-finger band 25%
        self.assertEqual(self.chk(rec(price=80.0))[0], 10)
        self.assertEqual(self.chk(rec(price=74.0))[1], "fat_finger")
        # SNIPE: 60% band, no 1.5% rule when marketable
        self.assertEqual(self.chk(rec(price=110.0, priority=Priority.SNIPE))[0], 10)
        self.assertEqual(self.chk(rec(price=50.0, priority=Priority.SNIPE))[0], 10)
        self.assertEqual(self.chk(rec(price=39.0, priority=Priority.SNIPE))[1], "fat_finger")

    def test_rule8_missing_touch_counts_as_marketable(self) -> None:
        book = BookView(symbol="PORT", bid=99.9, ask=None, bid_size=1, ask_size=None, bids=(), asks=(), mid=100.0,
                        half_spread_bp=None, age_s=0.0, empty=False, wall_only=False, server_ts=None)
        # mid present but ask missing: a buy is marketable, so the opening block applies
        self.assertEqual(self.chk(rec(price=99.0), book=book, ck=clock(1.0))[1], "opening")

    def test_rule9_opening_and_wall_only(self) -> None:
        self.assertEqual(self.chk(rec(price=100.2), ck=clock(2.9))[1], "opening")
        self.assertEqual(self.chk(rec(price=100.2), ck=clock(3.0))[0], 10)
        self.assertEqual(self.chk(rec(price=99.0), ck=clock(1.0))[0], 10)     # resting order is fine
        self.assertEqual(self.chk(rec(price=100.2), book=bv(wall_only=True))[1], "wall_only")
        self.assertEqual(self.chk(rec(price=99.0), book=bv(wall_only=True))[0], 10)

    def test_rule10_stale_storm_observations(self) -> None:
        self.assertEqual(self.chk(rec(), fd=feed(storm_age=6.5))[1], "storm_obs_stale")
        self.assertEqual(self.chk(rec(), fd=feed(storm_age=None))[1], "storm_obs_stale")
        self.assertEqual(self.chk(rec(side=Side.SELL, qty=10, price=99.9), position=40, fd=feed(storm_age=9.0)),
                         (10, "ok"))
        self.assertEqual(self.chk(rec(), ck=clock(50.0), fd=feed(storm_age=9.0))[0], 10)  # pre-storm

    def test_rule11_max_resting(self) -> None:
        live = [rec(lid=f"l{i}", price=90.0 + i * 0.1, state=OrderState.OPEN) for i in range(8)]
        self.assertEqual(self.chk(rec(price=95.0), live=live)[1], "max_resting")
        live[0].state = OrderState.CANCEL_ACKED
        self.assertEqual(self.chk(rec(price=95.0), live=live)[0], 10)

    def test_rule12_self_cross(self) -> None:
        sell = rec(side=Side.SELL, price=100.05, lid="s", state=OrderState.OPEN)
        self.assertEqual(self.chk(rec(price=100.05), live=[sell])[1], "self_cross")
        self.assertEqual(self.chk(rec(price=100.04), live=[sell])[0], 10)
        buy = rec(side=Side.BUY, price=99.95, lid="b", state=OrderState.SENDING)
        self.assertEqual(self.chk(rec(side=Side.SELL, price=99.95), live=[buy])[1], "self_cross")
        buy.state = OrderState.CANCEL_ACKED                                     # no longer resting
        self.assertEqual(self.chk(rec(side=Side.SELL, price=99.95), live=[buy])[0], 10)


class Breakers(RiskBase):
    def _place_req(self, sym: str = "PORT") -> ApiRequest:
        return ApiRequest(req_id=1, kind=ApiKind.PLACE, symbol=sym, side=Side.BUY, qty=1, price=1.0, local_id="l")

    def test_mode_precedence(self) -> None:
        self.assertEqual(self.r.mode, RiskMode.NORMAL)
        self.r.hold("x", 1.0, until=5.0)
        self.assertEqual(self.r.mode, RiskMode.NO_NEW_RISK)
        self.r.kill("k", 2.0)
        self.assertEqual(self.r.mode, RiskMode.FROZEN)
        self.r.unkill(3.0)
        self.assertEqual(self.r.mode, RiskMode.NO_NEW_RISK)
        self.r.holds(5.0)                                       # the timed hold has expired
        self.assertEqual(self.r.mode, RiskMode.NORMAL)
        self.assertTrue(any(k == "hold_expired" for k, _ in self.log.events))

    def test_hold_merge_and_release(self) -> None:
        self.r.hold("h", 1.0, until=3.0)
        self.r.hold("h", 2.0, until=6.0, sticky=True)
        h = self.r.holds(2.5)["h"]
        self.assertEqual((h["until"], h["sticky"]), (6.0, True))
        self.r.hold("h", 2.6)                                    # an open-ended re-hold makes it open-ended
        self.assertIsNone(self.r.holds(2.7)["h"]["until"])
        self.r.release("h", 3.0)
        self.assertEqual(self.r.holds(3.0), {})
        self.r.release("nope", 3.0)

    def test_auth(self) -> None:
        for _ in range(6):
            self.r.on_result(result(self._place_req(), status=401, data={"detail": "bad key"}), 1.0)
        self.assertIn("auth", self.r.holds(1.0))
        self.assertTrue(self.r.holds(1.0)["auth"]["sticky"])
        self.assertEqual(len(self.alerts("auth_failed")), 1)
        self.assertNotIn("PORT", self.r.disabled())              # never counted as a per-symbol reject
        self.assertEqual(self.chk(rec())[1], "hold:auth")

    def test_429(self) -> None:
        self.r.on_result(result(self._place_req(), status=429), 10.0)
        self.assertEqual(self.r.holds(12.0)["http_429"]["until"], 15.0)
        self.assertEqual(self.r.holds(15.0), {})
        self.assertNotIn("PORT", self.r.disabled())

    def test_5xx_streak(self) -> None:
        req = self._place_req()
        self.r.on_result(result(req, status=503), 1.0)
        self.r.on_result(result(req, status=None, error="timeout"), 1.1)
        self.r.on_result(result(req, status=200), 1.2)            # streak broken
        self.r.on_result(result(req, status=500), 1.3)
        self.r.on_result(result(req, status=500), 1.4)
        self.assertNotIn("api_errors", self.r.holds(1.4))
        self.r.on_result(result(req, status=None, error="conn reset"), 1.5)
        self.assertEqual(self.r.holds(1.5)["api_errors"]["until"], 11.5)
        self.assertEqual(len(self.alerts("api_errors")), 1)
        # purged or frozen requests are not transport errors
        r2 = RiskManager(FakeConfig(), FakeCase(), FakeSchedule(), self.log)
        for _ in range(5):
            r2.on_result(result(req, status=None, sent=False, error="frozen"), 2.0)
        self.assertEqual(r2.holds(2.0), {})

    def test_rejects_disable_symbol(self) -> None:
        req = self._place_req()
        for i in range(4):
            self.r.on_result(result(req, status=400, data={"detail": "bad price"}), 1.0 + i)
        self.assertNotIn("PORT", self.r.disabled())
        for i in range(5):                                         # market-closed texts never count
            self.r.on_result(result(req, status=409, data={"detail": "Market closed"}), 5.0 + i * 0.1)
        self.assertNotIn("PORT", self.r.disabled())
        self.r.on_result(result(req, status=400, data={"detail": "x"}), 6.0)
        self.assertIn("PORT", self.r.disabled())
        # old rejects fall out of the 30-s window
        req2 = self._place_req("ISLE")
        for t in (0.0, 10.0, 20.0, 31.0, 41.0):
            self.r.on_result(result(req2, status=400), t)
        self.assertNotIn("ISLE", self.r.disabled())
        cancel = ApiRequest(req_id=2, kind=ApiKind.CANCEL, symbol="AQUA", order_id="x")
        for i in range(6):
            self.r.on_result(result(cancel, status=404), 50.0 + i)
        self.assertNotIn("AQUA", self.r.disabled())

    def test_drawdown(self) -> None:
        self.r.on_round_start(1, 0.0, 0.0)
        self.r.on_mtm(15000.0, 1, 1.0)
        self.r.on_mtm(-4000.0, 1, 2.0)                             # 19,000 below the peak
        self.assertNotIn("drawdown", self.r.holds(2.0))
        self.r.on_mtm(-5500.0, 1, 3.0)                             # 20,500
        self.assertTrue(self.r.holds(3.0)["drawdown"]["sticky"])
        self.assertEqual(len(self.alerts("drawdown")), 1)
        self.r.on_mtm(-6000.0, 1, 4.0)
        self.assertEqual(len(self.alerts("drawdown")), 1)
        self.r.release("drawdown", 5.0)                            # the operator: peak restarts at the last MTM
        self.r.on_mtm(-7000.0, 1, 6.0)
        self.assertNotIn("drawdown", self.r.holds(6.0))
        # the peak resets at each round start
        self.r.on_mtm(10000.0, 1, 7.0)
        self.r.on_round_start(2, -12000.0, 8.0)
        self.r.on_mtm(-13000.0, 2, 9.0)
        self.assertNotIn("drawdown", self.r.holds(9.0))

    def test_fill_sanity(self) -> None:
        # expected 50 bp, threshold 150 bp: 101.6 is 160 bp of slippage on a buy
        self.r.on_fill(fill(101.6), 1.0)
        self.r.on_fill(fill(101.6), 2.0)
        self.r.on_fill(fill(101.0), 3.0)                           # a good fill resets the run
        self.r.on_fill(fill(101.6), 4.0)
        self.r.on_fill(fill(101.6), 5.0)
        self.r.on_fill(fill(200.0, quality=PriceQuality.LIMIT_BOUND), 5.5)    # ignored
        self.r.on_fill(fill(200.0, strategy="s1r3"), 5.6)                     # SNIPE ignored
        self.assertNotIn("PORT", self.r.disabled())
        self.r.on_fill(fill(98.3, side=Side.SELL), 6.0)            # a bad sell: 170 bp below ref
        self.assertIn("PORT", self.r.disabled())
        self.assertEqual(len(self.alerts("symbol_disabled")), 1)

    def test_fill_sanity_counts_clips_not_fills(self) -> None:
        # one bad clip that comes back as an ack fill plus two reconcile fills is one miss, not three
        for t in (1.0, 2.0, 3.0):
            self.r.on_fill(fill(101.6, lid="e0-77"), t)
        self.assertNotIn("PORT", self.r.disabled())
        self.assertEqual(len(self.log.kinds("fill_sanity_miss")), 1)
        self.r.on_fill(fill(101.6), 4.0)
        self.r.on_fill(fill(101.6), 5.0)
        self.assertIn("PORT", self.r.disabled())

    def test_state_round_trip(self) -> None:
        self.r.hold("restart", 1.0, sticky=True, reason="restart")
        self.r.hold("http_429", 1.0, until=6.0)
        self.r.disable("GRID", "x", 1.0)
        self.r.kill("k", 1.0)
        d = self.r.state_dict()
        r2 = RiskManager(FakeConfig(), FakeCase(), FakeSchedule(), self.log)
        r2.load_state(d)
        self.assertTrue(r2.killed())
        self.assertEqual(r2.disabled(), frozenset({"GRID"}))
        self.assertEqual(set(r2.holds(2.0)), {"restart"})
        r2.load_state("garbage")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
