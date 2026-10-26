"""Group B: OrderManager (clips, rests, cancels, ack handling, kill) with a fake limiter and fake A/C parts."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_b_support import (BTestCase, FakeConfig, FakeLimiter, PassRisk, Rig, ack, clock, feed, get_req,  # noqa: E402
                            orow, result)

from ctcbot.types import (ApiKind, ApiRequest, CancelIntent, Fill, OrderState, PriceQuality, Priority,  # noqa: E402
                          RestIntent, Side, TargetIntent)

S = OrderState
BOOKS = {"PORT": (99.90, 100.10), "ISLE": (59.90, 60.10), "AQUA": (32.95, 33.05), "GRID": (49.90, 50.10),
         "REIN": (79.90, 80.10), "ARCH": (99.0, 101.0), "FERRY": (19.95, 20.05), "MAT": (9.98, 10.02),
         "GEN": (29.9, 30.1)}


def tgt(sym: str = "PORT", target: int = 200, clip: int = 16, lb: float | None = 100.5, ls: float | None = 99.5,
        prio: Priority = Priority.TAKE, edge: float = 0.1, reduce_only: bool = False, strategy: str = "s3",
        interval: float = 2.0, ttl: float = 2.0) -> TargetIntent:
    return TargetIntent(strategy=strategy, symbol=sym, target=target, clip_qty=clip, clip_interval_s=interval,
                        limit_buy=lb, limit_sell=ls, priority=prio, ttl_s=ttl, reduce_only=reduce_only,
                        edge_usd_per_share=edge, expected_cost_bp=50.0, ref_mid=100.0)


def rest(key: str, sym: str, side: Side, px: float, qty: int | None = None, prio: Priority = Priority.SNIPE,
         edge: float = 0.0, strategy: str = "s1r3") -> RestIntent:
    return RestIntent(strategy=strategy, key=key, symbol=sym, side=side, price=px, priority=prio, qty=qty, edge_usd=edge)


def kinds(reqs: list[ApiRequest]) -> list[ApiKind]:
    return [r.kind for r in reqs]


class OMBase(BTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.rig = self.make_rig()
        self.emitted: list[ApiRequest] = []

    def make_rig(self, **kw) -> Rig:
        rig = Rig(self.log, **kw)
        for s, (b, a) in BOOKS.items():
            rig.set_book(s, b, a)
        return rig

    def step(self, now: float, intents=(), t: float | None = None, rig: Rig | None = None) -> list[ApiRequest]:
        rig = rig or self.rig
        if intents:
            rig.om.set_intents(list(intents), now)
        reqs = rig.om.step(now, clock(t if t is not None else now), feed())
        self.emitted += reqs
        self.check_invariants(rig)
        return reqs

    def check_invariants(self, rig: Rig) -> None:
        """No record is CANCEL_PENDING without an emitted cancel covering it."""
        for r in rig.store.live():
            if r.state != S.CANCEL_PENDING:
                continue
            covered = any((q.kind == ApiKind.CANCEL and q.local_id == r.local_id) or
                          (q.kind == ApiKind.CANCEL_ALL and q.symbol in (None, r.symbol)) for q in self.emitted)
            self.assertTrue(covered, f"{r.local_id} CANCEL_PENDING without a request")

    def ack_open(self, req: ApiRequest, now: float, filled: int = 0, avg: float | None = None, rig: Rig | None = None,
                 oid: str | None = None) -> list[Fill]:
        rig = rig or self.rig
        rem = req.qty - filled
        return rig.om.on_result(result(req, 200, parsed=ack(oid or f"X{req.req_id}", "open" if rem else "filled",
                                                             filled, rem, avg), t0=now, t1=now), now)

    def ack_ok(self, req: ApiRequest, now: float, status: int = 200, rig: Rig | None = None, **kw) -> None:
        (rig or self.rig).om.on_result(result(req, status, parsed={"status": "cancelled"}, t0=now, t1=now, **kw), now)

    def rec_of(self, req: ApiRequest, rig: Rig | None = None):
        return (rig or self.rig).store.by_local(req.local_id)


class TargetTests(OMBase):
    def test_clip_cadence_ttl_and_cancel_acked(self) -> None:
        sends: list[float] = []
        reqs = self.step(80.0, [tgt()])
        self.assertEqual(kinds(reqs), [ApiKind.PLACE])
        p1 = reqs[0]
        self.assertEqual((p1.qty, p1.side, p1.price, p1.priority), (16, Side.BUY, 100.5, Priority.TAKE))
        sends.append(80.0)
        fills = self.ack_open(p1, 80.05, filled=6, avg=100.08)
        self.assertEqual(len(fills), 1)
        self.assertEqual(self.rig.positions.pos["PORT"], 6)
        t = 80.25
        cancel = None
        while t < 82.0 - 1e-9:                                  # one live clip: nothing until the ttl
            self.assertEqual(self.step(t, [tgt()]), [])
            t += 0.25
        reqs = self.step(82.0, [tgt()])
        self.assertEqual(kinds(reqs), [ApiKind.CANCEL])          # remainder cancel at ttl, no new clip yet
        cancel = reqs[0]
        self.assertEqual(cancel.priority, Priority.HEDGE)
        self.assertEqual(self.rec_of(p1).state, S.CANCEL_PENDING)
        self.ack_ok(cancel, 82.1)
        self.assertEqual(self.rec_of(p1).state, S.CANCEL_ACKED)
        reqs = self.step(82.25, [tgt()])                         # CANCEL_ACKED does not block the next clip
        self.assertEqual(kinds(reqs), [ApiKind.PLACE])
        sends.append(82.25)
        self.assertEqual(reqs[0].qty, 16)
        from ctcbot.state.limits import resting_qty
        self.assertEqual(resting_qty(self.rig.store, "PORT", Side.BUY), 10 + 16)   # acked remainder still in room
        self.assertGreaterEqual(min(b - a for a, b in zip(sends, sends[1:])), 2.0)
        w = self.rig.om.working()["PORT"]
        self.assertEqual((w.clip_buy_qty, w.buy_qty), (16, 26))

    def test_interval_from_last_send(self) -> None:
        reqs = self.step(80.0, [tgt()])
        self.ack_open(reqs[0], 80.0, filled=16, avg=100.1)       # fully filled at once
        self.assertEqual(self.step(81.0, [tgt()]), [])           # filled, but the interval is 2 s
        self.assertEqual(kinds(self.step(82.0, [tgt()])), [ApiKind.PLACE])

    def test_stale_target_ignored(self) -> None:
        self.rig.om.set_intents([tgt()], 80.0)
        self.assertEqual(self.step(80.7), [])

    def test_reduce_only_never_crosses_zero(self) -> None:
        self.rig.positions.pos["PORT"] = 10
        reqs = self.step(100.0, [tgt(target=-200, clip=30, reduce_only=True, prio=Priority.HEDGE)])
        self.assertEqual([(r.side, r.qty) for r in reqs], [(Side.SELL, 10)])
        self.ack_open(reqs[0], 100.0, filled=10, avg=99.9)
        self.assertEqual(self.rig.positions.pos["PORT"], 0)
        self.assertEqual(self.step(103.0, [tgt(target=-200, clip=30, reduce_only=True)]), [])
        self.rig.positions.pos["ISLE"] = -7
        reqs = self.step(103.0, [tgt("ISLE", target=50, clip=30, lb=60.5, reduce_only=True)])
        self.assertEqual([(r.side, r.qty) for r in reqs], [(Side.BUY, 7)])

    def test_target_clamped_to_scaled_limit(self) -> None:
        rig = self.make_rig(cfg=FakeConfig({"sizing.scale": 0.25}))
        rig.positions.pos["PORT"] = 45
        reqs = self.step(100.0, [tgt(target=200, clip=16)], rig=rig)
        self.assertEqual(reqs[0].qty, 5)                         # L_s = 50

    def test_priority_and_edge_order_under_short_budget(self) -> None:
        lim = FakeLimiter(cap=12)                                # TAKE gets 2 actions, HEDGE more
        rig = self.make_rig(limiter=lim)
        intents = [tgt("PORT", clip=16, edge=0.1), tgt("ISLE", clip=25, lb=60.5, edge=0.5),
                   tgt("GRID", clip=33, lb=50.5, edge=0.0, prio=Priority.HEDGE, strategy="flatten")]
        reqs = self.step(100.0, intents, rig=rig)
        self.assertEqual([r.symbol for r in reqs], ["GRID", "ISLE"])
        self.assertEqual(rig.om.counters["budget_denied"], 1)

    def test_risk_rejection_skips_and_logs_once(self) -> None:
        reqs = self.step(100.0, [tgt(lb=103.0)])                 # 3% through: marketable_far
        self.assertEqual(reqs, [])
        self.step(100.25, [tgt(lb=103.0)])
        self.assertEqual(len([f for k, f in self.log.events if k == "risk_reject"]), 1)


class CancelTests(OMBase):
    def _open_clip(self, now: float, sym: str = "PORT", rig: Rig | None = None, **kw) -> ApiRequest:
        rig = rig or self.rig
        b, a = BOOKS[sym]
        reqs = self.step(now, [tgt(sym, lb=round(a + 0.01, 2), **kw)], rig=rig)
        self.assertEqual(kinds(reqs), [ApiKind.PLACE])
        self.ack_open(reqs[0], now, filled=1, avg=a, rig=rig)
        return reqs[0]

    def test_cancels_before_places(self) -> None:
        p = self._open_clip(80.0)
        reqs = self.step(82.0, [tgt("ISLE", lb=60.11)])
        self.assertEqual(kinds(reqs), [ApiKind.CANCEL, ApiKind.PLACE])
        self.assertEqual(reqs[0].local_id, p.local_id)

    def test_remainder_cancel_priorities(self) -> None:
        self._open_clip(80.0)
        self.assertEqual(self.step(82.0)[0].priority, Priority.HEDGE)
        lim = FakeLimiter()
        rig = self.make_rig(limiter=lim)
        self._open_clip(680.0, rig=rig)
        lim.reserve_window = (680.0, 780.0)
        reqs = self.step(682.0, rig=rig)
        self.assertEqual([(r.kind, r.priority) for r in reqs], [(ApiKind.CANCEL, Priority.SNIPE)])

    def test_denied_cancel_stays_queued(self) -> None:
        lim = FakeLimiter()
        rig = self.make_rig(limiter=lim)
        p = self._open_clip(80.0, rig=rig)
        lim._cap = 3                                     # HEDGE floor 4: denied
        for t in (82.0, 82.25, 82.5):
            self.assertEqual(self.step(t, rig=rig), [])
            self.assertEqual(rig.store.by_local(p.local_id).state, S.OPEN)
        self.assertGreaterEqual(rig.om.counters["cancel_denied"], 3)
        lim._cap = 150
        reqs = self.step(82.75, rig=rig)
        self.assertEqual(kinds(reqs), [ApiKind.CANCEL])
        self.assertEqual(rig.store.by_local(p.local_id).state, S.CANCEL_PENDING)
        self.assertEqual(self.step(83.0, rig=rig), [])           # never a second cancel for the record

    def test_overlapping_cancel_intents(self) -> None:
        reqs = self.step(740.0, [rest("s1r3:PORT:buy", "PORT", Side.BUY, 99.0, qty=50),
                                 rest("s1r3:ISLE:buy", "ISLE", Side.BUY, 59.0, qty=50)])
        for r in reqs:
            self.ack_open(r, 740.0)
        self.rig.positions.pos["PORT"] = 0
        # a clip of another strategy in PORT keeps "every live record in scope" false
        self._open_clip(741.0, "PORT", strategy="s3")
        cis = [CancelIntent("s1r3", Priority.SNIPE, "key", key="s1r3:PORT:buy"),
               CancelIntent("s1r3", Priority.SNIPE, "key_prefix", key="s1r3:PORT:"),
               CancelIntent("s1r3", Priority.SNIPE, "strategy", symbol="PORT")]
        out = self.step(741.5, cis)
        cancels = [r for r in out if r.kind == ApiKind.CANCEL]
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0].priority, Priority.SNIPE)
        self.assertEqual(self.step(741.75, cis), [])             # already being cancelled

    def test_strategy_scope_uses_cancel_all_when_whole_symbol_in_scope(self) -> None:
        reqs = self.step(740.0, [rest("s1r3:PORT:buy", "PORT", Side.BUY, 99.0, qty=50),
                                 rest("s1r3:PORT:sell", "PORT", Side.SELL, 101.0, qty=50)])
        for r in reqs:
            self.ack_open(r, 740.0)
        out = self.step(741.0, [CancelIntent("s1r3", Priority.TAKE, "strategy")])
        self.assertEqual([(r.kind, r.symbol, r.priority) for r in out], [(ApiKind.CANCEL_ALL, "PORT", Priority.HEDGE)])
        self.assertTrue(all(r.state == S.CANCEL_PENDING for r in self.rig.store.live("PORT")))
        self.ack_ok(out[0], 741.1)
        self.assertTrue(all(r.state == S.CANCEL_ACKED for r in self.rig.store.live("PORT")))

    def test_symbol_scope_and_cancel_on_ack(self) -> None:
        out = self.step(100.0, [CancelIntent("announce", Priority.SNIPE, "symbol", symbol="GEN")])
        self.assertEqual([(r.kind, r.symbol) for r in out], [(ApiKind.CANCEL_ALL, "GEN")])   # always, even empty
        p = self.step(101.0, [tgt("PORT", lb=100.11)])[0]                                  # SENDING
        out = self.step(101.1, [CancelIntent("announce", Priority.SNIPE, "symbol", symbol="PORT")])
        self.assertEqual([(r.kind, r.symbol, r.priority) for r in out], [(ApiKind.CANCEL_ALL, "PORT", Priority.SNIPE)])
        rec = self.rec_of(p)
        self.assertEqual(rec.state, S.SENDING)
        self.assertTrue(rec.cancel_on_ack)
        self.ack_ok(out[0], 101.2)                                # the CANCEL_ALL came back before the PLACE ack
        self.assertEqual(rec.state, S.SENDING)
        # a repeated identical intent with nothing new is not sent again
        self.assertEqual(self.step(101.25, [CancelIntent("announce", Priority.SNIPE, "symbol", symbol="PORT")]), [])
        self.ack_open(p, 101.3)                                   # the order rested after the cancel
        out = self.step(101.35)
        self.assertEqual([(r.kind, r.local_id, r.priority) for r in out], [(ApiKind.CANCEL, p.local_id, Priority.SNIPE)])

    def test_cancel_result_table(self) -> None:
        p = self._open_clip(80.0)
        c = self.step(82.0)[0]
        rec = self.rec_of(p)
        self.ack_ok(c, 82.1, status=404)
        self.assertEqual(rec.state, S.CANCEL_PENDING)             # reconcile decides
        p2 = self._open_clip(90.0, "ISLE")
        c2 = self.step(92.0)[0]
        self.ack_ok(c2, 92.1, status=503)
        self.assertEqual(self.rec_of(p2).state, S.CANCEL_UNKNOWN)
        p3 = self._open_clip(100.0, "AQUA")
        c3 = self.step(102.0)[0]
        self.rig.om.on_result(result(c3, None, sent=False, error="connect failed"), 102.1)
        self.assertEqual(self.rec_of(p3).state, S.OPEN)            # back to its previous state ...
        self.assertEqual(self.step(102.2), [])                     # ... backing off 0.25 s (hardening) ...
        again = self.step(102.4)
        self.assertEqual([(r.kind, r.local_id) for r in again], [(ApiKind.CANCEL, p3.local_id)])   # ... re-queued

    def test_dry_records_cancel_at_once(self) -> None:
        req = self.step(80.0, [tgt()])[0]
        self.rig.om.on_result(result(req, 200, parsed=ack("dry-1", "dry_run", 0, req.qty), dry=True, sent=False), 80.0)
        rec = self.rec_of(req)
        self.assertEqual((rec.state, rec.dry, rec.order_id), (S.OPEN, True, "dry-1"))
        c = self.step(82.0)[0]
        self.rig.om.on_result(result(c, 200, dry=True, sent=False), 82.0)
        self.assertEqual(rec.state, S.CANCELED)


class AckTests(OMBase):
    def _place(self, now: float = 80.0) -> ApiRequest:
        return self.step(now, [tgt()])[0]

    def test_place_result_table(self) -> None:
        cases = [
            (dict(status=200, parsed=ack("A", "filled", 16, 0, 100.07)), S.FILLED),
            (dict(status=200, parsed=ack("B", "open", 0, 16)), S.OPEN),
            (dict(status=200, parsed=ack(None, "open", 0, 16)), S.UNKNOWN),
            (dict(status=200, parsed=ack(None, "filled", 16, 0)), S.FILLED),
            (dict(status=401), S.REJECTED), (dict(status=429), S.REJECTED),
            (dict(status=400, data={"detail": "bad"}), S.REJECTED),
            (dict(status=503), S.UNKNOWN), (dict(status=None, error="timeout"), S.UNKNOWN),
            (dict(status=None, sent=False, error="purged"), S.REJECTED),
        ]
        t = 80.0
        for kw, want in cases:
            rig = self.make_rig(risk=PassRisk())
            req = self.step(t, [tgt()], rig=rig)[0]
            fills = rig.om.on_result(result(req, **kw), t)
            self.assertEqual(rig.store.by_local(req.local_id).state, want, kw)
            if want == S.FILLED:
                self.assertEqual(rig.positions.pos["PORT"], 16)
                q = PriceQuality.EXACT if kw["parsed"].avg_fill_price else PriceQuality.LIMIT_BOUND
                self.assertEqual([(f.qty, f.quality, f.source) for f in fills], [(16, q, "ack")])
        self.assertEqual(len(self.alerts("ack_no_id")), 1)
        rig = self.make_rig(risk=PassRisk())
        req = self.step(t, [tgt()], rig=rig)[0]
        rig.om.on_result(result(req, 400, data={"detail": "Insufficient room"}), t)
        self.assertIn("Insufficient room", rig.store.by_local(req.local_id).reject_reason)

    def test_place_result_releases_guard(self) -> None:
        req = self._place()
        self.assertEqual(self.rig.positions.quiet_stamp()["PORT"][0], 1)
        self.ack_open(req, 80.1)
        self.assertEqual(self.rig.positions.quiet_stamp()["PORT"][0], 0)

    def test_ack_partial_then_late_reconcile_is_idempotent(self) -> None:
        req = self._place()
        self.ack_open(req, 80.1, filled=6, avg=100.05)
        rec = self.rec_of(req)
        again = self.rig.om.on_result(result(req, 200, parsed=ack(rec.order_id, "open", 6, 10, 100.05)), 80.2)
        self.assertEqual(again, [])                               # nothing new
        self.assertEqual((rec.filled_qty, self.rig.positions.pos["PORT"]), (6, 6))


class RestTests(OMBase):
    def test_rest_placed_once_topped_up_and_bounded(self) -> None:
        intent = rest("s1r3:PORT:sell", "PORT", Side.SELL, 101.0)
        out = self.step(740.0, [intent])
        self.assertEqual([(r.side, r.qty, r.price, r.priority) for r in out], [(Side.SELL, 200, 101.0, Priority.SNIPE)])
        self.ack_open(out[0], 740.05)
        self.assertEqual(self.step(740.5, [intent]), [])           # idempotent ensure
        # an exchange-side cancel (reconcile resolved it CANCELED) frees room: topped up again
        rec = self.rec_of(out[0])
        self.rig.store.transition(rec, S.CANCELED, 740.7)
        out2 = self.step(741.0, [intent])
        self.assertEqual([(r.side, r.qty) for r in out2], [(Side.SELL, 200)])
        self.ack_open(out2[0], 741.05)
        # more sell room (we got longer): a second order tops up to the new room
        self.rig.positions.pos["PORT"] = 100
        out3 = self.step(741.5, [intent])
        self.assertEqual([(r.side, r.qty) for r in out3], [(Side.SELL, 100)])
        self.ack_open(out3[0], 741.55)
        self.rig.positions.pos["PORT"] = 150
        self.assertEqual(self.step(742.0, [intent]), [])           # 2 orders per key at most
        self.assertEqual(len(self.rig.store.by_key("s1r3:PORT:sell")), 2)

    def test_rest_replaced_on_price_change(self) -> None:
        out = self.step(740.0, [rest("rein_ask", "REIN", Side.SELL, 90.02, qty=60, prio=Priority.HEDGE,
                                     strategy="rein_ask")])
        self.ack_open(out[0], 740.0)
        out2 = self.step(745.0, [rest("rein_ask", "REIN", Side.SELL, 89.50, qty=60, prio=Priority.HEDGE,
                                      strategy="rein_ask")])
        self.assertEqual([(r.kind, r.price) for r in out2], [(ApiKind.CANCEL, None), (ApiKind.PLACE, 89.50)])
        self.assertEqual(out2[0].local_id, out[0].local_id)
        self.assertEqual(out2[0].priority, Priority.HEDGE)

    def test_rest_using_all_room_waits_for_old_cancel(self) -> None:
        a = rest("s1r3:PORT:buy", "PORT", Side.BUY, 99.0)
        out = self.step(740.0, [a])
        self.ack_open(out[0], 740.0)
        b = rest("s1r3:PORT:buy", "PORT", Side.BUY, 99.2)
        out2 = self.step(740.5, [b])
        self.assertEqual(kinds(out2), [ApiKind.CANCEL])            # the old order still holds the room
        self.ack_ok(out2[0], 740.6)
        self.assertEqual(self.step(741.0, [b]), [])                # CANCEL_ACKED still counts in room
        self.rig.store.transition(self.rec_of(out[0]), S.CANCELED, 741.1)    # reconcile resolved it
        out3 = self.step(741.5, [b])
        self.assertEqual([(r.kind, r.qty, r.price) for r in out3], [(ApiKind.PLACE, 200, 99.2)])

    def test_two_rests_never_exceed_room(self) -> None:
        out = self.step(740.0, [rest("k1", "PORT", Side.BUY, 99.0), rest("k2", "PORT", Side.BUY, 98.0)])
        self.assertEqual(sum(r.qty for r in out if r.kind == ApiKind.PLACE), 200)

    def test_scaled_room_for_s1r3_and_rein_ask(self) -> None:
        rig = self.make_rig(cfg=FakeConfig({"sizing.scale": 0.25}))
        out = self.step(740.0, [rest("s1r3:PORT:buy", "PORT", Side.BUY, 99.0),
                                rest("s1r3:PORT:sell", "PORT", Side.SELL, 101.0),
                                rest("rein_ask", "REIN", Side.SELL, 90.02, prio=Priority.HEDGE, strategy="rein_ask")],
                        rig=rig)
        got = {(r.symbol, r.side): r.qty for r in out}
        # HEDGE orders are capped at 0.5 L_s each (risk rule 5), so the REIN ask needs its second order
        self.assertEqual(got, {("PORT", Side.BUY): 50, ("PORT", Side.SELL): 50, ("REIN", Side.SELL): 25})
        for r in out:
            self.ack_open(r, 740.0, rig=rig)
        out_r = self.step(740.2, [rest("rein_ask", "REIN", Side.SELL, 90.02, prio=Priority.HEDGE,
                                       strategy="rein_ask")], rig=rig)
        self.assertEqual([(r.symbol, r.qty) for r in out_r], [("REIN", 25)])
        self.ack_open(out_r[0], 740.2, rig=rig)
        self.assertEqual(self.step(740.3, [rest("rein_ask", "REIN", Side.SELL, 90.02, prio=Priority.HEDGE,
                                                strategy="rein_ask")], rig=rig), [])
        from ctcbot.state.limits import resting_qty
        self.assertEqual(resting_qty(rig.store, "REIN", Side.SELL), 50)        # the ask ends at -L_s
        # after a fill of 30 on the buy, the sell side may take the position to -L_s, never beyond
        buy = next(r for r in out if r.side == Side.BUY and r.symbol == "PORT")
        rec = rig.store.by_local(buy.local_id)
        rig.store.transition(rec, S.OPEN, 740.2, remaining=20, filled_qty=30)
        rig.positions.pos["PORT"] = 30
        out2 = self.step(740.5, [rest("s1r3:PORT:buy", "PORT", Side.BUY, 99.0),
                                 rest("s1r3:PORT:sell", "PORT", Side.SELL, 101.0)], rig=rig)
        self.assertEqual([(r.side, r.qty) for r in out2], [(Side.SELL, 30)])

    def test_marketable_rests_first_larger_edge_first(self) -> None:
        out = self.step(740.0, [rest("k:GRID", "GRID", Side.BUY, 49.0, qty=10, edge=0.0),
                                rest("k:ISLE", "ISLE", Side.BUY, 60.20, qty=10, edge=0.10),
                                rest("k:AQUA", "AQUA", Side.BUY, 33.10, qty=10, edge=0.05),
                                rest("k:FERRY", "FERRY", Side.SELL, 19.90, qty=100, edge=0.05)])
        self.assertEqual([r.symbol for r in out], ["FERRY", "ISLE", "AQUA", "GRID"])  # 5.0, 1.0, 0.5, resting

    def test_self_cross_and_max_resting(self) -> None:
        out = self.step(740.0, [rest("ask", "PORT", Side.SELL, 100.05, qty=10)])
        self.ack_open(out[0], 740.0)
        self.assertEqual(self.step(740.5, [tgt(lb=100.10)], t=100.0), [])     # would cross our own ask
        self.assertTrue(any(f["reason"] == "self_cross" for k, f in self.log.events if k == "risk_reject"))
        intents = [rest(f"k{i}", "ISLE", Side.BUY, 59.0 - i * 0.1, qty=5) for i in range(9)]
        out = self.step(741.0, intents)
        self.assertEqual(len(out), 8)


class KillTests(OMBase):
    def setUp(self) -> None:
        super().setUp()
        self.lim = FakeLimiter()
        self.rig = self.make_rig(limiter=self.lim)

    def _setup_orders(self) -> tuple[ApiRequest, ApiRequest, ApiRequest]:
        a = self.step(80.0, [tgt("PORT", lb=100.11)])[0]
        b = self.step(80.0, [tgt("ISLE", lb=60.11)])[0]
        c = self.step(80.0, [tgt("AQUA", lb=33.06)])[0]
        self.ack_open(a, 80.1)
        self.ack_open(b, 80.1)
        return a, b, c                                              # c stays SENDING

    def kill(self, now: float) -> list[ApiRequest]:
        self.rig.risk.kill("test", now)
        out = self.rig.om.kill(now)
        self.emitted += out
        return out

    def test_kill_even_with_budget_exhausted(self) -> None:
        self._setup_orders()
        for _ in range(147):
            self.lim.try_acquire(Priority.TAKE, 0, 80.0)
            self.lim.granted.append((80.0, Priority.TAKE, "x"))
        self.assertEqual(self.lim.try_acquire(Priority.TAKE, 1, 80.2), [])
        out = self.kill(80.3)
        self.assertEqual([(r.kind, r.priority, r.symbol) for r in out], [(ApiKind.CANCEL_ALL, Priority.KILL, None)])
        self.assertIsNotNone(out[0].ticket)
        self.assertEqual(self.rig.om.kill_status()["pending"], 3)

    def test_kill_followups_and_completion(self) -> None:
        a, b, c = self._setup_orders()
        k1 = self.kill(81.0)[0]
        self.assertEqual(self.step(81.1), [])                         # one kill CANCEL_ALL in flight at most
        self.ack_ok(k1, 81.2)
        self.assertEqual(self.rec_of(a).state, S.CANCEL_ACKED)
        self.assertEqual(self.rec_of(c).state, S.SENDING)
        out = self.step(81.25)                                        # (c) a kill-set record is still SENDING
        self.assertEqual(kinds(out), [ApiKind.CANCEL_ALL])
        k2 = out[0]
        self.ack_open(c, 81.3)                                        # late OPEN ack -> follow-up wanted
        self.assertEqual(self.step(81.35), [])                        # k2 still in flight
        self.ack_ok(k2, 81.4)
        self.assertEqual(self.step(81.45), [])                        # 0.25 s spacing from k2 (81.25)
        out = self.step(81.5)
        self.assertEqual(kinds(out), [ApiKind.CANCEL_ALL])
        self.assertEqual(self.rec_of(c).state, S.CANCEL_PENDING)
        self.ack_ok(out[0], 81.55)
        self.assertFalse(self.rig.om.kill_status()["complete"])
        # reconcile cycles: the first one resolves the acked cancels, the second (sent after) completes the kill
        for t in (81.6, 82.1, 82.6):
            cid = self.rig.rec.begin_cycle(t)
            self.rig.rec.on_result(result(get_req(ApiKind.GET_OPEN, cid), 200, parsed=[], data=[], t0=t, t1=t + 0.05),
                                   t + 0.05)
            fills = self.rig.rec.on_result(result(get_req(ApiKind.GET_POSITIONS, cid), 200, parsed={}, data=[], t0=t,
                                                  t1=t + 0.05), t + 0.05)
            self.assertIsNotNone(fills)
            self.assertEqual(self.rig.om.kill_followup(self.rig.rec.last_open_rows(), t + 0.05), [])
        st = self.rig.om.kill_status()
        self.assertTrue(st["complete"])
        self.assertEqual((st["pending"], st["actions"]), (0, 3))
        self.assertEqual(len([k for k, _ in self.log.events if k == "kill_complete"]), 1)
        self.assertEqual(self.rig.om.step(83.0, clock(83.0), feed()), [])     # FROZEN: nothing else

    def test_followup_on_open_rows_and_overdraft_stop(self) -> None:
        self._setup_orders()
        k1 = self.kill(81.0)[0]
        self.ack_ok(k1, 81.1)
        rows = [orow("ZZ", "GEN", Side.BUY, 29.0, 5, 5)]              # a foreign order shows up
        out = self.rig.om.kill_followup(rows, 81.5)
        self.assertEqual(kinds(out), [ApiKind.CANCEL_ALL])
        self.ack_ok(out[0], 81.6)
        self.lim.granted += [(81.6, Priority.TAKE, "x")] * 170        # cap 150 + overdraft 20 exhausted
        self.assertEqual(self.rig.om.kill_followup(rows, 82.0), [])
        self.assertEqual(len(self.alerts("kill_incomplete")), 1)
        self.assertFalse(self.rig.om.kill_status()["complete"])

    def test_cancel_all_uses_given_priority(self) -> None:
        self._setup_orders()
        self.lim.reserve_window = (0.0, 1e9)
        out = self.rig.om.cancel_all(Priority.KILL, 85.0, reason="cx")
        self.assertEqual([(r.kind, r.priority, r.symbol) for r in out], [(ApiKind.CANCEL_ALL, Priority.KILL, None)])
        out = self.rig.om.cancel_all(Priority.SNIPE, 85.1, symbol="ISLE", reason="feed_down")
        self.assertEqual([(r.priority, r.symbol) for r in out], [(Priority.SNIPE, "ISLE")])


class DryAndUnknown(OMBase):
    def test_dry_records_never_reconcile_fill(self) -> None:
        req = self.step(80.0, [tgt()])[0]
        self.rig.om.on_result(result(req, 200, parsed=ack("dry-9", "dry_run", 0, req.qty), dry=True, sent=False), 80.0)
        rec = self.rec_of(req)
        for t in (80.5, 81.0, 81.5):
            cid = self.rig.rec.begin_cycle(t)
            self.rig.rec.on_result(result(get_req(ApiKind.GET_OPEN, cid), 200, parsed=[], data=[], t0=t), t)
            fills = self.rig.rec.on_result(result(get_req(ApiKind.GET_POSITIONS, cid), 200, parsed={}, data=[], t0=t), t)
            self.assertEqual(fills, [])
        self.assertEqual((rec.state, rec.filled_qty), (S.OPEN, 0))

    def test_unknown_blocks_next_clip(self) -> None:
        req = self.step(80.0, [tgt()])[0]
        self.rig.om.on_result(result(req, None, error="timeout"), 82.0)
        self.assertEqual(self.rec_of(req).state, S.UNKNOWN)
        self.assertEqual(self.step(84.0, [tgt()]), [])


class LifecycleEdges(OMBase):
    def test_retired_season_om_does_not_book_into_new_positions(self) -> None:
        old = self.rig
        req = self.step(80.0, [tgt()])[0]
        # a confirmed season reset: the core resets the positions and builds a new store and OM, keeping the
        # old OM only to absorb its late results
        old.positions.reset()
        from ctcbot.exec.order_manager import OrderManager
        from ctcbot.state.orders import OrderStore
        new_om = OrderManager(old.cfg, old.case, OrderStore(2), old.positions, old.books, old.risk, old.limiter,
                              self.log, old.ids)
        fills = self.ack_open(req, 80.2, filled=16, avg=100.05)          # the old season's ack, with a fill
        self.assertEqual(len(fills), 1)                                  # reported (the core logs it as retired)
        self.assertEqual(old.positions.pos["PORT"], 0)                   # but not booked into the new season
        self.assertEqual(old.positions.cash, 0.0)
        self.assertEqual(self.rec_of(req).state, S.FILLED)
        self.assertEqual(old.positions.quiet_stamp()["PORT"][0], 0)      # the in-flight guard still released
        req2 = new_om.step(81.0, clock(81.0), feed())
        self.assertEqual(req2, [])                                       # no intents in the new OM yet
        new_om.set_intents([tgt()], 81.0)
        p = new_om.step(81.0, clock(81.0), feed())[0]
        new_om.on_result(result(p, 200, parsed=ack("N1", "filled", 16, 0, 100.1)), 81.1)
        self.assertEqual(old.positions.pos["PORT"], 16)                  # the current OM books normally

    def test_intents_while_frozen_are_dropped(self) -> None:
        self.rig.risk.kill("test", 90.0)
        self.emitted += self.rig.om.kill(90.0)
        self.rig.om.set_intents([tgt(), rest("s1r3:GEN:buy", "GEN", Side.BUY, 29.0),
                                 CancelIntent("s1r3", Priority.SNIPE, "key_prefix", key="s1r3:")], 90.1)
        self.rig.risk.unkill(95.0)
        self.assertEqual(self.step(95.0), [])                            # nothing stale survives the kill

    def test_reverted_cancel_all_does_not_double_cancel(self) -> None:
        a = self.step(80.0, [tgt("PORT", lb=100.11)])[0]
        b = self.step(80.0, [tgt("ISLE", lb=60.11)])[0]
        self.ack_open(a, 80.1)
        self.ack_open(b, 80.1)
        ca = self.step(80.5, [CancelIntent("s3", Priority.TAKE, "all")])
        self.assertEqual(kinds(ca), [ApiKind.CANCEL_ALL])
        self.rig.om.on_result(result(ca[0], None, sent=False, error="frozen"), 80.6)
        self.assertEqual((self.rec_of(a).state, self.rec_of(b).state), (S.OPEN, S.OPEN))
        self.assertEqual(self.step(80.7), [])                           # backing off 0.25 s (hardening)
        again = self.step(80.9)
        self.assertEqual(kinds(again), [ApiKind.CANCEL_ALL])            # one re-queued CANCEL_ALL, no per-order
        self.assertEqual((self.rec_of(a).state, self.rec_of(b).state), (S.CANCEL_PENDING, S.CANCEL_PENDING))


if __name__ == "__main__":
    unittest.main()
