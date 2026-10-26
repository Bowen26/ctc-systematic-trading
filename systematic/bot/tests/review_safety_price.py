"""Safety review (adversarial): price protection in ``RiskManager.check``.

Not part of the default suite (``discover`` only picks up ``test*.py``). Run from BOT with:

    SYS/.venv/bin/python -m unittest discover -s tests -p "review_safety_price.py" -v

* ``test_opening_and_marketable_cap_hold``   - positive control: the 3-s opening block, the 1.5 % marketable cap
                                               for TAKE/HEDGE and the 25 % / 60 % bands are enforced (passes).
* ``test_cap_does_not_depend_on_touch``      - a TAKE buy 1.8 % above mid passes when a wide (but not wall-only)
                                               touch makes it look passive: the 1.5 % cap applies only to orders
                                               that are marketable against the bot's current view.
* ``test_stale_book_is_refused``             - the view's ``age_s`` is never checked: a 9-s-old book (the feed
                                               counts as up until 10 s) still defines mid and marketability.
                                               (Hardened variant: see the test's docstring.)
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

from test_b_support import BTestCase, Rig, clock, feed  # noqa: E402

from ctcbot.types import OrderRecord, Priority, Side  # noqa: E402


def _rec(side: Side, px: float, prio: Priority = Priority.TAKE, qty: int = 10, sym: str = "PORT") -> OrderRecord:
    return OrderRecord(local_id="c", strategy="s3", key=None, style="clip", symbol=sym, side=side, qty=qty,
                       price=px, priority=prio)


class TestPrice(BTestCase):
    def test_opening_and_marketable_cap_hold(self):
        rig = Rig(self.log)
        rig.set_book("PORT", 49.95, 50.05, 0.0)
        bv = rig.books.view("PORT", 0.0)
        ck = rig.risk.check
        self.assertEqual(ck(_rec(Side.BUY, 50.10), bv, 0, 200, clock(2.9), feed(), 0.0)[1], "opening")
        self.assertEqual(ck(_rec(Side.BUY, 50.10), bv, 0, 200, clock(3.0), feed(), 0.0)[1], "ok")
        self.assertEqual(ck(_rec(Side.BUY, 50.80), bv, 0, 200, clock(30.0), feed(), 0.0)[1], "marketable_far")
        self.assertEqual(ck(_rec(Side.SELL, 49.20, Priority.HEDGE), bv, 0, 200, clock(30.0), feed(), 0.0)[1],
                         "marketable_far")
        self.assertEqual(ck(_rec(Side.BUY, 37.0), bv, 0, 200, clock(30.0), feed(), 0.0)[1], "fat_finger")
        self.assertEqual(ck(_rec(Side.BUY, 79.0, Priority.SNIPE), bv, 0, 200, clock(30.0), feed(), 0.0)[1],
                         "ok")                          # SNIPE: marketable up to 60 % from mid, by design

    def test_cap_does_not_depend_on_touch(self):
        rig = Rig(self.log)
        rig.set_book("PORT", 49.0, 51.0, 0.0)          # half-spread 200 bp: not wall-only (300 bp)
        bv = rig.books.view("PORT", 0.0)
        q, why = rig.risk.check(_rec(Side.BUY, 50.90), bv, 0, 200, clock(30.0), feed(), 0.0)
        self.assertEqual(q, 0, f"TAKE buy at mid + 1.8 % approved ({why}): the 1.5 % cap is skipped because the "
                               f"touch says it is not marketable")

    def test_stale_book_is_refused(self):
        """Hardened variant of the reviewed test. A symbol's book age is not a staleness signal: on the testnet
        recording H1's entry books are p50 5.2 s / p90 19 s old at order time (the pre-storm book simply does not
        change), and a 3-s/8-s book-age rule would have refused 33 approved H1 entries and 24 S3 clips. The data
        that can go stale is the feed, so the rule keys on it: with no frame on any connection within the watchdog
        (``FeedHealth.up`` False, before the 10-s feed-down hold) a marketable or aggressive TAKE/HEDGE order may
        only reduce."""
        rig = Rig(self.log)
        rig.set_book("PORT", 49.95, 50.05, 0.0)
        now = 9.0                                       # nine seconds without a PORT book or quote
        bv = rig.books.view("PORT", now)
        q, why = rig.risk.check(_rec(Side.BUY, 50.70), bv, 0, 200, clock(600.0), feed(up=False), now)
        self.assertEqual((q, why), (0, "feed_stale"), f"marketable TAKE buy on a silent feed approved ({why})")
        q, why = rig.risk.check(_rec(Side.BUY, 50.70), bv, -30, 200, clock(600.0), feed(up=False), now)
        self.assertEqual(q, 10, why)                    # reducing a short is still allowed
        q, why = rig.risk.check(_rec(Side.BUY, 50.70), bv, 0, 200, clock(600.0), feed(), now)
        self.assertEqual(q, 10, why)                    # a quiet book on a live feed is a valid book


if __name__ == "__main__":
    unittest.main()
