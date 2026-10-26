"""Group B: BookStore (touch precedence, None sides, wall-only) and the fake-config sanity check."""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_b_support import B_CONFIG, BOT, BTestCase, FakeConfig, book_ev, quote_ev  # noqa: E402

from ctcbot.state.books import BookStore, parse_server_ts  # noqa: E402
from ctcbot.types import BookEvent, BookLevel, TradeEvent  # noqa: E402

T0 = "2026-10-23T10:47:26.809171"
T1 = "2026-10-23T10:47:27.000000"
T2 = "2026-10-23T10:47:28.500000"


class BookTests(BTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bs = BookStore(("PORT", "ARCH"), FakeConfig())

    def test_book_only_touch_and_levels(self) -> None:
        self.bs.on_book(book_ev("PORT", 44.85, 45.12, ts=T0, recv=1.0, bq=18, aq=10))
        v = self.bs.view("PORT", 2.5)
        self.assertEqual((v.bid, v.ask, v.bid_size, v.ask_size), (44.85, 45.12, 18, 10))
        self.assertAlmostEqual(v.mid, (44.85 + 45.12) / 2)
        self.assertAlmostEqual(v.half_spread_bp, (45.12 - 44.85) / 2 / v.mid * 1e4)
        self.assertAlmostEqual(v.age_s, 1.5)
        self.assertEqual(len(v.bids), 2)
        self.assertFalse(v.empty)
        self.assertFalse(v.wall_only)
        self.assertEqual(v.server_ts, T0)

    def test_newer_quote_wins_touch_but_levels_stay_from_book(self) -> None:
        self.bs.on_book(book_ev("PORT", 44.85, 45.12, ts=T0, recv=1.0))
        self.bs.on_quote(quote_ev("PORT", 44.90, 45.00, ts=T1, recv=1.1))
        v = self.bs.view("PORT", 2.0)
        self.assertEqual((v.bid, v.ask), (44.90, 45.00))
        self.assertEqual(v.bids[0].price, 44.85)        # depth from the orderbook
        self.assertEqual(v.server_ts, T1)

    def test_newer_book_wins_over_older_quote_even_if_received_later(self) -> None:
        self.bs.on_quote(quote_ev("PORT", 44.90, 45.00, ts=T1, recv=1.0))
        self.bs.on_book(book_ev("PORT", 44.70, 45.30, ts=T2, recv=1.2))
        self.assertEqual(self.bs.view("PORT", 2.0).bid, 44.70)
        # an older-stamped quote received afterwards does not move the touch
        self.bs.on_quote(quote_ev("PORT", 44.95, 44.99, ts=T0, recv=1.5))
        v = self.bs.view("PORT", 2.0)
        self.assertEqual((v.bid, v.ask), (44.70, 45.30))

    def test_tie_or_unparseable_stamp_falls_back_to_receive_order(self) -> None:
        self.bs.on_book(book_ev("PORT", 44.70, 45.30, ts="garbage", recv=1.0))
        self.bs.on_quote(quote_ev("PORT", 44.80, 45.20, ts="garbage", recv=2.0))
        self.assertEqual(self.bs.view("PORT", 3.0).bid, 44.80)

    def test_none_sides(self) -> None:
        self.bs.on_quote(quote_ev("PORT", None, 45.00, ts=T0, recv=1.0))
        v = self.bs.view("PORT", 1.0)
        self.assertIsNone(v.bid)
        self.assertEqual(v.ask, 45.00)
        self.assertIsNone(v.mid)
        self.assertIsNone(v.half_spread_bp)
        self.assertFalse(v.empty)
        self.assertFalse(v.wall_only)
        self.bs.on_book(BookEvent(symbol="PORT", bids=(), asks=(), server_ts=T1, recv_mono=2.0))
        v = self.bs.view("PORT", 2.0)
        self.assertTrue(v.empty)
        self.assertEqual(v.bids, ())
        self.assertIsNone(v.mid)

    def test_wall_only_over_300_bp(self) -> None:
        # half-spread exactly 300 bp is not wall-only; above it is
        self.bs.on_quote(quote_ev("ARCH", 97.0, 103.0, ts=T0, recv=0.0))      # hs = 3 / 100 = 300 bp
        self.assertFalse(self.bs.view("ARCH", 0.0).wall_only)
        self.bs.on_quote(quote_ev("ARCH", 96.0, 104.0, ts=T1, recv=0.1))      # 400 bp
        self.assertTrue(self.bs.view("ARCH", 0.1).wall_only)
        bs = BookStore(("ARCH",), FakeConfig({"guards.opening.max_half_spread_bp": 500.0}))
        bs.on_quote(quote_ev("ARCH", 96.0, 104.0, ts=T1, recv=0.1))
        self.assertFalse(bs.view("ARCH", 0.1).wall_only)

    def test_unknown_symbol_views_and_reset(self) -> None:
        v = self.bs.view("NOPE", 0.0)
        self.assertTrue(v.empty)
        self.assertIsNone(v.age_s)
        self.bs.on_book(book_ev("PORT", 44.85, 45.12, ts=T0, recv=1.0))
        self.assertEqual(set(self.bs.views(1.0)), {"PORT", "ARCH"})
        self.bs.reset()
        self.assertTrue(self.bs.view("PORT", 1.0).empty)

    def test_trades_and_bad_levels_do_not_raise(self) -> None:
        self.bs.on_trade(TradeEvent(symbol="PORT", price=45.12, qty=5, trade_id="x", server_ts=T0, recv_mono=1.0))
        self.assertEqual(self.bs.last_trade("PORT")[:2], (45.12, 5))
        self.bs.on_book(BookEvent(symbol="PORT", bids=(BookLevel(0.0, 5), BookLevel(44.0, 3)),
                                  asks=(BookLevel(46.0, 2),), server_ts=T1, recv_mono=2.0))
        v = self.bs.view("PORT", 2.0)
        self.assertEqual(v.bid, 44.0)                 # the non-positive level is skipped
        self.assertEqual(self.bs.view("PORT", 2.0).ask, 46.0)

    def test_parse_server_ts(self) -> None:
        self.assertLess(parse_server_ts(T0), parse_server_ts(T1))
        self.assertEqual(parse_server_ts("2026-10-23T10:00:00Z"), parse_server_ts("2026-10-23T10:00:00"))
        self.assertIsNone(parse_server_ts(None))
        self.assertIsNone(parse_server_ts("not a time"))
        self.assertEqual(parse_server_ts(12.5), 12.5)


class FakeConfigMatchesDefault(unittest.TestCase):
    """The fake config's values must equal config/default.json (group A's verbatim copy of section 8)."""

    def test_values_match(self) -> None:
        path = os.path.join(BOT, "config", "default.json")
        if not os.path.exists(path):
            self.skipTest("config/default.json not written yet")
        with open(path, encoding="utf-8") as f:
            d = json.load(f)

        def walk(node: object, prefix: str) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{prefix}.{k}" if prefix else k)
                return
            cur: object = d
            for part in prefix.split("."):
                self.assertIn(part, cur, prefix)          # type: ignore[operator]
                cur = cur[part]                           # type: ignore[index]
            self.assertEqual(cur, node, prefix)

        walk(B_CONFIG, "")


if __name__ == "__main__":
    unittest.main()
