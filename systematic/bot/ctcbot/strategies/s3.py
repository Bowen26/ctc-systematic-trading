"""S3, the mid-storm engine taker (DESIGN.md section 6.2; playbook section 4.1 with the [testnet] cost term).

From 80 s to 540 s of each round in ``rounds`` (anchors ``start``/``end``), while the engine snapshot is usable,
every symbol except ``exclude`` (REIN) and the blocked, insane or untradeable ones gets a full-size target as soon
as the engine edge clears ``th = k*sd + cost*mid``:

* ``edge_ref = "touch"`` (default): ``+L`` if ``mu - ask > th``, ``-L`` if ``bid - mu > th``; a long exits when
  ``mu < bid - xk*sd`` and a short when ``mu > ask + xk*sd``; otherwise S3 holds and emits nothing;
* ``edge_ref = "mid"`` (REC's rule, sim parity and ``abl_edge``): the same with ``e = mu - mid`` and the exit band
  ``xk*sd + band*mid``.

``cost`` is the phase entry cost (config table, or the cost model with ``cost_source = "model"``) times
``cost_mult``; ``band`` is the phase exit band. Entry limits are ``mu -/+ (k*sd + limit_cost_frac*cost*mid)``
capped at ``through_touch_bp`` beyond the touch and ``max_from_mid_bp`` from mid; exits never trade through fair.
Clips are ``L/12`` every 2 s with a 2-s ttl. S3 inherits whatever position H1 built, because it works from the
exposure. ``S3Rule`` holds the signal so the announcement strategy can reuse it for its adjusting state.

Hardening (DESIGN.md §13): S3 also skips while the engine's oldest pending message is older than
``max_engine_age_s`` (2 s; the general 6-s ``engine.stale_s`` let S3 trade on values up to ~5 s old when a compute
took 2-3 s); its clips carry ``valid_until`` = the window's end (no clip at or after 540 s) and
``first_clip_cost_bp`` (the walk alone, for a clip with no own same-side clip in the last ``cost.fo_window_s``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from ctcbot.strategies.base import (Strategy, StrategyDeps, blocked, book_tradeable, cap_buy, cap_sell, clip_of,
                                    cost_x, expected_clip_cost_bp, fo_allow_bp, fv_usable, fv_value, limit_of,
                                    round_px, symbols_of, t_of)
from ctcbot.types import SYMBOLS, BookView, Command, Intent, Priority, Side, StrategyContext, TargetIntent

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from ctcbot.config import Config

#: Phases without their own S3 table entry use the nearest measured regime.
PHASE_ALIAS: dict[str, str] = {"early": "s3_start", "h1_exit": "s3_start", "open": "after", "pre": "after",
                               "h1_entry": "after", "rep_prelim": "after", "rep_final": "after"}


def table_value(table: Mapping[str, Any] | None, phase: str) -> float | None:
    """Phase lookup in a per-symbol bp table with the alias and storm fallbacks."""
    if not isinstance(table, Mapping) or not table:
        return None
    for key in (phase, PHASE_ALIAS.get(phase), "storm"):
        if key is not None and isinstance(table.get(key), (int, float)):
            return float(table[key])
    nums = [float(v) for v in table.values() if isinstance(v, (int, float))]
    return max(nums) if nums else None


class S3Rule:
    """The S3 signal and price rules for one config section (``strategies.s3``)."""

    def __init__(self, deps: StrategyDeps, p: Mapping[str, Any]) -> None:
        self.deps = deps
        self.cfg: "Config" = deps.cfg
        self.p = p

    def k(self, round_: int | None) -> float:
        return float(self.p["k_r3"] if round_ == 3 else self.p["k_r12"])

    def cost_frac(self, sym: str, phase: str, clip: int) -> float:
        """Entry cost as a fraction of mid: the phase table (or the model) times ``cost_mult``."""
        mult = float(self.p.get("cost_mult", 1.0))
        if self.p.get("cost_source", "config") == "model":
            bp = self.deps.cost.entry_cost_bp(sym, phase, cost_x(self.deps, sym, clip))
        else:
            bp = table_value(self.cfg.sym(sym).get("entry_cost_bp"), phase)
            if bp is None:
                bp = self.deps.cost.entry_cost_bp(sym, phase, cost_x(self.deps, sym, clip))
        return bp * mult * 1e-4

    def band_frac(self, sym: str, phase: str) -> float:
        """Exit band as a fraction of mid (the phase half-spread)."""
        if self.p.get("cost_source", "config") == "model":
            return self.deps.cost.half_spread_bp(sym, phase) * 1e-4
        bp = table_value(self.cfg.sym(sym).get("exit_band_bp"), phase)
        if bp is None:
            bp = self.deps.cost.half_spread_bp(sym, phase)
        return bp * 1e-4

    def expected_cost_bp(self, sym: str, phase: str, clip: int, side: Side) -> float | None:
        """``walk(clip/L, ph, side) + fo_allow``: the per-clip cost inside the threshold (base helper)."""
        return expected_clip_cost_bp(self.deps, sym, phase, clip, side)

    def intent(self, ctx: StrategyContext, sym: str, b: BookView, mu: float, sd: float, k: float, phase: str,
               cap: int, clip: int, *, allow_exit: bool = True, strategy: str = "s3",
               priority: Priority = Priority.TAKE, decimals: int = 2,
               valid_until: float | None = None) -> tuple[TargetIntent | None, str]:
        """(intent or None, reason): the S3 decision for one symbol with target magnitude ``cap``."""
        assert b.mid is not None and b.bid is not None and b.ask is not None
        mid, bid, ask = float(b.mid), float(b.bid), float(b.ask)
        p = self.p
        cost = self.cost_frac(sym, phase, clip)
        th = k * sd + cost * mid
        xk = float(p.get("xk", 0.0))
        cur = ctx.exposure(sym)
        target: int | None = None
        edge = 0.0
        if p.get("edge_ref", "touch") == "mid":
            e = mu - mid
            if e > th:
                target, edge = (cap if cur < cap else None), e
            elif e < -th:
                target, edge = (-cap if cur > -cap else None), -e
            elif allow_exit:
                xb = xk * sd + self.band_frac(sym, phase) * mid
                if cur > 0 and e < -xb:
                    target, edge = 0, -e - xb
                elif cur < 0 and e > xb:
                    target, edge = 0, e - xb
        else:
            if mu - ask > th:
                target, edge = (cap if cur < cap else None), mu - ask
            elif bid - mu > th:
                target, edge = (-cap if cur > -cap else None), bid - mu
            elif allow_exit:
                if cur > 0 and mu < bid - xk * sd:
                    target, edge = 0, bid - mu
                elif cur < 0 and mu > ask + xk * sd:
                    target, edge = 0, mu - ask
        if target is None or target == cur:
            return None, "hold"
        tt = p.get("through_touch_bp")
        mfm = p.get("max_from_mid_bp")
        lcf = float(p.get("limit_cost_frac", 1.0))
        lb: float | None = None
        ls: float | None = None
        if target > cur:
            side = Side.BUY
            fair = mu if target == 0 else mu - k * sd - lcf * cost * mid
            lb = cap_buy(mid, ask, tt, mfm, fair)
            lb = round_limit(lb, side, decimals)
            px = lb
        else:
            side = Side.SELL
            fair = mu if target == 0 else mu + k * sd + lcf * cost * mid
            ls = cap_sell(mid, bid, tt, mfm, fair)
            ls = round_limit(ls, side, decimals)
            px = ls
        if px is None:
            return None, "no_price"
        if p.get("require_marketable", True):
            if (side is Side.BUY and px < ask) or (side is Side.SELL and px > bid):
                return None, "not_marketable"
        exp = self.expected_cost_bp(sym, phase, clip, side)
        it = TargetIntent(strategy=strategy, symbol=sym, target=int(target), clip_qty=max(1, int(clip)),
                          clip_interval_s=float(p.get("clip_interval_s", 2.0)), limit_buy=lb, limit_sell=ls,
                          priority=priority, ttl_s=float(p.get("ttl_s", 2.0)), reduce_only=(target == 0),
                          edge_usd_per_share=max(0.0, float(edge)),
                          expected_cost_bp=exp, ref_mid=mid,
                          reason=f"{'exit' if target == 0 else 'enter'} k={k:g} th={th:.4f} ph={phase}",
                          valid_until=valid_until,
                          first_clip_cost_bp=None if exp is None else exp - fo_allow_bp(self.deps, sym))
        return it, "ok"


def round_limit(px: float | None, side: Side, decimals: int) -> float | None:
    """Round a limit (buys down, sells up); None for a missing or non-positive price."""
    if px is None or not (px > 0):
        return None
    return round_px(px, side, decimals)


class S3(Strategy):
    """The engine taker (see the module docstring)."""

    name = "s3"

    def __init__(self, deps: StrategyDeps) -> None:
        super().__init__(deps)
        self.rule = S3Rule(deps, self.p)
        self.rounds: list[int] = [int(r) for r in self.p.get("rounds", [1, 2, 3])]
        self.exclude = set(symbols_of(self.p, "exclude", ["REIN"]))

    def decide(self, ctx: StrategyContext) -> list[Intent]:
        if not self.enabled:
            return []
        t = t_of(ctx)
        if t is None or ctx.clock.round not in self.rounds or not self.window(ctx, "start", "end"):
            return []
        ok, why = fv_usable(ctx, self.cfg)
        if ok:
            age = ctx.engine.oldest_pending_age_s
            lim = self.p.get("max_engine_age_s")
            if age is not None and lim is not None and age > float(lim):
                ok, why = False, "engine_slow"
        if not ok:
            self.rlog(("fv", why), "s3_skip", ctx.now, reason=why, t=t)
            return []
        k = self.rule.k(ctx.clock.round)
        phase = ctx.schedule.cost_phase(t)
        try:
            valid_until = ctx.now + (self.at(ctx, "end") - t)
        except (KeyError, TypeError, ValueError):
            valid_until = None
        out: list[Intent] = []
        for sym in SYMBOLS:
            if sym in self.exclude or blocked(ctx, sym):
                continue
            b = ctx.books.get(sym)
            if not book_tradeable(b):
                continue
            v = fv_value(ctx, sym)
            if v is None or not self.sane(ctx, sym):
                continue
            L = limit_of(ctx, sym)
            if L <= 0:
                continue
            assert b is not None
            it, why = self.rule.intent(ctx, sym, b, v[0], v[1], k, phase, L, clip_of(ctx, sym, "s3", self.cfg),
                                       decimals=self.decimals, valid_until=valid_until)
            if it is not None:
                out.append(it)
            elif why == "not_marketable":
                self.rlog(("nm", sym), "s3_not_marketable", ctx.now, symbol=sym, t=t)
        return out

    def on_command(self, cmd: Command, ctx: StrategyContext) -> list[Intent] | None:
        if cmd.name != "s3":
            return None
        args = [a.lower() for a in cmd.args if isinstance(a, str)]
        if args and args[0] == "rounds":
            rounds: list[int] = []
            for a in args[1:]:
                for x in a.split(","):
                    if x.strip().isdigit() and 1 <= int(x) <= 3:
                        rounds.append(int(x))
            if not rounds:
                self.event("command_error", command="s3", args=list(cmd.args), reason="expected rounds 1-3")
                return []
            self.rounds = sorted(set(rounds))
            self.event("s3_rounds", rounds=self.rounds)
            return []
        if args and args[0] == "costmult":
            try:
                v = float(args[1])
                if not (0.0 < v < 100.0):
                    raise ValueError(v)
            except (IndexError, ValueError):
                self.event("command_error", command="s3", args=list(cmd.args), reason="expected costmult X > 0")
                return []
            self.p = dict(self.p, cost_mult=v)
            self.rule.p = self.p
            self.event("s3_costmult", cost_mult=v)
            return []
        return super().on_command(cmd, ctx)

    def state_dict(self) -> dict:
        return {"enabled": self.enabled, "rounds": list(self.rounds), "cost_mult": float(self.p.get("cost_mult", 1.0))}

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        if not isinstance(d, dict):
            return
        r = d.get("rounds")
        if isinstance(r, list) and r and all(isinstance(x, int) and 1 <= x <= 3 for x in r):
            self.rounds = sorted(set(r))
        cm = d.get("cost_mult")
        if isinstance(cm, (int, float)) and 0 < cm < 100:
            self.p = dict(self.p, cost_mult=float(cm))
            self.rule.p = self.p

    def status(self) -> dict:
        return {"enabled": self.enabled, "rounds": list(self.rounds), "cost_mult": self.p.get("cost_mult", 1.0),
                "edge_ref": self.p.get("edge_ref", "touch")}
