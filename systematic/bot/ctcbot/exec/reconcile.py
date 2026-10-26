"""Reconciliation of order records and positions against the exchange (DESIGN.md section 4.B).

The exchange documents no private fill channel, so ``GET /orders/open`` plus ``GET /positions`` every
0.5 s is the source of truth. One cycle is one pair of GETs with the same ``cycle`` number; when both are
back and OK, open orders are applied first (match by id, adoption of lost acks, foreign orders), then the
positions (through the quiet-symbol guard), then the position change is attributed to fills.

Room safety drives every rule. The exchange counts ``position + same-side resting`` against the limit,
and a fill leaves that sum unchanged on its own side (it moves shares from resting to position), so the
bot's view stays conservative as long as it never lowers a record's remaining without also having the
fill in the position it uses:

* **Remaining drops are applied one cycle late** on symbols whose row is applied. The two GETs of a cycle
  may be answered in either order, so a lower remaining seen now may not be in this cycle's position row
  yet; it is certainly in the next cycle's row, which is requested after this cycle completed. On a
  skipped symbol (quiet-symbol guard) the internal position is not replaced by a row, so the drop moves
  cash and position at once.
* **Absent orders** count only when the GET was sent after the record's last acknowledgement; an order
  whose disappearance the position change does not explain is kept for a second cycle.
* **Unexplained change** is held for a cycle (the *carry*) before it is booked as a foreign fill.

Hardening (DESIGN.md §13): the cycle timeout is ``exchange.timeout_get_s * (1 + get_retries) + 0.5`` (a 2-3 s GET
is a slow answer, not a lost one). When one cycle shows ``_JUMP_SYMBOLS`` or more symbols changing with no live
record of ours in them (a new season seen through ``/positions`` before its ``competition_id``, or a manual
flatten in the browser), foreign fills are held for ``_JUMP_HOLD_S`` and ``season_hint`` is set, so the core can
ask ``GET /competitions/time`` at once; positions are applied as usual (room is unaffected).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from ctcbot.types import (ApiKind, ApiResult, EventLog, Fill, OpenOrderRow, OrderRecord, OrderState, PositionRow,
                          PriceQuality, Priority, Side)

from ctcbot.state.limits import live_contribution

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from ctcbot.config import Config
    from ctcbot.state.orders import OrderStore
    from ctcbot.state.positions import PositionBook

__all__ = ["Reconciler"]

CYCLE_TIMEOUT_S = 2.0       # used only without a config (unit fakes); see Reconciler.cycle_timeout_s
_JUMP_SYMBOLS = 4           # symbols changing at once with no live record of ours: maybe a season boundary
_JUMP_HOLD_S = 3.0          # foreign fills wait this long after such a jump
# While an order rests on the same side, this cycle's change may be its fill between the two GETs (seen as
# a lower remaining only next cycle), so absent records take only the carry until this many absent cycles.
_AMBIGUOUS_MISSING_MAX = 3
_RECENT_CANCEL_S = 15.0     # how long before a carry appeared a record resolved CANCELED may take it as a late fill
_RECENT_KEEP_S = 60.0       # how long such records are remembered at all
_PRICE_TOL = 1e-6
S = OrderState
_ABSENT_STATES = frozenset({S.OPEN, S.CANCEL_PENDING, S.CANCEL_ACKED, S.CANCEL_UNKNOWN, S.UNKNOWN})
_MATCHABLE = frozenset({S.OPEN, S.CANCEL_PENDING, S.CANCEL_ACKED, S.CANCEL_UNKNOWN, S.UNKNOWN, S.SENDING})


def _g(cfg: "Config", key: str, default: Any) -> Any:
    try:
        v = cfg.get(key, default)
    except (KeyError, TypeError, AttributeError):
        return default
    return default if v is None else v


def _sgn(x: float) -> int:
    return (x > 0) - (x < 0)


def _nonempty_payload(data: Any) -> bool:
    """True when a raw body evidently holds items (so an empty parse means an unknown shape)."""
    if isinstance(data, list):
        return len(data) > 0
    if isinstance(data, dict):
        if any(isinstance(v, list) and v for v in data.values()):
            return True
        return bool(data) and all(isinstance(v, dict) for v in data.values())
    return False


def _send_key(r: OrderRecord) -> tuple[float, float, str]:
    return (r.sent_mono if r.sent_mono is not None else r.created_mono, r.created_mono, r.local_id)


@dataclass(slots=True)
class _Cycle:
    cid: int
    started: float
    stamp: dict[str, tuple[int, int]]
    open_res: ApiResult | None = None
    pos_res: ApiResult | None = None


@dataclass(slots=True)
class _Pending:
    qty: int                    # signed position change that no record has explained yet
    price: float | None         # best price guess from the cycle that saw it
    since: float


class Reconciler:
    """Pairs GET_OPEN and GET_POSITIONS results into cycles and updates records, positions and fills."""

    def __init__(self, store: "OrderStore", positions: "PositionBook", cfg: "Config", log: EventLog) -> None:
        self.store, self.positions, self.cfg, self.log = store, positions, cfg, log
        self._grace = float(_g(cfg, "risk.unknown_grace_s", 1.0))
        try:
            tg = cfg.get("exchange.timeout_get_s", None)
            nr = cfg.get("exchange.get_retries", None)
        except (KeyError, TypeError, AttributeError):
            tg = nr = None
        self.cycle_timeout_s = (float(tg) * (1 + int(nr or 0)) + 0.5) if isinstance(tg, (int, float)) and \
            not isinstance(tg, bool) else CYCLE_TIMEOUT_S
        self.season_hint: float | None = None
        self._foreign_hold_until = -math.inf
        self._skip_alert_s = float(_g(cfg, "risk.pos_skip_alert_s", 5.0))
        self._n = 0
        self._cycle: _Cycle | None = None
        self._fails = 0
        self._last_rows: list[OpenOrderRow] | None = None
        self._last_cycle_sent: float | None = None
        self._pending: dict[str, _Pending] = {}
        self._obs: dict[str, int] = {}          # local_id -> lowest remaining seen in a row, not yet applied
        self._resv: dict[str, int] = {}         # local_id -> part of that drop already counted in a row delta
        self._resv_px: dict[str, float] = {}    # local_id -> fill price inferred when the reservation was made
        self._stale_alerted: set[str] = set()
        self._foreign_seen: set[str] = set()
        self._acked_open_logged: set[str] = set()
        # records resolved CANCELED with an unexplained remaining: symbol -> [[local_id, side, qty, mono]]
        self._recent_cancels: dict[str, list[list]] = {}
        self.counters: dict[str, int] = {"cycles": 0, "failed": 0, "adopted": 0, "foreign_orders": 0,
                                         "foreign_fills": 0, "fills": 0}

    # ------------------------------------------------------------------------------------------ cycle
    def begin_cycle(self, now: float) -> int:
        """Start a cycle (taking ``positions.quiet_stamp()``) and return its id for both GETs."""
        prev = self._cycle
        if prev is not None:
            if now - prev.started >= self.cycle_timeout_s:
                self._fail(prev, now, "timeout")
            else:
                self.log.event("reconcile_superseded", cycle=prev.cid)
        self._n += 1
        self._cycle = _Cycle(cid=self._n, started=now, stamp=self.positions.quiet_stamp())
        return self._n

    def outstanding(self, now: float) -> bool:
        """True while a cycle is open and younger than the cycle timeout (internal helper)."""
        return self._cycle is not None and now - self._cycle.started < self.cycle_timeout_s

    def _fail(self, cyc: _Cycle, now: float, why: str) -> None:
        self._fails += 1
        self.counters["failed"] += 1
        self.log.event("reconcile_fail", cycle=cyc.cid, reason=why, consecutive=self._fails)
        if self._cycle is cyc:
            self._cycle = None

    def consecutive_failures(self) -> int:
        return self._fails

    def last_open_rows(self) -> list[OpenOrderRow] | None:
        return None if self._last_rows is None else list(self._last_rows)

    def last_cycle_sent_mono(self) -> float | None:
        """``sent_mono`` of the last completed cycle's GET_OPEN (internal to group B)."""
        return self._last_cycle_sent

    def unknown_age(self, now: float) -> float | None:
        """Age of the oldest UNKNOWN (since its send) or CANCEL_UNKNOWN (since its cancel) record."""
        ages: list[float] = []
        for r in self.store.live():
            if r.dry:
                continue
            if r.state == S.UNKNOWN:
                ages.append(now - (r.sent_mono if r.sent_mono is not None else r.created_mono))
            elif r.state == S.CANCEL_UNKNOWN:
                ref = r.cancel_sent_mono if r.cancel_sent_mono is not None else (
                    r.sent_mono if r.sent_mono is not None else r.created_mono)
                ages.append(now - ref)
        return max(ages) if ages else None

    def on_result(self, res: ApiResult, now: float) -> list[Fill] | None:
        """Take one GET result; return the cycle's fills once both halves are in (None until then)."""
        cyc = self._cycle
        if cyc is None or res.req.cycle != cyc.cid or res.req.kind not in (ApiKind.GET_OPEN, ApiKind.GET_POSITIONS):
            return None
        if now - cyc.started > self.cycle_timeout_s:
            self._fail(cyc, now, "timeout")
            return None
        if not res.ok:
            self._fail(cyc, now, f"{res.req.kind} status={res.status} err={(res.error or '')[:120]}")
            return None
        if res.req.kind == ApiKind.GET_OPEN:
            if not isinstance(res.parsed, list) or (not res.parsed and _nonempty_payload(res.data)):
                self._fail(cyc, now, "open orders: unparsed body")
                return None
            cyc.open_res = res
        else:
            if not isinstance(res.parsed, Mapping) or (not res.parsed and _nonempty_payload(res.data)):
                self._fail(cyc, now, "positions: unparsed body")
                return None
            cyc.pos_res = res
        if cyc.open_res is None or cyc.pos_res is None:
            return None
        self._cycle = None
        try:
            fills = self._process(cyc, now)
        except Exception as e:  # noqa: BLE001 - a bad body must not take the loop down; skip the cycle
            self._fails += 1
            self.counters["failed"] += 1
            self.log.event("exception", where="reconcile", error=f"{type(e).__name__}: {e}")
            return None
        self._fails = 0
        self.counters["cycles"] += 1
        self.store.prune(now)
        return fills

    # ------------------------------------------------------------------------------------------ process
    def _observe(self, rec: OrderRecord, remaining: int) -> None:
        rem = max(0, int(remaining))
        cur = live_contribution(rec)
        if rem > cur:
            self.log.event("reconcile_remaining_up", local_id=rec.local_id, prev=cur, row=rem)
            rem = cur
        prev = self._obs.get(rec.local_id)
        self._obs[rec.local_id] = rem if prev is None else min(prev, rem)

    def _process(self, cyc: _Cycle, now: float) -> list[Fill]:
        assert cyc.open_res is not None and cyc.pos_res is not None
        t_get = cyc.open_res.sent_mono
        seen_at = max(cyc.open_res.sent_mono, cyc.open_res.done_mono)
        rows_open = [r for r in cyc.open_res.parsed
                     if isinstance(r, OpenOrderRow) and r.order_id and isinstance(r.symbol, str)
                     and isinstance(r.side, Side)]
        pos_rows: dict[str, PositionRow] = {k: v for k, v in cyc.pos_res.parsed.items() if isinstance(v, PositionRow)}
        store = self.store
        live = [r for r in store.live() if not r.dry]
        prev_obs = dict(self._obs)          # seen in earlier cycles: certainly inside this cycle's rows

        # ---- 1. open orders: match by id
        matched: set[str] = set()
        unclaimed: list[OpenOrderRow] = []
        for row in rows_open:
            rec = store.by_order_id(row.order_id)
            if rec is None or rec.dry:
                unclaimed.append(row)
                continue
            if rec.state not in _MATCHABLE:
                self.log.alert("terminal_order_open", f"{rec.local_id} is {rec.state} but {row.order_id} is open",
                               local_id=rec.local_id, order_id=row.order_id, state=str(rec.state))
                unclaimed.append(row)
                continue
            matched.add(rec.local_id)
            self._apply_match(rec, row, t_get, seen_at, now)

        # ---- 2. adoption of records whose ack is lost or still in flight
        cands = sorted((r for r in live if r.state in (S.UNKNOWN, S.SENDING) and not r.order_id
                        and r.local_id not in matched), key=_send_key)
        foreign_rows: list[OpenOrderRow] = []
        for row in unclaimed:
            rec = None
            if row.price is not None and math.isfinite(row.price):
                for c in cands:
                    if (c.symbol == row.symbol and c.side == row.side and abs(c.price - row.price) <= _PRICE_TOL
                            and 0 <= int(row.remaining) <= c.qty - c.filled_qty):
                        rec = c
                        break
            if rec is None:
                foreign_rows.append(row)
                continue
            cands.remove(rec)
            matched.add(rec.local_id)
            store.set_order_id(rec, row.order_id)
            store.transition(rec, S.OPEN, now, remaining=rec.qty - rec.filled_qty, last_seen_open_mono=seen_at,
                             missing_cycles=0)
            self._observe(rec, int(row.remaining))
            self.counters["adopted"] += 1
            self.log.event("order_adopted", local_id=rec.local_id, order_id=row.order_id, remaining=int(row.remaining))

        # ---- 3. open rows nobody owns become foreign records (counted in room; the kill cancels them)
        for row in foreign_rows:
            rem = int(row.remaining) if row.remaining is not None else 0
            if rem <= 0:
                continue
            qty = max(rem, int(row.qty) if row.qty is not None else 0)
            px = float(row.price) if row.price is not None and math.isfinite(row.price) else math.nan
            rec = store.new(strategy="foreign", key=None, style="foreign", symbol=row.symbol, side=row.side,
                            qty=qty, price=px, priority=Priority.HEDGE, state=S.OPEN, order_id=row.order_id,
                            remaining=rem, filled_qty=qty - rem, last_seen_open_mono=seen_at, created_mono=now,
                            reason="unknown open order")
            matched.add(rec.local_id)
            self.counters["foreign_orders"] += 1
            if row.order_id not in self._foreign_seen:
                self._foreign_seen.add(row.order_id)
                self.log.alert("foreign_order", f"open order {row.order_id} {row.side} {rem} {row.symbol} "
                               f"@ {row.price} is not ours (another instance or the browser?)",
                               order_id=row.order_id, symbol=row.symbol, side=str(row.side), remaining=rem,
                               price=row.price)

        # ---- 4. absences (only GETs sent after the record's last acknowledgement count)
        absent: dict[str, list[OrderRecord]] = {}
        for rec in live:
            if rec.local_id in matched or rec.state not in _ABSENT_STATES:
                continue
            if rec.state == S.UNKNOWN:
                ref = (rec.sent_mono if rec.sent_mono is not None else rec.created_mono) + self._grace
            else:
                ref = next((x for x in (rec.acked_mono, rec.last_seen_open_mono, rec.sent_mono) if x is not None),
                           rec.created_mono)
            if t_get > ref:
                absent.setdefault(rec.symbol, []).append(rec)

        # ---- 5. positions through the quiet-symbol guard
        old_rows = self.positions.last_rows()
        pre_pos = dict(self.positions.pos)
        deltas, skipped = self.positions.set_exchange(pos_rows, cyc.stamp, now)
        self._stale_alerts(now, skipped)
        jump = sorted(s for s, d in deltas.items() if d and not any(not r.dry for r in store.live(s)))
        if len(jump) >= _JUMP_SYMBOLS and now >= self._foreign_hold_until:
            self._foreign_hold_until = now + _JUMP_HOLD_S
            self.season_hint = now
            self.log.event("positions_jump", symbols=jump, deltas={s: deltas[s] for s in jump},
                           hold_s=_JUMP_HOLD_S)

        # ---- 6. fills, attribution and resolution per symbol
        by_sym: dict[str, list[OrderRecord]] = {}
        for lid in matched:
            r = store.by_local(lid)
            if r is not None:
                by_sym.setdefault(r.symbol, []).append(r)
        fills: list[Fill] = []
        syms = set(by_sym) | set(absent) | set(self._pending) | {s for s, d in deltas.items() if d}
        syms |= {r.symbol for r in live if r.local_id in self._obs or r.local_id in self._resv}
        for s in sorted(syms):
            s_matched = sorted(by_sym.get(s, []), key=_send_key)
            s_seen = sorted((r for r in store.live(s) if not r.dry and (r.local_id in self._obs
                                                                        or r.local_id in self._resv)), key=_send_key)
            if s in skipped or s not in deltas:
                fills.extend(self._skipped(s, s_seen, now))
                continue
            certain: list[tuple[OrderRecord, int, int]] = []
            for r in s_seen:
                o = prev_obs.get(r.local_id)
                q = live_contribution(r) - o if o is not None and o < live_contribution(r) else 0
                rv = self._resv.get(r.local_id, 0)
                if q or rv:
                    certain.append((r, q, rv))
            fills.extend(self._attribute(s, deltas[s], certain, absent.get(s, []), s_matched, old_rows, pos_rows,
                                         pre_pos.get(s, 0), now))
        for lid in [k for k in self._obs
                    if (r := store.by_local(k)) is None or r.state not in _MATCHABLE or self._obs[k] >= live_contribution(r)]:
            del self._obs[lid]
        for lid in [k for k in self._resv if (r := store.by_local(k)) is None or r.state not in _MATCHABLE]:
            del self._resv[lid]
        for lid in [k for k in self._resv_px if k not in self._resv]:
            del self._resv_px[lid]
        self._last_rows = rows_open
        self._last_cycle_sent = t_get
        return fills

    def _apply_match(self, rec: OrderRecord, row: OpenOrderRow, t_get: float, seen_at: float, now: float) -> None:
        state = rec.state
        updates: dict[str, Any] = {"last_seen_open_mono": seen_at, "missing_cycles": 0}
        if state in (S.UNKNOWN, S.SENDING):
            state = S.OPEN
            updates["remaining"] = rec.qty - rec.filled_qty
        elif state == S.CANCEL_UNKNOWN:
            ref = rec.cancel_sent_mono if rec.cancel_sent_mono is not None else -math.inf
            if t_get > ref + self._grace:
                state = S.OPEN
                self.log.event("cancel_not_taken", local_id=rec.local_id, order_id=rec.order_id)
        elif state == S.CANCEL_ACKED and rec.acked_mono is not None and t_get > rec.acked_mono + self._grace:
            if rec.local_id not in self._acked_open_logged:
                self._acked_open_logged.add(rec.local_id)
                self.log.event("cancel_acked_still_open", local_id=rec.local_id, order_id=rec.order_id)
        self.store.transition(rec, state, now, **updates)
        self._observe(rec, int(row.remaining))

    def _finish(self, rec: OrderRecord, now: float) -> None:
        """A record with nothing left after its fills is filled."""
        if rec.state in (S.OPEN, S.CANCEL_PENDING, S.CANCEL_ACKED, S.CANCEL_UNKNOWN) and rec.remaining <= 0:
            self.store.transition(rec, S.FILLED, now)
            self._obs.pop(rec.local_id, None)

    def _apply_drop(self, rec: OrderRecord, q: int, px: float | None, now: float) -> Fill:
        """Book a fill of ``q`` from a lower remaining (cash only; the caller moves the position if needed)."""
        before = live_contribution(rec)
        f = self._book_fill(rec, q, px, now, update_pos=False)
        if rec.state not in (S.UNKNOWN, S.SENDING, S.NEW):
            rec.remaining = max(0, before - q)
        return f

    def _skipped(self, s: str, s_seen: list[OrderRecord], now: float) -> list[Fill]:
        """A symbol whose row was not applied: nothing is booked. A lower remaining seen in its open rows
        stays recorded (``_obs``) and is applied at the next applied cycle, when the row certainly holds the
        fill; until then the record keeps counting its old remaining, which keeps position plus resting
        quantity conservative on both sides (a fill moves shares between the two, and acks keep moving the
        internal position)."""
        return []

    def _late_attribute(self, s: str, signed_qty: int, now: float, since: float | None = None
                        ) -> tuple[int, list[Fill]]:
        """Give a carry about to become foreign to records of its side that were recently resolved CANCELED
        with an unexplained remaining (fills in both directions inside one window hide a fill in the net
        position change). "Recently" is measured from when the carry first appeared (``since``), because a
        carry may wait several cycles for live records that could still explain it. Returns (signed
        quantity attributed, fills)."""
        ref = now if since is None else min(now, since)
        keep = [e for e in self._recent_cancels.get(s, ()) if now - e[3] <= _RECENT_KEEP_S and e[2] > 0]
        lst = [e for e in keep if ref - e[3] <= _RECENT_CANCEL_S]
        side = Side.BUY if signed_qty > 0 else Side.SELL
        left = abs(signed_qty)
        out: list[Fill] = []
        for e in lst:
            if left <= 0:
                break
            if e[1] != side:
                continue
            rec = self.store.by_local(e[0])
            if rec is None:
                continue
            take = min(left, e[2])
            out.append(self._book_fill(rec, take, None, now, update_pos=False))
            e[2] -= take
            left -= take
            self.log.event("reconcile_late_fill", local_id=rec.local_id, symbol=s, qty=take)
        self._recent_cancels[s] = [e for e in keep if e[2] > 0]
        return side.sign * (abs(signed_qty) - left), out

    def _may_explain_later(self, s: str, side: Side, deferred: set[str], matched: set[str]) -> bool:
        """True while a live record on ``side`` could still explain a carry: one this cycle did not see
        resting (its outcome is not known yet: SENDING, UNKNOWN inside its grace, absent but not yet counted,
        or deferred), or one with a lower remaining that was seen but is not applied yet."""
        for r in self.store.live(s):
            if r.dry or r.side != side:
                continue
            if r.state in (S.UNKNOWN, S.SENDING, S.NEW) or r.local_id in deferred or r.local_id not in matched:
                return True
            o = self._obs.get(r.local_id)
            if o is not None and live_contribution(r) - o > self._resv.get(r.local_id, 0):
                return True
        return False

    def _attribute(self, s: str, d: int, certain: list[tuple[OrderRecord, int, int]], s_abs: list[OrderRecord],
                   s_matched: list[OrderRecord], old_rows: Mapping[str, PositionRow],
                   new_rows: Mapping[str, PositionRow], pre_qty: int, now: float) -> list[Fill]:
        """Explain one applied symbol's position change.

        ``d`` (this cycle's row delta) plus the carry is what the rows put into the internal position. In
        order, it is explained by:

        1. remaining drops seen in earlier cycles (``certain``: record, drop, part already reserved), which
           these rows certainly hold; they are booked now;
        2. remaining drops seen in this cycle on the change's side, which the rows probably hold; they are
           reserved (counted now, booked next cycle, when the rows certainly hold them);
        3. absent records on the change's side, in send order. An order still resting on that side may
           have filled between the two GETs (the rows show it before the open orders do), so until their
           second absent cycle absent records take only the carry; one that gets less than its remaining
           waits a cycle.

        What is left becomes the carry. A carry from an earlier cycle that is still unexplained, with no
        live record left that could explain it, goes to records of its side recently resolved CANCELED, and
        only then is booked as a foreign fill.
        """
        n_part = sum(r.side.sign * (q - rv) for r, q, rv in certain)
        old = self._pending.get(s)
        old_q = old.qty if old is not None else 0
        t_total = d + old_q - n_part
        for rec in s_abs:
            rec.missing_cycles += 1
        after = {r.local_id: live_contribution(r) - q for r, q, _ in certain}

        reserve: dict[str, int] = {}
        explained: dict[str, int] = {}
        deferred: set[str] = set()
        left = abs(t_total)
        if t_total:
            side = Side.BUY if t_total > 0 else Side.SELL
            for r in s_matched:
                if left == 0:
                    break
                o = self._obs.get(r.local_id)
                if r.side != side or o is None:
                    continue
                cur = after.get(r.local_id, live_contribution(r)) - o
                if cur > 0:
                    reserve[r.local_id] = min(cur, left)
                    left -= reserve[r.local_id]
            ambiguous = any(r.side == side and after.get(r.local_id, live_contribution(r)) > 0 for r in s_matched)
            # what is left of the carry once the certain drops and the reservations covered it (oldest first)
            used = n_part + _sgn(t_total) * sum(reserve.values())
            e_old = _sgn(old_q) * min(abs(old_q), abs(used)) if old_q and _sgn(used) == _sgn(old_q) else 0
            old_left = old_q - e_old
            cap = min(abs(old_left), left) if old_left and _sgn(old_left) == _sgn(t_total) else 0
            # this cycle's own row change on that side, once the reservations took their part
            fresh = max(0, _sgn(t_total) * d - sum(reserve.values()))
            # records surely inside these rows first: absent in the last cycle too, or cancel-acked before
            # the GET was sent; an order that just went absent may have filled after the position GET
            def _order(r: OrderRecord) -> tuple:
                return (not (r.missing_cycles >= 2 or r.state == S.CANCEL_ACKED),) + _send_key(r)
            for rec in sorted((r for r in s_abs if r.side == side), key=_order):
                first = rec.state != S.CANCEL_ACKED and rec.missing_cycles < 2
                capped = ambiguous and rec.missing_cycles < _AMBIGUOUS_MISSING_MAX
                avail = after.get(rec.local_id, live_contribution(rec))
                if first:
                    # It may have filled after the position GET, so these rows need not hold its fill. Only
                    # this cycle's own row change may explain it (another order's fill in the rows, which
                    # still counts as resting until it is resolved): never an older change (the carry, or
                    # a drop the rows showed masked), whose order may already be resolved and out of the
                    # room. Taking one would drop this record from room while the rows lack its fill.
                    # With a same-side order resting (ambiguous), even this cycle's change may be that
                    # order's fill, so the record waits for its second absent cycle.
                    lim = 0 if ambiguous else min(left, fresh)
                else:
                    lim = min(left, cap) if capped else left
                take = min(avail, lim)
                if capped and not first and take < min(avail, left):
                    deferred.add(rec.local_id)        # it might own some of the change it could not take
                if take > 0:
                    explained[rec.local_id] = take
                    left -= take
                    if first:
                        fresh -= take
                    elif capped:
                        cap -= take
        reserved = sum(reserve.values()) * _sgn(t_total)
        attributed = _sgn(t_total) * (abs(t_total) - left) - reserved
        u = t_total - attributed - reserved

        # one price for the symbol's fills when they are all on one side and the rows show exactly them
        fill_signed = sum(r.side.sign * q for r, q, _ in certain) + attributed
        sides = {r.side for r, q, _ in certain if q} | {r.side for r in s_abs if r.local_id in explained}
        px: float | None = None
        old_row = old_rows.get(s)
        old_qty = int(old_row.qty) if old_row is not None else 0
        if len(sides) == 1 and fill_signed and not reserved and n_part == fill_signed - attributed:
            if not old_q and d == fill_signed and old_qty == pre_qty:
                new_row = new_rows.get(s) or PositionRow(s, 0, None, None, None, None)
                px = self.positions.infer_price(s, old_row, new_row, next(iter(sides)), abs(fill_signed))
            elif old is not None and d == 0 and fill_signed == old_q:
                px = old.price

        out: list[Fill] = []
        absent_ids = {r.local_id for r in s_abs}
        for rec, q, rv in certain:
            self._resv.pop(rec.local_id, None)
            rpx = self._resv_px.pop(rec.local_id, None)
            if q > 0:
                out.append(self._apply_drop(rec, q, rpx if (rpx is not None and q == rv) else px, now))
            o = self._obs.get(rec.local_id)
            if o is not None and o >= live_contribution(rec):
                del self._obs[rec.local_id]
            if rec.local_id not in absent_ids:
                self._finish(rec, now)
        rguess = None
        if reserve and not old_q and not certain and not explained and d == reserved and old_qty == pre_qty:
            rsides = {r.side for r in s_matched if r.local_id in reserve}
            if len(rsides) == 1:
                new_row = new_rows.get(s) or PositionRow(s, 0, None, None, None, None)
                rguess = self.positions.infer_price(s, old_row, new_row, next(iter(rsides)), abs(reserved))
        for lid, qv in reserve.items():
            self._resv[lid] = self._resv.get(lid, 0) + qv
            if rguess is not None and lid not in self._resv_px:
                self._resv_px[lid] = rguess
        for rec in s_abs:
            e = explained.get(rec.local_id, 0)
            if e:
                out.append(self._book_fill(rec, e, px, now, update_pos=False))
            if rec.local_id in deferred:
                continue
            rest = live_contribution(rec)
            if rest <= 0:
                self.store.transition(rec, S.FILLED, now)
            elif rec.state == S.CANCEL_ACKED or rec.missing_cycles >= 2:
                self.store.transition(rec, S.CANCELED, now)
                self._recent_cancels.setdefault(s, []).append([rec.local_id, rec.side, rest, now])
            if rec.state in (S.FILLED, S.CANCELED):
                self._obs.pop(rec.local_id, None)
                self._resv.pop(rec.local_id, None)
                self.log.event("order_resolved", local_id=rec.local_id, state=str(rec.state),
                               filled=rec.filled_qty, qty=rec.qty, via="absence")

        # the carry: explanations cover the oldest change first, so only what is left of an earlier carry
        # can become a foreign fill, and not while a live record on its side could still explain it
        new_pending = u
        if old_q:
            e_all = n_part + attributed + reserved
            e_old = _sgn(old_q) * min(abs(old_q), abs(e_all)) if _sgn(e_all) == _sgn(old_q) else 0
            r_old = old_q - e_old
            fq = _sgn(old_q) * min(abs(r_old), abs(u)) if r_old and _sgn(u) == _sgn(old_q) else 0
            if fq and (now < self._foreign_hold_until or self._may_explain_later(
                    s, Side.BUY if fq > 0 else Side.SELL, deferred, {r.local_id for r in s_matched})):
                fq = 0
            new_pending = u - fq
            if fq:
                late, fills_late = self._late_attribute(s, fq, now, old.since if old is not None else None)
                out.extend(fills_late)
                fq -= late
            if fq:
                out.append(self._foreign_fill(s, fq, old.price if old is not None else None, now))
        if new_pending:
            guess = None
            nr = new_rows.get(s)
            if nr is not None and not old_q:
                guess = self.positions.infer_price(s, old_row, nr, Side.BUY if new_pending > 0 else Side.SELL,
                                                   abs(new_pending))
            keep = old is not None and _sgn(old.qty) == _sgn(new_pending)
            self._pending[s] = _Pending(qty=new_pending, price=guess if guess is not None else (
                old.price if keep else None), since=old.since if keep else now)
            self.log.event("reconcile_unexplained", symbol=s, qty=new_pending)
        else:
            self._pending.pop(s, None)
        return out

    # ------------------------------------------------------------------------------------------ fills
    def _book_fill(self, rec: OrderRecord, q: int, px: float | None, now: float, update_pos: bool) -> Fill:
        quality = PriceQuality.INFERRED
        if px is None or (rec.side == Side.BUY and px > rec.price + _PRICE_TOL) or (
                rec.side == Side.SELL and px < rec.price - _PRICE_TOL) or not math.isfinite(rec.price):
            quality = PriceQuality.LIMIT_BOUND
            px = rec.price if math.isfinite(rec.price) else (px if px is not None else 0.0)
        f = Fill(local_id=rec.local_id, order_id=rec.order_id, strategy=rec.strategy, key=rec.key,
                 symbol=rec.symbol, side=rec.side, qty=int(q), price=float(px), quality=quality, mono=now,
                 ref_mid=rec.ref_mid, expected_cost_bp=rec.expected_cost_bp, source="reconcile")
        prev_f = rec.filled_qty
        rec.filled_qty = prev_f + int(q)
        if rec.avg_fill_price is None or prev_f <= 0:
            rec.avg_fill_price = float(px)
        else:
            rec.avg_fill_price = (rec.avg_fill_price * prev_f + float(px) * q) / rec.filled_qty
        if rec.state not in (S.UNKNOWN, S.SENDING, S.NEW):
            rec.remaining = max(0, min(rec.remaining - int(q), rec.qty - rec.filled_qty))
        self.positions.apply_fill(f, update_pos=update_pos)
        self.counters["fills"] += 1
        self.log.event("fill_booked", source="reconcile", local_id=rec.local_id, strategy=rec.strategy,
                       symbol=rec.symbol, side=str(rec.side), qty=int(q), price=float(px), quality=str(quality))
        return f

    def _foreign_fill(self, s: str, signed_qty: int, px: float | None, now: float) -> Fill:
        side = Side.BUY if signed_qty > 0 else Side.SELL
        quality = PriceQuality.INFERRED
        if px is None:
            quality = PriceQuality.LIMIT_BOUND
            row = self.positions.last_rows().get(s)
            px = next((float(v) for v in ((row.mark, row.avg_price) if row is not None else ())
                       if isinstance(v, (int, float)) and math.isfinite(v) and v > 0), 0.0)
        f = Fill(local_id=None, order_id=None, strategy="foreign", key=None, symbol=s, side=side,
                 qty=abs(int(signed_qty)), price=float(px), quality=quality, mono=now, ref_mid=None,
                 expected_cost_bp=None, source="foreign")
        self.positions.apply_fill(f, update_pos=False)       # the applied row already holds the position
        self.counters["foreign_fills"] += 1
        self.log.alert("position_mismatch", f"{s}: {signed_qty:+d} shares not explained by any order; "
                       "accepting the exchange position", symbol=s, qty=signed_qty, price=px)
        return f

    def _stale_alerts(self, now: float, skipped: frozenset[str]) -> None:
        since = self.positions.skipped_since()
        for s in list(self._stale_alerted):
            if s not in since:
                self._stale_alerted.discard(s)
        for s, t0 in since.items():
            if now - t0 >= self._skip_alert_s and s not in self._stale_alerted:
                self._stale_alerted.add(s)
                self.log.alert("positions_stale", f"{s}: /positions row skipped for {now - t0:.1f}s "
                               "(orders keep acking); internal position in use", symbol=s, since=t0)
