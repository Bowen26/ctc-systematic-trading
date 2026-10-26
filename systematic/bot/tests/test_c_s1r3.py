"""Group C: S1-r3 settlement positioning (DESIGN.md sections 6.4 and 9.1 C, s1r3)."""
from __future__ import annotations

import math
import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

from test_c_support import (MIDS, FakeConfig, FakeLawCheck, FakeLaws, books, fv, make_ctx, make_deps,  # noqa: E402
                            news, season)
from ctcbot.models.coverage import CLOSURE_SYMBOLS  # noqa: E402
from ctcbot.models.laws import LawCheck  # noqa: E402
from ctcbot.strategies.s1r3 import S1R3  # noqa: E402
from ctcbot.types import SYMBOLS, CancelIntent, Command, ListLog, Priority, RestIntent, Side  # noqa: E402

S = {s: round(MIDS[s] * 1.01, 4) for s in SYMBOLS}            # settlement values 1% above the stale books
DL = [s for s in SYMBOLS if s not in CLOSURE_SYMBOLS]
T0 = 10_000.0                                                   # mono = T0 + round time


def final_news(seq=900):
    return news("damage_report", {"stage": "final", "damage_pct": {}, "industry_loss_musd": 10.0}, seq=seq, round_=3)


def rests(intents):
    return [i for i in intents if isinstance(i, RestIntent)]


def placed(intents):
    return sorted({i.symbol for i in rests(intents)})


class S1R3Test(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()
        self.laws = FakeLaws(dict(S), tol={s: self.cfg.sym(s)["laws_tol"] for s in SYMBOLS})
        self.lc = FakeLawCheck(True)
        self.log = ListLog()
        self.s = S1R3(make_deps(self.cfg, laws=self.laws, law_check=self.lc, log=self.log))
        self.fv_ok = fv(dict(S), round_=3, seq_hi=900, diag={"status_r3": "final"}, computed=T0 + 740.25)

    def step(self, t, *, round_=3, fvs=None, news_=(), si=None, **kw):
        return self.s.decide(make_ctx(t, round_, now=T0 + t, cfg=self.cfg, fvs=fvs, news_=news_,
                                      seasoninfo=si or season(), **kw))

    def fire(self, t=740.25, **kw):
        self.step(740.2, news_=(final_news(),), **kw)
        return self.step(t, fvs=self.fv_ok, **kw)

    # -------------------------------------------------------------- arming and firing
    def test_arm_from_news_and_fire_on_seq_hi(self):
        self.assertEqual(self.step(740.2, news_=(final_news(),)), [])
        self.assertTrue(self.s.armed)
        self.assertEqual(self.s.final_seq, 900)
        self.assertEqual(self.step(740.22, fvs=fv(dict(S), round_=3, seq_hi=899)), [])
        self.assertEqual(self.step(740.23, fvs=fv(dict(S), round_=3, seq_hi=900, diag={"status_r3": "prelim"})), [])
        out = self.step(740.25, fvs=self.fv_ok)
        self.assertTrue(self.s.fired)
        self.assertEqual(placed(out), sorted(SYMBOLS))
        for it in rests(out):
            self.assertEqual(it.priority, Priority.SNIPE)
            self.assertIsNone(it.qty)
            self.assertEqual(it.strategy, "s1r3")
            eps = self.cfg.sym(it.symbol)["eps"]
            if it.side is Side.BUY:
                self.assertEqual(it.key, f"s1r3:{it.symbol}:buy")
                self.assertEqual(it.price, math.floor((S[it.symbol] - eps) * 100 + 1e-6) / 100)
            else:
                self.assertEqual(it.key, f"s1r3:{it.symbol}:sell")
                self.assertEqual(it.price, math.ceil((S[it.symbol] + eps) * 100 - 1e-6) / 100)
        self.assertTrue(self.log.kinds("s1r3_fire"))

    def test_arm_from_snapshot_damage_list(self):
        si = season(final={3: {"damage": {}}}, final_seq={3: -1})
        self.assertEqual(self.step(741.0, si=si), [])
        self.assertTrue(self.s.armed)
        self.assertEqual(self.s.final_seq, -1)
        self.assertEqual(self.step(741.1, si=si, fvs=fv(dict(S), round_=3, seq_hi=5, computed=T0 + 740.0)), [])
        out = self.step(741.2, si=si, fvs=fv(dict(S), round_=3, seq_hi=5, computed=T0 + 741.15))
        self.assertTrue(self.s.fired)
        self.assertEqual(len(placed(out)), 12)

    def test_only_round3_and_late_arm(self):
        self.assertEqual(self.step(740.2, round_=2, news_=(final_news(),)), [])
        self.assertFalse(self.s.armed)
        self.step(776.0, news_=(final_news(),))
        self.assertFalse(self.s.armed)
        self.assertTrue(any(a["alert"] == "s1r3_late" for a in self.log.kinds("alert")))

    # -------------------------------------------------------------- orders
    def test_edge_order_and_rein_cancel_first(self):
        out = self.fire()
        self.assertIsInstance(out[0], CancelIntent)
        self.assertEqual((out[0].scope, out[0].key, out[0].priority), ("key", "rein_ask", Priority.SNIPE))
        edges = [i.edge_usd for i in rests(out)]
        # 23 Oct: rests go out most capturable dollars first (value_usd), not largest per-share edge first
        values = [i.value_usd or 0.0 for i in rests(out)]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertTrue(all(v >= 0.0 for v in values))
        self.assertEqual(len(edges), len(values))
        top = rests(out)[0]
        self.assertEqual(top.side, Side.BUY)                       # the books are 1% stale below S
        self.assertGreater(top.edge_usd, 0)
        self.assertAlmostEqual(top.edge_usd, max(S[s] - self.cfg.sym(s)["eps"] - books()[s].ask for s in SYMBOLS))
        self.assertTrue(all(i.edge_usd == 0 for i in rests(out) if i.side is Side.SELL))
        again = self.step(740.8, fvs=self.fv_ok)
        self.assertFalse(any(isinstance(i, CancelIntent) for i in again))    # the REIN cancel goes once

    def test_sweep_cadence(self):
        emits = []
        self.fire(740.25)
        emits.append(740.25)
        t = 740.25
        while t < 780.0:
            t = round(t + 0.05, 2)
            if rests(self.step(t, fvs=self.fv_ok)):
                emits.append(t)
        gaps = [round(b - a, 2) for a, b in zip(emits, emits[1:])]
        fast = [g for a, g in zip(emits, gaps) if a < 750.0 - 0.5]
        slow = [g for a, g in zip(emits, gaps) if a >= 750.0]
        self.assertTrue(fast and all(abs(g - 0.5) < 1e-6 for g in fast), fast)
        self.assertTrue(slow and all(abs(g - 2.0) < 1e-6 for g in slow), slow)
        self.assertLess(emits[-1], 778.0)
        self.assertGreaterEqual(emits[-1], 776.0)

    def test_sanity_at_756_not_before(self):
        self.fire()
        bad = books(dict(S, PORT=S["PORT"] * 0.98, ARCH=S["ARCH"] * 0.9, ISLE=S["ISLE"] + 0.05))   # repriced house
        out = self.step(755.9, fvs=self.fv_ok, bk=bad)
        self.assertFalse(any(isinstance(i, CancelIntent) for i in out))
        out = self.step(756.0, fvs=self.fv_ok, bk=bad)
        cancels = [i for i in out if isinstance(i, CancelIntent)]
        self.assertEqual([(c.scope, c.key) for c in cancels], [("key_prefix", "s1r3:PORT:")])   # ETFs are not checked
        self.assertIn("PORT", self.s.sanity_excluded)
        self.assertTrue(any(a["alert"] == "s1r3_sanity" for a in self.log.kinds("alert")))
        later = self.step(758.0, fvs=self.fv_ok, bk=bad)
        self.assertNotIn("PORT", placed(later))
        self.assertIn("ISLE", placed(later))                       # 0.05 <= max(3 * 0.02, 0.5% S)
        self.assertIn("ARCH", placed(later))

    # -------------------------------------------------------------- gates
    def test_gate_a_b_c_d(self):
        cfg = FakeConfig()
        cfg.set("strategies.s1r3.symbols", [s for s in SYMBOLS if s != "REIN"])
        self.s = S1R3(make_deps(cfg, laws=self.laws, law_check=self.lc, log=self.log))
        self.cfg = cfg
        ctx = make_ctx(700.0, 3, cfg=cfg)
        self.assertEqual(self.s.on_command(Command("s1ok", ("PORT",)), ctx), [])
        out = self.fire(si=season(case_diff=frozenset({"GRID"})), frozen={"PORT", "HAVEN"}, disabled={"GEN"})
        got = placed(out)
        self.assertNotIn("REIN", got)                               # (a)
        self.assertNotIn("GRID", got)                               # (b)
        self.assertIn("PORT", got)                                  # (c) frozen, but s1ok
        self.assertNotIn("HAVEN", got)                              # (c) frozen
        self.assertNotIn("GEN", got)                                # disabled
        self.assertFalse(any(isinstance(i, CancelIntent) and i.key == "rein_ask" for i in out))
        gates = {e["symbol"]: e["gate"] for e in self.log.kinds("s1r3_gate")}
        self.assertEqual(gates, {"REIN": "a:symbols", "GRID": "b:case_diff", "HAVEN": "c:frozen", "GEN": "c:disabled"})
        res = self.s.on_command(Command("x1", ("AQUA",)), ctx)      # (d)
        self.assertEqual([(c.scope, c.key) for c in res], [("key_prefix", "s1r3:AQUA:")])
        self.assertNotIn("AQUA", placed(self.step(741.0, fvs=self.fv_ok)))

    def test_gate_c_late_announcement(self):
        ann = news("announcement", {"headline": "HAVEN raises its retention", "symbols": ["HAVEN"]}, seq=0,
                   round_=3)
        self.step(705.0, news_=(ann,))
        self.assertEqual(self.s.late_announced, {"HAVEN", "REIN", "MAT", "ARCH", "REBLD"})
        out = self.fire()
        for s in ("HAVEN", "REIN", "MAT", "ARCH", "REBLD"):
            self.assertNotIn(s, placed(out), s)
        self.s.on_command(Command("s1ok", ("HAVEN",)), make_ctx(741.0, 3, cfg=self.cfg))
        self.assertIn("HAVEN", placed(self.step(740.8, fvs=self.fv_ok)))
        early = S1R3(make_deps(self.cfg, laws=self.laws, law_check=self.lc))
        early.decide(make_ctx(650.0, 3, cfg=self.cfg, news_=(ann,)))
        self.assertEqual(early.late_announced, set())

    def test_gate_e_fail_closed(self):
        self.laws.vals = None                                       # a final report still missing
        self.assertEqual(placed(self.fire()), [])
        self.assertTrue(all(e["gate"] == "e:laws_incomplete" for e in self.log.kinds("s1r3_gate")))
        self.laws.vals = dict(S, PORT=None, GRID=S["GRID"] + 0.3, ISLE=S["ISLE"] + 0.019)
        got = placed(self.step(740.8, fvs=self.fv_ok))
        self.assertNotIn("PORT", got)                               # law value None
        self.assertNotIn("GRID", got)                               # |S - law| > 0.25
        self.assertIn("ISLE", got)                                  # within 0.02
        gates = {e["symbol"]: e["gate"] for e in self.log.kinds("s1r3_gate")}
        self.assertEqual(gates["PORT"], "e:law_value_none")
        self.assertEqual(gates["GRID"], "e:laws_mismatch")

    def test_gate_e_without_laws_module(self):
        s = S1R3(make_deps(self.cfg, laws=None, law_check=self.lc, log=self.log))
        self.s = s
        got = placed(self.fire())
        self.assertEqual(got, sorted(DL))
        self.assertTrue(self.log.kinds("s1r3_no_laws"))

    def test_gate_f_missing_round_and_wide_eps(self):
        out = self.fire(si=season(coverage={1: "missing", 2: "full", 3: "full"}))
        self.assertEqual(placed(out), sorted(DL))
        s2 = S1R3(make_deps(self.cfg, laws=self.laws, law_check=self.lc))
        self.s = s2
        out = self.fire(si=season(coverage={1: "full", 2: "gapped", 3: "full"}))
        self.assertEqual(placed(out), sorted(SYMBOLS))
        for it in rests(out):
            want = 0.25 if it.symbol in CLOSURE_SYMBOLS else self.cfg.sym(it.symbol)["eps"]
            self.assertAlmostEqual(s2.eps_used[it.symbol], want)
        port_buy = next(i for i in rests(out) if i.symbol == "PORT" and i.side is Side.BUY)
        self.assertEqual(port_buy.price, math.floor((S["PORT"] - 0.25) * 100 + 1e-6) / 100)

    def test_gate_g_law_check(self):
        lc = LawCheck(self.cfg, ListLog())                          # no rounds 1-2 check ran: fail closed
        self.s = S1R3(make_deps(self.cfg, laws=self.laws, law_check=lc, log=self.log))
        self.assertEqual(placed(self.fire()), [])
        self.assertTrue(all(e["gate"] == "g:law_check" for e in self.log.kinds("s1r3_gate")))
        self.assertEqual(self.s.on_command(Command("lawok", ("all",)), make_ctx(741.0, 3, cfg=self.cfg)), [])
        self.assertEqual(placed(self.step(740.8, fvs=self.fv_ok)), sorted(SYMBOLS))
        cfg = FakeConfig()
        cfg.set("strategies.s1r3.require_law_check", False)
        s = S1R3(make_deps(cfg, laws=self.laws, law_check=LawCheck(cfg, ListLog())))
        s.decide(make_ctx(740.2, 3, now=T0 + 740.2, cfg=cfg, news_=(final_news(),)))
        self.assertEqual(placed(s.decide(make_ctx(740.25, 3, now=T0 + 740.25, cfg=cfg, fvs=self.fv_ok))),
                         sorted(SYMBOLS))

    # -------------------------------------------------------------- engine late
    def test_laws_fallback_after_one_second(self):
        self.step(740.2, news_=(final_news(),))
        self.assertEqual(self.step(741.1, fvs=fv(dict(S), round_=3, seq_hi=899)), [])
        out = self.step(741.25, fvs=fv(dict(S), round_=3, seq_hi=899))
        self.assertEqual(self.s.source, "laws")
        self.assertEqual(placed(out), sorted(SYMBOLS))
        self.assertTrue(all(abs(self.s.eps_used[s] - self.cfg.sym(s)["eps_wide"]) < 1e-12 for s in SYMBOLS))
        self.assertTrue(any(a["alert"] == "s1r3_laws_fallback" for a in self.log.kinds("alert")))
        engine_S = dict(S, PORT=S["PORT"] + 0.1)
        self.laws.vals = dict(engine_S)
        out = self.step(741.3, fvs=fv(engine_S, round_=3, seq_hi=900, diag={"status_r3": "final"}))
        self.assertEqual(self.s.source, "engine")
        buy = next(i for i in rests(out) if i.symbol == "PORT" and i.side is Side.BUY)
        self.assertEqual(buy.price, math.floor((engine_S["PORT"] - 0.07) * 100 + 1e-6) / 100)

    def test_no_fallback_when_disabled(self):
        cfg = FakeConfig()
        cfg.set("strategies.s1r3.laws_fallback", False)
        s = S1R3(make_deps(cfg, laws=self.laws, law_check=self.lc))
        s.decide(make_ctx(740.2, 3, now=T0 + 740.2, cfg=cfg, news_=(final_news(),)))
        self.assertEqual(s.decide(make_ctx(742.0, 3, now=T0 + 742.0, cfg=cfg)), [])
        self.assertFalse(s.fired)

    # -------------------------------------------------------------- lifecycle
    def test_off_state_and_reset(self):
        self.fire()
        st = self.s.state_dict()
        other = S1R3(make_deps(self.cfg, laws=self.laws, law_check=self.lc))
        other.load_state(st)
        self.assertTrue(other.fired)
        self.assertEqual(other.S, self.s.S)
        out = other.decide(make_ctx(745.0, 3, now=T0 + 745.0, cfg=self.cfg, fvs=self.fv_ok))
        self.assertEqual(len(placed(out)), 12)                      # a restored sweep re-places at once
        res = self.s.on_command(Command("s1r3", ("off",)), make_ctx(745.0, 3, cfg=self.cfg))
        self.assertEqual([(c.scope, c.strategy) for c in res], [("strategy", "s1r3")])
        self.assertEqual(self.step(745.5, fvs=self.fv_ok), [])
        self.s.reset_season()
        self.assertFalse(self.s.armed or self.s.fired)
        self.assertIsNone(self.s.on_command(Command("h1", ("1",)), make_ctx(1.0, 1, cfg=self.cfg)))


if __name__ == "__main__":
    unittest.main()
