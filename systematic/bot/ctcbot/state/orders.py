"""Working-order records and their lifecycle (DESIGN.md section 4.B ``state/orders.py`` and section 5.3).

``OrderStore`` owns every ``OrderRecord`` of one season epoch. Records change state only through
``transition``, which enforces the ``TRANSITIONS`` table, so an impossible edge (for example a FILLED
order becoming OPEN again) raises instead of silently corrupting room accounting.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any, Iterator, Mapping

from ctcbot.types import LIVE_STATES, TERMINAL_STATES, OrderRecord, OrderState, Priority, Side, WorkingSummary

from ctcbot.state.limits import live_contribution

__all__ = ["TRANSITIONS", "OrderStore"]

S = OrderState
TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    S.NEW: frozenset({S.SENDING, S.REJECTED}),
    S.SENDING: frozenset({S.OPEN, S.FILLED, S.REJECTED, S.UNKNOWN}),
    S.OPEN: frozenset({S.OPEN, S.FILLED, S.CANCEL_PENDING, S.CANCELED}),
    S.CANCEL_PENDING: frozenset({S.CANCEL_ACKED, S.CANCEL_UNKNOWN, S.FILLED, S.CANCELED, S.OPEN}),
    S.CANCEL_ACKED: frozenset({S.CANCELED, S.FILLED}),
    S.UNKNOWN: frozenset({S.OPEN, S.FILLED, S.CANCELED, S.CANCEL_PENDING}),
    S.CANCEL_UNKNOWN: frozenset({S.OPEN, S.CANCELED, S.FILLED, S.CANCEL_PENDING}),
    S.FILLED: frozenset(),
    S.CANCELED: frozenset(),
    S.REJECTED: frozenset(),
}

_FIELDS = frozenset(f.name for f in dataclasses.fields(OrderRecord))
_CLIP_EXPOSURE_EXCLUDED = frozenset({S.CANCEL_ACKED})
_MONO_FIELDS = ("created_mono", "sent_mono", "acked_mono", "cancel_sent_mono", "last_seen_open_mono", "done_mono")
_RESTORED_AGE_S = 1e6       # restored records' times end this far before mono 0 (load_state)


def _finite_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _rec_to_dict(r: OrderRecord) -> dict:
    d = dataclasses.asdict(r)
    d["side"] = str(r.side)
    d["priority"] = int(r.priority)
    d["state"] = str(r.state)
    return d


def _rec_from_dict(d: Mapping[str, Any]) -> OrderRecord:
    kw = {k: v for k, v in d.items() if k in _FIELDS}
    kw["side"] = Side(kw["side"])
    kw["priority"] = Priority(int(kw["priority"]))
    kw["state"] = OrderState(kw.get("state", "new"))
    return OrderRecord(**kw)


class OrderStore:
    """All order records of one season epoch, with the lookups the OM and the Reconciler need."""

    def __init__(self, epoch: int) -> None:
        self.epoch: int = int(epoch)
        self._n: int = 0
        self._recs: dict[str, OrderRecord] = {}
        self._by_oid: dict[str, str] = {}

    # ------------------------------------------------------------------------------------------ create
    def new(self, **fields: Any) -> OrderRecord:
        """Create a record with ``local_id = f"e{epoch}-{n}"`` and ``remaining = qty``."""
        unknown = set(fields) - _FIELDS
        if unknown:
            raise TypeError(f"unknown OrderRecord fields: {sorted(unknown)}")
        fields.pop("local_id", None)
        self._n += 1
        lid = f"e{self.epoch}-{self._n}"
        qty = int(fields.get("qty", 0))
        if qty < 1:
            raise ValueError(f"order qty must be >= 1, got {qty}")
        fields["qty"] = qty
        fields.setdefault("remaining", qty)
        rec = OrderRecord(local_id=lid, **fields)
        self._recs[lid] = rec
        if rec.order_id:
            self._by_oid[rec.order_id] = lid
        return rec

    # ------------------------------------------------------------------------------------------ change
    def transition(self, rec: OrderRecord, state: OrderState, now: float, **updates: Any) -> OrderRecord:
        """Move ``rec`` to ``state`` and apply ``updates``; raise ValueError on an edge not in TRANSITIONS.

        A same-state call is an update and always allowed. Entering a terminal state sets ``remaining``
        to 0 and stamps ``done_mono``.
        """
        state = OrderState(state)
        if state != rec.state and state not in TRANSITIONS[rec.state]:
            raise ValueError(f"illegal order transition {rec.state} -> {state} ({rec.local_id})")
        bad = set(updates) - _FIELDS
        if bad or "local_id" in updates or "state" in updates:
            raise TypeError(f"bad update fields: {sorted(bad | ({'local_id', 'state'} & set(updates)))}")
        old_oid = rec.order_id
        for k, v in updates.items():
            setattr(rec, k, v)
        rec.state = state
        if state in TERMINAL_STATES:
            rec.remaining = 0
            if "done_mono" not in updates or rec.done_mono is None:
                rec.done_mono = now
        if rec.order_id != old_oid:
            if old_oid and self._by_oid.get(old_oid) == rec.local_id:
                del self._by_oid[old_oid]
            if rec.order_id:
                self._by_oid[rec.order_id] = rec.local_id
        return rec

    def set_order_id(self, rec: OrderRecord, order_id: str | None) -> None:
        """Change a record's exchange id and keep the index consistent (no state change)."""
        if rec.order_id and self._by_oid.get(rec.order_id) == rec.local_id:
            del self._by_oid[rec.order_id]
        rec.order_id = order_id
        if order_id:
            self._by_oid[order_id] = rec.local_id

    # ------------------------------------------------------------------------------------------ lookups
    def __iter__(self) -> Iterator[OrderRecord]:
        return iter(list(self._recs.values()))

    def __len__(self) -> int:
        return len(self._recs)

    def all(self) -> list[OrderRecord]:
        """Every record, live and terminal, in creation order."""
        return list(self._recs.values())

    def live(self, symbol: str | None = None) -> list[OrderRecord]:
        """Records in a LIVE state, optionally for one symbol, in creation order."""
        return [r for r in self._recs.values()
                if r.state in LIVE_STATES and (symbol is None or r.symbol == symbol)]

    def by_order_id(self, order_id: str | None) -> OrderRecord | None:
        """The record holding this exchange id, if any."""
        if not order_id:
            return None
        lid = self._by_oid.get(order_id)
        if lid is not None:
            r = self._recs.get(lid)
            if r is not None and r.order_id == order_id:
                return r
        for r in self._recs.values():           # fallback: an id set without the index
            if r.order_id == order_id:
                self._by_oid[order_id] = r.local_id
                return r
        return None

    def by_key(self, key: str | None) -> list[OrderRecord]:
        """Live records carrying this idempotency key, in creation order."""
        if key is None:
            return []
        return [r for r in self._recs.values() if r.key == key and r.state in LIVE_STATES]

    def by_local(self, local_id: str | None) -> OrderRecord | None:
        return self._recs.get(local_id) if local_id else None

    def working_summary(self, symbol: str) -> WorkingSummary:
        """Live quantities of one symbol: all styles for room, clip style for exposure (section 3)."""
        buy = sell = cbuy = csell = n = 0
        clip_working = False
        keys: set[str] = set()
        for r in self._recs.values():
            if r.symbol != symbol or r.state not in LIVE_STATES:
                continue
            n += 1
            q = live_contribution(r)
            if r.side == Side.BUY:
                buy += q
            else:
                sell += q
            if r.key:
                keys.add(r.key)
            if r.style == "clip" and r.state not in _CLIP_EXPOSURE_EXCLUDED:
                clip_working = True
                if r.side == Side.BUY:
                    cbuy += q
                else:
                    csell += q
        return WorkingSummary(symbol=symbol, buy_qty=buy, sell_qty=sell, clip_buy_qty=cbuy, clip_sell_qty=csell,
                              n_orders=n, clip_working=clip_working, keys=frozenset(keys))

    # ------------------------------------------------------------------------------------------ upkeep
    def prune(self, now: float, keep_s: float = 120.0) -> int:
        """Drop terminal records finished more than ``keep_s`` ago; return how many were dropped."""
        drop = [lid for lid, r in self._recs.items() if r.state in TERMINAL_STATES
                and now - (r.done_mono if r.done_mono is not None else r.created_mono) > keep_s]
        for lid in drop:
            r = self._recs.pop(lid)
            if r.order_id and self._by_oid.get(r.order_id) == lid:
                del self._by_oid[r.order_id]
        return len(drop)

    def drop_dry(self) -> int:
        """Remove every dry-run record (promotion to live); return how many were removed."""
        drop = [lid for lid, r in self._recs.items() if r.dry]
        for lid in drop:
            r = self._recs.pop(lid)
            if r.order_id and self._by_oid.get(r.order_id) == lid:
                del self._by_oid[r.order_id]
        return len(drop)

    def state_dict(self) -> dict:
        """Live records and the id counter, JSON-serialisable."""
        return {"epoch": self.epoch, "n": self._n, "records": [_rec_to_dict(r) for r in self.live()]}

    def load_state(self, d: Mapping[str, Any]) -> None:
        """Restore records saved by ``state_dict``; malformed entries are skipped.

        The saving process is gone, so no result of its in-flight requests will ever arrive: a restored
        NEW or SENDING record becomes UNKNOWN and a CANCEL_PENDING one CANCEL_UNKNOWN, which reconcile
        adopts or resolves (a SENDING record would otherwise block its symbol's clips and the kill
        forever). Mono times of another process need not be comparable with this one's (a reboot restarts
        the clock), so every restored time is moved into the distant past, keeping its order: the first
        reconcile cycle may judge every restored record, and a restored clip's remainder is cancelled at
        the first step.
        """
        if not isinstance(d, Mapping):
            return
        try:
            self._n = max(self._n, int(d.get("n", 0)))
        except (TypeError, ValueError):
            pass
        recs: list[OrderRecord] = []
        for item in d.get("records") or ():
            try:
                rec = _rec_from_dict(item)
            except (TypeError, ValueError, KeyError):
                continue
            if rec.state in TERMINAL_STATES or not isinstance(rec.local_id, str) or not rec.local_id:
                continue
            recs.append(rec)
        times = [float(v) for r in recs for f in _MONO_FIELDS if _finite_num(v := getattr(r, f))]
        shift = (max(times) if times else 0.0) + _RESTORED_AGE_S
        for rec in recs:
            for f in _MONO_FIELDS:
                v = getattr(rec, f)
                if _finite_num(v):
                    setattr(rec, f, float(v) - shift)
                elif f == "created_mono":
                    rec.created_mono = -shift
                else:
                    setattr(rec, f, None)
            if rec.state in (S.NEW, S.SENDING):
                rec.state = S.UNKNOWN
                rec.remaining = max(0, int(rec.qty) - int(rec.filled_qty))
                if rec.sent_mono is None:
                    rec.sent_mono = rec.created_mono
            elif rec.state == S.CANCEL_PENDING:
                rec.state = S.CANCEL_UNKNOWN
                if rec.cancel_sent_mono is None:
                    rec.cancel_sent_mono = rec.created_mono
            self._recs[rec.local_id] = rec
            if rec.order_id:
                self._by_oid[rec.order_id] = rec.local_id
            # keep the counter ahead of restored ids of this epoch
            pre = f"e{self.epoch}-"
            if rec.local_id.startswith(pre):
                try:
                    self._n = max(self._n, int(rec.local_id[len(pre):]))
                except ValueError:
                    pass
