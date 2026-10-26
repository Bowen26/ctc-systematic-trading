"""REIN riskless ask (DESIGN.md section 6.5; playbook section 4.4).

``U = cap - slope * max(L_known - attach, 0)`` (90 - 0.035 * max(L - 1300, 0)): the insured loss only rises, so REIN
can never settle above U. The strategy rests one sell for all REIN sell room at ``round_px(U + offset, SELL)``
(HEDGE, key ``rein_ask``), emitted at ROUND_START, every ``refresh_s`` and whenever U changes, while the market is
open, from ``start`` to ``end_r12`` (the close) in rounds 1-2 or ``end_r3`` (the final report, 740 s) in round 3.
At ``end_r3`` it cancels the ask (S1-r3 then trades REIN against the exact S). While REIN is frozen by an
announcement the ask is cancelled and not re-posted until REIN is unfrozen.

Hardening (DESIGN.md §13): risk caps a HEDGE order at ``max_clip_frac`` (0.5 L), so the ask needs two orders. While
the ask is live but short of the REIN sell room, it is re-emitted at every decision cycle instead of waiting for
the next 5-s refresh, so the second order follows the first within one cycle.
"""
from __future__ import annotations

from ctcbot.strategies.base import Strategy, StrategyDeps, limit_of, market_open, t_of
from ctcbot.types import CancelIntent, Intent, Priority, RestIntent, Side, StrategyContext, Trigger

KEY = "rein_ask"


class ReinAsk(Strategy):
    """The REIN riskless ask (see the module docstring)."""

    name = "rein_ask"
    command = "rein"

    def __init__(self, deps: StrategyDeps) -> None:
        super().__init__(deps)
        self.symbol = str(self.p.get("symbol", "REIN"))
        self._clear()

    def _clear(self) -> None:
        self._last_emit: float | None = None
        self._last_price: float | None = None
        self._emit_round: tuple | None = None
        self._was_frozen = False
        self._cancelled_r3: tuple | None = None

    def reset_season(self) -> None:
        super().reset_season()
        self._clear()

    def upper_bound(self, loss_known: float) -> float:
        """U for a known season loss (in $M)."""
        cap, slope, attach = float(self.p["cap"]), float(self.p["slope"]), float(self.p["attach"])
        return cap - slope * max(float(loss_known) - attach, 0.0)

    def price(self, loss_known: float) -> float:
        return self.px(self.upper_bound(loss_known) + float(self.p.get("offset", 0.02)), Side.SELL) or 0.0

    def _cancel(self, reason: str) -> list[Intent]:
        self._last_emit = None
        self._last_price = None
        return [CancelIntent(strategy=self.name, priority=Priority.SNIPE, scope="key", key=KEY, symbol=self.symbol,
                             reason=reason)]

    def set_enabled(self, on: bool, ctx: StrategyContext | None = None) -> list[Intent]:
        super().set_enabled(on, ctx)
        return [] if on else self._cancel("rein off")

    def decide(self, ctx: StrategyContext) -> list[Intent]:
        if not self.enabled:
            return []
        if self.symbol in ctx.frozen:
            if not self._was_frozen:
                self._was_frozen = True
                return self._cancel("frozen")
            return []
        self._was_frozen = False
        if self.symbol in ctx.disabled:
            return []
        t = t_of(ctx)
        r = ctx.clock.round
        if t is None or r is None:
            return []
        try:
            start = self.at(ctx, "start")
            end = self.at(ctx, "end_r3" if r == 3 else "end_r12")
        except (KeyError, TypeError, ValueError):
            return []
        if r == 3 and t >= end:
            key = (ctx.clock.season_epoch, r)
            if self._cancelled_r3 != key:
                self._cancelled_r3 = key
                return self._cancel("final report")
            return []
        if not (start <= t < end) or not market_open(ctx):
            return []
        px = self.price(ctx.season.loss_known)
        if px <= 0:
            return []
        key = (ctx.clock.season_epoch, r)
        elapsed = None if self._last_emit is None else ctx.now - self._last_emit
        due = (ctx.trigger == Trigger.ROUND_START or elapsed is None or elapsed < 0 or px != self._last_price
               or key != self._emit_round or elapsed >= float(self.p.get("refresh_s", 5.0)) - 1e-9
               or self._short_of_room(ctx))
        if not due:
            return []
        self._last_emit = ctx.now
        self._emit_round = key
        if px != self._last_price:
            self.event("rein_ask_price", price=px, loss_known=ctx.season.loss_known, round=r)
        self._last_price = px
        b = ctx.books.get(self.symbol)
        edge = max(0.0, float(b.bid) - px) if b is not None and b.bid is not None else 0.0
        return [RestIntent(strategy=self.name, key=KEY, symbol=self.symbol, side=Side.SELL, price=px,
                           priority=Priority.HEDGE, qty=None, edge_usd=edge, reason=f"U+offset r{r}")]

    def _short_of_room(self, ctx: StrategyContext) -> bool:
        """The ask is live (its key is working) but offers less than the REIN sell room (hardening)."""
        w = ctx.working.get(self.symbol)
        if w is None or KEY not in w.keys:
            return False
        L = limit_of(ctx, self.symbol)
        room = L + int(ctx.positions.get(self.symbol, 0) or 0)
        return room - int(w.sell_qty) > max(1.0, 0.05 * L)

    def status(self) -> dict:
        return {"enabled": self.enabled, "price": self._last_price}
