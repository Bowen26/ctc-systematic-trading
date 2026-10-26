"""Group B: Reconciler (adoption, absence, attribution, partial fills, foreign orders and fills, failures)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_b_support import BTestCase, PassRisk, Rig, ack, clock, feed, get_req, orow, prow, result  # noqa: E402

from ctcbot.state.limits import resting_qty  # noqa: E402
from ctcbot.types import (ApiKind, ApiRequest, Fill, OrderState, PriceQuality, Priority, RestIntent,  # noqa: E402
                          Side, TargetIntent)

S = OrderState


def tgt(sym: str, lb: float | None, ls: float | None = None, target: int = 100, clip: int = 16) -> TargetIntent:
    return TargetIntent(strategy="s3", symbol=sym, target=target, clip_qty=clip, clip_interval_s=2.0, limit_buy=lb,
                        limit_sell=ls, priority=Priority.TAKE, ttl_s=2.0, edge_usd_per_share=0.1, expected_cost_bp=50.0,
                        ref_mid=100.0)


class RecBase(BTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.rig = Rig(self.log, risk=PassRisk())
        for s, (b, a) in {"PORT": (99.9, 100.1), "ISLE": (59.9, 60.1), "GEN": (29.9, 30.1)}.items():
            self.rig.set_book(s, b, a)

    def place(self, sym: str, now: float, lb: float | None = None, ls: float | None = None, **kw) -> ApiRequest:
        self.rig.om.set_intents([tgt(sym, lb, ls, **kw)], now)
        reqs = [r for r in self.rig.om.step(now, clock(now), feed()) if r.kind == ApiKind.PLACE]
        self.assertEqual(len(reqs), 1)
        return reqs[0]

    def rest(self, key: str, sym: str, side: Side, px: float, qty: int, now: float) -> ApiRequest:
        self.rig.om.set_intents([RestIntent(strategy="s1r3", key=key, symbol=sym, side=side, price=px,
                                            priority=Priority.SNIPE, qty=qty)], now)
        reqs = [r for r in self.rig.om.step(now, clock(now), feed()) if r.kind == ApiKind.PLACE]
        self.assertEqual(len(reqs), 1)
        return reqs[0]

    def ack_open(self, req: ApiRequest, now: float, oid: str | None = None, filled: int = 0,
                 avg: float | None = None) -> list[Fill]:
        rem = req.qty - filled
        return self.rig.om.on_result(result(req, 200, parsed=ack(oid or f"X{req.req_id}", "open" if rem else "filled",
                                                                 filled, rem, avg), t0=now, t1=now), now)

    def cycle(self, t: float, open_rows: list, pos: dict, t_open: float | None = None, t_done: float | None = None,
              order: str = "open_first", pos_status: int = 200, open_status: int = 200):
        """Run one reconcile cycle begun at ``t``; GET_OPEN is 'sent' at ``t_open`` (default ``t``)."""
        cid = self.rig.rec.begin_cycle(t)
        to = t if t_open is None else t_open
        done = (t_done if t_done is not None else to + 0.05)
        r_open = result(get_req(ApiKind.GET_OPEN, cid), open_status, parsed=open_rows if open_status == 200 else None,
                        data=[{}] * len(open_rows), t0=to, t1=done)
        r_pos = result(get_req(ApiKind.GET_POSITIONS, cid, rid=10_001), pos_status,
                       parsed=pos if pos_status == 200 else None, data=[{}] * len(pos), t0=to, t1=done)
        first, second = (r_open, r_pos) if order == "open_first" else (r_pos, r_open)
        self.assertIsNone(self.rig.rec.on_result(first, done))
        return self.rig.rec.on_result(second, done)

    def rec(self, req: ApiRequest):
        return self.rig.store.by_local(req.local_id)


class Adoption(RecBase):
    def test_lost_ack_becomes_unknown_and_is_adopted(self) -> None:
        p = self.place("PORT", 80.0, lb=100.11)
        self.rig.om.on_result(result(p, None, error="timeout", t0=80.0, t1=82.0), 82.0)
        r = self.rec(p)
        self.assertEqual(r.state, S.UNKNOWN)
        self.assertEqual(self.rig.rec.unknown_age(83.0), 3.0)
        fills = self.cycle(82.5, [orow("EX1", "PORT", Side.BUY, 100.11, 16, 16)], {})
        self.assertEqual(fills, [])
        self.assertEqual((r.state, r.order_id), (S.OPEN, "EX1"))
        self.assertEqual([x for x in self.rig.store.live() if x.style == "foreign"], [])
        self.assertIsNone(self.rig.rec.unknown_age(83.0))

    def test_sending_record_adopted_and_late_ack_idempotent(self) -> None:
        p = self.place("PORT", 80.0, lb=100.11)
        fills = self.cycle(80.2, [orow("EX2", "PORT", Side.BUY, 100.11, 16, 10)], {"PORT": prow("PORT", 6, 100.11)})
        r = self.rec(p)
        # adopted; the symbol is skipped (PLACE in flight), so the lower remaining waits and nothing is booked
        self.assertEqual((r.state, r.order_id, r.remaining), (S.OPEN, "EX2", 16))
        self.assertEqual(fills, [])
        self.assertEqual(self.rig.positions.pos["PORT"], 0)
        late = self.ack_open(p, 80.3, oid="EX2", filled=6, avg=100.11)
        self.assertEqual([(f.qty, f.source) for f in late], [(6, "ack")])
        self.assertEqual((r.state, r.filled_qty, r.remaining, self.rig.positions.pos["PORT"]), (S.OPEN, 6, 10, 6))
        self.assertEqual(r.acked_mono, 80.3)
        self.assertEqual(len(self.rig.store.live("PORT")), 1)       # no foreign duplicate
        for t in (80.5, 81.0):                                      # the waiting drop is now covered by the ack
            self.assertEqual(self.cycle(t, [orow("EX2", "PORT", Side.BUY, 100.11, 16, 10)],
                                        {"PORT": prow("PORT", 6, 100.11)}), [])
        self.assertEqual((r.filled_qty, self.rig.positions.pos["PORT"]), (6, 6))
        self.assertEqual(self.alerts("position_mismatch"), [])

    def test_unknown_rows_become_foreign_records(self) -> None:
        self.cycle(10.0, [orow("BROWSER1", "GEN", Side.SELL, 31.0, 20, 15)], {"GEN": prow("GEN", -5, 31.0)})
        f = [x for x in self.rig.store.live() if x.style == "foreign"]
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0].state, f[0].remaining, f[0].strategy), (S.OPEN, 15, "foreign"))
        self.assertEqual(resting_qty(self.rig.store, "GEN", Side.SELL), 15)    # counts toward room
        self.assertEqual(len(self.alerts("foreign_order")), 1)
        self.cycle(10.5, [orow("BROWSER1", "GEN", Side.SELL, 31.0, 20, 15)], {"GEN": prow("GEN", -5, 31.0)})
        self.assertEqual(len([x for x in self.rig.store.live() if x.style == "foreign"]), 1)
        self.assertEqual(len(self.alerts("foreign_order")), 1)
        self.rig.risk.kill("t", 11.0)
        k = self.rig.om.kill(11.0)
        self.assertEqual(self.rec_by_oid("BROWSER1").state, S.CANCEL_PENDING)   # the kill covers it
        self.assertEqual(k[0].kind, ApiKind.CANCEL_ALL)

    def rec_by_oid(self, oid: str):
        return self.rig.store.by_order_id(oid)


class Absence(RecBase):
    def test_cancel_states_seen_in_get_keep_state(self) -> None:
        p = self.place("PORT", 80.0, lb=100.11)
        self.ack_open(p, 80.1)
        c = self.rig.om.step(82.0, clock(82.0), feed())[0]
        r = self.rec(p)
        self.assertEqual(r.state, S.CANCEL_PENDING)
        self.cycle(82.05, [orow(r.order_id, "PORT", Side.BUY, 100.11, 16, 16)], {})
        self.assertEqual(r.state, S.CANCEL_PENDING)
        self.rig.om.on_result(result(c, 200, parsed={}), 82.1)
        self.assertEqual(r.state, S.CANCEL_ACKED)
        self.cycle(82.08, [orow(r.order_id, "PORT", Side.BUY, 100.11, 16, 16)], {}, t_open=82.08)  # sent before ack
        self.assertEqual(r.state, S.CANCEL_ACKED)
        # first cycle sent after the cancel ack without the order: resolved at once
        self.cycle(82.5, [], {})
        self.assertEqual(r.state, S.CANCELED)

    def test_cancel_acked_partly_filled_before_cancel(self) -> None:
        p = self.place("PORT", 80.0, lb=100.11)
        self.ack_open(p, 80.1)
        c = self.rig.om.step(82.0, clock(82.0), feed())[0]
        self.rig.om.on_result(result(c, 200, parsed={}), 82.1)
        fills = self.cycle(82.5, [], {"PORT": prow("PORT", 4, 100.05)})
        r = self.rec(p)
        self.assertEqual((r.state, r.filled_qty), (S.CANCELED, 4))
        self.assertEqual([(f.qty, f.quality, f.price) for f in fills], [(4, PriceQuality.INFERRED, 100.05)])
        self.assertEqual(resting_qty(self.rig.store, "PORT", Side.BUY), 0)

    def test_absent_explained_fills_same_cycle_unexplained_cancels_after_two(self) -> None:
        a = self.place("PORT", 80.0, lb=100.11)
        b = self.place("ISLE", 80.0, lb=60.11)
        self.ack_open(a, 80.1)
        self.ack_open(b, 80.1)
        fills = self.cycle(80.5, [], {"PORT": prow("PORT", 16, 100.08)})
        self.assertEqual(self.rec(a).state, S.FILLED)
        self.assertEqual([(f.symbol, f.qty, f.price, f.quality) for f in fills],
                         [("PORT", 16, 100.08, PriceQuality.INFERRED)])
        self.assertEqual(self.rig.positions.pos["PORT"], 16)
        self.assertAlmostEqual(self.rig.positions.cash, -16 * 100.08)
        rb = self.rec(b)
        self.assertEqual((rb.state, rb.missing_cycles), (S.OPEN, 1))
        self.cycle(81.0, [], {"PORT": prow("PORT", 16, 100.08)})
        self.assertEqual(rb.state, S.CANCELED)
        self.assertEqual(rb.filled_qty, 0)

    def test_first_absence_is_not_explained_by_an_older_change(self) -> None:
        p = self.place("GEN", 80.0, lb=30.11, target=50, clip=3)
        self.ack_open(p, 80.0)
        r = self.rec(p)
        # +5 GEN that no order explains: the resting order shows open with its full remaining
        self.cycle(80.5, [orow(r.order_id, "GEN", Side.BUY, 30.11, 3, 3)], {"GEN": prow("GEN", 5, 30.0)})
        self.assertEqual(r.state, S.OPEN)
        # the order fills after this cycle's position GET: the rows still say 5, the open orders lack it
        self.cycle(81.0, [], {"GEN": prow("GEN", 5, 30.0)})
        self.assertEqual(r.state, S.OPEN)                          # not resolved against the older +5
        exchange = 5 + 3
        self.assertGreaterEqual(self.rig.positions.pos["GEN"] + resting_qty(self.rig.store, "GEN", Side.BUY),
                                exchange)                          # room never under-counts
        self.cycle(81.5, [], {"GEN": prow("GEN", 8, 30.04)})       # now the rows hold its fill
        self.assertEqual((r.state, r.filled_qty), (S.FILLED, 3))
        self.assertEqual(self.rig.positions.pos["GEN"], 8)

    def test_absence_not_counted_for_get_sent_before_ack(self) -> None:
        p = self.place("PORT", 80.0, lb=100.11)
        cid = self.rig.rec.begin_cycle(80.05)
        self.ack_open(p, 80.3)
        self.rig.rec.on_result(result(get_req(ApiKind.GET_OPEN, cid), 200, parsed=[], data=[], t0=80.05, t1=80.35), 80.35)
        self.rig.rec.on_result(result(get_req(ApiKind.GET_POSITIONS, cid), 200, parsed={}, data=[], t0=80.05,
                                      t1=80.35), 80.35)
        r = self.rec(p)
        self.assertEqual((r.state, r.missing_cycles), (S.OPEN, 0))

    def test_unknown_absent_only_after_grace(self) -> None:
        p = self.place("PORT", 80.0, lb=100.11)
        self.rig.om.on_result(result(p, 503, t0=80.0, t1=80.1), 80.1)
        r = self.rec(p)
        self.cycle(80.5, [], {})                                   # inside the 1-s grace
        self.assertEqual(r.missing_cycles, 0)
        self.cycle(81.2, [], {"PORT": prow("PORT", 16, 100.1)})     # it filled after all
        self.assertEqual((r.state, r.filled_qty), (S.FILLED, 16))
        self.assertEqual(self.rig.positions.pos["PORT"], 16)

    def test_partial_fill_from_open_row_applied_next_cycle(self) -> None:
        p = self.place("PORT", 80.0, lb=100.11)
        self.ack_open(p, 80.1)
        # the row shows the fill: the change is held (and reserved against the lower remaining) ...
        fills = self.cycle(80.5, [orow(f"X{p.req_id}", "PORT", Side.BUY, 100.11, 16, 10)],
                           {"PORT": prow("PORT", 6, 100.02)})
        r = self.rec(p)
        self.assertEqual(fills, [])
        self.assertEqual((r.state, r.remaining, r.filled_qty), (S.OPEN, 16, 0))
        self.assertEqual(self.rig.positions.pos["PORT"], 6)
        self.assertEqual(resting_qty(self.rig.store, "PORT", Side.BUY), 16)    # conservative until booked
        # ... and booked at the next cycle, which certainly holds it
        fills = self.cycle(81.0, [orow(f"X{p.req_id}", "PORT", Side.BUY, 100.11, 16, 10)],
                           {"PORT": prow("PORT", 6, 100.02)})
        self.assertEqual([(f.qty, f.price, f.quality) for f in fills], [(6, 100.02, PriceQuality.INFERRED)])
        self.assertEqual((r.state, r.remaining, r.filled_qty), (S.OPEN, 10, 6))
        self.assertEqual(self.rig.positions.pos["PORT"], 6)
        self.assertAlmostEqual(self.rig.positions.cash, -6 * 100.02)

    def test_drop_before_the_row_shows_it_stays_conservative(self) -> None:
        p = self.place("PORT", 80.0, lb=100.11)
        self.ack_open(p, 80.1)
        # GET_POSITIONS answered before the fill, GET_OPEN after: the row lags the open orders
        fills = self.cycle(80.5, [orow(f"X{p.req_id}", "PORT", Side.BUY, 100.11, 16, 10)], {})
        self.assertEqual(fills, [])
        self.assertEqual(self.rig.positions.pos["PORT"], 0)
        self.assertEqual(resting_qty(self.rig.store, "PORT", Side.BUY), 16)   # position + resting = 16 >= truth
        fills = self.cycle(81.0, [orow(f"X{p.req_id}", "PORT", Side.BUY, 100.11, 16, 10)],
                           {"PORT": prow("PORT", 6, 100.11)})
        self.assertEqual([(f.qty, f.quality) for f in fills], [(6, PriceQuality.INFERRED)])
        self.assertEqual((self.rig.positions.pos["PORT"], resting_qty(self.rig.store, "PORT", Side.BUY)), (6, 10))
        self.assertEqual(self.cycle(81.5, [orow(f"X{p.req_id}", "PORT", Side.BUY, 100.11, 16, 10)],
                                    {"PORT": prow("PORT", 6, 100.11)}), [])
        self.assertEqual(self.alerts("position_mismatch"), [])

    def test_fills_both_ways_inside_one_window(self) -> None:
        b = self.rest("kb", "PORT", Side.BUY, 99.0, 50, 80.0)
        self.ack_open(b, 80.0)
        s_ = self.rest("ks", "PORT", Side.SELL, 101.0, 50, 82.5)
        self.ack_open(s_, 82.5)
        ob, os_ = f"X{b.req_id}", f"X{s_.req_id}"
        # +12 on the buy and -20 on the sell between two cycles: the net row change is -8
        self.cycle(83.0, [orow(ob, "PORT", Side.BUY, 99.0, 50, 38), orow(os_, "PORT", Side.SELL, 101.0, 50, 30)],
                   {"PORT": prow("PORT", -8, 101.0)})
        fills = self.cycle(83.5, [orow(ob, "PORT", Side.BUY, 99.0, 50, 38), orow(os_, "PORT", Side.SELL, 101.0, 50, 30)],
                           {"PORT": prow("PORT", -8, 101.0)})
        self.assertEqual(sorted((f.side, f.qty) for f in fills), [(Side.BUY, 12), (Side.SELL, 20)])
        self.assertEqual(self.rig.positions.pos["PORT"], -8)
        self.assertAlmostEqual(self.rig.positions.cash, -12 * 99.0 + 20 * 101.0)
        self.assertEqual(self.alerts("position_mismatch"), [])

    def test_same_cycle_drop_wins_over_absent_record(self) -> None:
        old = self.place("PORT", 80.0, lb=99.5, target=50, clip=10)
        self.ack_open(old, 80.0)
        c = self.rig.om.step(82.0, clock(82.0), feed())[0]           # its remainder is cancelled
        self.rig.om.on_result(result(c, 200, parsed={}), 82.05)
        new = self.rest("kn", "PORT", Side.BUY, 99.6, 20, 82.1)
        self.ack_open(new, 82.1)
        ro = self.rec(old)
        # the resting order filled 5 (seen as a lower remaining now); the cancelled one is gone unfilled
        self.cycle(82.5, [orow(f"X{new.req_id}", "PORT", Side.BUY, 99.6, 20, 15)], {"PORT": prow("PORT", 5, 99.6)})
        self.assertEqual(ro.state, S.CANCELED)
        self.assertEqual(ro.filled_qty, 0)
        fills = self.cycle(83.0, [orow(f"X{new.req_id}", "PORT", Side.BUY, 99.6, 20, 15)],
                           {"PORT": prow("PORT", 5, 99.6)})
        self.assertEqual([(f.local_id, f.qty) for f in fills], [(new.local_id, 5)])
        self.assertEqual(self.alerts("position_mismatch"), [])

    def test_resting_order_fill_between_gets_does_not_go_to_absent_record(self) -> None:
        a = self.place("PORT", 80.0, lb=99.5, target=50, clip=10)
        self.ack_open(a, 80.0)
        c = self.rig.om.step(82.0, clock(82.0), feed())[0]
        self.rig.om.on_result(result(c, 200, parsed={}), 82.05)
        r_ = self.rest("kr", "PORT", Side.BUY, 99.6, 20, 82.1)
        self.ack_open(r_, 82.1)
        ra = self.rec(a)
        # GET_OPEN before the resting order filled 7, GET_POSITIONS after: the row shows +7 first
        self.cycle(82.5, [orow(f"X{r_.req_id}", "PORT", Side.BUY, 99.6, 20, 20)], {"PORT": prow("PORT", 7, 99.6)})
        self.assertNotEqual(ra.state, S.FILLED)                       # not given to the absent cancelled order
        self.assertEqual(ra.filled_qty, 0)
        self.cycle(83.0, [orow(f"X{r_.req_id}", "PORT", Side.BUY, 99.6, 20, 13)], {"PORT": prow("PORT", 7, 99.6)})
        fills = self.cycle(83.5, [orow(f"X{r_.req_id}", "PORT", Side.BUY, 99.6, 20, 13)],
                           {"PORT": prow("PORT", 7, 99.6)})
        self.assertEqual(self.rec(r_).filled_qty, 7)
        self.assertEqual(ra.state, S.CANCELED)
        self.assertEqual(ra.filled_qty, 0)
        self.assertEqual(self.alerts("position_mismatch"), [])
        self.assertTrue(fills is not None)

    def test_skipped_symbol_defers_drops(self) -> None:
        p = self.rest("kp", "PORT", Side.BUY, 99.0, 50, 80.0)
        self.ack_open(p, 80.0)
        q = self.rest("kq", "PORT", Side.BUY, 99.1, 5, 82.0)           # in flight: PORT is skipped
        self.cycle(82.5, [orow(f"X{p.req_id}", "PORT", Side.BUY, 99.0, 50, 30)], {"PORT": prow("PORT", 20, 99.0)})
        r = self.rec(p)
        self.assertEqual((r.remaining, r.filled_qty, self.rig.positions.pos["PORT"]), (50, 0, 0))
        self.ack_open(q, 82.6)
        fills = self.cycle(83.0, [orow(f"X{p.req_id}", "PORT", Side.BUY, 99.0, 50, 30),
                                  orow(f"X{q.req_id}", "PORT", Side.BUY, 99.1, 5, 5)], {"PORT": prow("PORT", 20, 99.0)})
        self.assertEqual([(f.local_id, f.qty) for f in fills], [(p.local_id, 20)])
        self.assertEqual((r.remaining, r.filled_qty, self.rig.positions.pos["PORT"]), (30, 20, 20))

class ForeignFills(RecBase):
    def test_unexplained_delta_becomes_foreign_after_second_cycle(self) -> None:
        self.assertEqual(self.cycle(10.0, [], {"GEN": prow("GEN", 5, 30.0)}), [])
        self.assertEqual(self.rig.positions.pos["GEN"], 5)          # the exchange position is accepted
        fills = self.cycle(10.5, [], {"GEN": prow("GEN", 5, 30.0)})
        self.assertEqual([(f.strategy, f.source, f.side, f.qty) for f in fills], [("foreign", "foreign", Side.BUY, 5)])
        self.assertEqual(len(self.alerts("position_mismatch")), 1)
        self.assertEqual(self.cycle(11.0, [], {"GEN": prow("GEN", 5, 30.0)}), [])

    def test_late_explanation_cancels_the_carry(self) -> None:
        self.cycle(10.0, [], {"GEN": prow("GEN", 5, 30.0)})
        # a late fill report moves the internal position as well (double count until the next cycle)
        self.rig.positions.apply_fill(Fill(local_id=None, order_id=None, strategy="s3", key=None, symbol="GEN",
                                           side=Side.BUY, qty=5, price=30.0, quality=PriceQuality.EXACT, mono=10.2,
                                           ref_mid=None, expected_cost_bp=None, source="ack"))
        self.assertEqual(self.cycle(10.5, [], {"GEN": prow("GEN", 5, 30.0)}), [])
        self.assertEqual(self.rig.positions.pos["GEN"], 5)
        self.assertEqual(self.alerts("position_mismatch"), [])

    def test_fill_before_ack_is_explained_by_the_ack(self) -> None:
        p = self.place("PORT", 80.0, lb=100.11)
        # the exchange filled it and /positions shows it before the ack: the in-flight symbol is skipped
        self.assertEqual(self.cycle(80.05, [], {"PORT": prow("PORT", 16, 100.1)}), [])
        self.assertEqual(self.rig.positions.pos["PORT"], 0)
        self.ack_open(p, 80.2, filled=16, avg=100.1)
        self.assertEqual(self.rig.positions.pos["PORT"], 16)
        for t in (80.5, 81.0):
            self.assertEqual(self.cycle(t, [], {"PORT": prow("PORT", 16, 100.1)}), [])
        self.assertEqual(self.alerts("position_mismatch"), [])

    def test_late_fill_for_recently_cancelled_record(self) -> None:
        p = self.place("GEN", 80.0, ls=29.9, target=-50, clip=10)
        self.ack_open(p, 80.0)
        r = self.rec(p)
        self.cycle(80.5, [], {})                                   # absent, nothing moved
        self.cycle(81.0, [], {})                                   # second cycle: CANCELED
        self.assertEqual((r.state, r.filled_qty), (S.CANCELED, 0))
        # its fill reaches /positions late: the carry goes to it instead of becoming foreign
        self.assertEqual(self.cycle(81.5, [], {"GEN": prow("GEN", -4, 29.9)}), [])
        fills = self.cycle(82.0, [], {"GEN": prow("GEN", -4, 29.9)})
        self.assertEqual([(f.local_id, f.qty, f.strategy) for f in fills], [(p.local_id, 4, "s3")])
        self.assertEqual((r.state, r.filled_qty), (S.CANCELED, 4))
        self.assertEqual(self.alerts("position_mismatch"), [])

    def test_positions_stale_alert(self) -> None:
        self.place("PORT", 80.0, lb=100.11)                         # never answered
        for i in range(12):
            self.cycle(80.1 + i * 0.5, [], {})
        self.assertEqual(len(self.alerts("positions_stale")), 1)


class Failures(RecBase):
    def test_failed_get_skips_cycle(self) -> None:
        self.assertIsNone(self.cycle(10.0, [], {}, pos_status=500))
        self.assertEqual(self.rig.rec.consecutive_failures(), 1)
        self.assertIsNone(self.rig.rec.last_open_rows())
        self.assertEqual(self.cycle(10.5, [], {}), [])
        self.assertEqual(self.rig.rec.consecutive_failures(), 0)
        self.assertEqual(self.rig.rec.last_open_rows(), [])

    def test_unparsed_body_and_stale_cycle(self) -> None:
        cid = self.rig.rec.begin_cycle(1.0)
        res = result(get_req(ApiKind.GET_OPEN, cid), 200, parsed=[], data=[{"weird": 1}], t0=1.0)
        self.assertIsNone(self.rig.rec.on_result(res, 1.1))
        self.assertEqual(self.rig.rec.consecutive_failures(), 1)
        cid = self.rig.rec.begin_cycle(2.0)
        old = result(get_req(ApiKind.GET_OPEN, cid - 1), 200, parsed=[], data=[], t0=1.0)
        self.assertIsNone(self.rig.rec.on_result(old, 2.1))          # other cycle: ignored
        self.rig.rec.on_result(result(get_req(ApiKind.GET_OPEN, cid), 200, parsed=[], data=[], t0=2.0), 2.1)
        self.assertIsNone(self.rig.rec.on_result(
            result(get_req(ApiKind.GET_POSITIONS, cid), 200, parsed={}, data=[], t0=2.0), 4.5))   # > 2 s: dropped
        self.assertEqual(self.rig.rec.consecutive_failures(), 2)
        cid = self.rig.rec.begin_cycle(5.0)
        self.rig.rec.begin_cycle(7.5)                                 # the old one timed out
        self.assertEqual(self.rig.rec.consecutive_failures(), 3)


if __name__ == "__main__":
    unittest.main()
