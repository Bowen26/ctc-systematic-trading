"""H1, the formation leg (DESIGN.md section 6.1; playbook section 4.2 with the [testnet] changes).

Per round in ``rounds``:

1. **Size.** Round 1 uses ``size_r1``; with ``auto_size_from_check`` rounds 2-3 use ``size_on_pass`` or
   ``size_on_fail`` from the round-1 check (``size_r1`` without it); the operator's ``h1 X`` overrides until
   changed (``h1 auto`` clears it). After a promotion H1 uses ``size_on_fail`` until ``h1 X``. The size is 0 when
   the mean half-spread of the H1 symbols at the reference time is at least ``off_if_half_spread_bp``.
2. **Reference mid** at the first decision with ``t >= ref_mid_at`` (48 s).
3. **Entry** for ``t`` in ``[entry_start, entry_last + 1)`` (50-56 s, clips at 50/52/54/56 s) when the clock
   quality is in ``clock_quality``: ``target = h1_dir * round(size * L)`` in L/4 clips, capped at the 48-s mid
   +- ``cap_bp`` and the current mid +- ``max_from_mid_bp``. No crowding skip, no entry on ``storm_formed``.
4. **No forced exit**: from 58 s H1 emits nothing and S3 inherits the position at 80 s.
5. **Fallback exit**: if at ``fallback_deadline`` (76 s) the engine is not usable or has not ingested this
   round's storm, H1 exits over ``[fallback_start, fallback_last + 1)`` (84-98 s) in L/8 reduce-only HEDGE clips.
6. **Round-1 check** at ``check_t1`` (90 s): the 58->90 s book move of ``check_symbols`` against the H1 *entry*
   fills' cost per share versus the 48-s mid; pass iff there was a fill, the move is positive and the cost is at
   most ``check_cost_frac`` of it. A cost ratio (actual / the phase tables) of ``drop_rebuilders_cost_ratio`` or
   more drops ``rebuilders`` from H1 for rounds 2-3.

Hardening (DESIGN.md §13):

* the check uses only entry fills (in the symbol's H1 direction); fallback-exit sells cannot pass a failing check;
* the cost ratio compares each symbol's *first* entry clip (the only clip the phase tables describe: later clips
  pay our own follow-on) with the walk table, and ignores LIMIT_BOUND fills (priced at our limit, not the fill);
* the check runs once per season: a restart after 90 s keeps the saved result instead of re-running it on an
  empty fill list;
* entry clips run on the fixed grid ``entry_start + k * clip_interval_s`` (``slot_anchor``), so API latency does
  not push the 4th clip out of the window, and no clip goes at or after the window's end (``valid_until``).
"""
from __future__ import annotations

import math
from typing import Any

from ctcbot.strategies.base import (Strategy, StrategyDeps, blocked, cap_buy, cap_sell, clip_of, cost_x,
                                    expected_clip_cost_bp, fo_allow_bp, fv_usable, limit_of, symbols_of, t_of)
from ctcbot.types import Command, Fill, Intent, Priority, PriceQuality, Side, StrategyContext, TargetIntent


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


class H1(Strategy):
    """The formation leg (see the module docstring)."""

    name = "h1"

    def __init__(self, deps: StrategyDeps) -> None:
        super().__init__(deps)
        self.rounds = [int(r) for r in self.p.get("rounds", [1, 2, 3])]
        self.symbols = symbols_of(self.p, "symbols", [])
        self.rebuilders = set(symbols_of(self.p, "rebuilders", []))
        self.check_symbols = symbols_of(self.p, "check_symbols", ["HAVEN", "PORT", "ARCH"])
        self.clock_quality = set(symbols_of(self.p, "clock_quality", ["server", "feed"]))
        # Set by the runner: is S3 going to manage the inherited position in round r? (23 Oct: without S3 the H1
        # position had no exit and rode passively until the flatten; now H1 takes its fallback exit instead.)
        self.s3_active: Any = None
        self._season_reset()

    # ------------------------------------------------------------------ state
    def _season_reset(self) -> None:
        self.check_result: bool | None = None
        self.check_info: dict[str, Any] = {}
        self.drop_rebuilders = False
        self.cost_ratio: float | None = None
        self.operator_size: float | None = None
        self.promoted = False
        self._round_key: tuple | None = None
        self._seen_fills: set[tuple] = set()
        self._round_reset()

    def _round_reset(self) -> None:
        self.mid48: dict[str, float] = {}
        self.ref_done = False
        self.size: float | None = None
        self.size_reason = ""
        self.spread_off = False
        self.mid58: dict[str, float] = {}
        self.mid90: dict[str, float] = {}
        self.t0_done = self.t1_done = False
        self.check_done = False
        self.fallback_checked = False
        self.fallback = False
        self.fills: list[Fill] = []
        self.clips: dict[str, int] = {}          # entry clip size per symbol (for the table cost)
        self.slot_anchor: float | None = None    # mono of entry_start this round

    def reset_season(self) -> None:
        super().reset_season()
        self._season_reset()

    def on_promotion(self) -> None:
        self.check_result = None
        self.operator_size = None
        self.promoted = True
        self.event("h1_promotion", size=self.p.get("size_on_fail"))

    def state_dict(self) -> dict:
        return {"enabled": self.enabled, "check_result": self.check_result, "drop_rebuilders": self.drop_rebuilders,
                "cost_ratio": self.cost_ratio, "operator_size": self.operator_size, "promoted": self.promoted}

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        if not isinstance(d, dict):
            return
        if d.get("check_result") in (True, False, None):
            self.check_result = d.get("check_result")
        self.drop_rebuilders = bool(d.get("drop_rebuilders", False))
        cr = d.get("cost_ratio")
        self.cost_ratio = float(cr) if isinstance(cr, (int, float)) else None
        os_ = d.get("operator_size")
        self.operator_size = float(os_) if isinstance(os_, (int, float)) and 0 <= os_ <= 1 else None
        self.promoted = bool(d.get("promoted", False))

    def status(self) -> dict:
        return {"enabled": self.enabled, "size": self.size, "size_reason": self.size_reason,
                "check": self.check_result, "drop_rebuilders": self.drop_rebuilders, "cost_ratio": self.cost_ratio,
                "operator_size": self.operator_size, "promoted": self.promoted, "fallback": self.fallback}

    # ------------------------------------------------------------------ sizing
    def size_for(self, round_: int | None) -> tuple[float, str]:
        """(size, reason) for a round before the half-spread rule."""
        if not self.enabled:
            return 0.0, "disabled"
        if round_ not in self.rounds:
            return 0.0, "round_off"
        if self.operator_size is not None:
            return self.operator_size, "operator"
        if self.promoted:
            return float(self.p.get("size_on_fail", 0.5)), "promoted"
        if round_ == 1:
            return float(self.p.get("size_r1", 0.5)), "r1"
        if self.p.get("auto_size_from_check", True):
            if self.check_result is True:
                return float(self.p.get("size_on_pass", 1.0)), "check_pass"
            return float(self.p.get("size_on_fail", 0.5)), ("check_fail" if self.check_result is False
                                                             else "no_check")
        return float(self.p.get("size_r1", 0.5)), "fixed"

    def entry_symbols(self, round_: int | None) -> list[str]:
        if self.drop_rebuilders and round_ in (2, 3):
            return [s for s in self.symbols if s not in self.rebuilders]
        return list(self.symbols)

    def _dir(self, sym: str) -> int:
        try:
            d = int(self.cfg.sym(sym).get("h1_dir", 0))
        except (KeyError, TypeError, ValueError, AttributeError):
            return 0
        return 1 if d > 0 else (-1 if d < 0 else 0)

    # ------------------------------------------------------------------ decision
    def decide(self, ctx: StrategyContext) -> list[Intent]:
        t = t_of(ctx)
        r = ctx.clock.round
        if t is None or r is None:
            return []
        key = (ctx.clock.season_epoch, r)
        if key != self._round_key:
            self._round_key = key
            self._round_reset()
        self._collect_fills(ctx)
        out: list[Intent] = []
        try:
            ref_at, t0, t1 = self.at(ctx, "ref_mid_at"), self.at(ctx, "check_t0"), self.at(ctx, "check_t1")
            e0, e1 = self.at(ctx, "entry_start"), self.at(ctx, "entry_last") + 1.0
            fd = self.at(ctx, "fallback_deadline")
            f0, f1 = self.at(ctx, "fallback_start"), self.at(ctx, "fallback_last") + 1.0
        except (KeyError, TypeError, ValueError):
            return []
        if not self.ref_done and t >= ref_at:
            self._reference(ctx, r, t)
        if not self.t0_done and t >= t0:
            self.mid58 = self._mids(ctx, self.check_symbols)
            self.t0_done = True
        if not self.t1_done and t >= t1:
            self.mid90 = self._mids(ctx, self.check_symbols)
            self.t1_done = True
            if r == 1 and not self.check_done:
                if self.check_result is not None:
                    # a restart past 90 s: the saved result stands (hardening; the fills are gone)
                    self.check_done = True
                    self.event("h1_check_kept", passed=self.check_result, cost_ratio=self.cost_ratio)
                else:
                    self._round1_check()
        if e0 <= t < e1 and self.enabled:
            if self.slot_anchor is None:
                self.slot_anchor = ctx.now - (t - e0)
            out.extend(self._entry(ctx, r, t, valid_until=ctx.now + (e1 - t)))
        if not self.fallback_checked and t >= fd:
            self._fallback_decision(ctx, t)
        if self.fallback and f0 <= t < f1:
            out.extend(self._fallback_exit(ctx, r, t, valid_until=ctx.now + (f1 - t)))
        return out

    def _mids(self, ctx: StrategyContext, syms: list[str]) -> dict[str, float]:
        out = {}
        for s in syms:
            b = ctx.books.get(s)
            if b is not None and b.mid is not None and b.mid > 0:
                out[s] = float(b.mid)
        return out

    def _collect_fills(self, ctx: StrategyContext) -> None:
        for f in ctx.fills:
            if f.strategy != self.name:
                continue
            k = (f.local_id, f.order_id, f.symbol, int(f.side.sign), f.qty, f.price, f.mono, f.source)
            if k in self._seen_fills:
                continue
            self._seen_fills.add(k)
            self.fills.append(f)

    def _reference(self, ctx: StrategyContext, r: int, t: float) -> None:
        self.ref_done = True
        self.mid48 = self._mids(ctx, sorted(set(self.symbols) | set(self.check_symbols)))
        size, why = self.size_for(r)
        hs = [ctx.books[s].half_spread_bp for s in self.entry_symbols(r)
              if s in ctx.books and ctx.books[s].half_spread_bp is not None]
        mean_hs = _mean([float(x) for x in hs])
        off = self.p.get("off_if_half_spread_bp")
        self.spread_off = off is not None and mean_hs is not None and mean_hs >= float(off)
        if size > 0 and self.spread_off:
            size, why = 0.0, f"half_spread {mean_hs:.1f} bp >= {off}"
        self.size, self.size_reason = size, why
        self.event("h1_ref", round=r, t=t, size=size, reason=why, mean_half_spread_bp=mean_hs, mid48=self.mid48,
                   drop_rebuilders=self.drop_rebuilders)

    def _entry(self, ctx: StrategyContext, r: int, t: float, valid_until: float | None = None) -> list[Intent]:
        if not self.ref_done:          # started after the reference time without a 48-s decision
            self._reference(ctx, r, t)
        size, why = self.size_for(r)   # re-read every cycle so that an operator 'h1 X' applies at once
        if self.spread_off:
            size = 0.0
        if size != self.size:
            self.event("h1_size_change", round=r, t=t, size=size, reason=why, previous=self.size)
            self.size, self.size_reason = size, why
        if size <= 0:
            return []
        if ctx.clock.quality not in self.clock_quality:
            self.rlog(("clockq",), "h1_skip", ctx.now, reason="clock_quality", quality=ctx.clock.quality, t=t)
            return []
        cap_bp = self.p.get("cap_bp")
        mfm = self.p.get("max_from_mid_bp")
        out: list[Intent] = []
        for sym in self.entry_symbols(r):
            d = self._dir(sym)
            if d == 0 or blocked(ctx, sym):
                continue
            b = ctx.books.get(sym)
            if b is None or b.mid is None or b.mid <= 0:
                continue
            L = limit_of(ctx, sym)
            target = d * int(round(size * L))
            if target == 0:
                continue
            mid = float(b.mid)
            ref = self.mid48.get(sym)
            if ref is None:
                ref = mid
                self.rlog(("noref", sym), "h1_no_ref", ctx.now, symbol=sym, t=t)
            clip = clip_of(ctx, sym, "h1", self.cfg)
            side = Side.BUY if d > 0 else Side.SELL
            if d > 0:
                cap = ref * (1.0 + float(cap_bp) / 1e4) if cap_bp is not None else None
                lb, ls = self.px(cap_buy(mid, None, None, mfm, cap), Side.BUY), None
                if lb is None:
                    continue
            else:
                cap = ref * (1.0 - float(cap_bp) / 1e4) if cap_bp is not None else None
                lb, ls = None, self.px(cap_sell(mid, None, None, mfm, cap), Side.SELL)
                if ls is None:
                    continue
            exp = expected_clip_cost_bp(self.deps, sym, "h1_entry", clip, side)
            self.clips[sym] = clip
            out.append(TargetIntent(strategy=self.name, symbol=sym, target=target, clip_qty=clip,
                                    clip_interval_s=float(self.p.get("clip_interval_s", 2.0)), limit_buy=lb,
                                    limit_sell=ls, priority=Priority.TAKE, ttl_s=float(self.p.get("ttl_s", 2.0)),
                                    edge_usd_per_share=0.0, expected_cost_bp=exp, ref_mid=mid,
                                    reason=f"h1 entry size {size:g} ({self.size_reason})", valid_until=valid_until,
                                    slot_anchor=self.slot_anchor,
                                    first_clip_cost_bp=None if exp is None else exp - fo_allow_bp(self.deps, sym)))
        return out

    def _fallback_decision(self, ctx: StrategyContext, t: float) -> None:
        self.fallback_checked = True
        ok, why = fv_usable(ctx, self.cfg)
        ingested = ctx.fv is not None and ctx.fv.round is not None and ctx.fv.round == ctx.clock.round
        s3_on = True
        if callable(self.s3_active):
            try:
                s3_on = bool(self.s3_active(ctx.clock.round))
            except Exception:  # noqa: BLE001 - unknown means "assume S3 runs", the pre-23-Oct behaviour
                s3_on = True
        self.fallback = (not ok) or (not ingested) or (not s3_on)
        self.event("h1_fallback_check", t=t, fallback=self.fallback, fv_usable=ok, reason=why, ingested=ingested,
                   s3_active=s3_on)
        if self.fallback:
            if not s3_on and ok and ingested:
                self.alert("h1_fallback", "S3 is off this round: H1 exits over the fallback window",
                           reason="s3_off")
            else:
                self.alert("h1_fallback", "engine not live at the H1 deadline: H1 exits over the fallback window",
                           reason=why or "storm not ingested")

    def _fallback_exit(self, ctx: StrategyContext, r: int, t: float, valid_until: float | None = None
                       ) -> list[Intent]:
        tt = self.p.get("fallback_through_touch_bp")
        mfm = self.p.get("max_from_mid_bp")
        phase = ctx.schedule.cost_phase(t)
        out: list[Intent] = []
        for sym in self.symbols:
            exp_ = ctx.exposure(sym)
            if exp_ == 0 or self._dir(sym) == 0:
                continue
            b = ctx.books.get(sym)
            if b is None or b.mid is None or b.mid <= 0:
                continue
            mid = float(b.mid)
            clip = clip_of(ctx, sym, "h1_exit", self.cfg)
            if exp_ > 0:
                side, lb, ls = Side.SELL, None, self.px(cap_sell(mid, b.bid, tt, mfm), Side.SELL)
                if ls is None:
                    continue
            else:
                side, lb, ls = Side.BUY, self.px(cap_buy(mid, b.ask, tt, mfm), Side.BUY), None
                if lb is None:
                    continue
            exp = expected_clip_cost_bp(self.deps, sym, phase, clip, side)
            out.append(TargetIntent(strategy=self.name, symbol=sym, target=0, clip_qty=clip,
                                    clip_interval_s=float(self.p.get("clip_interval_s", 2.0)), limit_buy=lb,
                                    limit_sell=ls, priority=Priority.HEDGE, ttl_s=float(self.p.get("ttl_s", 2.0)),
                                    reduce_only=True, edge_usd_per_share=0.0, expected_cost_bp=exp, ref_mid=mid,
                                    reason="h1 fallback exit", valid_until=valid_until,
                                    first_clip_cost_bp=None if exp is None else exp - fo_allow_bp(self.deps, sym)))
        return out

    def _round1_check(self) -> None:
        self.check_done = True
        moves = []
        for s in self.check_symbols:
            a, b = self.mid58.get(s), self.mid90.get(s)
            d = self._dir(s)
            if a and b and d:
                moves.append(d * (b - a) / a * 1e4)
        move_bp = _mean(moves)
        # entry fills only: a fill against the symbol's H1 direction is the fallback exit (hardening)
        fills = [f for f in self.fills if self._dir(f.symbol) != 0 and int(f.side.sign) == self._dir(f.symbol)]
        good = [f for f in fills if f.quality != PriceQuality.LIMIT_BOUND]
        use = good if good else fills
        num = den = 0.0
        for f in use:
            ref = self.mid48.get(f.symbol) or f.ref_mid
            if ref:
                num += f.qty * f.side.sign * (f.price - ref) / ref * 1e4
                den += f.qty
        cost_bp = num / den if den > 0 else None
        frac = float(self.p.get("check_cost_frac", 1.0 / 3.0))
        passed = bool(fills) and move_bp is not None and move_bp > 0 and cost_bp is not None and \
            cost_bp <= frac * move_bp
        act = expd = 0.0
        first = self._first_clip_fills(good)
        for f in first:
            # "round-1 costs >= 1.5x the tables": the tables describe one clip into an undisturbed book, which
            # is each symbol's first clip; later clips also pay our own follow-on (hardening, DESIGN.md §13)
            tab = self._table_bp(f)
            if f.ref_mid and tab is not None and tab > 0:
                act += f.qty * f.side.sign * (f.price - f.ref_mid) / f.ref_mid * 1e4
                expd += f.qty * tab
        ratio = act / expd if expd > 0 else None
        drop_at = self.p.get("drop_rebuilders_cost_ratio")
        drop = drop_at is not None and ratio is not None and ratio >= float(drop_at)
        self.check_result = passed
        self.cost_ratio = ratio
        if drop:
            self.drop_rebuilders = True
        self.check_info = {"passed": passed, "move_bp": move_bp, "moves_bp": moves, "cost_bp": cost_bp,
                           "check_cost_frac": frac, "n_fills": len(fills), "n_priced": len(good),
                           "all_fills_used": not good and bool(fills), "cost_ratio": ratio,
                           "ratio_fills": len(first), "n_exit_fills": len(self.fills) - len(fills),
                           "drop_rebuilders": self.drop_rebuilders, "mid48": dict(self.mid48),
                           "mid58": dict(self.mid58), "mid90": dict(self.mid90)}
        self.event("h1_check", **self.check_info)
        if not passed:
            self.alert("h1_check_failed", f"H1 round-1 check failed (move {move_bp} bp, cost {cost_bp} bp, "
                       f"{len(fills)} fills): rounds 2-3 at size_on_fail", **{k: v for k, v in self.check_info.items()
                                                                              if k in ("move_bp", "cost_bp")})

    @staticmethod
    def _first_clip_fills(fills: list[Fill]) -> list[Fill]:
        """The fills of each symbol's first clip (the order whose first fill came earliest)."""
        first_at: dict[tuple, float] = {}
        for f in fills:
            k = (f.symbol, f.local_id if f.local_id is not None else id(f))
            first_at[k] = min(first_at.get(k, f.mono), f.mono)
        best: dict[str, tuple] = {}
        for k, m in first_at.items():
            if k[0] not in best or m < first_at[best[k[0]]]:
                best[k[0]] = k
        keep = set(best.values())
        return [f for f in fills if (f.symbol, f.local_id if f.local_id is not None else id(f)) in keep]

    def _table_bp(self, f: Fill) -> float | None:
        """The walk table cost of the H1 entry clip of ``f``'s symbol (expected cost minus fo_allow as a fallback)."""
        clip = self.clips.get(f.symbol)
        if clip:
            try:
                v = float(self.deps.cost.walk_bp(f.symbol, "h1_entry", cost_x(self.deps, f.symbol, clip), f.side))
                if v == v and v > 0:
                    return v
            except Exception:  # noqa: BLE001 - fall back to the clip's own expected cost
                pass
        if f.expected_cost_bp is None:
            return None
        return f.expected_cost_bp - fo_allow_bp(self.deps, f.symbol)

    # ------------------------------------------------------------------ commands
    def on_command(self, cmd: Command, ctx: StrategyContext) -> list[Intent] | None:
        if cmd.name != "h1":
            return None
        if not cmd.args:
            self.event("command_error", command="h1", reason="expected on|off|auto|size")
            return []
        a = str(cmd.args[0]).lower()
        if a in ("on", "off"):
            return self.set_enabled(a == "on", ctx)
        if a == "auto":
            self.operator_size = None
            self.event("h1_size", operator_size=None)
            return []
        try:
            v = float(a)
        except ValueError:
            v = math.nan
        if not (0.0 <= v <= 1.0):
            self.event("command_error", command="h1", args=list(cmd.args), reason="size must be in [0, 1]")
            return []
        self.operator_size = v
        self.promoted = False
        self.event("h1_size", operator_size=v)
        return []
