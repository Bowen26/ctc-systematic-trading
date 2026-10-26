"""Virtual-time discrete-event driver of ``BotCore`` against ``SimExchange`` (DESIGN.md section 4.E).

``Harness(cfg, market, exchange, engine, core_factory, log, t_end=None, commands=())`` builds one core with
``core_factory(timesrc=<virtual clock>)`` and runs it deterministically: no threads, no wall clock (except the
reported ``timing.wall_s``), no randomness.

**At each event time t**, in this order:
  1. ``exchange.advance(t)``;
  2. due API results (``core.on_api_result``), then a due engine notification (``core.on_fair_values``);
  3. due jobs in ``core.jobs()`` order (reconcile, clock_poll, tick, ...), each firing at the multiples of its
     period from t = 0;
  4. every market frame stamped t, in one ``core.on_frames`` call;
  5. due scripted commands (``core.on_command``), then due probes.

Jobs run before the frames of the same instant (integration change to DESIGN.md §4.E): a clip sent at 80.0
fills on row 41 at 82.0 (step 1), the 82.0 reconcile marks it FILLED (step 3), and only then can the 82.0 news
decision cycle (step 4) look at it. With frames first, that cycle saw the clip at its 2-s ttl and sent a
remainder cancel for an order that had already filled (a 404 and a wasted action).

**After every core call** the harness drains ``take_requests()`` through ``exchange.execute`` at arrival t and
delivers each result at ``t + sim.api_latency_s``; with 0 latency the result is delivered at once, before the next
step, and its follow-up requests are drained the same way. ``take_control()`` is drained and ignored: there is no
executor in virtual time, and a FROZEN core emits no PLACE anyway. The engine listener only sets a flag; the
harness calls ``core.on_fair_values(t + sim.engine_latency_s)`` after the current call returns (at once with 0
latency), so there is no re-entrancy.

**End of run:** at ``t_end`` (default: the market's last frame or row plus 2 s) the harness calls
``exchange.close_all(t_end)``, runs jobs, results and engine notifications for 2 more virtual seconds so two
reconcile cycles see the result, then calls ``core.shutdown`` and drains its requests.

Every core call is wrapped: an exception is logged as ``exception`` (with the traceback) and the run continues, as
the shell does. ``run()`` returns ``{"exchange": exchange.results(), "core": core.report(), "status": ...,
"orders": order_id -> {strategy, style, key, ...}, "timing": {...}, "log_counts": ..., "alerts": ..., "probes":
..., "exceptions": ...}``. The ``orders`` map joins exchange fills to the bot's ``OrderRecord.strategy``: it is
read from ``core.store`` when each PLACE is executed, while the record is certainly live.

Extensions beyond the design's signature (keyword-only, optional): ``timesrc`` (share one virtual clock with an
inline ``EngineAdapter`` built before the harness), ``probes`` (``(t, fn(core, t))`` callbacks whose return values
are collected), ``max_pump`` (a guard against a request/result loop that never settles at one instant) and
``t_start`` (the virtual start time of a windowed recording run; jobs still fire at multiples of their period).
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import time
import traceback
from collections import Counter, deque
from typing import TYPE_CHECKING, Any, Callable, Iterator, Sequence

from ctcbot.types import ApiKind, ApiResult, Command, EventLog, FairValueProvider, ListLog, SourceFrame

from sim.research_market import MarketFrame

if TYPE_CHECKING:  # pragma: no cover
    from ctcbot.app.core import BotCore
    from ctcbot.config import Config

    from sim.exchange import SimExchange

__all__ = ["Harness", "VirtualClock", "EngineTap", "event_log_hash", "to_source_frame"]

_ROUND = 9   # event times are rounded to 1e-9 s so float noise never splits one instant in two


def _r(t: float) -> float:
    return round(float(t), _ROUND)


class VirtualClock:
    """``TimeSource`` in virtual time: ``mono() == t`` and ``wall() == wall0 + t`` (like A's ``VirtualTime``)."""

    def __init__(self, start: float = 0.0, wall0: float = 1.8e9) -> None:
        self.t = float(start)
        self.wall0 = float(wall0)

    def mono(self) -> float:
        return self.t

    def wall(self) -> float:
        return self.wall0 + self.t

    def set(self, t: float) -> None:
        self.t = float(t)

    def advance(self, dt: float) -> None:
        self.t += float(dt)


class EngineTap:
    """``FairValueProvider`` proxy that records what the core submits and when snapshots are published.

    ``submitted_snapshots`` lists the ``last_seq`` of every snapshot submitted to the engine (DESIGN.md section 9.2:
    exactly the relevant ones); ``publishes`` lists ``(t, version, seq_hi, round)`` of every published fv, with t
    read from the virtual clock.
    """

    def __init__(self, inner: FairValueProvider, clock: Callable[[], float]) -> None:
        self.inner = inner
        self.clock = clock
        self.submitted_snapshots: list[int | None] = []
        self.submitted_news = 0
        self.publishes: list[tuple[float, int, int | None, int | None]] = []
        self._cb: Callable[[], None] | None = None
        inner.set_listener(self._on_publish)

    def _on_publish(self) -> None:
        fv = self.inner.latest()
        if fv is not None:
            self.publishes.append((self.clock(), fv.version, fv.seq_hi, fv.round))
        if self._cb is not None:
            self._cb()

    def set_listener(self, cb: Callable[[], None]) -> None:
        self._cb = cb

    def submit(self, msgs: Sequence[dict], recv_mono: float) -> None:
        for m in msgs:
            if isinstance(m, dict) and m.get("type") == "snapshot":
                self.submitted_snapshots.append(m.get("last_seq"))
            else:
                self.submitted_news += 1
        self.inner.submit(msgs, recv_mono)

    def latest(self) -> Any:
        return self.inner.latest()

    def health(self, now: float) -> Any:
        return self.inner.health(now)

    def set_adjustment(self, symbol: str, mult: float = 1.0, add: float = 0.0) -> None:
        self.inner.set_adjustment(symbol, mult=mult, add=add)

    def override(self, dotted: str, value: Any) -> None:
        self.inner.override(dotted, value)

    def reset_season(self, season_epoch: int, persist_path: str | None) -> None:
        self.inner.reset_season(season_epoch, persist_path)

    def close(self) -> None:
        self.inner.close()


def to_source_frame(mf: MarketFrame, wall0: float) -> SourceFrame:
    """``SourceFrame`` of a market frame: ``recv_mono`` is the virtual time, ``recv_wall`` the recorded receipt
    time when the market has one, else ``wall0 + t``."""
    wall = mf.recv_wall if mf.recv_wall is not None else wall0 + mf.t
    return SourceFrame(frame=mf.frame, recv_wall=wall, recv_mono=mf.t, conn=mf.conn, control=mf.control)


def _canon(x: Any) -> Any:
    """JSON-safe canonical form for hashing (floats rounded to 9 significant decimals)."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return str(x)
        return float(f"{x:.9g}")
    if isinstance(x, dict):
        return {str(k): _canon(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        seq = [_canon(v) for v in x]
        return sorted(seq, key=repr) if isinstance(x, (set, frozenset)) else seq
    if isinstance(x, (str, int, bool)) or x is None:
        return x
    if hasattr(x, "__dataclass_fields__"):
        return {k: _canon(getattr(x, k)) for k in x.__dataclass_fields__}
    return str(x)


def event_log_hash(log: ListLog, ignore: Sequence[str] = ("compute_ms", "wall_s", "elapsed_ms", "t_wall",
                                                          "latency_ms", "dt_ms")) -> str:
    """sha256 of the event log with wall-clock fields removed, for the determinism check (section 9.1 E)."""
    drop = set(ignore)

    def strip(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in drop}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x

    data = [[k, strip(_canon(f))] for k, f in log.events]
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Harness:
    """Deterministic virtual-time driver (see the module docstring)."""

    def __init__(self, cfg: "Config", market: Any, exchange: "SimExchange", engine: FairValueProvider,
                 core_factory: Callable[..., "BotCore"], log: EventLog, t_end: float | None = None,
                 commands: Sequence[tuple[float, Command]] = (), *, timesrc: VirtualClock | None = None,
                 probes: Sequence[tuple[float, Callable[[Any, float], Any]]] = (), max_pump: int = 100000,
                 t_start: float = 0.0) -> None:
        self.cfg = cfg
        self.market = market
        self.exchange = exchange
        self.engine = engine
        self.core_factory = core_factory
        self.log = log
        self.t_end = t_end
        self.commands = sorted(((_r(t), k, c) for k, (t, c) in enumerate(commands)), key=lambda x: (x[0], x[1]))
        self.probes = sorted(((_r(t), k, f) for k, (t, f) in enumerate(probes)), key=lambda x: (x[0], x[1]))
        self.time = timesrc if timesrc is not None else VirtualClock()
        self.max_pump = int(max_pump)
        self.t_start = _r(t_start)
        self.api_latency = float(self._cfg("sim.api_latency_s", 0.0))
        self.engine_latency = float(self._cfg("sim.engine_latency_s", 0.0))
        self.core: Any = None
        self.now = 0.0
        self._results: list[tuple[float, int, ApiResult]] = []   # heap of delayed results
        self._ready: deque[ApiResult] = deque()                  # zero-latency results awaiting delivery
        self._engine_flag = False
        self._engine_due: float | None = None
        self._seq = 0
        self.order_meta: dict[str, dict] = {}
        self.exceptions: list[dict] = []
        self.probe_results: list[dict] = []
        self.calls = 0
        self.requests = 0
        self.instants = 0
        self.pump_overflows = 0

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            v = self.cfg.get(key, default)
        except Exception:  # noqa: BLE001
            return default
        return default if v is None else v

    # ------------------------------------------------------------------------------------------- plumbing
    def _on_engine(self) -> None:
        """Engine listener: only sets a flag (or schedules the notification with a latency)."""
        if self.engine_latency <= 0:
            self._engine_flag = True
        elif self._engine_due is None:
            self._engine_due = _r(self.now + self.engine_latency)

    def _invoke(self, name: str, fn: Callable[..., Any], *args: Any) -> Any:
        self.calls += 1
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001 - the harness, like the shell, never dies on a core exception
            tb = traceback.format_exc()
            self.exceptions.append({"t": self.now, "call": name, "error": f"{type(e).__name__}: {e}"})
            self.log.event("exception", where=f"harness:{name}", error=f"{type(e).__name__}: {e}", traceback=tb)
            return None

    def _call(self, name: str, fn: Callable[..., Any], *args: Any) -> Any:
        out = self._invoke(name, fn, *args)
        self._pump()
        return out

    def _record_meta(self, req: Any, res: ApiResult) -> None:
        if req.kind != ApiKind.PLACE or not res.ok:
            return
        oid = getattr(res.parsed, "order_id", None)
        if oid is None and isinstance(res.data, dict):
            oid = res.data.get("order_id")
        if oid is None:
            return
        meta = {"local_id": req.local_id, "symbol": req.symbol, "side": str(req.side) if req.side else None,
                "priority": int(req.priority), "t": self.now, "strategy": None, "style": None, "key": None}
        store = getattr(self.core, "store", None)
        rec = None
        if store is not None and req.local_id is not None:
            for getter in ("by_local", "get"):
                fn = getattr(store, getter, None)
                if fn is None:
                    continue
                try:
                    rec = fn(req.local_id)
                except Exception:  # noqa: BLE001 - a store without this lookup just leaves the meta empty
                    rec = None
                if rec is not None:
                    break
        if rec is not None:
            meta["strategy"] = getattr(rec, "strategy", None)
            meta["style"] = getattr(rec, "style", None)
            meta["key"] = getattr(rec, "key", None)
        self.order_meta[str(oid)] = meta

    def _pump(self) -> None:
        """Drain requests and zero-latency results/notifications until the instant is quiet."""
        core = self.core
        n = 0
        while True:
            n += 1
            if n > self.max_pump:
                self.pump_overflows += 1
                self.log.alert("harness_pump_overflow", "requests kept coming at one instant", t=self.now)
                break
            self._invoke("take_control", core.take_control)
            reqs = self._invoke("take_requests", core.take_requests) or []
            for req in reqs:
                self.requests += 1
                self.exchange.t = self.now
                try:
                    res = self.exchange.execute(req)
                except Exception as e:  # noqa: BLE001 - the simulator must report, not crash
                    self.exceptions.append({"t": self.now, "call": "exchange.execute",
                                            "error": f"{type(e).__name__}: {e}"})
                    self.log.event("exception", where="harness:exchange.execute",
                                   error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc())
                    continue
                self._record_meta(req, res)
                if self.api_latency <= 0:
                    self._ready.append(res)
                else:
                    self._seq += 1
                    heapq.heappush(self._results, (_r(self.now + self.api_latency), self._seq, res))
            if self._ready:
                res = self._ready.popleft()
                self._invoke("on_api_result", core.on_api_result, res, self.now)
                continue
            if self._engine_flag:
                self._engine_flag = False
                self._invoke("on_fair_values", core.on_fair_values, self.now)
                continue
            if not reqs:
                break

    # ------------------------------------------------------------------------------------------- run
    def _make_core(self) -> Any:
        try:
            return self.core_factory(timesrc=self.time)
        except TypeError:
            return self.core_factory(self.time)

    def run(self) -> dict:
        """Run the whole market, then the end-of-run sequence; return the combined results."""
        wall0 = time.perf_counter()
        t0 = self.t_start
        self.time.set(t0)
        self.core = core = self._make_core()
        reg = getattr(core, "set_engine_listener", None)    # the core keeps it for `eng restart`
        if callable(reg):
            self._invoke("set_engine_listener", reg, self._on_engine)
        else:
            self.engine.set_listener(self._on_engine)
        jobs = [(str(name), float(period)) for name, period in (core.jobs() or [])]
        jobs = [(n, p) for n, p in jobs if p > 0]
        job_k = {n: int(math.ceil(t0 / p - 1e-9)) if t0 > 0 else 0 for n, p in jobs}
        t_end = _r(self.t_end if self.t_end is not None else self.market.default_t_end())
        frames: Iterator[MarketFrame] = iter(self.market.frames())
        nxt: MarketFrame | None = next(frames, None)
        cmd_i = 0
        probe_i = 0

        self.now = t0
        self.exchange.t = t0
        self.exchange.advance(t0)
        self._call("start", core.start, t0)

        def next_job_time() -> float:
            return min((_r(job_k[n] * p) for n, p in jobs), default=math.inf)

        def step(t: float, with_frames: bool) -> None:
            nonlocal nxt, cmd_i, probe_i
            self.now = t
            self.time.set(t)
            self.instants += 1
            self.exchange.t = t
            self.exchange.advance(t)
            while self._results and self._results[0][0] <= t:
                _, _, res = heapq.heappop(self._results)
                self._call("on_api_result", core.on_api_result, res, t)
            if self._engine_due is not None and self._engine_due <= t:
                self._engine_due = None
                self._call("on_fair_values", core.on_fair_values, t)
            for name, period in jobs:
                if _r(job_k[name] * period) <= t:
                    while _r(job_k[name] * period) <= t:
                        job_k[name] += 1
                    self._call(f"job:{name}", core.run_job, name, t)
            if with_frames:                # after the jobs: see the module docstring
                batch: list[SourceFrame] = []
                while nxt is not None and _r(nxt.t) <= t:
                    batch.append(to_source_frame(nxt, self.time.wall0))
                    nxt = next(frames, None)
                if batch:
                    self._call("on_frames", core.on_frames, batch, t)
            while cmd_i < len(self.commands) and self.commands[cmd_i][0] <= t:
                self._call("on_command", core.on_command, self.commands[cmd_i][2], t)
                cmd_i += 1
            while probe_i < len(self.probes) and self.probes[probe_i][0] <= t:
                pt, _, fn = self.probes[probe_i]
                val = self._invoke("probe", fn, core, t)
                self.probe_results.append({"t": pt, "value": val})
                probe_i += 1

        def next_time(with_frames: bool) -> float:
            cands = [next_job_time()]
            if with_frames and nxt is not None:
                cands.append(_r(nxt.t))
            if self._results:
                cands.append(self._results[0][0])
            if self._engine_due is not None:
                cands.append(self._engine_due)
            if cmd_i < len(self.commands):
                cands.append(self.commands[cmd_i][0])
            if probe_i < len(self.probes):
                cands.append(self.probes[probe_i][0])
            return min(cands)

        # main phase: everything up to t_end
        while True:
            t = next_time(True)
            if t > t_end or t == math.inf:
                break
            step(max(t, self.now), True)
        # end of run: close every order, two more reconcile cycles, shutdown
        self.now = t_end
        self.time.set(t_end)
        self.exchange.t = t_end
        self.exchange.close_all(t_end)
        tail_end = _r(t_end + 2.0)
        while True:
            t = next_time(False)
            if t > tail_end or t == math.inf:
                break
            step(max(t, self.now), False)
        self.now = tail_end
        self.time.set(tail_end)
        self.exchange.t = tail_end
        self.exchange.advance(tail_end)
        self._call("shutdown", core.shutdown, tail_end)
        out = {
            "exchange": self.exchange.results(),
            "core": self._invoke("report", core.report),
            "status": self._invoke("status", core.status, tail_end),
            "orders": dict(self.order_meta),
            "exceptions": list(self.exceptions),
            "probes": list(self.probe_results),
            "timing": {"t_end": t_end, "t_final": tail_end, "wall_s": time.perf_counter() - wall0,
                       "instants": self.instants, "core_calls": self.calls, "requests": self.requests,
                       "pump_overflows": self.pump_overflows},
        }
        if isinstance(self.log, ListLog):
            out["log_counts"] = dict(Counter(k for k, _ in self.log.events))
            out["alerts"] = dict(Counter(str(f.get("alert")) for k, f in self.log.events if k == "alert"))
            out["log_hash"] = event_log_hash(self.log)
        return out
