"""Group D tests: the asyncio shell (DESIGN.md §9.1 D shell).

Unit tests drive ``run_shell`` with a fake core, fake sources, a fake executor and a fake API. The integration
test replays the first 300 s of the testnet recording at speed 50 through two real ``RecordingSource``s into the
real ``BotCore`` (default factories) with a fake API.
"""
from __future__ import annotations

import asyncio
import io
import itertools
import json
import os
import signal
import sys
import threading
import time
import unittest
from collections import Counter
from typing import Any, Callable

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

from ctcbot.types import (ApiKind, ApiRequest, ApiResult, Command, ListLog, OrderAck, Priority,  # noqa: E402
                          ServerTime, SourceFrame, SourceStats)
from ctcbot.app.shell import ResultBridge, run_shell  # noqa: E402
from test_d_core import FakeConfig, FakeEngine, CallLog  # noqa: E402


# ============================================================================ fakes
class FastTime:
    """A scaled clock owned by the test (speed x real time)."""

    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed
        self._t0 = time.monotonic()
        self._w0 = time.time()

    def mono(self) -> float:
        return (time.monotonic() - self._t0) * self.speed

    def wall(self) -> float:
        return self._w0 + (time.monotonic() - self._t0) * self.speed


def recording_head(seconds: float) -> list[tuple[float, dict]]:
    path = testpaths.require(testpaths.RECORDING)
    out: list[tuple[float, dict]] = []
    first = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            fr = json.loads(line)
            ra = fr.pop("received_at")
            if first is None:
                first = ra
            if ra - first >= seconds:
                break
            out.append((ra, fr))
    return out


class ListSource:
    """MarketDataSource emitting pre-loaded frames paced on a FastTime; ends (or idles) afterwards."""

    def __init__(self, name: str, frames: list[tuple[float, dict]], timesrc: FastTime, linger: bool = False) -> None:
        self.name = name
        self.frames = frames
        self.timesrc = timesrc
        self.linger = linger
        self._stop = False
        self.n = 0

    async def run(self, emit: Callable[[SourceFrame], None]) -> None:
        emit(SourceFrame({}, time.time(), self.timesrc.mono(), f"{self.name}#1", control="up"))
        first = self.frames[0][0] if self.frames else 0.0
        m0 = self.timesrc.mono()
        for ra, fr in self.frames:
            if self._stop:
                break
            wait = (m0 + (ra - first)) - self.timesrc.mono()
            if wait > 0.0005:
                await asyncio.sleep(wait / self.timesrc.speed)
            self.n += 1
            emit(SourceFrame(dict(fr), ra, self.timesrc.mono(), f"{self.name}#1"))
        while self.linger and not self._stop:
            await asyncio.sleep(0.02)
        emit(SourceFrame({}, time.time(), self.timesrc.mono(), f"{self.name}#1", control="down"))

    def stop(self) -> None:
        self._stop = True

    def stats(self) -> SourceStats:
        return SourceStats(self.name, 1, self.n, 0, None, 0)


class FakeAPI:
    """ExchangeAPI that answers everything 200; order actions as dry-run, like a RestClient without a token."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ApiRequest, float]] = []
        self.lock = threading.Lock()

    def execute(self, req: ApiRequest) -> ApiResult:
        with self.lock:
            self.calls.append((threading.current_thread().name, req, time.monotonic()))
        now = time.monotonic()
        dry = req.is_order_action
        parsed: Any = None
        if req.kind == ApiKind.GET_TIME:
            parsed = ServerTime("c1", "running", "1803-05-11T11:30:00", 1.0)
        elif req.kind == ApiKind.GET_OPEN:
            parsed = []
        elif req.kind == ApiKind.GET_POSITIONS:
            parsed = {}
        elif req.kind == ApiKind.PLACE:
            parsed = OrderAck(f"dry-{req.req_id}", "dry_run", 0, req.qty, None)
        return ApiResult(req=req, status=200, data={}, parsed=parsed, error=None, sent_mono=now, done_mono=now,
                         dry_run=dry, sent=not dry)

    def prewarm(self) -> None:
        pass


class FakeExecutor:
    """Executor look-alike: answers each submit on a thread after ``delay`` (per kind), records call order."""

    def __init__(self, api: Any, on_result: Callable[[ApiResult], None], delay: dict | None = None) -> None:
        self.api = api
        self.on_result = on_result
        self.delay = delay or {}
        self.calls: list[tuple] = []
        self.frozen = False
        self.closed = 0
        self.prewarms = 0

    def submit(self, req: ApiRequest) -> None:
        self.calls.append(("submit", req.kind, req.priority))
        if self.frozen and req.kind == ApiKind.PLACE:
            now = time.monotonic()
            self.on_result(ApiResult(req, None, None, None, "frozen", now, now, sent=False))
            return

        def run() -> None:
            d = self.delay.get(req.kind, 0.0)
            if d:
                time.sleep(d)
            self.on_result(self.api.execute(req))

        threading.Thread(target=run, daemon=True).start()

    def freeze(self) -> None:
        self.calls.append(("freeze",))
        self.frozen = True

    def unfreeze(self) -> None:
        self.calls.append(("unfreeze",))
        self.frozen = False

    def prewarm_all(self) -> None:
        self.prewarms += 1

    def close(self, timeout: float = 2.0) -> None:
        self.closed += 1


class FakeShellCore:
    """The BotCore surface the shell uses, with recording of every call."""

    def __init__(self, engine: FakeEngine, cfg_over: dict | None = None) -> None:
        self.cfg = FakeConfig(cfg_over)
        self.engine = engine
        self.ids = itertools.count(1)
        self.frames: list[SourceFrame] = []
        self.batches = 0
        self.jobs_run: Counter[str] = Counter()
        self.commands: list[Command] = []
        self.results: list[ApiResult] = []
        self.outbox: list[ApiRequest] = []
        self.control: list[str] = []
        self.output: list[str] = []
        self.quit_requested = False
        self.kill_acks = 0
        self._killed = False
        self.started: float | None = None
        self.shutdowns = 0
        self.fv_calls = 0
        self.notes = 0
        self.fail_frames_every = 0
        self.closed = 0

    def next_req_id(self) -> int:
        return next(self.ids)

    def start(self, now: float) -> None:
        self.started = now
        self.outbox.append(ApiRequest(self.next_req_id(), ApiKind.GET_TIME, Priority.HEDGE))

    def on_frames(self, frames, now) -> None:
        self.batches += 1
        self.frames.extend(frames)
        if self.fail_frames_every and self.batches % self.fail_frames_every == 0:
            raise RuntimeError("frame handling failed")

    def on_fair_values(self, now) -> None:
        self.fv_calls += 1

    def on_api_result(self, res, now) -> None:
        self.results.append(res)
        if res.req.kind == ApiKind.CANCEL_ALL and res.req.priority == Priority.KILL:
            self.kill_acks += 1

    def on_command(self, cmd: Command, now: float) -> None:
        self.commands.append(cmd)
        if cmd.name == "k":
            self._killed = True
            self.control.append("freeze")
            self.outbox.append(ApiRequest(self.next_req_id(), ApiKind.CANCEL_ALL, Priority.KILL))
        elif cmd.name == "q":
            self.quit_requested = True
        elif cmd.name == "st":
            self.output.append("status-line")

    def jobs(self):
        return [("reconcile", 0.5), ("tick", 0.25)]

    def run_job(self, name, now) -> None:
        self.jobs_run[name] += 1

    def take_requests(self):
        out, self.outbox = self.outbox, []
        return out

    def take_control(self):
        out, self.control = self.control, []
        return out

    def take_output(self):
        out, self.output = self.output, []
        return out

    def status(self, now) -> dict:
        return {"label": "DRY", "mode": "NORMAL", "epoch": 0}

    def shutdown(self, now) -> None:
        self.shutdowns += 1

    def killed(self) -> bool:
        return self._killed

    def note_exception(self, where, now) -> None:
        self.notes += 1

    def set_engine_listener(self, cb) -> None:
        self.engine.set_listener(cb)

    def close(self) -> None:
        self.closed += 1


def make(cfg_over: dict | None = None, delay: dict | None = None):
    eng = FakeEngine(CallLog())
    core = FakeShellCore(eng, cfg_over)
    api = FakeAPI()
    bridge = ResultBridge()
    ex = FakeExecutor(api, bridge, delay)
    return core, eng, api, bridge, ex


async def _run_and_check_tasks(coro) -> tuple[int, list]:
    rc = await coro
    leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    return rc, leftover


# ============================================================================ unit tests
class TestShellUnit(unittest.TestCase):
    def test_two_sources_replay_300s_at_speed_50(self):
        head = recording_head(300.0)
        ts = FastTime(50.0)
        core, eng, api, bridge, ex = make()
        core.fail_frames_every = 25                           # a failing core call is logged, never fatal
        log = ListLog()
        d = testpaths.tmpdir("shell")
        srcs = [ListSource("ws0", head, ts), ListSource("ws1", head, ts)]
        t0 = time.monotonic()
        rc, leftover = asyncio.run(_run_and_check_tasks(
            run_shell(core, srcs, ex, api, eng, ts, log, d, console=False, stdin=io.StringIO(""),
                      result_bridge=bridge, install_signals=False)))
        wall = time.monotonic() - t0
        self.assertEqual(rc, 0)
        self.assertEqual(leftover, [])
        data = [f for f in core.frames if f.control is None]
        self.assertEqual(len(data), 2 * len(head))
        self.assertEqual({f.conn for f in data}, {"ws0#1", "ws1#1"})
        self.assertLess(core.batches, len(data))              # bursts were coalesced
        self.assertLess(wall, 300.0 / 50.0 + 6.0)
        self.assertGreater(core.jobs_run["tick"], 300 / 0.25 * 0.5)  # job loops ran on the scaled clock
        self.assertEqual(core.shutdowns, 1)
        self.assertEqual(ex.prewarms, 1)
        self.assertEqual(ex.closed, 1)
        self.assertEqual(eng.closed, 1)
        self.assertEqual(core.closed, 1)
        excs = log.kinds("exception")
        self.assertEqual(len(excs), core.batches // 25)
        self.assertEqual(core.notes, len(excs))
        self.assertEqual(len(log.kinds("console_eof")), 1)
        self.assertEqual(len(log.kinds("sources_done")), 1)
        self.assertTrue(log.kinds("loop_lag"))
        # the raw feed was recorded in the record_feed format
        with open(os.path.join(d, "feed_raw.jsonl")) as f:
            n = sum(1 for _ in f)
        self.assertEqual(n, 2 * len(head))

    def test_quit_from_console_cancels_cleanly(self):
        ts = FastTime(1.0)
        core, eng, api, bridge, ex = make()
        log = ListLog()
        r, w = os.pipe()
        stdin = os.fdopen(r, "r")
        wr = os.fdopen(w, "w")

        def later() -> None:
            time.sleep(0.4)
            wr.write("st\n")
            wr.flush()
            time.sleep(0.2)
            wr.write("q\n")
            wr.flush()

        threading.Thread(target=later, daemon=True).start()
        out = io.StringIO()
        rc, leftover = asyncio.run(_run_and_check_tasks(
            run_shell(core, [ListSource("ws0", [], ts, linger=True)], ex, api, eng, ts, log, None, console=True,
                      stdin=stdin, result_bridge=bridge, status_stream=out, install_signals=False)))
        wr.close()
        stdin.close()
        self.assertEqual(rc, 0)
        self.assertEqual(leftover, [])
        self.assertEqual([c.name for c in core.commands], ["st", "q"])
        self.assertIn("status-line", out.getvalue())
        self.assertEqual(core.shutdowns, 0)                    # q already shut the core down itself

    def test_closed_stdin_stops_console_without_spinning(self):
        ts = FastTime(1.0)
        core, eng, api, bridge, ex = make()
        log = ListLog()

        async def go():
            loop = asyncio.get_running_loop()
            loop.call_later(0.8, setattr, core, "quit_requested", True)
            return await run_shell(core, [ListSource("ws0", [], ts, linger=True)], ex, api, eng, ts, log, None,
                                   console=False, stdin=io.StringIO(""), result_bridge=bridge, install_signals=False)

        t0 = time.process_time()
        rc = asyncio.run(go())
        cpu = time.process_time() - t0
        self.assertEqual(rc, 0)
        self.assertEqual(len(log.kinds("console_eof")), 1)
        self.assertLess(cpu, 0.6)                              # an EOF loop would burn the whole 0.8 s

    def test_kill_file_triggers_k_once(self):
        ts = FastTime(1.0)
        core, eng, api, bridge, ex = make()
        log = ListLog()
        d = testpaths.tmpdir("shellkill")

        async def go():
            loop = asyncio.get_running_loop()
            loop.call_later(0.2, lambda: open(os.path.join(d, "KILL"), "w").close())
            loop.call_later(1.5, setattr, core, "quit_requested", True)
            return await run_shell(core, [ListSource("ws0", [], ts, linger=True)], ex, api, eng, ts, log, d,
                                   console=False, stdin=None, result_bridge=bridge, install_signals=False)

        asyncio.run(go())
        ks = [c for c in core.commands if c.name == "k"]
        self.assertEqual(len(ks), 1)
        self.assertEqual(ks[0].source, "killfile")
        i_freeze = ex.calls.index(("freeze",))
        i_cancel = ex.calls.index(("submit", ApiKind.CANCEL_ALL, Priority.KILL))
        self.assertLess(i_freeze, i_cancel)                    # frozen before the kill's CANCEL_ALL is submitted

    def test_engine_listener_coalesces(self):
        ts = FastTime(1.0)
        core, eng, api, bridge, ex = make()
        log = ListLog()
        d = testpaths.tmpdir("shellfv")
        from ctcbot.types import FairValueSnapshot, SYMBOLS

        def hammer() -> None:
            for i in range(2000):
                eng.fv = FairValueSnapshot(version=i, values={s: (10.0 + i, 1.0) for s in SYMBOLS}, season_epoch=0,
                                           round=1, seq_hi=i, msg_time=None, included_mono=0.0, computed_mono=0.0,
                                           compute_ms=5.0, ok=True)
                eng.listener()

        async def go():
            loop = asyncio.get_running_loop()
            loop.call_later(0.2, lambda: threading.Thread(target=hammer, daemon=True).start())
            loop.call_later(1.2, setattr, core, "quit_requested", True)
            return await run_shell(core, [ListSource("ws0", [], ts, linger=True)], ex, api, eng, ts, log, d,
                                   console=False, stdin=None, result_bridge=bridge, install_signals=False)

        asyncio.run(go())
        self.assertGreaterEqual(core.fv_calls, 1)
        self.assertLess(core.fv_calls, 2000)
        with open(os.path.join(d, "fv.jsonl")) as f:
            recs = [json.loads(x) for x in f]
        self.assertTrue(recs)
        self.assertEqual(recs[-1]["kind"], "fv")
        self.assertEqual(recs[-1]["version"], 1999)
        self.assertEqual(len({r["version"] for r in recs}), len(recs))   # one line per snapshot version

    def test_sigint_kills_waits_and_exits(self):
        if threading.current_thread() is not threading.main_thread():
            self.skipTest("signals need the main thread")
        ts = FastTime(1.0)
        core, eng, api, bridge, ex = make(delay={ApiKind.CANCEL_ALL: 0.3})
        log = ListLog()

        async def go():
            loop = asyncio.get_running_loop()
            loop.call_later(0.3, os.kill, os.getpid(), signal.SIGINT)
            return await run_shell(core, [ListSource("ws0", [], ts, linger=True)], ex, api, eng, ts, log, None,
                                   console=False, stdin=None, result_bridge=bridge)

        t0 = time.monotonic()
        rc = asyncio.run(go())
        self.assertEqual(rc, 130)
        self.assertEqual(core.commands[0], Command("k", ("signal",), "signal"))
        self.assertEqual(core.kill_acks, 1)                    # it waited for the cancel result
        self.assertLess(time.monotonic() - t0, 0.3 + 2.5)
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)

    def test_second_sigint_hard_cancel_on_main_thread(self):
        if threading.current_thread() is not threading.main_thread():
            self.skipTest("signals need the main thread")
        ts = FastTime(1.0)
        core, eng, api, bridge, ex = make(delay={ApiKind.CANCEL_ALL: 1.5})
        log = ListLog()

        async def go():
            loop = asyncio.get_running_loop()
            loop.call_later(0.3, os.kill, os.getpid(), signal.SIGINT)
            loop.call_later(0.6, os.kill, os.getpid(), signal.SIGINT)
            return await run_shell(core, [ListSource("ws0", [], ts, linger=True)], ex, api, eng, ts, log, None,
                                   console=False, stdin=None, result_bridge=bridge)

        rc = asyncio.run(go())
        self.assertEqual(rc, 130)
        hard = [c for c in api.calls if c[1].kind == ApiKind.CANCEL_ALL and c[0] == threading.main_thread().name]
        self.assertEqual(len(hard), 1)
        self.assertEqual(hard[0][1].priority, Priority.KILL)
        self.assertTrue(log.kinds("hard_cancel"))
        self.assertTrue(log.kinds("hard_cancel")[0]["dry_run"])

    def test_result_bridge_buffers_and_drops(self):
        b = ResultBridge()
        got: list = []
        req = ApiRequest(1, ApiKind.GET_TIME)
        res = ApiResult(req, 200, {}, None, None, 0.0, 0.0)
        b(res)                                                 # before binding: buffered

        async def go():
            b.bind(asyncio.get_running_loop(), got.append)
            await asyncio.sleep(0.01)
            b(res)
            await asyncio.sleep(0.01)

        asyncio.run(go())
        self.assertEqual(len(got), 2)
        b(res)                                                 # loop closed: dropped, no exception
        self.assertEqual(b.dropped, 1)


# ============================================================================ integration
class TestShellIntegration(unittest.TestCase):
    def test_real_core_two_recording_sources(self):
        mods = testpaths.integration("ctcbot.config", "ctcbot.case", "ctcbot.sources", "ctcbot.timesource",
                                     "ctcbot.normalize", "ctcbot.clock", "ctcbot.exec.order_manager",
                                     "ctcbot.strategies.runner", "ctcbot.models.cost_model", "ctcbot.models.laws",
                                     "ctcbot.rest")
        config, case_mod, sources, timesource = mods[:4]
        from ctcbot.app.core import BotCore
        from ctcbot.models.cost_model import CostModel
        from ctcbot.rest import RestExecutor
        rec = testpaths.require(testpaths.RECORDING)
        cfg = config.load_config()
        case = case_mod.CaseInfo.from_file(testpaths.require(testpaths.RESEARCH_CASE))
        cost = CostModel(testpaths.require(testpaths.COST_MODEL), cfg)
        first = next(iter(sources.iter_recording(rec)))[0]
        ts = timesource.ScaledTime(50.0)
        log = ListLog()
        eng = FakeEngine(CallLog())
        core = BotCore(cfg, case, case_mod.diff_case(case.raw, case.raw), eng, ts, log, mode="replay",
                       run_dir=None, run_id="shellit", laws_module=None, cost=cost)
        srcs = [sources.RecordingSource(rec, ts, log, name=n, paced=True, t_to=first + 300.0) for n in ("r0", "r1")]
        api = FakeAPI()
        bridge = ResultBridge()
        ex = RestExecutor(api, 4, bridge, log)
        rc = asyncio.run(run_shell(core, srcs, ex, api, eng, ts, log, None, console=False, stdin=io.StringIO(""),
                                   result_bridge=bridge, install_signals=False))
        self.assertEqual(rc, 0)
        excs = log.kinds("exception")
        self.assertEqual(excs, [], excs[:2])
        self.assertEqual(core.report()["exceptions"], 0)
        st = core.normalizer.stats()
        self.assertTrue(st)
        self.assertGreater(len(eng.submits), 10)
        self.assertEqual(core.report()["relevant_snapshots"][:1], [695])     # the join snapshot reached the engine
        self.assertEqual(core.report()["resets"]["confirmed"], 0)
        kinds_seen = {c[1].kind for c in api.calls}
        self.assertTrue({ApiKind.GET_TIME, ApiKind.GET_OPEN, ApiKind.GET_POSITIONS} <= kinds_seen)


if __name__ == "__main__":
    unittest.main()
