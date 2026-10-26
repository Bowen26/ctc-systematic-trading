"""Hardening tests (DESIGN.md §13): the behaviours the hardening pass added that the review_* tests do not cover.

The review tests (``review_*.py``, run with ``-p "review_*.py"``) reproduce the reviewers' findings; this file is in
the default ``test*.py`` discovery and guards the rest of each fix:

* order manager: ``valid_until``, the H1 slot grid under latency, ``first_clip_cost_bp``, unsent-cancel backoff
  and refund, a malformed intent costing only itself;
* risk: the from-mid cap on the aggressive side; the REIN ask's second order within one cycle;
* executor: a PLACE that waited too long in the queue is answered ``stale_queue`` (and refunded), a cancel is not;
* WebSocket: a rejected handshake (401/403/429) backs off instead of retrying every 0.5 s;
* normalizer: a late copy of the previous season is dropped, but never a new season that reuses the calendar;
* clock: a server ahead of a delayed feed is still trusted; a server behind the feed is not;
* core: persist failures, ``eng restart``, persist debounce, the reconcile stale hold, the engine-slow alert,
  repeated ``k``, ``cx`` reporting, the comp_id candidate;
* runner: automatic disable cancels and re-enables after the cooldown, a limited number of times per season;
* config and cli: live bounds, the KILL budget validation;
* shell: SIGHUP is handled like SIGINT.
"""
from __future__ import annotations

import asyncio
import os
import random
import shutil
import signal
import sys
import threading
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.dirname(HERE)
sys.path.insert(0, BOT)
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

from ctcbot.types import (SYMBOLS, ApiKind, ApiRequest, ApiResult, CancelIntent, Command, ListLog,  # noqa: E402
                          OrderAck, OrderState, Priority, RestIntent, ServerTime, Side, SourceFrame, TargetIntent)

S = OrderState


# ================================================================================================ order manager
from test_b_order_manager import OMBase, kinds, rest, tgt  # noqa: E402
from test_b_support import FakeLimiter, Rig, clock, feed, result  # noqa: E402


class TestOrderManagerHardening(OMBase):
    def test_valid_until_stops_clips_at_the_window_end(self):
        it = TargetIntent(strategy="s3", symbol="PORT", target=200, clip_qty=16, clip_interval_s=2.0, limit_buy=100.5,
                          limit_sell=None, priority=Priority.TAKE, ttl_s=2.0, valid_until=540.0)
        self.rig.om.set_intents([it], 539.75)
        self.assertEqual(self.step(540.0), [])                    # the intent is still young, but the window ended
        self.assertEqual(self.rig.om.stats()["expired_targets"], 1)
        self.rig.om.set_intents([it], 539.5)
        self.assertEqual(kinds(self.step(539.5)), [ApiKind.PLACE])

    def test_slot_grid_keeps_four_h1_clips_under_latency(self):
        """0.6-s acks: with a free-running 2-s interval the clips drift to 50.0, 52.6, 55.2, 57.8 (the 4th misses
        the [50, 57) window); on the grid they go at 50.0, 52.6, 54.6, 56.6."""
        lat, anchor, sends = 0.6, 50.0, []
        pending: list[tuple[float, ApiRequest, str]] = []
        t = 50.0
        while t < 57.0 - 1e-9:
            for due, req, what in sorted(pending, key=lambda x: x[0]):
                if due <= t + 1e-9:
                    pending.remove((due, req, what))
                    if what == "place":
                        self.ack_open(req, due, filled=req.qty // 2)       # half fills, the rest rests
                    else:
                        self.ack_ok(req, due)
                    for r in self.rig.om.step(due, clock(due), feed()):
                        pending.append((due + lat, r, "place" if r.kind == ApiKind.PLACE else "cancel"))
                        if r.kind == ApiKind.PLACE:
                            sends.append(round(due, 2))
            it = TargetIntent(strategy="h1", symbol="PORT", target=200, clip_qty=50, clip_interval_s=2.0,
                              limit_buy=100.5, limit_sell=None, priority=Priority.TAKE, ttl_s=2.0, valid_until=57.0,
                              slot_anchor=anchor)
            self.rig.om.set_intents([it], t)
            for r in self.rig.om.step(t, clock(t), feed()):
                pending.append((t + lat, r, "place" if r.kind == ApiKind.PLACE else "cancel"))
                if r.kind == ApiKind.PLACE:
                    sends.append(round(t, 2))
            t = round(t + 0.25, 2)
            self.rig.positions.pos["PORT"] = sum(r.filled_qty for r in self.rig.store.all() if r.symbol == "PORT")
        self.assertEqual(len(sends), 4, sends)
        self.assertLess(sends[-1], 57.0)
        self.assertTrue(all(abs(s - round(s / 2.0) * 2.0) <= lat + 0.3 for s in sends), sends)

    def test_first_clip_cost_only_without_a_recent_same_side_clip(self):
        def it(t):
            return TargetIntent(strategy="s3", symbol="PORT", target=200, clip_qty=16, clip_interval_s=2.0,
                                limit_buy=100.5, limit_sell=None, priority=Priority.TAKE, ttl_s=2.0,
                                expected_cost_bp=60.0, first_clip_cost_bp=24.0)
        r1 = self.step(80.0, [it(80.0)])[0]
        self.ack_open(r1, 80.1, filled=16)
        self.rig.positions.pos["PORT"] = 16
        r2 = self.step(82.0, [it(82.0)])[0]
        self.ack_open(r2, 82.1, filled=16)
        self.rig.positions.pos["PORT"] = 32
        r3 = self.step(100.0, [it(100.0)])[0]                    # 18 s later: a first clip again
        costs = [self.rec_of(r).expected_cost_bp for r in (r1, r2, r3)]
        self.assertEqual(costs, [24.0, 60.0, 24.0])

    def test_unsent_cancel_backs_off_and_refunds(self):
        from ctcbot.ratelimit import ActionLimiter
        lim = ActionLimiter(150, 61.0, {Priority.KILL: 0, Priority.SNIPE: 1, Priority.HEDGE: 4, Priority.TAKE: 10},
                            20, 150)
        rig = self.make_rig(limiter=lim)
        p = self.step(80.0, [tgt()], rig=rig)[0]
        self.ack_open(p, 80.1, rig=rig)
        c = self.step(82.0, rig=rig)[0]
        used = lim.used(82.0)
        rig.om.on_result(result(c, None, sent=False, error="not sent: connect failed"), 82.0)
        self.assertEqual(lim.used(82.0), used - 1)                # the unsent cancel's ticket came back
        self.assertEqual(rig.om.step(82.0, clock(82.0), feed()), [])     # no re-send in the step after the result
        delays = []
        t = 82.0
        for _ in range(5):                                        # 0.25, 0.5, 1.0, 1.0, 1.0
            while True:
                t = round(t + 0.05, 2)
                out = [r for r in rig.om.step(t, clock(t), feed()) if r.kind == ApiKind.CANCEL]
                if out:
                    break
            delays.append(t)
            rig.om.on_result(result(out[0], None, sent=False, error="not sent"), t)
        gaps = [round(b - a, 2) for a, b in zip([82.0] + delays, delays)]
        self.assertEqual(gaps, [0.25, 0.5, 1.0, 1.0, 1.0])
        self.assertEqual(lim.used(t), used - 1)

    def test_bad_intent_costs_only_itself(self):
        good = tgt("PORT")
        bad = TargetIntent(strategy="s3", symbol="ISLE", target=50, clip_qty=0, clip_interval_s=2.0, limit_buy=60.5,
                           limit_sell=None, priority=Priority.TAKE, ttl_s=2.0)
        worse = RestIntent(strategy="rein_ask", key="rein_ask", symbol="REIN", side=Side.SELL, price=float("nan"),
                           priority=Priority.HEDGE)
        out = self.step(80.0, [good, bad, worse])
        self.assertEqual([(r.kind, r.symbol) for r in out], [(ApiKind.PLACE, "PORT")])
        self.assertEqual(self.rig.om.stats()["bad_intents"], 2)

    def test_exception_in_one_target_keeps_the_others(self):
        good, other = tgt("PORT"), tgt("ISLE", lb=60.5)
        orig = self.rig.om._do_target

        def boom(ti, *a):
            if ti.symbol == "ISLE":
                raise RuntimeError("strategy bug")
            return orig(ti, *a)
        self.rig.om._do_target = boom
        out = self.step(80.0, [good, other])
        self.assertEqual([r.symbol for r in out], ["PORT"])
        self.assertEqual([r.state for r in self.rig.store.live()], [S.SENDING])
        self.assertEqual(self.rig.om.stats()["step_errors"], 1)

    def test_repeated_kill_is_a_rearm(self):
        self.rig.risk.kill("t", 90.0)
        first = self.rig.om.kill(90.0)
        self.assertEqual(kinds(first), [ApiKind.CANCEL_ALL])
        self.assertEqual(self.rig.om.kill(90.1), [])               # in flight: no second CANCEL_ALL
        self.rig.om.on_result(result(first[0], 200, parsed={}), 90.2)
        again = self.rig.om.kill(90.6)                            # follow-up gap passed: one budgeted re-send
        self.assertEqual(kinds(again), [ApiKind.CANCEL_ALL])
        self.assertIsNotNone(again[0].ticket)

    def test_inflight_guard_expires(self):
        self.rig.positions.note_place("PORT", sent=True, now=0.0)
        self.assertEqual(self.rig.positions.quiet_stamp()["PORT"][0], 1)
        self.step(5.0)
        self.assertEqual(self.rig.positions.quiet_stamp()["PORT"][0], 1)
        self.step(11.0)
        self.assertEqual(self.rig.positions.quiet_stamp()["PORT"][0], 0)


# ================================================================================================ risk
from review_safety_price import _rec  # noqa: E402
from test_b_support import BTestCase  # noqa: E402


class TestRiskHardening(BTestCase):
    def test_aggressive_side_cap_but_passive_far_orders_pass(self):
        rig = Rig(self.log)
        rig.set_book("PORT", 49.0, 51.0, 0.0)
        bv = rig.books.view("PORT", 0.0)
        ck = rig.risk.check
        self.assertEqual(ck(_rec(Side.BUY, 50.70), bv, 0, 200, clock(30.0), feed(), 0.0)[1], "ok")   # 1.4 %
        self.assertEqual(ck(_rec(Side.SELL, 49.10), bv, 0, 200, clock(30.0), feed(), 0.0)[1], "marketable_far")
        self.assertEqual(ck(_rec(Side.SELL, 50.90), bv, 0, 200, clock(30.0), feed(), 0.0)[1], "ok")  # passive side
        self.assertEqual(ck(_rec(Side.BUY, 70.0, Priority.SNIPE), bv, 0, 200, clock(30.0), feed(), 0.0)[1], "ok")


class TestReinAskTopUp(unittest.TestCase):
    def test_second_order_follows_within_one_cycle(self):
        """Risk caps a HEDGE order at 0.5 L, so the ask needs two orders; the second follows within a cycle
        instead of at the next 5-s refresh (review spec finding 7)."""
        from test_c_support import FakeConfig, make_ctx, make_deps, season
        from ctcbot.strategies.rein_ask import ReinAsk
        from ctcbot.types import Trigger, WorkingSummary
        cfg = FakeConfig()
        ra = ReinAsk(make_deps(cfg))

        def step(t, sell_qty=None, pos=0, **kw):
            work = {} if sell_qty is None else {"REIN": WorkingSummary("REIN", 0, sell_qty, 0, 0, 1, False,
                                                                       frozenset({"rein_ask"}))}
            return ra.decide(make_ctx(t, 1, cfg=cfg, now=1000.0 + t, seasoninfo=season(), work=work,
                                      positions={"REIN": pos}, **kw))
        self.assertEqual(len(step(0.5, trigger=Trigger.ROUND_START)), 1)
        self.assertEqual(len(step(0.75, sell_qty=100)), 1)            # 100 of 200 offered: re-emit at once
        self.assertEqual(step(1.0, sell_qty=200), [])                 # all room offered: wait for the refresh
        self.assertEqual(step(1.25, sell_qty=100, pos=-100), [])      # 100 sold: the room is 100, all offered
        self.assertEqual(step(5.5, sell_qty=200), [])
        self.assertEqual(len(step(5.75, sell_qty=200)), 1)            # the 5-s refresh (from the last emit)


# ================================================================================================ executor
class _SlowGetAPI:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.seen: list[ApiKind] = []

    def execute(self, req: ApiRequest) -> ApiResult:
        if req.kind == ApiKind.GET_TIME:
            self.release.wait(3.0)
        else:
            self.seen.append(req.kind)
        t = time.monotonic()
        return ApiResult(req=req, status=200, data={}, parsed=OrderAck("o", "open", 0, 1, None), error=None,
                         sent_mono=t, done_mono=t, sent=True)


class TestExecutorQueueAge(unittest.TestCase):
    def test_old_place_is_answered_stale_cancel_still_goes(self):
        from ctcbot.rest import RestExecutor
        api = _SlowGetAPI()
        got: list[ApiResult] = []
        lock = threading.Lock()

        def on(res):
            with lock:
                got.append(res)
        ex = RestExecutor(api, 1, on, ListLog(), kill_workers=1, max_queue_age_s=0.2)
        try:
            ex.submit(ApiRequest(req_id=1, kind=ApiKind.GET_TIME, priority=Priority.HEDGE))
            time.sleep(0.05)
            ex.submit(ApiRequest(req_id=2, kind=ApiKind.PLACE, priority=Priority.TAKE, symbol="PORT", side=Side.BUY,
                                 qty=1, price=50.0))
            ex.submit(ApiRequest(req_id=3, kind=ApiKind.CANCEL, priority=Priority.HEDGE, order_id="o9"))
            time.sleep(0.4)
            api.release.set()
            end = time.monotonic() + 3.0
            while time.monotonic() < end and len(got) < 3:
                time.sleep(0.01)
        finally:
            api.release.set()
            ex.close(1.0)
        by = {r.req.req_id: r for r in got}
        self.assertEqual((by[2].sent, by[2].error), (False, "stale_queue"))
        self.assertTrue(by[3].sent)
        self.assertEqual(api.seen, [ApiKind.CANCEL])


# ================================================================================================ websocket
from test_a_sources import KEY, FakeConnect, FakeWS, cfg_with, run_for  # noqa: E402


class _Rejected(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"server rejected WebSocket connection: HTTP {code}")
        self.response = types.SimpleNamespace(status_code=code)


class TestWsHandshakeBackoff(unittest.TestCase):
    def _run(self, plan):
        from ctcbot.sources import WsSingleSource
        from ctcbot.timesource import RealTime
        sleeps: list[float] = []
        log = ListLog()

        async def fake_sleep(d):
            sleeps.append(d)
            await asyncio.sleep(0)
        src = WsSingleSource(cfg_with(), KEY, SYMBOLS, RealTime(), log, lambda: 10.0, connect=FakeConnect(plan),
                             sleep=fake_sleep, rng=random.Random(5))
        asyncio.run(run_for(src, 0.2))
        return sleeps, log

    def test_401_retries_every_5_s_and_alerts(self):
        sleeps, log = self._run([_Rejected(401)] * 3 + [FakeWS([], after="stall")])
        self.assertEqual(sleeps[:3], [5.0, 5.0, 5.0])
        self.assertTrue([a for a in log.kinds("alert") if a.get("alert") == "ws_rejected"])

    def test_429_backs_off_exponentially_to_10_s(self):
        sleeps, _ = self._run([_Rejected(429)] * 6 + [FakeWS([], after="stall")])
        self.assertEqual(sleeps[:6], [1.0, 2.0, 4.0, 8.0, 10.0, 10.0])

    def test_plain_drops_keep_the_fast_ladder(self):
        sleeps, log = self._run([OSError("refused")] * 3 + [FakeWS([], after="stall")])
        self.assertEqual(sleeps[0], 0.25)
        self.assertTrue(all(d <= 0.6 for d in sleeps[:3]))
        self.assertFalse(log.kinds("alert"))


# ================================================================================================ normalizer
def _mt(t: float, date: str) -> str:
    secs = 34200.0 + 30.0 * t
    h, rem = divmod(secs, 3600.0)
    m, s = divmod(rem, 60.0)
    return f"{date}T{int(h):02d}:{int(m):02d}:{int(s):02d}"


def _nf(m: dict, mono: float) -> SourceFrame:
    return SourceFrame(frame={"type": "news", "message": m}, recv_wall=1.8e9 + mono, recv_mono=mono, conn="ws0#1")


class TestNormalizerHardening(unittest.TestCase):
    def setUp(self) -> None:
        from ctcbot.config import load_config
        from ctcbot.normalize import FeedNormalizer
        self.n = FeedNormalizer(load_config(), ListLog())

    def feed(self, m: dict, mono: float) -> list:
        return self.n.ingest(_nf(m, mono)) + self.n.poll(mono)

    def _season(self, date: str, mono0: float, rounds=(3,), seq0: int = 700) -> float:
        mono = mono0
        for k in range(60):
            for r in rounds:
                self.feed({"type": "storm", "round": r, "storm": "Omar", "time": _mt(60 + 2 * k, date),
                           "seq": seq0 + k}, mono)
                mono += 2.0
        return mono

    def test_same_calendar_new_season_is_never_dropped(self):
        """A server that reuses one calendar for every season: the new season's messages carry the old (round,
        date) pairs and must all be accepted."""
        mono = self._season("1802-05-12", 10.0, rounds=(3,))
        self.feed({"type": "snapshot", "round": 1, "time": _mt(0, "1802-05-10"), "phase": "pre_storm",
                   "last_seq": -1}, mono)
        self.assertEqual(self.n.epoch, 1)
        evs = self.feed({"type": "storm_formed", "round": 1, "storm": "Pia", "time": _mt(60, "1802-05-10"),
                         "seq": 0, "headline": "Pia"}, mono + 60.0)
        # a round-3 message dated like the old season's round 3, 100 s into the new season: same calendar
        evs += self.feed({"type": "storm", "round": 3, "storm": "Pia", "time": _mt(70, "1802-05-12"), "seq": 1},
                         mono + 100.0)
        self.assertEqual(self.n.stats()["prev_season_dropped"], 0)
        self.assertEqual(self.n.epoch, 1)

    def test_old_calendar_copy_dropped_only_inside_the_merge_window(self):
        mono = self._season("1803-05-11", 10.0, rounds=(3,))
        self.feed({"type": "snapshot", "round": 1, "time": _mt(0, "1802-05-10"), "phase": "pre_storm",
                   "last_seq": -1}, mono)
        self.feed({"type": "storm", "round": 3, "storm": "Omar", "time": _mt(200, "1803-05-11"), "seq": 815},
                  mono + 1.0)
        self.assertEqual(self.n.stats()["prev_season_dropped"], 1)
        self.assertEqual(self.n.max_seq, -1)

    def test_bad_last_seq_is_a_bad_frame(self):
        for bad in (None, "x", -7, 1.5):
            evs = self.feed({"type": "snapshot", "round": 1, "time": _mt(10, "1802-05-10"), "phase": "storm",
                             "last_seq": bad}, 5.0)
            self.assertEqual(evs, [], bad)
        evs = self.feed({"type": "snapshot", "round": 1, "time": _mt(10, "1802-05-10"), "phase": "storm",
                         "last_seq": "12"}, 6.0)
        self.assertTrue(evs)                                      # an integer string is still accepted
        self.assertEqual(self.n.stats()["bad_last_seq"], 4)


# ================================================================================================ clock
class TestClockServerVsFeed(unittest.TestCase):
    def _clock(self):
        from ctcbot.case import Schedule
        from ctcbot.clock import ClockModel
        from ctcbot.config import load_config
        cfg = load_config()
        timing = {"pre_storm": 60, "storm": 480, "aftermath": 240, "prelim_report": 30, "final_report": 40}
        log = ListLog()
        return ClockModel(Schedule(timing, cfg.get("cost.phases")), cfg, log), log

    def test_server_ahead_of_a_delayed_feed_is_trusted(self):
        clk, log = self._clock()
        open_mono = 1000.0
        now = open_mono + 60.0
        while now <= open_mono + 200.0 + 1e-9:
            t = now - open_mono
            if abs(t / 2.0 - round(t / 2.0)) < 1e-9:
                clk.observe_feed(_mt(t - 4.0, "1802-05-10"), 1, now, "storm", 0)        # the feed lags 4 s
            if abs(t - round(t)) < 1e-9:
                clk.observe_server(ServerTime("c1", "running", _mt(t, "1802-05-10"), t), now - 0.01, now + 0.01)
            now = round(now + 0.5, 6)
        r = clk.read(open_mono + 200.0)
        self.assertEqual(r.quality, "server")
        self.assertLess(abs(r.t - 200.0), 0.5)
        self.assertFalse([a for a in log.kinds("alert") if a.get("alert") == "clock_server_stale"])


# ================================================================================================ core
class TestCoreHardening(unittest.TestCase):
    def setUp(self) -> None:
        import test_d_core as Dc
        self.Dc = Dc
        self.dir = testpaths.tmpdir("hardening")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def alerts(self, fk, kind):
        return [a for a in fk.log.kinds("alert") if a.get("alert") == kind]

    def test_persist_failure_alerts_once_per_minute(self):
        core, fk = self.Dc.make_core(mode="replay", run_dir=self.dir)
        with mock.patch("ctcbot.app.core.save_state", side_effect=OSError(28, "No space left on device")):
            for k in range(20):
                core.run_job("persist", 5.0 * k)
        self.assertEqual(len(self.alerts(fk, "persist_failed")), 2)       # at 0 s and 60 s
        self.assertEqual(core.report()["exceptions"], 0)

    def test_persist_debounce_after_an_order_change(self):
        core, fk = self.Dc.make_core(mode="replay", run_dir=self.dir)
        writes = []
        with mock.patch("ctcbot.app.core.save_state", side_effect=lambda p, d: writes.append(p)):
            core.run_job("tick", 0.25)
            n0 = len(writes)
            core._persist_dirty = True
            core.run_job("tick", 0.5)
            self.assertEqual(len(writes), n0 + 1)
            core._persist_dirty = True
            core.run_job("tick", 0.75)                       # debounced: within 1 s of the last write
            self.assertEqual(len(writes), n0 + 1)
            core.run_job("tick", 1.5)
            self.assertEqual(len(writes), n0 + 2)

    def test_reconcile_stale_hold_and_release(self):
        core, fk = self.Dc.make_core()
        core.take_requests()                                 # the first cycle is never answered
        core.run_job("tick", 9.0)
        self.assertNotIn("reconcile_down", fk.risk.holds(9.0))
        core.run_job("tick", 10.5)
        self.assertIn("reconcile_down", fk.risk.holds(10.5))
        self.assertTrue(self.alerts(fk, "reconcile_down"))
        core.run_job("reconcile", 11.0)                      # the stale cycle is dropped and a new one starts
        self.Dc.answer_all(core, [r for r in core.take_requests() if r.kind != ApiKind.GET_TIME], 11.2)
        self.assertNotIn("reconcile_down", fk.risk.holds(11.3))

    def test_engine_slow_alert(self):
        core, fk = self.Dc.make_core()
        from ctcbot.types import EngineHealth
        fk.engine.health = lambda now: EngineHealth(True, 0, None, 2500.0, 0, 0, True)
        core.run_job("tick", 1.0)
        core.run_job("tick", 2.0)
        self.assertEqual(len(self.alerts(fk, "engine_slow")), 1)

    def test_comp_id_candidate_flap_is_ignored(self):
        core, fk = self.Dc.make_core()
        t = 1.0
        for cid in ("comp-1", "other", "comp-1"):
            core.run_job("clock_poll", t)
            self.Dc.answer_all(core, [r for r in core.take_requests() if r.kind == ApiKind.GET_TIME], t, comp_id=cid)
            if cid == "other":
                self.assertIn("comp_id_suspect", fk.risk.holds(t))
            t += 5.0
        self.assertEqual(core.report()["resets"]["confirmed"], 0)
        self.assertNotIn("comp_id_suspect", fk.risk.holds(t))
        self.assertTrue(self.alerts(fk, "comp_id_flap"))

    def test_comp_id_confirmed_by_two_answers(self):
        core, fk = self.Dc.make_core()
        t = 1.0
        for cid in ("comp-1", "comp-2", "comp-2"):
            core.run_job("clock_poll", t)
            self.Dc.answer_all(core, [r for r in core.take_requests() if r.kind == ApiKind.GET_TIME], t, comp_id=cid)
            t += 1.5
        self.assertEqual(core.report()["resets"]["confirmed"], 1)


class TestCoreKillPaths(unittest.TestCase):
    def test_cx_reports_queued_when_the_kill_budget_is_spent(self):
        import test_d_core as Dc
        core, fk = Dc.make_core()
        core.take_requests()
        core.on_command(Command("cx"), 1.0)
        self.assertEqual(core.take_output(), ["cancel-all sent (KILL priority)"])
        fk.oms[-1].cancel_all = lambda *a, **k: []            # the OM queued it: the KILL ticket was denied
        core.on_command(Command("cx"), 2.0)
        out = core.take_output()
        self.assertTrue(out and out[0].startswith("cancel-all QUEUED"), out)
        self.assertTrue([a for a in fk.log.kinds("alert") if a.get("alert") == "cx_queued"])

    def test_quit_takes_the_kill_path(self):
        import test_d_core as Dc
        core, fk = Dc.make_core()
        core.take_requests()
        core.on_command(Command("q"), 1.0)
        self.assertTrue(core.quit_requested)
        self.assertTrue(core.killed())
        self.assertIn("freeze", core.take_control())


# ================================================================================================ runner
class TestRunnerReenable(unittest.TestCase):
    def test_auto_disable_cancels_and_reenables_twice_per_season(self):
        from test_c_support import FakeConfig, make_ctx, make_deps
        from ctcbot.strategies.runner import StrategyRunner
        cfg = FakeConfig()
        run = StrategyRunner(make_deps(cfg))
        s1 = run.strategies["s1r3"]
        calls = {"n": 0}

        def boom(ctx):
            calls["n"] += 1
            raise RuntimeError("bug")
        s1.decide = boom
        out = []
        for k in range(3):
            out += run.decide(make_ctx(745.0, 3, cfg=cfg, now=1000.0 + k))
        self.assertFalse(s1.enabled)
        self.assertTrue([i for i in out if isinstance(i, CancelIntent) and i.strategy == "s1r3"])   # its rests go
        run.decide(make_ctx(745.0, 3, cfg=cfg, now=1020.0))
        self.assertFalse(s1.enabled)                                 # cooldown 30 s
        self.assertTrue(run.state_dict()["s1r3"]["enabled"])         # an automatic disable is not persisted
        for cycle in range(2):
            run.decide(make_ctx(745.0, 3, cfg=cfg, now=1035.0 + 100 * cycle))
            self.assertTrue(s1.enabled)
            for k in range(3):
                run.decide(make_ctx(745.0, 3, cfg=cfg, now=1036.0 + 100 * cycle + k))
            self.assertFalse(s1.enabled)
        run.decide(make_ctx(745.0, 3, cfg=cfg, now=1500.0))
        self.assertFalse(s1.enabled)                                 # the two retries are used up
        run.reset_season()
        self.assertTrue(s1.enabled)                                  # a new season starts fresh

    def test_operator_off_is_never_undone(self):
        from test_c_support import FakeConfig, make_ctx, make_deps
        from ctcbot.strategies.runner import StrategyRunner
        cfg = FakeConfig()
        run = StrategyRunner(make_deps(cfg))
        run.on_command(Command("s3", ("off",)), make_ctx(100.0, 1, cfg=cfg, now=100.0))
        run.decide(make_ctx(100.0, 1, cfg=cfg, now=1000.0))
        self.assertFalse(run.strategies["s3"].enabled)


# ================================================================================================ config and cli
class TestLiveBounds(unittest.TestCase):
    def test_default_and_testnet_profiles_are_inside_the_bounds(self):
        from ctcbot.config import live_bound_problems, load_config
        self.assertEqual(live_bound_problems(load_config()), [])
        self.assertEqual(live_bound_problems(load_config(overlays=[os.path.join(BOT, "config",
                                                                                 "testnet_quarter.json")])), [])

    def test_loosened_bands_are_refused_for_live(self):
        from ctcbot.app import cli
        from ctcbot.config import live_bound_problems, load_config
        cfg = load_config(sets=["risk.max_marketable_from_mid=0.5", "risk.fat_finger_frac=5"])
        probs = live_bound_problems(cfg)
        self.assertEqual(len(probs), 2, probs)
        ok = dict(isatty=True, live_allowed=True, case_fetched=True, timing_diff=False, accept_timing_diff=False,
                  probe_ok=True, kill_file_present=False)
        self.assertEqual(cli.live_refusals(**ok), [])
        self.assertEqual(len(cli.live_refusals(**ok, bound_problems=probs)), 2)

    def test_rec_parity_profile_is_offline_only(self):
        from ctcbot.config import live_bound_problems, load_config
        cfg = load_config(overlays=[os.path.join(BOT, "config", "rec_parity.json")])
        self.assertFalse(cfg.live_allowed)
        self.assertTrue(live_bound_problems(cfg))


# ================================================================================================ shell
class TestShellSignals(unittest.TestCase):
    def test_sighup_is_handled_like_sigint(self):
        if not hasattr(signal, "SIGHUP"):
            self.skipTest("no SIGHUP on this platform")
        if threading.current_thread() is not threading.main_thread():
            self.skipTest("signals need the main thread")
        from ctcbot.app.shell import _Shell
        import test_d_shell as Ds
        core, eng, api, bridge, ex = Ds.make()

        async def go():
            sh = _Shell(core, [], ex, api, eng, Ds.FastTime(1.0), ListLog(), None, False, None, bridge, [], None,
                        True)
            sh.loop = asyncio.get_running_loop()
            sh._install_signals()
            try:
                return list(sh.handlers)
            finally:
                sh._remove_signals()
        handlers = asyncio.run(go())
        self.assertIn(int(signal.SIGHUP), handlers)
        self.assertIn(int(signal.SIGINT), handlers)

    def test_third_signal_sends_nothing_more(self):
        from ctcbot.app.shell import _Shell
        import test_d_shell as Ds
        core, eng, api, bridge, ex = Ds.make()
        sh = _Shell(core, [], ex, api, eng, Ds.FastTime(1.0), ListLog(), None, False, None, bridge, [], None, False)
        calls = []
        api.execute = lambda req: calls.append(req) or ApiResult(req, 200, {}, {}, None, 0.0, 0.0)
        sh.signals = 1
        sh._on_signal(int(signal.SIGINT))                    # the 2nd: one hard cancel
        sh._on_signal(int(signal.SIGINT))                    # the 3rd: nothing
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
