"""Fair-value engine adapter (DESIGN.md section 4.C, ``models/engine_adapter.py``).

``EngineAdapter`` implements ``ctcbot.types.FairValueProvider`` over the team engine ``engine/fairvalue.py``
(imported by file path, never edited). It owns one ``SeasonState``:

* **thread mode** (live): a daemon worker thread owns the state. ``submit`` only enqueues; the worker blocks up to
  ``engine.periodic_compute_s`` for work, drains everything queued (so a burst is coalesced), ingests the messages
  in order, merges the adjustment files if they changed, computes ``fair_values`` and publishes a
  ``FairValueSnapshot`` when the engine version, the adjustments or the overrides changed. The listener is then
  called from the worker thread (the shell hops back to the loop with ``call_soon_threadsafe``).
* **inline mode** (sim, tests): the same steps run synchronously inside each call.

Adjustment files (with ``adjust_dir``) keep one writer per file: ``adjust.json`` is the analyst's (hand-edited),
``adjust_console.json`` is written only by ``set_adjustment``/``override`` (the console's ``f``, ``m``, ``a``) and
``adjust_engine.json`` only by the worker, which merges the two (the console entry wins per symbol and per override
key) and hands the result to the engine as its ``adjust_file``. All three are rotated at a season reset.

``load_engine_module`` imports an engine file without writing bytecode; ``snapshot_fix_present`` replays the
committed probe (``ctcbot/models/data/snapshot_probe.jsonl``) to check that the engine carries the testnet snapshot
fix (TESTNET_FINDINGS section 1.3). Without it the adapter forces ``forward_snapshots = "never"`` and raises the
alert ``engine_snapshot_bug``.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
import queue
import sys
import threading
import time
import traceback
from collections import deque
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from ctcbot.types import SYMBOLS, EngineHealth, FairValueSnapshot

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from ctcbot.config import Config
    from ctcbot.types import EventLog, TimeSource

ADJUST_ENGINE = "adjust_engine.json"
ADJUST_CONSOLE = "adjust_console.json"
POSTERIOR_TOL = 1e-9
MEAN_TOL = 1e-6

_MODULES: dict[str, ModuleType] = {}
_MODULES_LOCK = threading.Lock()
_PROBE_CACHE: dict[tuple, bool] = {}
_LIBRARY_CACHE: dict[tuple, dict] = {}


# ============================================================================== module loading
def load_engine_module(engine_dir: str, filename: str = "fairvalue.py") -> ModuleType:
    """Import ``engine_dir/filename`` as module ``se_<stem>`` (cached per path), with bytecode writing off.

    The engine inserts its own folder into ``sys.path`` and resolves its data files relative to itself.
    ``sys.dont_write_bytecode`` is forced on first, so no ``__pycache__`` appears in the engine folder.
    """
    sys.dont_write_bytecode = True
    path = os.path.abspath(os.path.join(engine_dir, filename))
    with _MODULES_LOCK:
        mod = _MODULES.get(path)
        if mod is not None:
            return mod
        if not os.path.isfile(path):
            raise ImportError(f"engine module not found: {path}")
        stem = os.path.splitext(os.path.basename(path))[0]
        spec = importlib.util.spec_from_file_location("se_" + stem, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load engine module {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for attr in ("SeasonState", "fair_values"):
            if not callable(getattr(mod, attr, None)):
                raise ImportError(f"engine module {path} has no callable {attr}")
        _MODULES[path] = mod
        return mod


def _read_jsonl(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = json.loads(line)
            if isinstance(m, dict):
                out.append(m)
    return out


def _probe_arm(mod: ModuleType, params_path: str, case: dict, msgs: Sequence[dict],
               shared: dict[str, Any]) -> tuple[dict | None, dict[str, float]]:
    """Replay ``msgs`` through a fresh SeasonState; (pattern posterior p, means of the 12 symbols)."""
    kw: dict[str, Any] = {"params": params_path, "case": copy.deepcopy(case)}
    st = None
    if shared:
        try:
            st = mod.SeasonState(**kw, **shared)
        except TypeError:
            st = None
    if st is None:
        st = mod.SeasonState(**kw)
    for m in msgs:
        st.on_message(copy.deepcopy(m))
    fv = mod.fair_values(st)
    post = st.pattern_posterior()
    p = dict(post["p"]) if isinstance(post, dict) and isinstance(post.get("p"), dict) else None
    return p, {s: float(fv[s][0]) for s in SYMBOLS}


def _same(a: tuple[dict | None, dict[str, float]], b: tuple[dict | None, dict[str, float]]) -> bool:
    pa, ma = a
    pb, mb = b
    if (pa is None) != (pb is None):
        return False
    if pa is not None and pb is not None:
        if set(pa) != set(pb) or any(abs(float(pa[c]) - float(pb[c])) > POSTERIOR_TOL for c in pa):
            return False
    return all(abs(ma[s] - mb[s]) <= MEAN_TOL for s in SYMBOLS)


def snapshot_fix_present(mod: ModuleType, probe_path: str, case: dict, params_path: str) -> bool:
    """True iff the engine module carries the testnet snapshot fix (both parts).

    The probe file holds research season 401 round 1 up to age 100 followed by one testnet-schema snapshot whose
    advisory has no storm, seq, round or type. Four fresh states are compared on the pattern posterior (1e-9)
    and all 12 means (1e-6): the clean stream (reference); the clean stream plus the snapshot, which must be a
    no-op; the stream without the snapshot's advisory plus the snapshot (recovery, fix part 1); and that plus the
    late live advisory (double count, fix part 2). The unpatched engine resets its posterior in the recovery.
    Raises on a missing or malformed probe; callers treat a raise as "fix absent". Results are cached per
    (engine file, probe file and mtime, params, case).
    """
    probe = os.path.abspath(probe_path)
    st_ = os.stat(probe)
    case_h = hashlib.sha256(json.dumps(case, sort_keys=True, default=str).encode()).hexdigest()[:16]
    key = (getattr(mod, "__file__", None) or id(mod), probe, st_.st_mtime_ns, st_.st_size,
           os.path.abspath(params_path), case_h)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    msgs = _read_jsonl(probe)
    if len(msgs) < 2 or msgs[-1].get("type") != "snapshot":
        raise ValueError(f"probe {probe}: the last line must be a snapshot")
    snap, pre = msgs[-1], msgs[:-1]
    adv = snap.get("advisory")
    if not isinstance(adv, dict) or "number" not in adv:
        raise ValueError(f"probe {probe}: the snapshot has no official advisory")
    num, rnd = int(adv["number"]), snap.get("round")
    idx = [i for i, m in enumerate(pre) if m.get("type") == "advisory" and m.get("source") == "official"
           and m.get("round") == rnd and int(m.get("number", -1)) == num]
    if not idx:
        raise ValueError(f"probe {probe}: advisory #{num} of round {rnd} is not in the stream")
    lost = pre[idx[-1]]
    rest = pre[:idx[-1]] + pre[idx[-1] + 1:]
    shared: dict[str, Any] = {}
    try:  # load the big library and the classifier sources once for the four states
        with open(params_path) as f:
            P = json.load(f)
        base = os.path.dirname(os.path.abspath(params_path))
        paths = P.get("paths") or {}
        with open(os.path.normpath(os.path.join(base, paths["library"]))) as f:
            shared["library"] = json.load(f)
        with open(os.path.normpath(os.path.join(base, paths["sources"]))) as f:
            shared["sources"] = json.load(f)
    except (OSError, KeyError, ValueError, TypeError):
        shared = {}
    ref = _probe_arm(mod, params_path, case, pre, shared)
    arms = {"across": pre + [snap], "recovery": rest + [snap], "double_count": rest + [snap, lost]}
    result = all(_same(ref, _probe_arm(mod, params_path, case, seq, shared)) for seq in arms.values())
    _PROBE_CACHE[key] = result
    return result


# ============================================================================== adjustment files
def _atomic_write_json(path: str, obj: Any) -> None:
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _read_json_dict(path: str) -> dict:
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _finite(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def merge_adjustments(analyst: Mapping[str, Any], console: Mapping[str, Any]) -> tuple[dict, list[str]]:
    """Merge the analyst's and the console's adjustment dicts; the console entry wins per symbol and override.

    Returns ``(merged, problems)``; entries that are not ``{"mult": finite, "add": finite}`` for one of the 12
    symbols are dropped and reported.
    """
    out: dict[str, Any] = {}
    problems: list[str] = []
    overrides: dict[str, Any] = {}
    for name, src in (("adjust.json", analyst), ("adjust_console.json", console)):
        for k, v in src.items():
            if k == "_overrides":
                if isinstance(v, dict):
                    for ok_, ov in v.items():
                        if isinstance(ok_, str) and ok_:
                            overrides[ok_] = ov
                else:
                    problems.append(f"{name}: _overrides is not an object")
                continue
            if k not in SYMBOLS:
                problems.append(f"{name}: unknown key {k!r}")
                continue
            if not isinstance(v, dict):
                problems.append(f"{name}: {k} is not an object")
                continue
            mult, add = _finite(v.get("mult", 1.0)), _finite(v.get("add", 0.0))
            if mult is None or add is None:
                problems.append(f"{name}: {k} has a non-numeric mult or add")
                continue
            out[k] = {"mult": mult, "add": add}
    if overrides:
        out["_overrides"] = overrides
    return out, problems


def _library_without(params_path: str, exclude: Sequence[int]) -> dict:
    """``{"storms": [...]}`` from the engine's library file without the excluded seasons (cached per process)."""
    with open(params_path) as f:
        P = json.load(f)
    lib_path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(params_path)), P["paths"]["library"]))
    st_ = os.stat(lib_path)
    key = (lib_path, st_.st_mtime_ns, tuple(sorted(int(s) for s in exclude)))
    lib = _LIBRARY_CACHE.get(key)
    if lib is None:
        with open(lib_path) as f:
            raw = json.load(f)
        excl = set(key[2])
        lib = {"storms": [s for s in raw.get("storms", []) if s.get("season") not in excl]}
        _LIBRARY_CACHE.clear()
        _LIBRARY_CACHE[key] = lib
    return lib


# ============================================================================== adapter
class _Batch:
    __slots__ = ("bid", "recv_mono", "n")

    def __init__(self, bid: int, recv_mono: float, n: int) -> None:
        self.bid, self.recv_mono, self.n = bid, recv_mono, n


def _unwrap(m: Any) -> dict | None:
    if not isinstance(m, dict):
        return None
    if m.get("type") == "news" and isinstance(m.get("message"), dict):
        return m["message"]
    return m


def _as_round(v: Any) -> int | None:
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None


class EngineAdapter:
    """``FairValueProvider`` over the team engine (see the module docstring).

    Parameters
    ----------
    cfg: configuration (``engine.*``, ``app.adjust_file``).
    case: the served case.json dict, passed to the engine.
    timesrc: the bot's ``TimeSource`` (``computed_mono``, ages).
    log: the ``EventLog``.
    mode: ``"thread"`` (live) or ``"inline"`` (sim, tests).
    persist_path: the engine's JSONL message log (``engine_state_{run_id}_e{epoch}.jsonl``).
    adjust_dir: folder of the three adjustment files (the run directory); None applies console adjustments
        straight to the engine.
    run_id: used in the names of rotated adjustment files.
    restore: rebuild the state from ``persist_path`` with ``SeasonState.restore``.
    state_factory: replaces ``SeasonState`` (tests); it is called with the constructor's keyword arguments and,
        for ``restore``, its ``restore(persist_path, **kw)`` is used. Fair values then come from
        ``state.compute()`` and the snapshot-fix probe is not run.
    library_seasons: analog/future-storm library seasons (default ``engine.library_seasons``).
    snapshot_fix: the probe result when the caller already ran it; None runs it here (cached per process) when
        ``engine.require_snapshot_fix`` is true and the real engine is used.
    """

    def __init__(self, cfg: "Config", case: dict, timesrc: "TimeSource", log: "EventLog", mode: str = "thread",
                 persist_path: str | None = None, adjust_dir: str | None = None, run_id: str = "run",
                 restore: bool = False, state_factory: Callable[..., Any] | None = None,
                 library_seasons: tuple[int, int] | None = None, *, snapshot_fix: bool | None = None) -> None:
        if mode not in ("thread", "inline"):
            raise ValueError(f"EngineAdapter mode must be 'thread' or 'inline', not {mode!r}")
        self.cfg = cfg
        self.case = case
        self.timesrc = timesrc
        self.log = log
        self.mode = mode
        self.run_id = run_id
        self.engine_dir = cfg.path(cfg.get("engine.dir", "../engine")) or ""
        params = cfg.get("engine.params", "engine_params_fast.json")
        self.params_path = params if os.path.isabs(params) else os.path.join(self.engine_dir, params)
        self.periodic_s = max(0.01, float(cfg.get("engine.periodic_compute_s", 1.0)))
        self.down_after = max(1, int(cfg.get("engine.down_after_errors", 3)))
        self.compute_warn_ms = float(cfg.get("engine.compute_warn_ms", 150))
        fwd = cfg.get("engine.forward_snapshots", "relevant")
        self._forward = fwd if fwd in ("relevant", "all", "never") else "relevant"
        self.persist_path = persist_path

        # ---------------------------------------------------------------- adjustment files
        self.adjust_dir = adjust_dir
        self._analyst_path = self._console_path = self._engine_adj_path = None
        self._console: dict[str, Any] = {}
        self._console_gen = 0
        self._merged_sig: tuple | None = None
        if adjust_dir:
            os.makedirs(adjust_dir, exist_ok=True)
            self._analyst_path = os.path.join(adjust_dir, cfg.get("app.adjust_file", "adjust.json"))
            self._console_path = os.path.join(adjust_dir, ADJUST_CONSOLE)
            self._engine_adj_path = os.path.join(adjust_dir, ADJUST_ENGINE)
            if not os.path.exists(self._analyst_path):
                _atomic_write_json(self._analyst_path, {})
            self._console = _read_json_dict(self._console_path)
            if not os.path.exists(self._console_path):
                _atomic_write_json(self._console_path, {})

        # ---------------------------------------------------------------- engine state
        if state_factory is None:
            self.mod: ModuleType | None = load_engine_module(self.engine_dir, "fairvalue.py")
            factory: Any = self.mod.SeasonState
            fv_fn = self.mod.fair_values
            self._fv_fn: Callable[[Any], Mapping[str, tuple[float, float]]] = lambda st: fv_fn(st)
        else:
            self.mod = None
            factory = state_factory
            self._fv_fn = lambda st: st.compute()
        ls = library_seasons or tuple(cfg.get("engine.library_seasons", [1, 600]))
        kw: dict[str, Any] = {"params": self.params_path, "case": case, "adjust_file": self._engine_adj_path,
                              "library_seasons": tuple(int(x) for x in ls)}
        excl = list(cfg.get("engine.library_exclude_seasons", []) or [])
        if excl and state_factory is None:
            kw["library"] = _library_without(self.params_path, excl)
        if restore:
            if not persist_path:
                raise ValueError("restore=True needs persist_path")
            self.st = factory.restore(persist_path, **kw)
        else:
            self.st = factory(persist_path=persist_path, **kw)

        # ---------------------------------------------------------------- snapshot fix
        self.snapshot_fix: bool | None = snapshot_fix
        if snapshot_fix is None and self.mod is not None and bool(cfg.get("engine.require_snapshot_fix", True)):
            probe = cfg.path(cfg.get("engine.probe_file", "ctcbot/models/data/snapshot_probe.jsonl"))
            try:
                self.snapshot_fix = bool(snapshot_fix_present(self.mod, probe or "", case, self.params_path))
            except Exception as e:  # noqa: BLE001 - any probe failure means "fix absent"
                self.snapshot_fix = False
                self._event("engine_probe_error", error=f"{type(e).__name__}: {e}")
        if self.snapshot_fix is False:
            self._forward = "never"
            self._alert("engine_snapshot_bug", "engine snapshot fix not detected: snapshots are no longer forwarded "
                        "to the engine (no snapshot recovery); --live is refused")

        # ---------------------------------------------------------------- bookkeeping
        self._lock = threading.Lock()
        self._listener: Callable[[], None] | None = None
        self._latest: FairValueSnapshot | None = None
        self._last_key: tuple | None = None
        try:
            base = int(float(timesrc.wall()) * 1000.0)
        except Exception:  # noqa: BLE001
            base = 0
        self._version = max(0, base)          # monotonic across adapters built later in this run (wall-ms base)
        self._epoch = 0
        self._seq_hi: int | None = None
        self._round: int | None = None
        self._msg_time: str | None = None
        self._included = -math.inf
        self._pending: deque[_Batch] = deque()
        self._next_bid = 0
        self._processed_bid = -1
        self.errors = 0
        self.consecutive_errors = 0
        self.last_compute_ms: float | None = None
        self._down_alerted = False
        self._closed = False
        self._overrides_direct: dict[str, Any] = {}
        if restore:
            self._init_from_log()
        self._q: queue.Queue | None = None
        self._thread: threading.Thread | None = None
        if mode == "thread":
            self._q = queue.Queue()
            self._thread = threading.Thread(target=self._worker, name="engine-worker", daemon=True)
            self._thread.start()
        else:
            self._pass(dirty=True)
        self._event("engine_start", mode=mode, params=os.path.basename(self.params_path), library_seasons=list(ls),
                    library_exclude=excl, restore=restore, persist=persist_path, adjust_dir=adjust_dir,
                    forward_snapshots=self._forward, snapshot_fix=self.snapshot_fix)

    # ------------------------------------------------------------------ logging helpers
    def _event(self, kind: str, **fields: Any) -> None:
        try:
            self.log.event(kind, **fields)
        except Exception:  # noqa: BLE001 - logging never breaks the engine
            pass

    def _alert(self, kind: str, msg: str, **fields: Any) -> None:
        try:
            self.log.alert(kind, msg, **fields)
        except Exception:  # noqa: BLE001
            pass

    @property
    def forward_snapshots(self) -> str:
        """Effective snapshot forwarding: "relevant", "all" or "never" (forced when the fix is absent)."""
        return self._forward

    # ------------------------------------------------------------------ FairValueProvider
    def set_listener(self, cb: Callable[[], None]) -> None:
        """``cb()`` is called after every published snapshot (from the worker thread in thread mode)."""
        self._listener = cb

    def submit(self, msgs: Sequence[dict], recv_mono: float) -> None:
        """Queue raw news messages and snapshots (snapshots are dropped while forwarding is "never")."""
        if self._closed:
            return
        batch: list[dict] = []
        for m in msgs:
            mm = _unwrap(m)
            if mm is None:
                continue
            if mm.get("type") == "snapshot" and self._forward == "never":
                continue
            batch.append(dict(mm))
        if not batch:
            return
        with self._lock:
            bid = self._next_bid
            self._next_bid += 1
            self._pending.append(_Batch(bid, float(recv_mono), len(batch)))
        item = ("msgs", batch, float(recv_mono), bid)
        if self._q is not None:
            self._q.put(item)
        else:
            self._process([item])
            self._pass(dirty=True)

    def latest(self) -> FairValueSnapshot | None:
        """The newest published snapshot, or None before the first compute."""
        with self._lock:
            return self._latest

    def health(self, now: float) -> EngineHealth:
        """Liveness, queue depth and the age of the oldest message not yet reflected in a published value."""
        with self._lock:
            oldest = self._pending[0].recv_mono if self._pending else None
            n = sum(b.n for b in self._pending)
        thread_ok = self._thread is None or self._thread.is_alive()
        return EngineHealth(alive=bool(thread_ok and not self._closed and self.consecutive_errors < self.down_after),
                            pending=n, oldest_pending_age_s=None if oldest is None else max(0.0, now - oldest),
                            last_compute_ms=self.last_compute_ms, errors=self.errors,
                            consecutive_errors=self.consecutive_errors, snapshot_fix=self.snapshot_fix)

    def set_adjustment(self, symbol: str, mult: float = 1.0, add: float = 0.0) -> None:
        """Console adjustment ``v -> mult*v + add``; ``mult=1, add=0`` removes the console entry (``f SYM off``)."""
        if symbol not in SYMBOLS:
            raise ValueError(f"unknown symbol {symbol!r}")
        m, a = _finite(mult), _finite(add)
        if m is None or a is None:
            raise ValueError("mult and add must be finite numbers")
        if self.adjust_dir:
            with self._lock:
                if m == 1.0 and a == 0.0:
                    self._console.pop(symbol, None)
                else:
                    self._console[symbol] = {"mult": m, "add": a}
                snap = copy.deepcopy(self._console)
                self._console_gen += 1
            assert self._console_path is not None
            _atomic_write_json(self._console_path, snap)
            self._event("adjust_console", symbol=symbol, mult=m, add=a)
            self._kick(("wake",))
        else:
            self._kick(("adjust", symbol, m, a))

    def override(self, dotted: str, value: Any) -> None:
        """Console parameter override (``value=None`` removes it), e.g. ``settle.insurance.HAVEN.attach``."""
        if not isinstance(dotted, str) or not dotted:
            raise ValueError("override needs a dotted parameter path")
        if self.adjust_dir:
            with self._lock:
                ov = dict(self._console.get("_overrides") or {})
                if value is None:
                    ov.pop(dotted, None)
                else:
                    ov[dotted] = value
                if ov:
                    self._console["_overrides"] = ov
                else:
                    self._console.pop("_overrides", None)
                snap = copy.deepcopy(self._console)
                self._console_gen += 1
            assert self._console_path is not None
            _atomic_write_json(self._console_path, snap)
            self._event("override_console", key=dotted, value=value)
            self._kick(("wake",))
        else:
            self._kick(("override", dotted, value))

    def reset_season(self, season_epoch: int, persist_path: str | None) -> None:
        """New season: reset the state, clear adjustments and overrides, rotate the adjustment files."""
        self._kick(("reset", int(season_epoch), persist_path))

    def close(self) -> None:
        """Stop the worker (thread mode); idempotent."""
        if self._closed:
            return
        if self._q is not None and self._thread is not None:
            self._q.put(("close",))
            self._thread.join(2.0)
        self._closed = True
        self._event("engine_close", errors=self.errors)

    # ------------------------------------------------------------------ internals
    def _kick(self, item: tuple) -> None:
        if self._closed:
            return
        if self._q is not None:
            self._q.put(item)
        else:
            self._process([item])
            self._pass(dirty=True)

    def _init_from_log(self) -> None:
        """seq_hi, round and message time of a restored state, from its message log."""
        for m in getattr(self.st, "log", []) or []:
            self._note_msg(m)

    def _note_msg(self, m: dict) -> None:
        s = m.get("seq")
        if isinstance(s, int) and not isinstance(s, bool) and m.get("type") != "snapshot":
            self._seq_hi = s if self._seq_hi is None else max(self._seq_hi, s)
        r = _as_round(m.get("round"))
        if r is not None:
            self._round = r if self._round is None else max(self._round, r)
        if isinstance(m.get("time"), str):
            self._msg_time = m["time"]

    def _worker(self) -> None:
        assert self._q is not None
        dirty = True                         # publish the initial (prior) values at once
        wait = 0.0
        while True:
            try:
                first = self._q.get(timeout=wait) if wait > 0 else self._q.get_nowait()
                items = [first]
            except queue.Empty:
                items = []
            wait = self.periodic_s
            while True:
                try:
                    items.append(self._q.get_nowait())
                except queue.Empty:
                    break
            try:
                stop = self._process(items)
                if not stop:
                    self._pass(dirty=dirty or bool(items))
            except Exception as e:  # noqa: BLE001 - the worker must survive anything
                self.errors += 1
                self._event("exception", where="engine_worker", error=f"{type(e).__name__}: {e}",
                            traceback=traceback.format_exc(limit=8))
                stop = any(it[0] == "close" for it in items)
            dirty = False
            if stop:
                return

    def _process(self, items: Sequence[tuple]) -> bool:
        """Apply queued items in order; True when a close was requested."""
        stop = False
        for it in items:
            kind = it[0]
            if kind == "msgs":
                _, batch, recv_mono, bid = it
                for m in batch:
                    try:
                        self.st.on_message(m)
                    except Exception as e:  # noqa: BLE001 - skip one bad message, keep the season
                        self.errors += 1
                        self._event("engine_error", where="on_message", mtype=m.get("type"), seq=m.get("seq"),
                                    error=f"{type(e).__name__}: {e}")
                        continue
                    self._note_msg(m)
                self._included = max(self._included, recv_mono)
                self._processed_bid = max(self._processed_bid, bid)
            elif kind == "reset":
                self._do_reset(it[1], it[2])
            elif kind == "adjust":
                _, sym, m, a = it
                try:
                    self.st.set_adjustment(sym, m, a)
                    self._event("adjust_direct", symbol=sym, mult=m, add=a)
                except Exception as e:  # noqa: BLE001
                    self._event("engine_error", where="set_adjustment", error=f"{type(e).__name__}: {e}")
            elif kind == "override":
                _, dotted, value = it
                try:
                    if value is None:
                        self._overrides_direct.pop(dotted, None)
                        self._reapply_direct_overrides()
                    else:
                        self._overrides_direct[dotted] = value
                        self.st.override(dotted, value)
                    self._event("override_direct", key=dotted, value=value)
                except Exception as e:  # noqa: BLE001
                    self._event("engine_error", where="override", error=f"{type(e).__name__}: {e}")
            elif kind == "close":
                stop = True
        return stop

    def _reapply_direct_overrides(self) -> None:
        st = self.st
        if hasattr(st, "P0"):
            st.overrides = {}
            st.P = copy.deepcopy(st.P0)
            for k, v in self._overrides_direct.items():
                st.override(k, v)
            if hasattr(st, "_cache"):
                st._cache = None

    def _do_reset(self, epoch: int, persist_path: str | None) -> None:
        st = self.st
        old = self._epoch
        st.reset()
        if hasattr(st, "clear_adjustments"):
            st.clear_adjustments()
        st.overrides = {}
        if hasattr(st, "P0"):
            st.P = copy.deepcopy(st.P0)
        st.announcements = []
        st.persist_path = persist_path
        self.persist_path = persist_path
        self._overrides_direct = {}
        if self.adjust_dir:
            assert self._analyst_path and self._console_path and self._engine_adj_path
            for src, dst in ((self._analyst_path, f"adjust_{self.run_id}_e{old}.json"),
                             (self._console_path, f"adjust_console_{self.run_id}_e{old}.json")):
                try:
                    if os.path.exists(src):
                        os.replace(src, os.path.join(self.adjust_dir, dst))
                except OSError as e:
                    self._event("adjust_rotate_error", file=src, error=str(e))
            with self._lock:
                self._console = {}
                self._console_gen += 1
            for p in (self._analyst_path, self._console_path, self._engine_adj_path):
                _atomic_write_json(p, {})
            self._merged_sig = None
            if hasattr(st, "_adjust_mtime"):
                st._adjust_mtime = None
        self._epoch = int(epoch)
        self._seq_hi = None
        self._round = None
        self._msg_time = None
        self._included = -math.inf
        self._event("engine_reset", epoch=epoch, previous=old, persist=persist_path)

    def _file_sig(self, path: str | None) -> tuple:
        if not path:
            return (None,)
        try:
            s = os.stat(path)
            return (s.st_mtime_ns, s.st_size)
        except OSError:
            return (None,)

    def _merge_adjust_files(self) -> bool:
        """Re-merge adjust.json and adjust_console.json into adjust_engine.json when either changed."""
        if not self.adjust_dir:
            return False
        with self._lock:
            gen = self._console_gen
            console = copy.deepcopy(self._console)
        sig = (self._file_sig(self._analyst_path), self._file_sig(self._console_path), gen)
        if sig == self._merged_sig:
            return False
        assert self._analyst_path and self._engine_adj_path
        analyst = _read_json_dict(self._analyst_path)
        merged, problems = merge_adjustments(analyst, console)
        _atomic_write_json(self._engine_adj_path, merged)
        self._merged_sig = sig
        self._event("adjust_merge", effective=merged, problems=problems)
        if problems:
            self._alert("adjust_invalid", "adjustment entries ignored: " + "; ".join(problems), problems=problems)
        return True

    def _adjust_key(self) -> tuple:
        st = self.st
        try:
            adj = json.dumps(getattr(st, "adjust", {}) or {}, sort_keys=True, default=str)
            ov = json.dumps(getattr(st, "overrides", {}) or {}, sort_keys=True, default=str)
        except (TypeError, ValueError):
            adj, ov = repr(getattr(st, "adjust", None)), repr(getattr(st, "overrides", None))
        return adj, ov

    def _diag(self) -> dict[str, Any]:
        st = self.st
        out: dict[str, Any] = {}
        for name, fn in (("cum_damage", lambda: st.cumulative_damage()), ("season_loss", lambda: st.season_loss()),
                         ("status_r3", lambda: st.status(3)), ("pattern", lambda: st.pattern_posterior())):
            try:
                out[name] = fn()
            except Exception:  # noqa: BLE001 - diagnostics are optional
                continue
        return out

    def _pass(self, dirty: bool) -> None:
        """Merge adjustments, compute, and publish when something changed (or on an error)."""
        merged = self._merge_adjust_files()
        dirty = dirty or merged
        st = self.st
        t0 = time.perf_counter()
        err: str | None = None
        vals: Mapping[str, Any] | None = None
        try:
            vals = self._fv_fn(st)
            if not isinstance(vals, Mapping):
                raise TypeError("fair_values did not return a mapping")
            missing = [s for s in SYMBOLS if s not in vals]
            if missing:
                raise KeyError(f"fair values missing {missing}")
            values = {s: (float(vals[s][0]), float(vals[s][1])) for s in SYMBOLS}
        except Exception as e:  # noqa: BLE001 - keep the previous snapshot, count the error
            err = f"{type(e).__name__}: {e}"
            values = {}
        ms = 1000.0 * (time.perf_counter() - t0)
        self.last_compute_ms = ms
        if err is None:
            if ms > self.compute_warn_ms:
                self._event("engine_slow", compute_ms=round(ms, 1))
            if self.consecutive_errors:
                self._event("engine_recovered", after_errors=self.consecutive_errors)
            self.consecutive_errors = 0
            self._down_alerted = False
            key = (self._epoch, getattr(st, "version", None), *self._adjust_key())
            with self._lock:
                prev = self._latest
            if key != self._last_key or prev is None or not prev.ok:
                self._publish(values, ms, True, None, key)
            with self._lock:   # every processed message is now reflected in the latest snapshot
                while self._pending and self._pending[0].bid <= self._processed_bid:
                    self._pending.popleft()
            return
        self.errors += 1
        self.consecutive_errors += 1
        self._event("engine_error", where="compute", error=err, consecutive=self.consecutive_errors)
        if self.consecutive_errors >= self.down_after and not self._down_alerted:
            self._down_alerted = True
            self._alert("engine_down", f"engine compute failed {self.consecutive_errors} times in a row: {err}")
        with self._lock:
            prev = self._latest
        if dirty or prev is None or prev.ok:
            old = dict(prev.values) if prev is not None else {}
            self._publish(old, ms, False, err, self._last_key)

    def _publish(self, values: Mapping[str, tuple[float, float]], ms: float, ok: bool, err: str | None,
                 key: tuple | None) -> None:
        st = self.st
        diag = self._diag() if ok else {}
        with self._lock:
            self._version += 1
            snap = FairValueSnapshot(version=self._version, values=dict(values), season_epoch=self._epoch,
                                     round=self._round, seq_hi=self._seq_hi, msg_time=self._msg_time,
                                     included_mono=self._included, computed_mono=float(self.timesrc.mono()),
                                     compute_ms=ms, ok=ok, error=err, diag=diag,
                                     engine_version=int(getattr(st, "version", 0) or 0))
            self._latest = snap
            if ok:
                self._last_key = key
        cb = self._listener
        if cb is not None:
            try:
                cb()
            except Exception as e:  # noqa: BLE001 - a bad listener must not kill the worker
                self._event("exception", where="engine_listener", error=f"{type(e).__name__}: {e}")

    def status(self) -> dict[str, Any]:
        """Small summary for the status line and logs."""
        fv = self.latest()
        return {"mode": self.mode, "epoch": self._epoch, "version": fv.version if fv else None,
                "engine_version": fv.engine_version if fv else None, "seq_hi": self._seq_hi, "round": self._round,
                "errors": self.errors, "consecutive_errors": self.consecutive_errors,
                "last_compute_ms": self.last_compute_ms, "forward_snapshots": self._forward,
                "snapshot_fix": self.snapshot_fix}
