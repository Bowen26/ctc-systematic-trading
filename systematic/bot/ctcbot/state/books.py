"""Per-symbol book state built from the normalized feed (DESIGN.md section 4.B, ``state/books.py``).

The touch (best bid and ask with sizes) comes from whichever of the latest ``quote`` or ``orderbook``
event carries the newer server timestamp; the five-level depth always comes from the latest orderbook.
Empty books and quotes with None sides are normal at closes and season boundaries, so every field of a
``BookView`` may be None and nothing here raises on missing data.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

from ctcbot.types import BookEvent, BookLevel, BookView, QuoteEvent, TradeEvent

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from ctcbot.config import Config

__all__ = ["BookStore", "parse_server_ts"]

_UTC = _dt.timezone.utc


def parse_server_ts(ts: Any) -> float | None:
    """Exchange ISO timestamp -> float seconds, or None when it cannot be parsed.

    Naive stamps (the exchange's format, e.g. ``2026-10-23T10:47:26.809171``) are read as UTC so that the
    conversion is the same on every machine; only the ordering of stamps matters here. Numbers are
    accepted as already being seconds.
    """
    if isinstance(ts, bool):
        return None
    if isinstance(ts, (int, float)):
        return float(ts) if math.isfinite(ts) else None
    if not isinstance(ts, str) or not ts:
        return None
    try:
        d = _dt.datetime.fromisoformat(ts.strip())
    except (ValueError, OverflowError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_UTC)
    try:
        return d.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _num(x: Any) -> float | None:
    """A finite positive price, else None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) and x > 0 else None


def _size(x: Any) -> int | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        return int(x)
    except (ValueError, OverflowError):
        return None


@dataclass(slots=True)
class _Touch:
    """One touch observation (from a quote or the top of an orderbook)."""
    bid: float | None
    ask: float | None
    bid_size: int | None
    ask_size: int | None
    ts: float | None        # parsed server timestamp
    server_ts: str | None
    recv_mono: float


@dataclass(slots=True)
class _SymBook:
    book_touch: _Touch | None = None
    quote_touch: _Touch | None = None
    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()
    last_trade_price: float | None = None
    last_trade_qty: int | None = None
    last_trade_mono: float | None = None


def _newer(a: _Touch | None, b: _Touch | None) -> _Touch | None:
    """The touch with the newer server timestamp; ties and unparseable stamps fall back to receive order."""
    if a is None:
        return b
    if b is None:
        return a
    if a.ts is not None and b.ts is not None and a.ts != b.ts:
        return a if a.ts > b.ts else b
    return a if a.recv_mono >= b.recv_mono else b


class BookStore:
    """Latest touch and depth per symbol, read by strategies, the risk checks and the MTM.

    Owned by the core's event-loop thread; not thread-safe by design (DESIGN.md section 5.1).
    """

    def __init__(self, symbols: Sequence[str], cfg: "Config") -> None:
        self._symbols: tuple[str, ...] = tuple(symbols)
        try:
            self._wall_bp = float(cfg.get("guards.opening.max_half_spread_bp", 300.0))
        except (TypeError, ValueError):
            self._wall_bp = 300.0
        self._books: dict[str, _SymBook] = {}
        self.reset()

    # ------------------------------------------------------------------------------------------ input
    def _book(self, symbol: str) -> _SymBook:
        b = self._books.get(symbol)
        if b is None:
            b = self._books[symbol] = _SymBook()
        return b

    def on_book(self, ev: BookEvent) -> None:
        """Apply an orderbook event: new depth, and a touch candidate from its top levels."""
        if not isinstance(ev.symbol, str):
            return
        b = self._book(ev.symbol)
        bids = tuple(lv for lv in (ev.bids or ()) if isinstance(lv, BookLevel) and _num(lv.price) is not None)
        asks = tuple(lv for lv in (ev.asks or ()) if isinstance(lv, BookLevel) and _num(lv.price) is not None)
        b.bids, b.asks = bids, asks
        b.book_touch = _Touch(
            bid=float(bids[0].price) if bids else None, ask=float(asks[0].price) if asks else None,
            bid_size=_size(bids[0].qty) if bids else None, ask_size=_size(asks[0].qty) if asks else None,
            ts=parse_server_ts(ev.server_ts), server_ts=ev.server_ts if isinstance(ev.server_ts, str) else None,
            recv_mono=float(ev.recv_mono))

    def on_quote(self, ev: QuoteEvent) -> None:
        """Apply a quote event as a touch candidate (either side may be None)."""
        if not isinstance(ev.symbol, str):
            return
        b = self._book(ev.symbol)
        bid, ask = _num(ev.bid), _num(ev.ask)
        b.quote_touch = _Touch(
            bid=bid, ask=ask, bid_size=_size(ev.bid_size) if bid is not None else None,
            ask_size=_size(ev.ask_size) if ask is not None else None, ts=parse_server_ts(ev.server_ts),
            server_ts=ev.server_ts if isinstance(ev.server_ts, str) else None, recv_mono=float(ev.recv_mono))

    def on_trade(self, ev: TradeEvent) -> None:
        """Remember the last public trade (informational; it never moves the touch)."""
        if not isinstance(ev.symbol, str):
            return
        px = _num(ev.price)
        if px is None:
            return
        b = self._book(ev.symbol)
        b.last_trade_price, b.last_trade_qty, b.last_trade_mono = px, _size(ev.qty), float(ev.recv_mono)

    # ------------------------------------------------------------------------------------------ output
    def view(self, symbol: str, now: float) -> BookView:
        """A frozen view of one symbol's book at ``now`` (an empty view for a symbol never seen)."""
        b = self._books.get(symbol)
        t = _newer(b.book_touch, b.quote_touch) if b is not None else None
        if t is None:
            return BookView(symbol=symbol, bid=None, ask=None, bid_size=None, ask_size=None, bids=(), asks=(),
                            mid=None, half_spread_bp=None, age_s=None, empty=True, wall_only=False,
                            server_ts=None)
        bid, ask = t.bid, t.ask
        mid = hs_bp = None
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
            if mid > 0:
                hs_bp = (ask - bid) / 2.0 / mid * 1e4
        wall_only = hs_bp is not None and hs_bp > self._wall_bp
        return BookView(symbol=symbol, bid=bid, ask=ask, bid_size=t.bid_size, ask_size=t.ask_size,
                        bids=b.bids, asks=b.asks, mid=mid, half_spread_bp=hs_bp,
                        age_s=max(0.0, now - t.recv_mono), empty=bid is None and ask is None,
                        wall_only=wall_only, server_ts=t.server_ts)

    def views(self, now: float) -> dict[str, BookView]:
        """Views of every configured symbol (plus any other symbol the feed has sent)."""
        syms = list(self._symbols) + [s for s in self._books if s not in self._symbols]
        return {s: self.view(s, now) for s in syms}

    def last_trade(self, symbol: str) -> tuple[float | None, int | None, float | None]:
        """(price, qty, recv_mono) of the last public trade, or Nones."""
        b = self._books.get(symbol)
        if b is None:
            return None, None, None
        return b.last_trade_price, b.last_trade_qty, b.last_trade_mono

    def reset(self) -> None:
        """Forget every book (season reset)."""
        self._books = {s: _SymBook() for s in self._symbols}
