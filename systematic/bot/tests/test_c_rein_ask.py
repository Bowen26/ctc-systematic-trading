"""Group C: the REIN riskless ask (DESIGN.md sections 6.5 and 9.1 C, rein_ask)."""
from __future__ import annotations

import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

from test_c_support import FakeConfig, book, books, make_ctx, make_deps, season  # noqa: E402
from ctcbot.strategies.rein_ask import ReinAsk  # noqa: E402
from ctcbot.types import CancelIntent, Command, Priority, RestIntent, Side, Trigger  # noqa: E402


class ReinAskTest(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()
        self.ra = ReinAsk(make_deps(self.cfg))

    def step(self, t, round_=1, loss=0.0, **kw):
        kw.setdefault("now", 1000.0 * round_ + t)
        return self.ra.decide(make_ctx(t, round_, cfg=self.cfg, seasoninfo=season(loss_known=loss), **kw))

    def test_upper_bound(self):
        self.assertAlmostEqual(self.ra.upper_bound(0.0), 90.0)
        self.assertAlmostEqual(self.ra.upper_bound(1300.0), 90.0)
        self.assertAlmostEqual(self.ra.upper_bound(1500.0), 83.0)
        self.assertEqual(self.ra.price(0.0), 90.02)
        self.assertEqual(self.ra.price(1500.0), 83.02)

    def test_order_and_refresh(self):
        out = self.step(0.5, trigger=Trigger.ROUND_START)
        self.assertEqual(len(out), 1)
        it = out[0]
        self.assertIsInstance(it, RestIntent)
        self.assertEqual((it.key, it.symbol, it.side, it.price, it.priority, it.qty),
                         ("rein_ask", "REIN", Side.SELL, 90.02, Priority.HEDGE, None))
        self.assertEqual(it.edge_usd, 0.0)
        self.assertEqual(self.step(2.0), [])
        self.assertEqual(self.step(5.4), [])
        self.assertEqual(len(self.step(5.5)), 1)                       # every refresh_s
        self.assertEqual(self.step(6.0), [])
        out = self.step(6.25, loss=1500.0)                             # U moved: re-post at once
        self.assertEqual(out[0].price, 83.02)
        self.assertEqual(len(self.step(7.0, trigger=Trigger.ROUND_START, round_=2)), 1)

    def test_market_open_only_and_round_ends(self):
        self.assertEqual(self.step(10.0, running=False), [])
        self.assertEqual(len(self.step(779.0)), 1)                     # rounds 1-2 until the close
        self.assertEqual(self.step(780.0), [])
        self.assertEqual(len(self.step(739.0, round_=3)), 1)
        out = self.step(740.0, round_=3)
        self.assertEqual(len(out), 1)
        c = out[0]
        self.assertIsInstance(c, CancelIntent)
        self.assertEqual((c.scope, c.key, c.priority), ("key", "rein_ask", Priority.SNIPE))
        self.assertEqual(self.step(741.0, round_=3), [])
        self.assertEqual(self.step(760.0, round_=3), [])

    def test_freeze_cancels_and_stops(self):
        self.assertEqual(len(self.step(10.0)), 1)
        out = self.step(11.0, frozen={"REIN"})
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], CancelIntent)
        self.assertEqual(self.step(30.0, frozen={"REIN"}), [])
        out = self.step(31.0)                                         # unfrozen: re-post at once
        self.assertIsInstance(out[0], RestIntent)
        self.assertEqual(self.step(32.0, disabled={"REIN"}), [])

    def test_marketable_edge_and_command(self):
        out = self.step(10.0, bk=books(REIN=book("REIN", None, bid=90.10, ask=90.30)))
        self.assertAlmostEqual(out[0].edge_usd, 0.08)
        ctx = make_ctx(10.0, 1, cfg=self.cfg)
        res = self.ra.on_command(Command("rein", ("off",)), ctx)
        self.assertEqual([(c.scope, c.key) for c in res], [("key", "rein_ask")])
        self.assertEqual(self.step(20.0), [])
        self.assertEqual(self.ra.on_command(Command("rein", ("on",)), ctx), [])
        self.assertEqual(len(self.step(21.0)), 1)
        self.assertIsNone(self.ra.on_command(Command("rein_ask", ("on",)), ctx))


if __name__ == "__main__":
    unittest.main()
