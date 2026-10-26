"""Group B: PositionBook (fills, cash, infer_price, MTM, the quiet-symbol guard)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_b_support import BTestCase, prow  # noqa: E402

from ctcbot.state.positions import PositionBook  # noqa: E402
from ctcbot.types import Fill, PositionRow, PriceQuality, Side  # noqa: E402


def fill(sym: str, side: Side, qty: int, px: float, strategy: str = "s3") -> Fill:
    return Fill(local_id="e1-1", order_id="o1", strategy=strategy, key=None, symbol=sym, side=side, qty=qty, price=px,
                quality=PriceQuality.EXACT, mono=0.0, ref_mid=None, expected_cost_bp=None, source="ack")


class FillTests(BTestCase):
    def test_fills_move_position_and_cash(self) -> None:
        pb = PositionBook(("PORT", "ARCH"))
        pb.apply_fill(fill("PORT", Side.BUY, 10, 45.0))
        pb.apply_fill(fill("PORT", Side.SELL, 4, 46.0))
        pb.apply_fill(fill("ARCH", Side.SELL, 3, 100.0))
        self.assertEqual(pb.pos, {"PORT": 6, "ARCH": -3})
        self.assertAlmostEqual(pb.cash, -450.0 + 184.0 + 300.0)
        self.assertEqual(len(pb.fills), 3)
        pb.apply_fill(fill("PORT", Side.BUY, 5, 44.0), update_pos=False)    # cash only
        self.assertEqual(pb.pos["PORT"], 6)
        self.assertAlmostEqual(pb.cash, 34.0 - 220.0)
        pb.apply_fill(fill("PORT", Side.BUY, 0, 44.0))                       # zero qty ignored
        self.assertEqual(len(pb.fills), 4)

    def test_mtm_uses_last_mid_when_missing(self) -> None:
        pb = PositionBook(("PORT", "ARCH"))
        pb.apply_fill(fill("PORT", Side.BUY, 10, 45.0))
        pb.apply_fill(fill("ARCH", Side.SELL, 2, 100.0))
        self.assertAlmostEqual(pb.mtm({"PORT": 46.0, "ARCH": 99.0}), -450 + 200 + 460 - 198)
        self.assertAlmostEqual(pb.mtm({"PORT": None, "ARCH": None}), -450 + 200 + 460 - 198)
        pb2 = PositionBook(("PORT",))
        pb2.apply_fill(fill("PORT", Side.BUY, 10, 45.0))
        self.assertAlmostEqual(pb2.mtm({}), 0.0)          # no mid ever: the last fill price marks it

    def test_pnl_by_strategy(self) -> None:
        pb = PositionBook(("PORT",))
        pb.apply_fill(fill("PORT", Side.BUY, 10, 45.0, "h1"))
        pb.apply_fill(fill("PORT", Side.SELL, 10, 47.0, "s3"))
        out = pb.pnl_by_strategy({"PORT": 46.0})
        self.assertAlmostEqual(out["h1"], 10.0)
        self.assertAlmostEqual(out["s3"], 10.0)

    def test_reset(self) -> None:
        pb = PositionBook(("PORT",))
        pb.apply_fill(fill("PORT", Side.BUY, 10, 45.0))
        pb.reset()
        self.assertEqual((pb.pos, pb.cash, pb.fills), ({"PORT": 0}, 0.0, []))
        self.assertIsNone(pb.exchange_pnl())


class InferPriceTests(BTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.pb = PositionBook(("PORT",))

    def test_growth_long_and_short(self) -> None:
        self.assertAlmostEqual(self.pb.infer_price("PORT", prow("PORT", 10, 50.0), prow("PORT", 20, 51.0),
                                                   Side.BUY, 10), 52.0)
        self.assertAlmostEqual(self.pb.infer_price("PORT", prow("PORT", -10, 50.0), prow("PORT", -20, 45.0),
                                                   Side.SELL, 10), 40.0)
        # from flat (no old row): the new average is the fill price
        self.assertAlmostEqual(self.pb.infer_price("PORT", None, prow("PORT", 5, 44.5), Side.BUY, 5), 44.5)

    def test_shrink_uses_realized(self) -> None:
        old = prow("PORT", 20, 50.0, realized=100.0)
        new = prow("PORT", 10, 50.0, realized=130.0)       # sold 10 at 53
        self.assertAlmostEqual(self.pb.infer_price("PORT", old, new, Side.SELL, 10), 53.0)
        old = prow("PORT", -20, 50.0, realized=0.0)
        new = prow("PORT", -5, 50.0, realized=60.0)        # bought 15 at 46
        self.assertAlmostEqual(self.pb.infer_price("PORT", old, new, Side.BUY, 15), 46.0)
        self.assertAlmostEqual(self.pb.infer_price("PORT", prow("PORT", 10, 50.0, realized=0.0),
                                                   prow("PORT", 0, None, realized=-20.0), Side.SELL, 10), 48.0)

    def test_none_cases(self) -> None:
        # sign change
        self.assertIsNone(self.pb.infer_price("PORT", prow("PORT", 10, 50.0, 0.0), prow("PORT", -5, 49.0, 5.0),
                                              Side.SELL, 15))
        # missing average or realized
        self.assertIsNone(self.pb.infer_price("PORT", prow("PORT", 10, None), prow("PORT", 20, 51.0), Side.BUY, 10))
        self.assertIsNone(self.pb.infer_price("PORT", prow("PORT", 20, 50.0), prow("PORT", 10, 50.0), Side.SELL, 10))
        # side inconsistent with the change, no change, bad qty
        self.assertIsNone(self.pb.infer_price("PORT", prow("PORT", 10, 50.0), prow("PORT", 20, 51.0), Side.SELL, 10))
        self.assertIsNone(self.pb.infer_price("PORT", prow("PORT", 10, 50.0), prow("PORT", 10, 50.0), Side.BUY, 1))
        self.assertIsNone(self.pb.infer_price("PORT", prow("PORT", 10, 50.0), prow("PORT", 20, 51.0), Side.BUY, 0))


class GuardTests(BTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.pb = PositionBook(("PORT", "ISLE", "AQUA"))

    def test_quiet_symbols_apply_and_report_deltas(self) -> None:
        stamp = self.pb.quiet_stamp()
        deltas, skipped = self.pb.set_exchange({"PORT": prow("PORT", 7), "ISLE": prow("ISLE", -3)}, stamp, 1.0)
        self.assertEqual(skipped, frozenset())
        self.assertEqual(deltas, {"PORT": 7, "ISLE": -3, "AQUA": 0})   # AQUA omitted = flat
        self.assertEqual(self.pb.pos, {"PORT": 7, "ISLE": -3, "AQUA": 0})
        self.assertIn("PORT", self.pb.last_rows())

    def test_place_in_flight_at_stamp_is_skipped(self) -> None:
        self.pb.note_place("PORT", sent=True)
        stamp = self.pb.quiet_stamp()
        self.pb.apply_fill(fill("PORT", Side.BUY, 5, 45.0))                       # internal +5 from an ack
        deltas, skipped = self.pb.set_exchange({"PORT": prow("PORT", 0), "ISLE": prow("ISLE", 2)}, stamp, 1.0)
        self.assertIn("PORT", skipped)
        self.assertNotIn("PORT", deltas)
        self.assertEqual(self.pb.pos["PORT"], 5)          # the stale row did not erase the ack fill
        self.assertEqual(self.pb.pos["ISLE"], 2)

    def test_counter_moved_after_stamp_is_skipped(self) -> None:
        stamp = self.pb.quiet_stamp()
        self.pb.note_place("PORT", sent=True)              # a PLACE sent after the stamp
        self.pb.note_place("PORT", sent=False)             # ... and answered before the cycle ended
        _, skipped = self.pb.set_exchange({"PORT": prow("PORT", 9)}, stamp, 1.0)
        self.assertIn("PORT", skipped)
        self.assertEqual(self.pb.pos["PORT"], 0)
        # an ack received after the stamp (PLACE sent before it) also moves the counter
        self.pb.note_place("ISLE", sent=True)
        stamp = self.pb.quiet_stamp()
        self.pb.note_place("ISLE", sent=False)
        _, skipped = self.pb.set_exchange({"ISLE": prow("ISLE", 4)}, stamp, 2.0)
        self.assertIn("ISLE", skipped)
        # next cycle with nothing happening applies it
        stamp = self.pb.quiet_stamp()
        deltas, skipped = self.pb.set_exchange({"ISLE": prow("ISLE", 4)}, stamp, 2.5)
        self.assertNotIn("ISLE", skipped)
        self.assertEqual(deltas["ISLE"], 4)

    def test_skipped_since_tracks_continuous_skips(self) -> None:
        self.pb.note_place("PORT", sent=True)
        self.pb.set_exchange({}, self.pb.quiet_stamp(), 1.0)
        self.pb.set_exchange({}, self.pb.quiet_stamp(), 3.0)
        self.assertEqual(self.pb.skipped_since(), {"PORT": 1.0})
        self.pb.note_place("PORT", sent=False)
        st = self.pb.quiet_stamp()
        self.pb.set_exchange({}, st, 4.0)
        self.assertEqual(self.pb.skipped_since(), {})

    def test_exchange_pnl(self) -> None:
        self.assertIsNone(self.pb.exchange_pnl())
        rows = {"PORT": PositionRow("PORT", 5, 45.0, 46.0, 10.0, 5.0), "ISLE": PositionRow("ISLE", 0, None, None, -3.0, 0.0)}
        self.pb.set_exchange(rows, self.pb.quiet_stamp(), 1.0)
        self.assertAlmostEqual(self.pb.exchange_pnl(), 12.0)
        rows["AQUA"] = PositionRow("AQUA", 1, 10.0, 10.0, None, 0.0)
        self.pb.set_exchange(rows, self.pb.quiet_stamp(), 2.0)
        self.assertIsNone(self.pb.exchange_pnl())
        rows["AQUA"] = PositionRow("AQUA", 0, None, None, 2.0, 0.0)
        self.pb.set_exchange(rows, self.pb.quiet_stamp(), 2.5)
        self.assertAlmostEqual(self.pb.exchange_pnl(), 14.0)
        # a row that disappears (the exchange may omit flat symbols) takes its realized P&L with it: no sum
        self.pb.set_exchange({k: v for k, v in rows.items() if k != "ISLE"}, self.pb.quiet_stamp(), 3.0)
        self.assertIsNone(self.pb.exchange_pnl())
        self.pb.reset()                                                 # a new season starts with no rows
        self.pb.set_exchange({}, self.pb.quiet_stamp(), 4.0)
        self.assertEqual(self.pb.exchange_pnl(), 0.0)


if __name__ == "__main__":
    unittest.main()
