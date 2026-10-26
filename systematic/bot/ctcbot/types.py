"""ctcbot shared types. Copied verbatim from DESIGN.md section 3. Other modules import from here only."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

SYMBOLS: tuple[str, ...] = ("PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN",
                            "ARCH", "REBLD")
STOCKS: tuple[str, ...] = SYMBOLS[:10]
ETFS: tuple[str, ...] = ("ARCH", "REBLD")
ISLAND: tuple[str, ...] = ("PORT", "ISLE", "AQUA", "GRID", "FERRY")


class Side(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class Priority(enum.IntEnum):
    KILL = 0      # kill switch only
    SNIPE = 1     # S1-r3, announcement freeze cancels
    HEDGE = 2     # flatten, H1 fallback exit, REIN ask; the minimum priority of every cancel
    TAKE = 3      # S3, H1 entry, ETF lead-lag, announcement trades


class Phase(enum.StrEnum):
    PRE_STORM = "pre_storm"; STORM = "storm"; AFTERMATH = "aftermath"; CLOSED = "closed"; UNKNOWN = "unknown"


class RiskMode(enum.StrEnum):
    NORMAL = "NORMAL"; NO_NEW_RISK = "NO_NEW_RISK"; FROZEN = "FROZEN"


class Trigger(enum.StrEnum):
    TICK = "tick"; NEWS = "news"; FV = "fv"; ROUND_START = "round_start"; SEASON_RESET = "season_reset"
    COMMAND = "command"


class OrderState(enum.StrEnum):
    NEW = "new"                         # record created, ticket held, request not yet handed out
    SENDING = "sending"                 # POST /orders in flight
    OPEN = "open"                       # exchange id known, remaining > 0
    CANCEL_PENDING = "cancel_pending"   # DELETE in flight
    CANCEL_ACKED = "cancel_acked"       # DELETE 2xx; counts in room until one reconcile cycle confirms; not a live clip
    UNKNOWN = "unknown"                 # POST outcome unknown (timeout, transport error, 5xx)
    CANCEL_UNKNOWN = "cancel_unknown"   # DELETE outcome unknown
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


TERMINAL_STATES = frozenset({OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED})
LIVE_STATES = frozenset(set(OrderState) - TERMINAL_STATES)


class ApiKind(enum.StrEnum):
    PLACE = "place"; CANCEL = "cancel"; CANCEL_ALL = "cancel_all"; ETF_CREATE = "etf_create"; ETF_REDEEM = "etf_redeem"
    GET_OPEN = "get_open"; GET_POSITIONS = "get_positions"; GET_TIME = "get_time"; GET_BOOK = "get_book"
    GET_CASE = "get_case"


ORDER_ACTIONS = frozenset({ApiKind.PLACE, ApiKind.CANCEL, ApiKind.CANCEL_ALL, ApiKind.ETF_CREATE, ApiKind.ETF_REDEEM})


class PriceQuality(enum.StrEnum):
    EXACT = "exact"              # price reported by the exchange (ack or sim)
    INFERRED = "inferred"        # inferred from /positions average price or realized P&L
    LIMIT_BOUND = "limit_bound"  # unknown; the order's limit price (worst case)


# ----------------------------------------------------------------------------- time and logging
class TimeSource(Protocol):
    def mono(self) -> float: ...
    def wall(self) -> float: ...


class EventLog(Protocol):
    def event(self, kind: str, **fields: Any) -> None: ...
    def alert(self, kind: str, msg: str, **fields: Any) -> None: ...


class ListLog:
    """In-memory EventLog for tests and the simulator."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def event(self, kind: str, **fields: Any) -> None:
        self.events.append((kind, fields))

    def alert(self, kind: str, msg: str, **fields: Any) -> None:
        self.events.append(("alert", dict(fields, alert=kind, msg=msg)))

    def kinds(self, kind: str) -> list[dict]:
        return [f for k, f in self.events if k == kind]


# ----------------------------------------------------------------------------- market-data sources
@dataclass(frozen=True, slots=True)
class SourceFrame:
    frame: dict                 # parsed JSON frame as received (a 'received_at' key from recordings is ignored)
    recv_wall: float            # unix seconds at receipt
    recv_mono: float            # TimeSource.mono() at receipt
    conn: str                   # "<source>#<connection epoch>", e.g. "ws0#3"
    control: str | None = None  # None for exchange frames; "up" / "down" for connection changes (frame == {})


@dataclass(frozen=True, slots=True)
class SourceStats:
    name: str; connections_up: int; frames: int; reconnects: int; last_frame_mono: float | None; errors: int


class MarketDataSource(Protocol):
    name: str

    async def run(self, emit: Callable[[SourceFrame], None]) -> None:
        """Run until stop(); call emit (on the event-loop thread) for every frame and control change, in order.
        Never raises except asyncio.CancelledError; reconnects internally."""
        ...

    def stop(self) -> None: ...
    def stats(self) -> SourceStats: ...


# ----------------------------------------------------------------------------- normalized events
@dataclass(frozen=True, slots=True)
class BookLevel:
    price: float; qty: int


@dataclass(frozen=True, slots=True)
class BookEvent:
    symbol: str; bids: tuple[BookLevel, ...]; asks: tuple[BookLevel, ...]; server_ts: str; recv_mono: float


@dataclass(frozen=True, slots=True)
class QuoteEvent:
    symbol: str; bid: float | None; ask: float | None; bid_size: int | None; ask_size: int | None
    server_ts: str; recv_mono: float


@dataclass(frozen=True, slots=True)
class TradeEvent:
    symbol: str; price: float; qty: int; trade_id: str; server_ts: str; recv_mono: float


@dataclass(frozen=True, slots=True)
class NewsEvent:
    msg: dict                   # inner news message (never a snapshot)
    mtype: str; seq: int | None
    round: int | None           # int-normalized ("3" -> 3)
    market_time: str | None; season_epoch: int; recv_mono: float


@dataclass(frozen=True, slots=True)
class SnapshotEvent:
    msg: dict; last_seq: int; round: int | None; phase: str | None; market_time: str | None
    stage: str                  # "arrived" (emitted at once) or "released" (after feed.snapshot_hold_s)
    engine_relevant: bool       # released only: last_seq > the normalizer's contiguous high-water mark wm
    season_epoch: int; recv_mono: float


@dataclass(frozen=True, slots=True)
class ConnEvent:
    conn: str; up: bool; recv_mono: float


@dataclass(frozen=True, slots=True)
class HeartbeatEvent:
    conn: str; recv_mono: float


@dataclass(frozen=True, slots=True)
class SeasonResetEvent:
    reason: str                 # "storm_formed_r1" | "snapshot_last_seq" | "round_decrease" | "date_change"
                                # | "comp_id" | "operator"
    new_epoch: int; recv_mono: float
    provisional: bool = False   # True: feed-only evidence; the epoch has not changed yet (§4.A normalize, §4.D core)


MarketEvent = BookEvent | QuoteEvent | TradeEvent | NewsEvent | SnapshotEvent | ConnEvent | HeartbeatEvent | SeasonResetEvent


# ----------------------------------------------------------------------------- clock
@dataclass(frozen=True, slots=True)
class ClockReading:
    known: bool; season_epoch: int; comp_id: str | None; round: int | None
    date: str | None            # market date "YYYY-MM-DD"
    t: float | None             # real seconds since this round's 09:30 open (<0 or >= round_len when closed)
    phase: Phase
    running: bool               # market clock believed to be advancing (orders allowed)
    quality: str                # "server" | "feed" | "extrapolated" | "unknown"
    anchor_age_s: float | None


# ----------------------------------------------------------------------------- fair values
@dataclass(frozen=True, slots=True)
class FairValueSnapshot:
    version: int                # the adapter's own counter: +1 per published snapshot, never reset
    values: Mapping[str, tuple[float, float]]   # symbol -> (mean, sd), all 12 symbols
    season_epoch: int
    round: int | None           # highest round ingested
    seq_hi: int | None          # highest news seq ingested
    msg_time: str | None        # market time of the newest ingested message
    included_mono: float        # recv_mono of the newest ingested message (-inf if none)
    computed_mono: float; compute_ms: float; ok: bool
    error: str | None = None
    diag: Mapping[str, Any] = field(default_factory=dict)   # "cum_damage", "season_loss", "status_r3", "pattern"
    engine_version: int = 0     # SeasonState.version (restarts at 0 on reset; unchanged by adjustments)


@dataclass(frozen=True, slots=True)
class EngineHealth:
    alive: bool; pending: int; oldest_pending_age_s: float | None; last_compute_ms: float | None
    errors: int; consecutive_errors: int; snapshot_fix: bool | None


class FairValueProvider(Protocol):
    def set_listener(self, cb: Callable[[], None]) -> None: ...   # cb() when a new snapshot is ready; any thread
    def submit(self, msgs: Sequence[dict], recv_mono: float) -> None: ...   # raw news messages and snapshots
    def latest(self) -> FairValueSnapshot | None: ...
    def health(self, now: float) -> EngineHealth: ...
    def set_adjustment(self, symbol: str, mult: float = 1.0, add: float = 0.0) -> None: ...
    def override(self, dotted: str, value: Any) -> None: ...
    def reset_season(self, season_epoch: int, persist_path: str | None) -> None: ...
    def close(self) -> None: ...


# ----------------------------------------------------------------------------- books and working orders
@dataclass(frozen=True, slots=True)
class BookView:
    symbol: str; bid: float | None; ask: float | None; bid_size: int | None; ask_size: int | None
    bids: tuple[BookLevel, ...]; asks: tuple[BookLevel, ...]
    mid: float | None; half_spread_bp: float | None; age_s: float | None
    empty: bool                 # no bid and no ask
    wall_only: bool             # half_spread_bp > guards.opening.max_half_spread_bp
    server_ts: str | None


@dataclass(frozen=True, slots=True)
class WorkingSummary:
    symbol: str
    buy_qty: int; sell_qty: int            # live (LIVE_STATES) remaining, all styles: what room counts
    clip_buy_qty: int; clip_sell_qty: int  # clip style, every live state except CANCEL_ACKED: what exposure counts
    n_orders: int
    clip_working: bool                     # a clip record is live in a state other than CANCEL_ACKED (§5.3)
    keys: frozenset[str]


# ----------------------------------------------------------------------------- intents (strategies -> order manager)
@dataclass(frozen=True, slots=True)
class TargetIntent:
    strategy: str; symbol: str
    target: int                 # desired signed position once this work is done
    clip_qty: int               # max shares per clip
    clip_interval_s: float      # min seconds between clip sends in this symbol
    limit_buy: float | None     # max price for buys (None = no buys)
    limit_sell: float | None    # min price for sells (None = no sells)
    priority: Priority
    ttl_s: float                # cancel the unfilled remainder of a clip this long after sending
    reduce_only: bool = False
    edge_usd_per_share: float = 0.0
    expected_cost_bp: float | None = None
    ref_mid: float | None = None
    reason: str = ""
    valid_until: float | None = None    # mono: no clip at or after this time (the strategy window's end)
    slot_anchor: float | None = None    # mono: clips on the grid anchor + k*clip_interval_s; a remainder is cancelled at the next grid point
    first_clip_cost_bp: float | None = None   # expected cost of a clip with no own same-side clip within cost.fo_window_s (walk only)


@dataclass(frozen=True, slots=True)
class RestIntent:
    strategy: str
    key: str                    # idempotency key, e.g. "s1r3:PORT:buy", "rein_ask"
    symbol: str; side: Side; price: float; priority: Priority
    qty: int | None = None      # desired live quantity; None = all strategy room on that side (§4.B limits)
    edge_usd: float = 0.0       # per share if marketable now; the OM places marketable rests first, larger edge*qty first
    reason: str = ""
    value_usd: float | None = None  # dollars capturable now from the visible book; when set, the OM orders by it


@dataclass(frozen=True, slots=True)
class CancelIntent:
    strategy: str; priority: Priority
    scope: str                  # "key" | "key_prefix" | "strategy" | "symbol" | "all"
    symbol: str | None = None
    key: str | None = None
    reason: str = ""


Intent = TargetIntent | RestIntent | CancelIntent


# ----------------------------------------------------------------------------- orders and fills
@dataclass(slots=True)
class OrderRecord:
    local_id: str; strategy: str; key: str | None
    style: str                  # "clip" | "rest" | "foreign"
    symbol: str; side: Side; qty: int; price: float; priority: Priority
    state: OrderState = OrderState.NEW
    order_id: str | None = None
    filled_qty: int = 0
    remaining: int = 0
    avg_fill_price: float | None = None
    ttl_s: float | None = None
    reduce_only: bool = False
    ref_mid: float | None = None
    expected_cost_bp: float | None = None
    edge_usd_per_share: float = 0.0
    dry: bool = False                   # set from the PLACE result's dry_run, never from a flag
    cancel_on_ack: bool = False         # a cancel covered this record while it was SENDING: cancel it once acked
    last_seen_open_mono: float | None = None   # last reconcile cycle that showed it open
    created_mono: float = 0.0
    sent_mono: float | None = None
    acked_mono: float | None = None
    cancel_sent_mono: float | None = None
    done_mono: float | None = None
    missing_cycles: int = 0
    reject_reason: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Fill:
    local_id: str | None; order_id: str | None
    strategy: str               # "foreign" for unexplained position changes
    key: str | None; symbol: str; side: Side; qty: int; price: float; quality: PriceQuality; mono: float
    ref_mid: float | None; expected_cost_bp: float | None
    source: str                 # "ack" | "reconcile" | "foreign"


# ----------------------------------------------------------------------------- REST
@dataclass(frozen=True, slots=True)
class PositionRow:
    symbol: str; qty: int; avg_price: float | None; mark: float | None; realized: float | None
    unrealized: float | None


@dataclass(frozen=True, slots=True)
class OpenOrderRow:
    order_id: str; symbol: str; side: Side; price: float | None; qty: int | None; remaining: int; status: str | None


@dataclass(frozen=True, slots=True)
class OrderAck:
    order_id: str | None; status: str | None; filled_qty: int | None; remaining: int | None
    avg_fill_price: float | None


@dataclass(frozen=True, slots=True)
class ServerTime:
    comp_id: str | None; state: str | None; fictional_time: str | None; elapsed_active_s: float | None


@dataclass(frozen=True, slots=True)
class Ticket:
    priority: Priority; t: float; tag: str


@dataclass(frozen=True, slots=True)
class ApiRequest:
    req_id: int
    kind: ApiKind
    priority: Priority = Priority.TAKE
    symbol: str | None = None
    side: Side | None = None
    qty: int | None = None
    price: float | None = None
    order_type: str = "limit"
    order_id: str | None = None
    local_id: str | None = None
    ticket: Ticket | None = None
    cycle: int | None = None
    created_mono: float = 0.0

    @property
    def is_order_action(self) -> bool:
        return self.kind in ORDER_ACTIONS


@dataclass(frozen=True, slots=True)
class ApiResult:
    req: ApiRequest
    status: int | None          # HTTP status; None = transport error or timeout
    data: Any                   # decoded JSON body (or truncated text)
    parsed: Any                 # typed: OrderAck | list[OpenOrderRow] | dict[str, PositionRow] | ServerTime | dict | None
    error: str | None
    sent_mono: float
    done_mono: float
    dry_run: bool = False
    sent: bool = True           # False: provably never reached the exchange (dry-run, purged, frozen, connect failure)

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def outcome_unknown(self) -> bool:
        return self.sent and (self.status is None or self.status >= 500)

    @property
    def throttled(self) -> bool:
        return self.status == 429


class ExchangeAPI(Protocol):
    def execute(self, req: ApiRequest) -> ApiResult: ...   # blocking, thread-safe


# ----------------------------------------------------------------------------- strategy context
@dataclass(frozen=True, slots=True)
class FeedHealth:                        # computed on the merged stream of every connection
    up: bool                             # a connection is up and a frame arrived within watchdog_s()
    connections_up: int; connections: int
    last_frame_age_s: float | None; last_news_age_s: float | None
    last_storm_obs_age_s: float | None   # receive age of the newest storm observation (news or snapshot obs)
    round_max_age_gap_s: float           # largest jump in storm 'age' between consecutive forwarded observations this round
    down_since_mono: float | None; dup_news: int; seq_gaps: int
    wm: int; max_seq: int                # the normalizer's high-water mark and highest forwarded seq


@dataclass(frozen=True, slots=True)
class SeasonInfo:
    season_epoch: int; comp_id: str | None
    prelim: Mapping[int, dict]           # round -> preliminary damage_report message
    final: Mapping[int, dict]            # round -> final damage_report message
    final_seq: Mapping[int, int]         # round -> seq of the final report (-1 if learned from a snapshot)
    loss_known: float                    # sum of final industry_loss_musd
    announcements: tuple[dict, ...]
    case_diff: frozenset[str]            # symbols whose case.json values differ, minus those cleared by `caseok`
    timing_diff: bool                    # case timing differs and `caseok timing` has not acknowledged it
    coverage: Mapping[int, str]          # round -> "full" | "gapped" | "missing" (StormCoverage, §4.C)
    reset_pending: str | None            # reason of a provisional season reset, else None


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    args: tuple[str, ...] = ()
    source: str = "console"              # "console" | "killfile" | "signal" | "sim"


@dataclass(frozen=True, slots=True)
class StrategyContext:
    now: float; trigger: Trigger; clock: ClockReading
    schedule: Any                        # ctcbot.case.Schedule
    limits: Mapping[str, int]            # strategy limits L_s: int(case limit x sizing.scale)
    books: Mapping[str, BookView]; positions: Mapping[str, int]; working: Mapping[str, WorkingSummary]
    fv: FairValueSnapshot | None; engine: EngineHealth; feed: FeedHealth; season: SeasonInfo
    risk_mode: RiskMode; disabled: frozenset[str]
    frozen: frozenset[str]               # announce's frozen symbols (runner.frozen())
    leadlag: frozenset[str]              # ARCH/REBLD while etf_leadlag is on (runner.leadlag_symbols())
    news: tuple[NewsEvent, ...] = ()
    snapshots: tuple[SnapshotEvent, ...] = ()
    fills: tuple[Fill, ...] = ()
    command: Command | None = None
    budget_take: int = 0                 # actions available to TAKE right now

    def exposure(self, symbol: str) -> int:
        w = self.working.get(symbol)
        p = self.positions.get(symbol, 0)
        return p if w is None else p + w.clip_buy_qty - w.clip_sell_qty
