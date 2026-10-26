# ctcbot: production bot for Systematic Equities (Hurricane Season), CTC 2026

This document is the complete design for the competition bot. Five implementer groups (A-E) code from it in parallel without talking to each other. When this document and an older source disagree, this document wins. Within the strategy sources, `[testnet]` items in `systematic_playbook.md` and `TESTNET_FINDINGS.md` §6 win over older playbook text, and this document follows them by default; the only deliberate deviations are listed in §11. Every number below comes from one of those sources, from `testnet/analysis/books/cost_model.json` (via `writer/w05_params.out`) or from the REC re-backtest (`writer/w02_sens.py`).

**Revision 2** applies the design critique of revision 1. §11 lists the critique items that were changed in substance or rejected, with the reasons. §4.F lists every call that crosses a group boundary. §12 records the integrator's changes and §13 the hardening changes made after the spec, safety and robustness reviews; where they differ from the earlier sections, they win.

Paths: `SYS` = `/Users/bowentian/Documents/Fun Stuff/CTC2026/systematic`, `BOT` = `SYS/bot`, `SP` = the scratchpad named in §9.0.

---

## 0. Ground rules (every group)

1. **Safety**
   - The bot defaults to dry-run: order actions are logged and never sent.
   - Sending needs `--live`, an explicit `--host`, **and** the confirmation phrase typed interactively at startup (§4.D cli). The confirmed phrase is the only code path that creates a `LiveToken` (§4.A rest), and `RestClient` sends order actions only while it holds a valid one. No other class has a dry-run switch.
   - The key comes from `--key-file PATH` or the env var named in `exchange.key_env` (default `CTC_BOT_KEY`). There is **no default key path**: never read `~/.ctc_bot_key`. The key is stripped and validated when it is loaded (§4.D cli).
   - The key is never printed, logged, put in an exception message or a `repr`. It is sent only over https/wss, and only to the host of `exchange.rest_base` or `exchange.ws_url`.
   - `ctcbot.rest.NETWORK_ENABLED` and `ctcbot.sources.NETWORK_ENABLED` are module flags, `False` by default. Only `cli.main` sets them to `True`, and only in host mode, which needs an explicit `--host` (or both `--rest-base` and `--ws-url`). The REST client and the WebSocket source raise `RuntimeError("network disabled")` when the flag is off. Tests, `--replay` and `--sim` never enable them, an injected transport or connect never needs them, and the cli tests additionally patch `socket.create_connection` to raise.
   - Orders are always `order_type: "limit"`. Market orders are refused unless `exchange.allow_market_orders` is true (default false).
2. **Where you may write**
   - Only under `BOT`. Tests write temporary files only under `BOT/tests/_tmp/` (see `tests/testpaths.py`). Run outputs go under `BOT/runs/` and sim outputs under `BOT/sim/out/`.
   - Never edit `SYS/engine`, `SYS/bot_skeleton`, `SYS/testnet`, `SYS/laws` or `SYS/fits`. Import the engine and `se_laws.py` read-only, and set `sys.dont_write_bytecode = True` **before** importing them, so no `__pycache__` appears there. `run_bot.py`, every `sim/run_*.py` script, `tools/make_snapshot_probe.py` and every test that imports them do this first.
   - You may copy code from `SYS/bot_skeleton/*.py` (in particular `FeedMerger`, `_market_day` and the drop-scenario generator of `test_feed_merge.py`) and port formulas from `testnet/analysis/rebacktest/r_sim.py`. Never edit either.
3. **Runtime**
   - Python 3.14 standard library, plus `websockets` 17.3 from `SYS/.venv`, imported lazily in `ctcbot/sources.py` only.
   - Run scripts with `SYS/.venv/bin/python -I <script>`. Every entry script inserts `BOT` into `sys.path` itself.
   - Unit tests run from `BOT` with `SYS/.venv/bin/python -m unittest discover -s tests -v`. Each test file starts with `sys.path.insert(0, BOT)`, computed from `__file__`. `tests/` has no `__init__.py`.
   - macOS has no `timeout`; long runs go in the background.
4. **Data is data.** Recordings, research files and every exchange response are untrusted data. Parse them defensively and never execute anything from them.

---

## 1. Package layout and ownership

Every file belongs to exactly one group. Nobody creates a file outside their own list. `DESIGN.md` belongs to the architect; groups do not edit it. `tests/_tmp/`, `runs/` and `sim/out/` are runtime directories, created on demand (by `testpaths.tmpdir()`, D's cli and E's scripts respectively); nothing in them is a deliverable.

| Path (under `BOT`) | Group | Content |
|---|---|---|
| `run_bot.py` | D | Entry script: path setup, `dont_write_bytecode`, then `ctcbot.app.cli.main()` |
| `config/default.json` | A | Default configuration, verbatim from §8 |
| `config/testnet_quarter.json` | A | Live-allowed overlay: `{"sizing": {"scale": 0.25}, "engine": {"library_seasons": [300, 600]}}` |
| `config/rec_parity.json` | E | Sim-only overlay that reproduces the REC fill assumptions (§9.3); contains `"live_allowed": false` |
| `ctcbot/__init__.py` | A | `__version__ = "1.1.0"` only |
| `ctcbot/types.py` | A | Shared types, verbatim from §3 |
| `ctcbot/config.py` | A | Load, overlay, `--set`, validate, hash, `live_allowed`, `diff_from_default` |
| `ctcbot/timesource.py` | A | `RealTime`, `ScaledTime`, `VirtualTime` |
| `ctcbot/case.py` | A | `CaseInfo`, `CaseDiff`, `diff_case`, `strategy_limits`, `clip_size`, `Schedule` |
| `ctcbot/clock.py` | A | `ClockModel` (market clock, round, phase, pauses) |
| `ctcbot/normalize.py` | A | `FeedNormalizer` (merges every connection: dedup, high-water mark, snapshot hold, season reset) |
| `ctcbot/sources.py` | A | `WsSingleSource`, `build_sources`, `RecordingSource`, `iter_recording` |
| `ctcbot/rest.py` | A | `LiveToken`, `RestClient` (live gate, host allow-list), `RestExecutor` (workers plus kill worker), response parsers |
| `ctcbot/ratelimit.py` | A | `ActionLimiter`, `RequestLimiter` |
| `ctcbot/state/__init__.py`, `ctcbot/exec/__init__.py` | B | Empty |
| `ctcbot/state/books.py` | B | `BookStore` |
| `ctcbot/state/positions.py` | B | `PositionBook` (positions, cash, MTM, fill-price inference, quiet-symbol guard) |
| `ctcbot/state/orders.py` | B | `OrderStore` and the transition table |
| `ctcbot/state/limits.py` | B | Limit-room accounting |
| `ctcbot/exec/order_manager.py` | B | `OrderManager` (intents to clips, caps, remainder cancels, rests, cancels, kill) |
| `ctcbot/exec/reconcile.py` | B | `Reconciler` |
| `ctcbot/exec/risk.py` | B | `RiskManager` (modes, holds, pre-trade checks, breakers) |
| `ctcbot/models/__init__.py`, `ctcbot/strategies/__init__.py` | C | Empty |
| `ctcbot/models/engine_adapter.py` | C | `EngineAdapter` (thread and inline modes, adjust-file merge), `load_engine_module`, `snapshot_fix_present` |
| `ctcbot/models/cost_model.py` | C | `CostModel` |
| `ctcbot/models/coverage.py` | C | `StormCoverage` (per-round observation coverage) |
| `ctcbot/models/laws.py` | C | `load_laws_module`, `LawsCrossCheck`, `LawCheck` |
| `ctcbot/models/data/snapshot_probe.jsonl` | C | Probe messages for the snapshot-fix check (§4.C), generated by the script below and committed |
| `tools/make_snapshot_probe.py` | C | Offline generator of the probe file from `HISTORY/season-401/feed.jsonl` |
| `ctcbot/strategies/base.py` | C | `Strategy`, `StrategyDeps`, helpers |
| `ctcbot/strategies/runner.py` | C | `StrategyRunner` (precedence, guards, exception isolation) |
| `ctcbot/strategies/h1.py`, `s3.py`, `flatten.py`, `s1r3.py`, `rein_ask.py`, `etf_leadlag.py`, `announce.py` | C | One strategy each (§6) |
| `ctcbot/app/__init__.py` | D | Empty |
| `ctcbot/app/core.py` | D | `BotCore`, `CoreParts`, `default_factories` |
| `ctcbot/app/shell.py` | D | asyncio orchestration, threads, signals |
| `ctcbot/app/console.py` | D | Command parser |
| `ctcbot/app/status.py` | D | Status line |
| `ctcbot/app/log.py` | D | `JsonlLog` (structured logs, redaction), `FrameRecorder`, `install_redaction` |
| `ctcbot/app/persist.py` | D | `save_state`, `load_state`, `restart_plan`, `new_run_id` |
| `ctcbot/app/cli.py` | D | argparse, modes, live gate, startup sequence |
| `sim/__init__.py` | E | Empty |
| `sim/harness.py` | E | Virtual-time discrete-event driver of `BotCore` |
| `sim/exchange.py` | E | `SimExchange` (REST semantics, exchange rules, fills, P&L) and `SimSource` |
| `sim/research_market.py` | E | `ResearchMarket` (feed.jsonl, market.csv consensus, synthetic books) |
| `sim/recorded_market.py` | E | `RecordedMarket` (testnet recording, recorded books, server-time synthesis, reconnect control frames) |
| `sim/fillmodel.py` | E | r_sim-equivalent cost formulas |
| `sim/fv_cache.py` | E | `CachedFairValues` (a `FairValueProvider` over `fv_holdout_NNN.json`) |
| `sim/report.py` | E | Per-season and summary reports in r_sim's table format |
| `sim/run_research.py`, `sim/run_recording.py`, `sim/compare_rec.py` | E | Scripts (§4.E) |
| `sim/ablations/abl_edge.json`, `abl_h1size.json`, `abl_caps.json`, `abl_rein.json`, `abl_s1r3.json`, `abl_floors.json` | E | One-change overlays for the §9.3 attribution |
| `tests/testpaths.py` | A | Data paths, `require(path)`, `integration(modname)`, `tmpdir()` under `tests/_tmp` |
| `tests/test_a_*.py` | A | `config`, `case_schedule`, `clock`, `normalize`, `feed_merge`, `sources`, `rest`, `ratelimit` |
| `tests/test_b_*.py` | B | `books`, `positions`, `orders`, `order_manager`, `reconcile`, `risk`, `property` |
| `tests/test_c_*.py` | C | `engine_adapter`, `cost_model`, `coverage`, `laws`, `law_check`, `h1`, `s3`, `flatten`, `s1r3`, `rein_ask`, `etf_leadlag`, `announce`, `runner` |
| `tests/test_d_*.py` | D | `core`, `shell`, `kill`, `latency`, `console`, `persist`, `cli`, `log` |
| `tests/test_e_*.py` | E | `exchange`, `fillmodel`, `harness`, `recording`, `research` |

**Dependency rules:**
- `types.py` imports only the standard library.
- A imports only `types` and other A modules.
- B imports `types`, `ctcbot.case`, `ctcbot.ratelimit` and `ctcbot.config`.
- C imports `types`, `ctcbot.case`, `ctcbot.config` and its own modules.
- D (`ctcbot/app/*`) imports `types` and A modules at module level. It imports B and C modules **only inside functions**: `core.default_factories()` and the startup code in `cli.py`. Type hints for B and C classes go under `if TYPE_CHECKING:`. So every D module imports cleanly while B or C are unfinished.
- E (`sim/*`) imports `types` and A modules at module level, and B, C and D modules only inside functions (`SimExchange` receives its `CostModel` from the caller; the run scripts import inside `main`).
- Nothing in `ctcbot` imports `sim` at module level; only `ctcbot/app/cli.py` imports it lazily, inside the `--replay` and `--sim` branches.

**Unit-test rule:** a group's unit tests may import any module, but they must not depend on another group's *behaviour*. Every collaborator from another group is replaced by a fake written in the test file and passed in through the injection points of §4 (constructor arguments, `BotCore(factories=...)`, `cli.main(transport=..., ws_connect=..., stdin=..., isatty=...)`). Tests that need the real modules of several groups are marked integration: they call `testpaths.integration("ctcbot.exec.order_manager", ...)`, which raises `SkipTest` when an import fails. §9.2-9.3 are integration tests.

**Build order:** A writes `ctcbot/types.py`, `ctcbot/__init__.py`, `config/default.json` and `tests/testpaths.py` first, verbatim from §3 and §8. The other groups code against the spec in the meantime and do not create those files themselves.

`tests/testpaths.py` constants and helpers:
- `BOT`, `SYS`;
- `RECORDING = SYS/testnet/recordings/testnet_s224_s225.jsonl`;
- `ENGINE_DIR = SYS/engine`, `LAWS = SYS/laws/se_laws.py`;
- `COST_MODEL = SYS/testnet/analysis/books/cost_model.json`;
- `RESEARCH_CASE = SYS/data/case.json`;
- `SKELETON_FEED = SYS/bot_skeleton/feed.py` (read only; tests may load it with `dont_write_bytecode` to compare against);
- `HISTORY = os.environ.get("CTCBOT_HISTORY", SP + "/data/hurricane-season-research/history")`;
- `FV_CACHE = os.environ.get("CTCBOT_FV_CACHE", SP + "/work/se/backtest/cache")`;
- `REBACKTEST = SYS/testnet/analysis/rebacktest`;
- `SLOW = os.environ.get("CTCBOT_SLOW") == "1"`;
- `REQUIRE = os.environ.get("CTCBOT_REQUIRE_DATA") == "1"`: `require(path)` and `integration(...)` raise `AssertionError` instead of `SkipTest`, so an acceptance run cannot pass with nothing run.

---

## 2. Architecture

```
 WsSingleSource ws0 ─┐                                                BotCore (single thread, asyncio loop)
 WsSingleSource ws1 ─┼─SourceFrame─> FeedNormalizer ──events──>       |  ClockModel, BookStore, SeasonInfo, FeedHealth,
 (or RecordingSource)┘   one stream: merge, dedup, wm,                |  StormCoverage, LawsCrossCheck, LawCheck
                         snapshot hold, season reset                  |  engine.submit(news, relevant snapshots) --> EngineAdapter
                                                                      |                                     (worker thread owns SeasonState)
                                                                      |  <-- on_fair_values (call_soon_threadsafe)
                                                                      |  StrategyRunner.decide(ctx) -> intents
                                                                      |  OrderManager.set_intents/step -> ApiRequests (tickets from ActionLimiter)
                                                                      v
                             outbox + control -> RestExecutor: N workers (priority queue) + 1 kill worker -> RestClient (LiveToken gate) -> exchange
                             ApiResult -> call_soon_threadsafe -> BotCore.on_api_result -> OrderManager / Reconciler / ClockModel
```

**One idea makes the bot simulable.** `BotCore` (D) is a synchronous object that never sleeps, reads a clock, starts threads or does network I/O; its only I/O is logging and the persist job's file write.
- Every input is a method call that carries `now` (the `TimeSource.mono()` value).
- Every output is an `ApiRequest` that the caller collects with `take_requests()`, or a control word (`"freeze"`, `"unfreeze"`, `"prewarm_all"`) collected with `take_control()`.
- In production, `shell.py` drives it from asyncio.
- In simulation, `sim/harness.py` drives the **same** `BotCore`, with the same strategies and order manager, in virtual time against `SimExchange`.

---

## 3. Shared types: `ctcbot/types.py` (group A copies this verbatim)

```python
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
```

---

## 4. Module interfaces

Signatures are binding. "Semantics" bullets are requirements, and the tests in §9 check them.

### 4.A Infrastructure (group A)

**`config.py`**
```python
BOT_ROOT: str            # dirname(dirname(abspath(__file__)))
class ConfigError(Exception): ...
@dataclass(frozen=True)
class Config:
    d: dict; hash: str; root: str; sources: tuple[str, ...]
    live_allowed: bool                                        # False if any loaded file says "live_allowed": false
    def get(self, dotted: str, default: Any = ...) -> Any     # KeyError if missing and no default
    def path(self, p: str | None) -> str | None               # relative paths resolve against root
    def sym(self, symbol: str) -> dict                        # d["symbols"][symbol]
def load_config(path: str | None = None, overlays: Sequence[str] = (), sets: Sequence[str] = ()) -> Config
def deep_merge(base: dict, over: dict) -> dict                # dicts merge recursively; lists and scalars replace
def diff_from_default(cfg: Config) -> list[str]               # "a.b.c: <default> -> <effective>", sorted by path
```
- `path` defaults to `BOT_ROOT/config/default.json`; overlays apply in order; each `--set` item is `a.b.c=VALUE`, with VALUE parsed as JSON and falling back to a string.
- **`live_allowed`** is a top-level boolean that any file may carry. It is removed from each file before merging and never merged: `Config.live_allowed` is the AND over the loaded files (default true). `--set live_allowed=...` raises `ConfigError`.
- **Validation** raises `ConfigError` listing every problem:
  - `symbols` has exactly the 12 SYMBOLS; every strategy section exists; every anchor spec is `[anchor, number]` with an anchor in {open, storm_start, storm_end, prelim, final, close};
  - floors cover KILL, SNIPE, HEDGE and TAKE; `rate.bot_cap_per_min` ≤ `exchange.team_actions_per_min` − 10;
  - `strategies.s3.edge_ref` is "touch" or "mid"; with "touch", `strategies.s3.limit_cost_frac` must be 1.0; with "mid", it must be in [0, 0.5] (0.5 is the live value of REC's rule, 0.0 the parity value);
  - `feed.connections` has 1 to 4 entries with unique `name`s, each `{"name": str, "local_addr": null | [ip, port]}`;
  - `sizing.scale` is in (0, 1]; `sizing.clip_div` has positive ints for `s3`, `h1`, `h1_exit`, `flatten`, `flatten_max` and `leadlag`;
  - `exchange.rest_base` and `exchange.case_url`, when not null, start with `https://`, and `exchange.ws_url` with `wss://`.
- `hash` is the first 12 hex digits of the sha256 of `json.dumps(d, sort_keys=True, separators=(",", ":"))`.
- `diff_from_default` compares the effective `d` with `default.json` alone, leaf by leaf; the live prompt prints it (§4.D cli).

**`timesource.py`**
- `RealTime()`: `time.monotonic` and `time.time`.
- `ScaledTime(speed: float, start: float = 0.0)`: `mono = start + (time.monotonic() - t0) * speed`; wall is scaled the same way from start.
- `VirtualTime(start=0.0, wall0=1.8e9)`: `set(t)` and `advance(dt)`, with `mono = t` and `wall = wall0 + t`.

**`case.py`**
```python
@dataclass(frozen=True)
class CaseInfo:
    raw: dict; symbols: tuple[str, ...]; limits: dict[str, int]; baskets: dict[str, dict[str, int]]
    kinds: dict[str, str]     # company kind from case ("physical" | "rebuild" | "insurer"); "etf" for ETFs
    timing: dict
    @classmethod
    def from_dict(cls, d: dict) -> "CaseInfo": ...
    @classmethod
    def from_file(cls, path: str) -> "CaseInfo": ...
@dataclass(frozen=True)
class CaseDiff:
    symbols: frozenset[str]   # symbols whose values may differ (companies, limits, baskets, map)
    timing: bool              # case["timing"] differs
    lines: tuple[str, ...]    # readable differences for the log and the live prompt
    @property
    def clean(self) -> bool: ...    # not symbols and not timing
def diff_case(served: dict, research: dict) -> CaseDiff
def strategy_limits(case: CaseInfo, cfg: Config) -> dict[str, int]   # int(case limit * sizing.scale), every symbol
def clip_size(limit_scaled: int, div: int) -> int                    # max(1, int(limit_scaled / div))
class Schedule:
    def __init__(self, timing: Mapping[str, float], cost_phases: Sequence[Sequence]): ...
    open: float; storm_start: float; storm_end: float; prelim: float; final: float; close: float
    round_len: float; speed: float
    def at(self, spec: Sequence) -> float                 # ["storm_start", -10] -> 50.0
    def phase(self, t: float | None) -> Phase
    def cost_phase(self, t: float) -> str
    def advisory_times(self) -> list[float]
    @staticmethod
    def market_to_t(market_time: str, speed: float) -> tuple[str, float]   # ("1802-05-10", real s since 09:30)
```
- Timing comes from `case["timing"]`: `storm_start = pre_storm` (60); `storm_end = pre_storm + storm` (540); `prelim = storm_end + prelim_report` (570); `close = round_len = pre_storm + storm + aftermath` (780); `final = close - final_report` (740); `speed = 23400 / round_len` (30).
- `phase(t)` is pre_storm on `[0, storm_start)`, storm on `[storm_start, storm_end)` and aftermath on `[storm_end, close)`. Anything else gives closed, and `t is None` gives unknown.
- `cost_phase(t)` walks `cost.phases` (§8). Before the first phase it returns `"open"`; after the last it returns `"after"`. On the research timing it must equal r_sim's `phase_of` for every `t`.
- `market_to_t` parses an ISO time with no zone (years like 1802 are valid) and returns `(h*3600 + m*60 + s - 34200) / speed`. A trailing `Z` or offset is stripped.
- **`diff_case`** keeps value differences and timing apart:
  - a per-company difference (sites, weights, kind, `about` text) flags that symbol plus every ETF that contains it; a limit difference flags that symbol; a basket difference flags that ETF;
  - a difference in the map, islands, outlooks or any other top-level key except `timing` flags all 12;
  - a `timing` difference sets `timing=True` and flags no symbol. It is handled by the live gate (§4.D cli) and by `caseok timing`, because the engine hardcodes the 480-s storm (`status()`) and the measured cost phases assume the research timing.

**`clock.py`**
```python
class ClockModel:
    def __init__(self, schedule: Schedule, cfg: Config, log: EventLog): ...
    def observe_feed(self, market_time: str, round_: int | None, recv_mono: float, kind: str, season_epoch: int,
                     phase: str | None = None) -> None
        # kind: "storm" (storm observation), "news" (other news), "snapshot" (phase = the snapshot's phase)
    def observe_server(self, st: ServerTime, sent_mono: float, recv_mono: float) -> None
    def on_season_reset(self, epoch: int, comp_id: str | None) -> None
    def read(self, now: float) -> ClockReading
    def t_to_mono(self, t: float, now: float) -> float | None   # mono when this round reaches real second t
    def round_events(self) -> list[tuple[int, int]]             # (epoch, round) newly current since the last call
```
Semantics:
- **Trading-phase rule** (the skeleton's `_market_day`). A snapshot whose `phase` is not pre_storm, storm or aftermath never sets the round, the date or an anchor sample; it only feeds the pause rule below. In the recording, round-1 snapshots after the close read `1802-05-10T16:52:30`, then `1802-05-11T01:55:00`: neither changes the reading. News messages are always trading-phase. A server sample sets an anchor only while its `state` is running.
- **Samples.** Every trading-phase observation gives a sample `offset = recv_mono - t(market_time)`. A server sample uses `(sent + recv)/2`. A storm observation's market time is exact. A snapshot time has 1-real-second resolution.
- **Anchor.** Keep the last `clock.offset_window` (30) samples per kind for the current (epoch, round). The feed offset is the **minimum** offset among storm samples from the last 60 s, because delivery only adds delay. Source precedence: (1) server, if a sample is younger than `clock.server_fresh_s` (3 s), using the median of its last 5 offsets; (2) storm feed, if younger than `clock.feed_fresh_s` (10 s); (3) snapshot or other news, if younger than `clock.snapshot_fresh_s` (35 s); (4) otherwise extrapolate from the last anchor for at most `clock.max_extrapolate_s` (40 s), quality `"extrapolated"`; (5) after that, quality `"unknown"` and `known=False`. The server tick runs about 0.05% slow, and anchoring to recent storm frames absorbs that.
- **Round and date** come from the newest trading-phase observation. Within an epoch the round only increases: a lower round is ignored for the reading (log `clock_round_back`). A new anchor set starts only when the round increases or after a season reset. A different date for the current round is logged `clock_date_mismatch` and ignored (date changes are the normalizer's season trigger (d)).
- **`round_events()`** returns each (epoch, round) the first time it becomes current, including the first round after startup and after a season reset. The core turns each into exactly one ROUND_START (§4.D). Pauses, closed snapshots and date mismatches never produce one.
- **running.** If a server sample is fresh, `running = (state.lower() == "running")`; any other value means stopped. Otherwise `running` is true when the last two trading-phase observations of this epoch show advancing market time. Two snapshots with the **same** market time at least `clock.pause_same_time_s` (20 s) apart mean the clock is stopped: `running = False` and `t` freezes. After a season reset, `running = False` until there is evidence (the recording's season start shows five 09:30:00 snapshots during a pause, and 09:45:00 only after the open).
- `t = now - offset` while running, or the frozen value while stopped. `phase = schedule.phase(t)`, and it is CLOSED when `t < 0` or `t >= close`.
- Log a `clock` event whenever the quality or anchor source changes, or the offset jumps by more than 0.25 s.

**`normalize.py`**
```python
class FeedNormalizer:
    epoch: int; max_seq: int; wm: int; pending_reset: str | None    # epoch starts at 0; max_seq = wm = -1
    def __init__(self, cfg: Config, log: EventLog): ...
    def ingest(self, sf: SourceFrame) -> list[MarketEvent]
    def poll(self, now: float) -> list[MarketEvent]              # release held snapshots
    def force_season_reset(self, reason: str, now: float) -> SeasonResetEvent          # confirmed: "comp_id" | "operator"
    def confirm_season_reset(self, reason: str, now: float) -> SeasonResetEvent | None # pending -> confirmed; None if none
    def reject_season_reset(self, now: float) -> None                                  # pending -> merged back
    def phase_hint(self) -> str | None      # last phase seen in the forwarded stream
    def gaps(self) -> list[tuple[int, int]] # runs of seqs <= max_seq never forwarded (a snapshot may cover them)
    def stats(self) -> dict                 # frames by type and conn, dup_news, dup_trades, seq_gaps, stale_books,
                                            # snapshots_in / relevant / redundant, resets by reason, provisional resets
```
Semantics. **Port `FeedMerger` from `bot_skeleton/feed.py`** (`ingest`, `poll`, `_news`, `_snapshot`, `_season_change`, `_note`, `_advance`, `_market_day`, `_server_ts`); it is the reference implementation and the changes below are the only intended differences.
- **One stream from every connection.** Frames from all sources (`SourceFrame.conn` "ws0#3", "ws1#1", "rec#2") go through the one normalizer in arrival order. Every rule below is per stream, not per connection, so the first copy is forwarded and later copies are dropped.
- **Frame types.** Control frames become `ConnEvent`; `"orderbook"` becomes `BookEvent` (levels from `{price, quantity}`, None entries skipped); `"quote"` becomes `QuoteEvent` (bid or ask may be None); `"trade"` becomes `TradeEvent`; `"heartbeat"` becomes `HeartbeatEvent`; `"subscription_ack"` is logged only; `"news"` with a dict `message` goes to the snapshot path for snapshots and to the news path otherwise; a bare message with `seq` and `round` (research feed lines) is treated as news; unknown frames are counted and dropped.
- **Books and quotes:** per symbol and frame type, a frame whose timestamp (parsed as `_server_ts`) is not newer than the last forwarded one is dropped and counted `stale_books`.
- **Trades** are deduplicated by `trade_id` in a bounded LRU of `feed.trade_id_cache` (50,000), kept across seasons; a trade without an id uses (symbol, timestamp, price, quantity). The recording re-sends 37 after reconnects.
- **News dedup key:** `("seq", seq)` for every type except `announcement` and `news`, which use `("ann", seq, type, headline)`, because the packet's announcement shape carries `"seq": 0` and would otherwise collide with `storm_formed` seq 0. A message without a seq uses its canonical JSON. The normalizer stores a **fingerprint** (type, round, storm, time) for every forwarded key.
  - A message whose key was seen this epoch with the same fingerprint is a **duplicate**: dropped and counted `dup_news`. A duplicate never triggers anything (the packet's "a message arrives two times after a network retry").
  - A message whose key was seen with a different fingerprint is a **conflict**, which only a new season produces. It is checked against the season triggers below; if none applies, it is dropped, counted `seq_conflict`, and alerted after 3 within 60 s.
  - A message with an unseen key is forwarded, even when its seq is far below `max_seq`: that is a late delivery filling a gap, never a new season.
  - The seq of every forwarded message (any type) goes into `seen_seqs`, which drives `max_seq`, `wm` and `gaps()`; `round` is converted to int; `seq_gaps` counts a forwarded seq above `max_seq + 1`.
- **High-water mark `wm`:** every seq ≤ wm was forwarded or is covered by a forwarded snapshot. After each forwarded seq, `while wm + 1 in seen_seqs: wm += 1`.
- **Snapshots.** Emit `SnapshotEvent(stage="arrived", engine_relevant=False)` at once (for the clock, the phase and SeasonInfo). Drop a snapshot with the same (last_seq, time) as one already held or released. Hold each one for `feed.snapshot_hold_s` (1.0 s); on `poll` after the hold, emit `stage="released"`:
  - `engine_relevant = last_seq > wm`. When true, set `wm = last_seq` and advance, so the snapshot recovers whatever was lost below `last_seq`.
  - The literal rule "above the highest seq forwarded" never recovers anything on one connection, because the seqs up to `last_seq` always arrive before the snapshot (TESTNET_FINDINGS "Fix verification").
  - On the recording, the relevant set is exactly last_seq 695 (the join), 764 (s224 advisory #5), 250, 416 and 828 (s225 drops 3-5), as with the skeleton's merger.
- **Season triggers.** They are checked before the message updates any state, in the order (b), (a), (c), (d), and the first that applies wins. A duplicate never triggers, and neither does any `announcement` or `news` message:
  - (a) `storm_formed_r1`: a round-1 `storm_formed` that is not a duplicate, with `max_seq >= 50` and `seq < max_seq - feed.season_reset_seq_drop` (50). No other message triggers on a low seq: not an announcement, not news, not a storm observation.
  - (b) `snapshot_last_seq`: a snapshot with `last_seq == -1` while `max_seq >= 0` or `wm >= 0`.
  - (c) `round_decrease`: a round-1 news message that is a conflict, or a round-1 trading-phase snapshot, while this epoch's highest round is ≥ 2. A round-1 message delivered late in round 2 has an unseen key and does not trigger.
  - (d) `date_change`: a trading-phase message (a non-duplicate news message or a snapshot) whose (round, market date) disagrees with the date recorded for that round in this epoch (`_market_day`; closed-phase snapshots are never dated).
  - (e) `force_season_reset("comp_id" | "operator")`, called by the core.
- **Confirmed resets** — (b), (e), and (a), (c), (d) when `feed.provisional_feed_resets` is false: clear the epoch state (dedup keys, `seen_seqs`, `max_seq = wm = -1`, held snapshots, rounds, dates, the round-1 `storm_formed`, book and quote timestamps; the trade LRU stays), increment `epoch`, and emit `SeasonResetEvent(provisional=False)` **before** the triggering event.
- **Provisional resets** — (a), (c) and (d) when `feed.provisional_feed_resets` is true (the default, all modes):
  - save the epoch state, then clear it as above, so a new season's messages are not dropped as duplicates; `epoch` is unchanged; set `pending_reset = reason`; emit `SeasonResetEvent(reason, new_epoch=epoch + 1, provisional=True)` before the triggering event;
  - while pending, a snapshot with `last_seq == -1`, or a trading-phase snapshot of the trigger's round and date with `last_seq < saved max_seq - season_reset_seq_drop`, confirms it: `epoch += 1`, `pending_reset = None`, and emit `SeasonResetEvent(reason, provisional=False)` before that snapshot's events;
  - `confirm_season_reset(reason)` (the core, on a new comp_id or `season new`) confirms it the same way and returns the event;
  - `reject_season_reset()` (the core, on timeout or `season keep`) merges the saved state back: dedup keys and `seen_seqs` are the union, `max_seq` and `wm` are recomputed, rounds, dates and the round-1 `storm_formed` are the saved ones. It emits nothing;
  - another provisional trigger while one is pending is ignored and counted.
- **Repeated −1 snapshots** in a pause do not re-trigger, because `max_seq` and `wm` are −1 after the reset. A comp_id reset followed by the −1 snapshot is therefore one reset; the reverse order is merged by the core (§4.D).
- `phase_hint()`: the last snapshot `phase`; otherwise "storm" after storm, track, storm_formed, advisory or impact, and "aftermath" after storm_dissipated or damage_report (the skeleton's `_note`). The core uses it for the watchdog when the clock phase is unknown.

**`sources.py`**
```python
NETWORK_ENABLED = False
class WsSingleSource:      # MarketDataSource; one per entry of feed.connections
    def __init__(self, cfg: Config, key: str, symbols: Sequence[str], timesrc: TimeSource, log: EventLog,
                 watchdog_s: Callable[[], float], name: str = "ws0", local_addr: tuple[str, int] | None = None,
                 connect: Callable[..., Any] | None = None): ...   # tests inject connect; None = websockets (gated)
def build_sources(cfg: Config, key: str, symbols: Sequence[str], timesrc: TimeSource, log: EventLog,
                  watchdog_s: Callable[[], float], connect: Callable[..., Any] | None = None) -> list[WsSingleSource]
class RecordingSource:     # MarketDataSource, for shell replay
    def __init__(self, path: str, timesrc: TimeSource, log: EventLog, name: str = "rec", paced: bool = True,
                 t_from: float | None = None, t_to: float | None = None): ...   # received_at window, unix s
def iter_recording(path: str) -> Iterator[tuple[float, dict]]    # (received_at, frame without received_at), file order
```
- **`build_sources`** returns one `WsSingleSource` per `feed.connections` entry (default two: `ws0`, `ws1`), each with its own name, `local_addr`, connection epoch, watchdog and reconnect ladder, all feeding the one normalizer. Put the entries on different network paths when the laptop has them (Wi-Fi plus a hotspot) by setting `local_addr` to the interface address. The team limit is 25 market-data connections, browser tabs included (packet §8).
- **WsSingleSource:**
  - The URL must be `wss://` and its host must equal the host of `exchange.ws_url`; otherwise it raises `ValueError` and sends nothing.
  - Connect with `websockets.asyncio.client.connect(url, additional_headers={"X-API-Key": key}, user_agent_header=cfg UA, open_timeout=feed.ws_open_timeout_s, ping_interval=feed.ws_ping_interval_s, ping_timeout=feed.ws_ping_timeout_s, max_size=feed.ws_max_size, max_queue=None, proxy=None)`, adding `local_addr=local_addr` when it is set (passed through to the event loop's `create_connection`). Then send **exactly one** frame, immediately and in any case within 10 s: `{"action": "subscribe", "symbols": [...12...], "channels": feed.channels}`. Nothing else is ever sent (websockets answers pings itself), so the 10-frames-per-second rule cannot be broken.
  - Each received text frame is JSON-decoded and emitted with `recv_wall = time.time()`, `recv_mono = timesrc.mono()` and `conn = f"{name}#{epoch}"`. Emit `control="up"` after the subscribe and `"down"` on any exit.
  - **Watchdog:** every 0.5 s, if no frame arrived on this connection for `watchdog_s()` seconds (the core supplies it per phase, §4.D), close and reconnect. **Reconnect delays:** first `feed.reconnect_first_s` (0.25), then `feed.reconnect_every_s` (0.5) ± `feed.reconnect_jitter_s` (0.1), capped at `feed.reconnect_max_s` (2.0); never the 1-2-4-8-16 ladder.
  - The key never appears in logs; log the URL and the conn id only. D's `install_redaction` sets the `websockets` loggers to WARNING, because they log handshake headers at DEBUG.
- **RecordingSource:** emits each recorded frame whose `received_at` is in `[t_from, t_to)` with `recv_mono = timesrc.mono()`. With `paced`, it sleeps so that `received_at - first` maps to `timesrc` time; under `ScaledTime`, sleep `Δ/speed` real seconds. Each `subscription_ack` after the first becomes a `"down"` then `"up"` pair. The full recording has 6 acks, so 5 reconnects.

**`rest.py`**
```python
NETWORK_ENABLED = False
_ISSUE: object                      # module-private sentinel; referenced only in rest.py and app/cli.py (a test greps)
class LiveToken:
    """Permission to send order actions. Created only by cli after the typed phrase matched (§4.D)."""
    host: str; cfg_hash: str
    def __init__(self, host: str, cfg_hash: str, issuer: object): ...   # TypeError unless issuer is _ISSUE
class RestClient:          # ExchangeAPI
    def __init__(self, cfg: Config, key: str | None, timesrc: TimeSource, log: EventLog, live: LiveToken | None = None,
                 transport: Callable[[str, str, dict, bytes | None, float], tuple[int, bytes]] | None = None): ...
    @property
    def live(self) -> bool          # a token whose host equals the rest_base host
    def execute(self, req: ApiRequest) -> ApiResult
    def prewarm(self) -> None       # GET /competitions/time on this thread's connection (reopened if idle or stale)
class RestExecutor:
    def __init__(self, api: ExchangeAPI, workers: int, on_result: Callable[[ApiResult], None], log: EventLog,
                 kill_workers: int = 1): ...
    def submit(self, req: ApiRequest) -> None     # KILL -> kill queue; others -> PriorityQueue by (req.priority, req.req_id)
    def purge(self) -> list[ApiRequest]           # drop queued, not-started PLACE / ETF_* requests
    def freeze(self) -> None                      # purge(); later PLACE / ETF_* submits are answered at once, unsent
    def unfreeze(self) -> None
    def prewarm_all(self) -> None                 # one api.prewarm() on every worker thread, kill workers included
    def close(self, timeout: float = 2.0) -> None
def parse_positions(data: Any) -> dict[str, PositionRow]
def parse_open_orders(data: Any) -> list[OpenOrderRow]
def parse_order_ack(data: Any) -> OrderAck
def parse_time(data: Any) -> ServerTime
def parse_book(data: Any) -> tuple[tuple[BookLevel, ...], tuple[BookLevel, ...]]
```
- **Endpoints** (base `exchange.rest_base`): PLACE `POST /orders {symbol, side, order_type:"limit", quantity, price}`; CANCEL `DELETE /orders/{id}`; CANCEL_ALL `DELETE /orders/all[?symbol=S]`; GET_OPEN `GET /orders/open`; GET_POSITIONS `GET /positions`; GET_BOOK `GET /orderbook/{s}`; GET_TIME `GET /competitions/time`; ETF_CREATE/REDEEM `POST /etf/{s}/create|redeem {quantity}`; GET_CASE `exchange.case_url` (no `/api/v1` prefix).
- **Headers:** `User-Agent: exchange.user_agent` (urllib's default UA is refused), `Accept: application/json`, `Content-Type` on bodies. `X-API-Key` is added **only** when the URL is https and its host equals the host of `rest_base`; a `case_url` on any other host is fetched without the key. A non-https URL raises `ValueError` before any I/O.
- **Transport:** the default is `http.client.HTTPSConnection`, keep-alive, one per worker thread (thread-local), dropped on any error. Before each use, a connection idle for more than `exchange.idle_recycle_s` (10 s), or whose socket reads as closed (a non-blocking `MSG_PEEK` returns EOF), is closed and reopened. Plain urllib opens a fresh TLS connection per call, which costs 100-300 ms at 740 s. Timeouts are `exchange.timeout_order_s` (2.0) for order actions and `timeout_get_s` (3.0) for GETs. `RequestLimiter(rate.requests_per_s_cap)` gates every request.
- **`ApiResult.sent`** is False when the request provably never reached the exchange: dry-run, purged, frozen, or a failure before any request byte was written (connect or TLS handshake). Otherwise it is True, even on a timeout.
- **Retries:**
  - An order action that failed before any byte was written is retried once on a fresh connection; if that fails too, the result has `sent=False`.
  - Once the request was written, order actions are **never** retried, with one exception: a CANCEL_ALL at KILL priority, which is idempotent, is retried once on a fresh connection after a transport error.
  - GETs are retried `exchange.get_retries` (1) times on transport errors.
- **Kill workers:** `kill_workers` (1) dedicated threads, each with its own keep-alive connection, serve only the KILL queue, so a kill's CANCEL_ALL never waits behind in-flight POSTs.
- **`freeze`:** queued PLACE, ETF_CREATE and ETF_REDEEM requests are removed and answered through `on_result` with `status=None, sent=False, error="purged"`; until `unfreeze`, new ones are answered the same way with `error="frozen"`. Cancels and GETs are unaffected.
- **Network gate:** `NETWORK_ENABLED` guards only the default transport (and, in `sources.py`, only the real websockets connect). An injected `transport` or `connect` bypasses it, which is how tests run.
- **Dry-run** (no valid `LiveToken`): order actions never reach the transport. The result is status 200 with `data={"order_id": f"dry-{n}", "status": "dry_run"}`, `parsed=OrderAck("dry-n", "dry_run", 0, qty, None)`, `dry_run=True` and `sent=False`, and `dry_order` is logged with the body that would have been sent. GETs still go to the network (host dry-run) or to the injected transport. The core and the order manager learn the mode only from `ApiResult.dry_run`.
- **Prices:** buys round **down** and sells **up** to `exchange.price_decimals` (2); the exchange accepts up to 6, so 2 is also safe under a 0.01 tick. `quantity` is an int ≥ 1.
- **Parsers** are tolerant, as in the skeleton's `g()`. Positions may be a list or a dict keyed by symbol, with keys quantity/position/qty, avg_price/average_price/cost_basis, mark/mark_price, realized_pnl and unrealized_pnl. Ack keys are order_id/id, status, filled_quantity/filled, remaining_quantity/remaining and avg_fill_price/average_price/fill_price. A 2xx PLACE body without an order id parses to `OrderAck(order_id=None, ...)`. The time body is `{competition_id, state, fictional_time, elapsed_active_seconds}`. Unknown shapes return empty results and log `parse_warn` with at most 300 characters of the body.
- **Redaction:** the key never appears in an `ApiResult`, a log line or an exception text. Every error text passes through `redact()` (the key replaced by `***`) before it is stored or raised.

**`ratelimit.py`**
```python
class ActionLimiter:
    def __init__(self, cap: int, window_s: float, floors: Mapping[Priority, int], kill_overdraft: int, max_cap: int): ...
    cap: int    # property, read/write (console `b N`); the setter clamps to [0, max_cap]; max_cap = team cap - 10
    def try_acquire(self, priority: Priority, n: int, now: float, tag: str = "") -> list[Ticket]
        # n tickets if granted; [] if denied (all-or-nothing, nothing recorded)
    def available(self, now: float) -> int
    def used(self, now: float) -> int
    def floor(self, priority: Priority, now: float) -> int
    def reserve(self, start: float, end: float, floors: Mapping[Priority, int]) -> None   # raise floors in [start, end)
    def reserved(self, now: float) -> bool                    # a reservation window contains now
    def clear_reservations(self) -> None
    def on_throttled(self, now: float, freeze_s: float) -> None
    def seconds_until(self, priority: Priority, n: int, now: float) -> float
    def ledger(self) -> list[float]; def restore(self, ledger: Sequence[float]) -> None   # mono times of granted actions
    def stats(self, now: float) -> dict
class RequestLimiter:
    def __init__(self, per_s: int): ...
    def wait_needed(self) -> float                # thread-safe; 0.0 means go (and records the request)
```
- A request of priority P for n actions is granted iff `available - n >= floor(P)`.
- KILL may overdraw the cap by `kill_overdraft` (20): `used + n <= cap + 20`, and the team cap of 180 is never reached.
- A sequence that never has more than `cap` actions in any `window_s` (61 s) window satisfies a fixed-minute counter, a sliding 60-s window and a token bucket alike.
- `on_throttled` freezes everything except KILL for `freeze_s` (5 s), then pads the ledger with entries at `now` until `available == floor(TAKE)`.

### 4.B State and execution (group B)

**`state/books.py`**
```python
class BookStore:
    def __init__(self, symbols: Sequence[str], cfg: Config): ...
    def on_book(self, ev: BookEvent) -> None; def on_quote(self, ev: QuoteEvent) -> None; def on_trade(self, ev: TradeEvent) -> None
    def view(self, symbol: str, now: float) -> BookView; def views(self, now: float) -> dict[str, BookView]
    def reset(self) -> None
```
- The touch comes from whichever of quote or orderbook has the newer `server_ts`, and levels from the latest orderbook.
- `mid = (bid + ask) / 2` when both exist, else None. `half_spread_bp = (ask - bid) / 2 / mid * 1e4`.
- `empty` means both sides are None. The recording has 72 empty books and 74 quotes with None at closes and season boundaries, so this is normal.
- `wall_only = half_spread_bp > guards.opening.max_half_spread_bp`.

**`state/positions.py`**
```python
class PositionBook:
    pos: dict[str, int]; cash: float; fills: list[Fill]
    def __init__(self, symbols: Sequence[str]): ...
    def apply_fill(self, f: Fill, update_pos: bool = True) -> None    # pos += sign*qty (if update_pos); cash -= sign*qty*price
    def note_place(self, symbol: str, sent: bool) -> None             # OM: PLACE handed out (sent=True) or its result arrived
    def quiet_stamp(self) -> dict[str, tuple[int, int]]               # symbol -> (PLACEs in flight, action counter)
    def set_exchange(self, rows: Mapping[str, PositionRow], stamp: Mapping[str, tuple[int, int]], now: float
                     ) -> tuple[dict[str, int], frozenset[str]]       # (deltas of applied symbols, skipped symbols)
    def last_rows(self) -> dict[str, PositionRow]
    def mtm(self, mids: Mapping[str, float | None]) -> float    # cash + sum(pos*mid); a missing mid uses the last mid
    def exchange_pnl(self) -> float | None                # sum(realized + unrealized) if every row has both
    def infer_price(self, symbol: str, old: PositionRow | None, new: PositionRow, side: Side, qty: int) -> float | None
    def pnl_by_strategy(self, marks: Mapping[str, float]) -> dict[str, float]   # sum q*(mark - px), signed per fill
    def reset(self) -> None
```
- **Quiet-symbol guard.** A `/positions` row can be stale against fills already applied from acks, and a fill can reach the row before its ack reaches us. The Reconciler takes `quiet_stamp()` when it starts a cycle. `set_exchange` applies a symbol's row only if that symbol had no PLACE in flight at the stamp and its action counter has not moved since (every `note_place` call increments it). Any other symbol is skipped this cycle and keeps its internal position, which the acks keep current. A symbol skipped continuously for `risk.pos_skip_alert_s` (5 s) raises the alert `positions_stale`.
- `infer_price`: when |pos| grew without changing sign and both rows have `avg_price`, `price = (new.avg*new.qty - old.avg*old.qty) / (new.qty - old.qty)`; when |pos| shrank and both rows have `realized`, `price = old.avg + (Δrealized / qty) * sign(old.qty)`; otherwise None.

**`state/orders.py`**: `OrderStore(epoch: int)`.
- `new(**fields) -> OrderRecord` assigns `local_id = f"e{epoch}-{n}"` and `remaining = qty`. `transition(rec, state, now, **updates)` raises `ValueError` on an edge not in `TRANSITIONS` (§5.3); a same-state update (OPEN → OPEN) is allowed.
- Lookups `live(symbol=None)`, `by_order_id(id)`, `by_key(key)`, `by_local(local_id)`; `working_summary(symbol) -> WorkingSummary`; `prune(now, keep_s=120)` drops older terminal records; `state_dict()` and `load_state(d)` cover live records and the counter; `drop_dry() -> int` removes every dry record.

**`state/limits.py`**:
- `resting_qty(orders, symbol, side) -> int` sums `remaining` over `LIVE_STATES`. NEW and SENDING count their full `qty`, UNKNOWN counts its full `qty`, and CANCEL_* states count `remaining` until reconcile resolves them.
- `room(limit, position, side, resting) -> int` is `max(0, limit - position - resting)` for buys and `max(0, limit + position - resting)` for sells.
- `strategy_room(case_limit, scaled_limit, position, side, resting) -> int` is `min(room(case_limit, ...), room(scaled_limit, ...))`.
- The **exchange** counts position plus same-side resting orders against the **case** limit, so every order is clamped to the case room. Strategy sizing uses the **scaled** limit `L_s = strategy_limits(case, cfg)[sym]`: targets never exceed ±L_s, a RestIntent with `qty=None` takes `strategy_room`, and `risk.max_clip_frac` and `risk.max_snipe_frac` multiply L_s.

**`exec/risk.py`**
```python
class RiskManager:
    def __init__(self, cfg: Config, case: CaseInfo, schedule: Schedule, log: EventLog): ...   # L_s via strategy_limits
    @property
    def mode(self) -> RiskMode     # FROZEN if killed; NO_NEW_RISK if any hold is active; else NORMAL
    def hold(self, name: str, now: float, until: float | None = None, sticky: bool = False, reason: str = "",
             exempt: frozenset[str] = frozenset()) -> None     # exempt: strategy names this hold does not block
    def release(self, name: str, now: float) -> None; def holds(self, now: float) -> dict[str, dict]
    def kill(self, reason: str, now: float) -> None; def killed(self) -> bool; def unkill(self, now: float) -> None
    def check(self, rec: OrderRecord, book: BookView, position: int, room: int, clock: ClockReading,
              feed: FeedHealth, now: float) -> tuple[int, str]     # approved qty (0 = reject) and reason
    def on_result(self, res: ApiResult, now: float) -> None         # 401/403, 429, 5xx streaks, reject counting
    def on_mtm(self, mtm: float, round_: int | None, now: float) -> None
    def on_fill(self, f: Fill, now: float) -> None                   # fill sanity
    def on_round_start(self, round_: int, mtm: float, now: float) -> None
    def disabled(self) -> frozenset[str]; def disable(self, sym: str, reason: str, now: float) -> None; def enable(self, sym: str) -> None
    def state_dict(self) -> dict; def load_state(self, d: dict) -> None
```
**`check` rules, in order:**
1. FROZEN rejects everything.
2. Holds: an order is blocked by every active hold whose `exempt` set does not contain `rec.strategy`. A blocked order passes only with `reduce_only`, or if it reduces |position| by at most |position|.
3. A disabled symbol passes only reducing orders.
4. `clock.running` must be true and the phase not CLOSED or UNKNOWN.
5. qty ≥ 1, and qty ≤ `risk.max_clip_frac` × L_s (0.5 L_s) for TAKE and HEDGE, or ≤ `risk.max_snipe_frac` × L_s (2 L_s) for SNIPE. Clamp, never reject.
6. qty ≤ room (case room, §4.B limits), clamped.
7. The book must have a mid.
8. The order is **marketable** when a buy is priced ≥ ask or a sell ≤ bid; a missing touch counts as marketable.
   - A marketable TAKE or HEDGE order needs `|px - mid| <= risk.max_marketable_from_mid * mid` (1.5%).
   - Every order needs `|px - mid| <= risk.fat_finger_frac * mid` (25%), or `risk.snipe_band_frac` (60%) for SNIPE.
9. No marketable order before `schedule.at(risk.block_marketable_before)` (3 s into the round), and none when `book.wall_only`.
10. In the storm phase, a non-reducing order is rejected when `feed.last_storm_obs_age_s > feed.storm_obs_stale_s` (6 s).
11. At most `risk.max_resting_per_symbol` (8) live orders per symbol.
12. Self-cross: a buy priced ≥ any of our live sells in that symbol, or a sell ≤ any of our live buys, is rejected. Self-trade prevention would cancel our resting order.

**Breakers:**

| Breaker | Trigger | Action |
|---|---|---|
| `drawdown` | The round's peak MTM minus current MTM exceeds `risk.drawdown_usd` (20,000). MTM uses `exchange_pnl()` when `risk.pnl_source` is "auto" and it is available, else the internal book at book mids. The peak resets at each round start. | Sticky hold; the operator releases it |
| `auth` | Any 401 or 403 | Sticky hold for everything, alert `auth_failed`; never counted as a per-symbol reject |
| `http_429` | Any 429 | Hold for `rate.throttle_freeze_s` (5 s); the core also calls `limiter.on_throttled` |
| `api_errors` | `risk.http_5xx_streak` (3) 5xx or transport errors in a row | Hold for `risk.http_5xx_hold_s` (10 s) |
| `unknown_orders` | Set by the core when the oldest UNKNOWN or CANCEL_UNKNOWN is older than `risk.unknown_hold_s` (3 s), with `exempt={"s1r3"}` (S1-r3 orders are bounded by S ∓ ε and an UNKNOWN one still counts its full qty in room) | Released automatically once none remain |
| Fill sanity | `risk.fill_sanity_n` (3) consecutive TAKE or HEDGE clips (each order judged once, on its first priced fill) in a symbol with `quality != LIMIT_BOUND` and slippage `side*(price - ref_mid)/ref_mid*1e4 > risk.fill_sanity_mult * expected_cost_bp` (3×), where `expected_cost_bp` is `walk + fo_allow` (§12 item 1) | Disable the symbol (sticky) |
| Rejects | `risk.reject_disable_n` (5) 4xx rejects in a symbol within 30 s, excluding 401, 403, 429 and "market closed" texts | Disable the symbol |

**`exec/reconcile.py`**
```python
class Reconciler:
    def __init__(self, store: OrderStore, positions: PositionBook, cfg: Config, log: EventLog): ...
    def begin_cycle(self, now: float) -> int                              # also takes positions.quiet_stamp()
    def on_result(self, res: ApiResult, now: float) -> list[Fill] | None   # None until both halves of the cycle arrive
    def unknown_age(self, now: float) -> float | None
    def consecutive_failures(self) -> int
    def last_open_rows(self) -> list[OpenOrderRow] | None                 # open rows of the last completed cycle
```
The core issues `GET_OPEN` and `GET_POSITIONS` with the same `cycle`. When both are back and OK, apply open orders first, then positions. "Sent after X" below uses the `sent_mono` of the cycle's GET_OPEN.
- **Matched by `order_id`:** update `remaining` and `last_seen_open_mono`, and reset `missing_cycles`. The state is kept, except that UNKNOWN and SENDING become OPEN, and a CANCEL_UNKNOWN record seen in a GET sent after `cancel_sent_mono + risk.unknown_grace_s` becomes OPEN again (the cancel did not take; the OM re-queues it if the cancel is still wanted). CANCEL_PENDING and CANCEL_ACKED keep their state: a GET sent before the DELETE routinely shows the order. If `remaining` dropped, the difference is a partial fill, priced by `infer_price` (INFERRED) or else at the limit (LIMIT_BOUND).
- **Adoption:** an unclaimed open row is matched to an UNKNOWN **or SENDING** record with the same symbol and side, price within 1e-6 and remaining ≤ qty, oldest first. The record takes the row's `order_id` and becomes OPEN; the later PLACE ack is applied idempotently (§4.B ack table). This prevents a duplicate "foreign" record for an order whose ack is still in flight.
- **Absence** counts only when the GET_OPEN was sent after the record's `acked_mono` (OPEN and CANCEL_* records) or after `sent_mono + risk.unknown_grace_s` (1.0 s; UNKNOWN). SENDING records are never absent.
  - Attribute this cycle's position delta per symbol and side to the absent records in send order.
  - A CANCEL_ACKED record resolves at its **first** qualifying absent cycle: FILLED for the part the delta explains, CANCELED for the rest.
  - An OPEN, CANCEL_PENDING, CANCEL_UNKNOWN or UNKNOWN record whose remaining is fully explained by the delta resolves FILLED in this same cycle; otherwise it resolves at `missing_cycles == 2`, FILLED for the explained part and CANCELED for the rest.
- **Positions:** `positions.set_exchange(rows, stamp, now)`. Reconcile fills update cash only when their symbol's row was applied (the row already holds the position), and cash plus position when it was skipped. Skipped symbols get no delta attribution this cycle.
- **Foreign fills:** position change that no record explains is held for one more cycle, because a fill can reach `/positions` before its ack or before `/orders/open` shows the lower remaining. If the next completed cycle still does not explain it, emit `Fill(strategy="foreign", source="foreign")` and the alert `position_mismatch`.
- **Open rows we do not know** (after adoption) become `style="foreign"` records in state OPEN. They count toward room, and the kill switch and `cx` cancel them.
- **Dry records** (`dry=True`) are excluded entirely. They never fill and resolve only through their own cancels.
- A failed GET skips the cycle. A cycle still outstanding after 2 s is dropped. After `risk.reconcile_fail_hold_n` (5) consecutive failures the core holds `reconcile_down`.

**`exec/order_manager.py`**
```python
class OrderManager:
    def __init__(self, cfg: Config, case: CaseInfo, store: OrderStore, positions: PositionBook, books: BookStore,
                 risk: RiskManager, limiter: ActionLimiter, log: EventLog, next_req_id: Callable[[], int]): ...
    def set_intents(self, intents: Sequence[Intent], now: float) -> None
    def step(self, now: float, clock: ClockReading, feed: FeedHealth) -> list[ApiRequest]
    def on_result(self, res: ApiResult, now: float) -> list[Fill]       # also applies ack fills to positions
    def kill(self, now: float) -> list[ApiRequest]
    def kill_followup(self, open_rows: Sequence[OpenOrderRow] | None, now: float) -> list[ApiRequest]
    def kill_status(self) -> dict          # {"pending": n, "actions": n, "complete": bool, "since": mono}
    def cancel_all(self, priority: Priority, now: float, symbol: str | None = None, reason: str = "") -> list[ApiRequest]
    def working(self) -> dict[str, WorkingSummary]
    def drop_dry(self) -> int
    def state_dict(self) -> dict; def load_state(self, d: dict) -> None
```
There is no dry-run argument: a record is marked `dry` when its PLACE result has `dry_run=True`.

**`set_intents`:** a `TargetIntent` replaces the symbol's active target, stamped `now`; a target older than `orders.target_ttl_s` (0.6 s) is ignored. `RestIntent`s and `CancelIntent`s are queued for the next `step`.

**Cancel priority.** Every cancel (CANCEL or CANCEL_ALL) other than the kill's takes `p = min(q, HEDGE)` in `Priority` order, where q is the intent's priority (for a CancelIntent) or the record's (for a remainder cancel); so a cancel is never below HEDGE, and S1-r3 and announcement cancels stay SNIPE. When `limiter.reserved(now)` is true (round 3 from `s1r3.block_from`), a HEDGE cancel is raised to SNIPE. `cancel_all(priority, ...)` uses the priority it is given; the core passes KILL or SNIPE (§4.D).

**`step`** builds requests in this order:
1. **Kill:** if `risk.killed()`, return only what `kill` and the kill follow-up produce.
2. **Cancels:** the queued CancelIntents, the `cancel_all` requests and the remainder cancels (step 3) resolve to records. A record that already has a cancel queued or in flight is not cancelled again, so overlapping CancelIntents produce one action per record.
   - Scope "symbol" always sends one `CANCEL_ALL?symbol=S`, and scope "all" one `CANCEL_ALL`; both mark every SENDING record in scope `cancel_on_ack=True`, because an order still in flight has no id and may rest after the cancel.
   - Other scopes send one `CANCEL_ALL?symbol=` when every live record of the symbol is in scope and there are ≥ 2, otherwise a per-order `CANCEL` for each in-scope record with an `order_id`. In-scope SENDING records get `cancel_on_ack=True`; UNKNOWN records without an id are left to reconcile.
   - Each cancel request takes one ticket at its cancel priority. **If the ticket is denied, the cancel stays queued** (`cancel_denied` counted) and is retried at every step; the records do not change state. Only when the request is emitted do its records move to CANCEL_PENDING, so no record is ever CANCEL_PENDING without a request.
3. **Clip remainders:** a clip record in OPEN with `remaining > 0`, confirmed resting after its send (by an ack or by `last_seen_open_mono > sent_mono`), and `now - sent_mono >= ttl_s` gets a cancel queued (priority as above).
4. **Rests**, sorted by `(priority, not marketable, -edge_usd * desired)` (marketable: a buy priced ≥ ask or a sell ≤ bid, or a missing touch). For each `RestIntent`:
   - `desired = qty` if given, else `strategy_room(case L, L_s, position, side, resting excluding this key) + the key's live remaining`; with `qty` given, `desired` is still capped by that room;
   - cancel live orders of the key at a different price (|Δ| ≥ `orders.price_eq_tol`, 0.005);
   - if the key's live remaining at this price is below `desired - max(1, rest_tolerance_frac*desired)`, place the difference, clamped to the case room and passed through `risk.check` (one ticket at the intent priority), with at most `rest_max_orders_per_key` (2) live orders per key; if it exceeds desired by more than that tolerance, cancel the key's smallest orders.
5. **Targets**, sorted by `(priority, -edge_usd_per_share*clip_qty)`. For each fresh target:
   - skip while any clip record of the symbol is NEW, SENDING, OPEN, UNKNOWN, CANCEL_PENDING or CANCEL_UNKNOWN (a CANCEL_ACKED record does not block: §5.3), or if `now - last_clip_sent[symbol] < clip_interval_s`;
   - `need = target - exposure`, where exposure is the position plus the live clip buys minus the live clip sells over the same states. With `reduce_only`, clamp `need` so the position moves toward 0 without crossing it;
   - the side follows the sign of `need`; the limit is `limit_buy` or `limit_sell`, and the target is skipped if it is None;
   - `qty = min(|need|, clip_qty, case room(side))`, then `risk.check`. A rejection is skipped with a rate-limited log (once per symbol and reason per 10 s);
   - acquire one ticket at the intent priority; if denied, skip and count `budget_denied`;
   - create the record (style "clip", ttl, ref_mid, expected_cost, edge), emit PLACE, call `positions.note_place(symbol, sent=True)` and set `last_clip_sent[symbol] = now`.
6. **Room is recomputed after every PLACE** in the step: the new record (SENDING) counts at once in `resting_qty`.
7. Every PLACE, CANCEL and CANCEL_ALL takes **one** ticket. Cancels skip `risk.check` but still need a ticket.
8. In the returned list, cancels come before places, and places are sorted by priority.

**Ack handling (`on_result`).** Every PLACE result also calls `positions.note_place(symbol, sent=False)`. A result for a record that reconcile has already advanced (adopted, filled) applies only what is new.

| Result | Effect |
|---|---|
| PLACE `dry_run` | OPEN with `dry=True` |
| PLACE `sent=False` | REJECTED with the error ("purged", "frozen", "not_sent") |
| PLACE 2xx with an `order_id` | OPEN, or FILLED if the ack reports remaining 0 or status filled. Ack fills become `Fill(source="ack")`, EXACT if `avg_fill_price` is present, else LIMIT_BOUND, and are applied to the positions. If the record has `cancel_on_ack`, queue its cancel now (SNIPE) |
| PLACE 2xx without an `order_id` | FILLED if the ack reports remaining 0 or status filled; otherwise UNKNOWN and the alert `ack_no_id` |
| PLACE 401 / 403 | REJECTED; `risk.on_result` holds `auth` |
| PLACE 429 | REJECTED; `risk.on_result` holds, the core throttles the limiter |
| PLACE other 4xx | REJECTED with the body text |
| PLACE 5xx or None (sent) | UNKNOWN |
| CANCEL 2xx | CANCEL_ACKED. Dry records become CANCELED at once |
| CANCEL 404 | Leave the record for reconcile (absence) |
| CANCEL 5xx or None (sent) | CANCEL_UNKNOWN |
| CANCEL `sent=False` | The record returns to its previous state and the cancel is re-queued |
| CANCEL_ALL 2xx | Every in-scope record with an order id becomes CANCEL_ACKED; in-scope SENDING records keep `cancel_on_ack` |

**Kill.**
- `kill(now)` records the kill set (every live record and its state) and emits one `CANCEL_ALL` (no symbol) at KILL priority, with the overdraft. The core freezes the executor in the same call (§4.D).
- While FROZEN, `step` emits nothing but kill follow-ups. A follow-up is one `CANCEL_ALL` at KILL, sent when (a) a PLACE ack arrives OPEN, (b) the last completed reconcile cycle (`kill_followup(open_rows, now)`) shows any open row, ours or foreign, or (c) a kill-set record is still SENDING, UNKNOWN or CANCEL_UNKNOWN. At most one kill CANCEL_ALL is in flight, they are at least `app.kill_followup_s` (0.25 s) apart, and they stop when `limiter.used + 1 > cap + kill_overdraft` (alert `kill_incomplete`).
- The kill is **complete** when every kill-set record is terminal and a reconcile cycle sent after the last kill CANCEL_ALL shows no open row; log `kill_complete` with the elapsed time.

### 4.C Models and strategies (group C)

**`models/engine_adapter.py`**
```python
def load_engine_module(engine_dir: str, filename: str = "fairvalue.py") -> ModuleType
    # importlib.util.spec_from_file_location("se_" + stem, engine_dir/filename); requires sys.dont_write_bytecode
def snapshot_fix_present(mod: ModuleType, probe_path: str, case: dict, params_path: str) -> bool
class EngineAdapter:      # FairValueProvider
    def __init__(self, cfg: Config, case: dict, timesrc: TimeSource, log: EventLog, mode: str = "thread",
                 persist_path: str | None = None, adjust_dir: str | None = None, run_id: str = "run",
                 restore: bool = False, state_factory: Callable[..., Any] | None = None,
                 library_seasons: tuple[int, int] | None = None): ...
```
- **Construction:** `SeasonState(params=engine.dir/engine.params, case=case, persist_path=..., adjust_file=<adjust_dir>/adjust_engine.json or None, library_seasons=...)`, with `engine_params_fast.json` (11 ms median, p95 46 ms). The engine resolves its own data files relative to its folder; always pass the served case dict. When `engine.library_exclude_seasons` is non-empty, load `library_all.json` once and pass `library={"storms": [s for s in storms if s["season"] not in excl]}`. `restore=True` uses `SeasonState.restore(persist_path, ...)`. `state_factory` replaces the constructor in tests.
- **Thread mode** (live): one daemon thread owns the `SeasonState`, and `submit` puts `("msgs", list, recv_mono)` on a `queue.Queue` and returns at once. The worker loop:
  1. block up to `engine.periodic_compute_s` (1 s), then drain every queued item, so a burst is coalesced;
  2. call `st.on_message(m)` per message, in order;
  3. merge the adjustment files if either changed (below), then call `fair_values(st)`, which is cheap when nothing changed because the engine caches, and which polls `adjust_engine.json`;
  4. if `st.version`, the merged adjustments or the overrides changed, build a `FairValueSnapshot`: `version` = the adapter's own counter + 1 (never reset, across seasons and restores); `engine_version = st.version`; values copied into plain dicts; `seq_hi` = max seq ingested this season; `included_mono` = recv_mono of the newest message; `compute_ms`; `diag = {"cum_damage": st.cumulative_damage(), "season_loss": st.season_loss(), "status_r3": st.status(3), "pattern": st.pattern_posterior()}`;
  5. publish the snapshot under a lock, then call the listener.
  An exception in `on_message` skips that message (log `engine_error`). An exception in compute keeps the previous snapshot, counts `consecutive_errors` and publishes a copy with `ok=False` (with a new `version`).
- **Inline mode** (sim, tests): the same steps run synchronously inside `submit`, and the listener is called before `submit` returns.
- **Snapshots** are submitted only when `engine.forward_snapshots` allows it: "relevant" (default, released snapshots with `engine_relevant`), "all" or "never".
- **Startup probe.** `snapshot_fix_present` replays `ctcbot/models/data/snapshot_probe.jsonl` through a fresh inline `SeasonState` of the given module. `tools/make_snapshot_probe.py --history DIR --out PATH` writes that file from research season 401 round 1 up to age 100, followed by one snapshot built from that state in the testnet schema (an advisory without storm, seq, round or type). C runs the script once and commits its output; no test writes it.
  - The fix is present iff the pattern posterior and all 12 means are unchanged across the snapshot (within 1e-9 and 1e-6).
  - It must return True for `engine/fairvalue.py` and False for `engine/fairvalue_orig.py` (test).
  - If it returns False or raises, force `forward_snapshots = "never"` and raise the alert `engine_snapshot_bug`. The bot then loses snapshot recovery but avoids the 16-of-24 reset errors (TESTNET_FINDINGS §1.3), and the cli refuses `--live` (§4.D).
- **Adjustment files** (with `adjust_dir`), so that no two writers share a file:
  - `adjust.json` is the analyst's, edited by hand (`{"PORT": {"mult": 1, "add": -2.0}, "_overrides": {...}}`);
  - `adjust_console.json` is written only by `set_adjustment` and `override` (the console's `f`, `m`, `a`), atomically (tmp plus `os.replace`);
  - `adjust_engine.json` is written only by the worker: when the mtime of either input changes, it merges them (per symbol and per override key, the console entry wins; `f SYM off` removes the console entry), writes the result atomically and logs `adjust_merge` with the effective entries. It is the engine's `adjust_file`; `_poll_adjust_file` re-reads it whole.
  - Without `adjust_dir`, `set_adjustment` and `override` are applied to `st` inside the worker.
- **`reset_season(epoch, persist_path)`** enqueues, in order: `st.reset()`; then what `reset()` leaves behind: `st.clear_adjustments()`, `st.overrides = {}`, `st.P = copy.deepcopy(st.P0)`, `st.announcements = []`; `st.persist_path = persist_path`; the adjustment rotation (rename `adjust.json` and `adjust_console.json` to `adjust_{run_id}_e{old}.json` and `adjust_console_{run_id}_e{old}.json`, then write `{}` to all three files); and `seq_hi = None`.
- **`health(now)`:** `oldest_pending_age_s = now - recv_mono` of the oldest message submitted but not yet in a published snapshot; `alive = consecutive_errors < engine.down_after_errors` (3) and the thread is alive.

**`models/cost_model.py`**
```python
class CostModel:
    def __init__(self, path: str, cfg: Config): ...
    def walk_bp(self, symbol: str, phase: str, x: float, side: Side | None = None) -> float
    def half_spread_bp(self, symbol: str, phase: str) -> float     # table value at the smallest x (r_sim 'hs')
    def depth(self, symbol: str, phase: str) -> float              # inner_depth_within_3pct_over_L_median
    def rho(self, symbol: str) -> float                            # replenish_storm.depth50_100.within_2s
    def entry_cost_bp(self, symbol: str, phase: str, clip_x: float) -> float   # thr_mult*(walk(clip_x) + fo_allow)
    def check_against_config(self) -> list[str]                   # |model - config table| > cost.check_tolerance_bp
```
- `walk_bp` is r_sim's `walk_cost`: piecewise-linear in `x = shares/L` over `table`. With a side, multiply by the buy/sell ratio curve interpolated over the clip points (10 sh, L/8, L/4, L/2, L). `side=None` gives the mean table.
- `fo_allow` is `cost.fo_allow_bp.stock` (36) or `.etf` (17.28, which is 0.36 × 48).

**`models/coverage.py`**
```python
CLOSURE_SYMBOLS = ("PORT", "GRID", "FERRY", "GEN", "ARCH", "REBLD")   # settle through closure time T
class StormCoverage:
    def __init__(self, cfg: Config, log: EventLog): ...
    def on_news(self, msg: dict) -> None          # storm, track, storm_formed, storm_dissipated, damage_report
    def on_snapshot(self, msg: dict) -> None      # obs, track, round, phase
    def coverage(self) -> dict[int, str]          # round -> "full" | "gapped" | "missing", for rounds 1..highest seen
    def rebuild(self, messages: Iterable[dict]) -> None
    def reset(self) -> None
```
- Per round it keeps the ages of storm observations (which carry intensity) and of position samples (observations plus the points of `track` messages and snapshot tracks). The span is `[0, end]`, where `end` is the `storm_dissipated` age if seen; else, once the round is past its storm (a damage report, or an aftermath or closed snapshot of that round), the last observation age; else the newest sample.
- **missing:** the round has no sample at all, or some stretch of the span longer than `s1r3.long_gap_s` (10 s) has no position sample. Every round below the first round seen is missing: the bot started late without the engine log, and the engine imputes those rounds' closure (`_impute_round`).
- **gapped:** not missing, but some stretch longer than `long_gap_s` has no storm observation, so intensity there is interpolated. That includes `[0, first observation]` after a mid-storm join, and the 31.7-s outage, whose positions the next track message fills.
- **full:** otherwise.
- Gaps are judged on the forwarded (merged) stream, so a gap that one connection filled does not count.
- Expected on the recording: season 224 has rounds 1-2 missing and round 3 gapped (the join at storm age 160; the track covers 0-160); season 225 has rounds 1 and 3 full and round 2 gapped.

**`models/laws.py`**
```python
def load_laws_module(path: str) -> ModuleType
class LawsCrossCheck:
    def __init__(self, cfg: Config, log: EventLog, laws: ModuleType, coverage: StormCoverage, mode: str = "thread"): ...
    def on_news(self, msg: dict) -> None; def on_snapshot(self, msg: dict) -> None
    def values(self) -> dict[str, float | None] | None
        # None until rounds 1-3 all have final reports; then per symbol, None where its inputs are incomplete
    def compare(self, S: Mapping[str, float]) -> dict[str, tuple[float | None, bool | None]]
        # sym -> (S - law, |S - law| <= laws_tol[sym]); (None, None) where the law value is None
    def rebuild(self, messages: Iterable[dict]) -> None
    def reset(self) -> None
class LawCheck:
    def __init__(self, cfg: Config, log: EventLog): ...
    def on_report(self, round_: int, stage: str, seq: int | None, fv_before: FairValueSnapshot | None,
                  books: Mapping[str, BookView], now: float) -> None
    def on_fv(self, fv: FairValueSnapshot, now: float) -> None
    def on_tick(self, books: Mapping[str, BookView], now: float) -> None
    def checks_done(self) -> int
    def failures(self) -> frozenset[str]
    def clear(self, sym: str) -> None                  # `lawok SYM`; "all" clears every symbol and the no-check state
    def passed(self, sym: str) -> bool
    def reset(self) -> None; def state_dict(self) -> dict; def load_state(self, d: dict) -> None
```
- **D and L** are the sums of the final `damage_pct` and `industry_loss_musd` per round, from news or snapshot `damage` lists. The D/L-only symbols (ISLE, AQUA, RECON, MAT, HAVEN, REIN) have a value once rounds 1-3 have final reports. A `CLOSURE_SYMBOLS` value is None while any round's coverage is missing.
- **T** per round: merge storm observations (age → x, y, r, I) with track points that fall in observation gaps, interpolating gap intensity linearly between the bracketing observations (and holding the first observed intensity before the first observation). Feed the merged path in age order to a fresh `se_laws.ClosureIntegrator` and keep that round's `weighted()`.
  - T is computed **once per round, when its storm ends** (`storm_dissipated`, the round's first damage report, or its first aftermath or closed snapshot), in a background thread in mode "thread" (one worker owned by the object, results under a lock) and synchronously in mode "inline" (sim and tests). A later message that adds samples to a finished round schedules one recomputation.
  - `values()` only reads cached results and never integrates, so nothing heavy runs on the loop at 740 s. While a round is being computed its closure symbols are None.
- **Feed:** the core sends `LawsCrossCheck` and `StormCoverage` the same forwarded stream as the engine (every forwarded NewsEvent and every released `engine_relevant` snapshot), whatever `engine.forward_snapshots` says. On restore both are rebuilt from the engine log.
- If `load_laws_module` fails, the core passes `laws=None` to the strategies and raises the alert `laws_unavailable`; S1-r3 then excludes `CLOSURE_SYMBOLS` (§6.4).
- **`LawCheck`** runs at each rounds 1-2 report:
  - `on_report` (at the report message's arrival): record each stock's mid and the engine means before (`fv_before`);
  - `on_fv`: the first fv with `seq_hi >= seq` gives the means after;
  - `on_tick` at +`law_check.horizon_s` (16 s): compare the realised mid move with `slope[stage][sym][round-1] * (μ_after - μ_before)` for symbols whose |predicted| exceeds `min_move_frac` × mid. The check fails when `fail_sign_n` (2) or more symbols miss in sign or by more than `fail_size_frac` (50%) in size; the missed symbols of a failed check join `failures()`. Log `law_check`. A check whose `fv_after` never arrived within the horizon is void and not counted.
  - `passed(sym)` is true iff (`checks_done() >= 1` or `lawok all` was given) and `sym` is not in `failures()`. A late join without rounds 1-2 therefore fails the gate until the operator types `lawok`.

**`strategies/base.py`**
```python
@dataclass(frozen=True)
class StrategyDeps:
    cfg: Config; case: CaseInfo; schedule: Schedule; cost: CostModel
    laws: LawsCrossCheck | None; law_check: LawCheck; log: EventLog
class Strategy:
    name: str = "base"
    def __init__(self, deps: StrategyDeps): ...        # self.p = deps.cfg.get(f"strategies.{self.name}"); self.enabled = self.p["enabled"]
    def decide(self, ctx: StrategyContext) -> list[Intent]
    def on_command(self, cmd: Command, ctx: StrategyContext) -> list[Intent] | None   # None = not handled
    def on_promotion(self) -> None                     # dry-run state carried into a live run (§4.D persist)
    def reset_season(self) -> None
    def state_dict(self) -> dict; def load_state(self, d: dict) -> None
def fv_usable(ctx: StrategyContext, cfg: Config) -> tuple[bool, str]
def symbol_sane(ctx: StrategyContext, sym: str, cfg: Config) -> bool
def in_window(ctx: StrategyContext, start: Sequence, end: Sequence) -> bool   # start <= t < end, clock known
def round_px(px: float, side: Side, decimals: int) -> float        # buys floor, sells ceil
def limit_of(ctx: StrategyContext, sym: str) -> int               # ctx.limits[sym], the scaled limit L_s
def clip_of(ctx: StrategyContext, sym: str, which: str) -> int    # clip_size(L_s, sizing.clip_div[which])
def blocked(ctx: StrategyContext, sym: str) -> bool               # in ctx.frozen, ctx.disabled or ctx.leadlag
```
`fv_usable` returns False when `ctx.fv` is None or not `ok`, or its `season_epoch` differs from the clock's; when `ctx.engine.oldest_pending_age_s > engine.stale_s` (6 s); when `fv.round != clock.round` during the storm and aftermath; or, in the storm phase, when `ctx.feed.last_storm_obs_age_s > feed.storm_obs_stale_s` (6 s).

`symbol_sane` is false when μ or sd is not finite, or when `|μ - mid| > max(guards.engine_sanity.sd_mult*sd, guards.engine_sanity.mid_frac*mid)` (6 sd, 15%). Insanity sticks for `clear_after_s` (10 s) after the condition clears.

**`strategies/runner.py`**
```python
class StrategyRunner:
    ORDER = ("announce", "s1r3", "flatten", "etf_leadlag", "h1", "s3", "rein_ask")   # target precedence
    def __init__(self, deps: StrategyDeps): ...
    def decide(self, ctx: StrategyContext) -> list[Intent]
    def on_command(self, cmd: Command, ctx: StrategyContext) -> list[Intent]
    def frozen(self) -> frozenset[str]            # announce's frozen set
    def leadlag_symbols(self) -> frozenset[str]   # ARCH and REBLD while etf_leadlag is enabled, else empty
    def on_promotion(self) -> None
    def reset_season(self) -> None; def state_dict(self) -> dict; def load_state(self, d: dict) -> None
    def status(self) -> dict
```
- The core fills `ctx.frozen` from `frozen()` and `ctx.leadlag` from `leadlag_symbols()` before every decision. H1, S3 and S1-r3 skip `blocked` symbols (S1-r3 with the exception in §6.4 gate (c)).
- `decide` calls every enabled strategy in ORDER, each wrapped in try/except. An exception logs `exception` with the traceback and the strategy name; `risk.strategy_exceptions_disable` (3) exceptions within 60 s disable that strategy and raise an alert.
- **Merging:** the first strategy in ORDER that targets a symbol owns it this cycle, and later targets for it are dropped. For frozen symbols, only CancelIntents and flatten's `reduce_only` targets pass (and the S1-r3 RestIntents of symbols confirmed by `s1ok`).
- **Opening guard:** drop any TargetIntent or RestIntent that is marketable (buy limit ≥ ask, sell limit ≤ bid, or the touch is missing) when `t < schedule.at(guards.opening.until)` (3 s), when the book is `wall_only`, or when the clock is not running.
- `on_command` offers the command to each strategy in turn until one handles it.

**Strategies** (`h1.py`, `s3.py`, `flatten.py`, `s1r3.py`, `rein_ask.py`, `etf_leadlag.py`, `announce.py`) each subclass `Strategy`, with `name` equal to the config key. Their exact rules are in §6. Strategies do not gate on `ctx.risk_mode`: holds are enforced per order by `risk.check`, which is what lets a hold exempt S1-r3 (§4.B).

### 4.D Application and operations (group D)

**`app/core.py`**
```python
@dataclass
class CoreParts:          # every collaborator of the core; tests replace any of them with fakes
    normalizer: Any; clock: Any; books: Any; positions: Any; store: Any; risk: Any; om: Any; reconciler: Any
    runner: Any; coverage: Any; laws: Any; law_check: Any; limiter: Any
def default_factories() -> dict[str, Callable[..., Any]]
    # name -> factory(cfg, case, ...) building the real A/B/C object; the B and C imports happen inside this function.
    # In mode "sim" (every harness run) LawsCrossCheck is built in mode "inline", so the run stays deterministic.
class BotCore:
    def __init__(self, cfg: Config, case: CaseInfo, case_diff: CaseDiff, engine: FairValueProvider,
                 timesrc: TimeSource, log: EventLog, *, mode: str, run_dir: str | None, run_id: str,
                 laws_module: ModuleType | None, cost: CostModel, restore: dict | None = None,
                 promotion: bool = False, live_label: bool = False,
                 factories: Mapping[str, Callable[..., Any]] | None = None): ...
        # mode: "host" (live exchange, dry-run or live) | "replay" | "sim"; factories override default_factories()
        # live_label: for the status line and state.json only. It grants nothing: whether an order is sent is
        # decided by the RestClient's LiveToken, and records learn dry-run from ApiResult.dry_run.
    def start(self, now: float) -> None                     # initial GET_TIME, GET_POSITIONS + GET_OPEN (cycle)
    def on_frames(self, frames: Sequence[SourceFrame], now: float) -> None
    def on_fair_values(self, now: float) -> None             # reads engine.latest()
    def on_api_result(self, res: ApiResult, now: float) -> None
    def on_command(self, cmd: Command, now: float) -> None
    def jobs(self) -> list[tuple[str, float]]
    def run_job(self, name: str, now: float) -> None
    def take_requests(self) -> list[ApiRequest]
    def take_control(self) -> list[str]                      # "freeze" | "unfreeze" | "prewarm_all", in order
    def watchdog_s(self) -> float                            # feed.watchdog_s[phase] (below)
    def shutdown(self, now: float) -> None                   # cancel_all(KILL) if any live non-dry order
    def status(self, now: float) -> dict
    def report(self) -> dict                                 # fills, actions, rejects, alerts, exceptions, holds, resets
    # public read-only attributes used by sim/report: clock, books, positions, store, om, risk, limiter, runner, season
```
- **Jobs**, in this order (the harness runs same-instant jobs in list order, so reconcile sees a fill before the tick could cancel its remainder): `("reconcile", app.reconcile_s=0.5)`, `("clock_poll", clock.server_poll_s=1.0)`, `("tick", app.tick_s=0.25)`, plus `("persist", app.persist_s=5.0)` and `("status", app.status_s=2.0)` when `run_dir` is set (not in sim).
- **`watchdog_s()`:** "closed" while the clock is known but not running (a pause: the server says paused, or the frozen 09:30:00 snapshots of a pre-season pause, which read pre_storm); otherwise the clock phase if known; otherwise `normalizer.phase_hint()`; otherwise "unknown" (§12 item 3). Table `feed.watchdog_s`: storm 3, pre_storm 8, aftermath 8, closed 35, unknown 35 (an unknown phase is most often a pause after a restart, where 29.6-s gaps are normal).
- **`on_frames`** handles one batch, which the shell has drained in full:
  1. `normalizer.ingest` each frame, then `normalizer.poll(now)`.
  2. Apply each event in order:
     - `SeasonResetEvent`: provisional → `_season_suspect(ev)`; confirmed → `_season_change(ev)` (with the merge rule below).
     - Books and quotes go to `BookStore`.
     - `NewsEvent`: `ClockModel.observe_feed` (kind "storm" for storm messages); SeasonInfo (damage reports, losses, announcements, final seq); FeedHealth (storm observation times, age gaps); `laws.on_news` and `coverage.on_news`. A rounds 1-2 `damage_report` also calls `law_check.on_report(round, stage, seq, engine.latest(), books.views(now), now)` before the batch goes to the engine. The batch's messages go to `engine.submit`, and also to the pending buffer while a season reset is pending.
     - `SnapshotEvent` arrived: `ClockModel.observe_feed(kind="snapshot", phase=ev.phase)` and SeasonInfo (damage list, phase). Released with `engine_relevant`: `engine.submit` (subject to `forward_snapshots`), `laws.on_snapshot`, `coverage.on_snapshot`, and the pending buffer.
     - `ConnEvent` updates FeedHealth.
  3. Each entry of `clock.round_events()` triggers `_round_start`.
  4. If the batch had news, a released snapshot or a round start, run one decision cycle with trigger NEWS (or ROUND_START).
- **Decision cycle:** build a `StrategyContext` with `limits = strategy_limits(case, cfg)`, `frozen = runner.frozen()`, `leadlag = runner.leadlag_symbols()`, `season` (with `coverage.coverage()` and the case state) and `budget_take = limiter.available - limiter.floor(TAKE)`; `runner.decide`, then `om.set_intents`, then `om.step(now, clock, feed)`; append the requests to the outbox; log `intent` for each non-empty intent set (compact).
- **`on_fair_values`:** `law_check.on_fv(fv, now)`, then a decision cycle with trigger FV.
- **`tick`:**
  1. release snapshots (`normalizer.poll`), read the clock, and update FeedHealth (`down_since`, ages). The feed is **down** when no connection is up, or when no frame arrived on any connection for longer than `watchdog_s()`.
  2. If the feed has been down longer than `feed.feed_down_cancel_s` (10 s): `om.cancel_all(SNIPE)` once and hold `feed_down`, in every round and phase (playbook §6: while the feed is down the bot cannot see an announcement, and the 756-s sanity check would use stale mids). Release it after a released snapshot plus, in the storm phase, `risk.feed_resume_storm_obs` (2) fresh storm observations. S1-r3's sweep re-places its resting orders by itself once the hold is gone (§6.4 step 6).
  3. hold or release `unknown_orders` (with `exempt={"s1r3"}`) from `reconciler.unknown_age`, and pass MTM to `risk.on_mtm`;
  4. once per round 3, when the clock is known, call `limiter.reserve(t_to_mono(final-60), t_to_mono(close), {TAKE: cap, HEDGE: cap})`, recomputed if the anchor moves more than 0.5 s;
  5. at `s1r3.prewarm_at` (737 s) of round 3, emit the control `"prewarm_all"`;
  6. `law_check.on_tick(books.views(now), now)`;
  7. if a season reset has been pending for more than `feed.season_confirm_s` (60 s): `normalizer.reject_season_reset(now)`, keep all state and the current engine, and alert `season_reset_unconfirmed`; the `season_suspect` hold stays until the operator types `season keep`, `season new` or `r`;
  8. run a decision cycle with trigger TICK.
- **`reconcile` job:** `reconciler.begin_cycle`, then queue GET_OPEN and GET_POSITIONS. Skip it while the previous cycle is outstanding and younger than 2 s. When a cycle completes while FROZEN, call `om.kill_followup(reconciler.last_open_rows(), now)`.
- **`clock_poll` job:** GET_TIME. In `on_api_result`, call `clock.observe_server`. Then the comp_id rule:
  - the first comp_id is recorded;
  - a new comp_id while a reset is pending → `normalizer.confirm_season_reset("comp_id")`, handled as a confirmed reset;
  - a new comp_id within `feed.reset_merge_s` (180 s) after a confirmed feed-triggered reset, with no comp_id change since that reset → adopt it without a second reset (log `season_reset_merged`);
  - any other new comp_id → `normalizer.force_season_reset("comp_id")`.
  - A feed trigger after a comp_id reset is suppressed by the normalizer itself (its state was cleared), so each season change counts once.
- **`on_api_result`** routes order actions to `om.on_result` and `risk.on_result`, and GET_OPEN / GET_POSITIONS to `reconciler.on_result`. A 429 also calls `limiter.on_throttled(now, rate.throttle_freeze_s)`. After an order-action result the core calls `om.step(now, clock, feed)` once (no strategy decision) and appends its requests, so a `cancel_on_ack` cancel, a kill follow-up or the next clip does not wait for the tick. Fills (already applied to the positions by B) go to `risk.on_fill`, the runner (via `ctx.fills` on the next cycle) and the log `fill`.
- **`_season_suspect(ev)`** (provisional): hold `season_suspect` (sticky, no exemptions), alert `season_reset_suspect` with the reason, queue a GET_TIME at once, and start the pending buffer (forwarded news and relevant snapshots, at most `feed.pending_buffer_max` (5,000); overflow drops the oldest and alerts). Nothing is cancelled or reset, and the engine keeps ingesting: if the trigger was spurious, those messages belong to the current season.
- **`_season_change(ev)`** (confirmed): `om.cancel_all(KILL)` if live non-dry orders exist; reset the books, positions, store (new epoch), risk round state, laws, coverage and law_check; `runner.reset_season()`; `engine.reset_season(epoch, run_dir/engine_state_{run_id}_e{epoch}.jsonl)`; `clock.on_season_reset`; release `season_suspect`; if a pending buffer exists, submit it to the engine and replay it into laws and coverage; log `season_reset` with the reason and whether it was provisional first.
- **`_round_start(round)`:** `risk.on_round_start` (new peak); when the round increased from a known earlier round of the same epoch to 2 or 3, check that every symbol except `strategies.flatten.exclude` (REIN, whose short from the riskless ask is held) is flat, and alert `not_flat` otherwise; then a decision cycle with trigger ROUND_START.
- **Promotion** (`promotion=True`: the saved state was written in the other mode, §4.D persist): `om.drop_dry()`; positions and cash start empty and are rebuilt by the first reconcile (they include whatever another instance traded); the limiter ledger is not restored; the season info and the engine log are kept; `runner.on_promotion()` (H1 forgets its round-1 check and uses `size_on_fail` until `h1 X`); hold `restart` and alert `h1_size_needed`.
- **Commands** (`on_command`):

  | Command | Effect |
  |---|---|
  | `k` | `risk.kill` + `om.kill`, and the control `"freeze"` in the same call |
  | `cx` | `om.cancel_all(KILL)` |
  | `p` / `r` | Hold or release `operator_pause`; `r` also releases `drawdown`, `restart`, `feed_down`, `auth` and `season_suspect` |
  | `unfreeze-kill` | Typed in full: `risk.unkill`, control `"unfreeze"` |
  | `x SYM` / `e SYM` | `risk.disable` or `risk.enable` |
  | `f SYM PX`, `f SYM off`, `m SYM X`, `a SYM X` | `engine.set_adjustment` (absolute PX is `mult=0, add=PX`) |
  | `b N` | `limiter.cap = N` (clamped by the limiter) |
  | `caseok SYM` / `caseok all` / `caseok timing` | Remove the symbol (or every symbol) from `SeasonInfo.case_diff`, or acknowledge `timing_diff`; logged and persisted |
  | `season keep` / `season new` | Reject or confirm the pending feed-triggered season reset (`season new` with none pending forces one, reason "operator") |
  | `eng restart` | Close the engine adapter and rebuild it with `restore=True` from the current engine log (alert while it rebuilds) |
  | `st`, `pos`, `ord`, `ann`, `holds` | Log and print |
  | `q` | Shutdown |
  | anything else | Passed to `runner.on_command` (h1, s3, s1r3, lag, rein, flat, u, ua, s1ok, x1, lawok) |

  An unknown or bad command logs `command_error` and never raises.

**`app/shell.py`**: `async def run_shell(core: BotCore, sources: Sequence[MarketDataSource], executor: RestExecutor, api: ExchangeAPI, engine: FairValueProvider, timesrc: TimeSource, log: EventLog, run_dir: str | None, console: bool, stdin: TextIO | None = None) -> int`.
- **Tasks:** one `source.run(emit)` per source (two in host mode), all with the same `emit`, which appends to one `asyncio.Queue`; a consumer that awaits one frame, drains the rest without waiting, then calls `core.on_frames(batch, timesrc.mono())` and `flush()`; one loop per job (`core.run_job(name, now)` then `flush()`, scheduled on `timesrc` with drift correction); a kill-file watcher every 0.25 s (`run_dir/KILL` sends `Command("k", source="killfile")`); a console reader (`loop.run_in_executor(None, stdin.readline)`), which stops on EOF (an empty string) and logs `console_eof` instead of spinning; a status printer to stderr.
- `engine.set_listener(lambda: loop.call_soon_threadsafe(on_fv))`. Coalesce with a pending flag, so at most one call is queued.
- `flush()`: apply `core.take_control()` first (`freeze` → `executor.freeze()`, `unfreeze` → `executor.unfreeze()`, `prewarm_all` → `executor.prewarm_all()`), then `for req in core.take_requests(): executor.submit(req)`. So after `k` the executor is frozen before the kill's CANCEL_ALL is submitted to the kill worker, and no PLACE leaves after it.
- The executor's `on_result` is `loop.call_soon_threadsafe(lambda r: (core.on_api_result(r, timesrc.mono()), flush()), res)`.
- **Startup:** `executor.prewarm_all()` once the core has started.
- **Signals:** the first SIGINT runs the `k` command, waits up to `app.shutdown_wait_s` (2 s) for the cancel result, then exits. A second SIGINT makes one synchronous best-effort `api.execute(ApiRequest(kind=CANCEL_ALL, priority=KILL))` on the main thread, through the same `RestClient` and therefore the same `LiveToken` gate (a dry no-op in dry-run), and exits.
- Every core call is wrapped. An exception logs `exception`; more than `app.exceptions_per_min_hold` (10) per minute holds `exceptions`.
- `log.install_redaction(secrets, loop)` is called before any task starts.

**`app/log.py`**
- `JsonlLog(path, secrets: Sequence[str], echo: Callable[[str], None] | None)` implements `EventLog`. Each line is `{"t_wall", "t_mono", "kind", ...fields}` serialised with `default=str`. Every secret substring is replaced with `***` **after** serialisation. `alert` also echoes with a terminal bell. `redact(text) -> str` applies the same replacement.
- `FrameRecorder(path, secrets)` writes `{**frame, "received_at": recv_wall}` per frame, the `record_feed.py` format, so `check_recording.py` can read the bot's own feed; each line is redacted like the log.
- `install_redaction(secrets, loop=None)`: sets `sys.excepthook`, `threading.excepthook` and (with a loop) `loop.set_exception_handler` to handlers that format the traceback, redact it and log it as `exception`; sets the `websockets` loggers to WARNING.
- Files under `run_dir`: `events.jsonl`, `feed_raw.jsonl`, `fv.jsonl` (one line per fair-value snapshot: version, engine_version, compute_ms, means), `state.json`, `engine_state_{run_id}_e{epoch}.jsonl`, `adjust.json`, `adjust_console.json`, `adjust_engine.json`, `KILL`.

**`app/persist.py`**
- `new_run_id() -> str`: `time.strftime("%Y%m%dT%H%M%S")` plus 4 random hex digits. Every start gets a new one, so a `--fresh` start in an old run dir never appends to an old engine log.
- `save_state(path, d)` writes atomically (tmp + `os.replace`). `load_state(path) -> dict | None`.
- `restart_plan(saved, server_time, now_wall, dry_run_now: bool, mode: str) -> RestartPlan(restore: bool, promotion: bool, engine_log: str | None, reason: str)`.
- **State schema:** `saved_wall`, `run_id`, `cfg_hash`, `comp_id`, `season_epoch`, `round`, `dry_run`; `positions`, `cash`, `round_peak_mtm`; `orders` (`store.state_dict`), `risk` (holds, disabled, killed), `runner` (per-strategy `state_dict`), `law_check`, `limiter_ledger_wall`; `engine_log` (the current engine-state file name); `season` (final, prelim and final_seq per round, loss_known, announcements, case_diff after `caseok`, timing acknowledgement).
- **Restart rule:** restore only if `comp_id` equals the server's current `competition_id` (in replay, the comp_id that the replay's SimExchange serves; never in sim).
  - The engine restores via `EngineAdapter(restore=True, persist_path=engine_log)`; laws and coverage rebuild from the same log; the core starts with the sticky hold `restart` (NO_NEW_RISK) until the operator types `r`. If `app.restart_auto_resume` is true, it releases after one reconcile cycle plus one released snapshot. Ledger entries younger than 61 s are restored, converted from wall to mono time.
  - **Promotion:** when `saved.dry_run != dry_run_now`, `promotion=True`: the core drops the saved orders, positions, cash and ledger and rebuilds them from `/orders/open` and `/positions`, keeps the engine log and the season info, and asks the operator to set the H1 size (§4.D core). This is the standby's path from dry-run to `--live`.

**`app/cli.py`** and **`run_bot.py`**
```
run_bot.py [--config PATH] [--profile PATH ...] [--set a.b=VALUE ...] [--host HOST] [--rest-base URL] [--ws-url URL]
           [--case-url URL] [--key-file PATH] [--live] [--accept-timing-diff] [--run-dir DIR] [--fresh] [--no-trade]
           [--replay RECORDING.jsonl] [--replay-speed X] [--replay-from UNIX] [--replay-to UNIX]
           [--sim research:NNN | recording:PATH] [--sim-speed X] [--history DIR] [--fv-cache DIR]
           [--self-test] [--print-config]
```
```python
def main(argv: Sequence[str] | None = None, *, transport=None, ws_connect=None, stdin: TextIO | None = None,
         isatty: Callable[[], bool] | None = None, environ: Mapping[str, str] | None = None,
         out: TextIO | None = None, err: TextIO | None = None) -> int
```
- **Injection:** `transport` and `ws_connect` go to `RestClient` and `build_sources`; when either is given, the network flags stay False. `stdin`, `isatty` and `environ` replace `sys.stdin`, `sys.stdin.isatty` and `os.environ` for the live gate and the key env var.
- `--host H` sets `rest_base = https://H/api/v1`, `ws_url = wss://H/ws/v1/market-data` and `case_url = https://H/cases/hurricane.json`, unless those are given explicitly. `--no-trade` disables every strategy.
- **Modes:** `--replay` runs `RecordingSource` (with `--replay-from/--replay-to`) on a `ScaledTime(replay_speed)` with an offline `SimExchange` (recorded mode, `fills=False`, E's class from `sim.exchange`) as the API; `--sim` runs `SimSource` plus `SimExchange`; otherwise the bot runs in **host mode**, which requires `--host` (or both `--rest-base` and `--ws-url`) and exits 2 with "host mode needs --host" otherwise. Only host mode sets `NETWORK_ENABLED`.
- **Key** (host mode): read from `--key-file` or `environ[exchange.key_env]`; exit 2 if missing. Strip surrounding whitespace, then require `^[A-Za-z0-9._~+/=-]{16,256}$`; otherwise exit 2 with "bot key has an invalid format" (never the key; a trailing newline would otherwise make http.client raise an error that contains it). Warn `key_file_mode` if the file is readable by group or others.
- **KILL file:** if `run_dir/KILL` exists at startup, exit 2 ("remove the KILL file first").
- **Startup in host mode:** (1) the key; (2) `GET_CASE`, then `diff_case` against `paths.research_case`, logging every line; if the fetch fails, continue in dry-run on the research case with the alert `case_fetch_failed`; (3) `CostModel.check_against_config()`, logging differences as warnings; (4) the engine probe; (5) `load_laws_module` (alert `laws_unavailable` on failure); (6) `GET_OPEN` and `GET_POSITIONS`; (7) `restart_plan`; (8) build the core and the shell.
- **Live gate.** `--live` is refused (exit 2, with the reason) when: it is combined with `--replay`, `--sim` or `--self-test`; `isatty()` is false; `Config.live_allowed` is false (a sim-only profile such as `rec_parity.json`); `GET_CASE` failed; the case has a timing difference and `--accept-timing-diff` was not given; the engine probe failed; or a KILL file exists. Otherwise the bot prints the host, the config hash, the profiles, every `diff_from_default` line, the case-diff lines, the enabled strategies, `sizing.scale`, the action cap, a warning if open orders already exist on this key ("another live instance?"), and a warning if the laws module is unavailable. The operator must then type exactly `LIVE <host> <cfg_hash[:6]>`, with 3 attempts read from `stdin`; otherwise exit 2. On a match, and only there, the cli creates `LiveToken(host, cfg_hash, rest._ISSUE)` and passes it to the one `RestClient`. Without `--live` there is no token, so the bot is in dry-run.
- `--self-test` (offline): load the config, import the engine and run the probe, load the cost model and check it, load the laws, and run `diff_case` of the research case against itself, which must be clean. Exit 0 or 1.
- `run_bot.py` is about 10 lines: `sys.dont_write_bytecode = True`, insert `BOT` into `sys.path`, then `raise SystemExit(ctcbot.app.cli.main())`.

**`app/console.py`**: `parse(line: str) -> Command | None`. Tokens are split on whitespace and the name is lower-cased; symbols are upper-cased.

**`app/status.py`**: `format_status(st: dict) -> str`. One line, for example:

`[DRY] NORMAL e1 r2 storm t=213.4 feed(q) | ws 2/2 0.4s wm695 gaps0 dup0 | eng 0.3s 41ms q0 | act 87/150 rej0 unk0 | mtm +3,210 pk +4,020 | PORT+200 HAVEN-120 | frz - | holds -`

### 4.E Simulation (group E)

**`sim/exchange.py`**
```python
class SimExchange:        # ExchangeAPI
    def __init__(self, market: "ResearchMarket | RecordedMarket", cfg: Config, cost: CostModel, log: EventLog,
                 fills: bool = True): ...
    t: float                                           # virtual time, set by the harness or a ScaledTime source
    def advance(self, t: float) -> None                # process row fills and resting-order crossings due by t
    def execute(self, req: ApiRequest) -> ApiResult    # arrival = self.t (+ api latency is the harness's job)
    def close_all(self, t: float) -> None              # cancel every resting order (end of a run)
    def results(self) -> dict                          # per-season P&L, per-order fills, violations, rate stats
class SimSource:          # MarketDataSource for shell-mode sim (frames paced on a ScaledTime)
    def __init__(self, market, exchange: SimExchange, timesrc: TimeSource, name: str = "sim",
                 tail_s: float | None = 2.0): ...    # returns tail_s after the last frame (§12 item 6)
```
`sim/*` imports only `types` and A modules at module level (§1); the caller passes the `CostModel`, and the run scripts import B, C and D inside `main`. `SimExchange.execute` answers order actions whatever the bot's mode, because a replay or sim run has no `LiveToken` and the `RestClient` gate does not apply to it: the sim is the exchange. In shell replay (`--replay`), `SimExchange.t` follows the same `ScaledTime` as the `RecordingSource`, mapped to `received_at − first_received_at`.
**Exchange rules enforced** (each breach is counted in `results()["violations"]` by type):
- `market_closed`: a PLACE outside a running round returns 409 `{"detail": "market closed"}`.
- `rate`: more than `sim.team_actions_per_min` (180) order actions in any 60 s returns 429; separately, `bot_cap` counts any 61-s window with more than `rate.bot_cap_per_min` (150). `requests`: more than 600 requests in 1 s returns 429.
- `limit_shrink`: a PLACE where position + same-side resting + qty > L is shrunk to the room, or rejected with 400 if the room is 0. `limit_room_cancel`: an immediately-filling part that needs room cancels our resting same-side orders, worst price first.
- `self_trade`: a new order crossing our own resting opposite order cancels the resting one. `max_resting`: more than 200 resting orders in a symbol returns 400. Quantity must be ≥ 1 and the price must have at most 6 decimals, otherwise 400.
- **Close** (`sim.cancel_at_close`, default true): at each round close every resting order is cancelled, the conservative answer to the open Slack question on orders across closes; `close_all(t)` does the same at the end of a run, so "every order terminal" (§9.2) is defined. The bot sees the cancels through reconcile.

**Response schemas** (chosen to exercise A's parsers):

| Request | Response |
|---|---|
| PLACE | `{"order_id", "status": "open" \| "filled" \| "partially_filled", "filled_quantity", "remaining_quantity", "avg_fill_price"}` |
| GET /orders/open | a list of `{"order_id", "symbol", "side", "price", "quantity", "remaining_quantity", "status"}` |
| GET /positions | a list of `{"symbol", "quantity", "avg_price", "mark", "realized_pnl", "unrealized_pnl"}` |
| GET /competitions/time | `{"competition_id": "sim-NNN", "state": "running" \| "paused", "fictional_time", "elapsed_active_seconds"}` |
| DELETE | `{"status": "cancelled"}`, or 404 |

**Fills, research mode.** These are r_sim's `sim()` formulas, one row per 2 s:
- **Timing.** A PLACE arriving at `ta` is matched at row `j = floor(ta/2) + 1 + sim.lat_rows + (sim.lat_rep_rows if ta is in [prelim, prelim+30) or [final, close))`, at time `2j`, using the consensus `rows[j]`. Until then the order rests (cancellable). `j ≥ 390` never fills. The cost phase is `schedule.cost_phase(ta + 2*sim.lat_rows)`.
- **Cost per share**, with `x` = shares/L, s = side, and X1 our same-side shares already filled on (sym, j) divided by L:
  - **walk:** for an increment of q shares, `X2 = X1 + q/L` and `cw = (X2*c(X2) - X1*c(X1))/(X2 - X1)`, where `c = cost.walk_bp(sym, ph, ·, side)`;
  - **follow-on:** `fo = Σ beta*x_k` over our earlier same-side fills with `0 < 2j - t_k ≤ sim.fo_window_s` (8), where beta is `sim.beta_bp_per_L` (100 for stocks, 48 for ETFs);
  - **impact:** `im = Σ I(dt)*x_k` over same-side fills older than 8 s, minus `Σ I(dt)*x_k` over opposite-side fills of any age (`impact_credit`). `I` interpolates `sim.impact_kernel_t/bp`, then decays as `26.8*exp(-(dt-30)/47)`, and is 0 beyond 150 s;
  - **price:** `rows[j][sym] * (1 + s*mult*(cw + fo + im)*1e-4)`, with `mult = sim.cost_mult`.
- **Replenishment:** `q ≤ int(L*max(0, depth(ph) - (1 - rho)*x_prev)) - filled_side(sym, j, s)`, where `x_prev` is our same-side shares on row j-1 divided by L.
- **Limit:** fill the largest integer q* ≤ q whose **marginal** price, `rows[j]*(1 + s*mult*(m(X1 + q*/L) + fo + im)*1e-4)` with `m(x) = d(x*c(x))/dx` by finite difference, is on the right side of the limit. The increment's price is its average `cw`. The remainder rests at the limit.
- **Resting orders** fill at their own price when a later row's synthetic touch (`rows[i]*(1 ± hs/1e4)`) reaches them, for at most `depth(ph)*L` shares per row.
- **Synthetic market data:** at each row time `2i`, emit a `quote` and an `orderbook` frame per symbol. The touch is `rows[i]*(1 ± cost.half_spread_bp(sym, ph)/1e4)`; the 5 levels sit at cumulative `x = sim.book_levels_x` [0.1, 0.17, 0.45, 0.7, 3.0], priced at the marginal walk cost, with `qty = L*Δx`; timestamps are synthetic ISO strings.
- **News** comes from `feed.jsonl` at `t_round_open + market_to_t(time)`, in seq order, wrapped as `{"type": "news", "message": m}`. Optional chaos: `--drop-seq` and `--inject-snapshots` (testnet schema, every 30 s).
- **Rounds** open at `T_r = (r-1)*(780 + sim.pause_between_rounds_s)`, with a `closed` snapshot every 30 s during pauses; the server state is "running" only within `[T_r, T_r + 780)`.
- **Scoring:** rounds 1-2 are marked at the last row (`rows[389]`) and round 3 at `settlement.json`. Per-strategy attribution is `Σ q*(settle - px)`, joined to the bot's `OrderRecord.strategy` by order_id. Cost paid is `Σ |q|*rows[j]*cost`.

**Fills, recorded mode.**
- A PLACE arriving at `ta = t + sim.recorded_latency_s` (0.3) walks the latest recorded `orderbook` frame of the symbol with recv time ≤ ta. It takes levels priced at or through the limit, depleting them (per frame, per price) until the next frame. The fill price is the walk VWAP plus `side*mid*mult*(fo + im)*1e-4` when `sim.recorded_follow_on` is on, capped at the limit. The remainder rests.
- A resting order fills when a later frame's opposite touch is at or through its price, for at most that level's quantity, at its own price.
- `RecordedMarket` supplies frames in file order at `t = received_at - first_received_at`, as `SourceFrame`s with `conn = "rec#k"`. Like `RecordingSource`, it turns each `subscription_ack` after the first into a `"down"` then `"up"` control pair (bumping k), so the harness path also sees the recording's 5 reconnects. It also supplies per-round open times, fitted as the minimum over storm messages of `(recv - t(market_time))` and refined with the first snapshot, for `/competitions/time`; the server state "running" between open and close; comp_id `rec-<research season>`, mapping the date to the season through `HISTORY/manifest.json` round opens (the recording covers 224 r3 partial, 225 complete and 226 partial); and settlement from `HISTORY/season-NNN/settlement.json`, with partial seasons marked at the last mid.

**`sim/fv_cache.py`**: `CachedFairValues(cache_path: str, schedule: Schedule)` is a `FairValueProvider`.
- On `submit`, it tracks the newest (round, t) of the submitted messages and publishes the cache entry with the largest `t_e ≤ t` for that round. Rounds after the first carry the previous round's last value in at t = -1, exactly as `backtest.Season.fair_at` does.
- `seq_hi` is the max seq submitted. `diag` is empty. The listener is called synchronously.
- `health` is always fresh.

**`sim/harness.py`**
```python
class Harness:
    def __init__(self, cfg: Config, market, exchange: SimExchange, engine: FairValueProvider, core_factory: Callable[..., BotCore],
                 log: EventLog, t_end: float | None = None, commands: Sequence[tuple[float, Command]] = ()): ...
    def run(self) -> dict          # exchange.results() + core.report() + timing
```
- It is a deterministic discrete-event loop over virtual time: no threads, no wall clock, seeded randomness only.
- **At each event time `t`**, in this order: (1) `exchange.advance(t)`; (2) due API results, then due engine notifications (`core.on_fair_values`); (3) due jobs in `core.jobs()` order, i.e. reconcile, clock_poll, tick, with each job firing at multiples of its period from t = 0; (4) every market frame stamped `t` in one `core.on_frames` call; (5) due scripted commands. Jobs precede the frames of the same instant (§12 item 2).
- After **every** core call, drain `take_requests()` through `exchange.execute` at arrival `t`, and deliver each result at `t + sim.api_latency_s`. With 0 latency the result is delivered at once, before the next step. So a clip sent at 80.0 fills on row 41 at 82.0, the 82.0 reconcile marks it FILLED, and the 82.0 tick sends the next clip with no remainder cancel. `take_control()` is drained and ignored: there is no executor in virtual time, and a FROZEN core emits no PLACE anyway.
- **End of run:** at `t_end` (default: the last frame or row plus 2 s) the harness calls `exchange.close_all(t_end)`, then runs the jobs for 2 more virtual seconds so two reconcile cycles see the result, then `core.shutdown`.
- The engine listener only sets a flag. The harness calls `core.on_fair_values(t + sim.engine_latency_s)` after the current call returns, so there is no re-entrancy.

**Scripts:**
- `sim/run_research.py --history DIR --fv-cache DIR --seasons 401-440 [--profile PATH ...] [--set a.b=V ...] [--fv cache|inline] [--workers 8] [--out sim/out/NAME]` runs one fresh core and exchange per season in a multiprocessing fork pool. `--ablations` runs the default plus each `sim/ablations/abl_*.json` overlay and writes the §9.3 attribution table. `--fv inline` uses `EngineAdapter(mode="inline", library_seasons=(1, 400))` with standard params (`engine_params.json`, as the caches were built), which takes about 40 s per season. It writes `report.json` and prints the r_sim `HDR` table plus the paired comparison with the REC baseline in §9.3.
- `sim/run_recording.py --recording PATH --history DIR [--mode dryrun|book] [--fv inline] [--command T:LINE ...] [--out ...]` (`--command` scripts console commands at recording time T, e.g. `lawok all` for §9.2). `dryrun` is `SimExchange(fills=False)`, where orders rest, never fill and are cancelled by the bot; `book` uses recorded-book fills. Both use the real engine inline with fast params and `library_seasons = sim.recorded_library_seasons` [226, 600], so the replayed seasons are out of sample (TESTNET_FINDINGS §2).
- `sim/compare_rec.py --seasons 401-440` imports `r_sim` read-only (`sys.dont_write_bytecode = True`, no writes), runs the REC configuration exactly as `writer/w02_sens.py` does, and prints the paired table against the bot's `report.json`.

### 4.F Cross-group calls

Every call that crosses a group boundary is listed here. A call that is not in this table is not part of any interface: the callee may rename or remove it. Calls inside a group (for example the OM applying ack fills to `PositionBook`, or the Reconciler taking `quiet_stamp()`) are internal.

**Protocol implementations** (`types.py`): `ExchangeAPI` by A's `RestClient` and E's `SimExchange`; `MarketDataSource` by A's `WsSingleSource` and `RecordingSource` and E's `SimSource`; `FairValueProvider` by C's `EngineAdapter` and E's `CachedFairValues`; `EventLog` by `types.ListLog` and D's `JsonlLog`; `TimeSource` by A's time sources.

**Data contracts:** strategies (C) return `Intent`s that D passes to the OM (B); D builds `StrategyContext` from A, B and C state; `WorkingSummary` and `Fill` come from B and reach C through the context.

| Caller (group, where) | Callee (group) | Calls | When |
|---|---|---|---|
| D `cli` | A | `load_config`, `diff_from_default`, `CaseInfo.from_dict/from_file`, `diff_case`, `Schedule(...)`, `RealTime`, `ScaledTime`, `RestClient(...)`, `LiveToken(host, hash, rest._ISSUE)` (only in the confirmed-phrase path), `RestExecutor(...)`, `build_sources(...)`, `RecordingSource(...)`, `RestClient.execute` (startup GET_CASE, GET_OPEN, GET_POSITIONS) | Startup |
| D `cli` | C | `load_engine_module`, `snapshot_fix_present`, `EngineAdapter(...)`, `CostModel(...)`, `CostModel.check_against_config`, `load_laws_module` | Startup, `--self-test` |
| D `cli` | E | `sim.exchange.SimExchange`, `sim.exchange.SimSource`, `sim.recorded_market.RecordedMarket`, `sim.research_market.ResearchMarket`, `sim.fv_cache.CachedFairValues` (lazy imports) | `--replay`, `--sim` only |
| D `core.default_factories` | A | `FeedNormalizer(cfg, log)`, `ClockModel(schedule, cfg, log)`, `ActionLimiter(...)`, `strategy_limits` | Construction |
| D `core.default_factories` | B | `BookStore`, `PositionBook`, `OrderStore(epoch)`, `RiskManager`, `OrderManager`, `Reconciler` constructors | Construction and each confirmed season reset (new `OrderStore`) |
| D `core.default_factories` | C | `StormCoverage(cfg, log)`, `LawsCrossCheck(cfg, log, laws_module, coverage)` (skipped when `laws_module` is None), `LawCheck(cfg, log)`, `StrategyDeps(...)`, `StrategyRunner(deps)` | Construction |
| D `core.on_frames` | A | `FeedNormalizer.ingest`, `.poll`; `ClockModel.observe_feed`, `.round_events` | Every frame batch |
| D `core.on_frames` | B | `BookStore.on_book/on_quote/on_trade`, `.views` | Every book, quote, trade; law-check reports |
| D `core.on_frames` | C | `engine.submit`; `StormCoverage.on_news/on_snapshot`; `LawsCrossCheck.on_news/on_snapshot`; `LawCheck.on_report` (rounds 1-2 `damage_report`, before `engine.submit`) | Every forwarded news message and relevant snapshot |
| D `core` decision cycle | A | `ClockModel.read`; `ActionLimiter.available`, `.floor`; `strategy_limits` | Every cycle |
| D `core` decision cycle | B | `OrderManager.working`, `.set_intents`, `.step`; `PositionBook.pos`; `RiskManager.mode`, `.disabled`; `BookStore.views` | Every cycle |
| D `core` decision cycle | C | `StrategyRunner.frozen`, `.leadlag_symbols`, `.decide`; `StormCoverage.coverage`; `engine.latest`, `.health` | Every cycle |
| D `core.on_fair_values` | C | `engine.latest`; `LawCheck.on_fv` | Engine listener |
| D `core.tick` | A | `FeedNormalizer.poll`, `.reject_season_reset`, `.phase_hint`, `.stats`; `ClockModel.read`, `.t_to_mono`; `ActionLimiter.reserve`, `.clear_reservations` | Every tick |
| D `core.tick` | B | `OrderManager.cancel_all(SNIPE)` (feed down); `Reconciler.unknown_age`; `RiskManager.hold/release` (`feed_down`, `unknown_orders`), `.on_mtm`; `PositionBook.mtm`, `.exchange_pnl` | Every tick |
| D `core.tick` | C | `LawCheck.on_tick` | Every tick |
| D `core` reconcile job | B | `Reconciler.begin_cycle`, `.on_result`, `.consecutive_failures`, `.last_open_rows`; `OrderManager.kill_followup` (while FROZEN) | Every 0.5 s and on GET results |
| D `core.on_api_result` | A | `ClockModel.observe_server`; `FeedNormalizer.confirm_season_reset`, `.force_season_reset`; `ActionLimiter.on_throttled` (429) | GET_TIME results; 429 results |
| D `core.on_api_result` | B | `OrderManager.on_result`; `RiskManager.on_result`, `.on_fill` | Order-action results |
| D `core._season_change` | B, C, A | `OrderManager.cancel_all(KILL)`; `BookStore.reset`; `PositionBook.reset`; new `OrderStore(epoch)`; `RiskManager.on_round_start`; `StormCoverage.reset`, `LawsCrossCheck.reset`, `LawCheck.reset`; `StrategyRunner.reset_season`; `engine.reset_season`, `.submit` (pending buffer); `ClockModel.on_season_reset` | Confirmed season reset |
| D `core._round_start` | B | `RiskManager.on_round_start`; `PositionBook.pos` (flat check) | Each `round_events()` entry |
| D `core.on_command` | A, B, C | `ActionLimiter.cap` (setter); `RiskManager.kill/unkill/hold/release/disable/enable`; `OrderManager.kill`, `.cancel_all(KILL)`; `FeedNormalizer.confirm_season_reset`, `.reject_season_reset`, `.force_season_reset("operator")`; `engine.set_adjustment`, `.override`, `.close`; `StrategyRunner.on_command` | Console, KILL file, signals |
| D `core` restore and promotion | B, C | `OrderStore.load_state`, `OrderManager.load_state`, `.drop_dry`; `RiskManager.load_state`; `StrategyRunner.load_state`, `.on_promotion`; `LawCheck.load_state`; `StormCoverage.rebuild`, `LawsCrossCheck.rebuild` (from the engine log); `ActionLimiter.restore` | Startup with a saved state |
| D `core` persist job | A, B, C | `OrderStore.state_dict`, `RiskManager.state_dict`, `StrategyRunner.state_dict`, `LawCheck.state_dict`, `ActionLimiter.ledger` | Every 5 s |
| D `shell` | A | `MarketDataSource.run/stop/stats`; `RestExecutor.submit`, `.freeze`, `.unfreeze`, `.prewarm_all`, `.close`; `RestClient.execute` (second SIGINT) | Runtime |
| D `shell` | C | `engine.set_listener`, `.close` | Startup, shutdown |
| C `strategies.s1r3` | C | `LawsCrossCheck.values`, `.compare`; `LawCheck.passed`, `.clear` (`lawok`) | Inside group C |
| B `OrderManager` | A | `ActionLimiter.try_acquire`, `.reserved`, `.used`, `.cap` | Every step |
| B `RiskManager` | A | `strategy_limits`, `Schedule.at`, `Schedule.phase` | Every check |
| C strategies | A | `Schedule.at`, `.phase`, `.cost_phase`; `clip_size`; `Config.get` | Every decision |
| E `harness` | D | `BotCore.start`, `.on_frames`, `.on_fair_values`, `.on_api_result`, `.on_command`, `.jobs`, `.run_job`, `.take_requests`, `.take_control` (drained and ignored: no executor in virtual time), `.report`, `.status`, `.shutdown`; reads `clock`, `books`, `positions`, `store`, `om`, `risk`, `limiter`, `runner`, `season` | Every event |
| E `report`, `harness` | B, A | Read through the core's attributes: `OrderStore` records (`strategy`, `order_id`, `style`, fills), `PositionBook.fills`, `.pnl_by_strategy`, `.mtm`; `RiskManager.holds`; `ActionLimiter.stats` | Per-season reports |
| E `exchange`, `fillmodel`, `research_market` | C | `CostModel.walk_bp`, `.half_spread_bp`, `.depth`, `.rho` | Fills and synthetic books |
| E `exchange`, `research_market`, `fv_cache` | A | `Schedule(...)`, `.cost_phase`, `.market_to_t`; `load_config` | Construction |
| E run scripts | C, D | `EngineAdapter(mode="inline")`; `BotCore(...)` through `core_factory` | `--fv inline`; every run |
| E tests | A | `parse_positions`, `parse_open_orders`, `parse_order_ack`, `parse_time` | Schema checks |

---

## 5. Concurrency, time and order lifecycle

### 5.1 Concurrency model

| Thread | Owns | Talks to the loop by |
|---|---|---|
| Main (asyncio loop) | `BotCore` and everything inside it (state, OM, risk, runner, clock, normalizer, coverage, law check), sources, recorder, log | – |
| Engine worker (1) | `SeasonState` (exclusively), its queue, the adjust-file merge and the latest snapshot (lock) | `listener` → `call_soon_threadsafe` |
| Laws worker (1, `LawsCrossCheck` mode "thread") | The per-round closure integration; results under a lock that `values()` reads | none (the loop only reads cached results) |
| REST workers (`exchange.workers`, 12) | One keep-alive HTTPS connection each, `RequestLimiter` (lock) | `on_result` → `call_soon_threadsafe` |
| Kill worker (`exchange.kill_workers`, 1) | Its own keep-alive connection; serves only KILL-priority requests | `on_result` → `call_soon_threadsafe` |
| Console reader (executor) | stdin; stops at EOF | `call_soon_threadsafe(core.on_command)` |

Nothing else touches core state, so the core has no locks. The core is cheap per call (no engine or closure work on the loop; JSON parsing happens in the source tasks), so the 116-frames-per-second burst at 740 s is fine. At round 3's final report the engine computes in its own thread (about 30-75 ms) and S1-r3 fires from `on_fair_values`; the loop never blocks on the engine. The venv's Python 3.14 has the GIL enabled, so the worker threads still compete with the loop for the interpreter; §9.1 D `latency` measures the result on the wall clock.

### 5.2 Time model

- **Clock:** `TimeSource.mono()` everywhere. Each round's real second `t` comes from `ClockModel` (§4.A). Anchors come from storm frames (2 s), trading-phase snapshots (30 s) and `GET /competitions/time` (1 Hz, `fictional_time`, RTT/2 corrected, running state only).
- **Schedule:** never hardcode a time. Every window is an anchor spec resolved by `Schedule.at` from the **served** case.json timing. Event-driven actions key off feed events: S1-r3 waits for the round-3 final `damage_report` (or the snapshot `damage` list if that message was lost), formation is `storm_formed`, and announcements are messages.
- **Round** comes from the trading-phase message or snapshot `round` (int-normalised) and only increases within a season. **Phase** is `schedule.phase(t)`, cross-checked against the snapshot `phase` (log a mismatch; do not override).
- **Season** is the normalizer's epoch. It increments on a confirmed reset: a snapshot `last_seq == -1`, a new `competition_id`, `season new`, or a feed trigger (round-1 `storm_formed` restart, round decrease, date change) once a −1 snapshot, a consistent snapshot or a new comp_id confirms it (§4.A normalize).
- **Market open:** `clock.running` and phase in {pre_storm, storm, aftermath}.

### 5.3 Order lifecycle

```
NEW --send--> SENDING --2xx open--> OPEN --ttl/cancel (ticket granted)--> CANCEL_PENDING --2xx--> CANCEL_ACKED --absent x1--> CANCELED | FILLED part
                 |  \--2xx filled--> FILLED                                 |        \--None/5xx--> CANCEL_UNKNOWN --reconcile--> CANCELED | FILLED | OPEN
                 |--4xx/429/not sent--> REJECTED                            |--404--> (reconcile decides)
                 |--2xx without id, None/5xx--> UNKNOWN --matched--> OPEN ;  --absent x2 after grace--> FILLED (position delta) | CANCELED
                 \--matched by reconcile (ack still in flight)--> OPEN
OPEN --absent, delta explains remaining (1 cycle)--> FILLED ;  OPEN --absent x2, partly or not explained--> FILLED part / CANCELED rest
```

`TRANSITIONS` (B, `state/orders.py`):

| From | To |
|---|---|
| NEW | SENDING, REJECTED (risk or ticket failure after creation) |
| SENDING | OPEN, FILLED, REJECTED, UNKNOWN |
| OPEN | OPEN (partial), FILLED, CANCEL_PENDING, CANCELED |
| CANCEL_PENDING | CANCEL_ACKED, CANCEL_UNKNOWN, FILLED, CANCELED, OPEN or CANCEL_UNKNOWN (only when the cancel request came back `sent=False`) |
| CANCEL_ACKED | CANCELED, FILLED |
| UNKNOWN | OPEN, FILLED, CANCELED, CANCEL_PENDING |
| CANCEL_UNKNOWN | OPEN, CANCELED, FILLED, CANCEL_PENDING |

- Room counts every LIVE state (§4.B limits).
- **CANCEL_ACKED** (a 2xx DELETE) no longer blocks the next clip and is not part of the exposure, so the next clip can go as soon as the DELETE is acked. It still counts its `remaining` in room until the first reconcile cycle sent after the ack resolves it, because a fill just before the cancel is visible only in `/positions`.
- A record enters CANCEL_PENDING only when its cancel request is emitted with a ticket; a denied cancel stays queued and the record keeps its state.
- Order actions are never retried after a byte was written; the only exception is the kill's idempotent CANCEL_ALL (§4.A rest). Recovery always goes through reconcile.
- A clip's remainder is cancelled `ttl_s` (2.0 s) after sending, and the next clip waits until no clip is live in that symbol. This matches "cancel any unfilled remainder before the next clip". H1's clips therefore go at about 50, 52, 54 and 56 s, a tick or two later when a remainder has to be cancelled and acked first.
- In the research sim a clip sent at t fills on row ⌊t/2⌋+1, which is at most t+2. The fill is processed before the tick at the same instant, so no remainder cancel races it.

---

## 6. Strategy specifications

Notation:
- `t` is real seconds since the round open; **L** is `ctx.limits[sym]`, the case limit × `sizing.scale` as an int (L_s in §4.B);
- clips are `clip_size(L, sizing.clip_div[name])` = `max(1, int(L / div))`, with divisors s3 12, h1 4, h1_exit 8, flatten 12, flatten_max 4, leadlag 2. This is the only clip formula; the config has no per-symbol clip sizes;
- `mid`, `bid` and `ask` come from the book;
- (μ, sd) come from `ctx.fv`;
- costs are in bp of touch mid;
- times are shown at the research timing, with the anchor spec in brackets;
- a symbol is **blocked** for a strategy when it is in `ctx.frozen`, `ctx.disabled` or `ctx.leadlag`.

**Bot units at `sizing.scale` 1.0** (TESTNET_FINDINGS §6 and `w05_params.out`; the clip columns follow from the formula above):

| Sym | L | S3 clip L/12 | H1 clip L/4 | H1 exit L/8 | Entry cost s3_start / storm / after (bp) | Exit band s3_start / storm (bp) | ε | ε wide | Laws tol | H1 dir |
|---|---|---|---|---|---|---|---|---|---|---|
| PORT | 200 | 16 | 50 | 25 | 315 / 123 / 101 | 114.4 / 24.2 | 0.07 | 0.25 | 0.25 | +1 |
| ISLE | 300 | 25 | 75 | 37 | 165 / 107 / 99 | 43.2 / 16.4 | 0.02 | 0.02 | 0.02 | +1 |
| AQUA | 500 | 41 | 125 | 62 | 170 / 112 / 98 | 46.7 / 18.5 | 0.02 | 0.02 | 0.02 | +1 |
| GRID | 400 | 33 | 100 | 50 | 205 / 107 / 99 | 62.8 / 17.1 | 0.06 | 0.25 | 0.25 | +1 |
| FERRY | 800 | 66 | 200 | 100 | 138 / 117 / 102 | 31.7 / 21.4 | 0.09 | 0.25 | 0.25 | +1 |
| RECON | 500 | 41 | 125 | 62 | 195 / 110 / 99 | 57.2 / 18.4 | 0.02 | 0.02 | 0.02 | −1 |
| MAT | 700 | 58 | 175 | 87 | 164 / 110 / 100 | 42.8 / 18.0 | 0.02 | 0.02 | 0.02 | −1 |
| GEN | 600 | 50 | 150 | 75 | 161 / 109 / 100 | 42.0 / 17.8 | 0.11 | 0.25 | 0.25 | −1 |
| HAVEN | 250 | 20 | 62 | 31 | 342 / 138 / 104 | 125.4 / 31.2 | 0.02 | 0.02 | 0.02 | +1 |
| REIN | 200 | 16 | 50 | 25 | 100 / 100 / 98 | 13.1 / 13.0 | 0.02 | 0.02 | 0.02 | 0 |
| ARCH | 50 | 4 | 12 | 6 | 129 / 73 / 58 | 46.2 / 19.1 | 0.17 | 0.25 | 0.25 | +1 |
| REBLD | 100 | 8 | 25 | 12 | 101 / 69 / 55 | 33.1 / 17.3 | 0.12 | 0.25 | 0.25 | −1 |

- Entry cost is `2.0 × (walk(L/12, phase) + 36 bp)` for stocks and `+ 17.28 bp` for ETFs. The exit band is r_sim's `hs`, the table value at x = 0.025.
- When `cost_source` is "model", the strategies call `CostModel.entry_cost_bp` instead of using the table, scaled by `s3.cost_mult`. Raise `cost_mult` if round-1 fills come in above the tables.

### 6.1 H1, the formation leg (`h1`, TAKE)

1. **Size per round.** Round 1 uses `size_r1` (0.5). With `auto_size_from_check`, rounds 2-3 use `size_on_pass` (1.0) if the round-1 check passed and `size_on_fail` (0.5) if it failed; the command `h1 X` sets an operator size that overrides this until changed. Size is 0 when `enabled` is false, when the round is not in `rounds`, or when the mean half-spread of the H1 symbols at the reference time is ≥ `off_if_half_spread_bp` (100). After a promotion (§4.D), H1 forgets the check result and uses `size_on_fail` until the operator types `h1 X`.
2. **Reference mid.** At the first decision with `t ≥ ref_mid_at` (48 s, [storm_start, −12]), record `mid48[sym]`. If it is missing at entry, use the current mid and log `h1_no_ref`.
3. **Entry**, for `t ∈ [entry_start, entry_last + 1.0)` (50 s to 56 s inclusive; [storm_start, −10] to [storm_start, −4]), when clock quality is in `clock_quality` (server or feed). For each symbol in `symbols` that is not blocked:
   - `target = h1_dir × round(size × L)`, `clip = clip_size(L, 4)`, `clip_interval = 2.0`, `ttl = 2.0`, `expected_cost_bp = walk(clip/L, "h1_entry") + fo_allow` (§12 item 1; the fallback exit uses its own phase the same way);
   - `limit_buy = min(mid48 × (1 + cap_bp/1e4), mid × (1 + max_from_mid_bp/1e4))` (cap 50 bp, max 150 bp); `limit_sell = max(mid48 × (1 − cap_bp/1e4), mid × (1 − max_from_mid_bp/1e4))`. If `cap_bp` is null, the 48-s cap is skipped (parity profile only).
   The OM sends clips at about 50, 52, 54 and 56 s (§5.3). There is **no crowding skip** and **no entry on `storm_formed`**.
4. **No forced exit.** From 58 s H1 emits nothing for its symbols, and S3 inherits the position at 80 s.
5. **Fallback exit.** At `fallback_deadline` (76 s, [storm_start, +16]), if `fv_usable` is false or `fv` has not ingested a message of this round's storm, set fallback mode. For `t ∈ [fallback_start, fallback_last + 1.0)` (84 to 98 s), emit `target = 0`, `clip = clip_size(L, 8)`, `reduce_only`, priority HEDGE, with `limit_sell = max(bid × (1 − fallback_through_touch_bp/1e4), mid × 0.985)` (buy side symmetric).
6. **Round-1 check** [I]. Record `mid58` and `mid90` (`check_t0`, `check_t1`) for `check_symbols` (HAVEN, PORT, ARCH).
   - `move_bp` = mean over those symbols of `h1_dir × (mid90 − mid58)/mid58 × 1e4`;
   - `cost_bp` = mean over our H1 fills of `side × (price − mid48)/mid48 × 1e4`, using fills with quality other than LIMIT_BOUND (all fills, logged, if none qualify);
   - **pass** iff there was at least one H1 fill, `move_bp > 0` and `cost_bp ≤ check_cost_frac × move_bp` (⅓). With no H1 fill at all (dry-run, or every clip skipped) the check **fails**;
   - `cost_ratio = Σ actual / Σ expected clip costs`; if it is ≥ `drop_rebuilders_cost_ratio` (1.5), remove `rebuilders` (RECON, MAT, GEN, REBLD) from H1 for rounds 2-3.
   Log `h1_check` with every input.
7. **Commands:** `h1 on|off`, `h1 0|0.5|1`.

### 6.2 S3, the engine taker (`s3`, TAKE)

- **Window and inputs.** Active for `t ∈ [start, end)` (80-540 s; [storm_start, +20] to [storm_end, 0]) in `rounds` [1, 2, 3], while `fv_usable` holds. Symbols: all except `exclude` (REIN) and blocked ones, insane ones (`symbol_sane`), and books that are empty, `wall_only` or missing a mid. `k = k_r12` (0.1) in rounds 1-2 and `k_r3` (0.2) in round 3. `ph = cost_phase(t)` is s3_start for 80-120 s and storm for 120-540 s. `cost = entry_cost_bp[ph] × cost_mult × 1e-4` and `band = exit_band_bp[ph] × 1e-4`. `th = k·sd + cost·mid`, `cur = ctx.exposure(sym)`.
- **Signal, by `edge_ref`.**
  - **"touch"** (default; playbook §4.1 rules 1-2 with the [testnet] cost term): `target = +L` if `μ − ask > th` and `cur < L`; `target = −L` if `bid − μ > th` and `cur > −L`. Otherwise a long exits (`target = 0`) when `μ < bid − xk·sd`, and a short when `μ > ask + xk·sd`; anything else holds and emits nothing. The entry edge is `μ − ask` or `bid − μ`.
  - **"mid"** (REC's rule, as r_sim backtested it on the consensus): with `e = μ − mid`, `target = +L` if `e > th` and `cur < L`, `−L` if `e < −th` and `cur > −L`; otherwise a long exits when `e < −(xk·sd + band·mid)` and a short when `e > xk·sd + band·mid`. The entry edge is `|e|`. It is the parity profile's and `abl_edge`'s setting; using it live needs the team's sign-off recorded in the playbook (§10).
  - `xk` is 0. Full size as soon as the threshold is crossed (no proportional sizing). S3 inherits the H1 book, because the rule uses `cur` whatever built it.
- **Entry prices** (target ±L): `limit_buy = min(μ − k·sd − limit_cost_frac·cost·mid, ask × (1 + through_touch_bp/1e4), mid × (1 + max_from_mid_bp/1e4))` and `limit_sell = max(μ + k·sd + limit_cost_frac·cost·mid, bid × (1 − through_touch_bp/1e4), mid × (1 − max_from_mid_bp/1e4))`, with `through_touch_bp` 50 and `max_from_mid_bp` 150, so never beyond mid ± 1.5% (the wall sits about 8% out).
  - With "touch" and `limit_cost_frac` 1.0 this is the playbook's `min(μ − k·sd − cost·mid, ask × 1.005)` plus the 1.5% cap, and at the threshold `μ − k·sd − cost·mid > ask`, so the order is marketable.
  - With "mid" and 0.5, `μ − k·sd − 0.5·cost·mid > mid·(1 + 0.5·cost) ≥ ask`, because `0.5·cost = walk + 36 bp` exceeds the half-spread. Config validation enforces these pairings (§4.A).
  - With `require_marketable`, a clip whose limit is not at or through the touch is skipped (logged once per symbol per 10 s). A `null` cap is disabled (parity only).
- **Exit prices** (target 0): a sell is `max(μ, bid × (1 − through_touch_bp/1e4), mid × (1 − max_from_mid_bp/1e4))`, so S3 never sells below fair; a buy is symmetric.
- **Order parameters:** `clip_qty = clip_size(L, 12)`, `clip_interval_s = 2.0`, `ttl_s = 2.0`, `reduce_only = (target == 0)`, `edge_usd_per_share` = the entry edge, `expected_cost_bp = walk(clip/L, ph) + fo_allow` (§12 item 1; flatten likewise), `ref_mid = mid`.
- **End of round.** Rounds 1-2 are flattened by §6.3. Round 3 holds to settlement, and S1-r3 adjusts at 740 s.
- **Scoring:** the sim and live logs attribute S3 P&L on our own fills by round. If rounds 1-2 are negative on fills again, set `s3.rounds = [3]` [I].
- **Commands:** `s3 on|off`, `s3 rounds 3`, `s3 costmult X`.

### 6.3 Flatten (`flatten`, HEDGE)

- In `rounds` [1, 2], for `t ∈ [start, end)` (540-568 s; [storm_end, 0] to [prelim, −2]), every symbol except `exclude` (REIN, whose short from the riskless ask is held) with `exposure ≠ 0` gets:
  - `target = 0`, `clip_interval = 2.0`, `ttl = 2.0`, `reduce_only`;
  - `clip = min(max(clip_size(L, 12), ceil(|exposure| / slots_left)), clip_size(L, 4))`, where `slots_left = max(1, floor((end − t) / clip_interval))`. A full L needs 12 base clips in 14 slots, so the clip grows, up to L/4, as soon as the book falls behind;
  - `limit_sell = max(bid × (1 − through_touch_bp/1e4), mid × (1 − max_from_mid_bp/1e4))` (50 bp and 150 bp), with the buy side symmetric.
- Flatten bypasses frozen symbols, because reducing risk is allowed.
- At `behind_alert_at` (560 s, [prelim, −10]), alert `flatten_behind` once per round for any symbol with `|exposure| > clip × slots_left`.
- If any position other than `exclude` is non-zero at `end`, log the alert `not_flat` once.
- **Command:** `flat on|off`.

### 6.4 S1-r3, settlement positioning (`s1r3`, SNIPE)

1. **Arm.** In round 3, on the final `damage_report` news, record `final_seq`. If that message was lost, a snapshot `damage` list that contains the round-3 final arms it with `final_seq = -1`. A late arm is accepted until `late_trigger_ok_until` (775 s).
2. **S.** Fire on the first `fv` with `seq_hi ≥ final_seq` (or, for −1, the first fv computed after the arm), with `diag.status_r3 == "final"` when diag is present; S is `fv.values[sym][0]`. If no such fv arrives within `engine_wait_s` (1.0 s) and `laws_fallback` is set, use S from `LawsCrossCheck.values()` for every symbol whose value is not None, with the wide ε on all of them, and raise the alert `s1r3_laws_fallback`.
3. **Gates.** A symbol is excluded, with the failing gate logged, unless all of these hold:
   - (a) it is in `symbols`;
   - (b) it is not in `season.case_diff` (the operator clears a symbol with `caseok SYM` once **An** has signed off, playbook §5.0);
   - (c) it is not frozen; a symbol announced after `announce_lock_from` (700 s) needs the operator's `s1ok SYM` as well;
   - (d) it is not excluded by the operator (`x1 SYM`);
   - (e) **laws cross-check, fail closed:** `|S − law| ≤ laws_tol[sym]` (0.02 or 0.25). If `values()` is None (a round's final report is still missing), every symbol fails. If the symbol's law value is None, it fails. If the laws module is unavailable (`deps.laws is None`), the `CLOSURE_SYMBOLS` (PORT, GRID, FERRY, GEN, ARCH, REBLD) fail, and the D/L-only symbols pass with the log `s1r3_no_laws`, because their settlement depends only on the reported damage and loss;
   - (f) **coverage:** a `CLOSURE_SYMBOLS` symbol fails when any of rounds 1-3 is "missing" (late join, `--fresh` restart, or a standby without the engine log, where the engine imputes closure);
   - (g) **law check:** with `require_law_check` (default **true**; playbook §4.3 gate 3 and §5.1), `law_check.passed(sym)`. The operator clears a symbol with `lawok SYM` (or `lawok all`).
4. **ε** is `eps[sym]`, or `eps_wide[sym]` for a `CLOSURE_SYMBOLS` symbol when any of rounds 1-3 is "gapped" (an age stretch above `long_gap_s` (10 s) without a storm observation on the merged stream, such as the 31.7-s outage). The D/L-only symbols have `eps == eps_wide`.
5. **Orders** go out in the same cycle for each passing symbol, at priority SNIPE, using all strategy room on each side so the position goes to ±L toward settlement: `RestIntent(key="s1r3:SYM:buy", side=BUY, price=round_px(S − ε, BUY), qty=None, edge_usd=max(0, S − ε − ask))` and `RestIntent(key="s1r3:SYM:sell", side=SELL, price=round_px(S + ε, SELL), qty=None, edge_usd=max(0, bid − S − ε))`. Whichever side is marketable takes stale quotes at once and the other rests; neither can fill worse than S ∓ ε. For REIN, emit `CancelIntent(scope="key", key="rein_ask", priority=SNIPE)` first. The OM places marketable rests first, by edge × quantity: at most about 24 actions within about 300 ms.
6. **Sweep.** Re-emit the same RestIntents every `fast_s` (0.5 s) until `fast_until` (750 s), then every `slow_s` (2 s) until `sweep_end` (778 s). Each re-emission is an idempotent ensure that tops up room freed by exchange cancels or fills (§4.B rests), and re-places everything after a `feed_down` cancel once that hold is released.
7. **Sanity** at `sanity_at` (756 s, never earlier; at 746 s it would have cancelled the profitable PORT, HAVEN and ARCH). For each stock, if `|mid − S| > max(sanity_eps_mult × ε, sanity_min_frac × S)` (3ε, 0.5%), cancel `key_prefix="s1r3:SYM:"`, exclude the symbol, and alert.
8. **Budget.** The core reserves the limiter from `block_from` (680 s) to close, blocking TAKE and HEDGE (cancels are raised to SNIPE meanwhile, §4.B), and prewarms every REST connection at `prewarm_at` (737 s). The `unknown_orders` hold does not block S1-r3 (§4.B breakers).
9. No flattening. Orders stay until the close. Rounds 1-2 run only `LawCheck`.
10. **Commands:** `s1r3 on|off`, `x1 SYM`, `s1ok SYM`, `lawok SYM|all`.
11. **Expected on the recording (§9.2).** 224 r3 was joined at storm age 160 with no rounds 1-2 data: PORT, GRID, FERRY, GEN, ARCH and REBLD fail gate (f); the candidates are ISLE, AQUA, RECON, MAT, HAVEN and REIN (gate (g) is cleared by a scripted `lawok all`, standing in for the operator, because no rounds 1-2 check could run). In 225 r3 all 12 are candidates, the closure symbols with the wide ε (225 r2 is gapped).

### 6.5 REIN riskless ask (`rein_ask`, HEDGE)

- `U = cap − slope × max(loss_known − attach, 0) = 90 − 0.035 × max(L_known − 1300, 0)`.
- **Order:** `RestIntent(key="rein_ask", symbol="REIN", side=SELL, price=round_px(U + offset, SELL), qty=None)`, using all REIN sell strategy room. The offset is 0.02.
- **When:** emit at ROUND_START and every `refresh_s` (5 s), only while the market is open, from `start` (0 s) until `end_r12` (the close) in rounds 1-2, or `end_r3` (740 s, the final) in round 3. At `end_r3`, emit `CancelIntent(key="rein_ask", SNIPE)`.
- The price is non-marketable unless REIN's bid exceeds U + 0.02, and then the guard rules apply.
- If REIN is frozen, emit `CancelIntent(key="rein_ask", SNIPE)` and stop until it is unfrozen. Announcements on REIN, HAVEN or any island name freeze REIN through the links (§6.7).
- REIN stays out of H1, S3 and flatten. **Command:** `rein on|off`.

### 6.6 ETF lead-lag (`etf_leadlag`, TAKE, default OFF)

- **Signal:** `NAV = Σ basket weight × component mid`, and `gap = (NAV − ETF mid)/ETF mid`. Decide every `decision_s` (1 s) for `t ∈ [start, end)` (60 s to 738 s; [storm_start, 0] to [final, −2]).
- **Enter** when `|gap| > max(enter_gap_min, enter_hs_mult × ETF half-spread)` (0.5% or 3× the half-spread):
  - `target = ±L`, toward the gap's sign;
  - `limit = ETF mid × (1 + cap_gap_frac × gap)`, a marketable limit capped at mid × (1 + gap/2). Skip if that is not through the touch, and **never walk the book uncapped**;
  - `clip = clip_size(L, 2)` (½ L, playbook §6: never more than ½ L in one clip), `clip_interval = decision_s`, `ttl = ttl_s` (1 s); partial fills are accepted.
- **Exit** to 0 at the first of: `|gap| < exit_gap` (0.1%), `hold_s` (8 s) since entry, or a change of sign. Use `reduce_only` with `limit = touch ∓ exit_through_touch_bp` (50 bp).
- When enabled, ARCH and REBLD are removed from H1 and S3 (`ctx.leadlag`). Default OFF (TEST-FIRST). **Command:** `lag on|off`.

### 6.7 Announcement freeze and manual overrides (`announce`, SNIPE)

1. **Freeze.** On an `announcement` news message, in the same decision cycle (well within 100 ms): `frozen = named ∪ links[named]`, plus every ETF whose basket contains a frozen symbol (`freeze_etfs`); with no valid symbols and `freeze_all_if_no_symbols`, freeze all 12. Emit `CancelIntent(scope="symbol", symbol=s, priority=SNIPE)` for each frozen symbol, which the OM always sends as `CANCEL_ALL?symbol=s` and which also cancels orders still in flight once they are acked (§4.B). Raise the alert `announcement` with the headline and the symbols. Links (`announce.links`): HAVEN ↔ REIN ↔ MAT (through L); GRID → GEN; PORT, GRID or FERRY → RECON; every island company → HAVEN, REIN and MAT.
2. **While frozen**, the runner drops every intent for the symbol except cancels and flatten's reduce-only targets, and S1-r3 excludes it except as in §6.4 gate (c).
3. **Overrides.** The analyst writes `run_dir/adjust.json` (`{"PORT": {"mult": 1, "add": -2.0}, "_overrides": {"settle.insurance.HAVEN.attach": 450}}`); the console's `f`, `m` and `a` write `adjust_console.json`; the adapter merges both into the engine's file (§4.C). The engine rebuilds the ETFs from the adjusted stocks. All three files are rotated at a season reset.
4. **Unfreeze.** `u SYM` returns the symbol to normal. `ua SYM` enters the adjusting state for `adjust_window_s` (45 s): the strategy emits S3-style targets capped at ±`adjust_cap_frac` × L (50%), priority TAKE, in any open phase except `t < 3 s` and round 3 after 680 s. It stops (holds) once the house has covered `adjust_stop_coverage` (80%) of the distance, `(mid − mid_at_announce)/(μ − mid_at_announce) ≥ 0.8`. After 45 s the symbol returns to normal, with the override still in place.

### 6.8 Precedence and actions

- **Target owner per symbol:** announce (adjusting), then s1r3 (rests only), flatten, etf_leadlag, h1, s3. The windows barely overlap by design; S3 starts at 80 s, after H1's last clip at 56 s.
- **Action budget:** bot cap 150 per 61 s, with floors KILL 0 (+20 overdraft), SNIPE 1, HEDGE 4 and TAKE 10. REC uses about 1,229 actions per season and peaks at the cap at 80-120 s.
- **Cancels** are never below HEDGE, and SNIPE inside the round-3 reservation (§4.B). Operator and safety cancels: `k`, `cx`, shutdown and the season-reset cancel at KILL (with the overdraft); the feed-down cancel at SNIPE.

---

## 7. Failure handling

| Failure | Detection | Action | Recovery |
|---|---|---|---|
| WebSocket drop on one connection (abrupt, no close frame) | recv error or that connection's watchdog (storm 3 s, pre/after 8 s, closed and unknown 35 s) | Reconnect at 0.25 s, then every 0.5 ± 0.1 s, capped at 2 s; one subscribe per connection | The other connection keeps the stream; the normalizer drops the second copies, replays and trade re-sends |
| Feed down > 10 s (no connection up, or no frame on any) | `FeedHealth.down_since` | `cancel_all(SNIPE)` once and hold `feed_down`, round 3 included | Release after a released snapshot plus 2 fresh storm obs (storm) or the snapshot alone; S1-r3 re-places by its sweep |
| No storm obs for 6 s in the storm | `last_storm_obs_age_s` | S3 and H1 skip (`fv_usable`); risk rejects non-reducing orders | Automatic |
| Storm age gap > 10 s on the merged stream | `StormCoverage` | Round "gapped": S1-r3 uses the wide ε for the closure symbols | Lasts the season |
| Late join, `--fresh` restart, standby without the engine log | `StormCoverage` | Earlier rounds "missing": S1-r3 excludes the closure symbols; the law check has not run, so gate (g) needs `lawok` | Operator |
| Lost messages | seq gaps | Recovery from the next relevant released snapshot (1-s hold, `last_seq > wm`) | Automatic; about 30 s worst case |
| Spurious or real season change on feed evidence only | Normalizer trigger (a), (c) or (d) | Provisional: hold `season_suspect`, buffer messages, GET_TIME now; no cancel, no reset | Confirmed by a −1 snapshot, a consistent snapshot, a new comp_id or `season new`; rejected after 60 s or by `season keep` (state and engine kept) |
| Season change confirmed | −1 snapshot, comp_id, confirmation | Cancel-all (KILL) if orders are live; new engine file; full state reset; replay the pending buffer | – |
| Engine snapshot bug present | Startup probe | `forward_snapshots = "never"` plus alert; `--live` refused | Fix the engine |
| Engine stale (pending > 6 s) | `health()` | S3 skips; H1 fallback exit if the engine is not live by 76 s | Automatic |
| Engine errors (3 in a row) | `consecutive_errors` | Alert `engine_down`; S3 off; S1-r3 laws fallback | Command `eng restart`: rebuild the adapter with `restore=True` from the engine log |
| Laws module fails to load | Startup | Alert; S1-r3 excludes the closure symbols | Fix the path |
| POST timeout, transport error after sending, or 5xx | `outcome_unknown` | UNKNOWN; counted as resting for room; hold `unknown_orders` after 3 s (S1-r3 exempt) | Reconcile adopts or resolves; no retry |
| POST failed before sending (connect, TLS, stale socket) | `sent=False` | One retry on a fresh connection; then REJECTED | Automatic |
| Stale keep-alive connection at 740 s | Idle time, socket peek | Prewarm every connection at 737 s; reopen idle (> 10 s) or closed sockets before use | Automatic |
| 2xx PLACE without an order id | Ack parse | UNKNOWN plus alert `ack_no_id` | Reconcile adopts |
| Cancel outcome unknown | Same | CANCEL_UNKNOWN; still counted as resting | Reconcile; OPEN again if the order is still there after the grace |
| Cancel denied by the budget | `try_acquire` returns [] | The cancel stays queued and retries every step | Automatic |
| HTTP 401 / 403 | Status | Sticky hold `auth`, alert | Operator checks the key, then `r` |
| HTTP 429 | Status | Freeze 5 s (KILL exempt), pad the ledger, hold 5 s | Automatic |
| 5xx or transport error streak (3) | `risk.on_result` | Hold `api_errors` 10 s | Automatic |
| 4xx reject | Status | REJECTED; rate-limited log; 5 in 30 s in one symbol disables it | Operator `e SYM` |
| GET failures | Reconcile | Skip the cycle; 5 consecutive hold `reconcile_down` | Automatic |
| Stale `/positions` row | Quiet-symbol guard | Row skipped for that symbol; ack fills keep the position; alert after 5 s of skips | Automatic |
| Partial fill | Ack or open-row remaining | Partial Fill; a clip remainder is cancelled at ttl; a rest remainder stays | – |
| Exchange limit-room or self-trade cancel | Absent with no position delta | CANCELED; the next RestIntent ensure re-sends | Automatic |
| Position mismatch | Delta unexplained for 2 cycles | Accept the exchange value; foreign Fill; alert | – |
| Clock unknown, extrapolated or paused | `ClockReading` | H1 needs server or feed quality; risk needs `running`; S1-r3 is event-driven | Automatic |
| Closed-phase snapshot with a next-day date | Trading-phase rule | Ignored for round, date and anchors | – |
| Round transition | `clock.round_events()` | ROUND_START: per-round resets, flat check (REIN excluded), REIN re-post | – |
| Pause between rounds | Server state or repeated snapshot time | `running=False`, so no orders | Automatic |
| Restart mid-season | `restart_plan` | Restore engine, laws, coverage and state if comp_id matches; hold `restart` | Operator `r` |
| Dry-run standby promoted to live | `saved.dry_run != now` | Drop dry orders, positions, cash and ledger; rebuild from the exchange; keep the engine log; H1 at `size_on_fail` | Operator `h1 X`, then `r` |
| Process death | – | Operator cancels all in the browser; the standby runs `--live --host H --run-dir <its own dir>` (promotion) | – |
| Kill | `k`, KILL file, SIGINT | FROZEN; executor frozen (queued PLACEs purged); CANCEL_ALL on the kill worker; follow-up CANCEL_ALLs until every kill-set record is resolved | Restart, or `unfreeze-kill` |
| KILL file present at startup | cli | Refuse to start | Remove the file |
| Strategy exception | try/except in the runner | Log; disable after 3 in 60 s | Operator `<strategy> on` |
| Core exception | Shell wrapper | Log; 10 per minute hold `exceptions` | Operator |
| Drawdown > $20k in a round | `on_mtm` | Sticky hold `drawdown` | Operator `r` |
| Bad fills (3 consecutive > 3× table) | `on_fill` | Disable the symbol | Operator `e SYM` |
| Insane engine value | `symbol_sane` | Engine strategies skip the symbol; clears 10 s after it is sane | Automatic |
| Announcement | News | Freeze and cancel (in-flight orders included) | Operator `u` or `ua` |
| Case diff | Startup `diff_case` | Value diff: S1-r3 excludes the symbols; timing diff: `--live` refused without `--accept-timing-diff` | `caseok SYM\|all\|timing` after **An** signs off |
| Console stdin closed | EOF | Reader stops, `console_eof` logged | KILL file and signals still work |

---

## 8. `config/default.json` (group A writes it verbatim)

```json
{
  "version": 2,
  "live_allowed": true,
  "exchange": {
    "host": null, "rest_base": null, "ws_url": null, "case_url": null,
    "user_agent": "ctc26-se-bot/1.0 (python-stdlib)",
    "key_env": "CTC_BOT_KEY",
    "timeout_order_s": 2.0, "timeout_get_s": 3.0, "get_retries": 1,
    "workers": 12, "kill_workers": 1, "idle_recycle_s": 10.0,
    "price_decimals": 2, "allow_market_orders": false,
    "team_actions_per_min": 180, "exchange_max_resting_per_symbol": 200, "requests_per_s_team": 600,
    "max_queue_age_s": 0.5
  },
  "feed": {
    "connections": [{"name": "ws0", "local_addr": null}, {"name": "ws1", "local_addr": null}],
    "channels": ["news", "quotes", "trades", "orderbook"],
    "ws_open_timeout_s": 5.0, "ws_ping_interval_s": 20.0, "ws_ping_timeout_s": 20.0, "ws_max_size": 8388608,
    "reconnect_first_s": 0.25, "reconnect_every_s": 0.5, "reconnect_jitter_s": 0.1, "reconnect_max_s": 2.0,
    "reconnect_auth_s": 5.0, "reconnect_throttle_max_s": 10.0,
    "watchdog_s": {"storm": 3.0, "pre_storm": 8.0, "aftermath": 8.0, "closed": 35.0, "unknown": 35.0},
    "snapshot_hold_s": 1.0, "season_reset_seq_drop": 50, "trade_id_cache": 50000,
    "provisional_feed_resets": true, "season_confirm_s": 60.0, "reset_merge_s": 180.0, "pending_buffer_max": 5000,
    "feed_down_cancel_s": 10.0, "storm_obs_stale_s": 6.0, "record_raw": true
  },
  "clock": {
    "server_poll_s": 1.0, "server_fresh_s": 3.0, "feed_fresh_s": 10.0, "snapshot_fresh_s": 35.0,
    "offset_window": 30, "pause_same_time_s": 20.0, "max_extrapolate_s": 40.0
  },
  "app": {
    "tick_s": 0.25, "reconcile_s": 0.5, "persist_s": 5.0, "status_s": 2.0,
    "kill_file": "KILL", "adjust_file": "adjust.json", "run_root": "runs",
    "restart_auto_resume": false, "kill_followup_s": 0.25, "shutdown_wait_s": 2.0, "exceptions_per_min_hold": 10,
    "shutdown_kill_wait_s": 5.0, "persist_debounce_s": 1.0
  },
  "orders": {"target_ttl_s": 0.6, "rest_tolerance_frac": 0.05, "rest_max_orders_per_key": 2, "price_eq_tol": 0.005},
  "engine": {
    "dir": "../engine", "params": "engine_params_fast.json", "mode": "thread",
    "library_seasons": [1, 600], "library_exclude_seasons": [],
    "persist_file": "engine_state_{run_id}_e{epoch}.jsonl", "stale_s": 6.0, "periodic_compute_s": 1.0,
    "compute_warn_ms": 150, "slow_alert_ms": 1000, "down_after_errors": 3,
    "forward_snapshots": "relevant", "require_snapshot_fix": true,
    "probe_file": "ctcbot/models/data/snapshot_probe.jsonl"
  },
  "laws": {"module": "../laws/se_laws.py", "mode": "thread"},
  "cost": {
    "model": "../testnet/analysis/books/cost_model.json",
    "thr_mult": 2.0, "fo_allow_bp": {"stock": 36.0, "etf": 17.28}, "fo_window_s": 8.0, "check_tolerance_bp": 3.0,
    "phases": [
      ["open", ["open", 0], ["open", 3]], ["pre", ["open", 3], ["storm_start", -10]],
      ["h1_entry", ["storm_start", -10], ["storm_start", -2]], ["pre", ["storm_start", -2], ["storm_start", 0]],
      ["early", ["storm_start", 0], ["storm_start", 12]], ["h1_exit", ["storm_start", 12], ["storm_start", 20]],
      ["s3_start", ["storm_start", 20], ["storm_start", 60]], ["storm", ["storm_start", 60], ["storm_end", 0]],
      ["after", ["storm_end", 0], ["prelim", -2]], ["rep_prelim", ["prelim", -2], ["prelim", 30]],
      ["after", ["prelim", 30], ["final", -2]], ["rep_final", ["final", -2], ["final", 20]],
      ["after", ["final", 20], ["close", 0]]
    ]
  },
  "symbols": {
    "PORT":  {"kind": "stock", "h1_dir": 1,  "entry_cost_bp": {"s3_start": 315, "storm": 123, "after": 101}, "exit_band_bp": {"s3_start": 114.4, "storm": 24.2}, "eps": 0.07, "eps_wide": 0.25, "laws_tol": 0.25},
    "ISLE":  {"kind": "stock", "h1_dir": 1,  "entry_cost_bp": {"s3_start": 165, "storm": 107, "after": 99},  "exit_band_bp": {"s3_start": 43.2, "storm": 16.4},  "eps": 0.02, "eps_wide": 0.02, "laws_tol": 0.02},
    "AQUA":  {"kind": "stock", "h1_dir": 1,  "entry_cost_bp": {"s3_start": 170, "storm": 112, "after": 98},  "exit_band_bp": {"s3_start": 46.7, "storm": 18.5},  "eps": 0.02, "eps_wide": 0.02, "laws_tol": 0.02},
    "GRID":  {"kind": "stock", "h1_dir": 1,  "entry_cost_bp": {"s3_start": 205, "storm": 107, "after": 99},  "exit_band_bp": {"s3_start": 62.8, "storm": 17.1},  "eps": 0.06, "eps_wide": 0.25, "laws_tol": 0.25},
    "FERRY": {"kind": "stock", "h1_dir": 1,  "entry_cost_bp": {"s3_start": 138, "storm": 117, "after": 102}, "exit_band_bp": {"s3_start": 31.7, "storm": 21.4},  "eps": 0.09, "eps_wide": 0.25, "laws_tol": 0.25},
    "RECON": {"kind": "stock", "h1_dir": -1, "entry_cost_bp": {"s3_start": 195, "storm": 110, "after": 99},  "exit_band_bp": {"s3_start": 57.2, "storm": 18.4},  "eps": 0.02, "eps_wide": 0.02, "laws_tol": 0.02},
    "MAT":   {"kind": "stock", "h1_dir": -1, "entry_cost_bp": {"s3_start": 164, "storm": 110, "after": 100}, "exit_band_bp": {"s3_start": 42.8, "storm": 18.0},  "eps": 0.02, "eps_wide": 0.02, "laws_tol": 0.02},
    "GEN":   {"kind": "stock", "h1_dir": -1, "entry_cost_bp": {"s3_start": 161, "storm": 109, "after": 100}, "exit_band_bp": {"s3_start": 42.0, "storm": 17.8},  "eps": 0.11, "eps_wide": 0.25, "laws_tol": 0.25},
    "HAVEN": {"kind": "stock", "h1_dir": 1,  "entry_cost_bp": {"s3_start": 342, "storm": 138, "after": 104}, "exit_band_bp": {"s3_start": 125.4, "storm": 31.2}, "eps": 0.02, "eps_wide": 0.02, "laws_tol": 0.02},
    "REIN":  {"kind": "stock", "h1_dir": 0,  "entry_cost_bp": {"s3_start": 100, "storm": 100, "after": 98},  "exit_band_bp": {"s3_start": 13.1, "storm": 13.0},  "eps": 0.02, "eps_wide": 0.02, "laws_tol": 0.02},
    "ARCH":  {"kind": "etf",   "h1_dir": 1,  "entry_cost_bp": {"s3_start": 129, "storm": 73, "after": 58},   "exit_band_bp": {"s3_start": 46.2, "storm": 19.1},  "eps": 0.17, "eps_wide": 0.25, "laws_tol": 0.25},
    "REBLD": {"kind": "etf",   "h1_dir": -1, "entry_cost_bp": {"s3_start": 101, "storm": 69, "after": 55},   "exit_band_bp": {"s3_start": 33.1, "storm": 17.3},  "eps": 0.12, "eps_wide": 0.25, "laws_tol": 0.25}
  },
  "sizing": {"scale": 1.0, "clip_div": {"s3": 12, "h1": 4, "h1_exit": 8, "flatten": 12, "flatten_max": 4, "leadlag": 2}},
  "rate": {
    "bot_cap_per_min": 150, "window_s": 61.0,
    "floors": {"KILL": 0, "SNIPE": 1, "HEDGE": 4, "TAKE": 10},
    "kill_overdraft": 20, "throttle_freeze_s": 5.0, "requests_per_s_cap": 50
  },
  "risk": {
    "drawdown_usd": 20000.0, "pnl_source": "auto",
    "unknown_hold_s": 3.0, "unknown_grace_s": 1.0,
    "fat_finger_frac": 0.25, "snipe_band_frac": 0.60, "max_marketable_from_mid": 0.015,
    "max_clip_frac": 0.5, "max_snipe_frac": 2.0, "max_resting_per_symbol": 8,
    "block_marketable_before": ["open", 3.0],
    "fill_sanity_n": 3, "fill_sanity_mult": 3.0,
    "reject_disable_n": 5, "reject_disable_window_s": 30.0,
    "http_5xx_streak": 3, "http_5xx_hold_s": 10.0, "reconcile_fail_hold_n": 5, "pos_skip_alert_s": 5.0,
    "feed_resume_storm_obs": 2, "strategy_exceptions_disable": 3, "strategy_exceptions_window_s": 60.0,
    "strategy_reenable_s": 30.0, "strategy_reenable_max": 2, "reconcile_stale_hold_s": 10.0
  },
  "guards": {
    "opening": {"enabled": true, "until": ["open", 3.0], "max_half_spread_bp": 300.0},
    "engine_sanity": {"sd_mult": 6.0, "mid_frac": 0.15, "clear_after_s": 10.0}
  },
  "strategies": {
    "h1": {
      "enabled": true, "rounds": [1, 2, 3],
      "symbols": ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "HAVEN", "ARCH", "RECON", "MAT", "GEN", "REBLD"],
      "size_r1": 0.5, "auto_size_from_check": true, "size_on_pass": 1.0, "size_on_fail": 0.5,
      "ref_mid_at": ["storm_start", -12], "entry_start": ["storm_start", -10], "entry_last": ["storm_start", -4],
      "clip_interval_s": 2.0, "ttl_s": 2.0, "cap_bp": 50.0, "max_from_mid_bp": 150.0, "off_if_half_spread_bp": 100.0,
      "rebuilders": ["RECON", "MAT", "GEN", "REBLD"], "drop_rebuilders_cost_ratio": 1.5,
      "check_symbols": ["HAVEN", "PORT", "ARCH"], "check_t0": ["storm_start", -2], "check_t1": ["storm_start", 30],
      "check_cost_frac": 0.3333,
      "fallback_deadline": ["storm_start", 16], "fallback_start": ["storm_start", 24], "fallback_last": ["storm_start", 38],
      "fallback_through_touch_bp": 50.0, "clock_quality": ["server", "feed"]
    },
    "s3": {
      "enabled": true, "rounds": [1, 2, 3], "start": ["storm_start", 20], "end": ["storm_end", 0],
      "k_r12": 0.1, "k_r3": 0.2, "xk": 0.0, "cost_source": "config", "cost_mult": 1.0,
      "edge_ref": "touch", "limit_cost_frac": 1.0,
      "clip_interval_s": 2.0, "ttl_s": 2.0, "through_touch_bp": 50.0,
      "max_from_mid_bp": 150.0, "require_marketable": true, "exclude": ["REIN"], "max_engine_age_s": 2.0
    },
    "flatten": {
      "enabled": true, "rounds": [1, 2], "start": ["storm_end", 0], "end": ["prelim", -2],
      "behind_alert_at": ["prelim", -10],
      "clip_interval_s": 2.0, "ttl_s": 2.0, "through_touch_bp": 50.0, "max_from_mid_bp": 150.0, "exclude": ["REIN"]
    },
    "s1r3": {
      "enabled": true,
      "symbols": ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"],
      "engine_wait_s": 1.0, "laws_fallback": true,
      "fast_until": ["final", 10], "fast_s": 0.5, "slow_s": 2.0, "sweep_end": ["close", -2],
      "sanity_at": ["final", 16], "sanity_eps_mult": 3.0, "sanity_min_frac": 0.005,
      "block_from": ["final", -60], "prewarm_at": ["final", -3], "announce_lock_from": ["final", -40],
      "late_trigger_ok_until": ["close", -5], "require_law_check": true, "long_gap_s": 10.0
    },
    "rein_ask": {
      "enabled": true, "symbol": "REIN", "offset": 0.02, "cap": 90.0, "attach": 1300.0, "slope": 0.035,
      "refresh_s": 5.0, "start": ["open", 0], "end_r12": ["close", 0], "end_r3": ["final", 0]
    },
    "etf_leadlag": {
      "enabled": false, "rounds": [1, 2, 3], "symbols": ["ARCH", "REBLD"],
      "start": ["storm_start", 0], "end": ["final", -2],
      "enter_gap_min": 0.005, "enter_hs_mult": 3.0, "exit_gap": 0.001, "hold_s": 8.0, "decision_s": 1.0,
      "cap_gap_frac": 0.5, "ttl_s": 1.0, "exit_through_touch_bp": 50.0
    },
    "announce": {
      "enabled": true, "freeze_etfs": true, "freeze_all_if_no_symbols": true,
      "adjust_window_s": 45.0, "adjust_cap_frac": 0.5, "adjust_stop_coverage": 0.8,
      "links": {
        "HAVEN": ["REIN", "MAT"], "REIN": ["HAVEN", "MAT"], "MAT": ["HAVEN", "REIN"],
        "GRID": ["GEN", "RECON", "HAVEN", "REIN", "MAT"], "PORT": ["RECON", "HAVEN", "REIN", "MAT"],
        "FERRY": ["RECON", "HAVEN", "REIN", "MAT"], "ISLE": ["HAVEN", "REIN", "MAT"], "AQUA": ["HAVEN", "REIN", "MAT"],
        "RECON": [], "GEN": [], "ARCH": [], "REBLD": []
      }
    }
  },
  "law_check": {
    "enabled": true, "horizon_s": 16.0, "min_move_frac": 0.005, "fail_sign_n": 2, "fail_size_frac": 0.5,
    "slopes": {
      "prelim": {"PORT": [0.66, 0.82, 1.03], "ISLE": [0.76, 0.86, 1.00], "AQUA": [0.71, 0.79, 0.97], "GRID": [0.63, 0.74, 0.93],
                 "FERRY": [0.71, 0.84, 0.97], "RECON": [0.47, 0.68, 1.01], "MAT": [0.42, 0.65, 1.01], "HAVEN": [0.49, 0.59, 0.82]},
      "final": {"PORT": [0.65, 0.80], "ISLE": [0.76, 0.87], "AQUA": [0.72, 0.80], "GRID": [0.65, 0.80],
                "FERRY": [0.72, 0.86], "RECON": [0.46, 0.69], "MAT": [0.43, 0.65], "HAVEN": [0.48, 0.59]}
    }
  },
  "paths": {
    "research_case": "../data/case.json",
    "recording": "../testnet/recordings/testnet_s224_s225.jsonl",
    "history_dir": null, "fv_cache_dir": null
  },
  "sim": {
    "api_latency_s": 0.0, "engine_latency_s": 0.0, "lat_rows": 0, "lat_rep_rows": 1,
    "pause_between_rounds_s": 30.0, "team_actions_per_min": 180,
    "follow_on": true, "impact": true, "impact_credit": true, "replenish": true, "cost_mult": 1.0,
    "fo_window_s": 8.0, "beta_bp_per_L": {"stock": 100.0, "etf": 48.0},
    "impact_kernel_t": [0.0, 2.0, 4.0, 10.0, 20.0, 30.0], "impact_kernel_bp": [27.6, 29.7, 31.4, 41.0, 35.8, 26.8],
    "impact_tau_s": 47.0, "impact_cut_s": 150.0, "book_levels_x": [0.1, 0.17, 0.45, 0.7, 3.0],
    "recorded_latency_s": 0.3, "recorded_follow_on": true, "recorded_library_seasons": [226, 600],
    "cancel_at_close": true, "workers": 8
  }
}
```

Config semantics that are not obvious:
- `null` for an optional cap disables that cap.
- `exchange.host`, `rest_base`, `ws_url` and `case_url` are null on purpose: host mode needs `--host` (§4.D cli), so no test or default run can reach a real host.
- Clip windows (`entry_last`, `fallback_last`) are inclusive of a clip at that time. Other windows are `[start, end)`.
- `sizing.scale` multiplies the strategy limits (targets, clips via `clip_div`, rests with `qty=None`, and the risk clip caps). Exchange room always uses the case limits as well.
- `live_allowed` is not merged (§4.A config); a profile that sets it false can never be used with `--live`.
- `engine.persist_file` is formatted with the run id and the epoch.

---

## 9. Test plan and acceptance

### 9.0 Data and flags

- `SP = /private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad`. Override it with `CTCBOT_HISTORY` and `CTCBOT_FV_CACHE`; the scratchpad is session storage, so a later session points these at its own copy.
- Tests that need missing data `SkipTest` through `testpaths.require`, and tests that need other groups' modules through `testpaths.integration`.
- `CTCBOT_REQUIRE_DATA=1` turns both kinds of skip into failures. **The acceptance run sets it**, so §9.2-9.4 cannot pass with nothing run.
- Slow tests, including all of §9.2-9.3 and D `latency`, run only with `CTCBOT_SLOW=1`. The default `unittest discover` must finish in under 3 minutes.
- **Acceptance command** (from `BOT`): `CTCBOT_SLOW=1 CTCBOT_REQUIRE_DATA=1 SYS/.venv/bin/python -m unittest discover -s tests -v`, plus the scripts of §9.2-9.3.

### 9.1 Unit tests and acceptance per group

**A (infrastructure)**
- **config:** the default loads and validates; an overlay plus `--set` works (`strategies.s3.k_r3=0.3`); each of these raises `ConfigError`: a missing symbol, a bad anchor, a cap > 170, `edge_ref` "touch" with `limit_cost_frac` 0.5, `edge_ref` "mid" with 0.8, an `http://` rest_base, duplicate connection names, `--set live_allowed=true`. `live_allowed` is false after loading an overlay that says so, whatever comes after it. The hash is stable across key order. `diff_from_default` lists exactly the leaves an overlay changed.
- **case_schedule:** on the research case, `Schedule` gives open 0, storm_start 60, storm_end 540, prelim 570, final 740, close 780 and speed 30. `cost_phase(t)` equals an inline copy of r_sim's `phase_of` for t from 0 to 780 in steps of 0.25. `diff_case` of a copy with one GRID site weight changed gives symbols {GRID, ARCH} and `timing=False`; a timing change gives `timing=True` and no symbols; a map change flags all 12. `strategy_limits` at scale 0.25 gives PORT 50 and ARCH 12; `clip_size(12, 12) == 1`. `market_to_t("1802-05-10T10:15:00") == ("1802-05-10", 90.0)`.
- **clock:**
  - Synthetic storm obs every 2 s with uniform 0-300 ms delivery delay: |t error| ≤ 0.35 s.
  - A server tick slope of 1.0005 over 780 s: error ≤ 0.3 s.
  - Pause detection from repeated 09:30:00 snapshots; `running` stays false after a season reset until time advances.
  - Closed-phase snapshots: after 225 r1 closes, the `1802-05-10T16:52:30` and `1802-05-11T01:55:00` snapshots leave the reading at round 1, date 1802-05-10, and `round_events()` returns nothing for them.
  - **Recording replay** (feed only, no server), using `RecordingSource` timestamps as recv times: the estimated round-open unix times are within 0.5 s of the findings' fit, hardcoded in the test: 224 r3 `1792766628.455`; 225 r1 `1792767650.037`, r2 `1792768610.536`, r3 `1792769571.126` (10:43:48.455, 11:00:50.037, 11:16:50.536 and 11:32:51.126 EDT on 23 Oct 2026). `round_events()` over the recording is exactly (e0, 3), (e1, 1), (e1, 2), (e1, 3), (e2, 1).
- **normalize:**
  - Over the recording (one connection): the released `engine_relevant` snapshots have `last_seq` exactly [695, 764, 250, 416, 828] in that order (the skeleton's `FeedMerger` gives the same on this file); trades with duplicate `trade_id` are dropped (≥ 37); news seqs are unique per epoch; exactly 2 confirmed season resets, both `snapshot_last_seq`, and 0 provisional ones.
  - The literal-rule mutation (relevant iff `last_seq > max_seq`) is caught: a test subclass that uses it forwards none of the five.
  - Synthetic: an announcement with `"seq": 0` after `storm_formed` seq 0 is forwarded and triggers nothing; two announcements with seq 0 and different headlines are both forwarded, the same one twice only once; a duplicate round-1 `storm_formed` late in round 3 triggers nothing; a round-1 `damage_report` with an unseen seq delivered during round 2 is forwarded and triggers nothing; a conflicting seq with no trigger is dropped and counted; a new-season `storm_formed` (another storm, seq 0, max_seq 800) gives one provisional reset, and then (i) a −1 snapshot confirms it, (ii) a consistent snapshot confirms it, (iii) `confirm_season_reset` confirms it, (iv) `reject_season_reset` restores dedup (a re-sent seq 700 is dropped again) and `wm`; a −1 snapshot repeated in a pause resets once; a `force_season_reset("comp_id")` followed by the −1 snapshot resets once; the 1-s hold; a duplicate snapshot; None quotes and empty books raise nothing.
- **feed_merge** (the playbook's "two connections, merge and dedup"): copy `drop_windows`, `ServerIndex`, `make_copy` and the arrival-order merge of `bot_skeleton/test_feed_merge.py` (allowed by §0), and feed the two simulated copies as `ws0`/`ws1` SourceFrames through one `FeedNormalizer`. For both scenarios ("independent" and "half shared"), 5 seeds each (20 under SLOW): every news seq that either copy received is forwarded exactly once, with its content (N1); the season resets happen at 224→225 and 225→226 (N2); every forwarded relevant snapshot covers a seq that was neither forwarded nor covered before, and none is forwarded twice (S1, S2); every `trade_id` of either copy is forwarded once (T1); per symbol and type, forwarded book and quote timestamps strictly increase (B1); `FeedHealth.round_max_age_gap_s`, computed on the merged stream, exceeds 10 s in 225 r2 only when both copies were dark.
- **sources:** `build_sources` makes one source per `feed.connections` entry and passes `local_addr` to the injected connect; each `WsSingleSource` sends exactly one frame per connection within 10 s, uses reconnect delays of 0.25 then 0.5 ± 0.1 (never above 2.0), and reconnects after `watchdog_s()`; a `ws://` URL or a host other than `ws_url`'s raises before connecting; with `NETWORK_ENABLED` false, a real connect raises. `RecordingSource` emits 44,296 frames plus 5 down/up pairs, and with `t_from/t_to` only the frames in that window.
- **rest** (fake transport):
  - headers carry the custom UA; `X-API-Key` goes only to the rest_base host, and a `case_url` on another host is fetched without it; an `http://` URL raises before any I/O;
  - without a `LiveToken`, or with one for another host, order actions never call the transport and GETs do; `LiveToken(..., object())` raises; `_ISSUE` is referenced only in `rest.py` and `app/cli.py` (grep);
  - an order action that fails at connect is retried once on a fresh connection and then returns `sent=False`; one that fails after writing is never retried; a KILL CANCEL_ALL is retried once; GETs are retried once;
  - an idle (> 10 s) or peer-closed connection is reopened before use; `prewarm_all` touches every worker and the kill worker;
  - `freeze` purges queued PLACEs (answered `sent=False`, "purged") and answers later ones at once, while cancels and GETs still run; a KILL request is served by the kill worker while all 12 workers are blocked in slow POSTs;
  - prices round buy-down and sell-up; the parsers handle list, dict and nested shapes, and a 2xx PLACE without an id parses to `order_id=None`;
  - the key string never appears in results, logs or exception texts, including a transport error whose message contains it.
- **ratelimit:** random load for 30 virtual minutes never puts more than 150 non-KILL actions (170 including KILL) in any 61-s window; floors hold, reservations block and `reserved()` reports them, a 429 freezes for 5 s, `seconds_until` is correct, `try_acquire` returns `[]` on denial without recording anything, and the `cap` setter clamps to `max_cap`.

**B (state and execution)**
- **books:** touch precedence by `server_ts`; None sides; wall-only at a half-spread over 300 bp.
- **positions:** fills give the right position and cash; the `infer_price` formulas; MTM; the quiet-symbol guard: a row whose stamp shows a PLACE in flight, or whose action counter moved (a PLACE sent, or an ack received, after the stamp), is skipped and the internal position kept.
- **orders:** every legal transition passes and an illegal one raises; the prune; `drop_dry`.
- **order_manager** (fake limiter clock, fake risk where needed):
  - a target of +200 with clip 16 and interval 2 keeps at most one live clip, spaces sends ≥ 2 s apart and cancels a confirmed remainder at ttl; after the DELETE is acked (CANCEL_ACKED) the next clip goes at the next step, while room still counts the acked remainder;
  - `reduce_only` never crosses zero; under a short budget the higher-priority, larger-edge target goes first; cancels precede places in a step; room is recomputed after each PLACE in a step (two rests on one side never exceed the room together);
  - cancel priorities: a TAKE record's remainder cancel goes at HEDGE, and at SNIPE inside a reservation; a denied cancel stays queued, the record stays OPEN, and the cancel goes out at the first step with budget; no record is ever CANCEL_PENDING without its request;
  - CancelIntents that overlap produce one cancel per record; scope "symbol" always sends `CANCEL_ALL?symbol=`, and a SENDING record in scope is cancelled as soon as its ack arrives OPEN;
  - rests: a RestIntent with `qty=None` is placed once, topped up after an exchange cancel, replaced on a price change and never exceeds 2 orders per key; at `sizing.scale` 0.25 an S1-r3 rest never exceeds the scaled room (S1-r3 ends at ±L_s, the REIN ask at −50); marketable rests are placed before resting ones, larger edge first;
  - self-cross is blocked and at most 8 orders rest per symbol; dry records never reconcile-fill; a 2xx PLACE without an id becomes UNKNOWN;
  - kill: one CANCEL_ALL at KILL priority even with the budget exhausted; follow-ups on a late OPEN ack and on a cycle that still shows open rows, at most one in flight and none beyond the overdraft; `kill_status` reports complete only after a clean cycle.
- **reconcile:** a lost ack becomes UNKNOWN and is adopted by attribute match; a SENDING record whose order shows in GET_OPEN is adopted (no foreign duplicate), and its later ack is applied idempotently; a CANCEL_PENDING or CANCEL_ACKED record that a GET still shows keeps its state (no ValueError); a CANCEL_ACKED record resolves at its first absent cycle; an absent order fully explained by the position delta becomes FILLED in the same cycle, and an unexplained one CANCELED after 2 cycles; absence is not counted for a GET sent before the ack; partial fills; foreign orders are adopted; an unexplained delta becomes a foreign fill only after a second cycle, and not at all if the late ack explains it.
- **risk:** each check rule in §4.B, including rule 2 with an exempt strategy and rule 5 against the scaled limit; each breaker, including `auth` on a 401 (and no per-symbol reject count); and the mode precedence (FROZEN > NO_NEW_RISK > NORMAL).
- **property** (acceptance): 20,000 steps of random intents against a fake exchange that enforces the packet's limit rules, with random latency, timeouts, 5xx, partial fills, GETs that are processed before or after concurrent PLACEs and DELETEs, and acks that arrive after the next GET, give 0 limit violations, 0 windows over 150 actions, 0 foreign fills, and no live record older than 10 s once the fake exchange goes quiet and two reconciles run.

**C (models and strategies)**
- **engine_adapter:** thread mode with a fake `SeasonState` (listener called, stale tracking, error counting, reset, adjust-file merge with the console entry winning, `adjust_engine.json` written only by the adapter); `version` increases on every publish, also across `reset_season` and after an adjustment-only change, while `engine_version` follows the engine; `reset_season` clears adjustments, overrides and announcements and rotates the three adjust files; inline mode with the real engine on research season 401 round 1 up to t = 200 matches a direct `SeasonState` replay exactly; slow: inline with standard params and library (1, 400) on all of season 401 reproduces `fv_holdout_401.json` entries within 5e-4; **the probe returns True on `engine/fairvalue.py` and False on `engine/fairvalue_orig.py`**, using the committed probe file.
- **cost_model:** reproduces the §6 table (entry cost within 0.5 bp, exit band within 0.1) from `cost_model.json`. `walk_bp` with a side equals an inline copy of r_sim's `walk_cost` at 200 random points.
- **coverage:** synthetic rounds give full, gapped (a 12-s stretch without observations but with track points) and missing (a 12-s stretch without any sample; no data); over the recording, season 224 is {1: missing, 2: missing, 3: gapped} and season 225 is {1: full, 2: gapped, 3: full}.
- **laws:** on research seasons 401-405, `LawsCrossCheck.values()` after round 3's final report is within `laws_tol` of `settlement.json` for every symbol, with messages fed in order and again with 10% of storm obs dropped; `values()` never runs the integrator (a counting fake `ClosureIntegrator` is called only at storm ends); closure symbols are None while a round is missing, D/L-only ones are not.
- **law_check:** a scripted report with matching moves passes; 2 sign misses fail and list those symbols; `lawok SYM` removes one; no check at all fails `passed` until `lawok all`; a check without `fv_after` is void.
- **h1:** clips at 50/52/54/56 s with the right targets and caps; sizes by round from a pass or fail check; zero fills is a fail; dropping the rebuilders; the fallback exit when the engine is not live at 76 s; no exit otherwise; a skip when clock quality is poor or the symbol is blocked; `on_promotion` forces `size_on_fail` until `h1 X`.
- **s3:** for both `edge_ref` values, the threshold and band per phase and k per round, against hand-computed numbers for PORT and ARCH; the entry, exit and hold zones (touch: exit a long only below the bid); the limit formula, every cap and `require_marketable`; skips for stale fv, insane values, REIN, lead-lag and frozen symbols; an inherited H1 long is held until the exit condition.
- **flatten:** targets 0 only in rounds 1-2 within the window; REIN is kept and is not `not_flat`; a full-L position from 548 s grows the clip (never above L/4) and is flat by 568 s on a fake that fills every clip; `flatten_behind` fires at 560 s when it cannot finish.
- **s1r3:** arming from news and from a snapshot; firing on `seq_hi`; every gate, including (e) fail-closed with `values()` None, with a None law value, and with `laws=None` (closure symbols excluded, D/L-only kept); (f) with a missing round; (g) default on, cleared by `lawok`; the wide ε for closure symbols after a gapped round; edge-ordered RestIntents; the REIN-ask cancel before REIN orders; sweep cadence 0.5 s then 2 s; sanity at 756 s, not before; the laws fallback after 1 s.
- **rein_ask:** U at L_known = 0, 1300, 1500; the refresh; the cancel at 740 s in round 3 and on a freeze.
- **etf_leadlag:** off by default; when on, the entry cap at mid × (1 + gap/2), clips of L/2, the three exits, and ARCH/REBLD removed from H1 and S3.
- **announce:** a link-expansion table test for each of the 12 symbols; cancels in the same cycle; `u` and `ua` behaviour (50% cap, 80% coverage stop, 45 s); the S1-r3 lock after 700 s.
- **runner:** precedence, the opening guard, frozen filtering, exception isolation, and disabling after 3 exceptions.

**D (application)** — core tests use fakes for every A, B and C collaborator through `BotCore(factories=...)`; tests marked integration use the real modules.
- **core:** a scripted mini-round drives frames to intents to requests; reconcile pairing; ROUND_START exactly once per round (a closed snapshot dated the next day produces none); a provisional reset holds `season_suspect`, cancels nothing, keeps the engine and is (i) confirmed by a new comp_id, with the buffered messages replayed into the new engine, (ii) rejected after 60 s with all state kept; a −1-snapshot reset followed within 180 s by a new comp_id counts as one reset, as does the reverse order; feed down > 10 s produces one SNIPE CANCEL_ALL, also in round 3 after S1-r3 fired, and the S1-r3 rests come back after the release; `cx`, shutdown and a season reset at 700 s of round 3 (inside the reservation) still go out at KILL; the kill puts CANCEL_ALL and the `"freeze"` control first and nothing after them; the round-3 reservation blocks TAKE after 680 s; `law_check.on_report` is called before `engine.submit` for a rounds 1-2 report; `caseok` and `lawok` routing; promotion drops dry orders and asks for the H1 size; a 401 holds `auth`.
- **kill** (shell, fake transport, the acceptance for the playbook drill "no orders left 1 s after a kill"): 12 PLACEs are in flight on the 12 workers, each answered after 1.5 s, and the fake exchange registers each order only when it answers. After `k`, the CANCEL_ALL reaches the transport within 100 ms (wall clock), no PLACE reaches the transport after it, the 12 late OPEN acks trigger follow-up CANCEL_ALLs, and 1 s after the last POST resolves the fake exchange has no open order. Total kill actions ≤ 1 + `kill_overdraft`.
- **latency** (SLOW, wall clock): the test builds a run dir whose `state.json` and engine log hold season 225 up to 225 r3 t = 690 s, then runs `cli.main(["--replay", REC, "--replay-from", <690 s>, "--replay-to", <780 s>, "--replay-speed", "1", "--run-dir", DIR, "--set", "app.restart_auto_resume=true", "--set", "strategies.s1r3.require_law_check=false"])`, so the real shell, the thread-mode engine and the executor run against the offline SimExchange. From the receipt (`recv_mono`) of the round-3 final `damage_report` frame to the hand-off of each S1-r3 PLACE to the executor: p95 ≤ 300 ms and max ≤ 500 ms. Loop lag (the overshoot of a 10-ms `asyncio.sleep`, sampled every 50 ms) has p99 ≤ 50 ms during 740-745 s. Both are reported with the GIL-enabled Python 3.14 in use.
- **shell** (async): replay the first 300 s of the recording at speed 50 with a fake API and two sources, with no exceptions, a clean task cancel on `q`, SIGINT handled (the second SIGINT's CANCEL_ALL goes through the same `RestClient`, a dry no-op without a token), and a closed stdin that stops the console reader without spinning.
- **console:** every command in §4.D parses; garbage never raises. **persist:** save and load round-trip; atomic writes; every `restart_plan` case, including dry → live and live → dry promotion; engine log names carry the run id, so two `--fresh` starts never share a file.
- **cli** (`socket.create_connection` patched to raise in every test):
  - the default is dry-run; host mode without `--host` exits 2; `--live` exits 2 without a TTY (`isatty` injected), after 3 wrong phrases (`stdin` injected), with `--replay`, with a `live_allowed: false` profile, after a failed GET_CASE, on a timing diff without `--accept-timing-diff`, after a failed probe, and with a KILL file present;
  - a correct phrase creates exactly one `LiveToken` and the prompt shows the `diff_from_default` lines and the foreign-orders warning when open orders exist;
  - the key (from a file or env, with a trailing newline) never appears in stdout, stderr, `events.jsonl` or `feed_raw.jsonl` (grep); an invalid key exits 2 without echoing it; a group-readable key file warns;
  - `--self-test` passes offline.
- **log:** redaction of `JsonlLog`, `FrameRecorder` and the exception hooks (a thread exception whose text contains the key is logged as `***`); the `websockets` loggers are at WARNING; every line is valid JSON.

**E (simulation)**
- **exchange:** each rule in §4.E returns the right status and counts violations; resting orders are cancelled at each round close and by `close_all`; P&L reconciles (cash + marks); the schemas parse with A's parsers.
- **fillmodel:** equals inline copies of r_sim's formulas (walk increment, follow-on, impact kernel and decay, replenishment) on synthetic histories.
- **harness:** two identical runs give identical event-log hashes; the within-instant ordering is as specified.
- **recording:** `RecordedMarket` yields the 5 down/up control pairs and the comp_id changes at the two season boundaries.
- **research:** §9.3.

### 9.2 Integration acceptance: the testnet recording (SLOW)

Run `sim/run_recording.py --mode dryrun` and `--mode book`, using the real engine inline with fast params and library [226, 600], and a scripted `lawok all` at the start of season 224 (the operator's answer to a late join, §6.4 step 11).

**Both runs must show:** (1) 0 exceptions; (2) 0 violations of every type (no `limit_shrink`, `limit_room_cancel`, `self_trade`, `rate`, `bot_cap` or `max_resting`); (3) 0 HTTP 429; (4) at most 150 bot actions in any 61 s; (5) at most 8 resting orders per symbol at any time; (6) every order terminal after the run's final `close_all` plus two reconcile cycles.

**Event checks:**
- exactly 2 confirmed season resets (225 and 226), each counted once although both the −1 snapshot and the comp_id change report it, and 0 provisional resets;
- 5 reconnect pairs;
- the engine received exactly the relevant snapshots 695, 764, 250, 416 and 828;
- coverage as in §9.1 C, so 225's closure symbols use the wide ε;
- S1-r3 fired in 224 r3 and 225 r3 in the same decision cycle as the first fv with `seq_hi ≥ final_seq` (virtual time; the wall-clock bound is D `latency`). In 224 r3 it placed orders only in a non-empty subset of ISLE, AQUA, RECON, MAT, HAVEN and REIN, and none in PORT, GRID, FERRY, GEN, ARCH or REBLD; in 225 r3 every symbol not placed has a logged gate;
- no marketable order in the first 3 s of any round;
- in book mode, every symbol except REIN flat by 568 s in 225 r1 and r2, or a logged `not_flat`.

**Reported and non-blocking:** P&L by strategy and round, flagged if S3 in season 225 falls outside −15k..+15k. For reference, S3 alone in 225 lost −3.8k / −2.2k in independent book replays, and the S1-r3 sweeper found at least $924 in 224 r3 and $927 in 225 r3.

### 9.3 Integration acceptance: research seasons 401-440 against REC (SLOW)

**Baseline.** REC under the measured-cost fill model, holdout cache, seasons 401-440, recomputed for this design from `r_sim` with the `w02_sens.py` REC configuration:

| Variant | Mean | sd | se | p5 | Hit | R1 | R2 | R3 | Cost paid | Actions | Max in 60 s | Components |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| REC | **19,660** | 12,924 | 2,043 | 2,072 | 0.97 | 6,376 | 6,524 | 6,760 | 10,627 | 1,227 | 150 | H1 −5,723, S1 −9, S3 27,626, S3x −2,233 |
| REC +2 s | 18,966 | 12,964 | 2,050 | 1,452 | 0.97 | 6,170 | 6,261 | 6,535 | 10,637 | 1,227 | 150 | |
| REC H1 half | 17,644 | 10,265 | 1,623 | 3,911 | 1.00 | | | | 7,566 | 1,019 | 150 | |
| REC no H1 | 14,017 | 9,407 | 1,487 | 2,277 | 0.97 | | | | 5,228 | 772 | 150 | S3 16,313, S3x −2,287 |
| REC costs ×1.5 | 15,048 | 12,689 | 2,006 | −1,401 | 0.93 | | | | 14,286 | 1,086 | 150 | |

REC per-season P&L for 401..440, in order, for paired comparison:

`39726, 10763, 16896, 17789, 10488, 14979, 2100, 8724, 19606, 9497, 7562, 13957, 34683, 6837, 11038, 14500, 12384, 24360, 16981, 7937, 27665, 24751, 14242, 7971, 20350, -800, 25529, 34043, 40862, 58202, 19675, 8238, 35068, 16984, 35795, 1537, 40414, 14642, 30649, 29787`

For context, the 200-season holdout REC is 19,772 (`w02_sens_holdout.out`).

**Parity run** (`--profile config/rec_parity.json --fv cache`; the same fill model, latency 0, report-window lag 1 row). `rec_parity.json` (E) contains exactly:
- `"live_allowed": false`;
- `rate.window_s` 60, all floors 0;
- `strategies.h1`: `size_r1` 1.0, `auto_size_from_check` false, `size_on_fail` 1.0, `cap_bp` null, `max_from_mid_bp` 800, `off_if_half_spread_bp` 100000, `drop_rebuilders_cost_ratio` null;
- `strategies.s3`: `edge_ref` "mid", `limit_cost_frac` 0.0, `through_touch_bp` null, `max_from_mid_bp` 800, `require_marketable` false;
- `strategies.flatten`: `through_touch_bp` null, `max_from_mid_bp` 800;
- `strategies.s1r3`: `symbols` all except REIN, `require_law_check` false;
- `strategies.rein_ask.enabled` false;
- `risk.max_marketable_from_mid` 0.08;
- `risk.fill_sanity_n` 1000000, i.e. no fill-sanity breaker, which REC does not have (§12 item 5).

**Pass criteria:**
- **Mean P&L** within ±10% of 19,660, i.e. 17,694 to 21,626.
- **Paired per-season P&L** against the list above: Pearson r ≥ 0.90 and median |difference| ≤ 2,000.
- **H1 + S3 + flatten components** within ±12% of REC's 19,670.
- **Actions per season** within ±20% of 1,227. The bot also counts cancels, so a little higher is expected.
- **Max actions in any 60 s** ≤ 150, and 0 violations.
- **Diagnostic, must be reported:** the same run with `strategies.h1.enabled=false` lands within ±10% of 14,017.

**Default-config run** (`--fv cache`, no profile; S3 uses the playbook's touch rule):
- Blocking: p5 ≥ −6,000, hit ≥ 0.85, 0 violations, no window over 150, and mean ≥ 0.60 × REC (11,796), a floor that catches defects rather than a forecast.
- **`abl_edge`** (the default plus `s3.edge_ref` "mid" and `limit_cost_frac` 0.5, i.e. the default with REC's rule) must land within 0.70-1.10× REC (13,762 to 21,626); this is the old default-run band, now applied where it was calibrated.
- **Attribution, reported:** E runs each one-change overlay in `sim/ablations/` on top of the default and reports its mean, its paired difference against the default, and the residual between the default and REC that the six do not explain:

  | Overlay | Change toward REC |
  |---|---|
  | `abl_edge` | `s3.edge_ref` "mid", `limit_cost_frac` 0.5 |
  | `abl_h1size` | `h1.size_r1` 1.0, `auto_size_from_check` false, `size_on_fail` 1.0 |
  | `abl_caps` | the parity caps: `h1.cap_bp` null, `max_from_mid_bp` 800 (h1, s3, flatten), `s3.through_touch_bp` null, `require_marketable` false, `flatten.through_touch_bp` null, `risk.max_marketable_from_mid` 0.08 (sim only, `"live_allowed": false`) |
  | `abl_rein` | `rein_ask.enabled` false |
  | `abl_s1r3` | `s1r3.symbols` without REIN, `require_law_check` false |
  | `abl_floors` | `rate.window_s` 60, all floors 0 |
- If the default fails only the mean floor while `abl_edge` passes, E reports it to the team as evidence for the S3 sign-off (§10), not as a bot defect.

Expected divergence sources, which explain gaps but do not excuse a failure: clip timing (the 2-s interval runs from the last send, while REC's cap runs per 2-s row), cancels counting as actions, the bot's S1-r3 differing from REC's S1 taker (REC's S1 is −9), and edge ordering under the rate cap.

### 9.4 Unit-test command

From `BOT`: `SYS/.venv/bin/python -m unittest discover -s tests -v`. **It must pass with 0 failures and 0 errors.** Skips are allowed only for missing data, missing modules of other groups, or `SLOW` tests, and never under `CTCBOT_REQUIRE_DATA=1`.

---

## 10. Out of scope and open items

- **Not built:** report snipes (S2, and S1 in rounds 1-2), the clock-only fallback, passive quoting and ETF create/redeem. The REST client supports create/redeem, but nothing calls it.
- **S3 edge reference: team sign-off needed.** The bot follows playbook §4.1 (edge against the touch, price cap `min(μ − k·sd − cost·mid, ask × 1.005)` and mid ± 1.5%). The REC backtest behind the 19.7k figure measured the edge from the mid. If the team prefers REC's rule live, record that decision in the playbook, then run with `--set strategies.s3.edge_ref=mid --set strategies.s3.limit_cost_frac=0.5`; until then `abl_edge` is a sim-only comparison.
- **Second connection on a second network path:** set `feed.connections[1].local_addr` on competition day if a hotspot is available; whether the drops are server-side is still unmeasured (TESTNET_FINDINGS §7 #5).
- **Slack questions still open** (TESTNET_FINDINGS §8): resting orders across closes, the tick size, IOC, price protection, whether a 429 counts toward the rate limit, private fills, and whether `competition_id` changes between test seasons. The tolerant parsers, the provisional season reset and conservative defaults cover each of them.
- **Testnet scoring season:** use `--profile config/testnet_quarter.json` (25% size, library [300, 600]; raise the lower bound above the testnet's current season). Log S3 P&L on fills per round, and apply §6.2 scoring.

---

## 11. Rejected critique items

No critique item was rejected outright: every one is applied. The items below were applied in a different form than the critique proposed; the right-hand columns say how and why.

| Item | Critique asked for | This design does instead | Why |
|---|---|---|---|
| 13 (clip cadence) | A 2xx DELETE moves the record straight to CANCELED | The record goes to CANCEL_ACKED, which no longer blocks the next clip and is not part of the exposure, but still counts in room until the first reconcile cycle sent after the ack (one cycle, not two) | The cadence problem is solved the same way (the next clip waits for the ack, not for two reconcile cycles). Going straight to CANCELED would drop the remainder from room before `/positions` shows any fill made just before the cancel, which can overstate room for up to a cycle and produce `limit_shrink` violations in the property test and the sim |
| 7 (stale `/positions`) | Apply a row only if its GET was sent after the last locally applied fill, or add the fills acked since the send | The quiet-symbol guard: apply a row only if the symbol had no PLACE in flight when the cycle started and no PLACE or ack happened in it since | The critique's first rule misses the reverse race (the exchange fills an order, the GET sees the fill, the ack arrives after the GET), and its alternative double-counts exactly that fill. The guard covers both orders; an ack-free symbol is always applied |
| 5 (S3 validation) | Validate touch with 1.0 and mid with 0.5 | Touch requires 1.0; mid requires a value in [0, 0.5] | The critique's own parity profile uses mid with 0.0, which a strict "mid with 0.5" rule would refuse. Both values are marketable at the threshold; values above 0.5 are not |
| 5 (acceptance) | (implied) the old default-run band | The 0.70-1.10× REC band moves to `abl_edge`; the default run gets a 0.60× floor | The band was calibrated on REC's mid rule. With the playbook's touch rule as the default, the expected mean is unmeasured, and a band that cannot be derived from the sources would be a guess |
| 17 (watchdog) | Use the 35-s pause watchdog when the phase is unknown | 35 s only when both the clock phase and the normalizer's `phase_hint()` are unknown | A restart in the storm has an unknown clock for a few seconds but a known feed phase; a 35-s watchdog there would hide a dead connection |
| 2 (provisional reset) | Keep the previous epoch's engine unless comp_id or a −1 snapshot confirms | Same, plus a third confirmation: a trading-phase snapshot of the trigger's round and date whose `last_seq` fell by more than 50; the normalizer clears its dedup state during the pending window and merges it back on rejection; and "duplicate" means same key **and** same fingerprint (type, round, storm, time), so a new season's reused seqs are conflicts that can trigger, while re-sends and late gap-filling deliveries cannot | Whether `competition_id` changes between test seasons is unknown, and a −1 snapshot comes only before a season's first message. Without the third rule, a bot that reconnects after the boundary could never confirm a real reset. Without clearing dedup, a real new season's messages would be dropped as duplicates while pending. Judging duplicates by seq alone (as the skeleton does) would either let a re-sent round-1 `storm_formed` reset the season or stop a new season from ever triggering |
| 4a (laws unavailable) | Fail closed: exclude the six closure symbols | Same, and in addition every symbol fails when `values()` is None or its own law value is None | A missing final report means D or L is incomplete, which affects all twelve; with the module present, failing only the closure names would let the D/L names trade on an S the cross-check could not confirm |
| 22 (stale connections) | Recycle idle connections; retry once only when provably not sent | Same, plus the one idempotent exception: the kill's CANCEL_ALL is retried once after a transport error | A kill that fails on a stale connection must not wait for the 0.25-s follow-up loop; a second CANCEL_ALL cannot create risk |

---

## 12. Integration changes (after all five groups delivered)

These are the only changes the integrator made to the design; `INTEGRATION_REPORT.md` has the evidence.

1. **Expected clip cost includes the follow-on allowance** (C: `strategies/base.expected_clip_cost_bp`, used by H1, S3 and flatten). `expected_cost_bp = walk(clip/L, phase, side) + fo_allow` (36 bp stocks, 17.28 bp ETFs), the per-clip cost inside the S3 threshold (`entry_cost_bp / thr_mult`). With the walk alone, our own consecutive same-side clips (follow-on about 25 bp per L/4 in 8 s, plus impact) put real clip costs at 1.5-3× the walk, so the fill-sanity breaker disabled 8 of 12 symbols in research season 401 and 11 of 12 on the season-225 books (a disabled symbol takes only reducing orders, so S1-r3 placed only REIN in 225 r3). The H1 round-1 cost ratio (§6.1 step 6) stays on the tables alone, as specified ("round-1 costs ≥ 1.5× the tables"): H1 subtracts `fo_allow` again before summing. Note for the team: at base cost in the research sim that ratio is already 1.05-1.72 (median 1.39 over 401-440), so the rebuilders are dropped in 6 of 40 seasons although the findings say to keep them at base cost; `drop_rebuilders_cost_ratio` needs a calibration decision.
2. **Harness: jobs before frames at the same instant** (E: `sim/harness.py`). With frames first, the news decision cycle at 2j saw a clip sent at 2(j−1) at its 2-s ttl before the 2j reconcile could mark it FILLED, and sent a remainder cancel for a filled order (a 404 and a wasted action: 227 of 504 cancels in default-config season 401, about 80% of parity cancels by E's count; 0 of either after the change). The production shell is unaffected: it has no defined order between job loops and frames.
3. **Pause watchdog** (D: `BotCore.watchdog_s`). In a pre-season pause the clock reads pre_storm (frozen at 09:30:00) with `running` false, while the server sends one frame per ~30 s. The 8-s pre_storm watchdog then declared the feed down every 30 s (4 `feed_down` cancels and alerts per season start in the replay) and, in host mode, would have torn down and reconnected both connections every 8 s. While the clock is known and not running, the "closed" value (35 s) applies.
4. **Offline exchange record** (D: `cli`). `--replay` and `--sim` write `run_dir/sim_exchange.json` (the simulated exchange's violations, rate windows, resting maxima, live orders left and P&L) after the shell stops, so a replay can be checked against the exchange rules independently of the bot's own logs.
5. **Parity profile without the fill-sanity breaker** (E: `config/rec_parity.json`, `risk.fill_sanity_n` 1000000). REC has no such breaker. Under the parity caps (8% from mid) a research fill lands on the next consensus row, and after news that row has often already moved 130-310 bp from the decision mid, so the breaker disabled 1.7 symbols per season and cost about 2.0k per season against REC (mean 17,658, median |diff| 2,148). Under the default caps (1.5% from mid) and the item-1 expected cost it disables nothing in research seasons 401-440 or on the season-225 books.
6. **`--sim` ends by itself** (E: `SimSource(tail_s=2.0)`). `SimSource` used to wait for `stop()` after the market's last frame, so `run_bot.py --sim research:NNN` never exited unless the operator typed `q` (a scripted run with stdin at EOF ran on with no market until killed). It now returns `tail_s` virtual seconds after the last frame, like `RecordingSource`, so the shell stops through `sources_done` and the core's shutdown cancel; `tail_s=None` restores the old behaviour.

---

## 13. Hardening changes (after the spec, safety and robustness reviews)

These changes apply the three reviews of the integrated bot. They supersede the earlier sections where they differ. `STATUS.md` has the finding-by-finding table and the evidence. §3 (`TargetIntent`) and §8 (`config/default.json`) above already include the new fields and keys.

**Order actions and the budget**

1. **The KILL ceiling is absolute** (A `ratelimit`, D `core.default_factories`).
   - KILL is granted while `used + n ≤ max_cap + kill_overdraft`, whatever the current cap. A `b N` below the used count therefore no longer blocks `cx`, the kill follow-ups or the shutdown and season-reset cancels.
   - The core builds the limiter with `max_cap = team − 10 − kill_overdraft` (150). The bot alone, kill included, never passes 170 = team − 10 actions per window, which leaves 10 for humans during a kill and 30 otherwise.
   - Validation requires `bot_cap + kill_overdraft ≤ team − 10`, `kill_overdraft ≤ 20`, `team_actions_per_min ≤ 180` and `kill_workers ≥ 1`.
2. **Unsent results refund their ticket** (A `ActionLimiter.refund`, B OM). A result with `sent=False` (connect or TLS failure, purge, freeze, stale queue) gives its ticket back. A re-queued cancel or cancel-all backs off 0.25 s, doubling to 1 s, and is never re-sent by the `om.step` that follows the result. A network blip no longer drains the budget.
3. **Queued PLACEs expire** (A `RestExecutor`, `exchange.max_queue_age_s` 0.5).
   - A PLACE or ETF request that waited in the executor queue longer than 0.5 s (every worker busy on a slow exchange) is answered `sent=False, error="stale_queue"` and refunded. It was priced on an old book, and sending it late could put more than the cap into one exchange window: the ledger's 61-s window covers only 1 s of delay.
   - Cancels and GETs never expire. Risk ignores `stale_queue` and `closed` results in its error streak.
4. **Kill workers never prewarm** (A `RestExecutor.prewarm_all`). The kill worker reopens an idle connection on use, so a kill no longer waits behind a slow prewarm `GET /competitions/time` (up to about 6 s).
5. **Repeated kills are budgeted** (B `OrderManager.kill`, `rearm_kill`; D `core._kill`, `shell`).
   - While a kill is in progress, `k` (or another kill request) only re-arms the follow-up, which keeps the 0.25-s gap and the KILL budget.
   - Only the second signal makes the synchronous hard cancel; later signals do nothing.
   - The kill's post-write CANCEL_ALL retry (§11 item 22) is kept as designed.
   - `cx` prints `cancel-all QUEUED` and alerts `cx_queued` when the budget denied it.

**Shutdown**

6. **`q`, signals and the end of the sources take the kill path** (D `core.begin_shutdown`, `shutdown_complete`; `shell`).
   - SIGINT, SIGTERM and SIGHUP run `k`. `q` and `sources_done` call `begin_shutdown`, which is the kill: freeze, CANCEL_ALL and follow-ups.
   - The shell keeps the loop, the jobs and the executor running until the kill is complete (every kill-set record terminal and two clean reconcile cycles) or `app.shutdown_kill_wait_s` (5 s) passes, and no order action is outstanding.
   - Then it calls `core.shutdown` (the report, and a last cancel if anything is live), persists and exits. Late PLACE acks therefore get their follow-up cancels, and nothing can be placed during the wait.

**Prices**

7. **The from-mid cap covers the aggressive side** (B `risk` rule 8). The 1.5% cap applies to every TAKE or HEDGE order that is marketable **or** on the aggressive side of mid (a buy above mid, a sell below), not only to orders marketable against our view of the touch.
8. **Feed-stale rule** (B `risk` rule 10b). With no frame on any connection within the watchdog (`FeedHealth.up` false), a marketable or aggressive TAKE or HEDGE order may only reduce (`feed_stale`). The reviewer's book-age rule was not adopted:
   - On the recording, H1's entry books are p50 5.2 s and p90 19 s old at order time, because the pre-storm book does not change.
   - A 3-s/8-s book-age rule would have refused 33 approved H1 entries and 24 S3 clips.
   - The feed rule refused none of the approved orders on the recording.
9. **The REIN ask offers all its room within one cycle** (C `rein_ask`). Risk caps a HEDGE order at 0.5 L_s, so the riskless ask needs two orders. While the ask is live but short of the REIN sell room, it is re-emitted at every decision cycle instead of at its next 5-s refresh, so the second order follows within one cycle (0.25 s). Two alternatives were rejected because the B property test booked foreign fills with them:
   - an OM top-up in the same step: two same-price orders sent at once made the reconciler's adoption ambiguous;
   - exempting passive rests from `max_clip_frac`: a full-room rest broke one seed of the slow test.
10. **Live bounds** (A `config.live_bound_problems`, D `cli.live_refusals`). `--live` is refused when a safety band is loosened beyond its bound (`max_marketable_from_mid` ≤ 0.03, `fat_finger_frac` ≤ 0.5, `snipe_band_frac` ≤ 0.8, `max_clip_frac` ≤ 1, `max_snipe_frac` ≤ 2, `max_resting_per_symbol` 1-16, `drawdown_usd` ≤ 40,000, `fill_sanity_n` 1-10) or when market orders are allowed. Sim-only profiles may still loosen them (the parity profile uses 8% from mid).

**Order manager**

11. **`step` isolates every intent** (B OM).
    - Each cancel intent, rest and target runs in its own try/except. An exception costs that intent (`exception` event, `step_errors` counter), never the requests already built.
    - `_try_place` is atomic: a record whose request was never handed out goes to REJECTED, and the ticket is refunded.
    - `set_intents` validates every intent's fields and drops a malformed one (`om_bad_intent`).
    - `PositionBook.expire_inflight` forgets an in-flight PLACE after 10 s, so a lost result can no longer skip a symbol's `/positions` rows for the rest of the run.
12. **Windows and the H1 grid** (A `types.TargetIntent`, B OM, C H1, S3, flatten).
    - `TargetIntent.valid_until` (mono) stops new clips at the strategy window's end: H1 entry at 57 s, its fallback at 99 s, S3 at 540 s and flatten at 568 s. Before, a target stamped up to 0.6 s earlier could clip at or after the end.
    - `TargetIntent.slot_anchor` puts clips on the grid `anchor + k × clip_interval_s`. The OM sends at most one clip per grid slot and cancels a remainder at the next grid point. H1 anchors at `entry_start`, so its clips go at 50, 52, 54 and 56 s plus latency instead of drifting (at 0.6 s of latency the 4th clip used to fall out of the window).
    - At latency 0 the schedule is exactly the old one.
13. **First clips are judged against the tables** (A `TargetIntent.first_clip_cost_bp`, B OM, C strategies; `cost.fo_window_s` 8). A clip with no own same-side clip in the symbol within 8 s gets `expected_cost_bp = walk` (the phase table), and a follow-on clip keeps `walk + fo_allow` (§12 item 1). The fill-sanity breaker is therefore 3× the table for first clips, as the spec says, without the false disables that §12 item 1 fixed for follow-on clips.

**Strategies**

14. **H1 round-1 check** (C H1).
    - Only entry fills (the symbol's H1 direction) enter the check, so fallback-exit sells cannot pass a failing check.
    - The cost ratio compares each symbol's **first** entry clip, the only clip the tables describe, with the walk table, and ignores LIMIT_BOUND fills.
    - The check runs once per season: after a restart past 90 s the saved result stands.
    - With this ratio the rebuilders stay at base cost and are dropped when first clips cost 1.5× the tables (the integration report's open item G3 is answered this way, subject to the team's calibration).
15. **S3 freshness** (C S3, `strategies.s3.max_engine_age_s` 2). S3 skips while the engine's oldest pending message is older than 2 s. Before, the general 6-s gate let S3 trade on values about 5 s old when a compute took 2-3 s. The core alerts `engine_slow` when a compute exceeds `engine.slow_alert_ms` (1,000).
16. **Flatten "behind"** (C flatten). At 560 s the alert compares the exposure with the **base** clip (L/12) × the slots left. With the grown clip it could fire only with about a full limit still open, so it never fired.
17. **S1-r3** (C S1-r3).
    - A stock first placed after `sanity_at` (a late arm, or a gate cleared by `lawok`, `s1ok` or `caseok` after 756 s) gets the step-7 sanity check before its first placement.
    - ETF lead-lag blocks ARCH and REBLD only before `strategies.etf_leadlag.end` (738 s). S1-r3 starts later, so lead-lag never removes the ETFs from the settlement trade; this corrects the §4.C notation, and playbook §4.5 removes them from H1 and S3 only.
18. **Strategy exceptions** (C runner).
    - The automatic disable goes through `set_enabled(False)`, so a resting strategy cancels its orders.
    - It is undone after `risk.strategy_reenable_s` (30 s), at most `risk.strategy_reenable_max` (2) times per season, and at every season reset. It is not persisted across a restart.
    - An operator's `<name> off` is never undone.

**Feed, clock and seasons**

19. **`last_seq`** (A normalize). Only an explicit integer −1 starts a season. A snapshot whose `last_seq` is missing, null, non-integer or below −1 is a bad frame (`snapshot_bad_last_seq`): it is neither held nor a trigger.
20. **Late copies of the previous season** (A normalize). For `feed.reset_merge_s` (180 s) after a confirmed reset, a message is dropped (`prev_season_message`) when all of these hold:
    - its (round, date) is the previous season's;
    - the new season already has a calendar;
    - its date is at least 30 days off that calendar, extrapolated by one day per round.

    A server that reuses one calendar for every season is never affected.
21. **A lone new `competition_id`** (D core).
    - Without feed evidence (no pending provisional reset, no feed reset within the merge window), a new comp_id holds `comp_id_suspect` (at most 15 s). It needs a second answer at least 1 s later to force the reset.
    - An answer with the old id drops it (`comp_id_flap` alert).
    - A feed reset while a candidate is pending adopts it (merged).
    - An unconfirmed answer does not feed the clock.
22. **Stale server time** (A clock). A fresh server sample is not used when it is more than 2 s behind the fresh storm observations (a feed message is never received before its market time). The storm offset is used instead, with the alert `clock_server_stale`.
23. **WebSocket handshake rejects** (A sources). A rejected handshake retries every `feed.reconnect_auth_s` (5 s) on 401 or 403, and backs off 1, 2, 4, 8 and then 10 s (`feed.reconnect_throttle_max_s`) on 429, with the alert `ws_rejected`. Dropped connections keep the fast ladder.

**Reconcile, persistence and the engine**

24. **Reconcile under slow GETs** (B reconcile, D core).
    - Both cycle timeouts are `timeout_get_s × (1 + get_retries) + 0.5` (6.5 s); before, 2 s dropped every cycle once GETs took 2-3 s.
    - A dropped cycle counts as a failure.
    - With no completed cycle for `risk.reconcile_stale_hold_s` (10 s), the core holds `reconcile_down` and alerts.
25. **Multi-symbol position jumps** (B reconcile, D core). When one cycle shows at least 4 symbols changing with no live record of ours (a new season seen through `/positions` before its comp_id, or a manual flatten), foreign fills wait 3 s (`positions_jump`). The core asks `GET /competitions/time` at once. Positions and room are applied as usual.
26. **Persistence** (D core). A failed `state.json` write alerts `persist_failed` (at most once a minute) and no longer counts as a core exception, so a full disk cannot hold all new risk. The state is also written within `app.persist_debounce_s` (1 s) after any order action or result, so a restart knows the latest orders.
27. **`eng restart`** (D core). It builds the new engine first. If the rebuild fails, the running engine is kept (`engine_restart_failed`).

**Evidence after the hardening pass** (`STATUS.md` §2 has the details):
- research seasons 401-440, default config: mean 16,337 (16,350 before), all default checks pass;
- parity profile: mean 19,673 against REC's 19,660, r 0.996, median |d| 835. The action count still fails, as before (§9.3, integration gap G1);
- §9.2 recording runs: every blocking check passes in dryrun and book mode. Book-mode season 225 is 15,741 (11,739 before), because the rebuilders are no longer dropped at base cost;
- end-to-end `--replay` through the real shell: 0 exceptions, 0 exchange violations, at most 146 actions in any 61 s, 0 live orders, and exit through the kill path;
- every `tests/review_*.py` test passes; the 5 former expected failures are now plain tests.

## 14. Strategy updates (23 Oct, after the first testnet dry run)

1. **S1-r3 sends the most valuable orders first.** Each S1-r3 `RestIntent` carries `value_usd`: the dollars it would
   capture right now against settlement S. To get it, the strategy walks the visible levels its limit price takes
   (asks for a buy, bids for a sell, best first), up to the strategy room L − pos (buy) or L + pos (sell), counting
   S − price (buy) or price − S (sell) per share (`strategies/s1r3.py: capturable_usd`). The strategy and the OM's
   rest ordering both sort by it. The OM falls back to `edge_usd × desired` when `value_usd` is None, so other
   strategies are unchanged. Reason: other participants already take the final-report mispricing on the testnet,
   and only the first orders (8 workers) go out in parallel. In the simulator every order fills at the same
   instant, so its P&L is unchanged by design (225 r3 S1-r3 still +631); the gain only exists against a real
   exchange.
2. **`ov PARAM X|off` console command (announcements).** It patches one engine parameter for the rest of the
   season through `EngineAdapter.override` (logged as `override`; `off` restores the default). Allowed targets
   (`console.OVERRIDE_PARAMS`): `settle.island.{PORT,ISLE,AQUA,GRID,FERRY}.{V0,a,K}`,
   `settle.rebuild.{RECON,MAT,GEN}.{a,b,c}`, `settle.insurance.{HAVEN,REIN}.{v0,slope,attach,top}`, and
   `physics.k.{PORT,ISLE,AQUA,GRID,FERRY}`. Short forms `SYM.param` are accepted in any case (`haven.attach`,
   `port.k` = closure cost K, `port.dmg` = damage rate). The parser keeps the case of `ov` arguments. The
   announcement card (`ANNOUNCEMENT_CARD.md`) maps headline types to `ov` lines.
3. **H1 exits by itself when S3 will not run that round.** `StrategyRunner` gives H1 an `s3_active(round)` check
   (S3 enabled and the round in S3's rounds). At the fallback deadline (76 s), H1 now takes its fallback exit
   (84-98 s, L/8 reduce-only clips) when S3 is off for the round, as well as when the engine is not live. Before
   this change, `s3 off` or `s3 rounds 3` left the H1 position untouched until the flatten (rounds 1-2) or
   settlement (round 3). The event `h1_fallback_check` now logs `s3_active`.
4. **Evidence for a later S3 start (not yet the default).** Counterfactuals on the recorded testnet books (seasons
   224 r3, 225, 238 r2-3, 239):

   | Config | P&L |
   |---|---|
   | Current | $30.2k |
   | S3 off (H1 exits by itself) | $15.0k |
   | `strategies.s3.start = ["storm_start", 60]` (S3 from 120 s) | $42.8k |

   On research prices (holdout 401-440) the later start is neutral: 15,964 against 16,337, a per-season difference
   of −373 ± 472 that is better in 17 of 40 seasons. Markouts locate S3's book losses in 80-120 s (−$18.0k net
   over the 9 recorded rounds), where the books are still repricing the formation jump (half done at about 72 s,
   against 64 s for the consensus) and clip costs are 2-7× the storm level. Pending: an out-of-sample check on
   season 240 (recorded 23 Oct, after this change was chosen).
5. **Update, 24 Oct 05:15: the 120-s S3 start was reverted to 80 s** (`["storm_start", 20]`). It was the default
   from 00:30 to 05:15, but the five overnight testnet seasons recorded after the change (242-246) went the other
   way: the 80-s start made $65.1k against $52.1k with 120 s. Over all ten recorded seasons the difference is about
   +$3k for 120 s (a wash, with per-season swings of −$9.4k to +$11.3k). On 200 research seasons 120 s was −$292 a
   season (se 199). The 80-120 s markout losses in the first nine recorded rounds did not generalize. Item 3 (H1
   exits by itself when S3 is off) stays.
