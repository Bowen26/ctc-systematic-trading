"""Group E: ``sim.harness.Harness`` ordering, latency, end-of-run sequence and determinism (DESIGN.md 9.1 E).

The core, exchange, market and engine are fakes defined here; ``TestRealStack`` (SLOW, integration) runs the
real ``BotCore`` of group D with groups B and C on one research season twice and compares event-log hashes.
"""
from __future__ import annotations

import os
import sys
import unittest
from typing import Any, Callable

sys.dont_write_bytecode = True
BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT not in sys.path:
    sys.path.insert(0, BOT)

import testpaths  # noqa: E402
from ctcbot.types import (ApiKind, ApiRequest, ApiResult, Command, EngineHealth, FairValueSnapshot,  # noqa: E402
                          ListLog, OrderAck, Priority, ServerTime, Side, SYMBOLS)
from test_e_exchange import FakeConfig  # noqa: E402

from sim.harness import EngineTap, Harness, VirtualClock, event_log_hash, to_source_frame  # noqa: E402
from sim.research_market import MarketFrame  # noqa: E402


# ----------------------------------------------------------------------------------------- fakes
class FakeMarket:
    kind = "fake"

    def __init__(self, frames: list[MarketFrame], t_end: float = 10.0) -> None:
        self._frames = frames
        self._t_end = t_end

    def frames(self) -> Any:
        return iter(self._frames)

    def default_t_end(self) -> float:
        return self._t_end


class FakeExchange:
    """Records advance / execute / close_all; answers every request 200."""

    def __init__(self, trace: list) -> None:
        self.trace = trace
        self.t = 0.0
        self.n = 0
        self.executed: list[tuple[float, str]] = []

    def advance(self, t: float) -> None:
        self.trace.append(("advance", t))

    def execute(self, req: ApiRequest) -> ApiResult:
        self.n += 1
        self.executed.append((self.t, str(req.kind)))
        parsed: Any = None
        data: Any = {}
        if req.kind == ApiKind.PLACE:
            data = {"order_id": f"o{self.n}"}
            parsed = OrderAck(f"o{self.n}", "open", 0, req.qty, None)
        elif req.kind == ApiKind.GET_TIME:
            parsed = ServerTime("fake", "running", None, self.t)
        return ApiResult(req=req, status=200, data=data, parsed=parsed, error=None, sent_mono=self.t,
                         done_mono=self.t)

    def close_all(self, t: float) -> None:
        self.trace.append(("close_all", t))

    def results(self) -> dict:
        return {"executed": len(self.executed)}


class FakeEngine:
    """FairValueProvider whose submit publishes synchronously (calls the listener inside submit)."""

    def __init__(self) -> None:
        self.cb: Callable[[], None] | None = None
        self.version = 0
        self.snap: FairValueSnapshot | None = None

    def set_listener(self, cb: Callable[[], None]) -> None:
        self.cb = cb

    def submit(self, msgs: Any, recv_mono: float) -> None:
        self.version += 1
        seq = max((m.get("seq", -1) for m in msgs), default=None)
        self.snap = FairValueSnapshot(version=self.version, values={s: (1.0, 0.1) for s in SYMBOLS}, season_epoch=0,
                                      round=1, seq_hi=seq, msg_time=None, included_mono=recv_mono,
                                      computed_mono=recv_mono, compute_ms=0.0, ok=True)
        if self.cb is not None:
            self.cb()

    def latest(self) -> FairValueSnapshot | None:
        return self.snap

    def health(self, now: float) -> EngineHealth:
        return EngineHealth(True, 0, None, 0.0, 0, 0, True)

    def set_adjustment(self, symbol: str, mult: float = 1.0, add: float = 0.0) -> None:
        pass

    def override(self, dotted: str, value: Any) -> None:
        pass

    def reset_season(self, season_epoch: int, persist_path: str | None) -> None:
        pass

    def close(self) -> None:
        pass


class _Rec:
    def __init__(self, strategy: str) -> None:
        self.strategy = strategy
        self.style = "clip"
        self.key = None


class FakeStore:
    def __init__(self) -> None:
        self.recs: dict[str, _Rec] = {}

    def by_local(self, local_id: str) -> _Rec | None:
        return self.recs.get(local_id)


class FakeCore:
    """Records every call; places one order per frame that asks for it; reconcile issues one GET."""

    def __init__(self, timesrc: VirtualClock, engine: FakeEngine, log: ListLog, trace: list,
                 raise_at: float | None = None, chatty: bool = False) -> None:
        self.timesrc = timesrc
        self.engine = engine
        self.log = log
        self.trace = trace
        self.raise_at = raise_at
        self.chatty = chatty
        self.out: list[ApiRequest] = []
        self.ctrl: list[str] = []
        self.depth = 0
        self.nested = 0
        self.n = 0
        self.store = FakeStore()
        self.results: list[tuple[float, str]] = []

    def _req(self, kind: ApiKind, **kw: Any) -> ApiRequest:
        self.n += 1
        return ApiRequest(req_id=self.n, kind=kind, **kw)

    def _enter(self, name: str, now: float) -> None:
        if self.depth:
            self.nested += 1
        self.trace.append((name, now))
        self.log.event("call", name=name, t=now, clock=self.timesrc.mono())

    def jobs(self) -> list[tuple[str, float]]:
        return [("reconcile", 0.5), ("clock_poll", 1.0), ("tick", 0.25)]

    def start(self, now: float) -> None:
        self._enter("start", now)
        self.out.append(self._req(ApiKind.GET_TIME))

    def on_frames(self, frames: list, now: float) -> None:
        self._enter("on_frames", now)
        self.depth += 1
        try:
            if self.raise_at is not None and abs(now - self.raise_at) < 1e-9:
                raise RuntimeError("boom")
            news = [f.frame["message"] for f in frames if f.frame.get("type") == "news"]
            if news:
                self.engine.submit(news, now)
            for f in frames:
                if f.frame.get("place"):
                    lid = f"e0-{self.n + 1}"
                    self.store.recs[lid] = _Rec(str(f.frame.get("strategy", "h1")))
                    self.out.append(self._req(ApiKind.PLACE, symbol="PORT", side=Side.BUY, qty=1, price=10.0,
                                              local_id=lid, priority=Priority.TAKE))
                if f.frame.get("control_word"):
                    self.ctrl.append("freeze")
        finally:
            self.depth -= 1

    def on_fair_values(self, now: float) -> None:
        self._enter("on_fair_values", now)

    def on_api_result(self, res: ApiResult, now: float) -> None:
        self._enter("on_api_result", now)
        self.results.append((now, str(res.req.kind)))
        if self.chatty:
            self.out.append(self._req(ApiKind.GET_OPEN))

    def on_command(self, cmd: Command, now: float) -> None:
        self._enter(f"command:{cmd.name}", now)

    def run_job(self, name: str, now: float) -> None:
        self._enter(f"job:{name}", now)
        if name == "reconcile":
            self.out.append(self._req(ApiKind.GET_OPEN))

    def take_requests(self) -> list[ApiRequest]:
        out, self.out = self.out, []
        return out

    def take_control(self) -> list[str]:
        out, self.ctrl = self.ctrl, []
        return out

    def shutdown(self, now: float) -> None:
        self._enter("shutdown", now)
        self.out.append(self._req(ApiKind.CANCEL_ALL, priority=Priority.KILL))

    def report(self) -> dict:
        return {"calls": len(self.trace)}

    def status(self, now: float) -> dict:
        return {"t": now}


def news(t: float, seq: int) -> MarketFrame:
    return MarketFrame(t, {"type": "news", "message": {"type": "storm", "seq": seq}}, "sim#1")


def place(t: float, strategy: str = "h1") -> MarketFrame:
    return MarketFrame(t, {"type": "quote", "place": True, "strategy": strategy}, "sim#1")


def build(frames: list[MarketFrame], overrides: dict | None = None, t_end: float | None = 10.0,
          commands: tuple = (), **core_kw: Any) -> tuple[Harness, FakeExchange, list, ListLog]:
    trace: list = []
    log = ListLog()
    eng = FakeEngine()
    ex = FakeExchange(trace)
    holder: dict = {}

    def factory(timesrc: VirtualClock) -> FakeCore:
        holder["core"] = FakeCore(timesrc, eng, log, trace, **core_kw)
        return holder["core"]

    h = Harness(FakeConfig(overrides), FakeMarket(frames, t_end=t_end or 10.0), ex, eng, factory, log, t_end=t_end,
                commands=commands)
    return h, ex, trace, log


def calls_at(trace: list, t: float) -> list[str]:
    return [name for name, tt in trace if abs(tt - t) < 1e-9 and name != "advance"]


# ----------------------------------------------------------------------------------------- tests
class TestOrdering(unittest.TestCase):
    def test_within_instant_order(self) -> None:
        h, ex, trace, _ = build([news(1.0, 5), place(1.0)], commands=((1.0, Command("st", source="sim")),))
        h.run()
        # the jobs in core.jobs() order first (only reconcile issues a request here, answered at once), then the
        # frames: their PLACE is answered at once, then the engine notification, then the scripted command
        self.assertEqual(calls_at(trace, 1.0), ["job:reconcile", "on_api_result", "job:clock_poll", "job:tick",
                                                "on_frames", "on_api_result", "on_fair_values", "command:st"])
        i_adv = trace.index(("advance", 1.0))
        i_job = trace.index(("job:reconcile", 1.0))
        self.assertLess(i_adv, i_job)

    def test_start_first_and_its_result_at_once(self) -> None:
        h, ex, trace, _ = build([])
        h.run()
        self.assertEqual(trace[1:3], [("start", 0.0), ("on_api_result", 0.0)])
        self.assertEqual(ex.executed[0], (0.0, "get_time"))

    def test_zero_latency_request_executes_at_arrival(self) -> None:
        h, ex, trace, _ = build([place(3.7)])
        h.run()
        self.assertIn((3.7, "place"), ex.executed)
        core = h.core
        self.assertIn((3.7, "place"), core.results)

    def test_api_latency_delays_results(self) -> None:
        h, ex, trace, _ = build([place(3.7)], {"sim.api_latency_s": 0.1})
        h.run()
        self.assertIn((3.7, "place"), ex.executed)
        self.assertIn((3.8, "place"), h.core.results)
        self.assertNotIn((3.7, "place"), h.core.results)
        self.assertIn("on_api_result", calls_at(trace, 3.8))

    def test_engine_notification_after_the_call_never_nested(self) -> None:
        h, _, trace, _ = build([news(2.2, 1)])
        h.run()
        self.assertEqual(calls_at(trace, 2.2), ["on_frames", "on_fair_values"])
        self.assertEqual(h.core.nested, 0)

    def test_engine_latency(self) -> None:
        h, _, trace, _ = build([news(2.2, 1), news(2.3, 2)], {"sim.engine_latency_s": 0.5})
        h.run()
        self.assertEqual(calls_at(trace, 2.2), ["on_frames"])
        self.assertEqual(calls_at(trace, 2.7), ["on_fair_values"])     # the two publishes coalesce into one call
        self.assertEqual(sum(1 for n, _ in trace if n == "on_fair_values"), 1)

    def test_frames_of_one_instant_in_one_call(self) -> None:
        h, _, trace, _ = build([news(4.1, 1), news(4.1, 2), place(4.1)])
        h.run()
        self.assertEqual(calls_at(trace, 4.1).count("on_frames"), 1)

    def test_jobs_fire_at_multiples_from_zero(self) -> None:
        h, _, trace, _ = build([], t_end=10.0)
        h.run()
        ticks = [t for n, t in trace if n == "job:tick"]
        recs = [t for n, t in trace if n == "job:reconcile"]
        polls = [t for n, t in trace if n == "job:clock_poll"]
        self.assertEqual(ticks, [k * 0.25 for k in range(49)])          # through t_end + 2
        self.assertEqual(recs, [k * 0.5 for k in range(25)])
        self.assertEqual(polls, [float(k) for k in range(13)])


class TestEndOfRun(unittest.TestCase):
    def test_close_all_then_two_seconds_then_shutdown(self) -> None:
        h, ex, trace, _ = build([place(9.0), place(11.0)], t_end=10.0)
        res = h.run()
        names = [n for n, _ in trace if n != "advance"]
        i_close = names.index("close_all")
        self.assertEqual(trace[[k for k, x in enumerate(trace) if x[0] == "close_all"][0]], ("close_all", 10.0))
        after = [x for x in trace[[k for k, x in enumerate(trace) if x[0] == "close_all"][0] + 1:] if x[0] != "advance"]
        self.assertTrue(all(10.0 < t <= 12.0 for _, t in after))
        self.assertEqual([x for x in after if x[0] != "on_api_result"][-1], ("shutdown", 12.0))
        self.assertEqual(after[-1], ("on_api_result", 12.0))              # the shutdown's cancel is answered
        self.assertIn("job:reconcile", [n for n, _ in after])
        self.assertNotIn(("on_frames", 11.0), trace)                      # frames after t_end are not delivered
        self.assertGreater(i_close, names.index("on_frames"))
        self.assertEqual(ex.executed[-1], (12.0, "cancel_all"))           # shutdown requests are drained
        self.assertEqual(res["timing"]["t_end"], 10.0)
        self.assertEqual(res["timing"]["t_final"], 12.0)
        self.assertEqual(res["exchange"], {"executed": len(ex.executed)})
        self.assertEqual(res["status"], {"t": 12.0})

    def test_default_t_end_from_market(self) -> None:
        h, ex, trace, _ = build([place(1.0)], t_end=None)
        h.market._t_end = 5.0
        res = h.run()
        self.assertEqual(res["timing"]["t_end"], 5.0)
        self.assertIn(("close_all", 5.0), trace)


class TestRobustness(unittest.TestCase):
    def test_exception_is_logged_and_run_continues(self) -> None:
        h, _, trace, log = build([place(1.0), place(2.0), place(3.0)], raise_at=2.0)
        res = h.run()
        self.assertEqual(len(res["exceptions"]), 1)
        self.assertEqual(res["exceptions"][0]["call"], "on_frames")
        self.assertEqual(res["log_counts"]["exception"], 1)
        self.assertIn(("on_frames", 3.0), trace)
        ev = log.kinds("exception")[0]
        self.assertIn("RuntimeError: boom", ev["error"])
        self.assertIn("Traceback", ev["traceback"])

    def test_order_meta_comes_from_the_store(self) -> None:
        h, _, _, _ = build([place(1.0, "s1r3"), place(2.0, "h1")])
        res = h.run()
        strategies = sorted(m["strategy"] for m in res["orders"].values())
        self.assertEqual(strategies, ["h1", "s1r3"])
        meta = next(m for m in res["orders"].values() if m["strategy"] == "s1r3")
        self.assertEqual((meta["symbol"], meta["t"]), ("PORT", 1.0))
        self.assertEqual(h.core.store.recs[meta["local_id"]].strategy, "s1r3")

    def test_control_words_are_drained_and_ignored(self) -> None:
        h, _, _, _ = build([MarketFrame(1.0, {"type": "quote", "control_word": True}, "sim#1")])
        h.run()
        self.assertEqual(h.core.ctrl, [])

    def test_pump_guard(self) -> None:
        trace: list = []
        log = ListLog()
        eng = FakeEngine()
        ex = FakeExchange(trace)
        h = Harness(FakeConfig(), FakeMarket([], 1.0), ex, eng,
                    lambda timesrc: FakeCore(timesrc, eng, log, trace, chatty=True), log, t_end=1.0, max_pump=50)
        res = h.run()
        self.assertGreater(res["timing"]["pump_overflows"], 0)
        self.assertTrue(log.kinds("alert"))

    def test_virtual_clock_follows_event_time(self) -> None:
        h, _, _, log = build([place(1.5)])
        h.run()
        for f in log.kinds("call"):
            self.assertEqual(f["t"], f["clock"])
        vc = VirtualClock(wall0=100.0)
        vc.set(2.0)
        vc.advance(0.5)
        self.assertEqual((vc.mono(), vc.wall()), (2.5, 102.5))


class TestDeterminism(unittest.TestCase):
    def test_identical_runs_give_identical_hashes(self) -> None:
        frames = [news(0.6, 1), place(1.0), news(2.2, 2), place(3.1), place(7.9)]
        a = build(frames)[0].run()["log_hash"]
        b = build(frames)[0].run()["log_hash"]
        c = build(frames[:-1])[0].run()["log_hash"]
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_hash_ignores_wall_clock_fields(self) -> None:
        a, b = ListLog(), ListLog()
        a.event("fv", version=1, compute_ms=12.3, mu=1.0000000000001)
        b.event("fv", version=1, compute_ms=99.9, mu=1.0)
        self.assertEqual(event_log_hash(a), event_log_hash(b))
        b.event("fv", version=2)
        self.assertNotEqual(event_log_hash(a), event_log_hash(b))


class TestEngineTap(unittest.TestCase):
    def test_records_snapshots_and_publishes(self) -> None:
        eng = FakeEngine()
        clock = VirtualClock()
        tap = EngineTap(eng, clock.mono)
        seen: list[int] = []
        tap.set_listener(lambda: seen.append(1))
        clock.set(5.0)
        tap.submit([{"type": "storm", "seq": 3}, {"type": "snapshot", "last_seq": 695}], 5.0)
        self.assertEqual(tap.submitted_snapshots, [695])
        self.assertEqual(tap.submitted_news, 1)
        self.assertEqual(tap.publishes, [(5.0, 1, 3, 1)])
        self.assertEqual(seen, [1])
        self.assertIs(tap.latest(), eng.snap)

    def test_to_source_frame(self) -> None:
        sf = to_source_frame(MarketFrame(3.0, {"a": 1}, "rec#2", None, recv_wall=1792766849.8), 1.8e9)
        self.assertEqual((sf.recv_mono, sf.recv_wall, sf.conn, sf.control), (3.0, 1792766849.8, "rec#2", None))
        sf = to_source_frame(MarketFrame(3.0, {}, "sim#1", "up"), 1000.0)
        self.assertEqual((sf.recv_wall, sf.control), (1003.0, "up"))


class TestRealStack(unittest.TestCase):
    """The real BotCore, order manager and strategies on one research season, twice (SLOW, integration)."""

    def test_two_runs_identical(self) -> None:
        testpaths.slow()
        testpaths.require(os.path.join(testpaths.HISTORY, "season-401"))
        testpaths.require(os.path.join(testpaths.FV_CACHE, "fv_holdout_401.json"))
        testpaths.integration("ctcbot.app.core", "ctcbot.exec.order_manager", "ctcbot.exec.reconcile",
                              "ctcbot.strategies.runner", "ctcbot.models.cost_model")
        from sim import run_research
        opts = {"config": None, "profiles": [os.path.join(BOT, "config", "rec_parity.json")], "sets": [],
                "history": testpaths.HISTORY, "fv_cache": testpaths.FV_CACHE, "fv": "cache", "snapshots": "all"}
        a = run_research.run_season(401, opts)
        b = run_research.run_season(401, opts)
        self.assertEqual(a["log_hash"], b["log_hash"])
        self.assertEqual(a["pnl"], b["pnl"])
        self.assertEqual(a["violations_total"], 0, a["violations"])
        self.assertEqual(a["exceptions"], 0, a.get("harness_exceptions"))


class TestStart(unittest.TestCase):
    def test_t_start_offsets_the_run(self) -> None:
        trace: list = []
        log = ListLog()
        eng = FakeEngine()
        ex = FakeExchange(trace)
        h = Harness(FakeConfig(), FakeMarket([place(101.0)], 103.0), ex, eng,
                    lambda timesrc: FakeCore(timesrc, eng, log, trace), log, t_end=103.0, t_start=100.1)
        h.run()
        self.assertEqual(trace[1], ("start", 100.1))
        ticks = [t for n, t in trace if n == "job:tick"]
        self.assertEqual(ticks[0], 100.25)
        self.assertEqual([t for n, t in trace if n == "job:clock_poll"][0], 101.0)
        self.assertIn(("on_frames", 101.0), trace)


if __name__ == "__main__":
    unittest.main()
