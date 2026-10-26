"""S1-r3, settlement positioning after the round-3 final report (DESIGN.md section 6.4; playbook section 4.3).

1. **Arm** in round 3 on the final ``damage_report`` (``final_seq`` = its seq), or on a snapshot damage list that
   carries it (``final_seq = -1``, via ``SeasonInfo``); a late arm is accepted until ``late_trigger_ok_until``.
2. **S** comes from the first ok fair value with ``seq_hi >= final_seq`` (for -1, the first one computed after the
   arm) whose ``diag.status_r3`` is "final" when present. If none arrives within ``engine_wait_s`` and
   ``laws_fallback`` is set, S comes from ``LawsCrossCheck.values()`` with the wide epsilon on every symbol (alert
   ``s1r3_laws_fallback``); a qualifying engine value later replaces it.
3. **Gates** (a)-(g), each failure logged: in ``symbols``; not in the case diff; not frozen (unless ``s1ok``), not
   announced after ``announce_lock_from`` without ``s1ok``, not disabled or lead-lag owned; not excluded (``x1`` or
   the sanity check); the laws cross-check, failing closed; coverage (closure symbols fail when a round is
   missing); the rounds 1-2 law check (``require_law_check``).
4. **Epsilon** is ``eps``, or ``eps_wide`` for a closure symbol when any of rounds 1-3 is gapped.
5. **Orders**: for each passing symbol a SNIPE ``RestIntent`` pair at ``round_px(S - eps, BUY)`` and
   ``round_px(S + eps, SELL)`` with ``qty=None`` (all strategy room), so the position goes to +-L toward settlement
   and nothing can fill worse than S -+ eps. REIN's riskless ask is cancelled first.
6. **Sweep**: the same rests are re-emitted every ``fast_s`` until ``fast_until`` and every ``slow_s`` until
   ``sweep_end`` (the order manager tops up whatever exchange cancels or fills freed).
7. **Sanity** once at ``sanity_at`` (756 s, never earlier): a stock whose mid is more than
   ``max(sanity_eps_mult * eps, sanity_min_frac * S)`` from S is cancelled and excluded, with an alert. A stock
   first placed after ``sanity_at`` (a late arm, or a gate cleared by ``lawok``/``s1ok``/``caseok`` after 756 s) gets
   the same check before its first placement (hardening, DESIGN.md §13).

ETF lead-lag blocks ARCH and REBLD only before ``strategies.etf_leadlag.end`` (738 s): S1-r3 starts after it, so
lead-lag never removes the ETFs from the settlement trade (hardening; playbook §4.5 removes them from H1 and S3).
"""
from __future__ import annotations

import math
from typing import Any

from ctcbot.models.coverage import CLOSURE_SYMBOLS
from ctcbot.strategies.announce import case_baskets, expand_symbols, named_symbols
from ctcbot.strategies.base import Strategy, StrategyDeps, round_px, symbols_of, t_of
from ctcbot.types import (STOCKS, SYMBOLS, CancelIntent, Command, FairValueSnapshot, Intent, Priority, RestIntent,
                          Side, StrategyContext)


def capturable_usd(levels: Any, side: Side, limit_px: float, S: float, room: int) -> float:
    """Dollars our order would capture right now against settlement S.

    Walks the visible levels it would take (asks for a buy, bids for a sell; best first) up to ``room`` shares,
    counting S - price per share for a buy and price - S for a sell. Levels beyond ``limit_px`` are not taken.
    """
    usd, got = 0.0, 0
    for lv in levels or ():
        try:
            p, q = float(lv.price), int(lv.qty)
        except (AttributeError, TypeError, ValueError):
            continue
        if not math.isfinite(p) or q <= 0:
            continue
        if (side is Side.BUY and p > limit_px) or (side is Side.SELL and p < limit_px):
            break
        take = min(q, room - got)
        if take <= 0:
            break
        usd += take * ((S - p) if side is Side.BUY else (p - S))
        got += take
    return max(0.0, usd)


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


class S1R3(Strategy):
    """Round-3 settlement positioning (see the module docstring)."""

    name = "s1r3"

    def __init__(self, deps: StrategyDeps) -> None:
        super().__init__(deps)
        self.symbols = symbols_of(self.p, "symbols", list(SYMBOLS))
        ap = deps.cfg.get("strategies.announce", {}) or {}
        self._links = {k: list(v) for k, v in (ap.get("links") or {}).items() if isinstance(v, list)}
        self._freeze_etfs = bool(ap.get("freeze_etfs", True))
        self._freeze_all = bool(ap.get("freeze_all_if_no_symbols", True))
        self._baskets = case_baskets(deps)
        self.rein_symbol = str((deps.cfg.get("strategies.rein_ask", {}) or {}).get("symbol", "REIN"))
        self._eps: dict[str, tuple[float, float, float]] = {}
        for s in SYMBOLS:
            try:
                sc = deps.cfg.sym(s)
                self._eps[s] = (float(sc["eps"]), float(sc["eps_wide"]), float(sc["laws_tol"]))
            except (KeyError, TypeError, ValueError, AttributeError):
                self._eps[s] = (0.25, 0.25, 0.25)
        self._clear()

    # ------------------------------------------------------------------ state
    def _clear(self) -> None:
        self.epoch: int | None = None
        self.armed = False
        self.arm_refused = False
        self.final_seq: int | None = None
        self.arm_mono: float | None = None
        self.fired = False
        self.source: str | None = None           # "engine" | "laws"
        self.S: dict[str, float] = {}
        self.fv_version: int | None = None
        self.eps_used: dict[str, float] = {}
        self.last_emit: float | None = None
        self.sanity_done = False
        self.sanity_excluded: set[str] = set()
        self.sanity_checked: set[str] = set()
        self.x1: set[str] = set()
        self.s1ok: set[str] = set()
        self.late_announced: set[str] = set()
        self.gates: dict[str, str] = {}
        self.placed: set[str] = set()
        self._rein_cancelled = False
        self._fallback_note = False

    def reset_season(self) -> None:
        super().reset_season()
        self._clear()

    def confirmed(self) -> frozenset[str]:
        """Symbols the operator confirmed with ``s1ok`` (the runner lets their rests through a freeze)."""
        return frozenset(self.s1ok)

    def state_dict(self) -> dict:
        return {"enabled": self.enabled, "epoch": self.epoch, "armed": self.armed, "final_seq": self.final_seq,
                "fired": self.fired, "source": self.source, "S": dict(self.S), "eps": dict(self.eps_used),
                "sanity_done": self.sanity_done, "sanity_excluded": sorted(self.sanity_excluded),
                "x1": sorted(self.x1), "s1ok": sorted(self.s1ok), "late_announced": sorted(self.late_announced)}

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        if not isinstance(d, dict):
            return

        def syms(k: str) -> set[str]:
            v = d.get(k)
            return {s for s in v if isinstance(s, str) and s in SYMBOLS} if isinstance(v, list) else set()
        self.epoch = None                     # restored state belongs to the current season (restart rule)
        self.armed = bool(d.get("armed", False))
        fs = d.get("final_seq")
        self.final_seq = fs if isinstance(fs, int) else None
        S = d.get("S") if isinstance(d.get("S"), dict) else {}
        self.S = {k: float(v) for k, v in S.items() if k in SYMBOLS and _finite(v)}
        self.fired = bool(d.get("fired", False)) and bool(self.S)
        self.source = d.get("source") if d.get("source") in ("engine", "laws") else None
        E = d.get("eps") if isinstance(d.get("eps"), dict) else {}
        self.eps_used = {k: float(v) for k, v in E.items() if k in SYMBOLS and _finite(v)}
        self.sanity_done = bool(d.get("sanity_done", False))
        self.sanity_excluded, self.x1, self.s1ok = syms("sanity_excluded"), syms("x1"), syms("s1ok")
        self.late_announced = syms("late_announced")
        self.arm_mono = None
        self.last_emit = None

    def status(self) -> dict:
        return {"enabled": self.enabled, "armed": self.armed, "fired": self.fired, "source": self.source,
                "final_seq": self.final_seq, "placed": sorted(self.placed), "gates": dict(self.gates),
                "x1": sorted(self.x1), "s1ok": sorted(self.s1ok), "sanity_excluded": sorted(self.sanity_excluded)}

    # ------------------------------------------------------------------ decision
    def decide(self, ctx: StrategyContext) -> list[Intent]:
        if self.epoch is not None and ctx.clock.season_epoch != self.epoch:
            self._clear()                     # a new season without reset_season: forget the old one
        self.epoch = ctx.clock.season_epoch
        t = t_of(ctx)
        r = ctx.clock.round
        self._track_announcements(ctx, t, r)
        if not self.enabled or r != 3 or t is None:
            return []
        if not self.armed and not self.arm_refused:
            self._try_arm(ctx, t)
        if not self.armed:
            return []
        if self.arm_mono is None:
            self.arm_mono = ctx.now
        out: list[Intent] = []
        emit_now = False
        if not self.fired or self.source == "laws":
            fv = ctx.fv
            if self._qualifies(ctx, fv):
                assert fv is not None
                switched = self.fired
                self._fire(ctx, fv, t)
                if switched:
                    self.event("s1r3_engine_after_fallback", fv_version=fv.version)
                emit_now = True
            elif not self.fired and ctx.now - self.arm_mono >= float(self.p.get("engine_wait_s", 1.0)) - 1e-9:
                if self.p.get("laws_fallback", True) and self._fire_laws(ctx, t):
                    emit_now = True
            if not self.fired:
                return []
        try:
            sanity_at, fast_until = self.at(ctx, "sanity_at"), self.at(ctx, "fast_until")
            sweep_end = self.at(ctx, "sweep_end")
        except (KeyError, TypeError, ValueError):
            return []
        if not self.sanity_done and t >= sanity_at:
            out.extend(self._sanity(ctx, t))
        if t >= sweep_end:
            return out
        interval = float(self.p.get("fast_s", 0.5)) if t < fast_until else float(self.p.get("slow_s", 2.0))
        if emit_now or self.last_emit is None or ctx.now - self.last_emit >= interval - 1e-9:
            self.last_emit = ctx.now
            out.extend(self._rests(ctx, t))
        return out

    def _track_announcements(self, ctx: StrategyContext, t: float | None, r: int | None) -> None:
        if r != 3 or t is None or not ctx.news:
            return
        try:
            lock_from = self.at(ctx, "announce_lock_from")
        except (KeyError, TypeError, ValueError):
            return
        if t < lock_from:
            return
        for ev in ctx.news:
            if ev.mtype != "announcement":
                continue
            named = named_symbols(ev.msg)
            fz = expand_symbols(named, self._links, self._baskets, self._freeze_etfs) if named else \
                (frozenset(SYMBOLS) if self._freeze_all else frozenset())
            new = set(fz) - self.late_announced
            if new:
                self.late_announced |= new
                self.event("s1r3_late_announcement", symbols=sorted(new), t=t)

    def _try_arm(self, ctx: StrategyContext, t: float) -> None:
        seq: int | None = None
        found = False
        for ev in ctx.news:
            if ev.mtype == "damage_report" and ev.round == 3 and ev.msg.get("stage") == "final":
                found, seq = True, (ev.seq if isinstance(ev.seq, int) else -1)
                break
        if not found and 3 in ctx.season.final:
            found = True
            fs = ctx.season.final_seq.get(3, -1)
            seq = fs if isinstance(fs, int) else -1
        if not found:
            return
        try:
            late_ok = self.at(ctx, "late_trigger_ok_until")
        except (KeyError, TypeError, ValueError):
            late_ok = math.inf
        if t > late_ok:
            self.arm_refused = True
            self.alert("s1r3_late", f"round-3 final report seen at {t:.1f} s, after {late_ok:g} s: S1-r3 not armed",
                       t=t)
            return
        self.armed = True
        self.final_seq = seq
        self.arm_mono = ctx.now
        self.event("s1r3_arm", final_seq=seq, t=t)

    def _qualifies(self, ctx: StrategyContext, fv: FairValueSnapshot | None) -> bool:
        if fv is None or not fv.ok or fv.season_epoch != ctx.clock.season_epoch:
            return False
        if self.final_seq is not None and self.final_seq >= 0:
            if fv.seq_hi is None or fv.seq_hi < self.final_seq:
                return False
        elif self.arm_mono is None or fv.computed_mono <= self.arm_mono:
            return False
        if fv.diag and "status_r3" in fv.diag and fv.diag.get("status_r3") != "final":
            return False
        return any(_finite(fv.values.get(s, (math.nan,))[0]) for s in SYMBOLS)

    def _fire(self, ctx: StrategyContext, fv: FairValueSnapshot, t: float) -> None:
        self.S = {s: float(fv.values[s][0]) for s in SYMBOLS if s in fv.values and _finite(fv.values[s][0])}
        self.fired, self.source, self.fv_version = True, "engine", fv.version
        self.event("s1r3_fire", source="engine", t=t, fv_version=fv.version, seq_hi=fv.seq_hi,
                   final_seq=self.final_seq, S=dict(self.S))

    def _fire_laws(self, ctx: StrategyContext, t: float) -> bool:
        vals = self.deps.laws.values() if self.deps.laws is not None else None
        S = {s: float(v) for s, v in (vals or {}).items() if v is not None and _finite(v)}
        if not S:
            if not self._fallback_note:
                self._fallback_note = True
                self.alert("s1r3_no_value", "S1-r3: no engine value and no law values: nothing placed", t=t)
            return False
        self.S = S
        self.fired, self.source, self.fv_version = True, "laws", None
        self.alert("s1r3_laws_fallback", f"S1-r3: engine value late, using the settlement laws for {sorted(S)}",
                   t=t, S=dict(S))
        return True

    # ------------------------------------------------------------------ gates
    def gate(self, ctx: StrategyContext, sym: str, compare: dict | None, t: float | None = None
             ) -> tuple[str | None, float]:
        """(failing gate or None, epsilon) for one symbol after the fire."""
        g, e = self._gate(ctx, sym, compare, t)
        if g is None and t is not None and sym in STOCKS and sym not in self.sanity_checked:
            try:
                sanity_at = self.at(ctx, "sanity_at")
            except (KeyError, TypeError, ValueError):
                sanity_at = math.inf
            if t >= sanity_at and not self._sane_now(ctx, sym, t, e):
                return "d:sanity", e
        return g, e

    def _sane_now(self, ctx: StrategyContext, sym: str, t: float, eps: float) -> bool:
        """The step-7 check for one stock first placed after ``sanity_at``; marks it checked once a mid exists."""
        b = ctx.books.get(sym)
        if b is None or b.mid is None or sym not in self.S:
            return True
        self.sanity_checked.add(sym)
        S = self.S[sym]
        lim = max(float(self.p.get("sanity_eps_mult", 3.0)) * eps, float(self.p.get("sanity_min_frac", 0.005)) * abs(S))
        dev = float(b.mid) - S
        ok = abs(dev) <= lim
        self.event("s1r3_sanity_late", symbol=sym, t=t, mid=b.mid, S=S, dev=dev, limit=lim, ok=ok)
        if not ok:
            self.sanity_excluded.add(sym)
            self.alert("s1r3_sanity", f"S1-r3 sanity: {sym} mid {b.mid} vs S {S:.4f} (> {lim:.4f}): not placed",
                       symbol=sym, mid=b.mid, S=S, limit=lim)
        return ok

    def _gate(self, ctx: StrategyContext, sym: str, compare: dict | None, t: float | None
              ) -> tuple[str | None, float]:
        eps, eps_wide, _ = self._eps[sym]
        cov = ctx.season.coverage
        if sym not in self.symbols:
            return "a:symbols", eps
        if sym not in self.S:
            return "no_value", eps
        if sym in ctx.season.case_diff:
            return "b:case_diff", eps
        if sym in ctx.frozen and sym not in self.s1ok:
            return "c:frozen", eps
        if sym in self.late_announced and sym not in self.s1ok:
            return "c:announced_late", eps
        if sym in ctx.disabled:
            return "c:disabled", eps
        if sym in ctx.leadlag and (t is None or t < self._leadlag_end(ctx)):
            return "c:leadlag", eps
        if sym in self.x1:
            return "d:x1", eps
        if sym in self.sanity_excluded:
            return "d:sanity", eps
        if self.source != "laws":
            if self.deps.laws is None:
                if sym in CLOSURE_SYMBOLS:
                    return "e:laws_unavailable", eps
                self.rlog(("nolaws", sym), "s1r3_no_laws", ctx.now, symbol=sym)
            else:
                if compare is None:
                    return "e:laws_incomplete", eps
                d, ok = compare.get(sym, (None, None))
                if d is None:
                    return "e:law_value_none", eps
                if not ok:
                    return "e:laws_mismatch", eps
        if sym in CLOSURE_SYMBOLS and any(cov.get(r, "missing") == "missing" for r in (1, 2, 3)):
            return "f:coverage_missing", eps
        if self.p.get("require_law_check", True) and not self.deps.law_check.passed(sym):
            return "g:law_check", eps
        if self.source == "laws":
            return None, eps_wide
        if sym in CLOSURE_SYMBOLS and any(cov.get(r) == "gapped" for r in (1, 2, 3)):
            return None, eps_wide
        return None, eps

    def _leadlag_end(self, ctx: StrategyContext) -> float:
        try:
            return float(ctx.schedule.at(self.cfg.get("strategies.etf_leadlag.end")))
        except (KeyError, TypeError, ValueError):
            return math.inf

    def _rests(self, ctx: StrategyContext, t: float) -> list[Intent]:
        compare = None
        if self.deps.laws is not None and self.source != "laws":
            vals = self.deps.laws.values()
            compare = self.deps.laws.compare(self.S) if vals is not None else None
        rests: list[RestIntent] = []
        pre: list[Intent] = []
        placed: set[str] = set()
        for sym in SYMBOLS:
            g, eps = self.gate(ctx, sym, compare, t)
            if g is not None:
                if self.gates.get(sym) != g:
                    self.gates[sym] = g
                    detail = compare.get(sym) if compare is not None else None
                    self.event("s1r3_gate", symbol=sym, gate=g, t=t, S=self.S.get(sym), law_diff=detail)
                continue
            if self.gates.get(sym) is not None or sym not in self.placed:
                self.event("s1r3_pass", symbol=sym, t=t, S=self.S[sym], eps=eps)
            self.gates[sym] = None  # type: ignore[assignment]
            S = self.S[sym]
            self.eps_used[sym] = eps
            b = ctx.books.get(sym)
            bid = b.bid if b is not None else None
            ask = b.ask if b is not None else None
            if sym == self.rein_symbol and not self._rein_cancelled:
                self._rein_cancelled = True
                pre.append(CancelIntent(strategy=self.name, priority=Priority.SNIPE, scope="key", key="rein_ask",
                                        symbol=sym, reason="s1r3 takes REIN"))
            placed.add(sym)
            lim = int(ctx.limits.get(sym, 0))
            pos = int(ctx.positions.get(sym, 0))
            if S - eps > 0:
                buy_px = round_px(S - eps, Side.BUY, self.decimals)
                rests.append(RestIntent(strategy=self.name, key=f"s1r3:{sym}:buy", symbol=sym, side=Side.BUY,
                                        price=buy_px, priority=Priority.SNIPE,
                                        qty=None, edge_usd=max(0.0, S - eps - ask) if ask is not None else 0.0,
                                        reason=f"S {S:.4f} - eps {eps:g}",
                                        value_usd=capturable_usd(b.asks if b is not None else (), Side.BUY, buy_px,
                                                                 S, max(0, lim - pos))))
            sell_px = round_px(S + eps, Side.SELL, self.decimals)
            rests.append(RestIntent(strategy=self.name, key=f"s1r3:{sym}:sell", symbol=sym, side=Side.SELL,
                                    price=sell_px, priority=Priority.SNIPE,
                                    qty=None, edge_usd=max(0.0, bid - S - eps) if bid is not None else 0.0,
                                    reason=f"S {S:.4f} + eps {eps:g}",
                                    value_usd=capturable_usd(b.bids if b is not None else (), Side.SELL, sell_px,
                                                             S, max(0, lim + pos))))
        self.placed = placed
        # Most valuable first: the first orders out win the race against the house's repricing and other teams.
        rests.sort(key=lambda x: -(x.value_usd or 0.0))
        return pre + rests

    def _sanity(self, ctx: StrategyContext, t: float) -> list[Intent]:
        self.sanity_done = True
        out: list[Intent] = []
        mult = float(self.p.get("sanity_eps_mult", 3.0))
        frac = float(self.p.get("sanity_min_frac", 0.005))
        rows = {}
        for sym in STOCKS:
            if sym not in self.placed or sym not in self.S:
                continue
            b = ctx.books.get(sym)
            if b is None or b.mid is None:
                continue
            self.sanity_checked.add(sym)
            S = self.S[sym]
            eps = self.eps_used.get(sym, self._eps[sym][0])
            lim = max(mult * eps, frac * abs(S))
            dev = float(b.mid) - S
            rows[sym] = {"mid": b.mid, "S": S, "dev": dev, "limit": lim}
            if abs(dev) > lim:
                self.sanity_excluded.add(sym)
                self.placed.discard(sym)
                out.append(CancelIntent(strategy=self.name, priority=Priority.SNIPE, scope="key_prefix",
                                        key=f"s1r3:{sym}:", symbol=sym, reason="s1r3 sanity"))
                self.alert("s1r3_sanity", f"S1-r3 sanity: {sym} mid {b.mid} vs S {S:.4f} (> {lim:.4f}): cancelled",
                           symbol=sym, mid=b.mid, S=S, limit=lim)
        self.event("s1r3_sanity_check", t=t, rows=rows, excluded=sorted(self.sanity_excluded))
        return out

    # ------------------------------------------------------------------ commands
    def set_enabled(self, on: bool, ctx: StrategyContext | None = None) -> list[Intent]:
        super().set_enabled(on, ctx)
        if on:
            return []
        return [CancelIntent(strategy=self.name, priority=Priority.SNIPE, scope="strategy", reason="s1r3 off")]

    def on_command(self, cmd: Command, ctx: StrategyContext) -> list[Intent] | None:
        if cmd.name == "s1r3":
            return super().on_command(cmd, ctx)
        if cmd.name not in ("x1", "s1ok", "lawok"):
            return None
        if not cmd.args:
            self.event("command_error", command=cmd.name, reason="expected a symbol")
            return []
        arg = str(cmd.args[0]).upper()
        if cmd.name == "lawok":
            if arg != "ALL" and arg not in SYMBOLS:
                self.event("command_error", command="lawok", args=list(cmd.args), reason="unknown symbol")
                return []
            self.deps.law_check.clear("all" if arg == "ALL" else arg)
            return []
        if arg not in SYMBOLS:
            self.event("command_error", command=cmd.name, args=list(cmd.args), reason="unknown symbol")
            return []
        if cmd.name == "s1ok":
            self.s1ok.add(arg)
            self.event("s1ok", symbol=arg)
            return []
        self.x1.add(arg)
        self.event("x1", symbol=arg)
        return [CancelIntent(strategy=self.name, priority=Priority.SNIPE, scope="key_prefix", key=f"s1r3:{arg}:",
                             symbol=arg, reason="x1")]
