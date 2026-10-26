"""The synchronous bot core (DESIGN.md sections 2 and 4.D ``app/core.py``).

:class:`BotCore` owns every piece of trading state and never sleeps, starts threads, reads a clock or does network
I/O; its only I/O is logging and the persist job's file write. Every input is a method call carrying ``now`` (the
``TimeSource.mono()`` value) and every output is an :class:`ApiRequest` collected with :meth:`take_requests` or a
control word collected with :meth:`take_control`. ``shell.py`` drives it from asyncio in production and
``sim/harness.py`` drives the same object in virtual time.

Collaborators from groups A, B and C are built by factories (:func:`default_factories`), which import their
modules only when called, so this module imports cleanly while those groups are unfinished and tests can replace
any collaborator with a fake through ``BotCore(factories=...)``. Every factory is called with keyword arguments
only and ignores the ones it does not need:

=============  =======================================================================================
name           keyword arguments (``cfg`` and ``case`` are always passed)
=============  =======================================================================================
schedule       --
limits         --  (returns ``{symbol: L_s}``)
normalizer     log
clock          schedule, log
books          --
positions      --
store          epoch
limiter        --
risk           schedule, log
om             store, positions, books, risk, limiter, log, next_req_id
reconciler     store, positions, log
coverage       log
laws           log, laws_module, coverage, mode   (returns None when ``laws_module`` is None)
law_check      log
runner         schedule, cost, laws, law_check, log
engine         timesrc, log, persist_path, adjust_dir, run_id, restore, mode  (only for ``eng restart``)
=============  =======================================================================================
"""
from __future__ import annotations

import itertools
import math
import os
import threading
import traceback
from collections import Counter, deque
from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from ctcbot.types import (ORDER_ACTIONS, SYMBOLS, ApiKind, ApiRequest, ApiResult, BookEvent, CancelIntent,
                          Command, ConnEvent, EventLog, FairValueProvider, FeedHealth, Fill,
                          HeartbeatEvent, LIVE_STATES, NewsEvent, Phase, Priority, QuoteEvent, RestIntent,
                          SeasonInfo, SeasonResetEvent, ServerTime, SnapshotEvent, SourceFrame, StrategyContext,
                          TargetIntent, TimeSource, TradeEvent, Trigger)

from ctcbot.app import console as _console
from ctcbot.app.persist import engine_log_name, ledger_from_wall, ledger_to_wall, save_state, STATE_FILE

if TYPE_CHECKING:  # pragma: no cover - type hints only; B and C are imported lazily
    from ctcbot.case import CaseDiff, CaseInfo
    from ctcbot.config import Config
    from ctcbot.models.cost_model import CostModel

#: season-reset reasons that come from feed evidence (they open the comp_id merge window, DESIGN.md §4.D)
FEED_RESET_REASONS = frozenset({"storm_formed_r1", "snapshot_last_seq", "round_decrease", "date_change"})
_COMP_CONFIRM_S = 1.0         # a lone new competition_id needs a second answer at least this much later
_COMP_CANDIDATE_MAX_S = 15.0  # ... within this window, else it is dropped
_PLACE_KINDS = frozenset({ApiKind.PLACE, ApiKind.ETF_CREATE, ApiKind.ETF_REDEEM})


# ----------------------------------------------------------------------------- factories
def default_factories() -> dict[str, Callable[..., Any]]:
    """name -> factory building the real A/B/C object. The B and C imports happen inside each factory."""

    def schedule(*, cfg: Any, case: Any, **_: Any) -> Any:
        from ctcbot.case import Schedule
        return Schedule(case.timing, cfg.get("cost.phases"))

    def limits(*, cfg: Any, case: Any, **_: Any) -> dict[str, int]:
        from ctcbot.case import strategy_limits
        return dict(strategy_limits(case, cfg))

    def normalizer(*, cfg: Any, log: EventLog, **_: Any) -> Any:
        from ctcbot.normalize import FeedNormalizer
        return FeedNormalizer(cfg, log)

    def clock(*, cfg: Any, schedule: Any, log: EventLog, **_: Any) -> Any:
        from ctcbot.clock import ClockModel
        return ClockModel(schedule, cfg, log)

    def books(*, cfg: Any, case: Any, **_: Any) -> Any:
        from ctcbot.state.books import BookStore
        return BookStore(_symbols_of(case), cfg)

    def positions(*, case: Any, **_: Any) -> Any:
        from ctcbot.state.positions import PositionBook
        return PositionBook(_symbols_of(case))

    def store(*, epoch: int, **_: Any) -> Any:
        from ctcbot.state.orders import OrderStore
        return OrderStore(epoch)

    def limiter(*, cfg: Any, **_: Any) -> Any:
        from ctcbot.ratelimit import ActionLimiter
        floors = {Priority[str(k).upper()]: int(v) for k, v in dict(cfg.get("rate.floors")).items()}
        team = int(cfg.get("exchange.team_actions_per_min"))
        overdraft = int(cfg.get("rate.kill_overdraft"))
        # max_cap + overdraft = team - 10: the bot alone (kill included) never passes team - 10 per window, and
        # the KILL ceiling does not move with the operator's 'b N' (hardening, DESIGN.md §13)
        return ActionLimiter(int(cfg.get("rate.bot_cap_per_min")), float(cfg.get("rate.window_s")), floors,
                             overdraft, team - 10 - overdraft)

    def risk(*, cfg: Any, case: Any, schedule: Any, log: EventLog, **_: Any) -> Any:
        from ctcbot.exec.risk import RiskManager
        return RiskManager(cfg, case, schedule, log)

    def om(*, cfg: Any, case: Any, store: Any, positions: Any, books: Any, risk: Any, limiter: Any, log: EventLog,
           next_req_id: Callable[[], int], **_: Any) -> Any:
        from ctcbot.exec.order_manager import OrderManager
        return OrderManager(cfg, case, store, positions, books, risk, limiter, log, next_req_id)

    def reconciler(*, cfg: Any, store: Any, positions: Any, log: EventLog, **_: Any) -> Any:
        from ctcbot.exec.reconcile import Reconciler
        return Reconciler(store, positions, cfg, log)

    def coverage(*, cfg: Any, log: EventLog, **_: Any) -> Any:
        from ctcbot.models.coverage import StormCoverage
        return StormCoverage(cfg, log)

    def laws(*, cfg: Any, log: EventLog, laws_module: ModuleType | None, coverage: Any, mode: str, **_: Any) -> Any:
        if laws_module is None:
            return None
        from ctcbot.models.laws import LawsCrossCheck
        return LawsCrossCheck(cfg, log, laws_module, coverage, mode=mode)

    def law_check(*, cfg: Any, log: EventLog, **_: Any) -> Any:
        from ctcbot.models.laws import LawCheck
        return LawCheck(cfg, log)

    def runner(*, cfg: Any, case: Any, schedule: Any, cost: Any, laws: Any, law_check: Any, log: EventLog,
               **_: Any) -> Any:
        from ctcbot.strategies.base import StrategyDeps
        from ctcbot.strategies.runner import StrategyRunner
        return StrategyRunner(StrategyDeps(cfg=cfg, case=case, schedule=schedule, cost=cost, laws=laws,
                                           law_check=law_check, log=log))

    def engine(*, cfg: Any, case: Any, timesrc: TimeSource, log: EventLog, persist_path: str | None,
               adjust_dir: str | None, run_id: str, restore: bool, mode: str = "thread", **_: Any) -> Any:
        from ctcbot.models.engine_adapter import EngineAdapter
        lib = cfg.get("engine.library_seasons", None)
        return EngineAdapter(cfg, case.raw, timesrc, log, mode=mode, persist_path=persist_path,
                             adjust_dir=adjust_dir, run_id=run_id, restore=restore,
                             library_seasons=tuple(lib) if lib else None)

    return {"schedule": schedule, "limits": limits, "normalizer": normalizer, "clock": clock, "books": books,
            "positions": positions, "store": store, "limiter": limiter, "risk": risk, "om": om,
            "reconciler": reconciler, "coverage": coverage, "laws": laws, "law_check": law_check,
            "runner": runner, "engine": engine}


def _symbols_of(case: Any) -> tuple[str, ...]:
    syms = tuple(getattr(case, "symbols", ()) or ())
    return syms or SYMBOLS


@dataclass
class CoreParts:
    """Every collaborator of the core; tests replace any of them with fakes."""
    normalizer: Any; clock: Any; books: Any; positions: Any; store: Any; risk: Any; om: Any; reconciler: Any
    runner: Any; coverage: Any; laws: Any; law_check: Any; limiter: Any


# ----------------------------------------------------------------------------- small helpers
class _CountingLog:
    """EventLog proxy that counts events and alerts by kind for :meth:`BotCore.report`."""

    def __init__(self, inner: EventLog) -> None:
        self.inner = inner
        self._lock = threading.Lock()
        self.events: Counter[str] = Counter()
        self.alerts: Counter[str] = Counter()

    def event(self, kind: str, **fields: Any) -> None:
        with self._lock:
            self.events[kind] += 1
        self.inner.event(kind, **fields)

    def alert(self, kind: str, msg: str, **fields: Any) -> None:
        with self._lock:
            self.alerts[kind] += 1
        self.inner.alert(kind, msg, **fields)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _stage(s: Any) -> str:
    s = str(s or "").lower()
    return "final" if s.startswith("final") else "prelim"


def _conn_name(conn: str) -> str:
    return str(conn).split("#", 1)[0]


def _intent_text(i: Any) -> str:
    if isinstance(i, TargetIntent):
        return f"{i.strategy}:{i.symbol}:T{i.target:+d}/{i.clip_qty}"
    if isinstance(i, RestIntent):
        q = "room" if i.qty is None else str(i.qty)
        return f"{i.strategy}:{i.key}:{i.side}{q}@{i.price}"
    if isinstance(i, CancelIntent):
        return f"{i.strategy}:cancel:{i.scope}:{i.symbol or i.key or '*'}"
    return repr(i)[:80]


@dataclass
class _Cycle:
    """A reconcile cycle in flight: its id, start and the GETs still outstanding."""
    cycle: int
    begin: float
    pending: set = field(default_factory=lambda: {ApiKind.GET_OPEN, ApiKind.GET_POSITIONS})
    failed: bool = False
    fills: list = field(default_factory=list)


class _FeedTracker:
    """Connection state, frame ages and storm-observation ages on the merged stream (FeedHealth inputs)."""

    def __init__(self, names: Sequence[str]) -> None:
        self.conn: dict[str, tuple[str | None, bool]] = {n: (None, False) for n in names}
        self.last_frame: float | None = None
        self.last_news: float | None = None
        self.last_storm_obs: float | None = None
        self.round_key: tuple[int, int | None] | None = None
        self.last_obs_age: float | None = None
        self.max_age_gap: float = 0.0
        self.down_since: float | None = None

    # connections
    def on_control(self, conn: str, up: bool, mono: float) -> None:
        self.conn[_conn_name(conn)] = (conn, bool(up))
        if up:
            self.last_frame = mono if self.last_frame is None else max(self.last_frame, mono)

    def on_frame(self, conn: str, mono: float) -> None:
        name = _conn_name(conn)
        cur, up = self.conn.get(name, (None, False))
        if cur is None or (cur != conn and not up):
            self.conn[name] = (conn, True)           # frames without a control frame: the source is up
        elif cur == conn and not up:
            return                                    # late frame of a connection already reported down
        self.last_frame = mono if self.last_frame is None else max(self.last_frame, mono)

    def connections_up(self) -> int:
        return sum(1 for _, up in self.conn.values() if up)

    def connections(self) -> int:
        return len(self.conn)

    # storm observations
    def on_round(self, epoch: int, rnd: int | None) -> None:
        key = (epoch, rnd)
        if key != self.round_key:
            self.round_key = key
            self.last_obs_age = None
            self.max_age_gap = 0.0

    def on_obs(self, epoch: int, rnd: int | None, age: float | None, mono: float, from_snapshot: bool) -> bool:
        """Register a storm observation. Returns True when it is new information."""
        if rnd is not None:
            self.on_round(epoch, rnd)
        if age is None:
            if from_snapshot:
                return False
            self.last_storm_obs = mono
            return True
        if self.last_obs_age is not None:
            if age <= self.last_obs_age:
                if from_snapshot:
                    return False
            else:
                self.max_age_gap = max(self.max_age_gap, age - self.last_obs_age)
        if self.last_obs_age is None or age > self.last_obs_age:
            self.last_obs_age = age
        self.last_storm_obs = mono
        return True


class _SeasonTracker:
    """Damage reports, announcements and final seqs of the current season (SeasonInfo inputs)."""

    MAX_ANNOUNCEMENTS = 500

    def __init__(self) -> None:
        self.prelim: dict[int, dict] = {}
        self.final: dict[int, dict] = {}
        self.final_seq: dict[int, int] = {}
        self.announcements: list[dict] = []
        self._ann_keys: set = set()

    def on_news_msg(self, msg: Mapping[str, Any], rnd: int | None = None, seq: int | None = None) -> None:
        t = msg.get("type")
        if t == "damage_report":
            r = rnd if rnd is not None else _int(msg.get("round"))
            if r is None:
                return
            if _stage(msg.get("stage")) == "final":
                self.final[r] = dict(msg)
                s = seq if seq is not None else _int(msg.get("seq"))
                self.final_seq[r] = s if s is not None else -1
            else:
                self.prelim[r] = dict(msg)
        elif t == "announcement":
            key = (msg.get("seq"), msg.get("headline"), msg.get("time"))
            if key in self._ann_keys:
                return
            self._ann_keys.add(key)
            self.announcements.append(dict(msg))
            del self.announcements[:-self.MAX_ANNOUNCEMENTS]

    def on_snapshot_msg(self, msg: Mapping[str, Any]) -> None:
        dmg = msg.get("damage")
        if not isinstance(dmg, list):
            return
        for d in dmg:
            if not isinstance(d, Mapping):
                continue
            r = _int(d.get("round"))
            if r is None:
                continue
            if _stage(d.get("stage")) == "final":
                if r not in self.final:
                    self.final[r] = dict(d, type="damage_report")
                    self.final_seq[r] = -1
            elif r not in self.prelim:
                self.prelim[r] = dict(d, type="damage_report")

    def loss_known(self) -> float:
        tot = 0.0
        for m in self.final.values():
            v = _num(m.get("industry_loss_musd"))
            if v is not None:
                tot += v
        return tot

    def state(self) -> dict:
        return {"final": {str(k): v for k, v in self.final.items()},
                "prelim": {str(k): v for k, v in self.prelim.items()},
                "final_seq": {str(k): v for k, v in self.final_seq.items()},
                "loss_known": self.loss_known(), "announcements": list(self.announcements)}

    def load(self, d: Mapping[str, Any] | None) -> None:
        if not isinstance(d, Mapping):
            return
        for src, dst in (("final", self.final), ("prelim", self.prelim)):
            for k, v in dict(d.get(src) or {}).items():
                r = _int(k)
                if r is not None and isinstance(v, Mapping):
                    dst[r] = dict(v)
        for k, v in dict(d.get("final_seq") or {}).items():
            r, s = _int(k), _int(v)
            if r is not None and s is not None:
                self.final_seq[r] = s
        for m in d.get("announcements") or ():
            if isinstance(m, Mapping):
                self.on_news_msg(m)


# ----------------------------------------------------------------------------- the core
class BotCore:
    """Single-threaded trading core. See the module docstring; semantics are DESIGN.md §4.D ``core``.

    ``mode`` is ``"host"`` (live exchange, dry-run or live), ``"replay"`` or ``"sim"``. ``live_label`` only labels
    the status line and ``state.json``: whether an order is sent is decided by the ``RestClient``'s ``LiveToken``,
    and records learn dry-run from ``ApiResult.dry_run``.
    """

    def __init__(self, cfg: "Config", case: "CaseInfo", case_diff: "CaseDiff", engine: FairValueProvider,
                 timesrc: TimeSource, log: EventLog, *, mode: str, run_dir: str | None, run_id: str,
                 laws_module: ModuleType | None, cost: "CostModel", restore: dict | None = None,
                 promotion: bool = False, live_label: bool = False,
                 factories: Mapping[str, Callable[..., Any]] | None = None) -> None:
        if mode not in ("host", "replay", "sim"):
            raise ValueError(f"mode must be host, replay or sim, not {mode!r}")
        self.cfg = cfg
        self.case = case
        self.case_diff = case_diff
        self.engine = engine
        self.timesrc = timesrc
        self.log = _CountingLog(log)
        self.mode = mode
        self.run_dir = run_dir
        self.run_id = run_id
        self.laws_module = laws_module
        self.cost = cost
        self.live_label = bool(live_label)
        self.promotion = bool(promotion)
        self._fac: dict[str, Callable[..., Any]] = dict(default_factories())
        self._fac.update(dict(factories or {}))

        self._req_ids = itertools.count(1)
        self._outbox: list[ApiRequest] = []
        self._control: list[str] = []
        self._output: list[str] = []
        self.quit_requested = False
        self.kill_acks = 0
        self._now = 0.0

        # config values read often
        g = cfg.get
        self._fwd_snapshots = str(g("engine.forward_snapshots", "relevant"))
        self._watchdog_tab = dict(g("feed.watchdog_s"))
        self._feed_down_cancel_s = float(g("feed.feed_down_cancel_s"))
        self._season_confirm_s = float(g("feed.season_confirm_s"))
        self._reset_merge_s = float(g("feed.reset_merge_s"))
        self._pending_max = int(g("feed.pending_buffer_max"))
        self._unknown_hold_s = float(g("risk.unknown_hold_s"))
        self._resume_obs_n = int(g("risk.feed_resume_storm_obs"))
        self._recon_fail_n = int(g("risk.reconcile_fail_hold_n"))
        self._pnl_source = str(g("risk.pnl_source", "auto"))
        self._throttle_freeze_s = float(g("rate.throttle_freeze_s"))
        self._exc_per_min = int(g("app.exceptions_per_min_hold"))
        self._auto_resume = bool(g("app.restart_auto_resume", False))
        self._flat_exclude = frozenset(g("strategies.flatten.exclude", []) or [])
        self._timeout_get_s = float(g("exchange.timeout_get_s", 3.0))
        # a reconcile cycle is dropped only after its GETs could have used their whole timeout and retry
        self._recon_stale_s = self._timeout_get_s * (1 + int(g("exchange.get_retries", 1))) + 0.5
        self._recon_stale_hold_s = float(g("risk.reconcile_stale_hold_s", 10.0))
        self._persist_debounce_s = float(g("app.persist_debounce_s", 1.0))
        self._slow_alert_ms = float(g("engine.slow_alert_ms", 1000.0))

        # A/C collaborators that live for the whole run
        self.schedule = self._make("schedule")
        self.limits: dict[str, int] = dict(self._make("limits"))
        self.normalizer = self._make("normalizer", log=self.log)
        self.clock = self._make("clock", schedule=self.schedule, log=self.log)
        self.books = self._make("books")
        self.positions = self._make("positions")
        self.limiter = self._make("limiter")
        self.risk = self._make("risk", schedule=self.schedule, log=self.log)
        self.store = self._make("store", epoch=self.epoch)
        self.om = self._make_om()
        self.reconciler = self._make("reconciler", store=self.store, positions=self.positions, log=self.log)
        self.coverage = self._make("coverage", log=self.log)
        laws_mode = "inline" if mode == "sim" else str(g("laws.mode", "thread"))
        self.laws = self._make("laws", log=self.log, laws_module=laws_module, coverage=self.coverage, mode=laws_mode)
        self.law_check = self._make("law_check", log=self.log)
        self.runner = self._make("runner", schedule=self.schedule, cost=cost, laws=self.laws,
                                 law_check=self.law_check, log=self.log)

        # feed, season and decision state
        names = [c.get("name") for c in (g("feed.connections", []) or []) if isinstance(c, Mapping)]
        self._feed = _FeedTracker(names if mode == "host" else [])
        self._season = _SeasonTracker()
        self._case_diff_syms: set[str] = set(getattr(case_diff, "symbols", ()) or ())
        self._caseok: set[str] = set()
        self._timing_diff = bool(getattr(case_diff, "timing", False))
        self._timing_ack = False
        self._ctx_news: list[NewsEvent] = []
        self._ctx_snaps: list[SnapshotEvent] = []
        self._ctx_fills: list[Fill] = []
        self._phase_cache: Phase | None = None
        self._paused_cache = False            # the last clock reading was known and not running (a pause)
        self._norm_stats: dict = {}
        self._comp_id: str | None = None
        self._comp_changed_since_reset = False
        self._last_feed_reset_mono: float | None = None
        self._pending_since: float | None = None
        self._pending_buf: deque | None = None
        self._pending_overflow = 0
        self._rounds_started: set[tuple[int, int]] = set()
        self._last_round: dict[int, int] = {}
        self._round_key: tuple[int, int] | None = None
        self._cycle: _Cycle | None = None
        self._req_owner: dict[int, tuple[Any, float]] = {}
        self._time_req: tuple[int, float] | None = None
        self._feed_down_fired = False
        self._feed_down_active = False
        self._resume_snapshot = False
        self._resume_obs = 0
        self._unknown_held = False
        self._recon_down = False
        self._last_recon_ok: float | None = None
        self._recon_stale_alerted = False
        self._comp_candidate: tuple[str, float, float] | None = None     # (comp_id, first seen, last seen)
        self._persist_dirty = False
        self._last_persist = -math.inf
        self._persist_fail_alert = -math.inf
        self._persist_failures = 0
        self._engine_slow_alert = -math.inf
        self._jump_handled: float | None = None
        self._resv: tuple[tuple[int, int], float, float, int] | None = None
        self._prewarmed: set[tuple[int, int]] = set()
        self._exc_times: deque = deque()
        self._last_fv_version: int | None = None
        self._engine_listener: Callable[[], None] | None = None
        self._engine_alive: bool | None = None
        self._engine_log = engine_log_name(g("engine.persist_file", "engine_state_{run_id}_e{epoch}.jsonl"),
                                           run_id, self.epoch)
        self._peak: float | None = None
        self._mtm: float | None = None
        self._last_intents: tuple[str, ...] = ()
        self._intent_repeats = 0
        self._adjust: dict[str, tuple[float, float]] = {}
        self._restart_hold = False
        self._restart_cycles = 0
        self._restart_snaps = 0
        self._started = False
        self._shutdown_done = False
        self._deferred: list[Callable[[float], None]] = []
        self._restore_ledger: list | None = None
        # report counters
        self._n_fills = 0
        self._fills_by_strategy: Counter[str] = Counter()
        self._req_counts: Counter[str] = Counter()
        self._result_counts: Counter[str] = Counter()
        self._rejects = 0
        self._exceptions = 0
        self._resets: Counter[str] = Counter()
        self._resets_provisional = 0
        self._resets_merged = 0
        self._relevant_snaps: list[Any] = []
        self._commands = 0
        self._decisions = 0
        self._engine_submits = 0
        self._dry_seen = False

        if restore:
            self._restore(restore, self.promotion)

    # ------------------------------------------------------------------ construction helpers
    def _make(self, name: str, **kw: Any) -> Any:
        fac = self._fac.get(name)
        if fac is None:
            raise KeyError(f"no factory for {name!r}")
        return fac(cfg=self.cfg, case=self.case, **kw)

    def _make_om(self) -> Any:
        return self._make("om", store=self.store, positions=self.positions, books=self.books, risk=self.risk,
                          limiter=self.limiter, log=self.log, next_req_id=self.next_req_id)

    @property
    def parts(self) -> CoreParts:
        """The current collaborators (the store, OM and reconciler change at each confirmed season reset)."""
        return CoreParts(normalizer=self.normalizer, clock=self.clock, books=self.books, positions=self.positions,
                         store=self.store, risk=self.risk, om=self.om, reconciler=self.reconciler,
                         runner=self.runner, coverage=self.coverage, laws=self.laws, law_check=self.law_check,
                         limiter=self.limiter)

    @property
    def epoch(self) -> int:
        try:
            return int(getattr(self.normalizer, "epoch", 0))
        except (TypeError, ValueError, AttributeError):
            return 0

    def next_req_id(self) -> int:
        """A fresh request id (shared by the core and the order manager)."""
        return next(self._req_ids)

    def set_engine_listener(self, cb: Callable[[], None]) -> None:
        """Register the engine listener (kept so ``eng restart`` can re-attach it to the new adapter)."""
        self._engine_listener = cb
        self.engine.set_listener(cb)

    def killed(self) -> bool:
        try:
            return bool(self.risk.killed())
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ guarded calls and outputs
    def _guard(self, where: str, fn: Callable[..., Any], *args: Any, default: Any = None, **kw: Any) -> Any:
        try:
            return fn(*args, **kw)
        except Exception as e:  # noqa: BLE001 - one broken collaborator must not stop the core
            self._exception(where, e)
            return default

    def _exception(self, where: str, e: BaseException) -> None:
        self._exceptions += 1
        self.log.event("exception", where=where, error=f"{type(e).__name__}: {e}",
                       traceback="".join(traceback.format_exception(type(e), e, e.__traceback__)))
        self._exc_rate(self._now)

    def note_exception(self, where: str, now: float) -> None:
        """Called by the shell when a core call raised (already logged by the shell)."""
        self._exceptions += 1
        self._exc_rate(now)

    def _exc_rate(self, now: float) -> None:
        q = self._exc_times
        q.append(now)
        while q and now - q[0] > 60.0:
            q.popleft()
        if len(q) > self._exc_per_min:
            try:
                if "exceptions" not in self.risk.holds(now):
                    self.risk.hold("exceptions", now, until=now + 60.0, reason=f"{len(q)} exceptions in 60 s")
                    self.log.alert("exceptions", f"{len(q)} exceptions within 60 s: holding new risk")
            except Exception:  # noqa: BLE001
                pass

    def _req(self, kind: ApiKind, now: float, priority: Priority = Priority.HEDGE, **kw: Any) -> ApiRequest:
        return ApiRequest(req_id=self.next_req_id(), kind=kind, priority=priority, created_mono=now, **kw)

    def _emit(self, reqs: Sequence[ApiRequest] | None, now: float, owner: Any = None) -> None:
        if not reqs:
            return
        om = owner if owner is not None else self.om
        for r in reqs:
            if not isinstance(r, ApiRequest):
                continue
            if r.kind in ORDER_ACTIONS:
                self._req_owner[r.req_id] = (om, now)
                self._persist_dirty = True
            self._req_counts[str(r.kind)] += 1
            self._outbox.append(r)

    def take_requests(self) -> list[ApiRequest]:
        out, self._outbox = self._outbox, []
        return out

    def take_control(self) -> list[str]:
        out, self._control = self._control, []
        return out

    def take_output(self) -> list[str]:
        """Console output lines produced by ``st``, ``pos``, ``ord``, ``ann``, ``holds`` and command errors."""
        out, self._output = self._output, []
        return out

    def _say(self, text: str) -> None:
        self._output.append(text)

    # ------------------------------------------------------------------ jobs
    def jobs(self) -> list[tuple[str, float]]:
        g = self.cfg.get
        js = [("reconcile", float(g("app.reconcile_s"))), ("clock_poll", float(g("clock.server_poll_s"))),
              ("tick", float(g("app.tick_s")))]
        if self.run_dir and self.mode != "sim":
            js += [("persist", float(g("app.persist_s"))), ("status", float(g("app.status_s")))]
        return js

    def run_job(self, name: str, now: float) -> None:
        self._now = now
        if name == "reconcile":
            self._job_reconcile(now)
        elif name == "clock_poll":
            self._job_clock_poll(now)
        elif name == "tick":
            self._tick(now)
        elif name == "persist":
            self._job_persist(now)
        elif name == "status":
            self.log.event("status", **self.status(now))
        else:
            self.log.event("job_unknown", name=name)

    def watchdog_s(self) -> float:
        """``feed.watchdog_s`` for the clock phase, else the normalizer's phase hint, else ``unknown``.

        While the clock is known but not running (a pause: the server says paused, or the frozen 09:30:00
        snapshots of a pre-season pause) the pause value ``closed`` (35 s) applies whatever the frozen phase
        says, because the server then sends a frame only every ~30 s (integration change to DESIGN.md §4.D).
        """
        tab = self._watchdog_tab
        ph = self._phase_cache
        key = None
        if self._paused_cache and "closed" in tab:
            key = "closed"
        elif ph is not None and ph is not Phase.UNKNOWN:
            key = str(ph.value if isinstance(ph, Phase) else ph)
        else:
            hint = self._guard("normalizer.phase_hint", self.normalizer.phase_hint)
            if hint:
                key = str(hint)
        if key not in tab:
            key = "unknown"
        return float(tab.get(key, 35.0))

    # ------------------------------------------------------------------ start / shutdown
    def start(self, now: float) -> None:
        """Initial GET_TIME and a reconcile cycle; applies restore holds that need ``now``."""
        self._now = now
        self._started = True
        self._feed.down_since = now
        for fn in self._deferred:
            self._guard("start.deferred", fn, now)
        self._deferred.clear()
        if self.laws is None:
            self.log.alert("laws_unavailable", "settlement laws module unavailable: S1-r3 excludes the closure "
                                               "symbols")
        self._last_recon_ok = now
        self._job_clock_poll(now)
        self._job_reconcile(now)
        self.log.event("core_start", mode=self.mode, run_id=self.run_id, epoch=self.epoch,
                       label="LIVE" if self.live_label else "DRY", restore=self._restart_hold,
                       promotion=self.promotion, limits=self.limits)

    def shutdown(self, now: float) -> None:
        """``cancel_all(KILL)`` if any live non-dry order exists. Safe to call more than once."""
        self._now = now
        if self._has_live_orders():
            reqs = self._guard("om.cancel_all", self.om.cancel_all, Priority.KILL, now, reason="shutdown")
            self._emit(reqs, now)
        if not self._shutdown_done:
            self._shutdown_done = True
            self.log.event("shutdown", report=self.report())

    def begin_shutdown(self, reason: str, now: float) -> None:
        """Start an orderly exit (``q``, a signal, the end of the sources): the kill path (freeze, KILL
        CANCEL_ALL and follow-ups), so no tick, target or rest can place anything while the shell waits for
        :meth:`shutdown_complete` (hardening: a single cancel-all left new orders open)."""
        self._now = now
        self._kill(reason, now)

    def shutdown_complete(self) -> bool:
        """True once nothing can still be resting: the kill completed (every kill-set record terminal and two
        clean reconcile cycles), or, without a kill, no live non-dry order exists."""
        if self.killed():
            st = self._guard("om.kill_status", self.om.kill_status, default={}) or {}
            return bool(st.get("complete"))
        return not self._has_live_orders()

    def close(self) -> None:
        """Release background resources of collaborators (the laws worker thread). Safe to call twice."""
        for obj in (self.laws,):
            fn = getattr(obj, "close", None)
            if callable(fn):
                self._guard("close", fn)

    def _has_live_orders(self, store: Any = None) -> bool:
        st = store if store is not None else self.store
        try:
            recs = st.live()
        except Exception:  # noqa: BLE001
            return False
        for r in recs or ():
            if not getattr(r, "dry", False) and getattr(r, "state", None) in LIVE_STATES:
                return True
        return False

    # ------------------------------------------------------------------ frames
    def on_frames(self, frames: Sequence[SourceFrame], now: float) -> None:
        """Handle one batch the shell (or the harness) drained in full."""
        self._now = now
        events: list[Any] = []
        for sf in frames:
            if not isinstance(sf, SourceFrame):
                continue
            if sf.control is not None:
                self._feed.on_control(sf.conn, sf.control == "up", sf.recv_mono)
            else:
                self._feed.on_frame(sf.conn, sf.recv_mono)
            evs = self._guard("normalizer.ingest", self.normalizer.ingest, sf, default=())
            if evs:
                events.extend(evs)
        evs = self._guard("normalizer.poll", self.normalizer.poll, now, default=())
        if evs:
            events.extend(evs)
        flags = self._apply_events(events, now)
        started = self._drain_round_events(now)
        if (flags["news"] or flags["released"]) and not started:
            self._decide(Trigger.NEWS, now)

    def _apply_events(self, events: Sequence[Any], now: float) -> dict[str, bool]:
        flags = {"news": False, "released": False}
        batch: list[dict] = []
        batch_mono = -math.inf
        for ev in events:
            try:
                if isinstance(ev, SeasonResetEvent):
                    if batch:
                        self._submit(batch, batch_mono)
                        batch, batch_mono = [], -math.inf
                    if ev.provisional:
                        self._season_suspect(ev, now)
                    else:
                        self._season_change(ev, now)
                elif isinstance(ev, BookEvent):
                    self.books.on_book(ev)
                elif isinstance(ev, QuoteEvent):
                    self.books.on_quote(ev)
                elif isinstance(ev, TradeEvent):
                    self.books.on_trade(ev)
                elif isinstance(ev, NewsEvent):
                    self._on_news(ev, now)
                    batch.append(ev.msg)
                    batch_mono = max(batch_mono, ev.recv_mono)
                    flags["news"] = True
                elif isinstance(ev, SnapshotEvent):
                    if ev.stage == "released":
                        flags["released"] = True
                        if self._on_released(ev, now):
                            batch.append(ev.msg)
                            batch_mono = max(batch_mono, ev.recv_mono)
                    else:
                        self._on_arrived(ev, now)
                elif isinstance(ev, ConnEvent):
                    self._feed.on_control(ev.conn, ev.up, ev.recv_mono)
                    self.log.event("conn", conn=ev.conn, up=ev.up)
                elif isinstance(ev, HeartbeatEvent):
                    pass
            except Exception as e:  # noqa: BLE001
                self._exception(f"event:{type(ev).__name__}", e)
        if batch:
            self._submit(batch, batch_mono)
        return flags

    def _submit(self, msgs: list[dict], recv_mono: float) -> None:
        self._engine_submits += 1
        self._guard("engine.submit", self.engine.submit, list(msgs), recv_mono)

    def _on_news(self, ev: NewsEvent, now: float) -> None:
        msg = ev.msg
        mtype = ev.mtype
        epoch = ev.season_epoch
        if ev.market_time:
            self._guard("clock.observe_feed", self.clock.observe_feed, ev.market_time, ev.round, ev.recv_mono,
                        "storm" if mtype == "storm" else "news", epoch)
        self._season.on_news_msg(msg, ev.round, ev.seq)
        self._feed.last_news = ev.recv_mono
        if mtype == "storm":
            if self._feed.on_obs(epoch, ev.round, _num(msg.get("age")), ev.recv_mono, from_snapshot=False):
                if self._feed_down_active and self._feed_is_up(now):
                    self._resume_obs += 1
        if self.laws is not None:
            self._guard("laws.on_news", self.laws.on_news, msg)
        self._guard("coverage.on_news", self.coverage.on_news, msg)
        if mtype == "damage_report":
            self.log.event("report_news", round=ev.round, stage=_stage(msg.get("stage")), seq=ev.seq,
                           recv_mono=ev.recv_mono, now=now)
        if mtype == "damage_report" and ev.round in (1, 2):
            fv_before = self._guard("engine.latest", self.engine.latest)
            views = self._guard("books.views", self.books.views, now, default={}) or {}
            self._guard("law_check.on_report", self.law_check.on_report, ev.round, _stage(msg.get("stage")), ev.seq,
                        fv_before, views, now)
        if mtype == "announcement":
            self.log.event("announcement_seen", headline=msg.get("headline"), symbols=msg.get("symbols"),
                           round=ev.round)
        if self._pending_buf is not None:
            self._buffer(("news", msg, ev.recv_mono))
        self._ctx_news.append(ev)

    def _on_arrived(self, ev: SnapshotEvent, now: float) -> None:
        if ev.market_time:
            self._guard("clock.observe_feed", self.clock.observe_feed, ev.market_time, ev.round, ev.recv_mono,
                        "snapshot", ev.season_epoch, phase=ev.phase)
        self._season.on_snapshot_msg(ev.msg)
        obs = ev.msg.get("obs") if isinstance(ev.msg, Mapping) else None
        if ev.phase == "storm" and isinstance(obs, Mapping):
            if self._feed.on_obs(ev.season_epoch, ev.round, _num(obs.get("age")), ev.recv_mono, from_snapshot=True):
                if self._feed_down_active and self._feed_is_up(now):
                    self._resume_obs += 1
        self._ctx_snaps.append(ev)

    def _on_released(self, ev: SnapshotEvent, now: float) -> bool:
        """Handle a released snapshot; returns True when it goes to the engine."""
        if self._feed_down_active and self._feed_is_up(now):
            self._resume_snapshot = True
        if self._restart_hold:
            self._restart_snaps += 1
        to_engine = False
        if ev.engine_relevant:
            self._relevant_snaps.append(ev.msg.get("last_seq") if isinstance(ev.msg, Mapping) else None)
            if self.laws is not None:
                self._guard("laws.on_snapshot", self.laws.on_snapshot, ev.msg)
            self._guard("coverage.on_snapshot", self.coverage.on_snapshot, ev.msg)
            to_engine = self._fwd_snapshots in ("relevant", "all")
            if self._pending_buf is not None:
                self._buffer(("snapshot", ev.msg, ev.recv_mono))
        elif self._fwd_snapshots == "all":
            to_engine = True
        self._ctx_snaps.append(ev)
        return to_engine

    def _buffer(self, item: tuple[str, dict, float]) -> None:
        buf = self._pending_buf
        if buf is None:
            return
        if len(buf) >= self._pending_max:
            buf.popleft()
            self._pending_overflow += 1
            if self._pending_overflow == 1 or self._pending_overflow % 1000 == 0:
                self.log.alert("pending_buffer_overflow", f"season-reset buffer full ({self._pending_max}); "
                                                          f"dropped {self._pending_overflow} oldest")
        buf.append(item)

    # ------------------------------------------------------------------ seasons
    def _season_suspect(self, ev: SeasonResetEvent, now: float) -> None:
        self._resets_provisional += 1
        self._guard("risk.hold", self.risk.hold, "season_suspect", now, sticky=True, reason=str(ev.reason))
        self.log.alert("season_reset_suspect", f"possible new season ({ev.reason}); holding new risk until it is "
                                               f"confirmed or rejected", reason=ev.reason, new_epoch=ev.new_epoch)
        self._pending_since = now
        self._pending_buf = deque()
        self._pending_overflow = 0
        self._emit([self._req(ApiKind.GET_TIME, now)], now)
        self._time_req = (self._outbox[-1].req_id, now)

    def _season_change(self, ev: SeasonResetEvent, now: float, comp_confirmed: bool = False) -> None:
        """A confirmed season reset: cancel, reset every per-season object, replay the pending buffer."""
        was_provisional = self._pending_buf is not None
        old_om = self.om
        if self._has_live_orders():
            reqs = self._guard("om.cancel_all", old_om.cancel_all, Priority.KILL, now, reason="season_reset")
            self._emit(reqs, now, owner=old_om)
        epoch = int(ev.new_epoch) if ev.new_epoch is not None else self.epoch
        self._guard("books.reset", self.books.reset)
        self._guard("positions.reset", self.positions.reset)
        self.store = self._make("store", epoch=epoch)
        self.om = self._make_om()
        self.reconciler = self._make("reconciler", store=self.store, positions=self.positions, log=self.log)
        self._cycle = None
        self._guard("risk.on_round_start", self.risk.on_round_start, 0, 0.0, now)
        self._guard("coverage.reset", self.coverage.reset)
        if self.laws is not None:
            self._guard("laws.reset", self.laws.reset)
        self._guard("law_check.reset", self.law_check.reset)
        self._guard("runner.reset_season", self.runner.reset_season)
        self._engine_log = engine_log_name(self.cfg.get("engine.persist_file", "engine_state_{run_id}_e{epoch}.jsonl"),
                                           self.run_id, epoch)
        path = os.path.join(self.run_dir, self._engine_log) if self.run_dir else None
        self._guard("engine.reset_season", self.engine.reset_season, epoch, path)
        comp = self._comp_id if (comp_confirmed or ev.reason in ("comp_id",)) else None
        self._guard("clock.on_season_reset", self.clock.on_season_reset, epoch, comp)
        self._guard("limiter.clear_reservations", self.limiter.clear_reservations)
        self._resv = None
        self._guard("risk.release", self.risk.release, "season_suspect", now)
        self._season = _SeasonTracker()
        self._feed.on_round(epoch, None)
        self._round_key = None
        self._peak = None
        self._last_fv_version = None
        self._ctx_news = [e for e in self._ctx_news if e.season_epoch == epoch]
        self._ctx_snaps = [e for e in self._ctx_snaps if e.season_epoch == epoch]
        self._ctx_fills = []
        self._unknown_held = False
        buf = self._pending_buf
        self._pending_buf = None
        self._pending_since = None
        replayed = 0
        if buf:
            msgs = [m for _, m, _ in buf]
            last = max((t for _, _, t in buf), default=-math.inf)
            self._submit(msgs, last)
            for kind, m, _ in buf:
                if kind == "news":
                    self._season.on_news_msg(m, _int(m.get("round")), _int(m.get("seq")))
                    if self.laws is not None:
                        self._guard("laws.on_news", self.laws.on_news, m)
                    self._guard("coverage.on_news", self.coverage.on_news, m)
                else:
                    self._season.on_snapshot_msg(m)
                    if self.laws is not None:
                        self._guard("laws.on_snapshot", self.laws.on_snapshot, m)
                    self._guard("coverage.on_snapshot", self.coverage.on_snapshot, m)
                replayed += 1
        if ev.reason in FEED_RESET_REASONS and not comp_confirmed:
            self._last_feed_reset_mono = now
            self._comp_changed_since_reset = False
            cand = self._comp_candidate
            if cand is not None:            # a new comp_id seen just before the feed evidence: the same change
                self._comp_candidate = None
                old, self._comp_id = self._comp_id, cand[0]
                self._comp_changed_since_reset = True
                self._resets_merged += 1
                self._guard("risk.release", self.risk.release, "comp_id_suspect", now)
                self._guard("clock.on_season_reset", self.clock.on_season_reset, epoch, self._comp_id)
                self.log.event("season_reset_merged", old=old, new=cand[0], since_reset_s=0.0, order="comp_first")
        else:
            self._last_feed_reset_mono = None
        if comp_confirmed and self._comp_candidate is not None:
            self._comp_candidate = None
            self._guard("risk.release", self.risk.release, "comp_id_suspect", now)
        self._resets[str(ev.reason)] += 1
        self.log.event("season_reset", reason=ev.reason, epoch=epoch, provisional_first=was_provisional,
                       replayed=replayed, engine_log=self._engine_log, comp_id=self._comp_id)

    def _comp_id_rule(self, cid: str | None, now: float) -> None:
        if cid is None:
            return
        cid = str(cid)
        if self._comp_id is None:
            self._comp_id = cid
            self.log.event("comp_id", comp_id=cid)
            return
        if cid == self._comp_id:
            if self._comp_candidate is not None:       # a lone odd answer: not a new season (hardening)
                cand = self._comp_candidate
                self._comp_candidate = None
                self._guard("risk.release", self.risk.release, "comp_id_suspect", now)
                self.log.alert("comp_id_flap", f"GET /competitions/time answered competition {cand[0]} once, then "
                                               f"{cid} again: ignored (no season reset)", odd=cand[0], current=cid)
            return
        pending = getattr(self.normalizer, "pending_reset", None)
        in_merge = (self._last_feed_reset_mono is not None and now - self._last_feed_reset_mono <= self._reset_merge_s
                    and not self._comp_changed_since_reset)
        if not pending and not in_merge:
            # a new comp_id with no feed evidence must be seen on two answers at least 1 s apart (hardening)
            cand = self._comp_candidate
            if cand is None or cand[0] != cid or now - cand[1] > _COMP_CANDIDATE_MAX_S:
                self._comp_candidate = (cid, now, now)
                self._guard("risk.hold", self.risk.hold, "comp_id_suspect", now, until=now + _COMP_CANDIDATE_MAX_S,
                            reason=f"competition_id {cid} seen once")
                self.log.event("comp_id_candidate", old=self._comp_id, new=cid)
                return
            if now - cand[1] < _COMP_CONFIRM_S:
                self._comp_candidate = (cid, cand[1], now)
                return
            self._comp_candidate = None
            self._guard("risk.release", self.risk.release, "comp_id_suspect", now)
        old, self._comp_id = self._comp_id, cid
        self.log.event("comp_id_change", old=old, new=cid)
        if getattr(self.normalizer, "pending_reset", None):
            ev = self._guard("normalizer.confirm_season_reset", self.normalizer.confirm_season_reset, "comp_id", now)
            if ev is not None:
                self._season_change(ev, now, comp_confirmed=True)
                return
        if (self._last_feed_reset_mono is not None and now - self._last_feed_reset_mono <= self._reset_merge_s
                and not self._comp_changed_since_reset):
            self._comp_changed_since_reset = True
            self._resets_merged += 1
            self.log.event("season_reset_merged", old=old, new=cid,
                           since_reset_s=round(now - self._last_feed_reset_mono, 3))
            return
        ev = self._guard("normalizer.force_season_reset", self.normalizer.force_season_reset, "comp_id", now)
        if ev is not None:
            self._season_change(ev, now, comp_confirmed=True)

    # ------------------------------------------------------------------ rounds
    def _drain_round_events(self, now: float) -> bool:
        evs = self._guard("clock.round_events", self.clock.round_events, default=()) or ()
        started = False
        for item in evs:
            try:
                ep, rnd = int(item[0]), int(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            if self._round_start(ep, rnd, now):
                started = True
        return started

    def _round_start(self, epoch: int, rnd: int, now: float) -> bool:
        key = (epoch, rnd)
        if key in self._rounds_started:
            return False
        self._rounds_started.add(key)
        prev = self._last_round.get(epoch)
        self._last_round[epoch] = max(prev or 0, rnd)
        mtm = self._compute_mtm(now)
        self._guard("risk.on_round_start", self.risk.on_round_start, rnd, mtm if mtm is not None else 0.0, now)
        self._peak = mtm
        self._round_key = key
        self._feed.on_round(epoch, rnd)
        not_flat: dict[str, int] = {}
        if prev is not None and rnd > prev and rnd in (2, 3):
            for s, q in dict(self.positions.pos).items():
                if q and s not in self._flat_exclude:
                    not_flat[s] = int(q)
            if not_flat:
                self.log.alert("not_flat", f"round {rnd} started with open positions: {not_flat}", round=rnd,
                               positions=not_flat)
        self.log.event("round_start", epoch=epoch, round=rnd, prev=prev, mtm=mtm, not_flat=not_flat)
        self._decide(Trigger.ROUND_START, now)
        return True

    # ------------------------------------------------------------------ decisions
    def _feed_is_up(self, now: float) -> bool:
        lf = self._feed.last_frame
        return self._feed.connections_up() > 0 and lf is not None and now - lf <= self.watchdog_s()

    def _feed_health(self, now: float) -> FeedHealth:
        f = self._feed
        up = self._feed_is_up(now)
        if up:
            f.down_since = None
        elif f.down_since is None:
            f.down_since = now
        st = self._norm_stats
        gaps = st.get("seq_gaps")
        if not isinstance(gaps, int):
            gaps = 0
        dup = st.get("dup_news")
        if not isinstance(dup, int):
            dup = 0
        wm = _int(getattr(self.normalizer, "wm", -1))
        mx = _int(getattr(self.normalizer, "max_seq", -1))
        return FeedHealth(
            up=up, connections_up=f.connections_up(), connections=f.connections(),
            last_frame_age_s=None if f.last_frame is None else max(0.0, now - f.last_frame),
            last_news_age_s=None if f.last_news is None else max(0.0, now - f.last_news),
            last_storm_obs_age_s=None if f.last_storm_obs is None else max(0.0, now - f.last_storm_obs),
            round_max_age_gap_s=float(f.max_age_gap), down_since_mono=f.down_since, dup_news=dup, seq_gaps=gaps,
            wm=-1 if wm is None else wm, max_seq=-1 if mx is None else mx)

    @property
    def season(self) -> SeasonInfo:
        """The current :class:`SeasonInfo`."""
        cov = self._guard("coverage.coverage", self.coverage.coverage, default={}) or {}
        s = self._season
        return SeasonInfo(season_epoch=self.epoch, comp_id=self._comp_id, prelim=dict(s.prelim),
                          final=dict(s.final), final_seq=dict(s.final_seq), loss_known=s.loss_known(),
                          announcements=tuple(s.announcements), case_diff=frozenset(self._case_diff_syms),
                          timing_diff=self._timing_diff and not self._timing_ack, coverage=dict(cov),
                          reset_pending=getattr(self.normalizer, "pending_reset", None))

    def _context(self, now: float, trigger: Trigger, command: Command | None = None) -> StrategyContext:
        clock = self.clock.read(now)
        self._phase_cache = clock.phase
        self._paused_cache = bool(clock.known and not clock.running)
        try:
            budget = int(self.limiter.available(now)) - int(self.limiter.floor(Priority.TAKE, now))
        except Exception:  # noqa: BLE001
            budget = 0
        return StrategyContext(
            now=now, trigger=trigger, clock=clock, schedule=self.schedule, limits=self.limits,
            books=self.books.views(now), positions=dict(self.positions.pos), working=self.om.working(),
            fv=self.engine.latest(), engine=self.engine.health(now), feed=self._feed_health(now),
            season=self.season, risk_mode=self.risk.mode, disabled=frozenset(self.risk.disabled()),
            frozen=frozenset(self.runner.frozen()), leadlag=frozenset(self.runner.leadlag_symbols()),
            news=tuple(self._ctx_news), snapshots=tuple(self._ctx_snaps), fills=tuple(self._ctx_fills),
            command=command, budget_take=max(0, budget))

    def _decide(self, trigger: Trigger, now: float, command: Command | None = None) -> None:
        try:
            ctx = self._context(now, trigger, command)
        except Exception as e:  # noqa: BLE001
            self._exception("decide.context", e)
            return
        self._decisions += 1
        if command is not None:
            intents = self._guard("runner.on_command", self.runner.on_command, command, ctx, default=[]) or []
        else:
            intents = self._guard("runner.decide", self.runner.decide, ctx, default=[]) or []
            # news, snapshots and fills are handed to the strategies exactly once
            self._ctx_news.clear()
            self._ctx_snaps.clear()
            self._ctx_fills.clear()
        intents = list(intents)
        self._guard("om.set_intents", self.om.set_intents, intents, now)
        reqs = self._guard("om.step", self.om.step, now, ctx.clock, ctx.feed, default=[])
        self._emit(reqs, now)
        if intents:
            self._log_intents(trigger, intents)

    def _log_intents(self, trigger: Trigger, intents: Sequence[Any]) -> None:
        items = tuple(sorted(_intent_text(i) for i in intents))
        if items == self._last_intents:
            self._intent_repeats += 1
            if self._intent_repeats % 40:
                return
        else:
            self._intent_repeats = 0
        self._last_intents = items
        self.log.event("intent", trigger=str(trigger), n=len(items), items=list(items),
                       repeats=self._intent_repeats)

    # ------------------------------------------------------------------ fair values
    def on_fair_values(self, now: float) -> None:
        """Engine listener (on the loop): law check, then a decision cycle with trigger FV."""
        self._now = now
        fv = self._guard("engine.latest", self.engine.latest)
        if fv is None:
            return
        ver = getattr(fv, "version", None)
        if ver is not None and ver == self._last_fv_version:
            return
        self._last_fv_version = ver
        self._guard("law_check.on_fv", self.law_check.on_fv, fv, now)
        self._decide(Trigger.FV, now)

    # ------------------------------------------------------------------ API results
    def on_api_result(self, res: ApiResult, now: float) -> None:
        self._now = now
        if not isinstance(res, ApiResult):
            return
        req = res.req
        self._result_counts[f"{req.kind}:{res.status}"] += 1
        try:
            if req.kind == ApiKind.GET_TIME:
                self._on_time(res, now)
            elif req.kind in (ApiKind.GET_OPEN, ApiKind.GET_POSITIONS):
                self._on_reconcile(res, now)
            elif req.kind in ORDER_ACTIONS:
                self._on_order_result(res, now)
            else:
                self.log.event("api_result", req_kind=str(req.kind), status=res.status, error=res.error)
        except Exception as e:  # noqa: BLE001
            self._exception(f"on_api_result:{req.kind}", e)
        self._drain_round_events(now)

    def _on_time(self, res: ApiResult, now: float) -> None:
        if self._time_req is not None and self._time_req[0] == res.req.req_id:
            self._time_req = None
        if res.status in (401, 403, 429):
            self._guard("risk.on_result", self.risk.on_result, res, now)
            if res.throttled:
                self._guard("limiter.on_throttled", self.limiter.on_throttled, now, self._throttle_freeze_s)
        if not res.ok:
            return
        st = res.parsed if isinstance(res.parsed, ServerTime) else None
        if st is None and isinstance(res.data, Mapping):
            try:
                from ctcbot.rest import parse_time   # A's tolerant parser, for results built without `parsed`
                st = parse_time(res.data)
            except Exception:  # noqa: BLE001
                st = None
            if not isinstance(st, ServerTime):
                st = None
        if st is None:
            self.log.event("time_parse_warn", status=res.status)
            return
        odd = st.comp_id is not None and self._comp_id is not None and str(st.comp_id) != self._comp_id
        self._comp_id_rule(st.comp_id, now)
        if odd and self._comp_candidate is not None and self._comp_candidate[0] == str(st.comp_id):
            return          # an unconfirmed other competition's clock must not move ours
        self._guard("clock.observe_server", self.clock.observe_server, st, res.sent_mono, res.done_mono)

    def _on_reconcile(self, res: ApiResult, now: float) -> None:
        owner = self._req_owner.pop(res.req.req_id, (None, 0.0))[0]
        if owner is not None and owner is not self.reconciler:
            self.log.event("reconcile_stale_result", req_kind=str(res.req.kind), cycle=res.req.cycle)
            return
        if res.status in (401, 403, 429):
            self._guard("risk.on_result", self.risk.on_result, res, now)
            if res.throttled:
                self._guard("limiter.on_throttled", self.limiter.on_throttled, now, self._throttle_freeze_s)
        fills = self._guard("reconciler.on_result", self.reconciler.on_result, res, now)
        cy = self._cycle
        if fills:
            self._handle_fills(fills, now)
        hint = getattr(self.reconciler, "season_hint", None)
        if isinstance(hint, (int, float)) and not isinstance(hint, bool) and hint != self._jump_handled:
            self._jump_handled = hint            # many symbols moved at once: maybe a new season; ask now
            if self._time_req is None or now - self._time_req[1] >= 0.5:
                r = self._req(ApiKind.GET_TIME, now)
                self._time_req = (r.req_id, now)
                self._req_counts[str(r.kind)] += 1
                self._outbox.append(r)
        if cy is None or res.req.cycle != cy.cycle:
            return
        cy.pending.discard(res.req.kind)
        if not res.ok:
            cy.failed = True
        if cy.pending:
            return
        self._cycle = None
        self._cycle_done(not cy.failed and fills is not None, now)

    def _cycle_done(self, ok: bool, now: float) -> None:
        fails = self._guard("reconciler.consecutive_failures", self.reconciler.consecutive_failures, default=0) or 0
        if ok:
            self._last_recon_ok = now
            self._recon_stale_alerted = False
            if self._restart_hold:
                self._restart_cycles += 1
            if self._recon_down and fails < self._recon_fail_n:
                self._recon_down = False
                self._guard("risk.release", self.risk.release, "reconcile_down", now)
        if fails >= self._recon_fail_n and not self._recon_down:
            self._recon_down = True
            self._guard("risk.hold", self.risk.hold, "reconcile_down", now, sticky=True,
                        reason=f"{fails} consecutive reconcile failures")
            self.log.alert("reconcile_down", f"{fails} consecutive reconcile failures: holding new risk")
        if ok and self.killed():
            rows = self._guard("reconciler.last_open_rows", self.reconciler.last_open_rows)
            reqs = self._guard("om.kill_followup", self.om.kill_followup, rows, now)
            self._emit(reqs, now)

    def _on_order_result(self, res: ApiResult, now: float) -> None:
        owner = self._req_owner.pop(res.req.req_id, (self.om, 0.0))[0] or self.om
        self._persist_dirty = True
        if res.dry_run:
            self._dry_seen = True
        if res.req.kind == ApiKind.CANCEL_ALL and res.req.priority == Priority.KILL:
            self.kill_acks += 1
        if res.req.kind == ApiKind.PLACE and res.status is not None and 400 <= res.status < 500:
            self._rejects += 1
        fills = self._guard("om.on_result", owner.on_result, res, now, default=[]) or []
        self._guard("risk.on_result", self.risk.on_result, res, now)
        if res.throttled:
            self._guard("limiter.on_throttled", self.limiter.on_throttled, now, self._throttle_freeze_s)
        if owner is self.om:
            if fills:
                self._handle_fills(fills, now)
        elif fills:
            for f in fills:
                self.log.event("fill_retired", **self._fill_fields(f))
        clock = self._guard("clock.read", self.clock.read, now)
        if clock is not None:
            reqs = self._guard("om.step", self.om.step, now, clock, self._feed_health(now), default=[])
            self._emit(reqs, now)

    @staticmethod
    def _fill_fields(f: Any) -> dict:
        return {"strategy": getattr(f, "strategy", None), "key": getattr(f, "key", None),
                "symbol": getattr(f, "symbol", None), "side": str(getattr(f, "side", "")),
                "qty": getattr(f, "qty", None), "price": getattr(f, "price", None),
                "quality": str(getattr(f, "quality", "")), "source": getattr(f, "source", None),
                "order_id": getattr(f, "order_id", None), "local_id": getattr(f, "local_id", None),
                "ref_mid": getattr(f, "ref_mid", None)}

    def _handle_fills(self, fills: Sequence[Fill], now: float) -> None:
        for f in fills:
            self._n_fills += 1
            self._fills_by_strategy[str(getattr(f, "strategy", "?"))] += 1
            self._guard("risk.on_fill", self.risk.on_fill, f, now)
            self._ctx_fills.append(f)
            self.log.event("fill", **self._fill_fields(f))

    # ------------------------------------------------------------------ jobs
    def _job_reconcile(self, now: float) -> None:
        cy = self._cycle
        if cy is not None and now - cy.begin < self._recon_stale_s:
            return
        dropped = cy is not None
        if dropped:
            self.log.event("reconcile_cycle_dropped", cycle=cy.cycle, pending=sorted(str(k) for k in cy.pending))
        cyc = self._guard("reconciler.begin_cycle", self.reconciler.begin_cycle, now)
        if dropped:
            self._cycle = None
            self._cycle_done(False, now)           # a dropped cycle is a failure (hardening)
        if cyc is None:
            return
        self._cycle = _Cycle(cycle=cyc, begin=now)
        reqs = [self._req(ApiKind.GET_OPEN, now, cycle=cyc), self._req(ApiKind.GET_POSITIONS, now, cycle=cyc)]
        for r in reqs:
            self._req_owner[r.req_id] = (self.reconciler, now)
            self._req_counts[str(r.kind)] += 1
            self._outbox.append(r)

    def _job_clock_poll(self, now: float) -> None:
        tr = self._time_req
        if tr is not None and now - tr[1] < self._timeout_get_s + 0.5:
            return
        r = self._req(ApiKind.GET_TIME, now)
        self._time_req = (r.req_id, now)
        self._req_counts[str(r.kind)] += 1
        self._outbox.append(r)

    def _job_persist(self, now: float) -> None:
        if not self.run_dir:
            return
        self._last_persist = now
        self._persist_dirty = False
        try:
            save_state(os.path.join(self.run_dir, STATE_FILE), self.state_dict(now))
        except Exception as e:  # noqa: BLE001 - a full disk must not stop trading (hardening): alert, no hold
            self._persist_failures += 1
            self.log.event("persist_failed", error=f"{type(e).__name__}: {e}", n=self._persist_failures)
            if now - self._persist_fail_alert >= 60.0:
                self._persist_fail_alert = now
                self.log.alert("persist_failed", f"state.json could not be written ({type(e).__name__}: {e}); "
                                                 "trading continues, but a restart would lose recent state")

    def _compute_mtm(self, now: float) -> float | None:
        try:
            views = self.books.views(now)
            mids = {s: getattr(v, "mid", None) for s, v in views.items()}
            internal = self.positions.mtm(mids)
        except Exception as e:  # noqa: BLE001
            self._exception("mtm", e)
            internal = None
        if self._pnl_source in ("auto", "exchange"):
            ex = self._guard("positions.exchange_pnl", self.positions.exchange_pnl)
            if ex is not None:
                return float(ex)
        return None if internal is None else float(internal)

    def _tick(self, now: float) -> None:
        # 1. release snapshots, read the clock, update FeedHealth
        evs = self._guard("normalizer.poll", self.normalizer.poll, now, default=()) or ()
        if evs:
            self._apply_events(list(evs), now)
        cl = self._guard("clock.read", self.clock.read, now)
        if cl is not None:
            self._phase_cache = cl.phase
            self._paused_cache = bool(getattr(cl, "known", False) and not getattr(cl, "running", True))
        st = self._guard("normalizer.stats", self.normalizer.stats, default=None)
        if isinstance(st, dict):
            self._norm_stats = st
        feed = self._feed_health(now)
        # 2. feed down
        self._feed_down(feed, cl, now)
        # 3. unknown orders and MTM
        age = self._guard("reconciler.unknown_age", self.reconciler.unknown_age, now)
        if age is not None and age > self._unknown_hold_s:
            if not self._unknown_held:
                self._unknown_held = True
                self._guard("risk.hold", self.risk.hold, "unknown_orders", now, exempt=frozenset({"s1r3"}),
                            reason=f"order state unknown for {age:.1f} s")
        elif age is None and self._unknown_held:
            self._unknown_held = False
            self._guard("risk.release", self.risk.release, "unknown_orders", now)
        mtm = self._compute_mtm(now)
        self._mtm = mtm
        if mtm is not None:
            self._peak = mtm if self._peak is None else max(self._peak, mtm)
            self._guard("risk.on_mtm", self.risk.on_mtm, mtm, getattr(cl, "round", None), now)
        # 4-5. round-3 reservation and prewarm
        if cl is not None:
            self._round3(cl, now)
        # 6. law check horizon
        self._guard("law_check.on_tick", self.law_check.on_tick, self.books.views(now), now)
        # 7. unconfirmed season reset
        if self._pending_since is not None and now - self._pending_since > self._season_confirm_s:
            self._guard("normalizer.reject_season_reset", self.normalizer.reject_season_reset, now)
            self._pending_since = None
            self._pending_buf = None
            self.log.alert("season_reset_unconfirmed", "possible season change was not confirmed within "
                                                       f"{self._season_confirm_s:.0f} s: state kept; type "
                                                       "'season keep', 'season new' or 'r'")
        # reconcile: no completed cycle for too long (a slow or failing exchange) holds new risk (hardening)
        lr = self._last_recon_ok
        if (lr is not None and now - lr > self._recon_stale_hold_s and not self._recon_down
                and not self._recon_stale_alerted):
            self._recon_stale_alerted = True
            self._recon_down = True
            self._guard("risk.hold", self.risk.hold, "reconcile_down", now, sticky=True,
                        reason=f"no reconcile cycle completed for {now - lr:.1f} s")
            self.log.alert("reconcile_down", f"no reconcile cycle completed for {now - lr:.1f} s: holding new risk")
        # state.json soon after order changes, so a restart knows the latest orders (hardening)
        if (self._persist_dirty and self.run_dir and self.mode != "sim"
                and now - self._last_persist >= self._persist_debounce_s):
            self._job_persist(now)
        # housekeeping: restart auto-resume, engine health, request-owner pruning
        self._restart_resume(now)
        self._engine_watch(now)
        if len(self._req_owner) > 2000:
            old = [k for k, (_, t) in self._req_owner.items() if now - t > 120.0]
            for k in old:
                self._req_owner.pop(k, None)
        self._drain_round_events(now)
        # 8. decision cycle
        self._decide(Trigger.TICK, now)

    def _feed_down(self, feed: FeedHealth, cl: Any, now: float) -> None:
        if not feed.up:
            since = feed.down_since_mono if feed.down_since_mono is not None else now
            if now - since > self._feed_down_cancel_s and not self._feed_down_fired:
                self._feed_down_fired = True
                self._feed_down_active = True
                self._resume_snapshot = False
                self._resume_obs = 0
                reqs = self._guard("om.cancel_all", self.om.cancel_all, Priority.SNIPE, now, reason="feed_down")
                self._emit(reqs, now)
                self._guard("risk.hold", self.risk.hold, "feed_down", now, sticky=True,
                            reason=f"feed down {now - since:.1f} s")
                self.log.alert("feed_down", f"market data down for {now - since:.1f} s: cancelled all orders and "
                                            "holding new risk")
            return
        if self._feed_down_fired:
            self._feed_down_fired = False            # the episode is over; a new outage can fire again
        if self._feed_down_active:
            phase = getattr(cl, "phase", None)
            need_obs = self._resume_obs_n if phase == Phase.STORM else 0
            if self._resume_snapshot and self._resume_obs >= need_obs:
                self._feed_down_active = False
                self._guard("risk.release", self.risk.release, "feed_down", now)
                self.log.event("feed_resume", storm_obs=self._resume_obs, phase=str(phase))

    def _round3(self, cl: Any, now: float) -> None:
        if not getattr(cl, "known", False) or getattr(cl, "round", None) != 3:
            return
        g = self.cfg.get
        try:
            t_block = float(self.schedule.at(g("strategies.s1r3.block_from")))
            t_close = float(self.schedule.close)
            t_warm = float(self.schedule.at(g("strategies.s1r3.prewarm_at")))
        except Exception as e:  # noqa: BLE001
            self._exception("round3.schedule", e)
            return
        key = (self.epoch, 3)
        start = self._guard("clock.t_to_mono", self.clock.t_to_mono, t_block, now)
        end = self._guard("clock.t_to_mono", self.clock.t_to_mono, t_close, now)
        if start is not None and end is not None:
            cap = int(getattr(self.limiter, "cap", 0) or 0)
            r = self._resv
            if r is None or r[0] != key or abs(r[1] - start) > 0.5 or r[3] != cap:
                if r is not None:
                    self._guard("limiter.clear_reservations", self.limiter.clear_reservations)
                self._guard("limiter.reserve", self.limiter.reserve, start, end,
                            {Priority.TAKE: cap, Priority.HEDGE: cap})
                self._resv = (key, float(start), float(end), cap)
                self.log.event("reservation", start=start, end=end, cap=cap, moved=r is not None)
        t = getattr(cl, "t", None)
        if t is not None and t_warm <= t < t_close and key not in self._prewarmed:
            self._prewarmed.add(key)
            self._control.append("prewarm_all")
            self.log.event("prewarm", t=t)

    def _restart_resume(self, now: float) -> None:
        if not self._restart_hold or not self._auto_resume:
            return
        if self._restart_cycles >= 1 and self._restart_snaps >= 1:
            self._restart_hold = False
            self._guard("risk.release", self.risk.release, "restart", now)
            self.log.event("restart_auto_resume", cycles=self._restart_cycles, snapshots=self._restart_snaps)

    def _engine_watch(self, now: float) -> None:
        h = self._guard("engine.health", self.engine.health, now)
        if h is None:
            return
        ms = getattr(h, "last_compute_ms", None)
        if (isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > self._slow_alert_ms
                and now - self._engine_slow_alert >= 60.0):
            self._engine_slow_alert = now
            self.log.alert("engine_slow", f"fair-value compute took {ms:.0f} ms: S3 skips while values are older "
                                          "than strategies.s3.max_engine_age_s", compute_ms=ms)
        alive = bool(getattr(h, "alive", True))
        if self._engine_alive is not None and alive != self._engine_alive:
            if not alive:
                self.log.alert("engine_down", f"fair-value engine down ({getattr(h, 'consecutive_errors', '?')} "
                                              "consecutive errors): S3 off, S1-r3 laws fallback; 'eng restart'")
            else:
                self.log.event("engine_up")
        self._engine_alive = alive

    # ------------------------------------------------------------------ commands
    def on_command(self, cmd: Command, now: float) -> None:
        """Operator, KILL-file, signal and scripted commands. Never raises; errors log ``command_error``."""
        self._now = now
        self._commands += 1
        if not isinstance(cmd, Command):
            self.log.event("command_error", error="not a Command", value=repr(cmd)[:100])
            return
        self.log.event("command", name=cmd.name, args=list(cmd.args), source=cmd.source)
        try:
            self._command(cmd, now)
        except Exception as e:  # noqa: BLE001
            self._exception(f"command:{cmd.name}", e)
            self.log.event("command_error", name=cmd.name, error=f"{type(e).__name__}: {e}")
            self._say(f"command {cmd.name} failed: {type(e).__name__}: {e}")

    def _cmd_error(self, cmd: Command, error: str) -> None:
        self.log.event("command_error", name=cmd.name, args=list(cmd.args), error=error)
        self._say(f"? {error}")

    def _command(self, cmd: Command, now: float) -> None:
        n, a = cmd.name, cmd.args
        if n in _console.CORE_COMMANDS:
            err = _console.validate(cmd, _symbols_of(self.case))
            if err:
                self._cmd_error(cmd, err)
                return
        if n == "k":
            self._kill(f"{cmd.source}: k", now)
        elif n == "cx":
            reqs = self.om.cancel_all(Priority.KILL, now, reason="operator_cx")
            self._emit(reqs, now)
            if reqs:
                self._say("cancel-all sent (KILL priority)")
            else:
                self._say("cancel-all QUEUED: KILL budget exhausted; it goes out as actions age out (use k to "
                          "freeze, or cancel all in the browser)")
                self.log.alert("cx_queued", "operator cancel-all queued: KILL budget exhausted")
        elif n == "p":
            self.risk.hold("operator_pause", now, sticky=True, reason=cmd.source)
            self._say("paused: no new risk")
        elif n == "r":
            for h in ("operator_pause", "drawdown", "restart", "feed_down", "auth", "season_suspect",
                      "comp_id_suspect"):
                self.risk.release(h, now)
            self._restart_hold = False
            self._feed_down_active = False
            self._say("resumed")
        elif n == "unfreeze-kill":
            self.risk.unkill(now)
            self._control.append("unfreeze")
            self.log.alert("unfreeze_kill", "kill switch released by the operator")
            self._say("kill released; executor unfrozen")
        elif n == "x":
            sym = a[0]
            self.risk.disable(sym, f"operator ({cmd.source})", now)
            reqs = self.om.cancel_all(Priority.SNIPE, now, symbol=sym, reason="operator_disable")
            self._emit(reqs, now)
            self._say(f"{sym} disabled (reducing orders only); its orders cancelled")
        elif n == "e":
            self.risk.enable(a[0])
            self._say(f"{a[0]} enabled")
        elif n in ("f", "m", "a"):
            self._adjust_cmd(n, a[0], a[1], now)
        elif n == "ov":
            self._override_cmd(a[0], a[1])
        elif n == "b":
            self.limiter.cap = int(a[0])
            self._say(f"bot action cap {self.limiter.cap}")
            self.log.event("cap", requested=int(a[0]), cap=self.limiter.cap)
        elif n == "caseok":
            self._caseok_cmd(a[0])
        elif n == "season":
            self._season_cmd(a[0], now)
        elif n == "eng":
            self._eng_restart(now)
        elif n in ("st", "pos", "ord", "ann", "holds", "help"):
            self._show(n, now)
        elif n == "q":
            self.quit_requested = True
            self.begin_shutdown(f"{cmd.source}: q", now)
            self._say("shutting down: kill sent, waiting for every order to be cancelled")
        elif n in _console.RUNNER_COMMANDS:
            self._decide(Trigger.COMMAND, now, command=cmd)
        else:
            self._cmd_error(cmd, f"unknown command {n!r} (type 'help')")

    def _kill(self, reason: str, now: float) -> None:
        if self.killed():
            # already killed: re-arm the follow-up, which keeps the follow-up gap and the KILL budget (hardening)
            rearm = getattr(self.om, "rearm_kill", None)
            reqs = list(self._guard("om.rearm_kill", rearm, now, default=[]) or []) if callable(rearm) else []
            self._control.append("freeze")
            self._emit(reqs, now)
            self.log.event("kill_repeat", reason=reason, sent=len(reqs))
            self._say("already KILLED: follow-up re-armed" + (" (cancel-all sent)" if reqs else ""))
            return
        self.risk.kill(reason, now)
        reqs = list(self.om.kill(now) or [])
        self._control.append("freeze")
        purged = [r for r in self._outbox if r.kind in _PLACE_KINDS]
        keep = [r for r in self._outbox if r.kind not in _PLACE_KINDS]
        self._outbox = []
        self._emit(reqs, now)
        self._outbox.extend(keep)
        self.log.alert("kill", f"KILL ({reason}): cancel-all sent, executor frozen", purged=len(purged))
        for r in purged:          # never handed out: answer them as purged, like the executor would
            self.on_api_result(ApiResult(req=r, status=None, data=None, parsed=None, error="purged",
                                         sent_mono=now, done_mono=now, sent=False), now)
        self._say("KILLED: cancel-all sent, executor frozen ('unfreeze-kill' to release)")

    def _adjust_cmd(self, n: str, sym: str, val: str, now: float) -> None:
        mult, add = self._adjust.get(sym, (1.0, 0.0))
        if n == "f":
            if val == "off":
                self._adjust.pop(sym, None)
                self.engine.set_adjustment(sym, mult=1.0, add=0.0)
                self.log.event("adjust", symbol=sym, off=True)
                self._say(f"{sym}: adjustment removed")
                return
            mult, add = 0.0, float(val)
        elif n == "m":
            mult = float(val)
        else:
            add = float(val)
        self._adjust[sym] = (mult, add)
        self.engine.set_adjustment(sym, mult=mult, add=add)
        self.log.event("adjust", symbol=sym, mult=mult, add=add)
        self._say(f"{sym}: value -> {mult:g} x engine + {add:g}")

    def _override_cmd(self, target: str, val: str) -> None:
        """``ov PARAM X|off``: patch one engine parameter for the rest of the season (announcements)."""
        path = _console.resolve_override(target)
        if path is None:                                 # validate() already refused it; keep the core safe anyway
            self._say(f"ov: unknown parameter {target!r}")
            return
        value = None if val.lower() == "off" else float(val)
        self.engine.override(path, value)
        self.log.event("override", key=path, value=value)
        self._say(f"{path} -> {'default' if value is None else f'{value:g}'} "
                  "(engine values update on the next compute; then 'ua SYM' to trade toward them, or 'u SYM')")

    def _caseok_cmd(self, arg: str) -> None:
        if arg == "timing":
            self._timing_ack = True
            self.log.event("caseok", what="timing")
            self._say("case timing difference acknowledged")
            return
        syms = set(self._case_diff_syms) if arg == "all" else {arg}
        self._caseok |= syms
        self._case_diff_syms -= syms
        self.log.event("caseok", what=arg, cleared=sorted(syms), remaining=sorted(self._case_diff_syms))
        self._say(f"case diff cleared for {sorted(syms) or '-'}; remaining {sorted(self._case_diff_syms) or '-'}")

    def _season_cmd(self, arg: str, now: float) -> None:
        if arg == "keep":
            if getattr(self.normalizer, "pending_reset", None) or self._pending_buf is not None:
                self.normalizer.reject_season_reset(now)
            self._pending_buf = None
            self._pending_since = None
            self.risk.release("season_suspect", now)
            self.log.event("season_keep")
            self._say("season kept")
            return
        if getattr(self.normalizer, "pending_reset", None):
            ev = self.normalizer.confirm_season_reset("operator", now)
        else:
            ev = None
        if ev is None:
            ev = self.normalizer.force_season_reset("operator", now)
        if ev is not None:
            self._season_change(ev, now)
            self._say(f"new season: epoch {self.epoch}")

    def _eng_restart(self, now: float) -> None:
        fac = self._fac.get("engine")
        if self.mode == "sim" or fac is None:
            self._cmd_error(Command("eng", ("restart",)), "eng restart is not available in this mode")
            return
        path = os.path.join(self.run_dir, self._engine_log) if self.run_dir else None
        self.log.alert("engine_restart", "rebuilding the fair-value engine from its log; values are stale meanwhile",
                       engine_log=self._engine_log)
        old = self.engine
        try:          # build the replacement first: a failed rebuild must leave the running engine (hardening)
            new = fac(cfg=self.cfg, case=self.case, timesrc=self.timesrc, log=self.log, persist_path=path,
                      adjust_dir=self.run_dir, run_id=self.run_id, restore=True, mode="thread")
        except Exception as e:  # noqa: BLE001
            self.log.alert("engine_restart_failed", f"engine rebuild failed ({type(e).__name__}: {e}); the "
                                                    "running engine is kept", engine_log=self._engine_log)
            self._say(f"engine restart FAILED ({type(e).__name__}): running engine kept")
            return
        self._guard("engine.close", old.close)
        self.engine = new
        if self._engine_listener is not None:
            new.set_listener(self._engine_listener)
        self._last_fv_version = None
        self.log.event("engine_restarted", engine_log=self._engine_log)
        self._say("engine restarted from its log")

    def _show(self, n: str, now: float) -> None:
        if n == "help":
            self._say(_console.HELP)
            return
        if n == "st":
            from ctcbot.app.status import format_status
            st = self.status(now)
            self.log.event("show", what="st", status=st)
            self._say(format_status(st))
        elif n == "pos":
            pos = {s: int(q) for s, q in dict(self.positions.pos).items() if q}
            info = {"positions": pos, "cash": getattr(self.positions, "cash", None), "mtm": self._mtm,
                    "limits": self.limits}
            self.log.event("show", what="pos", **info)
            self._say(f"pos {pos or 'flat'} cash {info['cash']} mtm {self._mtm}")
        elif n == "ord":
            rows = []
            for r in self._guard("store.live", self.store.live, default=[]) or []:
                rows.append(f"{r.local_id} {r.strategy} {r.symbol} {r.side} {r.remaining}/{r.qty}@{r.price} "
                            f"{r.state}{' dry' if r.dry else ''}")
            self.log.event("show", what="ord", orders=rows)
            self._say("\n".join(rows) if rows else "no live orders")
        elif n == "ann":
            anns = [f"r{m.get('round')} {m.get('time')} {m.get('symbols')} {m.get('headline')}"
                    for m in self._season.announcements]
            frozen = sorted(self._guard("runner.frozen", self.runner.frozen, default=frozenset()) or ())
            self.log.event("show", what="ann", announcements=anns, frozen=frozen)
            self._say(("\n".join(anns) if anns else "no announcements") + f"\nfrozen: {frozen or '-'}")
        elif n == "holds":
            holds = self._guard("risk.holds", self.risk.holds, now, default={}) or {}
            self.log.event("show", what="holds", holds=holds, disabled=sorted(self.risk.disabled()))
            self._say(f"holds {holds or '-'} disabled {sorted(self.risk.disabled()) or '-'}")

    # ------------------------------------------------------------------ status, report, state
    def status(self, now: float) -> dict:
        cl = self._guard("clock.read", self.clock.read, now)
        eng = self._guard("engine.health", self.engine.health, now)
        fv = self._guard("engine.latest", self.engine.latest)
        feed = self._feed_health(now)
        holds = self._guard("risk.holds", self.risk.holds, now, default={}) or {}
        try:
            used = int(self.limiter.used(now))
        except Exception:  # noqa: BLE001
            used = None
        unk = 0
        for r in self._guard("store.live", self.store.live, default=[]) or []:
            if str(getattr(r, "state", "")) in ("unknown", "cancel_unknown"):
                unk += 1
        phase = getattr(cl, "phase", None)
        return {
            "label": "LIVE" if self.live_label else "DRY", "mode": str(self._guard("risk.mode", lambda: self.risk.mode)),
            "killed": self.killed(), "epoch": self.epoch, "round": getattr(cl, "round", None),
            "phase": None if phase is None else str(getattr(phase, "value", phase)),
            "t": getattr(cl, "t", None), "quality": getattr(cl, "quality", None),
            "running": bool(getattr(cl, "running", False)), "conns_up": feed.connections_up,
            "conns": feed.connections, "frame_age": feed.last_frame_age_s, "feed_up": feed.up, "wm": feed.wm,
            "max_seq": feed.max_seq, "gaps": feed.seq_gaps, "dup": feed.dup_news,
            "storm_obs_age": feed.last_storm_obs_age_s,
            "eng_age": getattr(eng, "oldest_pending_age_s", None), "eng_ms": getattr(eng, "last_compute_ms", None),
            "eng_q": getattr(eng, "pending", None), "eng_alive": getattr(eng, "alive", None),
            "fv_version": getattr(fv, "version", None), "act_used": used,
            "act_cap": getattr(self.limiter, "cap", None), "rej": self._rejects, "unk": unk, "mtm": self._mtm,
            "peak": self._peak, "positions": {s: int(q) for s, q in dict(self.positions.pos).items() if q},
            "frozen": sorted(self._guard("runner.frozen", self.runner.frozen, default=frozenset()) or ()),
            "holds": sorted(holds), "disabled": sorted(self._guard("risk.disabled", self.risk.disabled,
                                                                   default=frozenset()) or ()),
            "pending_reset": getattr(self.normalizer, "pending_reset", None), "comp_id": self._comp_id,
        }

    def report(self) -> dict:
        now = self._now
        lim = self._guard("limiter.stats", self.limiter.stats, now, default={})
        return {
            "fills": self._n_fills, "fills_by_strategy": dict(self._fills_by_strategy),
            "actions": lim, "requests": dict(self._req_counts), "results": dict(self._result_counts),
            "rejects": self._rejects, "alerts": dict(self.log.alerts), "exceptions": self._exceptions,
            "holds": self._guard("risk.holds", self.risk.holds, now, default={}),
            "resets": {"confirmed": sum(self._resets.values()), "by_reason": dict(self._resets),
                       "provisional": self._resets_provisional, "merged": self._resets_merged},
            "relevant_snapshots": list(self._relevant_snaps),
            "rounds_started": sorted(self._rounds_started), "commands": self._commands,
            "decisions": self._decisions, "engine_submits": self._engine_submits, "dry_seen": self._dry_seen,
            "kill": self._guard("om.kill_status", self.om.kill_status, default={}),
        }

    def state_dict(self, now: float) -> dict:
        """The ``state.json`` content (DESIGN.md §4.D persist schema)."""
        wall = float(self.timesrc.wall())
        cl = self._guard("clock.read", self.clock.read, now)
        ledger = self._guard("limiter.ledger", self.limiter.ledger, default=[]) or []
        season = self._season.state()
        season.update({"case_diff": sorted(self._case_diff_syms), "caseok": sorted(self._caseok),
                       "timing_ack": self._timing_ack, "timing_diff": self._timing_diff})
        return {
            "saved_wall": wall, "run_id": self.run_id, "cfg_hash": getattr(self.cfg, "hash", None),
            "comp_id": self._comp_id, "season_epoch": self.epoch, "round": getattr(cl, "round", None),
            "dry_run": not self.live_label, "mode": self.mode,
            "positions": {s: int(q) for s, q in dict(self.positions.pos).items()},
            "cash": float(getattr(self.positions, "cash", 0.0) or 0.0), "round_peak_mtm": self._peak,
            "orders": self._guard("store.state_dict", self.store.state_dict),
            "om": self._guard("om.state_dict", self.om.state_dict),
            "risk": self._guard("risk.state_dict", self.risk.state_dict),
            "runner": self._guard("runner.state_dict", self.runner.state_dict),
            "law_check": self._guard("law_check.state_dict", self.law_check.state_dict),
            "limiter_ledger_wall": ledger_to_wall(ledger, now, wall),
            "engine_log": self._engine_log, "season": season,
            "adjust_console": {s: list(v) for s, v in self._adjust.items()},
        }

    def _restore(self, d: Mapping[str, Any], promotion: bool) -> None:
        """Restore a saved state (or promote it), DESIGN.md §4.D persist and core 'Promotion'."""
        self._comp_id = d.get("comp_id") or None
        if d.get("engine_log"):
            self._engine_log = os.path.basename(str(d["engine_log"]))
        sea = d.get("season") if isinstance(d.get("season"), Mapping) else {}
        self._season.load(sea)
        saved_ok = set(sea.get("caseok") or ())
        self._caseok = saved_ok
        self._case_diff_syms -= saved_ok
        if "caseok" not in sea and "case_diff" in sea:
            self._case_diff_syms &= set(sea.get("case_diff") or ())
        self._timing_ack = bool(sea.get("timing_ack", False))
        msgs = [m for m in (d.get("engine_messages") or ()) if isinstance(m, Mapping)]
        for m in msgs:
            if m.get("type") == "snapshot":
                self._season.on_snapshot_msg(m)
            else:
                self._season.on_news_msg(m)
        self._guard("coverage.rebuild", self.coverage.rebuild, msgs)
        if self.laws is not None:
            self._guard("laws.rebuild", self.laws.rebuild, msgs)
        for name, obj in (("orders", self.store), ("om", self.om)):
            if isinstance(d.get(name), Mapping):
                self._guard(f"{name}.load_state", obj.load_state, d[name])
        if isinstance(d.get("risk"), Mapping):
            self._guard("risk.load_state", self.risk.load_state, d["risk"])
        if isinstance(d.get("runner"), Mapping):
            self._guard("runner.load_state", self.runner.load_state, d["runner"])
        if isinstance(d.get("law_check"), Mapping):
            self._guard("law_check.load_state", self.law_check.load_state, d["law_check"])
        for s, v in dict(d.get("adjust_console") or {}).items():
            try:
                self._adjust[str(s)] = (float(v[0]), float(v[1]))
            except (TypeError, ValueError, IndexError):
                pass
        if promotion:
            dropped = self._guard("om.drop_dry", self.om.drop_dry, default=0)
            self._guard("runner.on_promotion", self.runner.on_promotion)
            self.log.event("promotion", dropped_dry=dropped, saved_dry_run=d.get("dry_run"))
        else:
            pos = d.get("positions") if isinstance(d.get("positions"), Mapping) else {}
            for s, q in pos.items():
                qi = _int(q)
                if qi is not None and s in self.positions.pos:
                    self.positions.pos[s] = qi
            cash = _num(d.get("cash"))
            if cash is not None:
                self.positions.cash = cash
            self._peak = _num(d.get("round_peak_mtm"))
            self._restore_ledger = list(d.get("limiter_ledger_wall") or [])
        self._restart_hold = True

        def later(now: float) -> None:
            self.risk.hold("restart", now, sticky=True, reason="restart" if not promotion else "promotion")
            if self._restore_ledger:
                ledger = ledger_from_wall(self._restore_ledger, now, float(self.timesrc.wall()))
                if ledger:
                    self.limiter.restore(ledger)
                self._restore_ledger = None
            if promotion:
                self.log.alert("h1_size_needed", "promoted from the other mode: set the H1 size with 'h1 X', then 'r'")
            self.log.alert("restart", "restored saved state: holding new risk until 'r'", promotion=promotion,
                           comp_id=self._comp_id, engine_log=self._engine_log)

        self._deferred.append(later)
