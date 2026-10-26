"""Simulated exchange (``SimExchange``) and shell-mode market source (``SimSource``), DESIGN.md section 4.E.

``SimExchange`` implements ``ExchangeAPI`` over a ``ResearchMarket`` or a ``RecordedMarket``. It is the exchange:
it answers order actions whatever the bot's mode, because a replay or sim run has no ``LiveToken`` and the
``RestClient`` gate does not apply to it. It never touches the network.

**Exchange rules** (each breach is counted in ``results()["violations"]`` by type):

* ``market_closed``: a PLACE (or ETF create/redeem) outside a running round returns 409 ``{"detail": "market closed"}``.
* ``rate``: more than ``sim.team_actions_per_min`` (180) order actions in any 60 s returns 429; ``bot_cap`` counts
  every action after which a ``rate.window_s`` window (61 s by default; the window the bot's own limiter uses)
  holds more than ``rate.bot_cap_per_min`` (150); ``requests``: more than
  ``exchange.requests_per_s_team`` (600) requests in 1 s returns 429.
* ``limit_shrink``: a PLACE whose position + same-side resting + quantity exceeds the case limit L is shrunk to the
  room, or rejected with 400 when the room is 0. ``limit_room_cancel``: the part of an order that fills at once
  needs room, so a PLACE that is marketable against the current touch first cancels our resting same-side orders,
  worst price first, until it fits (the whole quantity counts as immediate: the synthetic and recorded books are
  deeper than any clip); a taker fill that still finds too little room at its match does the same and is then
  capped so the position never passes L.
* ``self_trade``: a new order crossing one of our resting opposite orders cancels the resting one.
* ``max_resting``: more than ``exchange.exchange_max_resting_per_symbol`` (200) resting orders in a symbol → 400.
  ``bad_qty`` / ``bad_price`` / ``bad_request``: quantity < 1 or not an int, a price with more than 6 decimals or not
  positive, an unknown symbol or side → 400.
* **Close** (``sim.cancel_at_close``, default true): at each round close every resting order is cancelled;
  ``close_all(t)`` does the same at the end of a run and keeps the market shut afterwards.

**Fills.** Research mode follows r_sim's ``sim()`` (see ``sim.fillmodel``): a PLACE arriving at round second ``ta``
is matched on consensus row ``j = floor(ta/2) + 1 + lat_rows (+ lat_rep_rows in the report windows)`` at
``T_r + 2j``, with the walk increment, follow-on, impact and replenishment, and the largest quantity whose marginal
price respects the limit; until then it rests (cancellable), and ``j >= 390`` never fills. The remainder rests and
fills at its own price when a later row's synthetic touch reaches it, for at most ``depth(ph) * L`` shares per row
and side. Recorded mode walks the latest recorded order book at ``t + sim.recorded_latency_s`` (levels at or through
the limit, depleted per frame and price), adds follow-on and impact at the mid when ``sim.recorded_follow_on`` is
on, caps at the limit, and fills resting orders against later frames' opposite touch. ``fills=False`` lets orders
rest without ever filling.

**Response schemas** (chosen to exercise group A's parsers): PLACE ``{"order_id", "status", "filled_quantity",
"remaining_quantity", "avg_fill_price"}``; GET /orders/open a list of ``{"order_id", "symbol", "side", "price",
"quantity", "remaining_quantity", "status"}``; GET /positions a list of ``{"symbol", "quantity", "avg_price", "mark",
"realized_pnl", "unrealized_pnl"}``; GET /competitions/time ``{"competition_id", "state", "fictional_time",
"elapsed_active_seconds"}``; DELETE ``{"status": "cancelled"}`` or 404. ``ApiResult.parsed`` carries the typed
values directly (``OrderAck``, ``list[OpenOrderRow]``, ``dict[str, PositionRow]``, ``ServerTime``).

**Time.** ``t`` is virtual time, set by the harness; for shell mode, ``clock`` may be a zero-argument callable
(for example a ``ScaledTime``'s ``mono``) that ``execute`` reads instead. Every call is serialised by a lock, so
``execute`` is thread-safe for a ``RestExecutor``.
"""
from __future__ import annotations

import heapq
import math
import threading
from collections import Counter, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from ctcbot.types import (ETFS, SYMBOLS, ApiKind, ApiRequest, ApiResult, BookLevel, EventLog, OpenOrderRow,
                          OrderAck, PositionRow, ServerTime, SourceFrame, SourceStats, TimeSource)

from sim.fillmodel import (FillHistory, FillParams, fill_price, follow_on_and_impact, limit_fill_qty, match_row,
                           replenish_room, side_enum, side_int, walk_increment)

if TYPE_CHECKING:  # pragma: no cover
    from ctcbot.config import Config
    from ctcbot.models.cost_model import CostModel

__all__ = ["SimExchange", "SimSource", "VIOLATION_TYPES"]

VIOLATION_TYPES: tuple[str, ...] = ("market_closed", "rate", "bot_cap", "requests", "limit_shrink",
                                    "limit_room_cancel", "self_trade", "max_resting", "bad_qty", "bad_price",
                                    "bad_request")

# internal event priorities at one instant
_P_OPEN, _P_FIRST, _P_SECOND, _P_CHECK, _P_CLOSE, _P_SEASON = 0, 1, 2, 3, 4, 5
_EPS = 1e-9


def _cfg(cfg: Any, key: str, default: Any) -> Any:
    try:
        v = cfg.get(key, default)
    except Exception:  # noqa: BLE001 - a partial config falls back to the documented default
        return default
    return default if v is None else v


@dataclass
class _Order:
    order_id: str
    symbol: str
    side: int
    qty: int
    price: float | None
    t_place: float
    season: str
    round: int
    local_id: str | None
    seq: int
    match_t: float = math.inf
    match_row: int | None = None
    phase: str = "storm"
    round_open: float = 0.0
    status: str = "pending"          # pending (awaiting its taker match) | resting | filled | cancelled
    filled: int = 0
    notional: float = 0.0
    rest_from_t: float = math.inf    # earliest market-data time at which it may fill passively
    cancel_reason: str | None = None
    done_t: float | None = None

    @property
    def remaining(self) -> int:
        return self.qty - self.filled

    @property
    def live(self) -> bool:
        return self.status in ("pending", "resting")

    @property
    def side_str(self) -> str:
        return "buy" if self.side > 0 else "sell"


class _Season:
    """Cash, positions and P&L of one season on the exchange (average-cost accounting)."""

    def __init__(self, label: str, comp_id: str) -> None:
        self.label = label
        self.comp_id = comp_id
        self.pos: dict[str, int] = {s: 0 for s in SYMBOLS}
        self.avg: dict[str, float] = {s: 0.0 for s in SYMBOLS}
        self.realized: dict[str, float] = {s: 0.0 for s in SYMBOLS}
        self.cash = 0.0
        self.round_values: dict[int, float] = {}
        self.round_pnl: dict[int, float] = {}
        self.final: float | None = None
        self.final_source: str | None = None
        self.cost_paid = 0.0
        self.fills = 0
        self.shares = 0
        self.checkpoints: dict[int, dict[str, int]] = {}

    def apply(self, sym: str, side: int, q: int, px: float) -> None:
        p0 = self.pos[sym]
        self.cash -= side * q * px
        if p0 == 0 or (p0 > 0) == (side > 0):
            self.avg[sym] = (self.avg[sym] * abs(p0) + px * q) / (abs(p0) + q)
        else:
            closing = min(q, abs(p0))
            self.realized[sym] += closing * (px - self.avg[sym]) * (1 if p0 > 0 else -1)
            if q > abs(p0):
                self.avg[sym] = px
            elif q == abs(p0):
                self.avg[sym] = 0.0
        self.pos[sym] = p0 + side * q
        self.fills += 1
        self.shares += q

    def value(self, marks: dict[str, float | None]) -> float:
        v = self.cash
        for s, p in self.pos.items():
            if p:
                m = marks.get(s)
                v += p * (m if m is not None else self.avg[s])
        return v


class SimExchange:
    """The simulated exchange (see the module docstring).

    ``SimExchange(market, cfg, cost, log, fills=True)``; ``cost`` is group C's ``CostModel`` (only research mode
    reads it: ``walk_bp``, ``depth``, ``rho``; the market uses ``half_spread_bp``).
    """

    def __init__(self, market: Any, cfg: "Config", cost: "CostModel | None", log: EventLog, fills: bool = True) -> None:
        self.market = market
        self.cfg = cfg
        self.cost = cost
        self.log = log
        self.fills_enabled = bool(fills)
        self.mode = str(getattr(market, "kind", "research"))
        if self.mode == "research" and cost is None:
            raise ValueError("research mode needs a CostModel")
        self.params = FillParams.from_config(cfg)
        self.schedule = market.schedule
        self.limits: dict[str, int] = dict(market.limits)
        self.baskets: dict[str, dict[str, int]] = dict(getattr(market, "baskets", {}) or {})
        self.team_cap = int(_cfg(cfg, "sim.team_actions_per_min", 180))
        self.bot_cap = int(_cfg(cfg, "rate.bot_cap_per_min", 150))
        self.bot_window_s = float(_cfg(cfg, "rate.window_s", 61.0))
        self.req_cap = int(_cfg(cfg, "exchange.requests_per_s_team", 600))
        self.max_resting_cap = int(_cfg(cfg, "exchange.exchange_max_resting_per_symbol", 200))
        self.cancel_at_close = bool(_cfg(cfg, "sim.cancel_at_close", True))
        self.allow_market = True          # the exchange accepts market orders; the bot's RestClient gates them
        try:
            self.block_early_tau = float(self.schedule.at(_cfg(cfg, "risk.block_marketable_before", ["open", 3.0])))
        except Exception:  # noqa: BLE001
            self.block_early_tau = 3.0
        try:
            self.checkpoint_tau = float(self.schedule.at(_cfg(cfg, "strategies.flatten.end", ["prelim", -2])))
        except Exception:  # noqa: BLE001
            self.checkpoint_tau = float(getattr(self.schedule, "prelim", 570.0)) - 2.0
        self.clock: Callable[[], float] | None = None
        self._closed_from: float | None = None   # set by close_all: the run is over, the market stays shut
        self.after_close_rejects = 0
        self._t = 0.0
        self._advanced_to = -math.inf
        self._lock = threading.RLock()
        # orders
        self._orders: dict[str, _Order] = {}
        self._live: dict[str, _Order] = {}
        self._n = 0
        self._seq = 0
        self._pending: list[tuple[float, int, str]] = []
        # rates and statistics
        self.violations: Counter[str] = Counter({k: 0 for k in VIOLATION_TYPES})
        self.http: Counter[str] = Counter()
        self.requests_by_kind: Counter[str] = Counter()
        self._act60: deque[float] = deque()
        self._act61: deque[float] = deque()
        self._actbot: deque[float] = deque()
        self._req1: deque[float] = deque()
        self.actions = 0
        self.actions_by_kind: Counter[str] = Counter()
        self.action_times: list[float] = []
        self.max_60 = 0
        self.max_61 = 0
        self.max_bot_window = 0
        self.max_req_1s = 0
        self.max_resting_seen: dict[str, int] = {s: 0 for s in SYMBOLS}
        self.early_marketable: list[dict] = []
        self.repl_hits = 0
        self.repl_lost = 0
        self.fills: list[dict] = []
        self.exchange_cancels: Counter[str] = Counter()
        # fill-model state
        self._hist = FillHistory()
        self._passive: dict[tuple[str, float, int], int] = {}
        self._depl: dict[tuple[int, int, float], int] = {}
        # seasons
        first = market.season_at(0.0) if market.rounds else "0"
        self._season = _Season(first, market.comp_id_at(0.0) if market.rounds else "sim")
        self._seasons: dict[str, _Season] = {first: self._season}
        # timeline
        self._events: list[tuple[float, int, int, str, Any]] = []
        for rs in market.rounds:
            self._events.append((rs.open_t, _P_OPEN, rs.index, "open", rs))
            self._events.append((rs.open_t + self.checkpoint_tau, _P_CHECK, rs.index, "check", rs))
            self._events.append((rs.close_t, _P_CLOSE, rs.index, "close", rs))
        for k, (t, label) in enumerate(market.season_switches()):
            self._events.append((t, _P_SEASON, k, "season", label))
        self._events.sort(key=lambda e: (e[0], e[1], e[2]))
        self._ev_ptr = 0
        if self.mode == "research":
            self._data = [(rs.open_t + 2.0 * i, rs, i) for rs in market.rounds for i in range(market.NROWS)]
        else:
            self._data = [(bf.t, sym, bf) for sym, lst in market.books.items() for bf in lst]
            self._data.sort(key=lambda x: (x[0], x[2].idx))
        self._data_ptr = 0

    # ------------------------------------------------------------------------------------------- time
    @property
    def t(self) -> float:
        return self._t

    @t.setter
    def t(self, value: float) -> None:
        self._t = float(value)

    def _now(self) -> float:
        if self.clock is not None:
            try:
                self._t = float(self.clock())
            except Exception:  # noqa: BLE001 - a failing clock keeps the last time
                pass
        return self._t

    def advance(self, t: float) -> None:
        """Process taker matches, resting-order crossings, closes and season switches due by virtual time t."""
        with self._lock:
            self._advance(float(t))
            self._t = max(self._t, float(t))

    def _advance(self, t: float) -> None:
        if t < self._advanced_to:
            return
        p_pend, p_data = (_P_FIRST, _P_SECOND) if self.mode == "research" else (_P_SECOND, _P_FIRST)
        while True:
            best: tuple[float, int] | None = None
            kind = None
            if self._pending:
                best, kind = (self._pending[0][0], p_pend), "pending"
            if self._data_ptr < len(self._data):
                c = (self._data[self._data_ptr][0], p_data)
                if best is None or c < best:
                    best, kind = c, "data"
            if self._ev_ptr < len(self._events):
                e = self._events[self._ev_ptr]
                c = (e[0], e[1])
                if best is None or c < best:
                    best, kind = c, "event"
            if best is None or best[0] > t + _EPS:
                break
            if kind == "pending":
                _, _, oid = heapq.heappop(self._pending)
                o = self._orders.get(oid)
                if o is not None and o.status == "pending":
                    self._match(o)
            elif kind == "data":
                item = self._data[self._data_ptr]
                self._data_ptr += 1
                self._on_data(item)
            else:
                e = self._events[self._ev_ptr]
                self._ev_ptr += 1
                self._on_event(e)
        self._advanced_to = t

    def _on_event(self, e: tuple[float, int, int, str, Any]) -> None:
        t, _, _, kind, payload = e
        if kind == "open":
            self._hist.reset()
        elif kind == "check":
            rs = payload
            if rs.season == self._season.label:
                self._season.checkpoints[rs.round] = {s: q for s, q in self._season.pos.items() if q}
        elif kind == "close":
            rs = payload
            if self.cancel_at_close:
                self._cancel_where(lambda o: True, t, "close")
            self._close_round(rs, t)
        elif kind == "season":
            self._switch_season(str(payload), t)

    def _round_marks(self, rs: Any, t: float) -> tuple[dict[str, float | None], str]:
        if rs.round >= 3:
            st = self.market.settlement(rs.season)
            if st is not None:
                return dict(st), "settlement"
        if self.mode == "research":
            return dict(self.market.round_marks(rs.round)), "last_row"
        return {s: self.market.mark(s, t) for s in SYMBOLS}, "last_mid"

    def _close_round(self, rs: Any, t: float) -> None:
        if rs.season != self._season.label:
            return
        marks, src = self._round_marks(rs, t)
        val = self._season.value(marks)
        prev = 0.0
        for r in sorted(self._season.round_values):
            if r < rs.round:
                prev = self._season.round_values[r]
        self._season.round_values[rs.round] = val
        self._season.round_pnl[rs.round] = val - prev
        if rs.round >= 3:
            self._season.final = val
            self._season.final_source = src

    def _switch_season(self, label: str, t: float) -> None:
        if label == self._season.label:
            return
        self._finalize(self._season, t)
        self._cancel_where(lambda o: True, t, "season_end")
        self._season = self._seasons.setdefault(label, _Season(label, self.market.comp_id_at(t)))
        self._hist.reset()

    def _finalize(self, ss: _Season, t: float) -> None:
        if ss.final is not None:
            return
        st = self.market.settlement(ss.label)
        if st is not None:
            ss.final, ss.final_source = ss.value(dict(st)), "settlement"
        else:
            ss.final, ss.final_source = ss.value({s: self.market.mark(s, t) for s in SYMBOLS}), "last_mid"

    # ------------------------------------------------------------------------------------------- matching
    def _match(self, o: _Order) -> None:
        if not self.fills_enabled:
            o.status = "resting"
            o.rest_from_t = math.inf
            return
        if self.mode == "research":
            self._match_research(o)
        else:
            self._match_recorded(o)
        if o.live and o.remaining > 0:
            if o.price is None:
                self._cancel(o, o.match_t, "market_unfilled")
            else:
                o.status = "resting"
        elif o.live and o.remaining <= 0:
            o.status = "filled"
            o.done_t = o.match_t
            self._live.pop(o.order_id, None)

    def _walk_fn(self, sym: str, ph: str, side: int) -> Callable[[float], float]:
        cost = self.cost
        se = side_enum(side)
        assert cost is not None
        return lambda x: float(cost.walk_bp(sym, ph, x, se))

    def _match_research(self, o: _Order) -> None:
        assert self.cost is not None
        j = int(o.match_row or 0)
        r, sym, s = o.round, o.symbol, o.side
        L = self.limits[sym]
        ph = o.phase
        row_px = self.market.row_px(r, j, sym)
        c = self._walk_fn(sym, ph, s)
        q = o.remaining
        if self.params.replenish:
            x_prev = self._hist.filled_side(sym, j - 1, s) / L
            room2 = replenish_room(L, float(self.cost.depth(sym, ph)), float(self.cost.rho(sym)), x_prev,
                                   self._hist.filled_side(sym, j, s))
            if room2 < q:
                self.repl_hits += 1
                self.repl_lost += q - max(0, room2)
                q = max(0, room2)
        if q > 0:
            x1 = self._hist.filled_side(sym, j, s) / L
            fo, im = follow_on_and_impact(self._hist.fills(sym), 2.0 * j, s, self.params.beta(sym), self.params)
            qs = limit_fill_qty(q, x1, L, c, s, row_px, fo, im, self.params.cost_mult, o.price,
                                self.params.marginal_h)
            qs = self._room_for_fill(o, qs, o.match_t)
            if qs > 0:
                cw = walk_increment(c, x1, x1 + qs / L)
                px = fill_price(row_px, s, self.params.cost_mult, cw, fo, im)
                if o.price is not None:
                    px = min(px, o.price) if s > 0 else max(px, o.price)
                self._fill(o, qs, px, o.match_t, row_px, "taker", row=j)
                self._hist.add(sym, j, 2.0 * j, s, qs, L)
        o.rest_from_t = o.round_open + 2.0 * (j + 1)

    def _match_recorded(self, o: _Order) -> None:
        sym, s = o.symbol, o.side
        ta = o.match_t
        bf = self.market.book_at(sym, ta)
        o.rest_from_t = ta + 1e-9
        if bf is None:
            return
        levels = bf.asks if s > 0 else bf.bids
        need = o.remaining
        taken: list[tuple[float, int]] = []
        for p, q in levels:
            if o.price is not None and ((s > 0 and p > o.price + 1e-12) or (s < 0 and p < o.price - 1e-12)):
                break
            key = (bf.idx, s, p)
            avail = q - self._depl.get(key, 0)
            if avail <= 0:
                continue
            take = min(need, avail)
            taken.append((p, take))
            need -= take
            if need <= 0:
                break
        qty = sum(x for _, x in taken)
        qty = self._room_for_fill(o, qty, ta)
        if qty <= 0:
            return
        left = qty
        notional = 0.0
        for p, take in taken:          # deplete only what the (possibly room-capped) fill used
            use = min(take, left)
            if use <= 0:
                break
            key = (bf.idx, s, p)
            self._depl[key] = self._depl.get(key, 0) + use
            notional += p * use
            left -= use
        vwap = notional / qty
        mid = bf.mid if bf.mid is not None else levels[0][0]
        fo = im = 0.0
        if self.params.recorded_follow_on:
            fo, im = follow_on_and_impact(self._hist.fills(sym), ta, s, self.params.beta(sym), self.params)
        px = vwap + s * mid * self.params.cost_mult * (fo + im) * 1e-4
        if o.price is not None:
            px = min(px, o.price) if s > 0 else max(px, o.price)
        self._fill(o, qty, px, ta, mid, "taker", row=None)
        self._hist.add(sym, -1, ta, s, qty, self.limits[sym])

    def _on_data(self, item: tuple) -> None:
        if not self.fills_enabled:
            return
        if self.mode == "research":
            t_row, rs, i = item
            self._passive_research(t_row, rs, i)
        else:
            t_b, sym, bf = item
            self._passive_recorded(t_b, sym, bf)

    def _resting_in(self, sym: str, t: float) -> list[_Order]:
        return [o for o in self._live.values() if o.symbol == sym and o.status == "resting"
                and o.price is not None and o.rest_from_t <= t + _EPS]

    def _passive_research(self, t_row: float, rs: Any, i: int) -> None:
        if not self._live or rs.season != self._season.label:
            return
        assert self.cost is not None
        ph = self.market.phase_of_row(i)
        for sym in SYMBOLS:
            cands = self._resting_in(sym, t_row)
            if not cands:
                continue
            bid, ask = self.market.touch(rs.round, i, sym)
            row_px = self.market.row_px(rs.round, i, sym)
            L = self.limits[sym]
            cap_total = int(float(self.cost.depth(sym, ph)) * L)
            for s in (1, -1):
                side_c = [o for o in cands if o.side == s and ((s > 0 and ask <= o.price + 1e-12)
                                                             or (s < 0 and bid >= o.price - 1e-12))]
                if not side_c:
                    continue
                side_c.sort(key=lambda o: (-s * o.price, o.seq))
                key = (sym, t_row, s)
                for o in side_c:
                    left = cap_total - self._passive.get(key, 0)
                    if left <= 0:
                        break
                    q = min(o.remaining, left)
                    self._passive[key] = self._passive.get(key, 0) + q
                    self._fill(o, q, float(o.price), t_row, row_px, "passive", row=i)
                    if o.remaining <= 0:
                        o.status = "filled"
                        o.done_t = t_row
                        self._live.pop(o.order_id, None)

    def _passive_recorded(self, t_b: float, sym: str, bf: Any) -> None:
        if not self._live:
            return
        cands = self._resting_in(sym, t_b - 2e-9)
        cands = [o for o in cands if o.rest_from_t < t_b]
        if not cands:
            return
        mid = bf.mid
        for s in (1, -1):
            touch = bf.asks[0] if (s > 0 and bf.asks) else (bf.bids[0] if (s < 0 and bf.bids) else None)
            if touch is None:
                continue
            p, q_lvl = touch
            side_c = [o for o in cands if o.side == s and ((s > 0 and p <= o.price + 1e-12)
                                                         or (s < 0 and p >= o.price - 1e-12))]
            side_c.sort(key=lambda o: (-s * o.price, o.seq))
            key = (bf.idx, s, p)
            for o in side_c:
                avail = q_lvl - self._depl.get(key, 0)
                if avail <= 0:
                    break
                q = min(o.remaining, avail)
                self._depl[key] = self._depl.get(key, 0) + q
                self._fill(o, q, float(o.price), t_b, mid if mid is not None else p, "passive", row=None)
                if o.remaining <= 0:
                    o.status = "filled"
                    o.done_t = t_b
                    self._live.pop(o.order_id, None)

    def _room_for_fill(self, o: _Order, q: int, t: float) -> int:
        """Apply the packet's rule for a fill that needs room: cancel our other resting same-side orders, worst
        price first, then cap so the position itself never passes the limit."""
        if q <= 0:
            return 0
        L = self.limits[o.symbol]
        s = o.side
        pos = self._season.pos[o.symbol]
        others = [x for x in self._live.values() if x.symbol == o.symbol and x.side == s and x is not o]
        excess = s * pos + sum(x.remaining for x in others) + o.remaining - L
        if excess > 0:
            others.sort(key=lambda x: (s * (x.price if x.price is not None else s * math.inf), -x.seq))
            for x in others:
                if excess <= 0:
                    break
                excess -= x.remaining
                self.violations["limit_room_cancel"] += 1
                self._cancel(x, t, "limit_room")
        return max(0, min(q, L - s * pos))

    def _fill(self, o: _Order, q: int, px: float, t: float, ref_px: float | None, kind: str,
              row: int | None) -> None:
        if q <= 0:
            return
        o.filled += q
        o.notional += q * px
        ss = self._season
        ss.apply(o.symbol, o.side, q, px)
        cost_frac = (o.side * (px - ref_px) / ref_px) if ref_px else 0.0
        if ref_px:
            ss.cost_paid += q * ref_px * cost_frac
        self.fills.append({"order_id": o.order_id, "local_id": o.local_id, "season": ss.label, "round": o.round,
                           "t": t, "tau": t - o.round_open, "symbol": o.symbol, "side": o.side_str, "qty": q,
                           "price": px, "ref_px": ref_px, "cost_frac": cost_frac, "kind": kind, "row": row})

    # ------------------------------------------------------------------------------------------- cancels
    def _cancel(self, o: _Order, t: float, reason: str) -> None:
        if not o.live:
            return
        o.status = "cancelled"
        o.cancel_reason = reason
        o.done_t = t
        self._live.pop(o.order_id, None)
        if reason not in ("client",):
            self.exchange_cancels[reason] += 1

    def _cancel_where(self, pred: Callable[[_Order], bool], t: float, reason: str) -> int:
        n = 0
        for o in list(self._live.values()):
            if pred(o):
                self._cancel(o, t, reason)
                n += 1
        return n

    def close_all(self, t: float) -> None:
        """End of a run at virtual time t: cancel every resting order and keep the market shut from then on.

        After ``close_all`` the server time reports "paused" and a PLACE or ETF action gets 409 "market closed"
        (counted in ``after_close_rejects``, not as a violation), so a run that ends mid-round (a recording) does
        not keep trading in the end-of-run reconcile window.
        """
        with self._lock:
            self._advance(float(t))
            self._t = max(self._t, float(t))
            self._cancel_where(lambda o: True, float(t), "close_all")
            self._closed_from = float(t) if self._closed_from is None else min(self._closed_from, float(t))

    def _round_open(self, now: float) -> Any:
        """The running round at ``now``, or None (also after ``close_all``)."""
        if self._closed_from is not None and now >= self._closed_from - _EPS:
            return None
        return self.market.round_at(now)

    # ------------------------------------------------------------------------------------------- API
    def execute(self, req: ApiRequest) -> ApiResult:
        """Answer one request at virtual time ``t`` (or ``clock()``), after advancing the market to it."""
        with self._lock:
            now = self._now()
            self._advance(now)
            try:
                status, data, parsed = self._dispatch(req, now)
            except Exception as e:  # noqa: BLE001 - a malformed request must never crash the simulator
                status, data, parsed = 400, {"detail": f"bad request: {type(e).__name__}"}, None
                self.violations["bad_request"] += 1
            self.http[str(status)] += 1
            error = None
            if not 200 <= status < 300:
                detail = data.get("detail") if isinstance(data, dict) else data
                error = f"HTTP {status}: {detail}"
            return ApiResult(req=req, status=status, data=data, parsed=parsed, error=error, sent_mono=now,
                             done_mono=now, dry_run=False, sent=True)

    def _rate_ok(self, req: ApiRequest, now: float) -> bool:
        self.requests_by_kind[str(req.kind)] += 1
        while self._req1 and self._req1[0] <= now - 1.0:
            self._req1.popleft()
        if len(self._req1) >= self.req_cap:
            self.violations["requests"] += 1
            return False
        self._req1.append(now)
        self.max_req_1s = max(self.max_req_1s, len(self._req1))
        if not req.is_order_action:
            return True
        while self._act60 and self._act60[0] <= now - 60.0:
            self._act60.popleft()
        if len(self._act60) >= self.team_cap:
            self.violations["rate"] += 1
            return False
        self._act60.append(now)
        while self._act61 and self._act61[0] <= now - 61.0:
            self._act61.popleft()
        self._act61.append(now)
        while self._actbot and self._actbot[0] <= now - self.bot_window_s:
            self._actbot.popleft()
        self._actbot.append(now)
        self.actions += 1
        self.actions_by_kind[str(req.kind)] += 1
        self.action_times.append(now)
        self.max_60 = max(self.max_60, len(self._act60))
        self.max_61 = max(self.max_61, len(self._act61))
        self.max_bot_window = max(self.max_bot_window, len(self._actbot))
        if len(self._actbot) > self.bot_cap:
            self.violations["bot_cap"] += 1
        return True

    def _dispatch(self, req: ApiRequest, now: float) -> tuple[int, Any, Any]:
        if not self._rate_ok(req, now):
            return 429, {"detail": "rate limit exceeded"}, None
        k = req.kind
        if k == ApiKind.PLACE:
            return self._place(req, now)
        if k == ApiKind.CANCEL:
            o = self._live.get(str(req.order_id)) if req.order_id is not None else None
            if o is None:
                return 404, {"detail": "order not found"}, None
            self._cancel(o, now, "client")
            d = {"status": "cancelled", "order_id": o.order_id}
            return 200, d, d
        if k == ApiKind.CANCEL_ALL:
            sym = req.symbol
            if sym is not None and sym not in self.limits:
                self.violations["bad_request"] += 1
                return 400, {"detail": "unknown symbol"}, None
            n = self._cancel_where(lambda o: sym is None or o.symbol == sym, now, "client")
            d = {"status": "cancelled", "cancelled": n}
            return 200, d, d
        if k == ApiKind.GET_OPEN:
            rows = [self._open_row(o) for o in sorted(self._live.values(), key=lambda o: o.seq)]
            parsed = [OpenOrderRow(r["order_id"], r["symbol"], side_enum(1 if r["side"] == "buy" else -1),
                                   r["price"], r["quantity"], r["remaining_quantity"], r["status"]) for r in rows]
            return 200, rows, parsed
        if k == ApiKind.GET_POSITIONS:
            rows = self._position_rows(now)
            parsed = {r["symbol"]: PositionRow(r["symbol"], r["quantity"], r["avg_price"], r["mark"],
                                               r["realized_pnl"], r["unrealized_pnl"]) for r in rows}
            return 200, rows, parsed
        if k == ApiKind.GET_TIME:
            d = self.market.server_time(now)
            if self._closed_from is not None and now >= self._closed_from - _EPS:
                d = dict(d, state="paused")
            parsed = ServerTime(d.get("competition_id"), d.get("state"), d.get("fictional_time"),
                                d.get("elapsed_active_seconds"))
            return 200, d, parsed
        if k == ApiKind.GET_BOOK:
            if req.symbol not in self.limits:
                self.violations["bad_request"] += 1
                return 404, {"detail": "unknown symbol"}, None
            d = self.market.book(str(req.symbol), now)
            parsed = (tuple(BookLevel(float(x["price"]), int(x["quantity"])) for x in d.get("bids", [])),
                      tuple(BookLevel(float(x["price"]), int(x["quantity"])) for x in d.get("asks", [])))
            return 200, d, parsed
        if k == ApiKind.GET_CASE:
            return 200, self.market.case_raw, self.market.case_raw
        if k in (ApiKind.ETF_CREATE, ApiKind.ETF_REDEEM):
            return self._etf(req, now)
        self.violations["bad_request"] += 1
        return 400, {"detail": f"unsupported request {k}"}, None

    def _open_row(self, o: _Order) -> dict:
        return {"order_id": o.order_id, "symbol": o.symbol, "side": o.side_str, "price": o.price,
                "quantity": o.qty, "remaining_quantity": o.remaining,
                "status": "partially_filled" if o.filled else "open"}

    def _marks(self, t: float) -> dict[str, float | None]:
        return {s: self.market.mark(s, t) for s in SYMBOLS}

    def _position_rows(self, t: float) -> list[dict]:
        ss = self._season
        marks = self._marks(t)
        out = []
        for s in SYMBOLS:
            q = ss.pos[s]
            m = marks.get(s)
            avg = ss.avg[s] if q else None
            unreal = (m - ss.avg[s]) * q if (q and m is not None) else 0.0
            out.append({"symbol": s, "quantity": q, "avg_price": avg, "mark": m,
                        "realized_pnl": ss.realized[s], "unrealized_pnl": unreal})
        return out

    def _closed_reply(self, now: float) -> tuple[int, Any, Any]:
        if self._closed_from is not None and now >= self._closed_from - _EPS:
            self.after_close_rejects += 1
        else:
            self.violations["market_closed"] += 1
        return 409, {"detail": "market closed"}, None

    def _place(self, req: ApiRequest, now: float) -> tuple[int, Any, Any]:
        rs = self._round_open(now)
        if rs is None:
            return self._closed_reply(now)
        sym = req.symbol
        if sym not in self.limits:
            self.violations["bad_request"] += 1
            return 400, {"detail": "unknown symbol"}, None
        try:
            s = side_int(req.side) if req.side is not None else 0
        except ValueError:
            s = 0
        if s == 0:
            self.violations["bad_request"] += 1
            return 400, {"detail": "bad side"}, None
        qty = req.qty
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
            self.violations["bad_qty"] += 1
            return 400, {"detail": "quantity must be an integer >= 1"}, None
        otype = str(req.order_type or "limit").lower()
        price = req.price
        if otype == "limit":
            if not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price) \
                    or price <= 0 or abs(round(price, 6) - price) > 1e-9:
                self.violations["bad_price"] += 1
                return 400, {"detail": "price must be positive with at most 6 decimals"}, None
            price = float(price)
        elif otype == "market":
            if price is not None:
                self.violations["bad_price"] += 1
                return 400, {"detail": "a market order has no price"}, None
        else:
            self.violations["bad_request"] += 1
            return 400, {"detail": f"bad order_type {otype}"}, None
        live_sym = [o for o in self._live.values() if o.symbol == sym]
        if len(live_sym) >= self.max_resting_cap:
            self.violations["max_resting"] += 1
            return 400, {"detail": "too many resting orders"}, None
        if otype == "market" and not self.allow_market:
            self.violations["bad_request"] += 1
            return 400, {"detail": "market orders are disabled"}, None
        # self-trade prevention: our new order cancels our resting orders it crosses
        for o in live_sym:
            if o.side == -s and o.price is not None and (price is None or (s > 0 and price >= o.price - 1e-12)
                                                          or (s < 0 and price <= o.price + 1e-12)):
                self.violations["self_trade"] += 1
                self._cancel(o, now, "self_trade")
        # position limit: position plus same-side resting orders. The part of a marketable order that fills at
        # once may claim room held by our resting same-side orders, which are cancelled worst price first; what
        # still does not fit is shrunk (or the order rejected when no room is left).
        L = self.limits[sym]
        same = [o for o in self._live.values() if o.symbol == sym and o.side == s]
        room = L - s * self._season.pos[sym] - sum(o.remaining for o in same)
        if qty > room and same and self._marketable_now(sym, s, price, now):
            same.sort(key=lambda o: (s * (o.price if o.price is not None else s * math.inf), -o.seq))
            for o in same:
                if qty <= room:
                    break
                room += o.remaining
                self.violations["limit_room_cancel"] += 1
                self._cancel(o, now, "limit_room")
        if qty > room:
            self.violations["limit_shrink"] += 1
            if room <= 0:
                return 400, {"detail": "position limit"}, None
            qty = int(room)
        self._n += 1
        self._seq += 1
        oid = f"o{self._n:06d}"
        o = _Order(order_id=oid, symbol=sym, side=s, qty=qty, price=price, t_place=now, season=rs.season,
                   round=rs.round, local_id=req.local_id, seq=self._seq, round_open=rs.open_t)
        tau = now - rs.open_t
        if self.mode == "research":
            j = match_row(tau, float(self.schedule.prelim), float(self.schedule.final), float(self.schedule.close),
                          self.params)
            o.match_row = j
            o.phase = self.schedule.cost_phase(tau + 2.0 * self.params.lat_rows)
            if j < self.market.NROWS:
                o.match_t = rs.open_t + 2.0 * j
            else:
                o.match_t = math.inf
                o.status = "resting"      # never matched: rests (cancellable) until the close
                o.rest_from_t = math.inf
        else:
            o.match_t = now + self.params.recorded_latency_s
        self._orders[oid] = o
        self._live[oid] = o
        if o.status == "pending":
            heapq.heappush(self._pending, (o.match_t, o.seq, oid))
        n_live = sum(1 for x in self._live.values() if x.symbol == sym)
        self.max_resting_seen[sym] = max(self.max_resting_seen[sym], n_live)
        if tau < self.block_early_tau and self._marketable_now(sym, s, price, now):
            self.early_marketable.append({"order_id": oid, "symbol": sym, "side": o.side_str, "price": price,
                                          "tau": tau, "season": rs.season, "round": rs.round})
        d = {"order_id": oid, "status": "open", "filled_quantity": 0, "remaining_quantity": qty,
             "avg_fill_price": None, "quantity": qty, "symbol": sym, "side": o.side_str, "price": price}
        return 200, d, OrderAck(oid, "open", 0, qty, None)

    def _marketable_now(self, sym: str, s: int, price: float | None, t: float) -> bool:
        if price is None:
            return True
        bk = self.market.book(sym, t)
        lv = bk.get("asks") if s > 0 else bk.get("bids")
        if not lv:
            return True
        best = float(lv[0]["price"])
        return price >= best - 1e-12 if s > 0 else price <= best + 1e-12

    def _etf(self, req: ApiRequest, now: float) -> tuple[int, Any, Any]:
        rs = self._round_open(now)
        if rs is None:
            return self._closed_reply(now)
        etf = req.symbol
        q = req.qty
        if etf not in ETFS or etf not in self.baskets:
            self.violations["bad_request"] += 1
            return 400, {"detail": "not an ETF"}, None
        if not isinstance(q, int) or isinstance(q, bool) or q < 1:
            self.violations["bad_qty"] += 1
            return 400, {"detail": "quantity must be an integer >= 1"}, None
        sign = 1 if req.kind == ApiKind.ETF_CREATE else -1
        legs = [(etf, sign * q)] + [(c, -sign * w * q) for c, w in self.baskets[etf].items()]
        for sym, dq in legs:
            s = 1 if dq > 0 else -1
            resting = sum(o.remaining for o in self._live.values() if o.symbol == sym and o.side == s)
            if s * self._season.pos[sym] + abs(dq) + resting > self.limits[sym]:
                return 400, {"detail": f"position limit ({sym})"}, None
        marks = self._marks(now)
        if any(marks.get(c) is None for c, _ in legs):
            return 400, {"detail": "no mark"}, None
        nav = sum(w * float(marks[c]) for c, w in self.baskets[etf].items())   # type: ignore[arg-type]
        for sym, dq in legs:
            px = nav if sym == etf else float(marks[sym])   # type: ignore[arg-type]
            self._season.apply(sym, 1 if dq > 0 else -1, abs(dq), px)
            self.fills.append({"order_id": None, "local_id": req.local_id, "season": self._season.label,
                               "round": rs.round, "t": now, "tau": now - rs.open_t, "symbol": sym,
                               "side": "buy" if dq > 0 else "sell", "qty": abs(dq), "price": px, "ref_px": px,
                               "cost_frac": 0.0, "kind": "etf", "row": None})
        d = {"status": "ok", "symbol": etf, "quantity": q, "action": "create" if sign > 0 else "redeem"}
        return 200, d, d

    # ------------------------------------------------------------------------------------------- results
    def positions(self) -> dict[str, int]:
        """Current exchange positions of the current season."""
        with self._lock:
            return dict(self._season.pos)

    def live_orders(self) -> list[dict]:
        with self._lock:
            return [self._open_row(o) for o in self._live.values()]

    def results(self) -> dict:
        """Per-season P&L, per-order fills, violations and rate statistics (JSON-serialisable)."""
        with self._lock:
            t = self._t
            seasons = {}
            for label, ss in self._seasons.items():
                if ss is self._season:
                    final = ss.final
                    src = ss.final_source
                    if final is None:
                        st = self.market.settlement(label)
                        complete = any(rs.season == label and rs.round == 3 and rs.close_t <= t
                                       for rs in self.market.rounds)
                        if st is not None and complete:
                            final, src = ss.value(dict(st)), "settlement"
                        else:
                            final, src = ss.value(self._marks(t)), "last_mid"
                else:
                    final, src = ss.final, ss.final_source
                seasons[label] = {
                    "season": label, "comp_id": ss.comp_id, "pnl": final, "marks": src,
                    "round_pnl": {str(r): v for r, v in sorted(ss.round_pnl.items())},
                    "round_value": {str(r): v for r, v in sorted(ss.round_values.items())},
                    "cash": ss.cash, "positions": {s: q for s, q in ss.pos.items() if q},
                    "realized": {s: v for s, v in ss.realized.items() if v},
                    "cost_paid": ss.cost_paid, "fills": ss.fills, "shares": ss.shares,
                    "checkpoints": {str(r): v for r, v in ss.checkpoints.items()},
                }
            orders = {oid: {"symbol": o.symbol, "side": o.side_str, "qty": o.qty, "price": o.price,
                            "t": o.t_place, "season": o.season, "round": o.round, "local_id": o.local_id,
                            "filled": o.filled, "avg_fill_price": (o.notional / o.filled) if o.filled else None,
                            "status": o.status, "cancel_reason": o.cancel_reason, "done_t": o.done_t}
                      for oid, o in self._orders.items()}
            return {
                "mode": self.mode, "fills_enabled": self.fills_enabled, "t": t,
                "seasons": seasons, "fills": list(self.fills), "orders": orders,
                "live_orders": len(self._live),
                "violations": dict(self.violations),
                "violations_total": sum(self.violations.values()),
                "http": dict(self.http),
                "rate": {"actions": self.actions, "by_kind": dict(self.actions_by_kind),
                         "max_60s": self.max_60, "max_61s": self.max_61, "max_requests_1s": self.max_req_1s,
                         "bot_window_s": self.bot_window_s, "max_bot_window": self.max_bot_window,
                         "http_429": int(self.http.get("429", 0)),
                         "requests_by_kind": dict(self.requests_by_kind)},
                "max_resting": dict(self.max_resting_seen),
                "early_marketable": list(self.early_marketable),
                "repl_hits": self.repl_hits, "repl_lost": self.repl_lost,
                "after_close_rejects": self.after_close_rejects,
                "exchange_cancels": dict(self.exchange_cancels),
            }


# ----------------------------------------------------------------------------------------------- shell source
class SimSource:
    """``MarketDataSource`` for shell-mode simulation: the market's frames paced on a ``TimeSource``.

    Each frame is emitted once ``timesrc.mono()`` reaches its virtual time, after ``exchange.advance`` to that
    time. If the exchange has no ``clock`` yet, the source sets it to ``timesrc.mono`` so that the executor's
    worker threads place orders at the current virtual time. After the market's last frame the source waits
    ``tail_s`` virtual seconds (so reconcile sees the end state) and returns, like ``RecordingSource``, so the
    shell stops by itself (integration change, DESIGN.md §12 item 6); ``tail_s=None`` waits for ``stop()``.
    """

    def __init__(self, market: Any, exchange: SimExchange, timesrc: TimeSource, name: str = "sim",
                 tail_s: float | None = 2.0) -> None:
        self.name = name
        self.market = market
        self.exchange = exchange
        self.timesrc = timesrc
        self.tail_s = tail_s
        self._stop = False
        self._frames = 0
        self._errors = 0
        self._ups = 0
        self._up = 0
        self._last: float | None = None
        if getattr(exchange, "clock", None) is None:
            exchange.clock = timesrc.mono

    async def run(self, emit: Callable[[SourceFrame], None]) -> None:
        import asyncio   # lazy: only shell mode needs it
        speed = float(getattr(self.timesrc, "speed", 1.0) or 1.0)
        epoch = "1"
        try:
            for mf in self.market.frames():
                if self._stop:
                    break
                while not self._stop:
                    now = self.timesrc.mono()
                    if now >= mf.t:
                        break
                    await asyncio.sleep(min(0.05, max(0.0005, (mf.t - now) / speed)))
                if self._stop:
                    break
                try:
                    self.exchange.advance(mf.t)
                except Exception:  # noqa: BLE001 - the source must not die on an exchange error
                    self._errors += 1
                epoch = mf.conn.rsplit("#", 1)[-1] if "#" in mf.conn else "1"
                if mf.control == "up":
                    self._ups += 1
                    self._up = 1
                elif mf.control == "down":
                    self._up = 0
                self._frames += 1
                self._last = self.timesrc.mono()
                emit(SourceFrame(frame=mf.frame, recv_wall=self.timesrc.wall(), recv_mono=self._last,
                                 conn=f"{self.name}#{epoch}", control=mf.control))
            end = None if self.tail_s is None else self.timesrc.mono() + float(self.tail_s)
            while not self._stop and (end is None or self.timesrc.mono() < end):
                await asyncio.sleep(0.1 if end is None else min(0.1, max(0.0005, (end - self.timesrc.mono()) / speed)))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never raise except CancelledError
            self._errors += 1
        finally:
            if self._up:
                self._up = 0
                try:
                    emit(SourceFrame(frame={}, recv_wall=self.timesrc.wall(), recv_mono=self.timesrc.mono(),
                                     conn=f"{self.name}#{epoch}", control="down"))
                except Exception:  # noqa: BLE001
                    self._errors += 1

    def stop(self) -> None:
        self._stop = True

    def stats(self) -> SourceStats:
        return SourceStats(name=self.name, connections_up=self._up, frames=self._frames,
                           reconnects=max(0, self._ups - 1), last_frame_mono=self._last, errors=self._errors)
