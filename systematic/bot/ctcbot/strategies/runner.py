"""The strategy runner: precedence, guards and exception isolation (DESIGN.md sections 4.C and 6.8).

``StrategyRunner.decide`` calls every enabled strategy in ``ORDER``, each in its own try/except:

* **exceptions** are logged (``exception`` with the traceback and the strategy name); ``risk.
  strategy_exceptions_disable`` (3) of them within ``risk.strategy_exceptions_window_s`` (60 s) disable the
  strategy with an alert (``<strategy> on`` re-enables it). Hardening (DESIGN.md §13): the disable goes through
  ``set_enabled(False)``, so a resting strategy's orders are cancelled; an automatic disable is undone after
  ``risk.strategy_reenable_s`` (30 s), at most ``risk.strategy_reenable_max`` (2) times per season, and at every
  season reset (an operator's ``off`` is never undone);
* **announce runs first** and the later strategies see its updated frozen set in the same cycle;
* **merging**: the first strategy in ``ORDER`` that targets a symbol owns it this cycle; later targets are dropped;
* **frozen symbols**: only cancels, flatten's reduce-only targets, S1-r3 rests of ``s1ok`` symbols and the
  announcement strategy's own (adjusting) intents pass;
* **opening guard** (``guards.opening``): a marketable target or rest (buy limit >= ask, sell limit <= bid, or a
  missing touch) is dropped before ``guards.opening.until`` (3 s), on a wall-only book and while the clock does
  not run.
Commands go to each strategy in turn until one handles them.
"""
from __future__ import annotations

import dataclasses
import traceback
from collections import deque
from typing import Any

from ctcbot.strategies.announce import Announce
from ctcbot.strategies.base import Strategy, StrategyDeps, marketable, t_of
from ctcbot.strategies.etf_leadlag import EtfLeadLag
from ctcbot.strategies.flatten import Flatten
from ctcbot.strategies.h1 import H1
from ctcbot.strategies.rein_ask import ReinAsk
from ctcbot.strategies.s1r3 import S1R3
from ctcbot.strategies.s3 import S3
from ctcbot.types import (CancelIntent, Command, Intent, RestIntent, Side, StrategyContext, TargetIntent)

STRATEGY_CLASSES: dict[str, type[Strategy]] = {"announce": Announce, "s1r3": S1R3, "flatten": Flatten,
                                               "etf_leadlag": EtfLeadLag, "h1": H1, "s3": S3, "rein_ask": ReinAsk}


class StrategyRunner:
    """Runs the strategies in precedence order and merges their intents (see the module docstring)."""

    ORDER = ("announce", "s1r3", "flatten", "etf_leadlag", "h1", "s3", "rein_ask")   # target precedence

    def __init__(self, deps: StrategyDeps) -> None:
        self.deps = deps
        self.cfg = deps.cfg
        self.log = deps.log
        self.strategies: dict[str, Strategy] = {name: STRATEGY_CLASSES[name](deps) for name in self.ORDER}
        h1, s3 = self.strategies.get("h1"), self.strategies.get("s3")
        if h1 is not None and s3 is not None:      # H1 exits by itself in a round S3 will not run (23 Oct)
            h1.s3_active = lambda r: bool(s3.enabled) and r in getattr(s3, "rounds", [1, 2, 3])  # type: ignore[attr-defined]
        self.exc_n = int(deps.cfg.get("risk.strategy_exceptions_disable", 3))
        self.exc_window = float(deps.cfg.get("risk.strategy_exceptions_window_s", 60.0))
        self._exc: dict[str, deque[float]] = {name: deque() for name in self.ORDER}
        self.exceptions: dict[str, int] = {name: 0 for name in self.ORDER}
        self.dropped: dict[str, int] = {"precedence": 0, "frozen": 0, "opening": 0}
        self.reenable_s = float(deps.cfg.get("risk.strategy_reenable_s", 30.0))
        self.reenable_max = int(deps.cfg.get("risk.strategy_reenable_max", 2))
        self._auto_off: dict[str, float] = {}             # strategy -> when it was disabled automatically
        self._auto_count: dict[str, int] = {}             # automatic re-enables this season
        g = deps.cfg.get("guards.opening", {}) or {}
        self.guard_enabled = bool(g.get("enabled", True))
        self.guard_until = g.get("until", ["open", 3.0])

    # ------------------------------------------------------------------ views for the core
    @property
    def announce(self) -> Announce:
        return self.strategies["announce"]  # type: ignore[return-value]

    def frozen(self) -> frozenset[str]:
        """Announce's frozen set (frozen plus adjusting symbols)."""
        return self.announce.frozen_set()

    def leadlag_symbols(self) -> frozenset[str]:
        """ARCH and REBLD while etf_leadlag is enabled, else empty."""
        s = self.strategies["etf_leadlag"]
        return s.leadlag_symbols() if isinstance(s, EtfLeadLag) else frozenset()

    def s1ok(self) -> frozenset[str]:
        """Symbols confirmed with ``s1ok`` (S1-r3 rests for them pass a freeze)."""
        fn = getattr(self.strategies.get("s1r3"), "confirmed", None)
        try:
            return frozenset(fn()) if callable(fn) else frozenset()
        except Exception:  # noqa: BLE001
            return frozenset()

    # ------------------------------------------------------------------ decision
    def decide(self, ctx: StrategyContext) -> list[Intent]:
        """Every enabled strategy in ORDER, isolated; then precedence, frozen and opening filtering."""
        produced: list[tuple[str, list[Intent]]] = []
        self._auto_reenable(ctx)
        for name in self.ORDER:
            strat = self.strategies[name]
            if not strat.enabled:
                continue
            if name != "announce":
                fz = self.frozen()
                if fz != ctx.frozen:
                    ctx = dataclasses.replace(ctx, frozen=fz)
            out = self._call(name, strat, ctx)
            if out:
                produced.append((name, out))
        return self._filter(produced, ctx)

    def _call(self, name: str, strat: Strategy, ctx: StrategyContext) -> list[Intent]:
        try:
            out = strat.decide(ctx)
            return [i for i in (out or []) if isinstance(i, (TargetIntent, RestIntent, CancelIntent))]
        except Exception as e:  # noqa: BLE001 - one strategy must never take the others down
            return self._exception(name, strat, ctx.now, e, ctx)

    def _auto_reenable(self, ctx: StrategyContext) -> None:
        for name, since in list(self._auto_off.items()):
            strat = self.strategies[name]
            if strat.enabled:                              # the operator switched it on meanwhile
                del self._auto_off[name]
                continue
            n = self._auto_count.get(name, 0)
            if ctx.now - since < self.reenable_s or n >= self.reenable_max:
                continue
            del self._auto_off[name]
            self._auto_count[name] = n + 1
            self._exc[name].clear()
            try:
                strat.set_enabled(True, ctx)
                self.log.alert("strategy_reenabled", f"strategy {name} re-enabled automatically after "
                               f"{self.reenable_s:g} s ({n + 1}/{self.reenable_max} this season)", strategy=name)
            except Exception:  # noqa: BLE001
                pass

    def _exception(self, name: str, strat: Strategy, now: float, e: BaseException,
                   ctx: StrategyContext | None = None) -> list[Intent]:
        self.exceptions[name] = self.exceptions.get(name, 0) + 1
        q = self._exc.setdefault(name, deque())
        q.append(now)
        while q and now - q[0] > self.exc_window:
            q.popleft()
        try:
            self.log.event("exception", strategy=name, error=f"{type(e).__name__}: {e}",
                           traceback=traceback.format_exc(limit=12))
        except Exception:  # noqa: BLE001
            pass
        out: list[Intent] = []
        if len(q) >= self.exc_n and strat.enabled:
            q.clear()
            try:          # through set_enabled, so a resting strategy cancels its orders (hardening)
                out = [i for i in (strat.set_enabled(False, ctx) or []) if isinstance(i, CancelIntent)]
            except Exception:  # noqa: BLE001
                out = []
            strat.enabled = False
            self._auto_off[name] = now
            left = self.reenable_max - self._auto_count.get(name, 0)
            try:
                self.log.alert("strategy_disabled", f"strategy {name} disabled after {self.exc_n} exceptions in "
                               f"{self.exc_window:g} s (automatic retry in {self.reenable_s:g} s, {max(0, left)} "
                               f"left this season; '{strat.command or name} on' re-enables it)", strategy=name)
            except Exception:  # noqa: BLE001
                pass
        return out

    def _filter(self, produced: list[tuple[str, list[Intent]]], ctx: StrategyContext) -> list[Intent]:
        frozen = self.frozen()
        s1ok = self.s1ok()
        owner: dict[str, str] = {}
        out: list[Intent] = []
        for name, intents in produced:
            for it in intents:
                if isinstance(it, CancelIntent):
                    out.append(it)
                    continue
                sym = it.symbol
                if sym in frozen and name != "announce":
                    passes = (name == "flatten" and isinstance(it, TargetIntent) and it.reduce_only) or \
                             (name == "s1r3" and isinstance(it, RestIntent) and sym in s1ok)
                    if not passes:
                        self.dropped["frozen"] += 1
                        continue
                if isinstance(it, TargetIntent):
                    o = owner.get(sym)
                    if o is not None and o != name:
                        self.dropped["precedence"] += 1
                        continue
                    owner[sym] = name
                elif name == "s1r3":
                    owner.setdefault(sym, name)      # S1-r3 owns its symbols through its rests (section 6.8)
                if self._opening_blocks(it, ctx):
                    self.dropped["opening"] += 1
                    continue
                out.append(it)
        return out

    def _opening_blocks(self, it: TargetIntent | RestIntent, ctx: StrategyContext) -> bool:
        """True when the opening guard drops this (marketable) intent."""
        if not self.guard_enabled:
            return False
        b = ctx.books.get(it.symbol)
        if isinstance(it, RestIntent):
            side, px = it.side, it.price
        else:
            need = it.target - ctx.exposure(it.symbol)
            if need == 0:
                return False
            side = Side.BUY if need > 0 else Side.SELL
            px = it.limit_buy if side is Side.BUY else it.limit_sell
            if px is None:
                return False            # the order manager will not send that side anyway
        if not marketable(side, px, b):
            return False
        t = t_of(ctx)
        if t is None or not ctx.clock.running:
            return True
        if b is not None and b.wall_only:
            return True
        try:
            return t < ctx.schedule.at(self.guard_until)
        except (KeyError, TypeError, ValueError):
            return True

    # ------------------------------------------------------------------ commands and lifecycle
    def on_command(self, cmd: Command, ctx: StrategyContext) -> list[Intent]:
        """Offer the command to each strategy in turn until one handles it; [] when none does."""
        for name in self.ORDER:
            strat = self.strategies[name]
            was = strat.enabled
            try:
                res = strat.on_command(cmd, ctx)
            except Exception as e:  # noqa: BLE001
                return self._filter([(name, self._exception(name, strat, ctx.now, e, ctx))], ctx)
            if res is None:
                continue
            self._auto_off.pop(name, None)             # the operator decides from now on
            if strat.enabled and not was:
                self._exc[name].clear()
            intents = [i for i in res if isinstance(i, (TargetIntent, RestIntent, CancelIntent))]
            return self._filter([(name, intents)], ctx)
        try:
            self.log.event("command_unhandled", command=cmd.name, args=list(cmd.args), source=cmd.source)
        except Exception:  # noqa: BLE001
            pass
        return []

    def on_promotion(self) -> None:
        for s in self.strategies.values():
            s.on_promotion()

    def reset_season(self) -> None:
        for name in list(self._auto_off):                 # a new season gets a fresh start (hardening)
            self.strategies[name].enabled = True
            self._exc[name].clear()
            try:
                self.log.alert("strategy_reenabled", f"strategy {name} re-enabled for the new season",
                               strategy=name)
            except Exception:  # noqa: BLE001
                pass
        self._auto_off.clear()
        self._auto_count.clear()
        for name, s in self.strategies.items():
            try:
                s.reset_season()
            except Exception as e:  # noqa: BLE001
                try:
                    self.log.event("exception", strategy=name, where="reset_season", error=f"{type(e).__name__}: {e}")
                except Exception:  # noqa: BLE001
                    pass

    def state_dict(self) -> dict:
        d: dict = {}
        for name, s in self.strategies.items():
            sd = dict(s.state_dict())
            if name in self._auto_off:
                sd["enabled"] = True             # an automatic disable is not carried over a restart
            d[name] = sd
        return d

    def load_state(self, d: dict) -> None:
        if not isinstance(d, dict):
            return
        for name, s in self.strategies.items():
            sd = d.get(name)
            if isinstance(sd, dict):
                try:
                    s.load_state(sd)
                except Exception as e:  # noqa: BLE001 - a bad saved state must not stop the bot
                    try:
                        self.log.event("exception", strategy=name, where="load_state",
                                       error=f"{type(e).__name__}: {e}")
                    except Exception:  # noqa: BLE001
                        pass

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {name: s.status() for name, s in self.strategies.items()}
        out["_runner"] = {"exceptions": dict(self.exceptions), "dropped": dict(self.dropped),
                          "frozen": sorted(self.frozen()), "leadlag": sorted(self.leadlag_symbols())}
        return out
