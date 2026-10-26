"""Risk modes, holds, pre-trade checks and breakers (DESIGN.md section 4.B, ``exec/risk.py``).

``RiskManager.check`` is the last gate before an order action gets a rate-limit ticket. It never sends
anything itself: it approves a (possibly clamped) quantity or rejects with a reason. Holds put the bot in
NO_NEW_RISK (only reducing orders pass, except for strategies a hold exempts); the kill puts it in FROZEN
(nothing passes). Breakers turn exchange results, MTM and fills into holds or disabled symbols.

Strategies never gate on the mode themselves: holds are enforced here, per order, which is what lets the
``unknown_orders`` hold exempt S1-r3.
"""
from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ctcbot.types import (ApiKind, ApiResult, ClockReading, EventLog, FeedHealth, Fill, OrderRecord, OrderState,
                          Phase, PriceQuality, Priority, RiskMode, Side, BookView)

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from ctcbot.case import CaseInfo, Schedule
    from ctcbot.config import Config

__all__ = ["RiskManager"]

_MARKET_CLOSED_RE = re.compile(r"market[\s_-]*(is[\s_-]*)?closed", re.IGNORECASE)
_NO_FILL_SANITY_STRATEGIES = frozenset({"s1r3", "foreign"})   # SNIPE rests and unexplained changes


def _scaled_limits(case: "CaseInfo", cfg: "Config") -> dict[str, int]:
    """``L_s`` per symbol via ``ctcbot.case.strategy_limits`` (imported lazily; tests patch this helper)."""
    from ctcbot.case import strategy_limits
    return dict(strategy_limits(case, cfg))


def _g(cfg: "Config", key: str, default: Any) -> Any:
    try:
        v = cfg.get(key, default)
    except (KeyError, TypeError, AttributeError):
        return default
    return default if v is None else v


@dataclass(slots=True)
class _Hold:
    name: str
    since: float
    until: float | None
    sticky: bool
    reason: str
    exempt: frozenset[str] = field(default_factory=frozenset)
    count: int = 1

    def active(self, now: float) -> bool:
        return self.until is None or now < self.until


class RiskManager:
    """Mode, holds, disabled symbols, the per-order ``check`` and the breakers of section 4.B."""

    def __init__(self, cfg: "Config", case: "CaseInfo", schedule: "Schedule", log: EventLog) -> None:
        self.cfg, self.case, self.schedule, self.log = cfg, case, schedule, log
        self._ls: dict[str, int] = _scaled_limits(case, cfg)
        self._max_clip_frac = float(_g(cfg, "risk.max_clip_frac", 0.5))
        self._max_snipe_frac = float(_g(cfg, "risk.max_snipe_frac", 2.0))
        self._fat_finger = float(_g(cfg, "risk.fat_finger_frac", 0.25))
        self._snipe_band = float(_g(cfg, "risk.snipe_band_frac", 0.60))
        self._mkt_from_mid = float(_g(cfg, "risk.max_marketable_from_mid", 0.015))
        self._max_resting = int(_g(cfg, "risk.max_resting_per_symbol", 8))
        self._storm_stale = float(_g(cfg, "feed.storm_obs_stale_s", 6.0))
        self._drawdown = float(_g(cfg, "risk.drawdown_usd", 20000.0))
        self._fs_n = int(_g(cfg, "risk.fill_sanity_n", 3))
        self._fs_mult = float(_g(cfg, "risk.fill_sanity_mult", 3.0))
        self._rej_n = int(_g(cfg, "risk.reject_disable_n", 5))
        self._rej_window = float(_g(cfg, "risk.reject_disable_window_s", 30.0))
        self._err_n = int(_g(cfg, "risk.http_5xx_streak", 3))
        self._err_hold_s = float(_g(cfg, "risk.http_5xx_hold_s", 10.0))
        self._throttle_s = float(_g(cfg, "rate.throttle_freeze_s", 5.0))
        try:
            self._block_before = float(schedule.at(_g(cfg, "risk.block_marketable_before", ["open", 3.0])))
        except Exception:  # noqa: BLE001 - a bad schedule must not stop the bot from refusing orders
            self._block_before = 3.0
        self._now: float = -math.inf
        self._killed = False
        self._kill_reason = ""
        self._holds: dict[str, _Hold] = {}
        self._disabled: dict[str, str] = {}
        self._err_streak = 0
        self._rejects: dict[str, deque[float]] = {}
        self._bad_fills: dict[str, int] = {}
        self._fs_judged: dict[str, None] = {}      # local_ids whose clip the fill-sanity run already judged
        self._round: int | None = None
        self._peak: float | None = None
        self._last_mtm: float | None = None
        self.counters: dict[str, int] = {"checks": 0, "rejects": 0, "auth": 0, "throttled": 0, "api_errors": 0}

    # ------------------------------------------------------------------------------------------ time
    def _touch(self, now: float) -> None:
        if now > self._now:
            self._now = now

    @property
    def scaled_limits(self) -> dict[str, int]:
        """``L_s`` per symbol (read-only copy)."""
        return dict(self._ls)

    # ------------------------------------------------------------------------------------------ mode
    @property
    def mode(self) -> RiskMode:
        """FROZEN if killed; NO_NEW_RISK if any hold is active (at the latest time seen); else NORMAL."""
        if self._killed:
            return RiskMode.FROZEN
        if any(h.active(self._now) for h in self._holds.values()):
            return RiskMode.NO_NEW_RISK
        return RiskMode.NORMAL

    def hold(self, name: str, now: float, until: float | None = None, sticky: bool = False, reason: str = "",
             exempt: frozenset[str] = frozenset()) -> None:
        """Set or extend a hold. ``until=None`` lasts until ``release``; ``exempt`` names unblocked strategies."""
        self._touch(now)
        h = self._holds.get(name)
        exempt = frozenset(exempt or ())
        if h is not None and h.active(now):
            h.until = None if (h.until is None or until is None) else max(h.until, until)
            h.sticky = h.sticky or sticky
            h.exempt = exempt
            h.count += 1
            if reason:
                h.reason = reason
            return
        self._holds[name] = _Hold(name=name, since=now, until=until, sticky=sticky, reason=reason, exempt=exempt)
        self.log.event("hold", name=name, until=until, sticky=sticky, reason=reason, exempt=sorted(exempt))

    def release(self, name: str, now: float) -> None:
        """Remove a hold (no-op if absent). Releasing ``drawdown`` restarts the round peak at the last MTM."""
        self._touch(now)
        h = self._holds.pop(name, None)
        if h is None:
            return
        if name == "drawdown":
            self._peak = self._last_mtm
        self.log.event("release", name=name, held_s=round(now - h.since, 3))

    def holds(self, now: float) -> dict[str, dict]:
        """Active holds: name -> {since, until, sticky, reason, exempt}. Expired holds are dropped."""
        self._touch(now)
        for name in [n for n, h in self._holds.items() if not h.active(now)]:
            h = self._holds.pop(name)
            self.log.event("hold_expired", name=name, held_s=round(now - h.since, 3))
        return {n: {"since": h.since, "until": h.until, "sticky": h.sticky, "reason": h.reason,
                    "exempt": sorted(h.exempt)} for n, h in self._holds.items()}

    def _active_holds(self, now: float) -> list[_Hold]:
        return [h for h in self._holds.values() if h.active(now)]

    def _hold_active(self, name: str, now: float) -> bool:
        h = self._holds.get(name)
        return h is not None and h.active(now)

    def kill(self, reason: str, now: float) -> None:
        self._touch(now)
        if not self._killed:
            self.log.alert("kill", f"KILL: {reason}", reason=reason)
        self._killed, self._kill_reason = True, reason

    def killed(self) -> bool:
        return self._killed

    def unkill(self, now: float) -> None:
        self._touch(now)
        if self._killed:
            self.log.alert("unkill", "kill released by the operator", reason=self._kill_reason)
        self._killed, self._kill_reason = False, ""

    # ------------------------------------------------------------------------------------------ symbols
    def disabled(self) -> frozenset[str]:
        return frozenset(self._disabled)

    def disable(self, sym: str, reason: str, now: float) -> None:
        self._touch(now)
        if sym not in self._disabled:
            self.log.alert("symbol_disabled", f"{sym} disabled: {reason}", symbol=sym, reason=reason)
        self._disabled[sym] = reason

    def enable(self, sym: str) -> None:
        if self._disabled.pop(sym, None) is not None:
            self.log.event("symbol_enabled", symbol=sym)
        self._bad_fills.pop(sym, None)
        self._rejects.pop(sym, None)

    # ------------------------------------------------------------------------------------------ check
    @staticmethod
    def _reducing_qty(side: Side, qty: int, position: int) -> int:
        """Shares of this order that reduce |position| without crossing zero (0 if it does not reduce)."""
        if position == 0 or side.sign * position > 0:
            return 0
        return min(qty, abs(int(position)))

    def check(self, rec: OrderRecord, book: BookView, position: int, room: int, clock: ClockReading,
              feed: FeedHealth, now: float, live: Sequence[OrderRecord] | None = None) -> tuple[int, str]:
        """Approved quantity (0 = reject) and reason, applying the twelve rules of section 4.B in order.

        ``live`` (an extension used by the OrderManager) is the symbol's live records, needed for rules 11
        and 12; when it is None those two rules are skipped.
        """
        self._touch(now)
        self.counters["checks"] += 1
        qty, why = self._check(rec, book, int(position), int(room), clock, feed, now, live)
        if qty <= 0:
            self.counters["rejects"] += 1
        return qty, why

    def _check(self, rec: OrderRecord, book: BookView, position: int, room: int, clock: ClockReading,
               feed: FeedHealth, now: float, live: Sequence[OrderRecord] | None) -> tuple[int, str]:
        # 1. frozen
        if self._killed:
            return 0, "frozen"
        qty = int(rec.qty)
        red = self._reducing_qty(rec.side, qty, position)
        # 2. holds
        blocking = [h.name for h in self._active_holds(now) if rec.strategy not in h.exempt]
        if blocking:
            if red <= 0:
                return 0, f"hold:{blocking[0]}"
            qty = min(qty, red)
        # 3. disabled symbol
        if rec.symbol in self._disabled:
            if red <= 0:
                return 0, "disabled"
            qty = min(qty, red)
        # 4. market open
        if clock is None or not clock.running or clock.phase in (Phase.CLOSED, Phase.UNKNOWN):
            return 0, "market_closed"
        # 5. size
        if qty < 1:
            return 0, "qty"
        frac = self._max_snipe_frac if rec.priority == Priority.SNIPE else self._max_clip_frac
        cap = int(math.floor(frac * self._ls.get(rec.symbol, 0) + 1e-9))
        if cap < 1:
            return 0, "size_cap"
        qty = min(qty, cap)
        # 6. case room
        if room < 1:
            return 0, "no_room"
        qty = min(qty, room)
        # 7. mid
        mid = book.mid if book is not None else None
        if mid is None or not math.isfinite(mid) or mid <= 0:
            return 0, "no_mid"
        px = float(rec.price)
        if not math.isfinite(px) or px <= 0:
            return 0, "bad_price"
        # 8. marketability and price bands
        if rec.side == Side.BUY:
            marketable = book.ask is None or px >= book.ask
        else:
            marketable = book.bid is None or px <= book.bid
        dev = abs(px - mid)
        # the from-mid cap holds for every TAKE/HEDGE order on the aggressive side of mid, not only for orders
        # marketable against our (possibly wide or stale) view of the touch (hardening, DESIGN.md §13)
        aggressive = px > mid if rec.side == Side.BUY else px < mid
        take_hedge = rec.priority in (Priority.TAKE, Priority.HEDGE)
        if take_hedge and (marketable or aggressive) and dev > self._mkt_from_mid * mid:
            return 0, "marketable_far"
        band = self._snipe_band if rec.priority == Priority.SNIPE else self._fat_finger
        if dev > band * mid:
            return 0, "fat_finger"
        # 9. opening and wall-only books
        if marketable:
            if clock.t is None or clock.t < self._block_before:
                return 0, "opening"
            if book.wall_only:
                return 0, "wall_only"
        # 10. stale storm observations
        if clock.phase == Phase.STORM:
            age = feed.last_storm_obs_age_s if feed is not None else None
            if age is None or age > self._storm_stale:
                red_now = self._reducing_qty(rec.side, qty, position)
                if red_now <= 0:
                    return 0, "storm_obs_stale"
                qty = min(qty, red_now)
        # 10b. no frame on any connection within the watchdog: the touch and the mid may be stale, so a
        # TAKE/HEDGE order that could trade must only reduce (the feed-down hold follows at 10 s)
        if take_hedge and (marketable or aggressive) and feed is not None and not feed.up:
            red_now = self._reducing_qty(rec.side, qty, position)
            if red_now <= 0:
                return 0, "feed_stale"
            qty = min(qty, red_now)
        if live is not None:
            others = [r for r in live if r.local_id != rec.local_id and r.state != OrderState.CANCEL_ACKED
                      and r.symbol == rec.symbol]
            # 11. resting orders per symbol
            if len(others) >= self._max_resting:
                return 0, "max_resting"
            # 12. self-cross
            for r in others:
                if r.side == rec.side or not math.isfinite(r.price):
                    continue
                if (rec.side == Side.BUY and px >= r.price) or (rec.side == Side.SELL and px <= r.price):
                    return 0, "self_cross"
        return qty, "ok"

    # ------------------------------------------------------------------------------------------ breakers
    def on_result(self, res: ApiResult, now: float) -> None:
        """Breakers fed by exchange results: auth, 429, 5xx/transport streaks and per-symbol rejects."""
        self._touch(now)
        if res.dry_run:
            return
        st = res.status
        if not res.sent and st is None and (res.error or "") in ("purged", "frozen", "dry_run", "stale_queue",
                                                                 "closed"):
            return
        if st in (401, 403):
            self.counters["auth"] += 1
            if not self._hold_active("auth", now):
                self.log.alert("auth_failed", f"HTTP {st}: check the key; hold until the operator types r",
                               status=st, api_kind=str(res.req.kind))
            self.hold("auth", now, sticky=True, reason=f"http {st}")
            self._err_streak = 0
            return
        if st == 429:
            self.counters["throttled"] += 1
            self.hold("http_429", now, until=now + self._throttle_s, reason="http 429")
            return
        if st is None or st >= 500:
            self._err_streak += 1
            if self._err_streak >= self._err_n:
                self.counters["api_errors"] += 1
                if not self._hold_active("api_errors", now):
                    self.log.alert("api_errors", f"{self._err_streak} 5xx/transport errors in a row",
                                   streak=self._err_streak, status=st, error=res.error)
                self.hold("api_errors", now, until=now + self._err_hold_s, reason=f"streak {self._err_streak}")
            return
        self._err_streak = 0
        if 400 <= st < 500 and res.req.kind == ApiKind.PLACE and res.req.symbol:
            if self._market_closed_text(res):
                return
            sym = res.req.symbol
            dq = self._rejects.setdefault(sym, deque())
            dq.append(now)
            while dq and dq[0] < now - self._rej_window:
                dq.popleft()
            if len(dq) >= self._rej_n:
                self.disable(sym, f"{len(dq)} rejects in {self._rej_window:g}s", now)
                dq.clear()

    @staticmethod
    def _market_closed_text(res: ApiResult) -> bool:
        parts = [res.error or ""]
        try:
            parts.append(str(res.data)[:500])
        except Exception:  # noqa: BLE001 - str() of untrusted data
            pass
        return any(_MARKET_CLOSED_RE.search(p) for p in parts)

    def on_mtm(self, mtm: float, round_: int | None, now: float) -> None:
        """Track the round's peak MTM and hold ``drawdown`` (sticky) beyond ``risk.drawdown_usd``."""
        self._touch(now)
        if mtm is None or not math.isfinite(mtm):
            return
        if round_ is not None and round_ != self._round:
            self._round, self._peak = round_, mtm
        self._last_mtm = mtm
        if self._peak is None or mtm > self._peak:
            self._peak = mtm
        dd = self._peak - mtm
        if dd > self._drawdown and not self._hold_active("drawdown", now):
            self.log.alert("drawdown", f"drawdown {dd:,.0f} > {self._drawdown:,.0f} in round {self._round}",
                           peak=self._peak, mtm=mtm, round=self._round)
            self.hold("drawdown", now, sticky=True, reason=f"dd {dd:.0f}")

    def on_fill(self, f: Fill, now: float) -> None:
        """Fill sanity: N consecutive TAKE/HEDGE clips slipping beyond mult x expected cost disable the symbol.

        The run counts clips (playbook: "3 consecutive clips"), not fills: only the first qualifying fill of
        an order is judged, so one clip that comes back as an ack fill plus reconcile fills counts once.
        """
        self._touch(now)
        if f.strategy in _NO_FILL_SANITY_STRATEGIES or f.source == "foreign":
            return
        if f.quality == PriceQuality.LIMIT_BOUND or f.ref_mid is None or f.expected_cost_bp is None:
            return
        if not math.isfinite(f.ref_mid) or f.ref_mid <= 0 or not math.isfinite(f.price):
            return
        if f.local_id is not None:
            if f.local_id in self._fs_judged:
                return
            self._fs_judged[f.local_id] = None
            while len(self._fs_judged) > 4096:
                del self._fs_judged[next(iter(self._fs_judged))]
        slip_bp = f.side.sign * (f.price - f.ref_mid) / f.ref_mid * 1e4
        if slip_bp > self._fs_mult * float(f.expected_cost_bp):
            n = self._bad_fills.get(f.symbol, 0) + 1
            self._bad_fills[f.symbol] = n
            self.log.event("fill_sanity_miss", symbol=f.symbol, slip_bp=round(slip_bp, 2),
                           expected_bp=f.expected_cost_bp, n=n)
            if n >= self._fs_n:
                self.disable(f.symbol, f"fill sanity: {n} clips over {self._fs_mult:g}x expected cost", now)
                self._bad_fills[f.symbol] = 0
        else:
            self._bad_fills[f.symbol] = 0

    def on_round_start(self, round_: int, mtm: float, now: float) -> None:
        """A new round (or season): restart the drawdown peak at the current MTM."""
        self._touch(now)
        self._round = round_
        self._peak = mtm if mtm is not None and math.isfinite(mtm) else None
        self._last_mtm = self._peak
        self.log.event("risk_round_start", round=round_, mtm=mtm)

    # ------------------------------------------------------------------------------------------ persist
    def state_dict(self) -> dict:
        """Holds without an expiry (timed holds are short and not carried over), disabled symbols, kill."""
        holds = {n: {"sticky": h.sticky, "reason": h.reason, "exempt": sorted(h.exempt)}
                 for n, h in self._holds.items() if h.until is None}
        return {"holds": holds, "disabled": dict(self._disabled), "killed": self._killed,
                "kill_reason": self._kill_reason}

    def load_state(self, d: Mapping[str, Any]) -> None:
        if not isinstance(d, Mapping):
            return
        now = self._now if math.isfinite(self._now) else 0.0
        for name, h in (d.get("holds") or {}).items():
            if not isinstance(h, Mapping):
                continue
            self._holds[str(name)] = _Hold(name=str(name), since=now, until=None, sticky=bool(h.get("sticky")),
                                           reason=str(h.get("reason", "")),
                                           exempt=frozenset(str(x) for x in (h.get("exempt") or ())))
        for sym, why in (d.get("disabled") or {}).items():
            self._disabled[str(sym)] = str(why)
        if d.get("killed"):
            self._killed = True
            self._kill_reason = str(d.get("kill_reason", "restored"))
