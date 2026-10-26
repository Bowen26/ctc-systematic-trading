"""Tests for the 23 Oct strategy updates: S1-r3 value ordering and the ``ov`` announcement-override command."""
from __future__ import annotations

import unittest

from ctcbot.app import console
from ctcbot.strategies.s1r3 import capturable_usd
from ctcbot.types import BookLevel, Side


class TestCapturableUsd(unittest.TestCase):
    def test_buy_walks_asks_up_to_limit_price(self):
        asks = (BookLevel(10.0, 5), BookLevel(10.5, 5), BookLevel(11.5, 100))
        # settlement 11.0, our buy limit 10.9: takes 10.0 x5 and 10.5 x5, not 11.5
        self.assertAlmostEqual(capturable_usd(asks, Side.BUY, 10.9, 11.0, 1000), 5 * 1.0 + 5 * 0.5)

    def test_buy_capped_by_room(self):
        asks = (BookLevel(10.0, 5), BookLevel(10.5, 5))
        self.assertAlmostEqual(capturable_usd(asks, Side.BUY, 10.9, 11.0, 7), 5 * 1.0 + 2 * 0.5)

    def test_sell_walks_bids(self):
        bids = (BookLevel(12.0, 3), BookLevel(11.6, 4), BookLevel(11.0, 50))
        # settlement 11.0, our sell limit 11.1: takes 12.0 x3 and 11.6 x4, not 11.0
        self.assertAlmostEqual(capturable_usd(bids, Side.SELL, 11.1, 11.0, 1000), 3 * 1.0 + 4 * 0.6)

    def test_nothing_marketable_is_zero(self):
        self.assertEqual(capturable_usd((BookLevel(12.0, 3),), Side.BUY, 10.9, 11.0, 100), 0.0)
        self.assertEqual(capturable_usd((), Side.SELL, 11.1, 11.0, 100), 0.0)
        self.assertEqual(capturable_usd((BookLevel(10.0, 5),), Side.BUY, 10.9, 11.0, 0), 0.0)

    def test_bad_levels_are_skipped(self):
        class Bad:
            price = "x"
            qty = 3
        self.assertAlmostEqual(capturable_usd((Bad(), BookLevel(10.0, 2)), Side.BUY, 10.9, 11.0, 10), 2.0)


class TestOverrideCommand(unittest.TestCase):
    def test_short_forms(self):
        r = console.resolve_override
        self.assertEqual(r("haven.attach"), "settle.insurance.HAVEN.attach")
        self.assertEqual(r("HAVEN.TOP"), "settle.insurance.HAVEN.top")
        self.assertEqual(r("rein.attach"), "settle.insurance.REIN.attach")
        self.assertEqual(r("port.a"), "settle.island.PORT.a")
        self.assertEqual(r("port.k"), "settle.island.PORT.K")
        self.assertEqual(r("port.v0"), "settle.island.PORT.V0")
        self.assertEqual(r("port.dmg"), "physics.k.PORT")
        self.assertEqual(r("recon.c"), "settle.rebuild.RECON.c")
        self.assertEqual(r("gen.b"), "settle.rebuild.GEN.b")

    def test_full_paths_and_refusals(self):
        r = console.resolve_override
        self.assertEqual(r("settle.insurance.HAVEN.attach"), "settle.insurance.HAVEN.attach")
        self.assertEqual(r("physics.k.FERRY"), "physics.k.FERRY")
        for bad in ("settle.insurance.haven.attach", "haven.retention", "arch.a", "foo", "", "engine.mc.n",
                    "settle.island.PORT", "physics.sigma.PORT"):
            self.assertIsNone(r(bad), bad)

    def test_parse_keeps_case_and_validate(self):
        cmd = console.parse("ov settle.insurance.HAVEN.attach 500")
        self.assertEqual(cmd.args, ("settle.insurance.HAVEN.attach", "500"))
        self.assertIsNone(console.validate(cmd))
        self.assertIsNone(console.validate(console.parse("ov haven.attach off")))
        self.assertIsNotNone(console.validate(console.parse("ov haven.attach")))
        self.assertIsNotNone(console.validate(console.parse("ov haven.attach abc")))
        self.assertIsNotNone(console.validate(console.parse("ov nonsense 1")))
        self.assertTrue(console.is_known("ov"))


if __name__ == "__main__":
    unittest.main()
