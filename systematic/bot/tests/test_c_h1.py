"""Group C: the H1 formation leg (DESIGN.md sections 6.1 and 9.1 C, h1)."""
from __future__ import annotations

import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

from test_c_support import (MIDS, FakeConfig, books, fill, fv, make_ctx, make_deps, targets)  # noqa: E402
from ctcbot.strategies.h1 import H1  # noqa: E402
from ctcbot.types import Command, Priority, PriceQuality, Side  # noqa: E402

REBUILDERS = {"RECON", "MAT", "GEN", "REBLD"}


class H1Test(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()
        self.h1 = H1(make_deps(self.cfg))

    def step(self, t, round_=1, mids=None, **kw):
        m = dict(MIDS)
        m.update(mids or {})
        kw.setdefault("bk", books(m))
        kw.setdefault("fvs", fv(round_=round_))
        return targets(self.h1.decide(make_ctx(t, round_, cfg=self.cfg, now=t + 1000.0 * round_, **kw)))

    def run_round1(self, fills=(), move=0.02, extra_mids=None):
        """Round 1 from 48 s to 90 s; check symbols move by ``move`` between 58 and 90 s."""
        for t in (48.0, 50.0, 52.0, 54.0, 56.0):
            self.step(t, mids=extra_mids)
        self.step(58.0)
        self.step(70.0, fills=tuple(fills))
        moved = {s: MIDS[s] * (1 + move * self.cfg.sym(s)["h1_dir"]) for s in ("HAVEN", "PORT", "ARCH")}
        self.step(90.0, mids=moved)

    # -------------------------------------------------------------- entry
    def test_entry_clips_targets_and_caps(self):
        self.assertEqual(self.step(47.9), {})
        self.assertEqual(self.step(48.0), {})                   # the reference time records mids only
        for t in (50.0, 52.0, 54.0, 56.0, 56.9):
            out = self.step(t, mids={"PORT": 46.0} if t >= 52 else None)
            self.assertEqual(set(out), set(self.cfg.get("strategies.h1.symbols")), t)
            port, recon, arch = out["PORT"], out["RECON"], out["ARCH"]
            self.assertEqual((port.target, port.clip_qty), (100, 50))
            self.assertEqual((recon.target, recon.clip_qty), (-250, 125))
            self.assertEqual((arch.target, arch.clip_qty), (25, 12))
            self.assertEqual(port.priority, Priority.TAKE)
            self.assertEqual((port.clip_interval_s, port.ttl_s), (2.0, 2.0))
            self.assertIsNone(port.limit_sell)
            self.assertIsNone(recon.limit_buy)
            self.assertEqual(port.limit_buy, 45.22)                 # 48-s mid 45 * 1.005 binds (mid moved to 46)
            self.assertEqual(recon.limit_sell, 51.74)               # 52 * 0.995
            self.assertFalse(port.reduce_only)
        self.assertEqual(self.step(57.0), {})
        for t in (58.0, 62.0, 72.0, 80.0, 120.0):
            self.assertEqual(self.step(t), {}, t)                   # no forced exit

    def test_max_from_mid_and_null_cap(self):
        self.step(48.0)
        out = self.step(50.0, mids={"PORT": 44.0})
        self.assertEqual(out["PORT"].limit_buy, 44.66)              # mid 44 * 1.015 is below the 48-s cap
        cfg = FakeConfig()
        cfg.set("strategies.h1.cap_bp", None)
        h1 = H1(make_deps(cfg))
        h1.decide(make_ctx(48.0, 1, cfg=cfg))
        out = targets(h1.decide(make_ctx(50.0, 1, cfg=cfg, bk=books(dict(MIDS, PORT=46.0)))))
        self.assertEqual(out["PORT"].limit_buy, 46.69)

    def test_skips_clock_quality_blocked_and_spread(self):
        self.step(48.0)
        self.assertEqual(self.step(50.0, quality="extrapolated"), {})
        self.assertTrue(self.h1.log.kinds("h1_skip"))
        out = self.step(50.0, frozen={"PORT"}, disabled={"GRID"}, leadlag={"ARCH", "REBLD"})
        self.assertFalse({"PORT", "GRID", "ARCH", "REBLD"} & set(out))
        self.assertIn("HAVEN", out)
        h1 = H1(make_deps(self.cfg))
        wide = books(MIDS, hs_bp=120.0)
        h1.decide(make_ctx(48.0, 1, cfg=self.cfg, bk=wide))
        self.assertEqual(h1.size, 0.0)
        self.assertEqual(targets(h1.decide(make_ctx(50.0, 1, cfg=self.cfg, bk=wide))), {})

    def test_late_start_uses_current_mid(self):
        out = self.step(53.0, mids={"PORT": 45.5})
        self.assertEqual(out["PORT"].limit_buy, 45.72)             # 48-s reference taken at the first decision

    # -------------------------------------------------------------- sizing from the round-1 check
    def test_check_pass_sizes_rounds_2_3(self):
        fills = [fill("PORT", Side.BUY, 50, 45.05, ref_mid=45.0, expected=20.0),
                 fill("HAVEN", Side.BUY, 62, 40.04, ref_mid=40.0, expected=20.0)]
        self.run_round1(fills=fills)
        self.assertTrue(self.h1.check_result)
        rec = self.h1.log.kinds("h1_check")[-1]
        self.assertTrue(rec["passed"])
        self.assertAlmostEqual(rec["move_bp"], 200.0, places=6)
        self.assertFalse(self.h1.drop_rebuilders)
        self.step(48.0, round_=2)
        self.assertEqual(self.step(50.0, round_=2)["PORT"].target, 200)
        self.step(48.0, round_=3)
        self.assertEqual(self.step(50.0, round_=3)["RECON"].target, -500)

    def test_check_fail_cases(self):
        self.run_round1(fills=())                                   # zero fills is a fail
        self.assertIs(self.h1.check_result, False)
        self.assertEqual(self.h1.log.kinds("h1_check")[-1]["n_fills"], 0)
        self.step(48.0, round_=2)
        self.assertEqual(self.step(50.0, round_=2)["PORT"].target, 100)
        h1 = H1(make_deps(self.cfg))
        self.h1 = h1
        self.run_round1(fills=[fill("PORT", Side.BUY, 50, 46.0, ref_mid=45.0)])   # cost 222 bp > move/3
        self.assertIs(h1.check_result, False)
        h1 = H1(make_deps(self.cfg))
        self.h1 = h1
        self.run_round1(fills=[fill("PORT", Side.BUY, 50, 45.01, ref_mid=45.0)], move=-0.02)   # wrong-way move
        self.assertIs(h1.check_result, False)

    def test_limit_bound_fills_are_used_only_without_better(self):
        fills = [fill("PORT", Side.BUY, 50, 45.9, quality=PriceQuality.LIMIT_BOUND, ref_mid=45.0),
                 fill("ISLE", Side.BUY, 75, 55.02, ref_mid=55.0, quality=PriceQuality.INFERRED)]
        self.run_round1(fills=fills)
        rec = self.h1.log.kinds("h1_check")[-1]
        self.assertEqual(rec["n_priced"], 1)
        self.assertTrue(rec["passed"])
        self.assertFalse(rec["all_fills_used"])

    def test_drop_rebuilders_on_cost_ratio(self):
        # H1 fills carry walk + fo_allow (DESIGN.md §12 item 1): walk 20 + 36; the ratio uses the walk alone
        fills = [fill("PORT", Side.BUY, 50, 45.18, ref_mid=45.0, expected=56.0),       # 40 bp vs 20 table
                 fill("RECON", Side.SELL, 125, 51.792, ref_mid=52.0, expected=56.0)]  # 40 bp
        self.run_round1(fills=fills, move=0.05)
        self.assertGreaterEqual(self.h1.cost_ratio, 1.5)
        self.assertTrue(self.h1.drop_rebuilders)
        self.step(48.0, round_=2)
        out = self.step(50.0, round_=2)
        self.assertFalse(REBUILDERS & set(out))
        self.assertIn("PORT", out)
        h1b = H1(make_deps(self.cfg))
        self.h1 = h1b
        self.run_round1(fills=[fill("PORT", Side.BUY, 50, 45.05, ref_mid=45.0, expected=56.0)], move=0.05)
        self.assertLess(h1b.cost_ratio, 1.5)                                         # 11 bp vs 20 table
        self.assertFalse(h1b.drop_rebuilders)

    # -------------------------------------------------------------- fallback exit
    def test_fallback_exit_when_engine_not_live(self):
        for t in (48.0, 50.0):
            self.step(t)
        pos = {"PORT": 100, "RECON": -125}
        self.assertEqual(self.step(76.0, fvs=None, positions=pos), {})
        self.assertTrue(self.h1.fallback)
        self.assertEqual(self.step(83.9, fvs=None, positions=pos), {})
        out = self.step(84.0, fvs=None, positions=pos)
        self.assertEqual(set(out), {"PORT", "RECON"})
        port, recon = out["PORT"], out["RECON"]
        self.assertEqual((port.target, port.clip_qty, port.priority), (0, 25, Priority.HEDGE))
        self.assertTrue(port.reduce_only and recon.reduce_only)
        self.assertEqual(recon.clip_qty, 62)
        self.assertEqual(port.limit_sell, 44.71)                    # max(bid * 0.995, mid * 0.985)
        self.assertEqual(recon.limit_buy, 52.34)                    # min(ask * 1.005, mid * 1.015)
        self.assertIn("PORT", self.step(98.9, fvs=None, positions=pos))
        self.assertEqual(self.step(99.0, fvs=None, positions=pos), {})

    def test_fallback_when_fv_not_of_this_round(self):
        self.step(48.0)
        self.step(76.0, fvs=fv(round_=0), positions={"PORT": 100})
        self.assertTrue(self.h1.fallback)

    def test_no_exit_when_engine_live(self):
        for t in (48.0, 50.0):
            self.step(t)
        pos = {"PORT": 100}
        self.step(76.0, positions=pos)
        self.assertFalse(self.h1.fallback)
        for t in (84.0, 90.0, 98.0):
            self.assertEqual(self.step(t, positions=pos), {})

    # -------------------------------------------------------------- promotion and commands
    def test_promotion_and_commands(self):
        self.run_round1(fills=[fill("PORT", Side.BUY, 50, 45.05, ref_mid=45.0)])
        self.assertTrue(self.h1.check_result)
        self.h1.on_promotion()
        self.assertIsNone(self.h1.check_result)
        self.step(48.0, round_=2)
        self.assertEqual(self.step(50.0, round_=2)["PORT"].target, 100)       # size_on_fail until h1 X
        ctx = make_ctx(50.0, 2, cfg=self.cfg)
        self.assertEqual(self.h1.on_command(Command("h1", ("1",)), ctx), [])
        self.step(48.0, round_=3)
        self.assertEqual(self.step(50.0, round_=3)["PORT"].target, 200)
        self.h1.on_command(Command("h1", ("0",)), ctx)              # applies at once, mid-entry
        h1 = self.h1
        self.assertEqual(self.step(52.0, round_=3), {})
        h1.on_command(Command("h1", ("auto",)), ctx)
        self.assertIsNone(h1.operator_size)
        h1.on_command(Command("h1", ("2",)), ctx)
        self.assertTrue(h1.log.kinds("command_error"))
        h1.on_command(Command("h1", ("off",)), ctx)
        self.assertFalse(h1.enabled)
        self.assertEqual(h1.size_for(2), (0.0, "disabled"))
        self.assertIsNone(h1.on_command(Command("s3", ("off",)), ctx))
        st = h1.state_dict()
        other = H1(make_deps(self.cfg))
        other.load_state(st)
        self.assertFalse(other.enabled)
        other.reset_season()
        self.assertIsNone(other.check_result)

    def test_rounds_config(self):
        cfg = FakeConfig()
        cfg.set("strategies.h1.rounds", [2, 3])
        h1 = H1(make_deps(cfg))
        h1.decide(make_ctx(48.0, 1, cfg=cfg))
        self.assertEqual(targets(h1.decide(make_ctx(50.0, 1, cfg=cfg))), {})
        cfg2 = FakeConfig()
        cfg2.set("strategies.h1.auto_size_from_check", False)
        cfg2.set("strategies.h1.size_r1", 1.0)
        self.assertEqual(H1(make_deps(cfg2)).size_for(3), (1.0, "fixed"))


if __name__ == "__main__":
    unittest.main()
