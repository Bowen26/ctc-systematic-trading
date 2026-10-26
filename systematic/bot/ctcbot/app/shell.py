"""asyncio orchestration of the core (DESIGN.md sections 4.D ``app/shell.py`` and 5.1).

:func:`run_shell` runs, on one event loop:

* one ``source.run(emit)`` task per market-data source, all appending to one queue;
* a consumer that awaits one frame, drains the rest without waiting and hands the batch to ``core.on_frames``;
* one loop per core job (``core.jobs()``), scheduled on the ``TimeSource`` with drift correction;
* a KILL-file watcher (every 0.25 s), a console reader and (interactive only) a status printer.

Results of the REST executor's worker threads come back through a :class:`ResultBridge`
(``loop.call_soon_threadsafe``), engine notifications through the engine listener (coalesced: at most one
call queued). After every core call :func:`flush` applies the core's control words first (``freeze``,
``unfreeze``, ``prewarm_all``) and then submits its requests, so after ``k`` the executor is frozen before the
kill's CANCEL_ALL is submitted and no PLACE leaves after it.

Signals: the first SIGINT, SIGTERM or SIGHUP runs ``k``; the loop, the jobs and the executor keep running until
the kill is complete (every order known at the kill terminal and two clean reconcile cycles, so late PLACE acks
get their follow-up CANCEL_ALLs) or ``app.shutdown_kill_wait_s`` passes, then the bot exits. ``q`` and the end of
the sources take the same kill path (hardening, DESIGN.md §13). A second signal makes one synchronous best-effort
CANCEL_ALL through the same ``ExchangeAPI`` (the ``RestClient``, so the same ``LiveToken`` gate applies: a dry
no-op in dry-run) and exits at once; later signals are ignored.

The console is read by a daemon thread rather than ``loop.run_in_executor``: a blocking ``readline`` in the
default executor would keep ``asyncio.run`` from returning at shutdown until the operator pressed Enter.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
import time
import traceback
from typing import TYPE_CHECKING, Any, Callable, Sequence, TextIO

from ctcbot.types import (ORDER_ACTIONS, ApiKind, ApiRequest, ApiResult, Command, EventLog, ExchangeAPI,
                          FairValueProvider, MarketDataSource, Priority, SourceFrame, TimeSource)

from ctcbot.app import console as _console
from ctcbot.app.log import FrameRecorder, JsonlLog, install_redaction
from ctcbot.app.status import format_status

if TYPE_CHECKING:  # pragma: no cover
    from ctcbot.app.core import BotCore
    from ctcbot.rest import RestExecutor


class ResultBridge:
    """Thread-safe ``on_result`` callback for the REST executor.

    The cli builds the executor before the loop exists and passes it this bridge; :func:`run_shell` binds it to
    the loop. Results that arrive before binding are queued and delivered on binding; results that arrive after
    the loop closed are dropped.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handler: Callable[[ApiResult], None] | None = None
        self._early: list[ApiResult] = []
        self.dropped = 0

    def bind(self, loop: asyncio.AbstractEventLoop, handler: Callable[[ApiResult], None]) -> None:
        with self._lock:
            self._loop, self._handler = loop, handler
            early, self._early = self._early, []
        for res in early:
            self._post(loop, handler, res)

    def unbind(self) -> None:
        with self._lock:
            self._loop, self._handler = None, None

    def __call__(self, res: ApiResult) -> None:
        with self._lock:
            loop, handler = self._loop, self._handler
            if loop is None or handler is None:
                if len(self._early) < 10_000:
                    self._early.append(res)
                else:
                    self.dropped += 1
                return
        self._post(loop, handler, res)

    def _post(self, loop: asyncio.AbstractEventLoop, handler: Callable[[ApiResult], None], res: ApiResult) -> None:
        try:
            loop.call_soon_threadsafe(handler, res)
        except RuntimeError:          # loop closed: the run is over
            self.dropped += 1


class _Shell:
    """State of one :func:`run_shell` call."""

    def __init__(self, core: "BotCore", sources: Sequence[MarketDataSource], executor: "RestExecutor",
                 api: ExchangeAPI, engine: FairValueProvider, timesrc: TimeSource, log: EventLog,
                 run_dir: str | None, console: bool, stdin: TextIO | None, bridge: ResultBridge | None,
                 secrets: Sequence[str], status_stream: TextIO | None, install_signals: bool) -> None:
        self.core = core
        self.sources = list(sources)
        self.executor = executor
        self.api = api
        self.engine = engine
        self.timesrc = timesrc
        self.log = log
        self.run_dir = run_dir
        self.console = bool(console)
        self.stdin = stdin
        self.bridge = bridge
        self.secrets = [s for s in secrets if s]
        self.status_stream = status_stream
        self.install_signals = install_signals
        cfg = getattr(core, "cfg", None)
        get = cfg.get if cfg is not None else (lambda k, d=None: d)
        self.shutdown_wait = float(get("app.shutdown_wait_s", 2.0))
        self.kill_wait = max(self.shutdown_wait, float(get("app.shutdown_kill_wait_s", 5.0)))
        self.status_s = float(get("app.status_s", 2.0))
        kill_name = str(get("app.kill_file", "KILL"))
        self.kill_file = os.path.join(run_dir, kill_name) if run_dir else None
        self.record_raw = bool(get("feed.record_raw", True))
        self.exc_hold = int(get("app.exceptions_per_min_hold", 10))
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue | None = None
        self.stop_ev: asyncio.Event | None = None
        self.console_q: asyncio.Queue | None = None
        self.stop_reason: str | None = None
        self.hard = False
        self.hard_done = False
        self.signals = 0
        self.kill_acks0 = 0
        self.outstanding: dict[int, ApiKind] = {}
        self.fv_pending = False
        self.fv_last: Any = None
        self.recorder: FrameRecorder | None = None
        self.fv_log: JsonlLog | None = None
        self.tasks: list[asyncio.Task] = []
        self.source_tasks: list[asyncio.Task] = []
        self.handlers: list[int] = []
        self.exceptions = 0
        self.frames = 0
        self.batches = 0
        self.lag_max = 0.0

    # ------------------------------------------------------------------ helpers
    def mono(self) -> float:
        return float(self.timesrc.mono())

    def speed(self) -> float:
        try:
            s = float(getattr(self.timesrc, "speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            s = 1.0
        return s if s > 0 else 1.0

    def call(self, where: str, fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
        """Run one core call; an exception is logged and counted, never propagated."""
        try:
            return fn(*args, **kw)
        except Exception as e:  # noqa: BLE001
            self.exceptions += 1
            try:
                self.log.event("exception", where=f"shell:{where}", error=f"{type(e).__name__}: {e}",
                               traceback=traceback.format_exc())
            except Exception:  # noqa: BLE001
                pass
            note = getattr(self.core, "note_exception", None)
            if callable(note):
                try:
                    note(where, self.mono())
                except Exception:  # noqa: BLE001
                    pass
            return None

    def write(self, text: str) -> None:
        stream = self.status_stream if self.status_stream is not None else sys.stderr
        try:
            stream.write(text + "\n")
            stream.flush()
        except Exception:  # noqa: BLE001
            pass

    def flush(self) -> None:
        """Apply control words, then submit requests, then print console output."""
        core = self.core
        controls = self.call("take_control", core.take_control) or []
        for c in controls:
            if c == "freeze":
                self.call("executor.freeze", self.executor.freeze)
            elif c == "unfreeze":
                self.call("executor.unfreeze", self.executor.unfreeze)
            elif c == "prewarm_all":
                self.call("executor.prewarm_all", self.executor.prewarm_all)
            else:
                self.log.event("control_unknown", control=str(c))
        reqs = self.call("take_requests", core.take_requests) or []
        for req in reqs:
            self.outstanding[req.req_id] = req.kind
            if req.kind in ORDER_ACTIONS:
                self.call("log.submit", self.log.event, "submit", req_id=req.req_id, req_kind=str(req.kind),
                          priority=int(req.priority), symbol=req.symbol,
                          side=None if req.side is None else str(req.side), qty=req.qty, price=req.price,
                          local_id=req.local_id, mono=self.mono())
            try:
                self.executor.submit(req)
            except Exception as e:  # noqa: BLE001 - answer it as never sent, on the next loop turn
                self.outstanding.pop(req.req_id, None)
                self.call("executor.submit", self._raise, e)
                now = self.mono()
                res = ApiResult(req=req, status=None, data=None, parsed=None, error=f"submit_failed: "
                                f"{type(e).__name__}", sent_mono=now, done_mono=now, sent=False)
                if self.loop is not None:
                    self.loop.call_soon(self.on_result, res)
        take_output = getattr(core, "take_output", None)
        if callable(take_output):
            lines = self.call("take_output", take_output) or []
            if lines and self.console:
                for line in lines:
                    self.write(str(line))
        if getattr(core, "quit_requested", False):
            self.request_stop("quit")

    @staticmethod
    def _raise(e: BaseException) -> None:
        raise e

    def request_stop(self, reason: str) -> None:
        if self.stop_ev is not None and not self.stop_ev.is_set():
            self.stop_reason = reason
            self.stop_ev.set()

    # ------------------------------------------------------------------ callbacks
    def emit(self, sf: SourceFrame) -> None:
        q = self.queue
        if q is not None:
            q.put_nowait(sf)

    def on_result(self, res: ApiResult) -> None:
        try:
            self.outstanding.pop(res.req.req_id, None)
        except Exception:  # noqa: BLE001
            pass
        self.call("on_api_result", self.core.on_api_result, res, self.mono())
        self.flush()

    def engine_notify(self) -> None:
        """Engine listener; called on the engine's worker thread (or inline)."""
        if self.fv_pending:
            return
        self.fv_pending = True
        loop = self.loop
        if loop is None:
            self.fv_pending = False
            return
        try:
            loop.call_soon_threadsafe(self.on_fv)
        except RuntimeError:
            self.fv_pending = False

    def on_fv(self) -> None:
        self.fv_pending = False
        self.call("on_fair_values", self.core.on_fair_values, self.mono())
        self.flush()
        self.write_fv()

    def write_fv(self) -> None:
        if self.fv_log is None:
            return
        eng = getattr(self.core, "engine", None) or self.engine
        fv = self.call("engine.latest", eng.latest)
        if fv is None:
            return
        ver = getattr(fv, "version", None)
        if ver == self.fv_last:
            return
        self.fv_last = ver
        try:
            means = {s: round(float(v[0]), 6) for s, v in dict(fv.values).items()}
            sds = {s: round(float(v[1]), 6) for s, v in dict(fv.values).items()}
        except Exception:  # noqa: BLE001
            means, sds = {}, {}
        self.fv_log.event("fv", version=ver, engine_version=getattr(fv, "engine_version", None),
                          compute_ms=getattr(fv, "compute_ms", None), ok=getattr(fv, "ok", None),
                          season_epoch=getattr(fv, "season_epoch", None), round=getattr(fv, "round", None),
                          seq_hi=getattr(fv, "seq_hi", None), means=means, sds=sds)

    def command(self, cmd: Command) -> None:
        self.call("on_command", self.core.on_command, cmd, self.mono())
        self.flush()

    # ------------------------------------------------------------------ tasks
    async def run_source(self, src: MarketDataSource) -> None:
        try:
            await src.run(self.emit)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - a source must never raise; log and let the others run
            self.call(f"source:{getattr(src, 'name', '?')}", self._raise, e)

    async def consumer(self) -> None:
        q = self.queue
        assert q is not None
        while True:
            sf = await q.get()
            batch = [sf]
            while True:
                try:
                    batch.append(q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            self.batches += 1
            self.frames += len(batch)
            if self.recorder is not None:
                for f in batch:
                    try:
                        self.recorder.record(f)
                    except Exception:  # noqa: BLE001
                        pass
            self.call("on_frames", self.core.on_frames, batch, self.mono())
            self.flush()

    async def job_loop(self, name: str, period: float) -> None:
        period = max(1e-3, float(period))
        next_t = self.mono() + period
        while True:
            delay = (next_t - self.mono()) / self.speed()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(0)
            now = self.mono()
            self.call(f"job:{name}", self.core.run_job, name, now)
            self.flush()
            next_t += period
            if next_t <= now:                       # fell behind (a long pause): skip the missed runs
                next_t = now + period

    async def killfile_watch(self) -> None:
        if not self.kill_file:
            return
        last = -1e9
        while True:
            await asyncio.sleep(0.25)
            try:
                present = os.path.exists(self.kill_file)
            except OSError:
                present = False
            if not present:
                continue
            killed = getattr(self.core, "killed", None)
            is_killed = bool(self.call("killed", killed)) if callable(killed) else False
            if not is_killed and time.monotonic() - last >= 1.0:
                last = time.monotonic()
                self.command(Command("k", ("killfile",), source="killfile"))

    def console_thread(self) -> None:
        stdin = self.stdin
        loop = self.loop
        while True:
            try:
                line = stdin.readline() if stdin is not None else ""
            except Exception:  # noqa: BLE001 - a broken stdin counts as EOF
                line = ""
            if not isinstance(line, str):
                line = ""
            try:
                loop.call_soon_threadsafe(self.console_q.put_nowait, line)
            except RuntimeError:
                return
            if line == "":
                return

    async def console_task(self) -> None:
        q = self.console_q
        assert q is not None
        while True:
            line = await q.get()
            if line == "":
                self.log.event("console_eof")
                return
            cmd = _console.parse(line)
            if cmd is not None:
                self.command(cmd)

    async def status_printer(self) -> None:
        while True:
            await asyncio.sleep(max(0.2, self.status_s))
            st = self.call("status", self.core.status, self.mono())
            if isinstance(st, dict):
                self.write(format_status(st))

    async def lag_monitor(self) -> None:
        """Samples the overshoot of a 10-ms sleep every 50 ms; logs ``loop_lag`` (max, p99) once per second."""
        loop = self.loop
        assert loop is not None
        samples: list[float] = []
        last = loop.time()
        while True:
            t0 = loop.time()
            await asyncio.sleep(0.010)
            samples.append(max(0.0, loop.time() - t0 - 0.010))
            await asyncio.sleep(0.040)
            if loop.time() - last >= 1.0 and samples:
                srt = sorted(samples)
                p99 = srt[min(len(srt) - 1, int(0.99 * len(srt)))]
                self.lag_max = max(self.lag_max, srt[-1])
                self.log.event("loop_lag", mono=self.mono(), n=len(srt), max_ms=round(srt[-1] * 1e3, 3),
                               p99_ms=round(p99 * 1e3, 3))
                samples = []
                last = loop.time()

    async def sources_watch(self) -> None:
        if not self.source_tasks:
            return
        await asyncio.gather(*self.source_tasks, return_exceptions=True)
        q = self.queue
        while q is not None and not q.empty():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)                  # let the consumer finish the last batch
        self.log.event("sources_done")
        self.request_stop("sources_done")

    # ------------------------------------------------------------------ signals
    def on_signal(self, signum: int) -> None:
        try:
            self._on_signal(signum)
        except Exception as e:  # noqa: BLE001 - a signal must always end the run
            self.call("on_signal", self._raise, e)
            self.request_stop("signal")

    def _on_signal(self, signum: int) -> None:
        self.signals += 1
        self.log.event("signal", signum=int(signum), count=self.signals)
        if self.signals == 1:
            self.kill_acks0 = int(getattr(self.core, "kill_acks", 0) or 0)
            self.command(Command("k", ("signal",), source="signal"))
            self.request_stop("signal")
        elif self.signals == 2:
            self.hard_cancel()
            self.hard = True
            self.stop_reason = "signal2"
            if self.stop_ev is not None:
                self.stop_ev.set()
        # later signals: already exiting at once; nothing more is sent (hardening: no unbudgeted CANCEL_ALLs)

    def hard_cancel(self) -> None:
        """One synchronous best-effort CANCEL_ALL through the same ExchangeAPI (the LiveToken gate applies).
        At most once per run."""
        if self.hard_done:
            return
        self.hard_done = True
        nid = getattr(self.core, "next_req_id", None)
        rid = nid() if callable(nid) else 2_000_000_000 + self.signals
        req = ApiRequest(req_id=rid, kind=ApiKind.CANCEL_ALL, priority=Priority.KILL, created_mono=self.mono())
        try:
            res = self.api.execute(req)
            self.log.event("hard_cancel", status=getattr(res, "status", None), dry_run=getattr(res, "dry_run", None),
                           sent=getattr(res, "sent", None), error=getattr(res, "error", None))
        except Exception as e:  # noqa: BLE001
            self.call("hard_cancel", self._raise, e)

    def _install_signals(self) -> None:
        if not self.install_signals or self.loop is None:
            return
        sigs = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGHUP"):
            sigs.append(signal.SIGHUP)          # terminal or SSH session closed: kill, like Ctrl-C (hardening)
        for sig in sigs:
            try:
                self.loop.add_signal_handler(sig, self.on_signal, int(sig))
                self.handlers.append(int(sig))
            except (NotImplementedError, RuntimeError, ValueError):
                pass

    def _remove_signals(self) -> None:
        if self.loop is None:
            return
        for sig in self.handlers:
            try:
                self.loop.remove_signal_handler(sig)
            except Exception:  # noqa: BLE001
                pass
        self.handlers.clear()

    # ------------------------------------------------------------------ main
    async def run(self) -> int:
        loop = asyncio.get_running_loop()
        self.loop = loop
        self.queue = asyncio.Queue()
        self.stop_ev = asyncio.Event()
        self.console_q = asyncio.Queue()
        inst = getattr(self.log, "install_redaction", None)
        if callable(inst):
            hooks = inst(self.secrets, loop)
        else:
            hooks = install_redaction(self.secrets, loop, log=self.log)
        try:
            return await self._run(loop)
        finally:
            self._remove_signals()
            try:
                hooks.uninstall()
            except Exception:  # noqa: BLE001
                pass

    async def _run(self, loop: asyncio.AbstractEventLoop) -> int:
        if self.run_dir:
            os.makedirs(self.run_dir, exist_ok=True)
            if self.record_raw:
                self.recorder = FrameRecorder(os.path.join(self.run_dir, "feed_raw.jsonl"), self.secrets)
            self.fv_log = JsonlLog(os.path.join(self.run_dir, "fv.jsonl"), self.secrets, timesrc=self.timesrc)
        if self.bridge is not None:
            self.bridge.bind(loop, self.on_result)
        else:
            self.log.alert("no_result_bridge", "the executor has no result bridge: results will not reach the core")
        setl = getattr(self.core, "set_engine_listener", None)
        if callable(setl):
            self.call("set_engine_listener", setl, self.engine_notify)
        else:
            self.call("engine.set_listener", self.engine.set_listener, self.engine_notify)
        self.call("start", self.core.start, self.mono())
        self.flush()
        self.call("executor.prewarm_all", self.executor.prewarm_all)

        for src in self.sources:
            self.source_tasks.append(asyncio.create_task(self.run_source(src), name=f"src:{src.name}"))
        self.tasks.extend(self.source_tasks)
        self.tasks.append(asyncio.create_task(self.consumer(), name="consumer"))
        jobs = self.call("jobs", self.core.jobs) or []
        for name, period in jobs:
            self.tasks.append(asyncio.create_task(self.job_loop(name, period), name=f"job:{name}"))
        self.tasks.append(asyncio.create_task(self.killfile_watch(), name="killfile"))
        if self.stdin is not None:
            th = threading.Thread(target=self.console_thread, name="console", daemon=True)
            th.start()
            self.tasks.append(asyncio.create_task(self.console_task(), name="console"))
        if self.console:
            self.tasks.append(asyncio.create_task(self.status_printer(), name="status"))
        self.tasks.append(asyncio.create_task(self.sources_watch(), name="sources_watch"))
        self.tasks.append(asyncio.create_task(self.lag_monitor(), name="lag_monitor"))
        self._install_signals()
        self.log.event("shell_start", sources=[getattr(s, "name", "?") for s in self.sources],
                       jobs=[j[0] for j in jobs], console=self.console, run_dir=self.run_dir)
        await self.stop_ev.wait()
        return await self._shutdown(loop)

    def _order_actions_pending(self) -> bool:
        return any(k in ORDER_ACTIONS for k in self.outstanding.values())

    async def _shutdown(self, loop: asyncio.AbstractEventLoop) -> int:
        reason = self.stop_reason or "stop"
        self.log.event("shell_stop", reason=reason, frames=self.frames, batches=self.batches,
                       exceptions=self.exceptions)
        if not self.hard:
            begin = getattr(self.core, "begin_shutdown", None)
            done = getattr(self.core, "shutdown_complete", None)
            if reason not in ("signal", "quit"):
                if callable(begin):
                    self.call("begin_shutdown", begin, reason, self.mono())     # sources done: the kill path too
                else:
                    self.call("shutdown", self.core.shutdown, self.mono())
                self.flush()
            # keep the loop, the jobs and the executor running until the kill is complete (late acks get their
            # follow-up CANCEL_ALLs), then until no order action is outstanding
            deadline = loop.time() + (self.kill_wait if callable(done) else self.shutdown_wait)
            while loop.time() < deadline and not self.hard:
                complete = bool(self.call("shutdown_complete", done)) if callable(done) else (
                    reason != "signal" or int(getattr(self.core, "kill_acks", 0) or 0) > self.kill_acks0)
                if complete and not self._order_actions_pending():
                    break
                await asyncio.sleep(0.02)
            if not self.hard and callable(done):
                if not self.call("shutdown_complete", done):
                    self.log.event("shutdown_incomplete", reason=reason, waited_s=self.kill_wait,
                                   outstanding=sum(1 for k in self.outstanding.values() if k in ORDER_ACTIONS))
                self.call("shutdown", self.core.shutdown, self.mono())   # report, and a last cancel if needed
                self.flush()
                end = loop.time() + self.shutdown_wait
                while loop.time() < end and self._order_actions_pending() and not self.hard:
                    await asyncio.sleep(0.02)
            if self.run_dir:
                self.call("persist", self.core.run_job, "persist", self.mono())
        for src in self.sources:
            self.call(f"source.stop:{getattr(src, 'name', '?')}", src.stop)
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.bridge is not None:
            self.bridge.unbind()
        await self._bounded(lambda: self.executor.close(self.shutdown_wait), self.shutdown_wait + 1.0)
        eng = getattr(self.core, "engine", None) or self.engine
        await self._bounded(eng.close, 3.0)
        close_core = getattr(self.core, "close", None)
        if callable(close_core):
            await self._bounded(close_core, 2.0)
        if self.recorder is not None:
            self.recorder.close()
        if self.fv_log is not None:
            self.fv_log.close()
        self.log.event("shell_exit", reason=reason, hard=self.hard)
        return 130 if reason in ("signal", "signal2") else 0

    async def _bounded(self, fn: Callable[[], Any], timeout: float) -> None:
        """Run a blocking close on a daemon thread and wait for it at most ``timeout`` seconds."""
        done = threading.Event()

        def target() -> None:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.call("close", self._raise, e)
            finally:
                done.set()

        th = threading.Thread(target=target, name="closer", daemon=True)
        th.start()
        end = time.monotonic() + timeout
        while not done.is_set() and time.monotonic() < end:
            await asyncio.sleep(0.02)


async def run_shell(core: "BotCore", sources: Sequence[MarketDataSource], executor: "RestExecutor",
                    api: ExchangeAPI, engine: FairValueProvider, timesrc: TimeSource, log: EventLog,
                    run_dir: str | None, console: bool, stdin: TextIO | None = None, *,
                    result_bridge: ResultBridge | None = None, secrets: Sequence[str] = (),
                    status_stream: TextIO | None = None, install_signals: bool = True) -> int:
    """Run the bot until ``q``, a signal or (replay, sim) the end of every source. Returns the exit code.

    ``result_bridge`` is the callback the executor was built with (:class:`ResultBridge`); ``secrets`` are
    redacted from the raw-feed recorder and the exception hooks (the ``JsonlLog`` has its own list);
    ``status_stream`` receives the status line and console output (default stderr).
    """
    bridge = result_bridge
    if bridge is None and isinstance(getattr(executor, "on_result", None), ResultBridge):
        bridge = executor.on_result
    shell = _Shell(core, sources, executor, api, engine, timesrc, log, run_dir, console, stdin, bridge, secrets,
                   status_stream, install_signals)
    return await shell.run()
