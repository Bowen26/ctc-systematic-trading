"""Group C: ETF lead-lag (DESIGN.md sections 6.6 and 9.1 C, etf_leadlag)."""
from __future__ import annotations

import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

from test_c_support import MIDS, FakeConfig, book, books, fv, make_ctx, make_deps, targets  # noqa: E402
from ctcbot.strategies.etf_leadlag import EtfLeadLag  # noqa: E402
from ctcbot.strategies.h1 import H1  # noqa: E402
from ctcbot.strategies.s3 import S3  # noqa: E402
from ctcbot.types import Command, Priority  # noqa: E402

NAV_ARCH = 45 + 55 + 2 * 27 + 36 + 2 * 19 + 40          # 268 with the default mids
NAV_REBLD = 2 * 52 + 2 * 36 + 40                        # 216


def bk(arch_mid: float, rebld_mid: float = NAV_REBLD, hs: float = 15.0) -> dict:
    return books(dict(MIDS), ARCH=book("ARCH", arch_mid, hs), REBLD=book("REBLD", rebld_mid, hs))


class LeadLagTest(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()
        self.cfg.set("strategies.etf_leadlag.enabled", True)
        self.ll = EtfLeadLag(make_deps(self.cfg))

    def step(self, t, arch_mid=NAV_ARCH, round_=1, positions=None, **kw):
        kw.setdefault("bk", bk(arch_mid))
        return targets(self.ll.decide(make_ctx(t, round_, now=1000.0 + t, cfg=self.cfg, positions=positions or {},
                                               **kw)))

    def test_off_by_default(self):
        ll = EtfLeadLag(make_deps(FakeConfig()))
        self.assertFalse(ll.enabled)
        self.assertEqual(ll.leadlag_symbols(), frozenset())
        self.assertEqual(ll.decide(make_ctx(100.0, 1, bk=bk(265.0))), [])
        self.assertEqual(self.ll.leadlag_symbols(), frozenset({"ARCH", "REBLD"}))

    def test_entry_cap_and_clip(self):
        self.assertEqual(self.step(100.0, arch_mid=NAV_ARCH), {})                   # no gap
        out = self.step(101.0, arch_mid=265.0)                                       # gap +1.13%
        it = out["ARCH"]
        gap = (NAV_ARCH - 265.0) / 265.0
        self.assertEqual(it.target, 50)
        self.assertEqual(it.clip_qty, 25)                                             # L/2
        self.assertEqual((it.clip_interval_s, it.ttl_s, it.priority), (1.0, 1.0, Priority.TAKE))
        self.assertEqual(it.limit_buy, int(265.0 * (1 + gap / 2) * 100 + 1e-6) / 100)
        self.assertIsNone(it.limit_sell)
        self.assertFalse(it.reduce_only)
        short = self.step(102.0, arch_mid=NAV_ARCH, bk=bk(NAV_ARCH, rebld_mid=219.0))["REBLD"]
        self.assertEqual(short.target, -100)
        self.assertEqual(short.clip_qty, 50)
        self.assertLess(short.limit_sell, 219.0)

    def test_threshold_and_not_through_touch(self):
        self.assertEqual(self.step(100.0, arch_mid=NAV_ARCH / 1.004), {})            # 0.4% < 0.5%
        cfg = FakeConfig()
        cfg.set("strategies.etf_leadlag.enabled", True)
        cfg.set("strategies.etf_leadlag.enter_hs_mult", 0.0)
        cfg.set("strategies.etf_leadlag.enter_gap_min", 0.001)
        ll = EtfLeadLag(make_deps(cfg))
        wide = bk(NAV_ARCH / 1.006, hs=100.0)                                         # cap mid*1.003 < ask
        self.assertEqual(targets(ll.decide(make_ctx(100.0, 1, cfg=cfg, bk=wide))), {})
        self.assertTrue(ll.log.kinds("leadlag_not_through"))

    def test_exit_on_gap_closed(self):
        self.step(100.0, arch_mid=265.0)
        out = self.step(101.0, arch_mid=NAV_ARCH / 1.0005, positions={"ARCH": 25})
        ex = out["ARCH"]
        self.assertEqual((ex.target, ex.priority), (0, Priority.HEDGE))
        self.assertTrue(ex.reduce_only)
        b = bk(NAV_ARCH / 1.0005)["ARCH"]
        self.assertEqual(ex.limit_sell, -(-b.bid * (1 - 50e-4) * 100 // 1) / 100)
        self.assertEqual(self.step(102.0, arch_mid=NAV_ARCH / 1.0005, positions={"ARCH": 0}), {})
        self.assertNotIn("ARCH", self.ll.state)
        self.assertIn("ARCH", self.step(103.0, arch_mid=265.0))                      # flat again: may re-enter

    def test_exit_on_hold_time(self):
        self.step(100.0, arch_mid=265.0)
        for t in (101.0, 104.0, 107.0):
            self.assertEqual(self.step(t, arch_mid=265.0, positions={"ARCH": 25})["ARCH"].target, 50)
        ex = self.step(108.0, arch_mid=265.0, positions={"ARCH": 50})["ARCH"]
        self.assertEqual(ex.target, 0)
        self.assertTrue(ex.reduce_only)

    def test_exit_on_sign_change(self):
        self.step(100.0, arch_mid=265.0)
        ex = self.step(101.0, arch_mid=271.0, positions={"ARCH": 25})["ARCH"]        # gap now -1.1%
        self.assertEqual(ex.target, 0)
        self.assertIsNotNone(ex.limit_sell)

    def test_cadence_window_and_flatten_guard(self):
        self.assertIn("ARCH", self.step(100.0, arch_mid=265.0))
        self.assertEqual(self.step(100.5, arch_mid=265.0), {})                       # decide every 1 s
        ll = EtfLeadLag(make_deps(self.cfg))
        self.assertEqual(targets(ll.decide(make_ctx(59.0, 1, cfg=self.cfg, bk=bk(265.0)))), {})
        ll = EtfLeadLag(make_deps(self.cfg))
        self.assertEqual(targets(ll.decide(make_ctx(550.0, 1, cfg=self.cfg, bk=bk(265.0)))), {})   # flatten window
        self.assertIn("ARCH", targets(ll.decide(make_ctx(550.0, 3, now=1.0, cfg=self.cfg, bk=bk(265.0)))))
        ll = EtfLeadLag(make_deps(self.cfg))
        ll.decide(make_ctx(737.0, 3, now=0.0, cfg=self.cfg, bk=bk(265.0)))
        ex = targets(ll.decide(make_ctx(738.0, 3, now=1.0, cfg=self.cfg, bk=bk(265.0), positions={"ARCH": 25})))
        self.assertEqual(ex["ARCH"].target, 0)                                        # window end exits
        ll2 = EtfLeadLag(make_deps(self.cfg))
        self.assertEqual(targets(ll2.decide(make_ctx(100.0, 1, cfg=self.cfg, bk=bk(265.0), frozen={"ARCH"}))), {})

    def test_removed_from_h1_and_s3_and_command(self):
        lag = self.ll.leadlag_symbols()
        h1 = H1(make_deps(self.cfg))
        h1.decide(make_ctx(48.0, 1, cfg=self.cfg, leadlag=lag))
        self.assertFalse({"ARCH", "REBLD"} & set(targets(h1.decide(make_ctx(50.0, 1, cfg=self.cfg, leadlag=lag)))))
        s3 = S3(make_deps(self.cfg))
        vals = dict(MIDS, ARCH=320.0, REBLD=200.0, PORT=50.0)
        out = targets(s3.decide(make_ctx(200.0, 1, cfg=self.cfg, fvs=fv(vals), leadlag=lag)))
        self.assertIn("PORT", out)
        self.assertFalse({"ARCH", "REBLD"} & set(out))
        ctx = make_ctx(100.0, 1, cfg=self.cfg)
        self.assertEqual(self.ll.on_command(Command("lag", ("off",)), ctx), [])
        self.assertEqual(self.ll.leadlag_symbols(), frozenset())
        self.assertIsNone(self.ll.on_command(Command("etf_leadlag", ("on",)), ctx))


if __name__ == "__main__":
    unittest.main()
