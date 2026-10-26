"""Flatten rounds 1-2 before the reports (DESIGN.md section 6.3; playbook section 4.1 rule 6 [testnet]).

In ``rounds`` (1, 2) for ``t`` in ``[start, end)`` (540-568 s), every symbol except ``exclude`` (REIN, whose short
from the riskless ask is held) with a non-zero exposure gets a reduce-only HEDGE target of 0. The clip starts at
L/12 and grows to ``ceil(|exposure| / slots_left)``, at most L/4, as soon as the book falls behind, where
``slots_left = max(1, floor((end - t) / clip_interval))``. Limits sit ``through_touch_bp`` beyond the touch,
capped at ``max_from_mid_bp`` from mid. Flatten bypasses frozen and disabled symbols because reducing risk is
always allowed. It alerts ``flatten_behind`` once per round at ``behind_alert_at`` (560 s) for a symbol that cannot
finish, and ``not_flat`` once at ``end`` if any position other than ``exclude`` is still open.

Hardening (DESIGN.md §13): "behind" compares the exposure with the *base* clip (L/12) times the slots left. With
the grown clip the test could fire only with about a full limit still open, so it never fired (season 401 ended
rounds 1-2 not flat without one). Clips carry ``valid_until`` = ``end`` and ``first_clip_cost_bp``.
"""
from __future__ import annotations

import math

from ctcbot.strategies.base import (Strategy, StrategyDeps, cap_buy, cap_sell, clip_of,
                                    expected_clip_cost_bp, fo_allow_bp, limit_of, symbols_of, t_of)
from ctcbot.types import SYMBOLS, Intent, Priority, Side, StrategyContext, TargetIntent


class Flatten(Strategy):
    """Rounds 1-2 flattener (see the module docstring)."""

    name = "flatten"
    command = "flat"

    def __init__(self, deps: StrategyDeps) -> None:
        super().__init__(deps)
        self.rounds = [int(r) for r in self.p.get("rounds", [1, 2])]
        self.exclude = set(symbols_of(self.p, "exclude", ["REIN"]))
        self._alerted: set[tuple] = set()

    def reset_season(self) -> None:
        super().reset_season()
        self._alerted = set()

    def clip_for(self, ctx: StrategyContext, sym: str, exposure: int, t: float, end: float) -> tuple[int, int]:
        """(clip, slots_left) for a symbol at time ``t``."""
        interval = float(self.p.get("clip_interval_s", 2.0))
        slots_left = max(1, int(math.floor((end - t) / interval + 1e-9)))
        base = clip_of(ctx, sym, "flatten", self.cfg)
        top = clip_of(ctx, sym, "flatten_max", self.cfg)
        clip = min(max(base, int(math.ceil(abs(exposure) / slots_left))), top)
        return max(1, clip), slots_left

    def decide(self, ctx: StrategyContext) -> list[Intent]:
        if not self.enabled:
            return []
        t = t_of(ctx)
        r = ctx.clock.round
        if t is None or r not in self.rounds:
            return []
        try:
            start, end, behind_at = self.at(ctx, "start"), self.at(ctx, "end"), self.at(ctx, "behind_alert_at")
        except (KeyError, TypeError, ValueError):
            return []
        key = (ctx.clock.season_epoch, r)
        if t >= end:
            if (key, "not_flat") not in self._alerted:
                self._alerted.add((key, "not_flat"))
                open_ = {s: q for s, q in ctx.positions.items() if q and s not in self.exclude}
                if open_:
                    self.alert("not_flat", f"round {r}: not flat at {end:g} s: {open_}", positions=open_, round=r)
            return []
        if t < start:
            return []
        tt, mfm = self.p.get("through_touch_bp"), self.p.get("max_from_mid_bp")
        phase = ctx.schedule.cost_phase(t)
        out: list[Intent] = []
        behind: dict[str, int] = {}
        for sym in SYMBOLS:
            if sym in self.exclude:
                continue
            exp_ = ctx.exposure(sym)
            if exp_ == 0 or limit_of(ctx, sym) <= 0:
                continue
            clip, slots_left = self.clip_for(ctx, sym, exp_, t, end)
            if abs(exp_) > clip_of(ctx, sym, "flatten", self.cfg) * slots_left:
                behind[sym] = exp_
            b = ctx.books.get(sym)
            if b is None or b.mid is None or b.mid <= 0:
                continue
            mid = float(b.mid)
            if exp_ > 0:
                side, lb, ls = Side.SELL, None, self.px(cap_sell(mid, b.bid, tt, mfm), Side.SELL)
                if ls is None:
                    continue
            else:
                side, lb, ls = Side.BUY, self.px(cap_buy(mid, b.ask, tt, mfm), Side.BUY), None
                if lb is None:
                    continue
            exp_bp = expected_clip_cost_bp(self.deps, sym, phase, clip, side)
            out.append(TargetIntent(strategy=self.name, symbol=sym, target=0, clip_qty=clip,
                                    clip_interval_s=float(self.p.get("clip_interval_s", 2.0)), limit_buy=lb,
                                    limit_sell=ls, priority=Priority.HEDGE, ttl_s=float(self.p.get("ttl_s", 2.0)),
                                    reduce_only=True, edge_usd_per_share=0.0, expected_cost_bp=exp_bp, ref_mid=mid,
                                    reason=f"flatten {exp_:+d} slots {slots_left}", valid_until=ctx.now + (end - t),
                                    first_clip_cost_bp=None if exp_bp is None else
                                    exp_bp - fo_allow_bp(self.deps, sym)))
        if t >= behind_at and (key, "behind") not in self._alerted:
            self._alerted.add((key, "behind"))           # evaluated once per round, at the first cycle >= 560 s
            if behind:
                self.alert("flatten_behind", f"round {r}: flatten behind at {t:.1f} s: {behind}", exposure=behind,
                           round=r)
        return out
