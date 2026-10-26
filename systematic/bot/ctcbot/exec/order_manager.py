"""Intents to exchange requests (DESIGN.md section 4.B ``exec/order_manager.py`` and section 5.3).

The ``OrderManager`` turns the strategies' intents into ``ApiRequest``s:

* ``TargetIntent`` -> one clip at a time per symbol, spaced by ``clip_interval_s``; the unfilled remainder
  of a clip is cancelled ``ttl_s`` after it was sent;
* ``RestIntent``   -> an idempotent "ensure": keep this much resting at this price under this key;
* ``CancelIntent`` -> per-order cancels or one ``CANCEL_ALL`` per symbol, never two cancels for one record.

Every request takes exactly one rate-limit ticket; a denied cancel stays queued and is retried at every
step. Every PLACE passes ``RiskManager.check`` first and is clamped to the case room, which counts the new
record at once. There is no dry-run switch here: a record learns it is dry from its PLACE result.

The kill path (``kill``, ``kill_followup``) has its own budget (the limiter's KILL overdraft) and sends
``CANCEL_ALL`` follow-ups until every record known at the kill is terminal and a clean reconcile cycle,
sent after the last kill cancel came back, shows no open order.

Hardening (DESIGN.md §13): a result that provably never reached the exchange (``sent=False``) refunds its
ticket, and the re-queued cancel backs off (0.25 s doubling to 1 s) instead of being re-sent by the step that
follows the result; ``step`` isolates every intent, so one bad intent cannot strand the requests already built;
intents are validated in ``set_intents``; a target can carry ``valid_until`` (no clip at or after the window's
end), ``slot_anchor`` (clips on a fixed grid, remainders cancelled at the next grid point) and
``first_clip_cost_bp`` (the expected cost of a clip with no same-side clip in the symbol within
``cost.fo_window_s``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from ctcbot.types import (TERMINAL_STATES, ApiKind, ApiRequest, ApiResult, CancelIntent, ClockReading, EventLog,
                          FeedHealth, Fill, Intent, OpenOrderRow, OrderAck, OrderRecord, OrderState, PriceQuality,
                          Priority, RestIntent, Side, TargetIntent, WorkingSummary)

from ctcbot.state.limits import live_contribution, resting_qty, room, strategy_room

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from ctcbot.case import CaseInfo
    from ctcbot.config import Config
    from ctcbot.exec.risk import RiskManager
    from ctcbot.ratelimit import ActionLimiter
    from ctcbot.state.books import BookStore
    from ctcbot.state.orders import OrderStore
    from ctcbot.state.positions import PositionBook

__all__ = ["OrderManager"]

S = OrderState
_CLIP_BLOCKING = frozenset({S.NEW, S.SENDING, S.OPEN, S.UNKNOWN, S.CANCEL_PENDING, S.CANCEL_UNKNOWN})
_CANCELLING = frozenset({S.CANCEL_PENDING, S.CANCEL_ACKED, S.CANCEL_UNKNOWN})
_CANCELLABLE = frozenset({S.OPEN, S.UNKNOWN, S.CANCEL_UNKNOWN})     # with an order id
_KILL_RETRY_STATES = frozenset({S.SENDING, S.UNKNOWN, S.CANCEL_UNKNOWN})
_FILLED_WORDS = frozenset({"filled", "fully_filled", "complete", "completed", "executed", "done"})
_LOG_EVERY_S = 10.0
_EXCESS_PERSIST_S = 1.0     # a rest excess must survive two reconcile cycles before it is cancelled
_RETRY_FIRST_S = 0.25       # backoff of a cancel that came back sent=False: 0.25 s, doubling ...
_RETRY_MAX_S = 1.0          # ... up to 1 s
_INFLIGHT_EXPIRE_S = 10.0   # positions' in-flight PLACE entries older than this are forgotten


def _backoff(attempts: int) -> float:
    return min(_RETRY_MAX_S, _RETRY_FIRST_S * (2.0 ** max(0, attempts - 1)))


def _finite_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _intent_problem(it: Any) -> str | None:
    """Why an intent is malformed (None when it is fine). Strategies are trusted code, but a bad field must cost
    one intent, never the step (hardening)."""
    if isinstance(it, TargetIntent):
        if not isinstance(it.symbol, str) or not isinstance(it.priority, Priority):
            return "symbol/priority"
        if isinstance(it.target, bool) or not isinstance(it.target, int):
            return "target"
        if isinstance(it.clip_qty, bool) or not isinstance(it.clip_qty, int) or it.clip_qty < 1:
            return "clip_qty"
        if not _finite_num(it.clip_interval_s) or it.clip_interval_s < 0:
            return "clip_interval_s"
        if not _finite_num(it.ttl_s) or it.ttl_s <= 0:
            return "ttl_s"
        for v in (it.limit_buy, it.limit_sell):
            if v is not None and (not _finite_num(v) or v <= 0):
                return "limit"
        for v in (it.valid_until, it.slot_anchor, it.expected_cost_bp, it.first_clip_cost_bp, it.ref_mid):
            if v is not None and not _finite_num(v):
                return "optional field"
        if not _finite_num(it.edge_usd_per_share):
            return "edge"
        return None
    if isinstance(it, RestIntent):
        if not isinstance(it.symbol, str) or not isinstance(it.key, str) or not it.key:
            return "symbol/key"
        if not isinstance(it.side, Side) or not isinstance(it.priority, Priority):
            return "side/priority"
        if not _finite_num(it.price) or it.price <= 0:
            return "price"
        if it.qty is not None and (isinstance(it.qty, bool) or not isinstance(it.qty, int) or it.qty < 0):
            return "qty"
        if not _finite_num(it.edge_usd):
            return "edge"
        if it.value_usd is not None and not _finite_num(it.value_usd):
            return "value"
        return None
    if isinstance(it, CancelIntent):
        if not isinstance(it.scope, str) or not isinstance(it.priority, Priority):
            return "scope/priority"
        return None
    return "type"


def _scaled_limits(case: "CaseInfo", cfg: "Config") -> dict[str, int]:
    """``L_s`` per symbol via ``ctcbot.case.strategy_limits`` (imported lazily; tests patch this helper)."""
    from ctcbot.case import strategy_limits
    return dict(strategy_limits(case, cfg))


def _g(cfg: "Config", key: str, default: Any) -> Any:
    try:
        v = cfg.get(key, default)
    except (KeyError, TypeError, AttributeError):
        return default
    return default if v is None else v


def _finite_pos(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) and x > 0 else None


def _int_or_none(x: Any) -> int | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return int(v) if math.isfinite(v) else None


@dataclass(slots=True)
class _CancelJob:
    kind: str                       # "order" | "symbol" | "all"
    priority: Priority              # cancel priority before the reservation raise
    reason: str
    local_id: str | None = None     # kind "order"
    symbol: str | None = None       # kind "symbol"
    raise_ok: bool = True           # False for cancel_all(): it uses the priority it is given
    created: float = 0.0
    not_before: float = -math.inf   # backoff after an unsent attempt (hardening)
    attempts: int = 0               # unsent attempts so far


@dataclass(slots=True)
class _Inflight:
    kind: ApiKind
    job: _CancelJob | None
    local_ids: list[str] = field(default_factory=list)     # records this request moved to CANCEL_PENDING
    prev: dict[str, OrderState] = field(default_factory=dict)
    kill: bool = False
    sent_mono: float = 0.0


class OrderManager:
    """Clips, rests, cancels and the kill, against one ``OrderStore`` (section 4.B)."""

    def __init__(self, cfg: "Config", case: "CaseInfo", store: "OrderStore", positions: "PositionBook",
                 books: "BookStore", risk: "RiskManager", limiter: "ActionLimiter", log: EventLog,
                 next_req_id: Callable[[], int]) -> None:
        self.cfg, self.case, self.store, self.positions, self.books = cfg, case, store, positions, books
        self.risk, self.limiter, self.log, self.next_req_id = risk, limiter, log, next_req_id
        self._case_l: dict[str, int] = {s: int(v) for s, v in dict(getattr(case, "limits", {}) or {}).items()}
        self._ls: dict[str, int] = _scaled_limits(case, cfg)
        self._symbols: tuple[str, ...] = tuple(getattr(case, "symbols", None) or self._case_l)
        self._target_ttl = float(_g(cfg, "orders.target_ttl_s", 0.6))
        self._rest_tol_frac = float(_g(cfg, "orders.rest_tolerance_frac", 0.05))
        self._rest_max = int(_g(cfg, "orders.rest_max_orders_per_key", 2))
        self._px_tol = float(_g(cfg, "orders.price_eq_tol", 0.005))
        self._decimals = int(_g(cfg, "exchange.price_decimals", 2))
        self._kill_gap = float(_g(cfg, "app.kill_followup_s", 0.25))
        self._overdraft = int(_g(cfg, "rate.kill_overdraft", 20))
        self._fo_window = float(_g(cfg, "cost.fo_window_s", 8.0))
        # the PositionBook's season generation this OM books into; after a season reset the core keeps the
        # old OM only to absorb late results, and those must not move the new season's positions
        self._pos_gen = getattr(positions, "generation", None)

        self._targets: dict[str, tuple[TargetIntent, float]] = {}
        self._rests: dict[str, RestIntent] = {}
        self._cancel_intents: list[CancelIntent] = []
        self._cancel_queue: list[_CancelJob] = []
        self._queued_ids: set[str] = set()
        self._inflight: dict[int, _Inflight] = {}
        self._last_clip_sent: dict[str, float] = {}
        self._last_clip_side: dict[tuple[str, Side], float] = {}
        self._last_place: dict[str, float] = {}
        self._last_cancel_all: dict[str | None, float] = {}
        self._excess_since: dict[str, float] = {}
        self._rl: dict[tuple[str, str], float] = {}
        self._seq = 0
        # kill state
        self._kill_set: dict[str, str] = {}
        self._kill_since: float | None = None
        self._kill_actions = 0
        self._kill_inflight: int | None = None
        self._kill_last_sent: float | None = None
        self._kill_wanted = False
        self._kill_result_mono: float | None = None
        self._kill_cycles = 0
        self._kill_complete = False
        self._kill_incomplete_alerted = False
        self.counters: dict[str, int] = {"places": 0, "cancels": 0, "cancel_alls": 0, "budget_denied": 0,
                                         "cancel_denied": 0, "risk_rejects": 0, "rejected": 0, "unknown": 0,
                                         "ack_fills": 0, "cancel_dedup": 0, "refunds": 0, "bad_intents": 0,
                                         "step_errors": 0, "expired_targets": 0, "rest_topups": 0}

    # ------------------------------------------------------------------------------------------ helpers
    def _rl_log(self, kind: str, sym: str, reason: str, now: float, **fields: Any) -> None:
        k = (sym, f"{kind}:{reason}")
        last = self._rl.get(k)
        if last is None or now - last >= _LOG_EVERY_S:
            self._rl[k] = now
            self.log.event(kind, symbol=sym, reason=reason, **fields)

    def _round_px(self, px: float, side: Side) -> float:
        """Buys round down and sells up to ``exchange.price_decimals`` (the RestClient does the same)."""
        f = 10 ** self._decimals
        v = math.floor(px * f + 1e-7) / f if side == Side.BUY else math.ceil(px * f - 1e-7) / f
        return round(v, self._decimals)

    @staticmethod
    def _marketable(side: Side, px: float, bid: float | None, ask: float | None) -> bool:
        if side == Side.BUY:
            return ask is None or px >= ask
        return bid is None or px <= bid

    def _cancel_prio(self, q: Priority) -> Priority:
        return Priority(min(int(q), int(Priority.HEDGE)))

    def _queue_job(self, job: _CancelJob) -> bool:
        """Queue a cancel job unless an equivalent one is queued already."""
        if job.kind == "order":
            if job.local_id in self._queued_ids:
                return False
            self._queued_ids.add(job.local_id)  # type: ignore[arg-type]
        else:
            for j in self._cancel_queue:
                if j.kind == job.kind and j.symbol == job.symbol:
                    j.priority = Priority(min(int(j.priority), int(job.priority)))
                    return False
        self._cancel_queue.append(job)
        return True

    def _requeue_ids(self) -> None:
        self._queued_ids = {j.local_id for j in self._cancel_queue if j.kind == "order" and j.local_id}

    @property
    def killed(self) -> bool:
        return bool(self.risk.killed())

    def _retired(self) -> bool:
        """True once the PositionBook was reset for a new season after this OM was built."""
        return self._pos_gen is not None and getattr(self.positions, "generation", self._pos_gen) != self._pos_gen

    # ------------------------------------------------------------------------------------------ intents
    def set_intents(self, intents: Sequence[Intent], now: float) -> None:
        """Targets replace the symbol's active target (stamped ``now``); rests and cancels wait for ``step``.

        While FROZEN nothing is queued: ``step`` only runs the kill, which cancels everything anyway, and
        intents kept from that time would be stale (and pile up) by the ``unfreeze-kill``.
        """
        if self.killed:
            return
        for it in intents or ():
            why = _intent_problem(it)
            if why is not None:
                self.counters["bad_intents"] += 1
                self._rl_log("om_bad_intent", str(getattr(it, "symbol", "?")), why, now,
                             type=type(it).__name__, strategy=str(getattr(it, "strategy", "?")))
                continue
            if isinstance(it, TargetIntent):
                if it.symbol not in self._case_l:
                    self._rl_log("om_skip", str(it.symbol), "unknown_symbol", now, strategy=it.strategy)
                    continue
                self._targets[it.symbol] = (it, now)
            elif isinstance(it, RestIntent):
                if it.symbol not in self._case_l:
                    self._rl_log("om_skip", str(it.symbol), "unknown_symbol", now, strategy=it.strategy)
                    continue
                self._rests.pop(it.key, None)
                self._rests[it.key] = it
            elif isinstance(it, CancelIntent):
                self._cancel_intents.append(it)
            else:
                self.log.event("om_bad_intent", type=type(it).__name__)

    # ------------------------------------------------------------------------------------------ step
    def step(self, now: float, clock: ClockReading, feed: FeedHealth) -> list[ApiRequest]:
        """Build this instant's requests: cancels first, then places sorted by priority (section 4.B)."""
        if self.killed:
            return self._kill_step(now)
        if self._kill_since is not None:     # the kill was released (unfreeze-kill): forget its state
            self._kill_since, self._kill_set, self._kill_inflight = None, {}, None
            self._kill_wanted = False
        reserved = bool(self.limiter.reserved(now))
        cancels: list[ApiRequest] = []
        places: list[ApiRequest] = []

        for rec in self.store.live():
            if rec.cancel_on_ack and rec.state == S.OPEN and rec.order_id:
                self._queue_job(_CancelJob("order", Priority.SNIPE, "cancel_on_ack", local_id=rec.local_id,
                                           created=now))
        expire = getattr(self.positions, "expire_inflight", None)
        if callable(expire):
            self._safe("positions.expire_inflight", expire, now, _INFLIGHT_EXPIRE_S)
        intents, self._cancel_intents = self._cancel_intents, []
        for ci in intents:
            self._safe("cancel_intent", self._resolve_cancel_intent, ci, now)
        for rec in self.store.live():
            if (rec.style == "clip" and rec.state == S.OPEN and rec.remaining > 0 and rec.ttl_s is not None
                    and rec.sent_mono is not None and now - rec.sent_mono >= rec.ttl_s
                    and (rec.acked_mono is not None or (rec.last_seen_open_mono is not None
                                                        and rec.last_seen_open_mono > rec.sent_mono))):
                self._queue_job(_CancelJob("order", self._cancel_prio(rec.priority), "ttl", local_id=rec.local_id,
                                           created=now))
        cancels += self._drain_cancels(now, reserved)
        c2, p2 = self._step_rests(now, clock, feed, reserved)
        cancels += c2
        places += p2
        places += self._step_targets(now, clock, feed)
        places.sort(key=lambda r: int(r.priority))
        return cancels + places

    def _safe(self, where: str, fn: Callable[..., Any], *args: Any) -> Any:
        """Run one per-intent step; an exception costs that intent only (logged), never the step."""
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001 - one bad intent must not strand the requests already built
            import traceback
            self.counters["step_errors"] += 1
            self.log.event("exception", where=f"om.{where}", error=f"{type(e).__name__}: {e}",
                           traceback="".join(traceback.format_exception(type(e), e, e.__traceback__))[-2000:])
            return None

    # ------------------------------------------------------------------------------------------ cancels
    def _resolve_cancel_intent(self, ci: CancelIntent, now: float) -> None:
        base = self._cancel_prio(ci.priority)
        reason = ci.reason or f"{ci.strategy}:{ci.scope}"
        if ci.scope in ("symbol", "all"):
            sym = ci.symbol if ci.scope == "symbol" else None
            if ci.scope == "symbol" and not ci.symbol:
                self.log.event("om_bad_intent", type="CancelIntent", why="scope symbol without symbol")
                return
            if self._cancel_all_redundant(sym):
                self.counters["cancel_dedup"] += 1
                return
            self._queue_job(_CancelJob("symbol" if sym else "all", base, reason, symbol=sym, created=now))
            return
        if ci.scope == "key":
            match: Callable[[OrderRecord], bool] = lambda r: r.key is not None and r.key == ci.key
        elif ci.scope == "key_prefix":
            match = lambda r: r.key is not None and ci.key is not None and r.key.startswith(ci.key)
        elif ci.scope == "strategy":
            match = lambda r: r.strategy == ci.strategy
        else:
            self.log.event("om_bad_intent", type="CancelIntent", why=f"scope {ci.scope!r}")
            return
        groups: dict[str, tuple[list[OrderRecord], list[OrderRecord]]] = {}
        for rec in self.store.live():
            if rec.state in (S.CANCEL_PENDING, S.CANCEL_ACKED) or rec.local_id in self._queued_ids:
                continue
            if ci.symbol and rec.symbol != ci.symbol:
                continue
            all_, in_ = groups.setdefault(rec.symbol, ([], []))
            all_.append(rec)
            if match(rec):
                in_.append(rec)
        for sym, (all_, in_) in groups.items():
            if not in_:
                continue
            if len(in_) == len(all_) and len(in_) >= 2:
                self._queue_job(_CancelJob("symbol", base, reason, symbol=sym, created=now))
                continue
            for rec in in_:
                if rec.order_id and rec.state in _CANCELLABLE:
                    self._queue_job(_CancelJob("order", base, reason, local_id=rec.local_id, created=now))
                else:
                    rec.cancel_on_ack = True     # SENDING, or UNKNOWN without an id: cancel once it is OPEN

    def _cancel_all_redundant(self, sym: str | None) -> bool:
        """A symbol/all CANCEL_ALL is redundant when nothing in scope is uncovered and none was placed since
        the last CANCEL_ALL of that scope (protects the budget from repeated identical CancelIntents)."""
        for rec in self.store.live(sym):
            if rec.state in _CANCELLABLE or (rec.state == S.SENDING and not rec.cancel_on_ack):
                return False
        last_c = max(self._last_cancel_all.get(sym, -math.inf), self._last_cancel_all.get(None, -math.inf))
        if sym is None:
            last_p = max(self._last_place.values(), default=-math.inf)
        else:
            last_p = self._last_place.get(sym, -math.inf)
        return last_c >= last_p and last_c > -math.inf

    def _drain_cancels(self, now: float, reserved: bool, jobs: list[_CancelJob] | None = None) -> list[ApiRequest]:
        """Emit queued cancel jobs (or only ``jobs``) that get a ticket; denied jobs stay queued."""
        if jobs is None:
            todo, keep = self._cancel_queue, []
        else:
            for j in jobs:
                if j.kind == "order":
                    self._queued_ids.add(j.local_id)  # type: ignore[arg-type]
            todo, keep = jobs, [j for j in self._cancel_queue if all(j is not x for x in jobs)]
        todo = sorted(todo, key=lambda j: (int(j.priority), j.created))
        out: list[ApiRequest] = []
        for job in todo:
            if job.not_before > now:
                keep.append(job)
                continue
            ready = self._job_ready(job)
            if not ready:
                continue
            p = job.priority
            if reserved and job.raise_ok and p == Priority.HEDGE:
                p = Priority.SNIPE
            tickets = self.limiter.try_acquire(p, 1, now, f"cancel:{job.reason}")
            if not tickets:
                self.counters["cancel_denied"] += 1
                keep.append(job)
                continue
            out.append(self._emit_cancel(job, p, tickets[0], now))
        self._cancel_queue = keep
        self._requeue_ids()
        return out

    def _job_ready(self, job: _CancelJob) -> bool:
        if job.kind != "order":
            return True
        rec = self.store.by_local(job.local_id)
        if rec is None or rec.state in TERMINAL_STATES or rec.state in (S.CANCEL_PENDING, S.CANCEL_ACKED):
            return False
        if rec.state in _CANCELLABLE and rec.order_id:
            return True
        rec.cancel_on_ack = True
        return False

    def _emit_cancel(self, job: _CancelJob, p: Priority, ticket: Any, now: float, kill: bool = False) -> ApiRequest:
        rid = self.next_req_id()
        if job.kind == "order":
            rec = self.store.by_local(job.local_id)
            assert rec is not None
            inf = _Inflight(kind=ApiKind.CANCEL, job=job, sent_mono=now)
            inf.local_ids.append(rec.local_id)
            inf.prev[rec.local_id] = rec.state
            self.store.transition(rec, S.CANCEL_PENDING, now, cancel_sent_mono=now, cancel_on_ack=True)
            req = ApiRequest(req_id=rid, kind=ApiKind.CANCEL, priority=p, symbol=rec.symbol, side=rec.side,
                             order_id=rec.order_id, local_id=rec.local_id, ticket=ticket, created_mono=now)
            self.counters["cancels"] += 1
        else:
            sym = job.symbol
            inf = _Inflight(kind=ApiKind.CANCEL_ALL, job=job, kill=kill, sent_mono=now)
            for rec in self.store.live(sym):
                if rec.state in _CANCELLABLE and rec.order_id:
                    inf.local_ids.append(rec.local_id)
                    inf.prev[rec.local_id] = rec.state
                    self.store.transition(rec, S.CANCEL_PENDING, now, cancel_sent_mono=now, cancel_on_ack=True)
                elif rec.state in (S.SENDING, S.NEW) or (rec.state in _CANCELLABLE and not rec.order_id):
                    rec.cancel_on_ack = True
            req = ApiRequest(req_id=rid, kind=ApiKind.CANCEL_ALL, priority=p, symbol=sym, ticket=ticket,
                             created_mono=now)
            self._last_cancel_all[sym] = now
            self.counters["cancel_alls"] += 1
        self._inflight[rid] = inf
        self.log.event("order_cancel", req_id=rid, api_kind=str(req.kind), symbol=req.symbol, priority=int(p),
                       local_ids=list(inf.local_ids), reason=job.reason, kill=kill)
        return req

    def cancel_all(self, priority: Priority, now: float, symbol: str | None = None, reason: str = ""
                   ) -> list[ApiRequest]:
        """One CANCEL_ALL (for ``symbol`` or everything) at exactly ``priority``; queued if the budget denies."""
        job = _CancelJob("symbol" if symbol else "all", Priority(priority), reason or "cancel_all", symbol=symbol,
                         raise_ok=False, created=now)
        return self._drain_cancels(now, reserved=False, jobs=[job])

    # ------------------------------------------------------------------------------------------ places
    def _try_place(self, *, strategy: str, key: str | None, style: str, symbol: str, side: Side, qty: int,
                   price: float, priority: Priority, now: float, clock: ClockReading, feed: FeedHealth,
                   ttl_s: float | None = None, reduce_only: bool = False, ref_mid: float | None = None,
                   expected_cost_bp: float | None = None, edge: float = 0.0, reason: str = "") -> ApiRequest | None:
        book = self.books.view(symbol, now)
        pos = int(self.positions.pos.get(symbol, 0))
        case_room = room(self._case_l.get(symbol, 0), pos, side, resting_qty(self.store, symbol, side))
        qty = min(int(qty), case_room)
        if qty < 1:
            self._rl_log("om_skip", symbol, "no_room", now, strategy=strategy, side=str(side))
            return None
        cand = OrderRecord(local_id="candidate", strategy=strategy, key=key, style=style, symbol=symbol, side=side,
                           qty=qty, price=price, priority=priority, ttl_s=ttl_s, reduce_only=reduce_only,
                           ref_mid=ref_mid, expected_cost_bp=expected_cost_bp, edge_usd_per_share=edge,
                           created_mono=now, reason=reason)
        ok_qty, why = self.risk.check(cand, book, pos, case_room, clock, feed, now, live=self.store.live(symbol))
        if ok_qty <= 0:
            self.counters["risk_rejects"] += 1
            self._rl_log("risk_reject", symbol, why, now, strategy=strategy, side=str(side), qty=qty, price=price)
            return None
        tickets = self.limiter.try_acquire(priority, 1, now, f"place:{strategy}")
        if not tickets:
            self.counters["budget_denied"] += 1
            self._rl_log("om_skip", symbol, "budget_denied", now, strategy=strategy, priority=int(priority))
            return None
        try:
            rec = self.store.new(strategy=strategy, key=key, style=style, symbol=symbol, side=side,
                                 qty=int(ok_qty), price=price, priority=priority, ttl_s=ttl_s,
                                 reduce_only=reduce_only, ref_mid=ref_mid if ref_mid is not None else book.mid,
                                 expected_cost_bp=expected_cost_bp, edge_usd_per_share=edge, created_mono=now,
                                 reason=reason)
        except Exception:
            self._refund(tickets[0], now)
            raise
        try:
            req = ApiRequest(req_id=self.next_req_id(), kind=ApiKind.PLACE, priority=priority, symbol=symbol,
                             side=side, qty=int(ok_qty), price=price, order_type="limit", local_id=rec.local_id,
                             ticket=tickets[0], created_mono=now)
            self.store.transition(rec, S.SENDING, now, sent_mono=now)
        except Exception:
            # the request was never handed out: the record must not stay NEW/SENDING (it would block the symbol)
            if rec.state in (S.NEW, S.SENDING):
                self.store.transition(rec, S.REJECTED, now, reject_reason="om_error")
            self._refund(tickets[0], now)
            raise
        try:
            self.positions.note_place(symbol, sent=True, now=now)
        except TypeError:
            self.positions.note_place(symbol, sent=True)
        self._last_place[symbol] = now
        self.counters["places"] += 1
        self.log.event("order_place", req_id=req.req_id, local_id=rec.local_id, strategy=strategy, style=style,
                       key=key, symbol=symbol, side=str(side), qty=int(ok_qty), price=price, priority=int(priority),
                       check=why)
        return req

    def _rest_room(self, it: RestIntent, px: float) -> tuple[int, list[OrderRecord]]:
        """(desired live quantity, the key's live records) for one RestIntent."""
        sym, side = it.symbol, it.side
        key_recs = self.store.by_key(it.key)
        pos = int(self.positions.pos.get(sym, 0))
        own = sum(live_contribution(r) for r in key_recs if r.symbol == sym and r.side == side)
        excl = resting_qty(self.store, sym, side) - own
        room_s = strategy_room(self._case_l.get(sym, 0), self._ls.get(sym, 0), pos, side, excl)
        desired = room_s if it.qty is None else min(max(0, int(it.qty)), room_s)
        return desired, key_recs

    def _step_rests(self, now: float, clock: ClockReading, feed: FeedHealth, reserved: bool
                    ) -> tuple[list[ApiRequest], list[ApiRequest]]:
        if not self._rests:
            return [], []
        intents, self._rests = list(self._rests.values()), {}
        items = []
        for i, it in enumerate(intents):
            px_raw = _finite_pos(it.price)
            if px_raw is None:
                self._rl_log("om_skip", it.symbol, "bad_rest_price", now, key=it.key)
                continue
            px = self._round_px(px_raw, it.side)
            bv = self.books.view(it.symbol, now)
            mkt = self._marketable(it.side, px, bv.bid, bv.ask)
            desired, _ = self._rest_room(it, px)
            value = float(it.value_usd) if it.value_usd is not None else float(it.edge_usd or 0.0) * desired
            items.append(((int(it.priority), not mkt, -value, i), it, px))
        items.sort(key=lambda x: x[0])
        cancels: list[ApiRequest] = []
        places: list[ApiRequest] = []
        for _, it, px in items:
            res = self._safe("rest", self._do_rest, it, px, now, clock, feed, reserved)
            if res is None:
                continue
            c, ps = res
            cancels += c
            places += ps
        return cancels, places

    def _do_rest(self, it: RestIntent, px: float, now: float, clock: ClockReading, feed: FeedHealth,
                 reserved: bool) -> tuple[list[ApiRequest], list[ApiRequest]]:
        sym, side = it.symbol, it.side
        desired, key_recs = self._rest_room(it, px)
        prio = self._cancel_prio(it.priority)
        active = [r for r in key_recs if r.state not in _CANCELLING and r.local_id not in self._queued_ids]
        wrong = [r for r in active if r.symbol != sym or r.side != side or abs(r.price - px) >= self._px_tol]
        at_px = [r for r in active if r not in wrong]
        live_at_px = sum(live_contribution(r) for r in at_px)
        tol_q = max(1.0, self._rest_tol_frac * desired)
        jobs: list[_CancelJob] = []
        for r in wrong:
            if r.order_id and r.state in _CANCELLABLE:
                jobs.append(_CancelJob("order", prio, f"{it.key}:reprice", local_id=r.local_id, created=now))
            else:
                r.cancel_on_ack = True
        if live_at_px > desired + tol_q:
            t0 = self._excess_since.setdefault(it.key, now)
            if now - t0 >= _EXCESS_PERSIST_S:
                for r in sorted(at_px, key=lambda r: (live_contribution(r), r.created_mono)):
                    if live_at_px <= desired + tol_q:
                        break
                    if r.order_id and r.state in _CANCELLABLE:
                        jobs.append(_CancelJob("order", prio, f"{it.key}:excess", local_id=r.local_id, created=now))
                        live_at_px -= live_contribution(r)
                self._excess_since.pop(it.key, None)
        else:
            self._excess_since.pop(it.key, None)
        cancels = self._drain_cancels(now, reserved, jobs=jobs) if jobs else []
        if live_at_px >= desired - tol_q:
            return cancels, []
        n_live = sum(1 for r in key_recs if r.state != S.CANCEL_ACKED)
        if n_live >= self._rest_max:
            self._rl_log("om_skip", sym, "rest_max_orders", now, key=it.key, n=n_live)
            return cancels, []
        pos = int(self.positions.pos.get(sym, 0))
        room_now = strategy_room(self._case_l.get(sym, 0), self._ls.get(sym, 0), pos, side,
                                 resting_qty(self.store, sym, side))
        q = min(int(desired - live_at_px), room_now)
        if q < 1:
            return cancels, []
        bv = self.books.view(sym, now)
        req = self._try_place(strategy=it.strategy, key=it.key, style="rest", symbol=sym, side=side, qty=q, price=px,
                              priority=it.priority, now=now, clock=clock, feed=feed, ref_mid=bv.mid,
                              edge=float(it.edge_usd or 0.0), reason=it.reason)
        return cancels, ([req] if req is not None else [])

    def _step_targets(self, now: float, clock: ClockReading, feed: FeedHealth) -> list[ApiRequest]:
        items = []
        for sym in list(self._targets):
            ti, stamp = self._targets[sym]
            if now - stamp > self._target_ttl:
                del self._targets[sym]
                continue
            if ti.valid_until is not None and now >= ti.valid_until - 1e-9:
                del self._targets[sym]          # the strategy's window is over: no clip at or after its end
                self.counters["expired_targets"] += 1
                continue
            self._seq += 1
            items.append(((int(ti.priority), -float(ti.edge_usd_per_share or 0.0) * int(ti.clip_qty), self._seq), ti))
        items.sort(key=lambda x: x[0])
        out: list[ApiRequest] = []
        for _, ti in items:
            req = self._safe("target", self._do_target, ti, now, clock, feed)
            if req is not None:
                out.append(req)
        return out

    @staticmethod
    def _slot(ti: TargetIntent, t: float) -> int:
        """Index of the clip slot containing ``t`` on the grid ``slot_anchor + k * clip_interval_s`` (a decision
        up to 0.1 s before a grid point counts in the next slot, so clock jitter never skips a slot)."""
        iv = max(1e-6, float(ti.clip_interval_s))
        return int(math.floor((t - float(ti.slot_anchor) + 0.1) / iv))

    def _do_target(self, ti: TargetIntent, now: float, clock: ClockReading, feed: FeedHealth) -> ApiRequest | None:
        sym = ti.symbol
        if any(r.style == "clip" and r.state in _CLIP_BLOCKING for r in self.store.live(sym)):
            return None
        last = self._last_clip_sent.get(sym)
        if ti.slot_anchor is not None:
            if last is not None and self._slot(ti, now) <= self._slot(ti, last):
                return None
        elif last is not None and now - last < float(ti.clip_interval_s):
            return None
        ls = int(self._ls.get(sym, 0))
        target = max(-ls, min(ls, int(ti.target)))
        if target != int(ti.target):
            self._rl_log("om_clamp", sym, "target_beyond_Ls", now, strategy=ti.strategy, target=ti.target, ls=ls)
        w = self.store.working_summary(sym)
        exposure = int(self.positions.pos.get(sym, 0)) + w.clip_buy_qty - w.clip_sell_qty
        need = target - exposure
        if ti.reduce_only:
            if exposure > 0:
                need = max(min(need, 0), -exposure)
            elif exposure < 0:
                need = min(max(need, 0), -exposure)
            else:
                need = 0
        if need == 0:
            return None
        side = Side.BUY if need > 0 else Side.SELL
        limit = _finite_pos(ti.limit_buy if side == Side.BUY else ti.limit_sell)
        if limit is None:
            return None
        qty = min(abs(need), int(ti.clip_qty))
        if qty < 1:
            return None
        px = self._round_px(limit, side)
        ttl = float(ti.ttl_s)
        if ti.slot_anchor is not None:
            # the remainder is cancelled at the next grid point, so a late (latency) clip does not push the
            # following ones later and later (hardening; at latency 0 this is exactly ttl_s)
            iv = float(ti.clip_interval_s)
            boundary = float(ti.slot_anchor) + (self._slot(ti, now) + 1) * iv
            ttl = min(ttl, max(0.25, boundary - now))
        exp_cost = ti.expected_cost_bp
        if ti.first_clip_cost_bp is not None:
            prev = self._last_clip_side.get((sym, side))
            if prev is None or now - prev > self._fo_window:
                exp_cost = ti.first_clip_cost_bp      # no own same-side clip within the follow-on window
        req = self._try_place(strategy=ti.strategy, key=None, style="clip", symbol=sym, side=side, qty=qty, price=px,
                              priority=ti.priority, now=now, clock=clock, feed=feed, ttl_s=ttl,
                              reduce_only=bool(ti.reduce_only), ref_mid=ti.ref_mid,
                              expected_cost_bp=exp_cost, edge=float(ti.edge_usd_per_share or 0.0),
                              reason=ti.reason)
        if req is not None:
            self._last_clip_sent[sym] = now
            self._last_clip_side[(sym, side)] = now
        return req

    # ------------------------------------------------------------------------------------------ results
    def on_result(self, res: ApiResult, now: float) -> list[Fill]:
        """Apply one order-action result (section 4.B ack table); return the fills it revealed."""
        kind = res.req.kind
        if not res.sent and not res.dry_run and res.req.ticket is not None:
            self._refund(res.req.ticket, now)
        try:
            if kind == ApiKind.PLACE:
                return self._on_place(res, now)
            if kind == ApiKind.CANCEL:
                self._on_cancel(res, now)
            elif kind == ApiKind.CANCEL_ALL:
                self._on_cancel_all(res, now)
        except ValueError as e:     # an illegal transition means a logic bug: log it, keep the loop alive
            self.log.event("exception", where="om.on_result", error=str(e), api_kind=str(kind))
        return []

    def _on_place(self, res: ApiResult, now: float) -> list[Fill]:
        req = res.req
        if req.symbol:
            self.positions.note_place(req.symbol, sent=False)
        rec = self.store.by_local(req.local_id)
        if rec is None:
            self.log.event("om_result_orphan", req_id=req.req_id, local_id=req.local_id, status=res.status)
            return []
        if res.dry_run:
            ack = res.parsed if isinstance(res.parsed, OrderAck) else None
            oid = (ack.order_id if ack is not None else None) or f"dry-{req.req_id}"
            if rec.state == S.SENDING:
                self.store.transition(rec, S.OPEN, now, dry=True, order_id=oid, acked_mono=now)
            if self.killed:
                self._kill_wanted = True
            return []
        if not res.sent:
            if rec.state == S.SENDING:
                self.store.transition(rec, S.REJECTED, now, reject_reason=res.error or "not_sent")
                self.counters["rejected"] += 1
                self.log.event("order_rejected", local_id=rec.local_id, symbol=rec.symbol, reason=rec.reject_reason)
            return []
        if res.ok:
            ack = res.parsed if isinstance(res.parsed, OrderAck) else OrderAck(None, None, None, None, None)
            return self._apply_ack(rec, ack, now)
        st = res.status
        if st is None or st >= 500:
            if rec.state == S.SENDING:
                self.store.transition(rec, S.UNKNOWN, now)
                self.counters["unknown"] += 1
                self.log.event("order_unknown", local_id=rec.local_id, symbol=rec.symbol, status=st,
                               error=(res.error or "")[:200])
            return []
        try:
            body = str(res.data)[:200]
        except Exception:  # noqa: BLE001 - str() of untrusted data
            body = ""
        why = f"http {st}: {body or res.error or ''}"[:240]
        if rec.state == S.SENDING:
            self.store.transition(rec, S.REJECTED, now, reject_reason=why)
            self.counters["rejected"] += 1
            self._rl_log("order_rejected", rec.symbol, f"http_{st}", now, local_id=rec.local_id, detail=why)
        else:
            self.log.event("om_late_reject", local_id=rec.local_id, state=str(rec.state), detail=why)
        return []

    def _apply_ack(self, rec: OrderRecord, ack: OrderAck, now: float) -> list[Fill]:
        qty = int(rec.qty)
        status = (ack.status or "").strip().lower()
        filled_flag = status in _FILLED_WORDS
        a_filled = _int_or_none(ack.filled_qty)
        a_rem = _int_or_none(ack.remaining)
        if a_filled is None:
            a_filled = qty if (filled_flag and a_rem in (None, 0)) else (qty - a_rem if a_rem is not None else 0)
        a_filled = max(0, min(qty, a_filled))
        if a_rem is None:
            a_rem = 0 if filled_flag else qty - a_filled
        a_rem = max(0, min(a_rem, qty - a_filled))
        done = a_rem == 0 or filled_flag
        oid = ack.order_id or None
        fills: list[Fill] = []
        if rec.state in TERMINAL_STATES:
            if a_filled > rec.filled_qty:
                self.log.alert("late_ack_conflict", f"{rec.local_id} is {rec.state} but its ack reports "
                               f"{a_filled} filled", local_id=rec.local_id, state=str(rec.state), filled=a_filled)
            return fills
        if oid:
            self._adopt_ack_id(rec, oid)
        new_fill = a_filled - rec.filled_qty
        if new_fill > 0:
            px = _finite_pos(ack.avg_fill_price)
            quality = PriceQuality.EXACT if px is not None else PriceQuality.LIMIT_BOUND
            if px is None:
                px = rec.price
            f = Fill(local_id=rec.local_id, order_id=rec.order_id, strategy=rec.strategy, key=rec.key,
                     symbol=rec.symbol, side=rec.side, qty=new_fill, price=float(px), quality=quality, mono=now,
                     ref_mid=rec.ref_mid, expected_cost_bp=rec.expected_cost_bp, source="ack")
            if self._retired():
                self.log.event("fill_not_booked", why="earlier season", local_id=rec.local_id, symbol=rec.symbol,
                               side=str(rec.side), qty=new_fill, price=float(px))
            else:
                self.positions.apply_fill(f, update_pos=True)
            prev = rec.filled_qty
            rec.filled_qty = a_filled
            rec.avg_fill_price = float(px) if (rec.avg_fill_price is None or prev <= 0) else (
                (rec.avg_fill_price * prev + float(px) * new_fill) / a_filled)
            fills.append(f)
            self.counters["ack_fills"] += 1
            self.log.event("fill_booked", source="ack", local_id=rec.local_id, strategy=rec.strategy, symbol=rec.symbol,
                           side=str(rec.side), qty=new_fill, price=float(px), quality=str(quality))
        if rec.state in (S.SENDING, S.UNKNOWN):
            if done:
                if rec.filled_qty > 0 or filled_flag:
                    self.store.transition(rec, S.FILLED, now, acked_mono=now)
                elif rec.state == S.SENDING:
                    self.store.transition(rec, S.REJECTED, now, acked_mono=now, reject_reason=f"ack {status or 'done'}")
                else:
                    self.store.transition(rec, S.CANCELED, now, acked_mono=now)
            elif rec.order_id:
                self.store.transition(rec, S.OPEN, now, remaining=a_rem, acked_mono=now)
            else:
                if rec.state == S.SENDING:
                    self.store.transition(rec, S.UNKNOWN, now)
                    self.counters["unknown"] += 1
                self.log.alert("ack_no_id", f"{rec.local_id}: 2xx PLACE ack without an order id; reconcile will adopt",
                               local_id=rec.local_id, symbol=rec.symbol, status=status)
        else:   # already advanced by reconcile (adopted): apply only what is new
            if rec.acked_mono is None:
                rec.acked_mono = now
            rec.remaining = max(0, min(rec.remaining, a_rem))
            if rec.remaining == 0:
                self.store.transition(rec, S.FILLED, now)
        self.log.event("order_ack", local_id=rec.local_id, order_id=rec.order_id, state=str(rec.state),
                       filled=rec.filled_qty, remaining=rec.remaining, status=status)
        if rec.state == S.OPEN:
            if self.killed:
                self._kill_wanted = True
            elif rec.cancel_on_ack:
                self._queue_job(_CancelJob("order", Priority.SNIPE, "cancel_on_ack", local_id=rec.local_id,
                                           created=now))
        return fills

    def _refund(self, ticket: Any, now: float) -> None:
        fn = getattr(self.limiter, "refund", None)
        if callable(fn):
            try:
                if fn(ticket, now):
                    self.counters["refunds"] += 1
            except Exception:  # noqa: BLE001 - a refund is a courtesy to the budget
                pass

    def _adopt_ack_id(self, rec: OrderRecord, oid: str) -> None:
        if rec.order_id == oid:
            return
        if rec.order_id is None:
            other = self.store.by_order_id(oid)
            if other is not None and other is not rec:
                self.store.set_order_id(other, None)
            self.store.set_order_id(rec, oid)
            return
        # reconcile adopted a different row for this record: swap with the record that holds the ack's id
        other = self.store.by_order_id(oid)
        mine = rec.order_id
        self.log.alert("ack_id_mismatch", f"{rec.local_id} was adopted as {mine} but its ack says {oid}",
                       local_id=rec.local_id, adopted=mine, acked=oid)
        if other is not None and other is not rec:
            self.store.set_order_id(other, mine)
        self.store.set_order_id(rec, oid)

    def _revert(self, rec: OrderRecord, prev: OrderState | None, now: float) -> None:
        if rec.state != S.CANCEL_PENDING:
            return
        target = prev if prev in (S.OPEN, S.CANCEL_UNKNOWN) else (S.CANCEL_UNKNOWN if prev == S.UNKNOWN else S.OPEN)
        self.store.transition(rec, target, now)

    def _on_cancel(self, res: ApiResult, now: float) -> None:
        inf = self._inflight.pop(res.req.req_id, None)
        rec = self.store.by_local(res.req.local_id)
        if rec is None:
            return
        prev = inf.prev.get(rec.local_id) if inf is not None else None
        if rec.state != S.CANCEL_PENDING:
            return     # reconcile resolved it meanwhile
        if res.dry_run or (res.ok and rec.dry):
            self.store.transition(rec, S.CANCELED, now)
            return
        st = res.status
        if not res.sent or st == 429:
            self._revert(rec, prev, now)
            job = inf.job if inf is not None and inf.job is not None else _CancelJob(
                "order", Priority.SNIPE, "retry", local_id=rec.local_id, created=now)
            n = job.attempts + 1
            self._queue_job(_CancelJob("order", job.priority, job.reason, local_id=rec.local_id,
                                       raise_ok=job.raise_ok, created=now, not_before=now + _backoff(n),
                                       attempts=n))
            return
        if res.ok:
            self.store.transition(rec, S.CANCEL_ACKED, now, acked_mono=now)
            return
        if st == 404:
            self.log.event("cancel_404", local_id=rec.local_id, order_id=rec.order_id)
            return
        self.store.transition(rec, S.CANCEL_UNKNOWN, now)
        self.log.event("cancel_unknown", local_id=rec.local_id, status=st, error=(res.error or "")[:200])

    def _on_cancel_all(self, res: ApiResult, now: float) -> None:
        inf = self._inflight.pop(res.req.req_id, None)
        if inf is None:
            return
        if inf.kill and self._kill_inflight == res.req.req_id:
            self._kill_inflight = None
        recs = [r for r in (self.store.by_local(lid) for lid in inf.local_ids) if r is not None]
        st = res.status
        if res.dry_run:
            for r in recs:
                if r.state == S.CANCEL_PENDING:
                    self.store.transition(r, S.CANCELED, now)
            if inf.kill:
                self._kill_result_mono, self._kill_cycles = now, 0
            return
        if not res.sent or st == 429:
            for r in recs:
                self._revert(r, inf.prev.get(r.local_id), now)
            if inf.kill:
                self._kill_wanted = True
            elif inf.job is not None:
                j = inf.job
                n = j.attempts + 1
                self._queue_job(_CancelJob(j.kind, j.priority, j.reason, symbol=j.symbol, raise_ok=j.raise_ok,
                                           created=now, not_before=now + _backoff(n), attempts=n))
                for r in recs:      # the re-queued CANCEL_ALL covers them: no extra per-order cancels
                    if r.state in _CANCELLABLE:
                        r.cancel_on_ack = False
            return
        if res.ok:
            for r in recs:
                if r.state == S.CANCEL_PENDING:
                    if r.dry:
                        self.store.transition(r, S.CANCELED, now)
                    else:
                        self.store.transition(r, S.CANCEL_ACKED, now, acked_mono=now)
            if inf.kill:
                self._kill_result_mono, self._kill_cycles = now, 0
            return
        for r in recs:
            if r.state == S.CANCEL_PENDING:
                self.store.transition(r, S.CANCEL_UNKNOWN, now)
        if inf.kill:
            self._kill_wanted = True
        self.log.event("cancel_all_failed", req_id=res.req.req_id, symbol=res.req.symbol, status=st,
                       error=(res.error or "")[:200])

    # ------------------------------------------------------------------------------------------ kill
    def kill(self, now: float) -> list[ApiRequest]:
        """Record the kill set and emit one CANCEL_ALL at KILL priority (with the limiter's overdraft).

        While a kill is already in progress this only re-arms the follow-up (``rearm_kill``): a repeated kill
        never sends an unbudgeted CANCEL_ALL (hardening)."""
        if self._kill_since is not None and self.killed:
            return self.rearm_kill(now)
        self._kill_set = {r.local_id: str(r.state) for r in self.store.live()}
        self._kill_since = now
        self._kill_actions = 0
        self._kill_wanted = False
        self._kill_result_mono = None
        self._kill_cycles = 0
        self._kill_complete = False
        self._kill_incomplete_alerted = False
        self._targets.clear()
        self._rests.clear()
        self._cancel_intents.clear()
        self._cancel_queue.clear()
        self._queued_ids.clear()
        self.log.event("kill_start", live=len(self._kill_set), states=dict(self._kill_set))
        req = self._emit_kill(now, initial=True)
        return [req] if req is not None else []

    def _emit_kill(self, now: float, initial: bool) -> ApiRequest | None:
        tickets = self.limiter.try_acquire(Priority.KILL, 1, now, "kill")
        if not tickets and not initial:
            return None
        if not tickets:
            self.log.alert("kill_over_budget", "kill CANCEL_ALL sent without a ticket (budget exhausted)")
        job = _CancelJob("all", Priority.KILL, "kill", raise_ok=False, created=now)
        req = self._emit_cancel(job, Priority.KILL, tickets[0] if tickets else None, now, kill=True)
        self._kill_inflight = req.req_id
        self._kill_last_sent = now
        self._kill_actions += 1
        self._kill_result_mono = None
        self._kill_cycles = 0
        return req

    def _kill_step(self, now: float) -> list[ApiRequest]:
        if self._kill_since is None:     # killed elsewhere without om.kill: start the kill set now
            return self.kill(now)
        if self._kill_inflight is not None:
            return []
        retry = any((r := self.store.by_local(lid)) is not None and r.state in _KILL_RETRY_STATES
                    for lid in self._kill_set)
        if not (self._kill_wanted or retry):
            return []
        if self._kill_last_sent is not None and now - self._kill_last_sent < self._kill_gap:
            return []
        if int(self.limiter.used(now)) + 1 > self._kill_ceiling():
            if not self._kill_incomplete_alerted:
                self._kill_incomplete_alerted = True
                self.log.alert("kill_incomplete", "kill follow-ups stopped: KILL overdraft exhausted",
                               pending=self._kill_pending(), actions=self._kill_actions)
            return []
        req = self._emit_kill(now, initial=False)
        if req is None:
            return []
        self._kill_wanted = False
        return [req]

    def _kill_ceiling(self) -> int:
        c = getattr(self.limiter, "kill_ceiling", None)
        if isinstance(c, int) and not isinstance(c, bool):
            return c
        return int(self.limiter.cap) + self._overdraft

    def rearm_kill(self, now: float) -> list[ApiRequest]:
        """A repeated ``k`` while already killed: add new live records to the kill set and re-arm the follow-up,
        which keeps the follow-up gap and the KILL budget (hardening: repeated ``k`` must not bypass the budget)."""
        if self._kill_since is None or not self.killed:
            self._kill_since = None
            return self.kill(now)
        for r in self.store.live():
            self._kill_set.setdefault(r.local_id, str(r.state))
        self._kill_wanted = True
        self._kill_complete = False
        self.log.event("kill_rearm", live=len(self._kill_set))
        return self._kill_step(now)

    def _kill_pending(self) -> int:
        return sum(1 for lid in self._kill_set
                   if (r := self.store.by_local(lid)) is not None and r.state not in TERMINAL_STATES)

    def kill_followup(self, open_rows: Sequence[OpenOrderRow] | None, now: float) -> list[ApiRequest]:
        """Called when a reconcile cycle completes while FROZEN: follow up on open rows, check completion."""
        if not self.killed:
            return []
        if self._kill_result_mono is not None:
            self._kill_cycles += 1
        rows = list(open_rows or ())
        if rows:
            self._kill_wanted = True
        out = self._kill_step(now)
        if (not out and not rows and self._kill_inflight is None and self._kill_result_mono is not None
                and self._kill_cycles >= 2 and self._kill_pending() == 0 and not self._kill_complete):
            self._kill_complete = True
            self.log.event("kill_complete", elapsed_s=round(now - (self._kill_since or now), 3),
                           actions=self._kill_actions)
        return out

    def kill_status(self) -> dict:
        return {"pending": self._kill_pending(), "actions": self._kill_actions, "complete": self._kill_complete,
                "since": self._kill_since}

    # ------------------------------------------------------------------------------------------ misc
    def working(self) -> dict[str, WorkingSummary]:
        syms = list(self._symbols) + sorted({r.symbol for r in self.store.live()} - set(self._symbols))
        return {s: self.store.working_summary(s) for s in syms}

    def drop_dry(self) -> int:
        n = self.store.drop_dry()
        self._cancel_queue = [j for j in self._cancel_queue
                              if j.kind != "order" or self.store.by_local(j.local_id) is not None]
        self._requeue_ids()
        self._kill_set = {k: v for k, v in self._kill_set.items() if self.store.by_local(k) is not None}
        return n

    def stats(self) -> dict:
        return dict(self.counters, queued_cancels=len(self._cancel_queue), inflight_cancels=len(self._inflight),
                    targets=len(self._targets))

    def state_dict(self) -> dict:
        return {"counters": dict(self.counters),
                "kill": {"since": self._kill_since, "actions": self._kill_actions, "complete": self._kill_complete,
                         "set": dict(self._kill_set)}}

    def load_state(self, d: Mapping[str, Any]) -> None:
        if not isinstance(d, Mapping):
            return
        for k, v in (d.get("counters") or {}).items():
            if k in self.counters and isinstance(v, int):
                self.counters[k] = v
        k = d.get("kill") or {}
        if isinstance(k, Mapping) and self.risk.killed():
            self._kill_set = {str(a): str(b) for a, b in (k.get("set") or {}).items()}
            self._kill_since = k.get("since") if isinstance(k.get("since"), (int, float)) else None
            self._kill_actions = int(k.get("actions") or 0)
