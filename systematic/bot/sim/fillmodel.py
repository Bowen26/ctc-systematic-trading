"""r_sim-equivalent fill-cost formulas for the research simulator (DESIGN.md section 4.E).

The formulas are ports of ``testnet/analysis/rebacktest/r_sim.py`` (``interp``, ``walk_cost``, ``imp_kernel`` and
the cost block of ``sim()``), written against the ``CostModel`` interface of group C instead of r_sim's
preprocessed table:

* **walk** of an increment of q shares on a row that already carries X1 of our same-side shares (in units of L):
  ``cw = (X2*c(X2) - X1*c(X1)) / (X2 - X1)`` with ``X2 = X1 + q/L`` and ``c = cost.walk_bp(sym, phase, x, side)``;
* **follow-on**: ``beta * x_k`` for every earlier same-side fill of ours with ``0 < t - t_k <= fo_window_s``;
* **impact**: ``I(dt) * x_k`` for same-side fills older than the follow-on window, minus ``I(dt) * x_k`` for
  opposite-side fills (``impact_credit``); ``I`` interpolates the kernel table, then decays exponentially and is
  zero beyond ``impact_cut_s``;
* **replenishment**: ``q <= int(L * max(0, depth - (1 - rho) * x_prev)) - filled_side``;
* **price**: ``row_px * (1 + side * mult * (cw + fo + im) * 1e-4)``.

The limit rule (``limit_fill_qty``) is the bot-specific extension of section 4.E: the largest integer q* whose
*marginal* price ``row_px * (1 + side * mult * (m(X1 + q*/L) + fo + im) * 1e-4)`` with ``m(x) = d(x c(x))/dx`` is
on the right side of the order's limit.

Everything here is pure and deterministic; ``FillHistory`` holds the per-round state the formulas read.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ctcbot.types import ETFS, Side

__all__ = [
    "FillParams", "FillHistory", "interp", "impact_kernel", "walk_increment", "marginal_cost",
    "follow_on_and_impact", "replenish_room", "limit_fill_qty", "fill_price", "match_row", "side_int",
    "side_enum",
]

# r_sim defaults (also in config/default.json "sim"); used when a config value is missing.
_KERNEL_T: tuple[float, ...] = (0.0, 2.0, 4.0, 10.0, 20.0, 30.0)
_KERNEL_BP: tuple[float, ...] = (27.6, 29.7, 31.4, 41.0, 35.8, 26.8)


def side_int(side: Side | str | int) -> int:
    """+1 for a buy, -1 for a sell (accepts ``Side``, "buy"/"sell" or +-1)."""
    if isinstance(side, int) and not isinstance(side, bool):
        if side in (1, -1):
            return side
        raise ValueError(f"bad side {side!r}")
    s = str(side).lower()
    if s == "buy":
        return 1
    if s == "sell":
        return -1
    raise ValueError(f"bad side {side!r}")


def side_enum(side: int) -> Side:
    """``Side`` for +1 / -1."""
    return Side.BUY if side > 0 else Side.SELL


@dataclass(frozen=True)
class FillParams:
    """The ``sim.*`` knobs the fill formulas read (DESIGN.md section 8)."""

    cost_mult: float = 1.0
    follow_on: bool = True
    impact: bool = True
    impact_credit: bool = True
    replenish: bool = True
    fo_window_s: float = 8.0
    beta_stock: float = 100.0
    beta_etf: float = 48.0
    kernel_t: tuple[float, ...] = _KERNEL_T
    kernel_bp: tuple[float, ...] = _KERNEL_BP
    tau_s: float = 47.0
    cut_s: float = 150.0
    lat_rows: int = 0
    lat_rep_rows: int = 1
    book_levels_x: tuple[float, ...] = (0.1, 0.17, 0.45, 0.7, 3.0)
    recorded_latency_s: float = 0.3
    recorded_follow_on: bool = True
    marginal_h: float = 1e-4

    @classmethod
    def from_config(cls, cfg: Any) -> "FillParams":
        """Read ``sim.*`` from a ``Config`` (anything with ``get(dotted, default)``)."""
        def g(key: str, default: Any) -> Any:
            try:
                v = cfg.get(key, default)
            except Exception:  # noqa: BLE001 - a partial config falls back to the r_sim default
                return default
            return default if v is None else v

        beta = g("sim.beta_bp_per_L", {}) or {}
        kt = tuple(float(x) for x in g("sim.impact_kernel_t", _KERNEL_T))
        kv = tuple(float(x) for x in g("sim.impact_kernel_bp", _KERNEL_BP))
        if len(kt) != len(kv) or len(kt) < 2 or any(b <= a for a, b in zip(kt, kt[1:])):
            raise ValueError("sim.impact_kernel_t / impact_kernel_bp must be increasing and of equal length")
        levels = tuple(float(x) for x in g("sim.book_levels_x", (0.1, 0.17, 0.45, 0.7, 3.0)))
        if not levels or any(b <= a for a, b in zip(levels, levels[1:])) or levels[0] <= 0:
            raise ValueError("sim.book_levels_x must be positive and increasing")
        return cls(
            cost_mult=float(g("sim.cost_mult", 1.0)),
            follow_on=bool(g("sim.follow_on", True)),
            impact=bool(g("sim.impact", True)),
            impact_credit=bool(g("sim.impact_credit", True)),
            replenish=bool(g("sim.replenish", True)),
            fo_window_s=float(g("sim.fo_window_s", 8.0)),
            beta_stock=float(beta.get("stock", 100.0)),
            beta_etf=float(beta.get("etf", 48.0)),
            kernel_t=kt,
            kernel_bp=kv,
            tau_s=float(g("sim.impact_tau_s", 47.0)),
            cut_s=float(g("sim.impact_cut_s", 150.0)),
            lat_rows=int(g("sim.lat_rows", 0)),
            lat_rep_rows=int(g("sim.lat_rep_rows", 1)),
            book_levels_x=levels,
            recorded_latency_s=float(g("sim.recorded_latency_s", 0.3)),
            recorded_follow_on=bool(g("sim.recorded_follow_on", True)),
        )

    def beta(self, symbol: str) -> float:
        """Follow-on beta in bp per L of our earlier same-side fill (r_sim's beta_s / beta_e)."""
        return self.beta_etf if symbol in ETFS else self.beta_stock


# ----------------------------------------------------------------------------------------------- formulas
def interp(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    """Piecewise-linear interpolation clamped at both ends (r_sim ``interp``)."""
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    k = bisect.bisect_right(xs, x)
    x0, x1 = xs[k - 1], xs[k]
    return float(ys[k - 1] + (ys[k] - ys[k - 1]) * (x - x0) / (x1 - x0))


def impact_kernel(dt: float, p: FillParams) -> float:
    """Impact kernel I(dt) in bp per L (r_sim ``imp_kernel``): table, then exponential decay, 0 beyond the cut."""
    if dt <= p.kernel_t[-1]:
        return interp(p.kernel_t, p.kernel_bp, dt)
    if dt > p.cut_s:
        return 0.0
    return p.kernel_bp[-1] * math.exp(-(dt - p.kernel_t[-1]) / p.tau_s)


def _g(c: Callable[[float], float], x: float) -> float:
    """Total walk cost x * c(x) in bp * L (0 at x <= 0, as r_sim's ``walk_cost``)."""
    return 0.0 if x <= 0 else x * c(x)


def walk_increment(c: Callable[[float], float], x1: float, x2: float) -> float:
    """Average per-share walk cost (bp) of the shares between x1 and x2 on one row (r_sim's ``cw``)."""
    if x2 <= x1:
        raise ValueError("walk_increment needs x2 > x1")
    return (_g(c, x2) - _g(c, x1)) / (x2 - x1)


def marginal_cost(c: Callable[[float], float], x: float, h: float = 1e-4) -> float:
    """m(x) = d(x c(x))/dx by finite difference (central where possible, forward near 0)."""
    if x <= h:
        return (_g(c, x + h) - _g(c, x)) / h
    return (_g(c, x + h) - _g(c, x - h)) / (2.0 * h)


def follow_on_and_impact(hist: Sequence[tuple[float, int, float]], tf: float, side: int, beta: float,
                         p: FillParams) -> tuple[float, float]:
    """(fo, im) in bp for a fill at time tf on ``side`` given our earlier fills ``(t_k, side_k, x_k)``.

    Exactly the loop of r_sim ``sim()`` in kernel mode: fills with ``dt <= 0`` or ``dt > cut`` are skipped;
    same-side fills within the follow-on window add ``beta * x``; older same-side fills add ``I(dt) * x``;
    opposite-side fills subtract ``I(dt) * x`` when ``impact_credit`` is on.
    """
    fo = 0.0
    im = 0.0
    for tk, sk, xk in hist:
        dt = tf - tk
        if dt <= 0 or dt > p.cut_s:
            continue
        if sk == side:
            if dt <= p.fo_window_s:
                if p.follow_on:
                    fo += beta * xk
            elif p.impact:
                im += impact_kernel(dt, p) * xk
        elif p.impact and p.impact_credit:
            im -= impact_kernel(dt, p) * xk
    return fo, im


def replenish_room(L: int, depth: float, rho: float, x_prev: float, filled_side_now: int) -> int:
    """Shares still available to us on this row and side (r_sim's ``room2``); may be <= 0."""
    xmax = max(0.0, depth - (1.0 - rho) * x_prev)
    return int(xmax * L) - int(filled_side_now)


def match_row(tau: float, prelim: float, final: float, close: float, p: FillParams) -> int:
    """Consensus row j on which an order arriving at round second ``tau`` is matched (section 4.E timing)."""
    rep = (prelim <= tau < prelim + 30.0) or (final <= tau < close)
    return int(math.floor(tau / 2.0 + 1e-9)) + 1 + p.lat_rows + (p.lat_rep_rows if rep else 0)


def fill_price(row_px: float, side: int, mult: float, cw: float, fo: float, im: float) -> float:
    """r_sim fill price ``rows[j] * (1 + side * mult * (cw + fo + im) * 1e-4)``."""
    return row_px * (1.0 + side * mult * (cw + fo + im) * 1e-4)


def limit_fill_qty(q: int, x1: float, L: int, c: Callable[[float], float], side: int, row_px: float, fo: float,
                   im: float, mult: float, limit: float | None, h: float = 1e-4) -> int:
    """Largest integer q* <= q whose marginal price is on the right side of ``limit`` (None = no limit).

    The marginal cost is monotone on every measured table, so a bisection finds q*; on a non-monotone stretch
    the result is still a quantity whose marginal price respects the limit, possibly not the largest one.
    """
    if q <= 0:
        return 0
    if limit is None:
        return q

    def ok(n: int) -> bool:
        mp = row_px * (1.0 + side * mult * (marginal_cost(c, x1 + n / L, h) + fo + im) * 1e-4)
        return mp <= limit + 1e-12 if side > 0 else mp >= limit - 1e-12

    if ok(q):
        return q
    lo, hi = 0, q
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo if (lo == 0 or ok(lo)) else 0


# ----------------------------------------------------------------------------------------------- state
@dataclass
class FillHistory:
    """Per-round taker-fill state that the formulas read.

    ``hist[sym]`` lists our taker fills ``(t, side, x)`` (x = shares / L) for follow-on and impact; ``side_rows``
    counts our taker shares per (symbol, row, side) for the walk increment and replenishment. Both reset at
    each round open, as in r_sim.
    """

    hist: dict[str, list[tuple[float, int, float]]] = field(default_factory=dict)
    side_rows: dict[tuple[str, int, int], int] = field(default_factory=dict)

    def reset(self) -> None:
        self.hist.clear()
        self.side_rows.clear()

    def filled_side(self, sym: str, row: int, side: int) -> int:
        return self.side_rows.get((sym, row, side), 0)

    def add(self, sym: str, row: int, t: float, side: int, q: int, L: int) -> None:
        if q <= 0:
            return
        self.side_rows[(sym, row, side)] = self.side_rows.get((sym, row, side), 0) + int(q)
        self.hist.setdefault(sym, []).append((float(t), int(side), q / float(L)))

    def fills(self, sym: str) -> list[tuple[float, int, float]]:
        return self.hist.get(sym, [])
