"""Group B: OrderStore, the TRANSITIONS table, limits accounting, prune and drop_dry."""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_b_support import BTestCase  # noqa: E402

from ctcbot.state.limits import live_contribution, resting_qty, room, strategy_room  # noqa: E402
from ctcbot.state.orders import TRANSITIONS, OrderStore  # noqa: E402
from ctcbot.types import LIVE_STATES, TERMINAL_STATES, OrderState, Priority, Side  # noqa: E402

S = OrderState

# section 5.3, verbatim
EXPECTED = {
    S.NEW: {S.SENDING, S.REJECTED},
    S.SENDING: {S.OPEN, S.FILLED, S.REJECTED, S.UNKNOWN},
    S.OPEN: {S.OPEN, S.FILLED, S.CANCEL_PENDING, S.CANCELED},
    S.CANCEL_PENDING: {S.CANCEL_ACKED, S.CANCEL_UNKNOWN, S.FILLED, S.CANCELED, S.OPEN},
    S.CANCEL_ACKED: {S.CANCELED, S.FILLED},
    S.UNKNOWN: {S.OPEN, S.FILLED, S.CANCELED, S.CANCEL_PENDING},
    S.CANCEL_UNKNOWN: {S.OPEN, S.CANCELED, S.FILLED, S.CANCEL_PENDING},
}


def mk(store: OrderStore, **kw):
    base = dict(strategy="s3", key=None, style="clip", symbol="PORT", side=Side.BUY, qty=10, price=45.0,
                priority=Priority.TAKE)
    base.update(kw)
    return store.new(**base)


class StoreTests(BTestCase):
    def test_table_matches_design(self) -> None:
        for st, tos in EXPECTED.items():
            self.assertEqual(set(TRANSITIONS[st]), tos, st)
        for st in TERMINAL_STATES:
            self.assertEqual(TRANSITIONS[st], frozenset())

    def test_every_legal_edge_passes_and_every_other_raises(self) -> None:
        for src in OrderState:
            for dst in OrderState:
                store = OrderStore(3)
                rec = mk(store)
                rec.state = src
                legal = dst == src or dst in TRANSITIONS[src]
                if legal:
                    store.transition(rec, dst, 1.0)
                    self.assertEqual(rec.state, dst)
                else:
                    with self.assertRaises(ValueError, msg=f"{src}->{dst}"):
                        store.transition(rec, dst, 1.0)
                    self.assertEqual(rec.state, src)

    def test_new_ids_and_remaining(self) -> None:
        store = OrderStore(4)
        a, b = mk(store), mk(store, qty=7)
        self.assertEqual((a.local_id, b.local_id), ("e4-1", "e4-2"))
        self.assertEqual((b.remaining, b.state), (7, S.NEW))
        with self.assertRaises(ValueError):
            mk(store, qty=0)
        with self.assertRaises(TypeError):
            mk(store, bogus=1)

    def test_transition_updates_index_and_terminal_fields(self) -> None:
        store = OrderStore(1)
        r = mk(store)
        store.transition(r, S.SENDING, 1.0, sent_mono=1.0)
        store.transition(r, S.OPEN, 1.2, order_id="X1", acked_mono=1.2, remaining=6, filled_qty=4)
        self.assertIs(store.by_order_id("X1"), r)
        self.assertEqual(store.by_key(None), [])
        store.transition(r, S.OPEN, 1.3, remaining=5)          # same-state update
        self.assertEqual(r.remaining, 5)
        store.transition(r, S.FILLED, 2.0)
        self.assertEqual((r.remaining, r.done_mono), (0, 2.0))
        self.assertEqual(store.live(), [])
        with self.assertRaises(TypeError):
            store.transition(r, S.FILLED, 2.0, state=S.OPEN)

    def test_lookups_and_working_summary(self) -> None:
        store = OrderStore(1)
        c = mk(store, qty=16)                                          # clip buy, NEW: full qty
        r1 = mk(store, style="rest", key="s1r3:PORT:sell", side=Side.SELL, qty=50, price=46.0, strategy="s1r3")
        store.transition(r1, S.SENDING, 0.0)
        store.transition(r1, S.OPEN, 0.1, order_id="R1", remaining=30, filled_qty=20)
        ca = mk(store, qty=8)
        store.transition(ca, S.SENDING, 0.0)
        store.transition(ca, S.OPEN, 0.1, order_id="C2")
        store.transition(ca, S.CANCEL_PENDING, 0.2)
        store.transition(ca, S.CANCEL_ACKED, 0.3)
        mk(store, symbol="ISLE")
        w = store.working_summary("PORT")
        self.assertEqual((w.buy_qty, w.sell_qty), (16 + 8, 30))        # CANCEL_ACKED counts in room
        self.assertEqual((w.clip_buy_qty, w.clip_sell_qty), (16, 0))   # ... but not in exposure
        self.assertTrue(w.clip_working)
        self.assertEqual(w.n_orders, 3)
        self.assertEqual(w.keys, frozenset({"s1r3:PORT:sell"}))
        self.assertEqual(store.by_key("s1r3:PORT:sell"), [r1])
        self.assertIs(store.by_local(c.local_id), c)
        self.assertEqual(len(store.live("PORT")), 3)
        # only a CANCEL_ACKED clip: not working
        store.transition(c, S.REJECTED, 1.0)
        self.assertFalse(store.working_summary("PORT").clip_working)

    def test_prune(self) -> None:
        store = OrderStore(1)
        a, b, c = mk(store), mk(store), mk(store)
        store.transition(a, S.REJECTED, 10.0)
        store.transition(b, S.REJECTED, 100.0)
        self.assertEqual(store.prune(200.0, keep_s=120), 1)
        self.assertIsNone(store.by_local(a.local_id))
        self.assertIs(store.by_local(b.local_id), b)
        self.assertIs(store.by_local(c.local_id), c)                    # live records are never pruned

    def test_drop_dry(self) -> None:
        store = OrderStore(1)
        a, b = mk(store), mk(store)
        store.transition(a, S.SENDING, 0.0)
        store.transition(a, S.OPEN, 0.1, dry=True, order_id="dry-1")
        self.assertEqual(store.drop_dry(), 1)
        self.assertIsNone(store.by_order_id("dry-1"))
        self.assertEqual(store.live(), [b])

    def test_state_round_trip(self) -> None:
        store = OrderStore(2)
        a = mk(store, key="rein_ask", style="rest", side=Side.SELL, strategy="rein_ask", priority=Priority.HEDGE)
        store.transition(a, S.SENDING, 0.0, sent_mono=0.0)
        store.transition(a, S.OPEN, 0.1, order_id="A", acked_mono=0.1)
        done = mk(store)
        store.transition(done, S.REJECTED, 0.2)
        d = json.loads(json.dumps(store.state_dict()))
        s2 = OrderStore(2)
        s2.load_state(d)
        r = s2.by_order_id("A")
        self.assertIsNotNone(r)
        self.assertEqual((r.state, r.side, r.priority, r.key), (S.OPEN, Side.SELL, Priority.HEDGE, "rein_ask"))
        self.assertEqual(len(s2.all()), 1)                              # terminal records are not saved
        self.assertEqual(mk(s2).local_id, "e2-3")                       # the counter continues
        s2.load_state({"records": [{"bad": 1}], "n": "x"})              # malformed input is ignored

    def test_restore_resolves_in_flight_states_and_ages_times(self) -> None:
        store = OrderStore(1)
        sending = mk(store, created_mono=5000.0)
        store.transition(sending, S.SENDING, 5000.0, sent_mono=5000.0)
        cpend = mk(store, created_mono=4990.0)
        store.transition(cpend, S.SENDING, 4990.0, sent_mono=4990.0)
        store.transition(cpend, S.OPEN, 4990.2, order_id="B", acked_mono=4990.2)
        store.transition(cpend, S.CANCEL_PENDING, 4999.0, cancel_sent_mono=4999.0)
        d = json.loads(json.dumps(store.state_dict()))
        s2 = OrderStore(1)
        s2.load_state(d)
        a, b = s2.by_local(sending.local_id), s2.by_order_id("B")
        # no result of the old process's requests can arrive: reconcile adopts or resolves them
        self.assertEqual((a.state, a.remaining), (S.UNKNOWN, 10))
        self.assertEqual(b.state, S.CANCEL_UNKNOWN)
        # times move into the distant past (another process's monotonic clock), keeping their order
        for r in (a, b):
            for f in ("created_mono", "sent_mono", "acked_mono", "cancel_sent_mono"):
                v = getattr(r, f)
                self.assertTrue(v is None or v < -1e5, (r.local_id, f, v))
        self.assertLess(b.sent_mono, a.sent_mono)
        self.assertLess(b.acked_mono, b.cancel_sent_mono)


class LimitsTests(BTestCase):
    def test_room_formulas(self) -> None:
        self.assertEqual(room(200, 50, Side.BUY, 30), 120)
        self.assertEqual(room(200, 50, Side.SELL, 30), 220)
        self.assertEqual(room(200, -250, Side.SELL, 0), 0)            # never negative
        self.assertEqual(strategy_room(200, 50, 10, Side.BUY, 5), 35)
        self.assertEqual(strategy_room(200, 50, -10, Side.SELL, 5), 35)

    def test_resting_qty_counts_by_state(self) -> None:
        store = OrderStore(1)
        new = mk(store, qty=10)                                         # NEW: full qty
        snd = mk(store, qty=20)
        store.transition(snd, S.SENDING, 0.0)                           # SENDING: full qty
        unk = mk(store, qty=30)
        store.transition(unk, S.SENDING, 0.0)
        store.transition(unk, S.UNKNOWN, 0.1)                           # UNKNOWN: full qty
        op = mk(store, qty=40)
        store.transition(op, S.SENDING, 0.0)
        store.transition(op, S.OPEN, 0.1, order_id="O", remaining=15, filled_qty=25)   # OPEN: remaining
        cp = mk(store, qty=50)
        store.transition(cp, S.SENDING, 0.0)
        store.transition(cp, S.OPEN, 0.1, order_id="P", remaining=50)
        store.transition(cp, S.CANCEL_PENDING, 0.2)                     # CANCEL_*: remaining
        sell = mk(store, qty=99, side=Side.SELL)
        done = mk(store, qty=77)
        store.transition(done, S.REJECTED, 0.3)
        self.assertEqual(resting_qty(store, "PORT", Side.BUY), 10 + 20 + 30 + 15 + 50)
        self.assertEqual(resting_qty(store.all(), "PORT", Side.SELL), 99)
        self.assertEqual(resting_qty(store, "ISLE", Side.BUY), 0)
        self.assertEqual(live_contribution(done), 0)
        self.assertEqual(live_contribution(new), 10)
        self.assertTrue(all(s in LIVE_STATES for s in (new.state, snd.state, unk.state, op.state, cp.state,
                                                       sell.state)))


if __name__ == "__main__":
    unittest.main()
