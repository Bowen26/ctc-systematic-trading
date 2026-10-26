"""Robustness review (lens: failure handling): fault injection through the recorded-market simulator. SLOW.

The testnet recording is replayed in virtual time through ``sim.harness.Harness`` with the real core, strategies,
order manager and inline engine against ``SimExchange`` (recorded books), while a wrapper market rewrites the frame
stream (second connection, drops, duplicates, malformed frames) or a wrapper exchange delays REST results.

* ``test_feed_chaos_*`` are regression guards and pass on the reviewed code.
* ``test_malformed_snapshot_*`` and ``test_slow_gets_*`` reproduce review findings; they failed on the reviewed code
  and pass after the hardening pass (DESIGN.md §13).

Run from BOT (about 20 s per test):

    CTCBOT_SLOW=1 SYS/.venv/bin/python -m unittest discover -s tests -p "review_robust_sim.py" -v
"""
from __future__ import annotations

import copy
import os
import random
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

BASE_RELEVANT = [695, 764, 250, 416, 828]


class _ChaosMarket:
    """The harness-facing market: the recorded market with ``transform(list[MarketFrame])`` applied to frames()."""

    def __init__(self, inner, transform) -> None:
        self.inner, self.transform, self._fr = inner, transform, None

    def frames(self):
        if self._fr is None:
            out = list(enumerate(self.transform(list(self.inner.frames()))))
            out.sort(key=lambda kv: (round(kv[1].t, 9), kv[0]))
            self._fr = [f for _, f in out]
        return iter(self._fr)

    def default_t_end(self):
        return self.inner.default_t_end()

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _mf(t, frame, conn, control=None, recv_wall=None):
    from sim.research_market import MarketFrame
    return MarketFrame(t, frame, conn, control=control, recv_wall=recv_wall)


def _is_news(f) -> bool:
    return f.control is None and isinstance(f.frame, dict) and f.frame.get("type") == "news"


def _run(transform=None, sets=(), probes=()):
    """Run the whole recording in book mode; return (harness, results, log, exchange)."""
    testpaths.slow()
    testpaths.require(testpaths.RECORDING)
    testpaths.require(testpaths.HISTORY)
    from ctcbot.app.core import BotCore
    from ctcbot.case import CaseInfo, Schedule, diff_case
    from ctcbot.config import load_config
    from ctcbot.models.cost_model import CostModel
    from ctcbot.models.engine_adapter import EngineAdapter
    from ctcbot.models.laws import load_laws_module
    from ctcbot.types import Command, ListLog
    from sim.exchange import SimExchange
    from sim.harness import EngineTap, Harness, VirtualClock
    from sim.recorded_market import RecordedMarket
    cfg = load_config(None, sets=list(sets))
    log = ListLog()
    case = CaseInfo.from_file(cfg.path(cfg.get("paths.research_case")))
    schedule = Schedule(case.timing, cfg.get("cost.phases"))
    cost = CostModel(cfg.path(cfg.get("cost.model")), cfg)
    laws = load_laws_module(cfg.path(cfg.get("laws.module")))
    market = RecordedMarket(testpaths.RECORDING, cfg, testpaths.HISTORY, case=case, schedule=schedule)
    vt = VirtualClock(wall0=float(market.first_received_at))
    exchange = SimExchange(market, cfg, cost, log, fills=True)
    lib = cfg.get("sim.recorded_library_seasons", [226, 600])
    engine = EngineAdapter(cfg, case.raw, vt, log, mode="inline", library_seasons=(int(lib[0]), int(lib[1])),
                           snapshot_fix=True)
    tap = EngineTap(engine, vt.mono)
    cd = diff_case(case.raw, case.raw)

    def core_factory(timesrc):
        return BotCore(cfg, case, cd, tap, timesrc, log, mode="sim", run_dir=None, run_id="rr", laws_module=laws,
                       cost=cost)

    hm = _ChaosMarket(market, transform) if transform is not None else market
    h = Harness(cfg, hm, exchange, tap, core_factory, log, commands=[(1.0, Command("lawok", ("all",), "sim"))],
                timesrc=vt, probes=list(probes))
    res = h.run()
    return h, res, log, exchange


class ReviewRobustSim(unittest.TestCase):
    def common_checks(self, h, res, log, exchange) -> None:
        ex = res["exchange"]
        core = res["core"]
        self.assertEqual(core["exceptions"], 0, [f for k, f in log.events if k == "exception"][:3])
        self.assertFalse(res["exceptions"])
        self.assertEqual(sum(ex["violations"].values()), 0, ex["violations"])
        self.assertEqual(core["resets"]["confirmed"], 2, core["resets"])
        self.assertEqual(core["resets"]["provisional"], 0, core["resets"])
        self.assertEqual(ex["live_orders"], 0)
        s1 = {(o["season"], o["symbol"]) for oid, o in ex["orders"].items()
              if (res["orders"].get(oid) or {}).get("strategy") == "s1r3"}
        self.assertTrue({s for s, _ in s1} >= {"224", "225"}, "S1-r3 did not fire in both round 3s")
        self.assertEqual({s: q for s, q in h.core.positions.pos.items() if q},
                         {s: q for s, q in exchange.positions().items() if q})

    # ------------------------------------------------------------------ regression guards (pass)
    def test_feed_chaos_two_connections_with_drops(self) -> None:
        """A second connection lagging 0-0.3 s; each copy of every frame dropped with p = 0.20."""
        def tf(frames):
            rng = random.Random(7)
            out = []
            for f in frames:
                k = f.conn.split("#")[1]
                if f.control is not None:
                    out += [f, _mf(f.t + 0.01, {}, f"dup#{k}", control=f.control, recv_wall=f.recv_wall)]
                    continue
                if rng.random() >= 0.20:
                    out.append(f)
                if rng.random() >= 0.20:
                    out.append(_mf(f.t + rng.uniform(0, 0.3), f.frame, f"dup#{k}", recv_wall=f.recv_wall))
            return out
        h, res, log, ex = _run(tf)
        self.common_checks(h, res, log, ex)
        self.assertGreater(h.core.normalizer.stats()["dup_news"], 500)

    def test_feed_chaos_single_connection_drops_and_duplicates(self) -> None:
        """One connection: 10% of news (snapshots included) dropped, half of the rest repeated 0-8 s later."""
        def tf(frames):
            rng = random.Random(3)
            out = []
            for f in frames:
                if _is_news(f):
                    if rng.random() < 0.10:
                        continue
                    if rng.random() < 0.5:
                        out.append(_mf(f.t + rng.uniform(0, 8.0), f.frame, f.conn, recv_wall=f.recv_wall))
                out.append(f)
            return out
        h, res, log, ex = _run(tf)
        self.common_checks(h, res, log, ex)
        self.assertGreater(len(res["core"]["relevant_snapshots"]), len(BASE_RELEVANT))

    # ------------------------------------------------------------------ findings (fail on the reviewed code)
    def test_malformed_snapshot_mid_round_is_not_a_season_reset(self) -> None:
        """One copy of a 225 r2 storm snapshot with ``last_seq: null`` (normalize.py:420-421)."""
        def tf(frames):
            out, done, last = [], False, None
            for f in frames:
                if _is_news(f) and f.frame["message"].get("type") == "snapshot":
                    last = f
                if not done and f.t >= 1950.0 and last is not None:
                    done = True
                    m = copy.deepcopy(last.frame["message"])
                    m["last_seq"] = None
                    m["time"] = m["time"][:-2] + "07"
                    out.append(_mf(f.t, {"type": "news", "message": m}, f.conn, recv_wall=f.recv_wall))
                out.append(f)
            return out
        h, res, log, ex = _run(tf)
        self.assertEqual(res["core"]["resets"]["confirmed"], 2, res["core"]["resets"])

    def test_slow_gets_keep_positions_in_sync(self) -> None:
        """Every REST result 2.1 s late: GETs still inside exchange.timeout_get_s (3 s), but beyond the 2-s
        reconcile cycle limit (core.py:63, reconcile.py:40)."""
        box = {}

        def probe(core, t):
            return ({s: q for s, q in core.positions.pos.items() if q},
                    {s: q for s, q in box["ex"]._season.pos.items() if q})

        def tf(frames):
            return frames
        # the exchange is built inside _run; reach it through the probe's closure
        import sim.exchange as SX
        orig_init = SX.SimExchange.__init__

        def init(self, *a, **kw):
            orig_init(self, *a, **kw)
            box["ex"] = self
        SX.SimExchange.__init__ = init
        # hardened criterion: with every answer 2.1 s late the bot cannot know a fill of the last ~2 s, and the
        # quiet-symbol guard skips rows while PLACEs are in flight, so mid-storm (t=1000, 2000) the bot's position
        # must equal the exchange's position of at most 10 s earlier, and at quiet times (after 225 r1's and r2's
        # flatten: 1520 s, 2480 s) it must equal it exactly. On the reviewed code no cycle ever completed and the
        # bot stayed flat.
        mid = [1000.0 - 0.25 * k for k in range(41)] + [2000.0 - 0.25 * k for k in range(41)]
        quiet = [1520.0, 2480.0]
        try:
            h, res, log, ex = _run(tf, sets=["sim.api_latency_s=2.1"], probes=[(t, probe) for t in mid + quiet])
        finally:
            SX.SimExchange.__init__ = orig_init
        got = {round(p["t"], 2): p["value"] for p in res["probes"]}
        for t in quiet:
            bot, exch = got[t]
            self.assertEqual(bot, exch, f"t={t}: bot positions {bot} vs exchange {exch}")
        for t in (1000.0, 2000.0):
            bot = got[t][0]
            past = [round(t - 0.25 * k, 2) for k in range(41)]
            self.assertTrue(any(got[u][1] == bot for u in past),
                            f"t={t}: bot positions {bot} match no exchange position of the last 10 s")
        self.assertGreater(h.core.reconciler.counters["cycles"], 50)        # season 226 (partial) alone


if __name__ == "__main__":
    unittest.main()
