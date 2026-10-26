"""Group C: announcement freeze and manual overrides (DESIGN.md sections 6.7 and 9.1 C, announce)."""
from __future__ import annotations

import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

from test_c_support import (MIDS, FakeConfig, FakeLawCheck, FakeLaws, books, fv, make_ctx, make_deps,  # noqa: E402
                            news, targets)
from ctcbot.strategies.announce import Announce, expand_symbols, named_symbols  # noqa: E402
from ctcbot.strategies.runner import StrategyRunner  # noqa: E402
from ctcbot.types import SYMBOLS, CancelIntent, Command, Priority, RestIntent  # noqa: E402

EXPANSION = {
    "PORT": {"PORT", "RECON", "HAVEN", "REIN", "MAT", "ARCH", "REBLD"},
    "ISLE": {"ISLE", "HAVEN", "REIN", "MAT", "ARCH", "REBLD"},
    "AQUA": {"AQUA", "HAVEN", "REIN", "MAT", "ARCH", "REBLD"},
    "GRID": {"GRID", "GEN", "RECON", "HAVEN", "REIN", "MAT", "ARCH", "REBLD"},
    "FERRY": {"FERRY", "RECON", "HAVEN", "REIN", "MAT", "ARCH", "REBLD"},
    "RECON": {"RECON", "REBLD"},
    "MAT": {"MAT", "HAVEN", "REIN", "ARCH", "REBLD"},
    "GEN": {"GEN", "REBLD"},
    "HAVEN": {"HAVEN", "REIN", "MAT", "ARCH", "REBLD"},
    "REIN": {"REIN", "HAVEN", "MAT", "ARCH", "REBLD"},
    "ARCH": {"ARCH"},
    "REBLD": {"REBLD"},
}


def ann(symbols, headline="Company action", seq=0, round_=1, now=0.0):
    msg = {"headline": headline, "storm": "X", "time": "1802-05-10T11:00:00"}
    if symbols is not None:
        msg["symbols"] = symbols
    return news("announcement", msg, seq=seq, round_=round_, now=now)


class AnnounceTest(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()
        self.an = Announce(make_deps(self.cfg))

    def test_link_expansion_table(self):
        self.assertEqual(set(EXPANSION), set(SYMBOLS))
        for sym, want in EXPANSION.items():
            with self.subTest(symbol=sym):
                a = Announce(make_deps(self.cfg))
                out = a.decide(make_ctx(200.0, 1, cfg=self.cfg, news_=(ann([sym]),)))
                self.assertEqual(a.frozen_set(), frozenset(want))
                self.assertEqual({c.symbol for c in out}, want)
                for c in out:
                    self.assertIsInstance(c, CancelIntent)
                    self.assertEqual((c.scope, c.priority, c.strategy), ("symbol", Priority.SNIPE, "announce"))
                self.assertTrue(any(x["alert"] == "announcement" for x in a.log.kinds("alert")))

    def test_no_valid_symbols_freezes_all(self):
        out = self.an.decide(make_ctx(200.0, 1, cfg=self.cfg, news_=(ann(None),)))
        self.assertEqual(self.an.frozen_set(), frozenset(SYMBOLS))
        self.assertEqual(len(out), 12)
        a = Announce(make_deps(self.cfg))
        a.decide(make_ctx(200.0, 1, cfg=self.cfg, news_=(ann(["nope", 3]),)))
        self.assertEqual(a.frozen_set(), frozenset(SYMBOLS))
        cfg = FakeConfig()
        cfg.set("strategies.announce.freeze_all_if_no_symbols", False)
        b = Announce(make_deps(cfg))
        self.assertEqual(b.decide(make_ctx(200.0, 1, cfg=cfg, news_=(ann([]),))), [])
        self.assertEqual(named_symbols({"symbols": "gen"}), ["GEN"])
        self.assertEqual(expand_symbols(["GEN"], {}, {}, False), frozenset({"GEN"}))

    def test_news_headline_only_alerts(self):
        out = self.an.decide(make_ctx(200.0, 1, cfg=self.cfg, news_=(news("news", {"headline": "Storm talk"}),)))
        self.assertEqual(out, [])
        self.assertEqual(self.an.frozen_set(), frozenset())
        self.assertTrue(any(x["alert"] == "news" for x in self.an.log.kinds("alert")))

    def test_u_unfreezes(self):
        self.an.decide(make_ctx(200.0, 1, cfg=self.cfg, news_=(ann(["GEN"]),)))
        ctx = make_ctx(205.0, 1, cfg=self.cfg)
        self.assertEqual(self.an.on_command(Command("u", ("GEN",)), ctx), [])
        self.assertEqual(self.an.frozen_set(), frozenset({"REBLD"}))
        self.an.on_command(Command("u", ("all",)), ctx)
        self.assertEqual(self.an.frozen_set(), frozenset())
        self.an.on_command(Command("u", ("XYZ",)), ctx)
        self.assertTrue(self.an.log.kinds("command_error"))
        self.assertIsNone(self.an.on_command(Command("s3", ("on",)), ctx))

    def test_ua_adjusting_cap_coverage_and_window(self):
        self.an.decide(make_ctx(200.0, 1, now=200.0, cfg=self.cfg, news_=(ann(["GEN"]),)))
        self.an.on_command(Command("ua", ("GEN",)), make_ctx(201.0, 1, now=201.0, cfg=self.cfg))
        self.assertIn("GEN", self.an.frozen_set())                  # adjusting symbols stay frozen for others
        vals = dict(MIDS, GEN=42.0)                                   # the override moved GEN up by 2.00
        out = targets(self.an.decide(make_ctx(202.0, 1, now=202.0, cfg=self.cfg, fvs=fv(vals))))
        it = out["GEN"]
        self.assertEqual(it.target, 300)                              # 50% of L = 600
        self.assertEqual(it.priority, Priority.TAKE)
        self.assertEqual(it.strategy, "announce")
        self.assertEqual(it.clip_qty, 50)
        self.assertLessEqual(it.limit_buy, 42.0)
        held = books(dict(MIDS, GEN=41.6))                            # (41.6 - 40) / (42 - 40) = 0.8: covered
        self.assertEqual(self.an.decide(make_ctx(204.0, 1, now=204.0, cfg=self.cfg, fvs=fv(vals), bk=held)), [])
        self.assertTrue(self.an.log.kinds("announce_adjust_hold"))
        self.assertEqual(targets(self.an.decide(make_ctx(205.0, 1, now=205.0, cfg=self.cfg, fvs=fv(vals),
                                                         positions={"GEN": 300}))), {})   # at the cap: hold
        self.an.decide(make_ctx(246.0, 1, now=246.0, cfg=self.cfg, fvs=fv(vals)))
        self.assertNotIn("GEN", self.an.frozen_set())                # back to normal after 45 s
        self.assertTrue(self.an.log.kinds("announce_adjust_end"))

    def test_ua_restrictions(self):
        vals = dict(MIDS, GEN=42.0)
        for t, r in ((2.0, 1), (690.0, 3)):
            a = Announce(make_deps(self.cfg))
            a.decide(make_ctx(t, r, now=0.0, cfg=self.cfg, news_=(ann(["GEN"], round_=r),)))
            a.on_command(Command("ua", ("GEN",)), make_ctx(t, r, now=0.0, cfg=self.cfg))
            self.assertEqual(a.decide(make_ctx(t, r, now=1.0, cfg=self.cfg, fvs=fv(vals, round_=r))), [], (t, r))
        a = Announce(make_deps(self.cfg))
        a.on_command(Command("ua", ("GEN",)), make_ctx(300.0, 2, now=0.0, cfg=self.cfg))
        self.assertIn("GEN", targets(a.decide(make_ctx(300.0, 2, now=1.0, cfg=self.cfg, fvs=fv(vals, round_=2)))))
        self.assertEqual(a.decide(make_ctx(300.0, 2, now=2.0, cfg=self.cfg, fvs=None)), [])

    def test_state_roundtrip(self):
        self.an.decide(make_ctx(200.0, 1, cfg=self.cfg, news_=(ann(["RECON"]),)))
        self.an.on_command(Command("ua", ("RECON",)), make_ctx(201.0, 1, cfg=self.cfg))
        st = self.an.state_dict()
        other = Announce(make_deps(self.cfg))
        other.load_state(st)
        self.assertEqual(other.frozen_set(), frozenset({"RECON", "REBLD"}))   # adjusting is restored frozen
        self.assertEqual(other.adjusting, {})
        other.reset_season()
        self.assertEqual(other.frozen_set(), frozenset())

    def test_same_cycle_freeze_and_s1r3_lock_after_700(self):
        S = {s: MIDS[s] * 1.01 for s in SYMBOLS}
        runner = StrategyRunner(make_deps(self.cfg, laws=FakeLaws(dict(S), tol=10.0), law_check=FakeLawCheck(True)))
        # same cycle: an announcement drops S3's PORT target and cancels the symbol
        out = runner.decide(make_ctx(200.0, 1, cfg=self.cfg, fvs=fv(dict(MIDS, PORT=50.0, GRID=40.0)),
                                     news_=(ann(["PORT"]),)))
        self.assertNotIn("PORT", targets(out))
        self.assertIn("GRID", targets(out))
        self.assertIn(("PORT", "symbol"), {(c.symbol, c.scope) for c in out if isinstance(c, CancelIntent)})
        runner.reset_season()
        # round 3, 705 s: HAVEN is announced; after 'u HAVEN' S1-r3 still needs s1ok
        runner.decide(make_ctx(705.0, 3, now=705.0, cfg=self.cfg, news_=(ann(["HAVEN"], round_=3),)))
        runner.on_command(Command("u", ("all",)), make_ctx(706.0, 3, cfg=self.cfg))
        self.assertEqual(runner.frozen(), frozenset())
        final = news("damage_report", {"stage": "final", "damage_pct": {}}, seq=900, round_=3)
        runner.decide(make_ctx(740.2, 3, now=740.2, cfg=self.cfg, news_=(final,)))
        fvs = fv(dict(S), round_=3, seq_hi=900, diag={"status_r3": "final"}, computed=740.25)
        out = runner.decide(make_ctx(740.3, 3, now=740.3, cfg=self.cfg, fvs=fvs))
        got = {i.symbol for i in out if isinstance(i, RestIntent) and i.strategy == "s1r3"}
        self.assertTrue({"PORT", "ISLE", "GRID"} <= got)
        self.assertFalse({"HAVEN", "REIN", "MAT", "ARCH", "REBLD"} & got)
        runner.on_command(Command("s1ok", ("HAVEN",)), make_ctx(741.0, 3, cfg=self.cfg))
        out = runner.decide(make_ctx(740.9, 3, now=740.9, cfg=self.cfg, fvs=fvs))
        self.assertIn("HAVEN", {i.symbol for i in out if isinstance(i, RestIntent) and i.strategy == "s1r3"})


if __name__ == "__main__":
    unittest.main()
