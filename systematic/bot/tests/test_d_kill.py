"""Group D tests: the kill drill "no orders left 1 s after a kill" (DESIGN.md §9.1 D kill).

* Unit: the real ``BotCore`` (fake collaborators) inside the real shell with a fake executor: after ``k`` the
  executor is frozen before the kill's CANCEL_ALL is submitted, and no PLACE is submitted after it.
* Integration (acceptance): 12 PLACEs in flight on the 12 workers of the real ``RestExecutor``, each answered
  after 1.5 s by a fake exchange that registers the order only when it answers. After ``k`` the CANCEL_ALL
  reaches the exchange within 100 ms (wall clock), no PLACE reaches it afterwards, the late OPEN acks trigger
  follow-up CANCEL_ALLs, and 1 s after the last POST resolves no order is open. Kill actions <= 1 + overdraft.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import os
import sys
import threading
import time
import unittest
from typing import Any

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

from ctcbot.types import (SYMBOLS, ApiKind, ApiRequest, ApiResult, BookEvent, BookLevel, ListLog,  # noqa: E402
                          NewsEvent, OpenOrderRow, OrderAck, Priority, RestIntent, ServerTime, Side, SourceFrame,
                          SourceStats)
from ctcbot.app.shell import ResultBridge, run_shell  # noqa: E402
from test_d_core import (CallLog, FakeClock, FakeCoverage, FakeEngine, FakeLawCheck, FakeNormalizer,  # noqa: E402
                         FakeRunner, make_core)
from test_d_shell import FakeAPI, FakeExecutor, FastTime  # noqa: E402


class _Pipe:
    """A real pipe used as the shell's stdin, so commands arrive at a chosen moment."""

    def __init__(self) -> None:
        r, w = os.pipe()
        self.r = os.fdopen(r, "r")
        self.w = os.fdopen(w, "w")

    def send(self, line: str) -> float:
        t = time.monotonic()
        self.w.write(line + "\n")
        self.w.flush()
        return t

    def close(self) -> None:
        for f in (self.w, self.r):
            try:
                f.close()
            except OSError:
                pass


class TickSource:
    """Emits, every 0.25 s, a frame carrying book events for all symbols and a storm observation."""

    name = "ws0"

    def __init__(self, timesrc: Any) -> None:
        self.timesrc = timesrc
        self._stop = False
        self.n = 0

    async def run(self, emit) -> None:
        emit(SourceFrame({}, time.time(), self.timesrc.mono(), "ws0#1", control="up"))
        base = _dt.datetime(2026, 10, 23, 10, 0, 0)
        while not self._stop:
            self.n += 1
            now = self.timesrc.mono()
            ts = (base + _dt.timedelta(seconds=0.25 * self.n)).isoformat()
            evs: list = [BookEvent(s, (BookLevel(49.95, 100),), (BookLevel(50.05, 100),), ts, now) for s in SYMBOLS]
            evs.append(NewsEvent({"type": "storm", "seq": self.n, "round": 1, "age": 140 + self.n,
                                  "time": "1802-05-10T11:40:00"}, "storm", self.n, 1, "1802-05-10T11:40:00", 0, now))
            emit(SourceFrame({"_ev": evs}, time.time(), now, "ws0#1"))
            await asyncio.sleep(0.25)

    def stop(self) -> None:
        self._stop = True

    def stats(self) -> SourceStats:
        return SourceStats("ws0", 1, self.n, 0, None, 0)


class SlowExchange:
    """Fake exchange: PLACE answers after ``delay`` and only then registers the order; CANCEL_ALL clears."""

    def __init__(self, delay: float = 1.5) -> None:
        self.delay = delay
        self.lock = threading.Lock()
        self.open: dict[str, ApiRequest] = {}
        self.place_arrivals: list[float] = []
        self.place_done: list[float] = []
        self.cancel_all_arrivals: list[tuple[float, Priority]] = []
        self.inflight = 0
        self.n = 0

    def execute(self, req: ApiRequest) -> ApiResult:
        t0 = time.monotonic()
        if req.kind == ApiKind.PLACE:
            with self.lock:
                self.place_arrivals.append(t0)
                self.inflight += 1
            time.sleep(self.delay)
            with self.lock:
                self.n += 1
                oid = f"x{self.n}"
                self.open[oid] = req
                self.inflight -= 1
                self.place_done.append(time.monotonic())
            data = {"order_id": oid, "status": "open", "filled_quantity": 0, "remaining_quantity": req.qty}
            return self._res(req, 200, data, OrderAck(oid, "open", 0, req.qty, None), t0)
        if req.kind == ApiKind.CANCEL_ALL:
            with self.lock:
                self.cancel_all_arrivals.append((t0, req.priority))
                if req.symbol is None:
                    self.open.clear()
                else:
                    self.open = {k: v for k, v in self.open.items() if v.symbol != req.symbol}
            return self._res(req, 200, {"status": "cancelled"}, {}, t0)
        if req.kind == ApiKind.CANCEL:
            with self.lock:
                self.open.pop(str(req.order_id), None)
            return self._res(req, 200, {"status": "cancelled"}, {}, t0)
        if req.kind == ApiKind.GET_OPEN:
            with self.lock:
                rows = [OpenOrderRow(oid, r.symbol, r.side, r.price, r.qty, r.qty, "open") for oid, r in self.open.items()]
            return self._res(req, 200, [], rows, t0)
        if req.kind == ApiKind.GET_POSITIONS:
            return self._res(req, 200, [], {}, t0)
        if req.kind == ApiKind.GET_TIME:
            return self._res(req, 200, {}, ServerTime("c1", "running", "1802-05-10T11:40:00", 100.0), t0)
        return self._res(req, 404, {}, None, t0)

    @staticmethod
    def _res(req: ApiRequest, status: int, data: Any, parsed: Any, t0: float) -> ApiResult:
        return ApiResult(req=req, status=status, data=data, parsed=parsed, error=None, sent_mono=t0,
                         done_mono=time.monotonic(), dry_run=False, sent=True)

    def prewarm(self) -> None:
        pass


class TestKillUnit(unittest.TestCase):
    def test_freeze_before_cancel_and_no_place_after(self):
        core, fk = make_core(start=False)
        core.cfg.d["app"]["shutdown_wait_s"] = 0.3
        state = {"on": True}
        fk.runner.policy = lambda ctx: ([RestIntent("s1r3", f"k:{s}", s, Side.BUY, 49.0, Priority.SNIPE, qty=1)
                                         for s in SYMBOLS] if state["on"] else [])
        ts = FastTime(1.0)
        fk.clock.open_mono = -200.0
        api = FakeAPI()
        bridge = ResultBridge()
        ex = FakeExecutor(api, bridge, delay={ApiKind.PLACE: 0.5})
        pipe = _Pipe()
        log = ListLog()

        async def go():
            loop = asyncio.get_running_loop()
            loop.call_later(0.6, pipe.send, "k")
            loop.call_later(1.5, pipe.send, "q")
            return await run_shell(core, [TickSource(ts)], ex, api, fk.engine, ts, log, None, console=False,
                                   stdin=pipe.r, result_bridge=bridge, install_signals=False)

        try:
            rc = asyncio.run(go())
        finally:
            pipe.close()
        self.assertEqual(rc, 0)
        i_freeze = ex.calls.index(("freeze",))
        i_kill = ex.calls.index(("submit", ApiKind.CANCEL_ALL, Priority.KILL))
        self.assertEqual(i_kill, i_freeze + 1)                  # the kill's CANCEL_ALL right after the freeze
        self.assertNotIn(ApiKind.PLACE, [c[1] for c in ex.calls[i_freeze:] if c[0] == "submit"])
        self.assertGreaterEqual(len([c for c in ex.calls[:i_freeze] if c[:2] == ("submit", ApiKind.PLACE)]), 12)


class TestKillDrill(unittest.TestCase):
    def test_no_orders_left_1s_after_kill(self):
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
        clock.open_mono = ts.mono() - 200.0                    # mid-storm, clock known and running
        runner = FakeRunner()
        sent = {"done": False}
        runner.policy = lambda ctx: ([] if sent["done"] else
                                     [RestIntent("s1r3", f"drill:{s}", s, Side.BUY, 49.0, Priority.SNIPE, qty=5)
                                      for s in SYMBOLS])
        norm = FakeNormalizer()
        fac = dict(default_factories())
        fac.update({"normalizer": lambda **_: norm, "clock": lambda **_: clock,
                    "coverage": lambda **_: FakeCoverage(), "laws": lambda **_: None,
                    "law_check": lambda **_: FakeLawCheck(calls), "runner": lambda **_: runner})
        log = ListLog()
        eng = FakeEngine(calls)
        core = BotCore(cfg, case, case_mod.diff_case(case.raw, case.raw), eng, ts, log, mode="host", run_dir=None,
                       run_id="drill", laws_module=None, cost=None, factories=fac)
        exch = SlowExchange(1.5)
        bridge = ResultBridge()
        ex = rest.RestExecutor(exch, 12, bridge, log, kill_workers=1)
        pipe = _Pipe()
        marks: dict[str, float] = {}

        def driver() -> None:
            end = time.monotonic() + 10.0
            while time.monotonic() < end:                       # wait for 12 PLACEs blocked in the exchange
                with exch.lock:
                    if exch.inflight >= 12:
                        break
                time.sleep(0.005)
            sent["done"] = True
            time.sleep(0.1)
            marks["k"] = pipe.send("k")
            end = time.monotonic() + 10.0
            while time.monotonic() < end:                       # until every POST has resolved
                with exch.lock:
                    if len(exch.place_done) >= len(exch.place_arrivals) and exch.inflight == 0:
                        break
                time.sleep(0.01)
            marks["last_post"] = max(exch.place_done) if exch.place_done else time.monotonic()
            time.sleep(1.0)
            with exch.lock:
                marks["open_after_1s"] = float(len(exch.open))
            pipe.send("q")

        th = threading.Thread(target=driver, daemon=True)

        async def go():
            asyncio.get_running_loop().call_later(0.05, th.start)
            return await run_shell(core, [TickSource(ts)], ex, exch, eng, ts, log, None, console=False,
                                   stdin=pipe.r, result_bridge=bridge, install_signals=False)

        try:
            rc = asyncio.run(go())
        finally:
            pipe.close()
        self.assertEqual(rc, 0)
        self.assertIn("k", marks, "the 12 PLACEs never got in flight")
        self.assertGreaterEqual(len(exch.place_arrivals), 12)
        kills = [t for t, p in exch.cancel_all_arrivals if p == Priority.KILL and t >= marks["k"]]
        self.assertTrue(kills)
        self.assertLess(kills[0] - marks["k"], 0.100, f"kill latency {kills[0] - marks['k']:.3f} s")
        self.assertEqual([t for t in exch.place_arrivals if t > kills[0]], [])   # no PLACE after the kill
        self.assertTrue([t for t in kills if t > marks["last_post"] - 1.6])     # follow-ups after the late acks
        self.assertEqual(marks["open_after_1s"], 0.0)
        sys.stderr.write(f"\n[kill drill] cancel-all after {(kills[0] - marks['k']) * 1e3:.1f} ms, "
                         f"{len(kills)} kill CANCEL_ALLs, {len(exch.place_arrivals)} PLACEs, open 1 s after the last "
                         f"POST: {int(marks['open_after_1s'])}\n")
        overdraft = int(cfg.get("rate.kill_overdraft"))
        self.assertLessEqual(len(kills), 1 + overdraft)
        self.assertEqual(log.kinds("exception"), [])


if __name__ == "__main__":
    unittest.main()
