"""Robustness review (lens: failure handling): fast reproductions of the failures found by fault injection.

Every test here asserts the behaviour the bot should have. Each one FAILS on the code as reviewed; together they
document the review findings and turn green when the fixes land. Run from BOT with

    SYS/.venv/bin/python -m unittest discover -s tests -p "review_robust_*.py" -v

(the default ``test*.py`` discovery pattern does not pick this file up, so the main suite is unaffected).
Offline only; temporary files go under ``tests/_tmp`` and are removed.
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
from unittest import mock

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT)
sys.path.insert(0, TESTS)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from ctcbot.types import (ApiKind, ApiResult, Command, ListLog, OrderState, SeasonResetEvent,  # noqa: E402
                          ServerTime, SourceFrame)

D = "1802-05-10"


def _mtime(t: float, date: str = D) -> str:
    secs = 34200.0 + 30.0 * t
    h, rem = divmod(secs, 3600.0)
    m, s = divmod(rem, 60.0)
    return f"{date}T{int(h):02d}:{int(m):02d}:{int(s):02d}"


def _news(m: dict, mono: float, conn: str = "ws0#1") -> SourceFrame:
    return SourceFrame(frame={"type": "news", "message": m}, recv_wall=1.8e9 + mono, recv_mono=mono, conn=conn)


# ============================================================================ feed normalizer
class TestNormalizerSeasonResets(unittest.TestCase):
    def setUp(self) -> None:
        from ctcbot.config import load_config
        from ctcbot.normalize import FeedNormalizer
        self.log = ListLog()
        self.n = FeedNormalizer(load_config(), self.log)

    def feed(self, sf: SourceFrame) -> list:
        return self.n.ingest(sf) + self.n.poll(sf.recv_mono)

    def resets(self, evs: list) -> list[SeasonResetEvent]:
        return [e for e in evs if isinstance(e, SeasonResetEvent)]

    def test_snapshot_without_usable_last_seq_is_not_a_season_reset(self) -> None:
        """normalize.py:420-421 maps a missing / null / non-int last_seq to -1, which is the confirmed season-start
        trigger (b): one malformed snapshot mid-round cancels everything (KILL) and resets every per-season object.
        In the recording replay this cost 3.7k in season 225 (review evidence: malformed.py snap_null_seq)."""
        for bad in (None, "n/a", "MISSING"):
            with self.subTest(last_seq=bad):
                self.setUp()
                evs = []
                for k in range(80):
                    evs += self.feed(_news({"type": "storm", "round": 2, "storm": "Rex", "time": _mtime(60 + 2 * k),
                                            "seq": 300 + k}, 100.0 + 2 * k))
                snap = {"type": "snapshot", "round": 2, "storm": "Rex", "time": _mtime(221), "phase": "storm"}
                if bad != "MISSING":
                    snap["last_seq"] = bad
                evs += self.feed(_news(snap, 300.0))
                evs += self.n.poll(302.0)
                self.assertEqual(self.resets(evs), [], f"last_seq={bad!r} produced a season reset")
                self.assertEqual(self.n.epoch, 0)

    def test_previous_season_message_after_a_reset_does_not_reset_again(self) -> None:
        """A message of the old season that arrives after the new season's -1 snapshot (a catch-up straddling the
        boundary) is accepted into the new epoch: it sets max_seq / round_hi / days, and the new season's genuine
        round-1 storm_formed (seq 0) then reads as a second new season. Review evidence: dupburst replay, an extra
        storm_formed_r1 reset 60 s into 225 r1 with 11 position_mismatch alerts."""
        old = "1803-05-11"
        evs = []
        for k in range(120):
            evs += self.feed(_news({"type": "storm", "round": 3, "storm": "Omar", "time": _mtime(60 + 2 * k, old),
                                    "seq": 700 + k}, 10.0 + 2 * k))
        evs += self.feed(_news({"type": "snapshot", "round": 1, "time": _mtime(0), "phase": "pre_storm",
                                "last_seq": -1}, 400.0))
        first = self.resets(evs)
        self.assertEqual([e.reason for e in first], ["snapshot_last_seq"])
        evs = self.feed(_news({"type": "storm", "round": 3, "storm": "Omar", "time": _mtime(300, old),
                               "seq": 815}, 401.0))                        # late copy from the old season
        evs += self.feed(_news({"type": "storm_formed", "round": 1, "storm": "Pia", "time": _mtime(60),
                                "seq": 0, "headline": "Pia formed"}, 460.0))
        evs += self.feed(_news({"type": "snapshot", "round": 1, "storm": "Pia", "time": _mtime(61), "phase": "storm",
                                "last_seq": 0}, 461.0))
        evs += self.n.poll(463.0)
        self.assertEqual(self.resets(evs), [], "a stale old-season message caused a second season reset")


# ============================================================================ order manager
class TestOrderManagerStepIsAtomic(unittest.TestCase):
    def setUp(self) -> None:
        from ctcbot.exec import order_manager, risk
        import test_b_support as B
        self.B = B
        for mod in (risk, order_manager):
            p = mock.patch.object(mod, "_scaled_limits", B.fake_strategy_limits)
            p.start()
            self.addCleanup(p.stop)
        self.log = ListLog()
        self.rig = B.Rig(self.log)
        self.rig.set_book("PORT", 99.9, 100.1)
        self.rig.set_book("ISLE", 59.9, 60.1)

    def test_one_bad_intent_does_not_strand_other_orders(self) -> None:
        """order_manager.py:227-258: step() builds requests strategy by strategy; an exception in a later intent
        (here a TargetIntent with ttl_s=None) propagates out of step(), so the PLACE already built for PORT is never
        returned: its record stays SENDING forever (it blocks PORT clips for the season) and
        positions._inflight['PORT'] never decrements (the quiet-symbol guard then skips PORT's /positions rows for
        the rest of the run, across season resets: positions.py:44, 82-86). Review evidence: strategy_chaos.py
        bad_intent, PORT/REBLD stuck SENDING from 1950 s to the end of season 225."""
        from ctcbot.types import Priority, TargetIntent
        good = TargetIntent(strategy="s3", symbol="PORT", target=200, clip_qty=16, clip_interval_s=2.0, limit_buy=100.5,
                            limit_sell=None, priority=Priority.TAKE, ttl_s=2.0, edge_usd_per_share=0.2)
        bad = TargetIntent(strategy="s3", symbol="ISLE", target=50, clip_qty=10, clip_interval_s=2.0, limit_buy=59.9,
                           limit_sell=None, priority=Priority.TAKE, ttl_s=None, edge_usd_per_share=0.0)  # type: ignore
        self.rig.om.set_intents([good, bad], 80.0)
        try:
            reqs = self.rig.om.step(80.0, self.B.clock(80.0), self.B.feed())
        except Exception:  # noqa: BLE001 - the defect: the exception escapes and the requests are lost
            reqs = []
        sent = {r.local_id for r in reqs if r.kind == ApiKind.PLACE}
        stranded = [r.local_id for r in self.rig.store.live() if r.state == OrderState.SENDING and
                    r.local_id not in sent]
        self.assertEqual(stranded, [], "records left SENDING with no request emitted")
        n_port = sum(1 for r in reqs if r.kind == ApiKind.PLACE and r.symbol == "PORT")
        self.assertEqual(self.rig.positions._inflight.get("PORT", 0), n_port)


# ============================================================================ core: reconcile, persist, eng restart
class TestReconcileUnderSlowGets(unittest.TestCase):
    def test_get_latency_above_two_seconds_is_detected(self) -> None:
        """core.py:63 (_RECONCILE_STALE_S = 2.0) and reconcile.py:40 (CYCLE_TIMEOUT_S = 2.0) drop every cycle whose
        GETs take longer than 2 s, although exchange.timeout_get_s is 3 s; and the reconcile_down hold is only
        evaluated when a cycle completes (core.py:1187-1199), so a never-completing reconcile raises no hold and no
        alert. With 2.1-s GETs the bot never learns a fill (positions frozen, CANCEL_ACKED remainders never leave
        room). Review evidence: latency.py 2.1, 1,900 dropped cycles, bot flat vs 12 exchange positions."""
        mods = testpaths.integration("ctcbot.config", "ctcbot.case", "ctcbot.models.cost_model", "ctcbot.app.core")
        config, case_mod, cm, core_mod = mods
        import test_d_core as Dc
        cfg = config.load_config()
        case = case_mod.CaseInfo.from_file(testpaths.require(testpaths.RESEARCH_CASE))
        cost = cm.CostModel(testpaths.require(testpaths.COST_MODEL), cfg)
        log = ListLog()
        core = core_mod.BotCore(cfg, case, case_mod.diff_case(case.raw, case.raw), Dc.FakeEngine(Dc.CallLog()),
                                Dc._Time(), log, mode="sim", run_dir=None, run_id="rr", laws_module=None, cost=cost)
        core.start(0.0)
        pending: list[tuple[float, object]] = []
        lat = 2.1
        t = 0.0
        while t <= 20.0 + 1e-9:
            for req in core.take_requests():
                pending.append((t + lat, req))
            due = [(d, r) for d, r in pending if d <= t + 1e-9]
            pending = [(d, r) for d, r in pending if d > t + 1e-9]
            for d, req in due:
                if req.kind == ApiKind.GET_OPEN:
                    res = ApiResult(req=req, status=200, data=[], parsed=[], error=None, sent_mono=d - lat, done_mono=d)
                elif req.kind == ApiKind.GET_POSITIONS:
                    res = ApiResult(req=req, status=200, data=[], parsed={}, error=None, sent_mono=d - lat, done_mono=d)
                else:
                    continue
                core.on_api_result(res, d)
            core.run_job("reconcile", t)
            core.run_job("tick", t)
            t = round(t + 0.25, 6)
        completed = core.reconciler.counters["cycles"]
        held = "reconcile_down" in core.risk.holds(t)
        self.assertTrue(completed > 0 or held, f"cycles completed {completed}, reconcile_down hold {held}")


class TestCoreFailureCoupling(unittest.TestCase):
    def setUp(self) -> None:
        import test_d_core as Dc
        self.Dc = Dc
        self.dir = testpaths.tmpdir("review_robust")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_persist_failures_do_not_stop_trading(self) -> None:
        """core.py:1271-1277 counts a failed state.json write as a core exception; at app.persist_s = 5 s that is 12
        per minute, above app.exceptions_per_min_hold (10), so a full disk or an unwritable run dir holds all new
        risk for as long as it lasts. Review evidence: diskfull.py, 63 'exceptions' holds, 225 P&L -511 vs 12,271."""
        core, fk = self.Dc.make_core(mode="replay", run_dir=self.dir)
        with mock.patch("ctcbot.app.core.save_state", side_effect=OSError(28, "No space left on device")):
            for k in range(13):
                core.run_job("persist", 5.0 * k)
        self.assertNotIn("exceptions", fk.risk.holds(60.0))

    def test_one_odd_competition_id_does_not_reset_the_season(self) -> None:
        """core.py:950-976: the first GET_TIME with another competition_id forces a confirmed reset (KILL cancel-all,
        engine, positions and store reset), and the next normal answer forces a second one. Nothing on the feed
        corroborates it. Review evidence: compflip.py, one flipped answer at 1950 s -> 2 extra resets, 14
        position_mismatch alerts, 225 P&L 8,085 vs 11,739."""
        core, fk = self.Dc.make_core(mode="sim")
        t = 1.0
        for cid in ("comp-1", "comp-1", "other-backend", "comp-1", "comp-1"):
            core.run_job("clock_poll", t)
            self.Dc.answer_all(core, [r for r in core.take_requests() if r.kind == ApiKind.GET_TIME], t, comp_id=cid)
            t += 5.0
        self.assertEqual(core.report()["resets"]["confirmed"], 0, core.report()["resets"])

    def test_failed_eng_restart_keeps_the_running_engine(self) -> None:
        """core.py:1576-1580 closes the running engine before building its replacement; when the rebuild raises
        (for example no engine log yet right after a season reset), the core is left with a closed engine: no fair
        values for the rest of the season."""
        core, fk = self.Dc.make_core(mode="replay", run_dir=self.dir)

        def broken(**_):
            raise FileNotFoundError("engine log missing")

        core._fac["engine"] = broken
        core.on_command(Command("eng", ("restart",), source="console"), 1.0)
        self.assertEqual(fk.engine.closed, 0, "the working engine was closed although its replacement failed")


# ============================================================================ restart: H1 round-1 check
class TestH1CheckSurvivesRestart(unittest.TestCase):
    def test_restart_after_round1_check_keeps_the_result(self) -> None:
        """h1.py:85-100 persists check_result but not that the check ran; after a restart past 90 s of round 1
        the new H1 re-runs _round1_check (h1.py:302) with an empty fill list, the check fails and overwrites the
        saved pass, so rounds 2-3 run at size_on_fail. Review evidence: restart_sim.py 900 1 1, 225 P&L 8,697 vs
        12,275 with the check kept."""
        from test_c_h1 import H1Test
        from test_c_support import fill, make_deps
        from ctcbot.strategies.h1 import H1
        from ctcbot.types import Side
        t = H1Test("test_check_pass_sizes_rounds_2_3")
        t.setUp()
        t.run_round1(fills=[fill("PORT", Side.BUY, 50, 45.05, ref_mid=45.0, expected=20.0),
                            fill("HAVEN", Side.BUY, 62, 40.04, ref_mid=40.0, expected=20.0)])
        self.assertTrue(t.h1.check_result)
        saved = t.h1.state_dict()
        t.h1 = H1(make_deps(t.cfg))            # the restarted process
        t.h1.load_state(saved)
        t.step(97.0)                           # first decision after the restart, round 1, past check_t1
        self.assertTrue(t.h1.check_result, "the restarted H1 re-ran the round-1 check and overwrote the pass")
        t.step(48.0, round_=2)
        self.assertEqual(t.step(50.0, round_=2)["PORT"].target, 200)


# ============================================================================ clock
class TestClockStaleServer(unittest.TestCase):
    def test_frozen_server_time_does_not_freeze_the_clock(self) -> None:
        """clock.py:270-280: a fresh server sample always wins over storm observations, with no cross-check. A
        /competitions/time that keeps answering "running" with a stale fictional_time (cache, server bug) froze
        the bot at t = 138.5 s for the whole of 225 r2 while storm observations every 2 s showed time advancing;
        round-end flatten never ran. Review evidence: stale_time.py 1900 2600."""
        from ctcbot.case import Schedule
        from ctcbot.clock import ClockModel
        from ctcbot.config import load_config
        cfg = load_config()
        timing = {"pre_storm": 60, "storm": 480, "aftermath": 240, "obs_every": 2, "track_every": 20,
                  "advisory_first": 30, "advisory_every": 60, "prelim_report": 30, "final_report": 40}
        clk = ClockModel(Schedule(timing, cfg.get("cost.phases")), cfg, ListLog())
        open_mono = 1000.0
        frozen = None
        now = open_mono + 60.0
        while now <= open_mono + 300.0 + 1e-9:
            t = now - open_mono
            if abs((t / 2.0) - round(t / 2.0)) < 1e-9:
                clk.observe_feed(_mtime(t), 1, now + 0.05, "storm", 0)
            if abs(t - round(t)) < 1e-9:
                ft = _mtime(t) if t < 120.0 else (frozen or _mtime(120.0))
                frozen = frozen or (_mtime(120.0) if t >= 120.0 else None)
                clk.observe_server(ServerTime("c1", "running", ft, t), now - 0.02, now + 0.02)
            now = round(now + 0.5, 6)
        r = clk.read(open_mono + 300.0)
        self.assertTrue(r.known)
        self.assertLess(abs(r.t - 300.0), 2.0, f"clock t={r.t} (quality {r.quality}) while the feed says 300 s")


if __name__ == "__main__":
    unittest.main()
