"""Group C: the strategy runner (DESIGN.md sections 4.C, 6.8 and 9.1 C, runner)."""
from __future__ import annotations

import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

from test_c_support import FakeConfig, book, books, make_ctx, make_deps, targets  # noqa: E402
from ctcbot.strategies.base import Strategy  # noqa: E402
from ctcbot.strategies.runner import StrategyRunner  # noqa: E402
from ctcbot.types import CancelIntent, Command, ListLog, Priority, RestIntent, Side, TargetIntent  # noqa: E402


def tgt(strategy, sym, target, lb=None, ls=None, reduce_only=False):
    return TargetIntent(strategy=strategy, symbol=sym, target=target, clip_qty=10, clip_interval_s=2.0,
                        limit_buy=lb, limit_sell=ls, priority=Priority.TAKE, ttl_s=2.0, reduce_only=reduce_only)


def rest(strategy, sym, side, price, key=None):
    return RestIntent(strategy=strategy, key=key or f"{strategy}:{sym}:{side}", symbol=sym, side=side, price=price,
                      priority=Priority.SNIPE)


class FakeStrategy(Strategy):
    """Emits a fixed list of intents, or raises."""

    def __init__(self, deps, name, intents=(), raises=False):
        self.name = name
        super().__init__(deps)
        self.intents = list(intents)
        self.raises = raises
        self.calls = 0
        self.seen_frozen = None

    def decide(self, ctx):
        self.calls += 1
        self.seen_frozen = ctx.frozen
        if self.raises:
            raise RuntimeError(f"{self.name} broke")
        return list(self.intents)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()
        self.log = ListLog()
        self.deps = make_deps(self.cfg, log=self.log)
        self.r = StrategyRunner(self.deps)
        for name in self.r.ORDER:                                    # silence the real strategies
            self.r.strategies[name].enabled = name == "announce"

    def put(self, name, intents=(), raises=False):
        s = FakeStrategy(self.deps, name, intents, raises)
        s.enabled = True
        self.r.strategies[name] = s
        return s

    def test_order_and_precedence(self):
        calls = []
        for name in StrategyRunner.ORDER:
            if name == "announce":
                continue
            s = self.put(name)
            s.decide = (lambda n: (lambda ctx: calls.append(n) or []))(name)
        self.r.decide(make_ctx(200.0, 1, cfg=self.cfg))
        self.assertEqual(calls, [n for n in StrategyRunner.ORDER if n != "announce"])
        self.put("h1", [tgt("h1", "PORT", 100, lb=45.1)])
        self.put("s3", [tgt("s3", "PORT", -200, ls=44.9), tgt("s3", "GRID", 33, lb=36.1)])
        self.put("flatten", [tgt("flatten", "RECON", 0, lb=52.2, reduce_only=True)])
        out = targets(self.r.decide(make_ctx(200.0, 1, cfg=self.cfg)))
        self.assertEqual(out["PORT"].strategy, "h1")
        self.assertEqual(out["GRID"].strategy, "s3")
        self.assertEqual(out["RECON"].strategy, "flatten")
        self.assertEqual(self.r.dropped["precedence"], 1)
        # S1-r3 owns its symbols through its rests
        self.put("s1r3", [rest("s1r3", "ISLE", Side.BUY, 55.0)])
        self.put("s3", [tgt("s3", "ISLE", 25, lb=55.1)])
        out = self.r.decide(make_ctx(741.0, 3, cfg=self.cfg))
        self.assertEqual([type(i).__name__ for i in out if i.symbol == "ISLE"], ["RestIntent"])

    def test_frozen_filtering(self):
        self.r.strategies["announce"].frozen = {"PORT"}
        self.put("s1r3", [rest("s1r3", "PORT", Side.BUY, 45.0), rest("s1r3", "ISLE", Side.BUY, 55.0)])
        self.put("flatten", [tgt("flatten", "PORT", 0, ls=44.9, reduce_only=True)])
        self.put("s3", [tgt("s3", "PORT", 200, lb=45.1), tgt("s3", "GRID", 33, lb=36.1)])
        self.put("rein_ask", [CancelIntent(strategy="rein_ask", priority=Priority.SNIPE, scope="key", key="rein_ask",
                                           symbol="PORT")])
        out = self.r.decide(make_ctx(550.0, 1, cfg=self.cfg))
        kinds = {(type(i).__name__, i.strategy, i.symbol) for i in out}
        self.assertIn(("TargetIntent", "flatten", "PORT"), kinds)
        self.assertIn(("CancelIntent", "rein_ask", "PORT"), kinds)
        self.assertIn(("RestIntent", "s1r3", "ISLE"), kinds)
        self.assertNotIn(("RestIntent", "s1r3", "PORT"), kinds)
        self.assertIn(("TargetIntent", "s3", "GRID"), kinds)
        self.assertEqual(self.r.dropped["frozen"], 2)               # S1-r3's PORT rest and S3's PORT target
        self.r.strategies["s1r3"].s1ok = {"PORT"}
        self.r.strategies["s1r3"].confirmed = lambda: frozenset({"PORT"})
        out = self.r.decide(make_ctx(741.0, 3, cfg=self.cfg))
        self.assertIn(("RestIntent", "s1r3", "PORT"), {(type(i).__name__, i.strategy, i.symbol) for i in out})

    def test_later_strategies_see_a_fresh_freeze(self):
        from test_c_support import news
        s3 = self.put("s3", [tgt("s3", "GEN", 50, lb=40.1)])
        ann = news("announcement", {"headline": "GEN plant", "symbols": ["GEN"]}, seq=0, round_=1)
        out = self.r.decide(make_ctx(200.0, 1, cfg=self.cfg, news_=(ann,)))
        self.assertIn("GEN", s3.seen_frozen)
        self.assertNotIn("GEN", targets(out))
        self.assertEqual({c.symbol for c in out if isinstance(c, CancelIntent)}, {"GEN", "REBLD"})
        self.assertEqual(self.r.frozen(), frozenset({"GEN", "REBLD"}))

    def test_opening_guard(self):
        mk = tgt("s3", "PORT", 100, lb=45.10)                      # buy at or through the ask 45.07: marketable
        passive = tgt("s3", "GRID", 33, lb=35.0)                    # inside the book
        ask = rest("rein_ask", "REIN", Side.SELL, 90.02, key="rein_ask")
        self.put("s3", [mk, passive])
        self.put("rein_ask", [ask])
        cases = {2.9: False, 3.0: True}
        for t, allowed in cases.items():
            out = self.r.decide(make_ctx(t, 1, cfg=self.cfg))
            self.assertEqual("PORT" in targets(out), allowed, t)
            self.assertIn("GRID", targets(out))
            self.assertIn("REIN", {i.symbol for i in out if isinstance(i, RestIntent)})
        out = self.r.decide(make_ctx(100.0, 1, cfg=self.cfg, running=False))
        self.assertNotIn("PORT", targets(out))
        wall = books(PORT=book("PORT", 45.0, hs_bp=400.0))            # touch 43.20 / 46.80
        out = self.r.decide(make_ctx(100.0, 1, cfg=self.cfg, bk=wall))
        self.assertIn("PORT", targets(out))                         # 45.10 is not marketable there
        self.put("s3", [tgt("s3", "PORT", 100, lb=46.80), passive])
        out = self.r.decide(make_ctx(100.0, 1, cfg=self.cfg, bk=wall))
        self.assertNotIn("PORT", targets(out))                      # marketable on a wall-only book
        self.put("s3", [mk, passive])
        out = self.r.decide(make_ctx(2.0, 1, cfg=self.cfg, bk=books(PORT=book("PORT", None), GRID=book("GRID", None))))
        self.assertEqual(targets(out), {})                          # a missing touch counts as marketable
        out = self.r.decide(make_ctx(100.0, 1, cfg=self.cfg, bk=books(PORT=book("PORT", None))))
        self.assertIn("PORT", targets(out))                         # ... but is only guarded at the open
        out = self.r.decide(make_ctx(None, 1, cfg=self.cfg, known=False))
        self.assertNotIn("PORT", targets(out))
        self.put("s3", [tgt("s3", "PORT", 0, lb=45.10)])
        out = self.r.decide(make_ctx(1.0, 1, cfg=self.cfg))
        self.assertIn("PORT", targets(out))                         # no order needed: nothing to guard
        cfg = FakeConfig()
        cfg.set("guards.opening.enabled", False)
        r2 = StrategyRunner(make_deps(cfg))
        for name in r2.ORDER:
            r2.strategies[name].enabled = False
        r2.strategies["s3"] = FakeStrategy(r2.deps, "s3", [mk])
        self.assertIn("PORT", targets(r2.decide(make_ctx(1.0, 1, cfg=cfg))))

    def test_exception_isolation_and_disable(self):
        bad = self.put("h1", raises=True)
        self.put("s3", [tgt("s3", "GRID", 33, lb=36.1)])
        for k in range(2):
            out = self.r.decide(make_ctx(200.0 + k, 1, now=200.0 + k, cfg=self.cfg))
            self.assertIn("GRID", targets(out))
        self.assertTrue(bad.enabled)
        exc = self.log.kinds("exception")
        self.assertEqual(len(exc), 2)
        self.assertEqual(exc[0]["strategy"], "h1")
        self.assertIn("RuntimeError", exc[0]["traceback"])
        self.r.decide(make_ctx(203.0, 1, now=203.0, cfg=self.cfg))
        self.assertFalse(bad.enabled)
        self.assertTrue(any(a["alert"] == "strategy_disabled" for a in self.log.kinds("alert")))
        calls = bad.calls
        self.r.decide(make_ctx(204.0, 1, now=204.0, cfg=self.cfg))
        self.assertEqual(bad.calls, calls)
        self.r.on_command(Command("h1", ("on",)), make_ctx(205.0, 1, now=205.0, cfg=self.cfg))
        self.assertTrue(bad.enabled)
        self.r.decide(make_ctx(206.0, 1, now=206.0, cfg=self.cfg))
        self.assertTrue(bad.enabled)                                # the counter was cleared by 'h1 on'

    def test_exceptions_outside_the_window_do_not_disable(self):
        bad = self.put("s3", raises=True)
        for now in (0.0, 70.0, 140.0, 210.0):
            self.r.decide(make_ctx(200.0, 1, now=now, cfg=self.cfg))
        self.assertTrue(bad.enabled)

    def test_commands_status_state(self):
        ctx = make_ctx(200.0, 1, cfg=self.cfg)
        self.r.strategies["announce"].frozen = {"PORT"}
        self.assertEqual(self.r.on_command(Command("u", ("PORT",)), ctx), [])
        self.assertEqual(self.r.frozen(), frozenset())
        self.assertEqual(self.r.on_command(Command("zzz", ()), ctx), [])
        self.assertTrue(self.log.kinds("command_unhandled"))
        r = StrategyRunner(self.deps)
        self.assertEqual(r.leadlag_symbols(), frozenset())
        r.on_command(Command("lag", ("on",)), ctx)
        self.assertEqual(r.leadlag_symbols(), frozenset({"ARCH", "REBLD"}))
        res = r.on_command(Command("x1", ("PORT",)), ctx)
        self.assertEqual([(c.scope, c.key) for c in res], [("key_prefix", "s1r3:PORT:")])
        st = r.state_dict()
        self.assertEqual(set(st), set(StrategyRunner.ORDER))
        r2 = StrategyRunner(self.deps)
        r2.load_state(st)
        self.assertTrue(r2.strategies["etf_leadlag"].enabled)
        self.assertIn("PORT", r2.strategies["s1r3"].x1)
        r2.load_state({"h1": {"enabled": "garbage", "operator_size": 7}})
        status = r2.status()
        self.assertIn("_runner", status)
        r2.on_promotion()
        self.assertTrue(r2.strategies["h1"].promoted)
        r2.reset_season()
        self.assertEqual(r2.strategies["s1r3"].x1, set())

    def test_real_strategies_smoke(self):
        r = StrategyRunner(self.deps)
        for t in (0.5, 48.0, 50.0, 100.0, 300.0, 545.0, 600.0, 740.5):
            out = r.decide(make_ctx(t, 3 if t > 700 else 1, now=t, cfg=self.cfg))
            for it in out:
                self.assertIsInstance(it, (TargetIntent, RestIntent, CancelIntent))
        self.assertEqual(self.log.kinds("exception"), [])


if __name__ == "__main__":
    unittest.main()
