"""Strategy base class, dependencies and shared helpers (DESIGN.md sections 4.C and 6).

Every strategy subclasses ``Strategy`` with ``name`` equal to its config key under ``strategies.``. A strategy is a
pure decision function of the ``StrategyContext`` plus its own small state: it returns ``Intent``s (targets, rests
and cancels) that the order manager turns into orders. Strategies never send anything, never read a clock and
never gate on ``ctx.risk_mode`` (holds are enforced per order by the risk manager).

Module helpers:

* ``fv_usable``: the engine snapshot is current enough to trade on;
* ``symbol_sane``: the engine value of one symbol is finite and near the book (with a sticky ``SanityTracker``);
* ``in_window``, ``round_px``, ``limit_of``, ``clip_of``, ``blocked``: windows, price rounding and sizing;
* ``cap_buy``/``cap_sell``: the ``through_touch_bp`` / ``max_from_mid_bp`` price caps (``None`` disables one).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ctcbot.case import clip_size
from ctcbot.types import (BookView, Command, Intent, Phase, Side, StrategyContext)

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from ctcbot.case import CaseInfo, Schedule
    from ctcbot.config import Config
    from ctcbot.models.cost_model import CostModel
    from ctcbot.models.laws import LawCheck, LawsCrossCheck
    from ctcbot.types import EventLog

#: Clip divisors of DESIGN.md section 6, used only when ``clip_of`` gets no config.
DEFAULT_CLIP_DIV: dict[str, int] = {"s3": 12, "h1": 4, "h1_exit": 8, "flatten": 12, "flatten_max": 4, "leadlag": 2}
OPEN_PHASES = frozenset({Phase.PRE_STORM, Phase.STORM, Phase.AFTERMATH})


@dataclass(frozen=True)
class StrategyDeps:
    """Everything a strategy may use besides the per-cycle context."""

    cfg: "Config"
    case: "CaseInfo"
    schedule: "Schedule"
    cost: "CostModel"
    laws: "LawsCrossCheck | None"
    law_check: "LawCheck"
    log: "EventLog"


# ============================================================================== helpers
def t_of(ctx: StrategyContext) -> float | None:
    """Real seconds since this round's open, or None when the clock is unknown."""
    c = ctx.clock
    if not c.known or c.t is None:
        return None
    return float(c.t)


def market_open(ctx: StrategyContext) -> bool:
    """The market clock runs and the phase is pre_storm, storm or aftermath."""
    return bool(ctx.clock.known and ctx.clock.running and ctx.clock.phase in OPEN_PHASES)


def fv_usable(ctx: StrategyContext, cfg: "Config") -> tuple[bool, str]:
    """(usable, reason): the engine snapshot is ok, of this season, fresh and of the current round's storm."""
    fv = ctx.fv
    if fv is None:
        return False, "no_fv"
    if not fv.ok:
        return False, "fv_error"
    if fv.season_epoch != ctx.clock.season_epoch:
        return False, "fv_epoch"
    age = ctx.engine.oldest_pending_age_s
    if age is not None and age > float(cfg.get("engine.stale_s", 6.0)):
        return False, "engine_stale"
    phase = ctx.clock.phase
    if phase in (Phase.STORM, Phase.AFTERMATH) and fv.round != ctx.clock.round:
        return False, "fv_round"
    if phase == Phase.STORM:
        obs_age = ctx.feed.last_storm_obs_age_s
        if obs_age is None or obs_age > float(cfg.get("feed.storm_obs_stale_s", 6.0)):
            return False, "storm_obs_stale"
    return True, ""


class SanityTracker:
    """Sticky per-symbol insanity: a symbol stays insane ``clear_after_s`` after the condition last held."""

    def __init__(self, clear_after_s: float = 10.0) -> None:
        self.clear_after_s = float(clear_after_s)
        self.reset()

    def reset(self) -> None:
        self._last_bad: dict[str, float] = {}
        self._last_now: float | None = None

    def update(self, sym: str, bad_now: bool, now: float) -> bool:
        """Record the instantaneous condition; return True when the symbol counts as sane."""
        if self._last_now is not None and now < self._last_now - 1e-9:
            self._last_bad.clear()            # time went backwards: a new run in the same process
        self._last_now = now
        if bad_now:
            self._last_bad[sym] = now
            return False
        last = self._last_bad.get(sym)
        if last is not None and now - last < self.clear_after_s:
            return False
        self._last_bad.pop(sym, None)
        return True

    def insane(self) -> frozenset[str]:
        return frozenset(self._last_bad)


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def symbol_insane_now(ctx: StrategyContext, sym: str, cfg: "Config") -> bool:
    """The instantaneous insanity condition (no stickiness)."""
    fv = ctx.fv
    if fv is None:
        return True
    v = fv.values.get(sym)
    if v is None or len(v) < 2 or not _finite(v[0]) or not _finite(v[1]):
        return True
    mu, sd = float(v[0]), float(v[1])
    b = ctx.books.get(sym)
    mid = b.mid if b is not None else None
    if mid is None or not _finite(mid):
        return False
    lim = max(float(cfg.get("guards.engine_sanity.sd_mult", 6.0)) * sd,
              float(cfg.get("guards.engine_sanity.mid_frac", 0.15)) * float(mid))
    return abs(mu - float(mid)) > lim


def symbol_sane(ctx: StrategyContext, sym: str, cfg: "Config", tracker: SanityTracker | None = None) -> bool:
    """False when mu or sd is not finite or ``|mu - mid| > max(6 sd, 15% mid)``.

    With a ``tracker`` the insanity sticks for ``guards.engine_sanity.clear_after_s`` after the condition clears;
    without one the check is instantaneous.
    """
    bad = symbol_insane_now(ctx, sym, cfg)
    if tracker is None:
        return not bad
    return tracker.update(sym, bad, ctx.now)


def in_window(ctx: StrategyContext, start: Sequence, end: Sequence) -> bool:
    """``start <= t < end`` for two anchor specs, with the clock known."""
    t = t_of(ctx)
    if t is None:
        return False
    try:
        return ctx.schedule.at(start) <= t < ctx.schedule.at(end)
    except (KeyError, TypeError, ValueError):
        return False


def round_px(px: float, side: Side, decimals: int) -> float:
    """Round a limit price to ``decimals``: buys floor, sells ceil (never more aggressive than asked)."""
    if not _finite(px):
        raise ValueError(f"price is not finite: {px!r}")
    f = 10 ** int(decimals)
    x = float(px) * f
    v = math.floor(x + 1e-6) if side is Side.BUY else math.ceil(x - 1e-6)
    return round(v / f, int(decimals))


def limit_of(ctx: StrategyContext, sym: str) -> int:
    """``ctx.limits[sym]``, the scaled strategy limit L_s (0 when unknown)."""
    try:
        return max(0, int(ctx.limits.get(sym, 0)))
    except (TypeError, ValueError):
        return 0


def clip_of(ctx: StrategyContext, sym: str, which: str, cfg: "Config | None" = None) -> int:
    """``clip_size(L_s, sizing.clip_div[which])``; the divisor comes from ``cfg`` (or the section 6 defaults)."""
    div: Any = None
    if cfg is not None:
        div = cfg.get(f"sizing.clip_div.{which}", None)
    if not isinstance(div, int) or isinstance(div, bool) or div <= 0:
        div = DEFAULT_CLIP_DIV.get(which, 12)
    return clip_size(limit_of(ctx, sym), div)


def blocked(ctx: StrategyContext, sym: str) -> bool:
    """The symbol is frozen by an announcement, disabled by risk, or owned by the ETF lead-lag."""
    return sym in ctx.frozen or sym in ctx.disabled or sym in ctx.leadlag


def book_tradeable(b: BookView | None) -> bool:
    """A two-sided book with a mid that is not wall-only."""
    return b is not None and not b.empty and b.mid is not None and b.bid is not None and b.ask is not None \
        and not b.wall_only


def cap_buy(mid: float, ask: float | None, through_touch_bp: float | None, max_from_mid_bp: float | None,
            *others: float | None) -> float | None:
    """min of the given limits, ``ask*(1+tt)`` and ``mid*(1+mfm)``; a None cap is skipped; None if nothing left."""
    c = [x for x in others if x is not None]
    if through_touch_bp is not None and ask is not None:
        c.append(ask * (1.0 + float(through_touch_bp) / 1e4))
    if max_from_mid_bp is not None:
        c.append(mid * (1.0 + float(max_from_mid_bp) / 1e4))
    return min(c) if c else None


def cap_sell(mid: float, bid: float | None, through_touch_bp: float | None, max_from_mid_bp: float | None,
             *others: float | None) -> float | None:
    """max of the given limits, ``bid*(1-tt)`` and ``mid*(1-mfm)``; a None cap is skipped; None if nothing left."""
    c = [x for x in others if x is not None]
    if through_touch_bp is not None and bid is not None:
        c.append(bid * (1.0 - float(through_touch_bp) / 1e4))
    if max_from_mid_bp is not None:
        c.append(mid * (1.0 - float(max_from_mid_bp) / 1e4))
    return max(c) if c else None


def marketable(side: Side, price: float | None, b: BookView | None) -> bool:
    """A buy priced at or above the ask, a sell at or below the bid, or a missing touch."""
    if b is None:
        return True
    if side is Side.BUY:
        return b.ask is None or (price is not None and price >= b.ask)
    return b.bid is None or (price is not None and price <= b.bid)


def case_limit(deps: StrategyDeps, sym: str) -> int:
    """The case (exchange) limit of a symbol, falling back to the cost model's limit."""
    try:
        v = int(deps.case.limits[sym])
        if v > 0:
            return v
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    try:
        return int(deps.cost.L(sym))
    except Exception:  # noqa: BLE001
        return 1


def cost_x(deps: StrategyDeps, sym: str, shares: float) -> float:
    """Cost-model ``x`` of a clip: shares over the full (case) limit, the unit the measured tables use."""
    return float(shares) / max(1, case_limit(deps, sym))


def fo_allow_bp(deps: StrategyDeps, sym: str) -> float:
    """The follow-on allowance of a symbol: ``cost.fo_allow_bp.stock`` (36) or ``.etf`` (17.28)."""
    kind = "stock"
    try:
        kind = str(deps.cfg.sym(sym).get("kind", "stock"))
    except (KeyError, TypeError, AttributeError):
        pass
    dflt = 17.28 if kind == "etf" else 36.0
    try:
        fo = deps.cfg.get("cost.fo_allow_bp", None) or {}
        return float(fo.get(kind, dflt))
    except (KeyError, TypeError, ValueError, AttributeError):
        return dflt


def expected_clip_cost_bp(deps: StrategyDeps, sym: str, phase: str, shares: float,
                          side: Side | None) -> float | None:
    """Expected all-in cost of one clip in bp of mid: ``walk(clip/L, phase, side) + fo_allow`` (DESIGN.md §6).

    This is the per-clip cost inside the S3 threshold (``entry_cost_bp / thr_mult``): the book walk plus the
    follow-on allowance for our own consecutive same-side clips. The fill-sanity breaker and the H1 cost ratio
    compare realized clip costs (walk, follow-on and impact) with it. None if the cost model fails.
    """
    try:
        walk = float(deps.cost.walk_bp(sym, phase, cost_x(deps, sym, shares), side))
    except Exception:  # noqa: BLE001 - an expected cost is informative only
        return None
    if not math.isfinite(walk):
        return None
    return walk + fo_allow_bp(deps, sym)


def parse_on_off(args: Sequence[str]) -> bool | None:
    """True for "on", False for "off", None otherwise."""
    if len(args) >= 1 and isinstance(args[0], str):
        a = args[0].lower()
        if a == "on":
            return True
        if a == "off":
            return False
    return None


# ============================================================================== base class
class Strategy:
    """Base strategy: config section, enable flag, command plumbing, rate-limited logs and state hooks."""

    name: str = "base"
    #: console command word that switches this strategy ("<command> on|off"); defaults to ``name``
    command: str | None = None

    def __init__(self, deps: StrategyDeps) -> None:
        self.deps = deps
        self.cfg = deps.cfg
        self.log = deps.log
        self.p: dict = deps.cfg.get(f"strategies.{self.name}")
        self.enabled: bool = bool(self.p["enabled"])
        self.decimals = int(deps.cfg.get("exchange.price_decimals", 2))
        self.sanity = SanityTracker(float(deps.cfg.get("guards.engine_sanity.clear_after_s", 10.0)))
        self._rl: dict[tuple, float] = {}

    # ------------------------------------------------------------------ interface
    def decide(self, ctx: StrategyContext) -> list[Intent]:
        """Intents for this cycle (the base strategy has none)."""
        return []

    def on_command(self, cmd: Command, ctx: StrategyContext) -> list[Intent] | None:
        """Handle ``<command> on|off``; None when the command is not this strategy's."""
        word = self.command or self.name
        if cmd.name != word:
            return None
        v = parse_on_off(cmd.args)
        if v is None:
            self.event("command_error", command=cmd.name, args=list(cmd.args), reason="expected on|off")
            return []
        return self.set_enabled(v, ctx)

    def set_enabled(self, on: bool, ctx: StrategyContext | None = None) -> list[Intent]:
        """Switch the strategy; subclasses that rest orders return the cancels for ``off``."""
        self.enabled = on
        self.event("strategy_enabled", strategy=self.name, enabled=on)
        return []

    def on_promotion(self) -> None:
        """Dry-run state was carried into a live run (DESIGN.md section 4.D persist)."""

    def reset_season(self) -> None:
        """A confirmed new season: forget per-season state."""
        self.sanity.reset()
        self._rl.clear()

    def state_dict(self) -> dict:
        """Persisted state (the enable flag; subclasses add theirs)."""
        return {"enabled": self.enabled}

    def load_state(self, d: dict) -> None:
        """Restore what ``state_dict`` saved; unknown or malformed fields are ignored."""
        if isinstance(d, dict) and isinstance(d.get("enabled"), bool):
            self.enabled = d["enabled"]

    def status(self) -> dict:
        """Small status summary."""
        return {"enabled": self.enabled}

    # ------------------------------------------------------------------ helpers
    def at(self, ctx: StrategyContext, key: str) -> float:
        """The round time of the anchor spec ``self.p[key]``."""
        return float(ctx.schedule.at(self.p[key]))

    def window(self, ctx: StrategyContext, start_key: str, end_key: str, inclusive_end: float = 0.0) -> bool:
        """``at(start) <= t < at(end) + inclusive_end`` with the clock known."""
        t = t_of(ctx)
        if t is None:
            return False
        try:
            return self.at(ctx, start_key) <= t < self.at(ctx, end_key) + inclusive_end
        except (KeyError, TypeError, ValueError):
            return False

    def sane(self, ctx: StrategyContext, sym: str) -> bool:
        """``symbol_sane`` with this strategy's sticky tracker (logs a rate-limited ``engine_insane``)."""
        ok = symbol_sane(ctx, sym, self.cfg, self.sanity)
        if not ok:
            v = ctx.fv.values.get(sym) if ctx.fv is not None else None
            b = ctx.books.get(sym)
            self.rlog(("insane", sym), "engine_insane", ctx.now, strategy=self.name, symbol=sym,
                      fv=list(v) if v is not None else None, mid=b.mid if b is not None else None)
        return ok

    def px(self, price: float | None, side: Side) -> float | None:
        """Round a limit to the exchange decimals (buys down, sells up); None and non-finite stay None."""
        if price is None or not _finite(price) or price <= 0:
            return None
        return round_px(price, side, self.decimals)

    def event(self, kind: str, **fields: Any) -> None:
        try:
            self.log.event(kind, **fields)
        except Exception:  # noqa: BLE001 - logging never breaks a decision
            pass

    def alert(self, kind: str, msg: str, **fields: Any) -> None:
        try:
            self.log.alert(kind, msg, **fields)
        except Exception:  # noqa: BLE001
            pass

    def rlog(self, key: tuple, kind: str, now: float, every: float = 10.0, **fields: Any) -> None:
        """Log ``kind`` at most once per ``every`` seconds for ``key``."""
        last = self._rl.get(key)
        if last is not None and 0 <= now - last < every:
            return
        self._rl[key] = now
        self.event(kind, **fields)


def fv_value(ctx: StrategyContext, sym: str) -> tuple[float, float] | None:
    """(mu, sd) of a symbol from the context's fair values, or None."""
    if ctx.fv is None:
        return None
    v = ctx.fv.values.get(sym)
    if v is None or len(v) < 2 or not _finite(v[0]) or not _finite(v[1]):
        return None
    return float(v[0]), float(v[1])


def symbols_of(p: Mapping[str, Any], key: str, default: Sequence[str]) -> list[str]:
    """A symbol list from a config section, defensively."""
    v = p.get(key, default)
    return [s for s in v if isinstance(s, str)] if isinstance(v, (list, tuple)) else list(default)
