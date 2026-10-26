"""Announcement freeze and manual overrides (DESIGN.md section 6.7; playbook section 5.2).

1. **Freeze.** On an ``announcement`` message, in the same decision cycle: ``frozen = named + links[named]``, plus
   every ETF whose basket contains a frozen symbol (``freeze_etfs``); with no valid symbol and
   ``freeze_all_if_no_symbols``, all 12. Each frozen symbol gets a SNIPE ``CancelIntent(scope="symbol")``, which
   the order manager sends as ``CANCEL_ALL?symbol=`` and which also catches orders still in flight. The alert
   ``announcement`` carries the headline and the symbols.
2. **While frozen** the runner drops every intent for the symbol except cancels, flatten's reduce-only targets and
   S1-r3 rests confirmed by ``s1ok``.
3. **Overrides** go through the analyst's ``adjust.json`` and the console's ``f``/``m``/``a`` (engine adapter).
4. **Unfreeze.** ``u SYM`` returns the symbol to normal. ``ua SYM`` enters the adjusting state for
   ``adjust_window_s`` (45 s): S3-style targets capped at ``adjust_cap_frac * L`` (TAKE) in any open phase except the
   first 3 s and round 3 from ``s1r3.block_from``; it holds once the house has covered ``adjust_stop_coverage`` of
   the distance, ``(mid - mid_at_announce) / (mu - mid_at_announce) >= 0.8``; after the window the symbol returns
   to normal with the override in place. Adjusting symbols stay in ``frozen()`` so that no other strategy trades
   them meanwhile; the runner lets this strategy's own intents through.
A ``news`` message (a headline without economics) only raises the alert ``news``.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ctcbot.strategies.base import (Strategy, StrategyDeps, book_tradeable, clip_of, fv_usable, fv_value,
                                    limit_of, market_open, t_of)
from ctcbot.strategies.s3 import S3Rule
from ctcbot.types import SYMBOLS, CancelIntent, Command, Intent, Priority, StrategyContext

#: The packet's baskets, used only when the case has none.
DEFAULT_BASKETS: dict[str, dict[str, int]] = {"ARCH": {"PORT": 1, "ISLE": 1, "AQUA": 2, "GRID": 1, "FERRY": 2,
                                                       "HAVEN": 1},
                                              "REBLD": {"RECON": 2, "MAT": 2, "GEN": 1}}


def named_symbols(msg: Mapping[str, Any]) -> list[str]:
    """Valid upper-cased tickers of an announcement's ``symbols`` field (a list or a single string)."""
    raw = msg.get("symbols") if isinstance(msg, Mapping) else None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for s in raw:
        if isinstance(s, str) and s.strip().upper() in SYMBOLS and s.strip().upper() not in out:
            out.append(s.strip().upper())
    return out


def expand_symbols(named: Iterable[str], links: Mapping[str, Iterable[str]],
                   baskets: Mapping[str, Mapping[str, Any]], freeze_etfs: bool = True) -> frozenset[str]:
    """``named + links[named]`` plus (with ``freeze_etfs``) every ETF whose basket holds a frozen symbol."""
    fz = {s for s in named if s in SYMBOLS}
    for s in list(fz):
        fz.update(x for x in (links.get(s) or []) if isinstance(x, str) and x in SYMBOLS)
    if freeze_etfs:
        for etf, basket in baskets.items():
            if etf in SYMBOLS and isinstance(basket, Mapping) and any(c in fz for c in basket):
                fz.add(etf)
    return frozenset(fz)


def case_baskets(deps: StrategyDeps) -> dict[str, dict[str, Any]]:
    b = getattr(deps.case, "baskets", None)
    if isinstance(b, Mapping) and b:
        return {str(k): dict(v) for k, v in b.items() if isinstance(v, Mapping)}
    return {k: dict(v) for k, v in DEFAULT_BASKETS.items()}


class Announce(Strategy):
    """Announcement freeze, ``u`` / ``ua`` and the adjusting state (see the module docstring)."""

    name = "announce"

    def __init__(self, deps: StrategyDeps) -> None:
        super().__init__(deps)
        links = self.p.get("links") or {}
        self.links: dict[str, list[str]] = {k: [x for x in v if isinstance(x, str)] for k, v in links.items()
                                            if isinstance(v, list)}
        self.baskets = case_baskets(deps)
        self.rule = S3Rule(deps, deps.cfg.get("strategies.s3"))
        self._clear()

    def _clear(self) -> None:
        self.frozen: set[str] = set()
        self.adjusting: dict[str, dict[str, float]] = {}
        self.mid_at_announce: dict[str, float] = {}
        self.announcements: list[dict[str, Any]] = []
        self._held: set[str] = set()

    def reset_season(self) -> None:
        super().reset_season()
        self._clear()

    def expand(self, named: Iterable[str]) -> frozenset[str]:
        return expand_symbols(named, self.links, self.baskets, bool(self.p.get("freeze_etfs", True)))

    def frozen_set(self) -> frozenset[str]:
        """Frozen symbols plus those in the adjusting state (what ``StrategyRunner.frozen()`` reports)."""
        return frozenset(self.frozen) | frozenset(self.adjusting)

    # ------------------------------------------------------------------ decision
    def decide(self, ctx: StrategyContext) -> list[Intent]:
        out: list[Intent] = []
        for ev in ctx.news:
            if ev.mtype == "announcement" and self.enabled:
                out.extend(self._freeze(ctx, ev.msg, ev.round, ev.seq))
            elif ev.mtype == "news":
                self.alert("news", f"news: {ev.msg.get('headline', '')}", headline=ev.msg.get("headline"),
                           round=ev.round, seq=ev.seq)
        for sym in list(self.adjusting):
            st = self.adjusting[sym]
            if ctx.now >= st["until"]:
                del self.adjusting[sym]
                self._held.discard(sym)
                self.event("announce_adjust_end", symbol=sym, held_s=ctx.now - st["start"])
                continue
            out.extend(self._adjust(ctx, sym, st))
        return out

    def _freeze(self, ctx: StrategyContext, msg: Mapping[str, Any], round_: int | None, seq: int | None
                ) -> list[Intent]:
        named = named_symbols(msg)
        if named:
            fz = self.expand(named)
        elif self.p.get("freeze_all_if_no_symbols", True):
            fz = frozenset(SYMBOLS)
        else:
            fz = frozenset()
        headline = str(msg.get("headline", ""))
        t = t_of(ctx)
        for s in fz:
            self.frozen.add(s)
            self.adjusting.pop(s, None)
            self._held.discard(s)
            b = ctx.books.get(s)
            if b is not None and b.mid is not None:
                self.mid_at_announce[s] = float(b.mid)
        rec = {"headline": headline, "named": named, "frozen": sorted(fz), "round": round_, "seq": seq, "t": t,
               "mono": ctx.now}
        self.announcements.append(rec)
        self.alert("announcement", f"ANNOUNCEMENT: {headline} -> frozen {', '.join(sorted(fz)) or '-'}", **rec)
        return [CancelIntent(strategy=self.name, priority=Priority.SNIPE, scope="symbol", symbol=s,
                             reason="announcement") for s in sorted(fz)]

    def _adjust(self, ctx: StrategyContext, sym: str, st: Mapping[str, float]) -> list[Intent]:
        t = t_of(ctx)
        r = ctx.clock.round
        if t is None or not market_open(ctx):
            return []
        try:
            if t < ctx.schedule.at(self.cfg.get("guards.opening.until", ["open", 3.0])):
                return []
            if r == 3 and t >= ctx.schedule.at(self.cfg.get("strategies.s1r3.block_from", ["final", -60])):
                return []
        except (KeyError, TypeError, ValueError):
            return []
        ok, why = fv_usable(ctx, self.cfg)
        if not ok:
            self.rlog(("adj_fv", sym), "announce_adjust_skip", ctx.now, symbol=sym, reason=why)
            return []
        b = ctx.books.get(sym)
        v = fv_value(ctx, sym)
        if not book_tradeable(b) or v is None or not self.sane(ctx, sym):
            return []
        assert b is not None and b.mid is not None
        mu, sd = v
        mid0 = st.get("mid0")
        if mid0 is not None:
            denom = mu - mid0
            cov = 1.0 if abs(denom) < 1e-12 else (float(b.mid) - mid0) / denom
            if cov >= float(self.p.get("adjust_stop_coverage", 0.8)):
                if sym not in self._held:
                    self._held.add(sym)
                    self.event("announce_adjust_hold", symbol=sym, coverage=cov, mu=mu, mid=b.mid, mid0=mid0)
                return []
        L = limit_of(ctx, sym)
        cap = int(float(self.p.get("adjust_cap_frac", 0.5)) * L)
        if cap <= 0:
            return []
        phase = ctx.schedule.cost_phase(t)
        it, _ = self.rule.intent(ctx, sym, b, mu, sd, self.rule.k(r), phase, cap, clip_of(ctx, sym, "s3", self.cfg),
                                 allow_exit=False, strategy=self.name, priority=Priority.TAKE,
                                 decimals=self.decimals)
        return [it] if it is not None else []

    # ------------------------------------------------------------------ commands
    def _targets(self, args: tuple[str, ...]) -> list[str]:
        if not args:
            return []
        a = str(args[0]).upper()
        if a == "ALL":
            return sorted(self.frozen_set())
        return [a] if a in SYMBOLS else []

    def on_command(self, cmd: Command, ctx: StrategyContext) -> list[Intent] | None:
        if cmd.name not in ("u", "ua"):
            return None
        syms = self._targets(cmd.args)
        if not syms:
            self.event("command_error", command=cmd.name, args=list(cmd.args), reason="expected a symbol or all")
            return []
        for s in syms:
            if cmd.name == "u":
                self.frozen.discard(s)
                self.adjusting.pop(s, None)
                self._held.discard(s)
                self.event("unfreeze", symbol=s)
            else:
                b = ctx.books.get(s)
                mid0 = self.mid_at_announce.get(s)
                if mid0 is None and b is not None and b.mid is not None:
                    mid0 = float(b.mid)
                self.frozen.discard(s)
                self._held.discard(s)
                window = float(self.p.get("adjust_window_s", 45.0))
                self.adjusting[s] = {"start": ctx.now, "until": ctx.now + window,
                                     **({"mid0": mid0} if mid0 is not None else {})}
                self.event("announce_adjust_start", symbol=s, window_s=window, mid0=mid0)
        return []

    # ------------------------------------------------------------------ persistence
    def state_dict(self) -> dict:
        # adjusting symbols are saved as frozen: after a restart the operator re-confirms with u / ua
        return {"enabled": self.enabled, "frozen": sorted(self.frozen_set()),
                "mid_at_announce": dict(self.mid_at_announce), "announcements": list(self.announcements[-50:])}

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        if not isinstance(d, dict):
            return
        fz = d.get("frozen")
        if isinstance(fz, list):
            self.frozen = {s for s in fz if isinstance(s, str) and s in SYMBOLS}
        m = d.get("mid_at_announce")
        if isinstance(m, dict):
            self.mid_at_announce = {k: float(v) for k, v in m.items() if k in SYMBOLS and isinstance(v, (int, float))}
        a = d.get("announcements")
        if isinstance(a, list):
            self.announcements = [x for x in a if isinstance(x, dict)]
        self.adjusting = {}

    def status(self) -> dict:
        return {"enabled": self.enabled, "frozen": sorted(self.frozen), "adjusting": sorted(self.adjusting),
                "announcements": len(self.announcements)}
