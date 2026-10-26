"""Limit-room accounting (DESIGN.md section 4.B, ``state/limits.py``).

The exchange counts position plus same-side resting orders against the **case** limit, so every order
is clamped to the case room. Strategy sizing uses the **scaled** limit ``L_s`` (``strategy_limits``), and
``strategy_room`` is the smaller of the two rooms.

Resting quantity is deliberately conservative: an order whose outcome is not yet known (NEW, SENDING,
UNKNOWN) counts its full unfilled quantity, and cancel-in-progress states count their remaining until a
reconcile cycle resolves them.
"""
from __future__ import annotations

from typing import Iterable, Protocol

from ctcbot.types import LIVE_STATES, OrderRecord, OrderState, Side

__all__ = ["live_contribution", "resting_qty", "room", "strategy_room"]

_FULL_QTY_STATES = frozenset({OrderState.NEW, OrderState.SENDING, OrderState.UNKNOWN})


class _HasLive(Protocol):
    def live(self, symbol: str | None = None) -> list[OrderRecord]: ...


def live_contribution(rec: OrderRecord) -> int:
    """Shares this record may still add to the exchange's resting count (0 for terminal records)."""
    if rec.state not in LIVE_STATES:
        return 0
    if rec.state in _FULL_QTY_STATES:
        return max(0, int(rec.qty) - int(rec.filled_qty))
    return max(0, int(rec.remaining))


def resting_qty(orders: "_HasLive | Iterable[OrderRecord]", symbol: str, side: Side) -> int:
    """Sum of live resting quantity for (symbol, side) over an ``OrderStore`` or an iterable of records."""
    recs = orders.live(symbol) if hasattr(orders, "live") else orders
    total = 0
    for r in recs:
        if r.symbol == symbol and r.side == side:
            total += live_contribution(r)
    return total


def room(limit: int, position: int, side: Side, resting: int) -> int:
    """Shares that may still be added on ``side`` under ``limit``: never negative."""
    if side == Side.BUY:
        return max(0, int(limit) - int(position) - int(resting))
    return max(0, int(limit) + int(position) - int(resting))


def strategy_room(case_limit: int, scaled_limit: int, position: int, side: Side, resting: int) -> int:
    """The smaller of the case room (what the exchange enforces) and the scaled strategy room."""
    return min(room(case_limit, position, side, resting), room(scaled_limit, position, side, resting))
