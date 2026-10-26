"""Positions, cash and mark-to-market (DESIGN.md section 4.B, ``state/positions.py``).

The internal position of a symbol moves with every fill we learn about (acks first, reconcile second).
``/positions`` rows are the exchange's truth, but a row can be stale against fills already applied from
acks, and a fill can reach the row before its ack reaches us. The **quiet-symbol guard** resolves both
races: ``set_exchange`` applies a symbol's row only if that symbol had no PLACE in flight when the
reconcile cycle started (``quiet_stamp``) and no PLACE was handed out or answered since (every
``note_place`` call moves the symbol's action counter). Other symbols keep their internal position.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

from ctcbot.types import Fill, PositionRow, Side

__all__ = ["PositionBook"]


def _finite(x: object) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _sgn(x: float) -> int:
    return (x > 0) - (x < 0)


class PositionBook:
    """Signed positions per symbol, cash, the fill log and the latest exchange rows."""

    def __init__(self, symbols: Sequence[str]) -> None:
        self._symbols: tuple[str, ...] = tuple(symbols)
        self.pos: dict[str, int] = {}
        self.cash: float = 0.0
        self.fills: list[Fill] = []
        self._rows: dict[str, PositionRow] = {}
        self._rows_seen: bool = False
        self._last_mid: dict[str, float] = {}
        self._last_fill_px: dict[str, float] = {}
        # quiet-symbol guard: kept across season resets, because PLACEs in flight are real requests
        self._inflight: dict[str, int] = {s: 0 for s in self._symbols}
        self._inflight_t: dict[str, list[float]] = {}        # send times of the PLACEs counted in _inflight
        self._counter: dict[str, int] = {s: 0 for s in self._symbols}
        self._skip_since: dict[str, float] = {}
        # +1 at every reset (season change): an OrderManager of an earlier season, which the core keeps only
        # to absorb its late results, must not book that season's fills into this one (internal to group B)
        self.generation: int = -1
        self.reset()

    # ------------------------------------------------------------------------------------------ fills
    def apply_fill(self, f: Fill, update_pos: bool = True) -> None:
        """Book one fill: ``pos += sign*qty`` (if ``update_pos``) and ``cash -= sign*qty*price``."""
        q = int(f.qty)
        if q <= 0:
            return
        sign = f.side.sign
        if update_pos:
            self.pos[f.symbol] = self.pos.get(f.symbol, 0) + sign * q
        px = _finite(f.price)
        if px is not None:
            self.cash -= sign * q * px
            if px > 0:
                self._last_fill_px[f.symbol] = px
        self.fills.append(f)

    def adjust_pos(self, symbol: str, delta: int) -> None:
        """Move a position without cash (internal to group B: the Reconciler's unreflected partial fills)."""
        if delta:
            self.pos[symbol] = self.pos.get(symbol, 0) + int(delta)

    # ------------------------------------------------------------------------------------------ guard
    def note_place(self, symbol: str, sent: bool, now: float | None = None) -> None:
        """A PLACE was handed out (``sent=True``) or its result arrived (``sent=False``)."""
        n = self._inflight.get(symbol, 0)
        self._inflight[symbol] = n + 1 if sent else max(0, n - 1)
        ts = self._inflight_t.setdefault(symbol, [])
        if sent:
            ts.append(-math.inf if now is None else float(now))
        elif ts:
            ts.pop(0)
        self._counter[symbol] = self._counter.get(symbol, 0) + 1

    def expire_inflight(self, now: float, max_age_s: float) -> list[str]:
        """Forget in-flight PLACEs sent more than ``max_age_s`` ago (hardening: a request whose result never
        came back, e.g. after an exception, must not skip the symbol's ``/positions`` rows for the rest of the
        run). Entries recorded without a time never expire. Returns the symbols that changed."""
        out = []
        for sym, ts in self._inflight_t.items():
            k = 0
            while k < len(ts) and ts[k] != -math.inf and now - ts[k] > max_age_s:
                k += 1
            if k:
                del ts[:k]
                self._inflight[sym] = max(0, self._inflight.get(sym, 0) - k)
                self._counter[sym] = self._counter.get(sym, 0) + 1
                out.append(sym)
        return out

    def quiet_stamp(self) -> dict[str, tuple[int, int]]:
        """symbol -> (PLACEs in flight, action counter), taken when a reconcile cycle starts."""
        syms = set(self._symbols) | set(self._inflight) | set(self._counter)
        return {s: (self._inflight.get(s, 0), self._counter.get(s, 0)) for s in syms}

    def is_quiet(self, symbol: str, stamp: Mapping[str, tuple[int, int]]) -> bool:
        """True iff ``symbol`` had no PLACE in flight at ``stamp`` and no PLACE action since."""
        st = stamp.get(symbol)
        if st is None:
            return self._inflight.get(symbol, 0) == 0 and self._counter.get(symbol, 0) == 0
        inflight, counter = st
        return inflight == 0 and self._counter.get(symbol, 0) == counter

    def set_exchange(self, rows: Mapping[str, PositionRow], stamp: Mapping[str, tuple[int, int]], now: float
                     ) -> tuple[dict[str, int], frozenset[str]]:
        """Apply the quiet symbols' rows; return (delta per applied symbol, skipped symbols).

        A tracked symbol missing from ``rows`` is read as flat (the exchange may omit flat symbols). The
        rows themselves are kept whole for ``last_rows`` and ``exchange_pnl``.
        """
        clean: dict[str, PositionRow] = {}
        for k, r in (rows or {}).items():
            if isinstance(r, PositionRow):
                clean[r.symbol if isinstance(r.symbol, str) and r.symbol else k] = r
        deltas: dict[str, int] = {}
        skipped: set[str] = set()
        syms = list(self._symbols) + [s for s in clean if s not in self._symbols]
        for s in syms:
            row = clean.get(s)
            new_qty = int(row.qty) if row is not None and _finite(row.qty) is not None else 0
            if not self.is_quiet(s, stamp):
                skipped.add(s)
                self._skip_since.setdefault(s, now)
                continue
            self._skip_since.pop(s, None)
            old = self.pos.get(s, 0)
            deltas[s] = new_qty - old
            self.pos[s] = new_qty
        self._rows = clean
        self._rows_seen = True
        self._row_syms.update(clean)
        return deltas, frozenset(skipped)

    def skipped_since(self) -> dict[str, float]:
        """symbol -> mono time since when its rows have been skipped continuously (internal to group B)."""
        return dict(self._skip_since)

    def last_rows(self) -> dict[str, PositionRow]:
        return dict(self._rows)

    # ------------------------------------------------------------------------------------------ P&L
    def _mark(self, symbol: str, mids: Mapping[str, float | None]) -> float | None:
        m = _finite(mids.get(symbol)) if mids else None
        if m is not None and m > 0:
            self._last_mid[symbol] = m
            return m
        if symbol in self._last_mid:
            return self._last_mid[symbol]
        row = self._rows.get(symbol)
        if row is not None:
            for v in (row.mark, row.avg_price):
                fv = _finite(v)
                if fv is not None and fv > 0:
                    return fv
        return self._last_fill_px.get(symbol)

    def mtm(self, mids: Mapping[str, float | None]) -> float:
        """``cash + sum(pos * mid)``; a missing mid uses the last mid (then the row mark, then the last fill)."""
        total = self.cash
        for s, q in self.pos.items():
            if q == 0:
                continue
            m = self._mark(s, mids)
            if m is not None:
                total += q * m
        return total

    def exchange_pnl(self) -> float | None:
        """Sum of realized + unrealized over the last ``/positions`` rows, if every row has both.

        None as well when a symbol that had a row earlier this season, or that holds a position now, has no
        row: the exchange may omit flat symbols, and their realized P&L would otherwise vanish from the sum
        (a false drawdown).
        """
        if not self._rows_seen:
            return None
        if any(s not in self._rows for s in self._row_syms) or any(
                q and s not in self._rows for s, q in self.pos.items()):
            return None
        total = 0.0
        for r in self._rows.values():
            a, b = _finite(r.realized), _finite(r.unrealized)
            if a is None or b is None:
                return None
            total += a + b
        return total

    def infer_price(self, symbol: str, old: PositionRow | None, new: PositionRow, side: Side, qty: int
                    ) -> float | None:
        """Fill price implied by two consecutive ``/positions`` rows, or None (section 4.B).

        Growth without a sign change uses the average prices; a reduction without crossing zero uses the
        realized P&L change. ``old=None`` is read as a flat row.
        """
        if new is None or qty is None or int(qty) <= 0:
            return None
        qty = int(qty)
        oq = int(old.qty) if old is not None and _finite(old.qty) is not None else 0
        nq = int(new.qty) if _finite(new.qty) is not None else None
        if nq is None or nq == oq:
            return None
        if _sgn(nq - oq) != side.sign:
            return None
        px: float | None = None
        if abs(nq) > abs(oq) and (oq == 0 or _sgn(oq) == _sgn(nq)):
            na = _finite(new.avg_price)
            oa = _finite(old.avg_price) if old is not None else None
            if na is not None and (oq == 0 or oa is not None):
                px = (na * nq - (oa or 0.0) * oq) / (nq - oq)
        elif abs(nq) < abs(oq) and (nq == 0 or _sgn(oq) == _sgn(nq)):
            if old is None:
                return None
            oa, orl, nrl = _finite(old.avg_price), _finite(old.realized), _finite(new.realized)
            if oa is not None and orl is not None and nrl is not None:
                px = oa + ((nrl - orl) / qty) * _sgn(oq)
        if px is None or not math.isfinite(px) or px <= 0:
            return None
        return px

    def pnl_by_strategy(self, marks: Mapping[str, float]) -> dict[str, float]:
        """Sum over fills of ``sign*qty*(mark - price)`` per strategy; fills without a mark are skipped."""
        out: dict[str, float] = {}
        for f in self.fills:
            m = _finite(marks.get(f.symbol)) if marks else None
            if m is None:
                m = self._last_mid.get(f.symbol)
            px = _finite(f.price)
            if m is None or px is None:
                continue
            out[f.strategy] = out.get(f.strategy, 0.0) + f.side.sign * int(f.qty) * (m - px)
        return out

    def reset(self) -> None:
        """Season reset: flat positions, zero cash, no fills or rows (the in-flight guard is kept)."""
        self.pos = {s: 0 for s in self._symbols}
        self.cash = 0.0
        self.fills = []
        self._rows = {}
        self._rows_seen = False
        self._row_syms: set[str] = set()
        self._last_mid = {}
        self._last_fill_px = {}
        self._skip_since = {}
        self.generation += 1
