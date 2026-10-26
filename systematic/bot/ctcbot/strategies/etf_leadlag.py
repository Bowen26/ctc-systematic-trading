"""ETF lead-lag (DESIGN.md section 6.6; playbook section 4.5 [testnet]): TEST-FIRST, default OFF.

The ETF books lag their baskets by about 7 s. Every ``decision_s`` (1 s) for ``t`` in ``[start, end)`` (60-738 s):

* ``NAV = sum(basket weight * component mid)`` and ``gap = (NAV - ETF mid) / ETF mid``;
* **enter** toward the gap's sign when ``|gap| > max(enter_gap_min, enter_hs_mult * ETF half-spread)``: target
  ``+-L`` in clips of L/2 with a marketable limit capped at ``mid * (1 + cap_gap_frac * gap)``; the clip is skipped
  when that cap is not through the touch, so the book is never walked uncapped;
* **exit** to 0 at the first of ``|gap| < exit_gap``, ``hold_s`` since entry, a change of the gap's sign, or the
  end of the window: reduce-only at the touch +- ``exit_through_touch_bp`` (HEDGE, so exits outrank new risk).

While enabled, ARCH and REBLD are removed from H1 and S3 (``leadlag_symbols``, filled into ``ctx.leadlag``). No new
entry is made while the rounds 1-2 flatten window is active. Command: ``lag on|off``.
"""
from __future__ import annotations

from typing import Any

from ctcbot.strategies.base import (Strategy, StrategyDeps, book_tradeable, clip_of, limit_of, market_open,
                                    symbols_of, t_of)
from ctcbot.strategies.announce import case_baskets
from ctcbot.types import Intent, Priority, Side, StrategyContext, TargetIntent


class EtfLeadLag(Strategy):
    """ETF-only lead-lag toward the live basket value (see the module docstring)."""

    name = "etf_leadlag"
    command = "lag"

    def __init__(self, deps: StrategyDeps) -> None:
        super().__init__(deps)
        self.symbols = symbols_of(self.p, "symbols", ["ARCH", "REBLD"])
        self.rounds = [int(r) for r in self.p.get("rounds", [1, 2, 3])]
        self.baskets = case_baskets(deps)
        self._clear()

    def _clear(self) -> None:
        self.state: dict[str, dict[str, Any]] = {}
        self._last_decision: float | None = None

    def reset_season(self) -> None:
        super().reset_season()
        self._clear()

    def leadlag_symbols(self) -> frozenset[str]:
        """The ETFs this strategy owns while enabled."""
        return frozenset(self.symbols) if self.enabled else frozenset()

    def nav_gap(self, ctx: StrategyContext, etf: str) -> tuple[float, float] | None:
        """(NAV, gap) from the live component mids, or None when a mid is missing."""
        basket = self.baskets.get(etf)
        b = ctx.books.get(etf)
        if not basket or b is None or b.mid is None or b.mid <= 0:
            return None
        nav = 0.0
        for c, w in basket.items():
            cb = ctx.books.get(c)
            if cb is None or cb.mid is None:
                return None
            nav += float(w) * float(cb.mid)
        return nav, (nav - float(b.mid)) / float(b.mid)

    def _flatten_active(self, ctx: StrategyContext, t: float) -> bool:
        fp = self.cfg.get("strategies.flatten", {}) or {}
        if not fp.get("enabled", True) or ctx.clock.round not in (fp.get("rounds") or []):
            return False
        try:
            return ctx.schedule.at(fp["start"]) <= t < ctx.schedule.at(fp["end"])
        except (KeyError, TypeError, ValueError):
            return False

    def decide(self, ctx: StrategyContext) -> list[Intent]:
        if not self.enabled:
            return []
        t = t_of(ctx)
        if t is None:
            return []
        dec = float(self.p.get("decision_s", 1.0))
        if self._last_decision is not None and 0 <= ctx.now - self._last_decision < dec - 1e-9:
            return []
        self._last_decision = ctx.now
        in_win = (ctx.clock.round in self.rounds and self.window(ctx, "start", "end") and market_open(ctx))
        can_enter = in_win and not self._flatten_active(ctx, t)
        out: list[Intent] = []
        for etf in self.symbols:
            b = ctx.books.get(etf)
            ng = self.nav_gap(ctx, etf)
            exp_ = ctx.exposure(etf)
            st = self.state.get(etf)
            if st is not None:
                gap = ng[1] if ng is not None else None
                why = None
                if st.get("exiting"):
                    why = st["exiting"]
                elif not in_win:
                    why = "window"
                elif gap is None:
                    why = "no_nav"
                elif abs(gap) < float(self.p.get("exit_gap", 0.001)):
                    why = "gap_closed"
                elif ctx.now - st["entry_mono"] >= float(self.p.get("hold_s", 8.0)):
                    why = "hold"
                elif (gap > 0) != (st["sign"] > 0):
                    why = "sign"
                if why is not None:
                    if not st.get("exiting"):
                        st["exiting"] = why
                        self.event("leadlag_exit", symbol=etf, reason=why, gap=gap, exposure=exp_)
                    if exp_ == 0:
                        del self.state[etf]
                        continue
                    it = self._exit(ctx, etf, exp_)
                    if it is not None:
                        out.append(it)
                    continue
                if ng is not None and b is not None and can_enter and etf not in ctx.frozen and etf not in ctx.disabled:
                    it = self._enter(ctx, etf, ng[1], st["sign"])
                    if it is not None:
                        out.append(it)
                continue
            if not can_enter or ng is None or etf in ctx.frozen or etf in ctx.disabled or not book_tradeable(b):
                continue
            assert b is not None
            gap = ng[1]
            hs = (float(b.half_spread_bp) / 1e4) if b.half_spread_bp is not None else 0.0
            thr = max(float(self.p.get("enter_gap_min", 0.005)), float(self.p.get("enter_hs_mult", 3.0)) * hs)
            if abs(gap) <= thr:
                continue
            sign = 1 if gap > 0 else -1
            it = self._enter(ctx, etf, gap, sign)
            if it is not None:
                self.state[etf] = {"sign": sign, "entry_mono": ctx.now, "gap0": gap, "exiting": None}
                self.event("leadlag_enter", symbol=etf, gap=gap, threshold=thr, nav=ng[0], mid=b.mid)
                out.append(it)
        return out

    def _enter(self, ctx: StrategyContext, etf: str, gap: float, sign: int) -> TargetIntent | None:
        b = ctx.books.get(etf)
        if not book_tradeable(b):
            return None
        assert b is not None and b.mid is not None and b.bid is not None and b.ask is not None
        if (gap > 0) != (sign > 0):
            return None
        L = limit_of(ctx, etf)
        if L <= 0 or ctx.exposure(etf) == sign * L:
            return None
        raw = float(b.mid) * (1.0 + float(self.p.get("cap_gap_frac", 0.5)) * gap)
        side = Side.BUY if sign > 0 else Side.SELL
        px = self.px(raw, side)
        if px is None or (side is Side.BUY and px < b.ask) or (side is Side.SELL and px > b.bid):
            self.rlog(("nm", etf), "leadlag_not_through", ctx.now, symbol=etf, limit=px, bid=b.bid, ask=b.ask)
            return None
        dec = float(self.p.get("decision_s", 1.0))
        return TargetIntent(strategy=self.name, symbol=etf, target=sign * L, clip_qty=clip_of(ctx, etf, "leadlag",
                                                                                              self.cfg),
                            clip_interval_s=dec, limit_buy=px if side is Side.BUY else None,
                            limit_sell=px if side is Side.SELL else None, priority=Priority.TAKE,
                            ttl_s=float(self.p.get("ttl_s", 1.0)), edge_usd_per_share=abs(gap) * float(b.mid) / 2.0,
                            ref_mid=float(b.mid), reason=f"leadlag gap {gap:+.4f}")

    def _exit(self, ctx: StrategyContext, etf: str, exposure: int) -> TargetIntent | None:
        b = ctx.books.get(etf)
        if b is None:
            return None
        etb = float(self.p.get("exit_through_touch_bp", 50.0)) / 1e4
        if exposure > 0:
            if b.bid is None:
                return None
            ls, lb = self.px(float(b.bid) * (1.0 - etb), Side.SELL), None
            if ls is None:
                return None
        else:
            if b.ask is None:
                return None
            lb, ls = self.px(float(b.ask) * (1.0 + etb), Side.BUY), None
            if lb is None:
                return None
        return TargetIntent(strategy=self.name, symbol=etf, target=0, clip_qty=clip_of(ctx, etf, "leadlag", self.cfg),
                            clip_interval_s=float(self.p.get("decision_s", 1.0)), limit_buy=lb, limit_sell=ls,
                            priority=Priority.HEDGE, ttl_s=float(self.p.get("ttl_s", 1.0)), reduce_only=True,
                            ref_mid=float(b.mid) if b.mid is not None else None, reason="leadlag exit")

    def state_dict(self) -> dict:
        return {"enabled": self.enabled}

    def status(self) -> dict:
        return {"enabled": self.enabled, "positions": {k: v.get("sign") for k, v in self.state.items()}}
