"""Group C: the S3 engine taker (DESIGN.md sections 6.2 and 9.1 C, s3)."""
from __future__ import annotations

import math
import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

from test_c_support import (MIDS, FakeConfig, book, books, feed_health, fv, health, make_ctx,  # noqa: E402
                            make_deps, targets, working)
from ctcbot.strategies.base import symbol_sane  # noqa: E402
from ctcbot.strategies.s3 import S3  # noqa: E402
from ctcbot.types import Command, Priority  # noqa: E402

PORT_BID, PORT_ASK = 44.93, 45.07          # 15 bp around 45.00 on the cent grid (test_c_support.book)
ARCH_BID, ARCH_ASK = 289.56, 290.44


def ceil2(x: float) -> float:
    return math.ceil(x * 100 - 1e-6) / 100


def floor2(x: float) -> float:
    return math.floor(x * 100 + 1e-6) / 100


class S3Test(unittest.TestCase):
    def setUp(self):
        # The rule tests below use S3 active from 80 s (storm_start + 20), the default. test_default_start_is_80s
        # pins the default (a 120 s start was tried on 24 Oct and reverted the same morning: DESIGN §14 item 4).
        self.cfg = FakeConfig(strategies__s3__start=["storm_start", 20])
        self.s3 = S3(make_deps(self.cfg))

    def ctx(self, t=200.0, round_=1, mu=None, **kw):
        vals = dict(MIDS)
        vals.update(mu or {})
        kw.setdefault("fvs", fv(vals, round_=round_))
        kw.setdefault("cfg", self.cfg)
        return make_ctx(t, round_, **kw)

    def decide(self, *a, **k):
        return targets(self.s3.decide(self.ctx(*a, **k)))

    # -------------------------------------------------------------- thresholds
    def test_touch_threshold_port_storm_round1(self):
        th = 0.1 * 1.0 + 123e-4 * 45.0                     # k_r12 sd + entry cost (storm) * mid = 0.6535
        self.assertNotIn("PORT", self.decide(mu={"PORT": PORT_ASK + th - 0.002}))
        it = self.decide(mu={"PORT": PORT_ASK + th + 0.002})["PORT"]
        self.assertEqual(it.target, 200)
        self.assertEqual(it.clip_qty, 16)
        self.assertEqual((it.clip_interval_s, it.ttl_s), (2.0, 2.0))
        self.assertEqual(it.priority, Priority.TAKE)
        self.assertFalse(it.reduce_only)
        self.assertIsNone(it.limit_sell)
        self.assertAlmostEqual(it.edge_usd_per_share, th + 0.002, places=9)
        self.assertAlmostEqual(it.ref_mid, 45.0)
        # limit: min(mu - k sd - cost mid, ask * 1.005, mid * 1.015), floored to cents
        self.assertEqual(it.limit_buy, 45.07)
        self.assertGreaterEqual(it.limit_buy, PORT_ASK)

    def test_touch_threshold_round3_and_s3_start_phase(self):
        th3 = 0.2 + 123e-4 * 45.0
        self.assertNotIn("PORT", self.decide(round_=3, mu={"PORT": PORT_ASK + th3 - 0.002}))
        self.assertIn("PORT", self.decide(round_=3, mu={"PORT": PORT_ASK + th3 + 0.002}))
        th_s = 0.1 + 315e-4 * 45.0                          # 80-120 s: s3_start costs
        self.assertNotIn("PORT", self.decide(t=100.0, mu={"PORT": PORT_ASK + th_s - 0.002}))
        self.assertIn("PORT", self.decide(t=100.0, mu={"PORT": PORT_ASK + th_s + 0.002}))

    def test_default_start_is_80s(self):
        """Default: S3 starts 20 s after formation (80 s). A 120 s start was tried on 24 Oct and reverted: over
        10 recorded testnet seasons it was a wash and on 200 research seasons slightly worse (DESIGN §14)."""
        cfg = FakeConfig()
        self.assertEqual(cfg.get("strategies.s3.start"), ["storm_start", 20])
        s3 = S3(make_deps(cfg))
        big = {"PORT": PORT_ASK + 5.0}
        self.assertNotIn("PORT", targets(s3.decide(self.ctx(t=75.0, mu=big, cfg=cfg))))
        self.assertIn("PORT", targets(s3.decide(self.ctx(t=85.0, mu=big, cfg=cfg))))

    def test_touch_short_arch(self):
        th = 0.1 + 73e-4 * 290.0
        self.assertNotIn("ARCH", self.decide(mu={"ARCH": ARCH_BID - th + 0.01}))
        it = self.decide(mu={"ARCH": ARCH_BID - th - 0.01})["ARCH"]
        self.assertEqual(it.target, -50)
        self.assertEqual(it.clip_qty, 4)
        self.assertIsNone(it.limit_buy)
        mu = ARCH_BID - th - 0.01
        want = max(mu + 0.1 + 73e-4 * 290.0, ARCH_BID * 0.995, 290.0 * 0.985)
        self.assertAlmostEqual(it.limit_sell, ceil2(want), places=9)
        self.assertLessEqual(it.limit_sell, ARCH_BID)

    def test_mid_rule_threshold_and_band(self):
        cfg = FakeConfig(strategies__s3__start=["storm_start", 20])   # decides at 100 s (see setUp)
        cfg.set("strategies.s3.edge_ref", "mid")
        cfg.set("strategies.s3.limit_cost_frac", 0.5)
        s3 = S3(make_deps(cfg))
        th = 0.1 + 123e-4 * 45.0
        dec = lambda **k: targets(s3.decide(make_ctx(200.0, 1, cfg=cfg, **k)))  # noqa: E731
        self.assertNotIn("PORT", dec(fvs=fv({"PORT": 45.0 + th - 0.002})))
        it = dec(fvs=fv({"PORT": 45.0 + th + 0.002}))["PORT"]
        self.assertEqual(it.target, 200)
        self.assertAlmostEqual(it.edge_usd_per_share, th + 0.002, places=9)
        mu = 45.0 + th + 0.002
        self.assertEqual(it.limit_buy, floor2(min(mu - 0.1 - 0.5 * 123e-4 * 45.0, PORT_ASK * 1.005)))
        self.assertGreaterEqual(it.limit_buy, PORT_ASK)
        band = 24.2e-4 * 45.0                              # exit band, storm
        pos = {"PORT": 100}
        self.assertNotIn("PORT", dec(fvs=fv({"PORT": 45.0 - band + 0.005}), positions=pos))
        ex = dec(fvs=fv({"PORT": 45.0 - band - 0.005}), positions=pos)["PORT"]
        self.assertEqual(ex.target, 0)
        self.assertTrue(ex.reduce_only)
        # ARCH short: band 46.2 bp at 80-120 s, but only 19.1 bp in the storm
        band_a = 46.2e-4 * 290.0
        self.assertEqual(dec(fvs=fv({"ARCH": 290.0 + band_a - 0.01}), positions={"ARCH": -20})["ARCH"].target, 0)
        self.assertNotIn("ARCH", targets(s3.decide(make_ctx(100.0, 1, cfg=cfg, fvs=fv({"ARCH": 290.0 + band_a - 0.01}),
                                                            positions={"ARCH": -20}))))
        ex = targets(s3.decide(make_ctx(100.0, 1, cfg=cfg, fvs=fv({"ARCH": 290.0 + band_a + 0.01}),
                                        positions={"ARCH": -20})))["ARCH"]
        self.assertEqual(ex.target, 0)

    # -------------------------------------------------------------- zones and prices
    def test_exit_long_only_below_bid(self):
        pos = {"PORT": 100}
        self.assertNotIn("PORT", self.decide(mu={"PORT": PORT_BID + 0.01}, positions=pos))   # between bid and mid
        ex = self.decide(mu={"PORT": 44.83}, positions=pos)["PORT"]
        self.assertEqual(ex.target, 0)
        self.assertTrue(ex.reduce_only)
        self.assertIsNone(ex.limit_buy)
        self.assertEqual(ex.limit_sell, 44.83)                # max(mu, bid*0.995, mid*0.985), ceiled
        ex2 = self.decide(mu={"PORT": 44.7}, positions=pos)["PORT"]
        self.assertEqual(ex2.limit_sell, ceil2(PORT_BID * 0.995))   # bid - 50 bp binds
        ex3 = self.decide(mu={"PORT": 44.905}, positions=pos)["PORT"]
        self.assertGreaterEqual(ex3.limit_sell, 44.905)        # never sells below fair
        sh = self.decide(mu={"PORT": 45.17}, positions={"PORT": -50})["PORT"]
        self.assertEqual(sh.target, 0)
        self.assertEqual(sh.limit_buy, 45.17)                  # min(mu, ask*1.005, mid*1.015), floored

    def test_hold_at_limit_and_inherited_long(self):
        self.assertNotIn("PORT", self.decide(mu={"PORT": 47.0}, positions={"PORT": 200}))
        self.assertNotIn("PORT", self.decide(mu={"PORT": 47.0}, work={"PORT": working("PORT", buy=16)},
                                             positions={"PORT": 184}))
        # an H1 long (from 50-56 s) is held from 80 s until the exit condition
        h1_pos = {"PORT": 100, "RECON": -125}
        out = self.decide(t=80.0, mu={"PORT": 45.01, "RECON": 52.0}, positions=h1_pos)
        self.assertNotIn("PORT", out)
        self.assertNotIn("RECON", out)
        out = self.decide(t=81.0, mu={"PORT": 44.83}, positions=h1_pos)
        self.assertEqual(out["PORT"].target, 0)
        flip = self.decide(mu={"PORT": 43.0}, positions=h1_pos)["PORT"]
        self.assertEqual(flip.target, -200)                    # a strong short signal flips the inherited long

    def test_caps(self):
        it = self.decide(mu={"PORT": 50.0})["PORT"]
        self.assertEqual(it.limit_buy, 45.29)                  # ask * 1.005 binds
        cfg = FakeConfig()
        cfg.set("strategies.s3.through_touch_bp", None)
        it = targets(S3(make_deps(cfg)).decide(make_ctx(200.0, 1, cfg=cfg, fvs=fv({"PORT": 50.0}))))["PORT"]
        self.assertEqual(it.limit_buy, 45.67)                  # mid * 1.015 binds
        cfg.set("strategies.s3.max_from_mid_bp", None)
        it = targets(S3(make_deps(cfg)).decide(make_ctx(200.0, 1, cfg=cfg, fvs=fv({"PORT": 50.0}))))["PORT"]
        self.assertEqual(it.limit_buy, 49.34)                  # mu - k sd - cost mid

    def test_require_marketable(self):
        cfg = FakeConfig()
        cfg.set("strategies.s3.max_from_mid_bp", 1.0)          # mid * 1.0001 is inside the ask
        s3 = S3(make_deps(cfg))
        self.assertNotIn("PORT", targets(s3.decide(make_ctx(200.0, 1, cfg=cfg, fvs=fv({"PORT": 50.0})))))
        self.assertTrue(s3.log.kinds("s3_not_marketable"))
        cfg.set("strategies.s3.require_marketable", False)
        it = targets(S3(make_deps(cfg)).decide(make_ctx(200.0, 1, cfg=cfg, fvs=fv({"PORT": 50.0}))))["PORT"]
        self.assertEqual(it.limit_buy, 45.0)

    def test_cost_source_model_and_costmult(self):
        cfg = FakeConfig()
        cfg.set("strategies.s3.cost_source", "model")
        s3 = S3(make_deps(cfg))                                # FakeCost: 2 * (20 + 36) = 112 bp
        th = 0.1 + 112e-4 * 45.0
        self.assertNotIn("PORT", targets(s3.decide(make_ctx(200.0, 1, cfg=cfg, fvs=fv({"PORT": PORT_ASK + th - 0.002})))))
        self.assertIn("PORT", targets(s3.decide(make_ctx(200.0, 1, cfg=cfg, fvs=fv({"PORT": PORT_ASK + th + 0.002})))))
        ctx = make_ctx(200.0, 1, cfg=cfg)
        self.assertEqual(s3.on_command(Command("s3", ("costmult", "2")), ctx), [])
        self.assertNotIn("PORT", targets(s3.decide(make_ctx(200.0, 1, cfg=cfg, fvs=fv({"PORT": PORT_ASK + th + 0.002})))))

    # -------------------------------------------------------------- skips
    def test_window_rounds_and_commands(self):
        mu = {"PORT": 50.0}
        self.assertEqual(self.decide(t=79.9, mu=mu), {})
        self.assertEqual(self.decide(t=540.0, mu=mu), {})
        self.assertIn("PORT", self.decide(t=539.9, mu=mu))
        ctx = self.ctx()
        self.assertEqual(self.s3.on_command(Command("s3", ("rounds", "3")), ctx), [])
        self.assertEqual(self.decide(round_=1, mu=mu), {})
        self.assertIn("PORT", self.decide(round_=3, mu=mu))
        self.s3.on_command(Command("s3", ("off",)), ctx)
        self.assertEqual(self.decide(round_=3, mu=mu), {})
        self.assertIsNone(self.s3.on_command(Command("h1", ("off",)), ctx))
        self.s3.on_command(Command("s3", ("ON",)), ctx)
        self.assertTrue(self.s3.enabled)
        st = self.s3.state_dict()
        other = S3(make_deps(self.cfg))
        other.load_state(st)
        self.assertEqual(other.rounds, [3])

    def test_stale_or_unusable_engine(self):
        mu = {"PORT": 50.0}
        self.assertEqual(self.decide(mu=mu, engine=health(oldest=7.0)), {})
        # hardening: S3 has its own 2-s freshness gate (strategies.s3.max_engine_age_s)
        self.assertEqual(self.decide(mu=mu, engine=health(oldest=2.5)), {})
        self.assertIn("PORT", self.decide(mu=mu, engine=health(oldest=1.5)))
        self.assertEqual(self.decide(mu=mu, feed=feed_health(obs_age=6.5)), {})
        self.assertEqual(self.decide(mu=mu, feed=feed_health(obs_age=None)), {})
        self.assertEqual(self.decide(fvs=fv({"PORT": 50.0}, ok=False)), {})
        self.assertEqual(self.decide(fvs=fv({"PORT": 50.0}, round_=0)), {})       # fv of another round
        self.assertEqual(self.decide(fvs=fv({"PORT": 50.0}, epoch=1)), {})
        self.assertEqual(self.decide(fvs=None), {})

    def test_symbol_skips(self):
        mu = {"PORT": 50.0, "REIN": 99.0, "ARCH": 300.0, "GRID": 40.0}
        out = self.decide(mu=mu)
        self.assertIn("PORT", out)
        self.assertNotIn("REIN", out)                           # excluded
        self.assertIn("ARCH", out)
        out = self.decide(mu=mu, leadlag={"ARCH", "REBLD"}, frozen={"PORT"}, disabled={"GRID"})
        self.assertEqual(set(out), set())
        out = self.decide(mu=mu, bk=books(PORT=book("PORT", None), ARCH=book("ARCH", 290.0, hs_bp=400.0)))
        self.assertNotIn("PORT", out)                           # empty book
        self.assertNotIn("ARCH", out)                           # wall-only book
        self.assertIn("GRID", out)

    def test_insane_values_and_stickiness(self):
        mu = {"PORT": 45.0 + 7.0}                                # > max(6 sd, 15% mid = 6.75)
        self.assertNotIn("PORT", self.decide(now=200.0, mu=mu))
        self.assertTrue(self.s3.log.kinds("engine_insane"))
        self.assertNotIn("PORT", self.decide(now=205.0, mu={"PORT": 50.0}))   # sticky for 10 s
        self.assertIn("PORT", self.decide(now=210.5, mu={"PORT": 50.0}))
        ctx = self.ctx(mu={"PORT": float("nan")})
        self.assertFalse(symbol_sane(ctx, "PORT", self.cfg))
        self.assertTrue(symbol_sane(self.ctx(mu={"PORT": 50.0}), "PORT", self.cfg))


if __name__ == "__main__":
    unittest.main()
