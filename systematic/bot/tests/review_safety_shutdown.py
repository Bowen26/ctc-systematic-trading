"""Safety review (adversarial): kill, Ctrl-C and quit leave nothing resting.

Each test asserts the SAFE property; a failure is the evidence for a review finding. Not part of the default
suite (``discover`` only picks up ``test*.py``). Run from BOT with:

    SYS/.venv/bin/python -m unittest discover -s tests -p "review_safety_shutdown.py" -v

The rig is the D kill drill's (``test_d_kill.TestKillDrill``): the real BotCore with the real order manager,
risk manager, limiter, store, positions and reconciler, the real ``RestExecutor`` (12 workers + 1 kill worker)
and the real shell; only the clock, normalizer, runner, engine, laws and the exchange are fakes.

* ``test_sigint_leaves_no_order``   - the drill with Ctrl-C instead of ``k``: the shell exits on the first kill
                                      ack, before the late PLACE acks can trigger follow-up CANCEL_ALLs.
* ``test_quit_leaves_no_order``     - ``q`` sends one CANCEL_ALL but never freezes: the next tick re-places the
                                      rests while that cancel is in flight, and the shell exits with them open.
* ``test_kill_not_stuck_behind_prewarm`` - the only kill worker also runs ``prewarm`` (a GET with 3-s timeout and
                                      one retry); a kill submitted meanwhile waits for it.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
import time
import unittest
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.dirname(HERE)
sys.path.insert(0, BOT)
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

from ctcbot.types import (SYMBOLS, ApiKind, ApiRequest, ApiResult, ListLog, OpenOrderRow, OrderAck,  # noqa: E402
                          Priority, RestIntent, ServerTime, Side)
from ctcbot.app.shell import ResultBridge, run_shell  # noqa: E402
from test_d_core import CallLog, FakeClock, FakeCoverage, FakeEngine, FakeLawCheck, FakeNormalizer, FakeRunner  # noqa: E402,E501
from test_d_kill import SlowExchange, TickSource, _Pipe  # noqa: E402
from test_d_shell import FastTime  # noqa: E402


def _rig(policy):
    mods = testpaths.integration("ctcbot.config", "ctcbot.case", "ctcbot.rest", "ctcbot.ratelimit",
                                 "ctcbot.state.books", "ctcbot.state.positions", "ctcbot.state.orders",
                                 "ctcbot.exec.risk", "ctcbot.exec.order_manager", "ctcbot.exec.reconcile")
    config, case_mod, rest = mods[0], mods[1], mods[2]
    from ctcbot.app.core import BotCore, default_factories
    cfg = config.load_config()
    case = case_mod.CaseInfo.from_file(testpaths.require(testpaths.RESEARCH_CASE))
    ts = FastTime(1.0)
    calls = CallLog()
    sched = default_factories()["schedule"](cfg=cfg, case=case)
    clock = FakeClock(sched)
    clock.open_mono = ts.mono() - 200.0                      # mid-storm, clock known and running
    runner = FakeRunner()
    runner.policy = policy
    norm = FakeNormalizer()
    fac = dict(default_factories())
    fac.update({"normalizer": lambda **_: norm, "clock": lambda **_: clock,
                "coverage": lambda **_: FakeCoverage(), "laws": lambda **_: None,
                "law_check": lambda **_: FakeLawCheck(calls), "runner": lambda **_: runner})
    log = ListLog()
    eng = FakeEngine(calls)
    core = BotCore(cfg, case, case_mod.diff_case(case.raw, case.raw), eng, ts, log, mode="host", run_dir=None,
                   run_id="review", laws_module=None, cost=None, factories=fac)
    return core, eng, ts, log, rest


class ArrivalExchange(SlowExchange):
    """Registers a PLACE when it arrives; CANCEL_ALL clears at arrival and answers after ``cancel_delay``."""

    def __init__(self, place_delay: float = 0.05, cancel_delay: float = 0.4) -> None:
        super().__init__(place_delay)
        self.cancel_delay = cancel_delay
        self.place_after_cancel: list[float] = []

    def execute(self, req: ApiRequest) -> ApiResult:
        t0 = time.monotonic()
        if req.kind == ApiKind.PLACE:
            with self.lock:
                self.n += 1
                oid = f"x{self.n}"
                self.open[oid] = req
                self.place_arrivals.append(t0)
                if self.cancel_all_arrivals:
                    self.place_after_cancel.append(t0)
            time.sleep(self.delay)
            with self.lock:
                self.place_done.append(time.monotonic())
            return self._res(req, 200, {"order_id": oid}, OrderAck(oid, "open", 0, req.qty, None), t0)
        if req.kind == ApiKind.CANCEL_ALL:
            with self.lock:
                self.cancel_all_arrivals.append((t0, req.priority))
                self.open = {} if req.symbol is None else {k: v for k, v in self.open.items()
                                                          if v.symbol != req.symbol}
            time.sleep(self.cancel_delay)
            return self._res(req, 200, {"status": "cancelled"}, {}, t0)
        return super().execute(req)


class TestShutdownPaths(unittest.TestCase):
    def test_sigint_leaves_no_order(self):
        if threading.current_thread() is not threading.main_thread():
            self.skipTest("signals need the main thread")
        state = {"done": False}
        core, eng, ts, log, rest = _rig(lambda ctx: [] if state["done"] else
                                        [RestIntent("s1r3", f"rev:{s}", s, Side.BUY, 49.0, Priority.SNIPE, qty=5)
                                         for s in SYMBOLS])
        exch = SlowExchange(1.5)                              # the D drill's exchange: a PLACE rests when it answers
        bridge = ResultBridge()
        ex = rest.RestExecutor(exch, 12, bridge, log, kill_workers=1)
        marks: dict[str, float] = {}

        def driver() -> None:
            end = time.monotonic() + 10.0
            while time.monotonic() < end:
                with exch.lock:
                    if exch.inflight >= 12:
                        break
                time.sleep(0.005)
            state["done"] = True
            time.sleep(0.1)
            marks["sigint"] = time.monotonic()
            os.kill(os.getpid(), signal.SIGINT)               # the operator presses Ctrl-C

        th = threading.Thread(target=driver, daemon=True)

        async def go():
            asyncio.get_running_loop().call_later(0.05, th.start)
            return await run_shell(core, [TickSource(ts)], ex, exch, eng, ts, log, None, console=False,
                                   stdin=None, result_bridge=bridge)

        rc = asyncio.run(go())
        end = time.monotonic() + 5.0
        while time.monotonic() < end:                         # let every in-flight POST resolve
            with exch.lock:
                if exch.inflight == 0:
                    break
            time.sleep(0.01)
        self.assertEqual(rc, 130)
        kills = [t for t, p in exch.cancel_all_arrivals if p == Priority.KILL]
        self.assertEqual(len(exch.open), 0,
                         f"{len(exch.open)} orders still open after Ctrl-C exited ({len(kills)} kill CANCEL_ALL, "
                         f"exit {time.monotonic() - marks['sigint']:.2f} s after the signal)")

    def test_quit_leaves_no_order(self):
        core, eng, ts, log, rest = _rig(lambda ctx: [RestIntent("s1r3", f"rev:{s}", s, Side.BUY, 49.0,
                                                                Priority.SNIPE, qty=5) for s in SYMBOLS])
        exch = ArrivalExchange(place_delay=0.05, cancel_delay=0.4)
        bridge = ResultBridge()
        ex = rest.RestExecutor(exch, 12, bridge, log, kill_workers=1)
        pipe = _Pipe()

        def driver() -> None:
            end = time.monotonic() + 10.0
            while time.monotonic() < end:
                with exch.lock:
                    if len(exch.open) >= 12:
                        break
                time.sleep(0.005)
            time.sleep(0.3)
            pipe.send("q")                                    # the operator quits at the end of the day

        th = threading.Thread(target=driver, daemon=True)

        async def go():
            asyncio.get_running_loop().call_later(0.05, th.start)
            return await run_shell(core, [TickSource(ts)], ex, exch, eng, ts, log, None, console=False,
                                   stdin=pipe.r, result_bridge=bridge, install_signals=False)

        try:
            rc = asyncio.run(go())
        finally:
            pipe.close()
        time.sleep(0.3)
        self.assertEqual(rc, 0)
        self.assertTrue(exch.cancel_all_arrivals, "q sent no CANCEL_ALL")
        self.assertEqual((len(exch.place_after_cancel), len(exch.open)), (0, 0),
                         f"{len(exch.place_after_cancel)} PLACEs reached the exchange after the shutdown "
                         f"CANCEL_ALL; {len(exch.open)} orders open after the bot exited")


class _PrewarmAPI:
    def __init__(self, prewarm_s: float) -> None:
        self.prewarm_s = prewarm_s
        self.arrivals: list[tuple[ApiKind, float]] = []

    def execute(self, req: ApiRequest) -> ApiResult:
        self.arrivals.append((req.kind, time.monotonic()))
        t = time.monotonic()
        return ApiResult(req=req, status=200, data={}, parsed={}, error=None, sent_mono=t, done_mono=t, sent=True)

    def prewarm(self) -> None:                                # RestClient.prewarm: GET /competitions/time,
        time.sleep(self.prewarm_s)                            # timeout 3 s, one retry, on a slow exchange


class TestKillWorker(unittest.TestCase):
    def test_kill_not_stuck_behind_prewarm(self):
        from ctcbot.rest import RestExecutor
        api = _PrewarmAPI(1.5)
        ex = RestExecutor(api, 12, lambda r: None, ListLog(), kill_workers=1)
        try:
            ex.prewarm_all()                                  # what the core asks for at 737 s of round 3
            time.sleep(0.05)
            t0 = time.monotonic()
            ex.submit(ApiRequest(req_id=1, kind=ApiKind.CANCEL_ALL, priority=Priority.KILL))   # operator: k
            end = t0 + 3.0
            while time.monotonic() < end and not api.arrivals:
                time.sleep(0.005)
        finally:
            ex.close(2.0)
        self.assertTrue(api.arrivals)
        lat = api.arrivals[0][1] - t0
        self.assertLess(lat, 0.2, f"the kill CANCEL_ALL reached the exchange {lat:.2f} s after k: the kill "
                                  f"worker was busy prewarming")


if __name__ == "__main__":
    unittest.main()
