"""Measured testnet cost model (DESIGN.md section 4.C, ``models/cost_model.py``).

``CostModel`` reads ``testnet/analysis/books/cost_model.json`` (the books report's per-symbol, per-phase cost
tables) and exposes the same quantities that ``testnet/analysis/rebacktest/r_sim.py`` uses:

* ``walk_bp``: r_sim's ``walk_cost``: the average per-share cost (bp of the touch mid) of a clip of ``x * L``
  shares walking a fresh book, piecewise-linear in ``x`` over the phase ``table``; with a side it is multiplied by
  the buy/sell ratio curve interpolated over the measured clip points (10 sh, L/8, L/4, L/2, L);
* ``half_spread_bp``: r_sim's ``hs``, the table value at the smallest ``x`` (the S3 exit band);
* ``depth`` and ``rho``: the replenishment inputs of the research-mode fill model;
* ``entry_cost_bp``: the S3 entry-cost term ``thr_mult * (walk(clip_x) + fo_allow)``.

``x`` is always shares divided by the cost model's own limit ``L`` (the case limit at full size), see ``L()``.
The file is untrusted data: it is parsed defensively and a malformed symbol raises ``ValueError`` at load time.
"""
from __future__ import annotations

import bisect
import json
import math
from typing import TYPE_CHECKING, Any

from ctcbot.types import ETFS, SYMBOLS, Side

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from ctcbot.config import Config

#: Every cost phase the measured tables define (r_sim's ``PH_LIST``).
MODEL_PHASES: tuple[str, ...] = ("open", "pre", "h1_entry", "early", "h1_exit", "s3_start", "storm", "after",
                                 "rep_prelim", "rep_final")
#: The clip points whose buy/sell means give the side-specific ratio curve (r_sim ``prep``).
CLIP_POINTS: tuple[str, ...] = ("10sh", "L/8", "L/4", "L/2", "L")
#: Phase used when a caller asks for a phase the model does not have.
FALLBACK_PHASE = "storm"


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    """Piecewise-linear interpolation clamped at both ends (r_sim ``interp``)."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    k = bisect.bisect_right(xs, x)
    x0, x1 = xs[k - 1], xs[k]
    return ys[k - 1] + (ys[k] - ys[k - 1]) * (x - x0) / (x1 - x0)


def _side_sign(side: Side | int | str | None) -> int:
    """+1 for a buy, -1 for a sell, 0 for no side (mean table)."""
    if side is None:
        return 0
    if isinstance(side, Side):
        return side.sign
    if isinstance(side, str):
        s = side.lower()
        if s == "buy":
            return 1
        if s == "sell":
            return -1
        raise ValueError(f"unknown side {side!r}")
    return 1 if side > 0 else (-1 if side < 0 else 0)


class _PhaseTable:
    """One symbol and phase: the mean cost table, the buy/sell ratio curves, depth and half-spread."""

    __slots__ = ("xs", "ys", "rx", "rb", "rs", "depth", "hs")

    def __init__(self, raw: dict, limit: int) -> None:
        table = raw["table"]
        keys = sorted(table, key=float)
        if not keys:
            raise ValueError("empty cost table")
        self.xs: list[float] = [float(k) for k in keys]
        self.ys: list[float] = [float(table[k]) for k in keys]
        pts: list[tuple[float, float, float]] = []
        clips = raw.get("clips") or {}
        for name in CLIP_POINTS:
            c = clips.get(name)
            if not isinstance(c, dict):
                continue
            mean, bm, sm, q = c.get("mean"), c.get("buy_mean"), c.get("sell_mean"), c.get("q")
            if mean and bm is not None and sm is not None and q and float(mean) > 0:
                pts.append((float(q) / limit, float(bm) / float(mean), float(sm) / float(mean)))
        pts.sort()
        self.rx: list[float] = [p[0] for p in pts]
        self.rb: list[float] = [p[1] for p in pts]
        self.rs: list[float] = [p[2] for p in pts]
        d = raw.get("inner_depth_within_3pct_over_L_median")
        self.depth: float = float(d) if d is not None else 0.0
        self.hs: float = self.ys[0]


class CostModel:
    """Per-symbol, per-phase walk costs from the measured testnet books.

    Parameters
    ----------
    path: the ``cost_model.json`` file (``cfg.path(cfg.get("cost.model"))`` in production).
    cfg: the bot configuration; uses ``cost.thr_mult``, ``cost.fo_allow_bp``, ``cost.check_tolerance_bp``,
        ``cost.phases``, ``sizing.clip_div.s3`` and the per-symbol ``kind``, ``entry_cost_bp`` and ``exit_band_bp``.
    """

    def __init__(self, path: str, cfg: "Config") -> None:
        self.path = path
        self.cfg = cfg
        with open(path) as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"cost model {path}: top level is not an object")
        self._L: dict[str, int] = {}
        self._ph: dict[str, dict[str, _PhaseTable]] = {}
        self._rho: dict[str, float] = {}
        for s in SYMBOLS:
            d = raw.get(s)
            if not isinstance(d, dict):
                raise ValueError(f"cost model {path}: symbol {s} missing")
            try:
                limit = int(d["L"])
                if limit <= 0:
                    raise ValueError("L must be positive")
                phases = {ph: _PhaseTable(d[ph], limit) for ph in MODEL_PHASES if isinstance(d.get(ph), dict)}
                if FALLBACK_PHASE not in phases:
                    raise ValueError(f"phase {FALLBACK_PHASE} missing")
                rep = (d.get("replenish_storm") or {}).get("depth50_100") or {}
                rho = rep.get("within_2s")
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"cost model {path}: symbol {s}: {e}") from e
            self._L[s] = limit
            self._ph[s] = phases
            self._rho[s] = float(rho) if rho is not None else 0.0
        self.thr_mult = float(cfg.get("cost.thr_mult", 2.0))
        fo = cfg.get("cost.fo_allow_bp", {}) or {}
        self.fo_allow = {"stock": float(fo.get("stock", 36.0)), "etf": float(fo.get("etf", 17.28))}
        self.tolerance_bp = float(cfg.get("cost.check_tolerance_bp", 3.0))
        #: phase name -> number of lookups that fell back to ``FALLBACK_PHASE`` (reported by check_against_config)
        self.unknown_phase_hits: dict[str, int] = {}

    # ------------------------------------------------------------------ lookups
    def _table(self, symbol: str, phase: str) -> _PhaseTable:
        tabs = self._ph[symbol]          # KeyError for an unknown symbol, deliberately
        t = tabs.get(phase)
        if t is None:
            self.unknown_phase_hits[phase] = self.unknown_phase_hits.get(phase, 0) + 1
            t = tabs[FALLBACK_PHASE]
        return t

    def L(self, symbol: str) -> int:
        """The cost model's limit for ``symbol``: ``x`` in every lookup is shares divided by this."""
        return self._L[symbol]

    def kind(self, symbol: str) -> str:
        """``"etf"`` or ``"stock"``, from the config (falling back to the ETF list)."""
        try:
            k = self.cfg.sym(symbol).get("kind")
        except (KeyError, AttributeError, TypeError):
            k = None
        if k in ("etf", "stock"):
            return k
        return "etf" if symbol in ETFS else "stock"

    def walk_bp(self, symbol: str, phase: str, x: float, side: Side | int | str | None = None) -> float:
        """Average per-share cost (bp of touch mid) of a clip of ``x * L`` shares; r_sim's ``walk_cost``."""
        if not (x > 0) or math.isinf(x):
            return 0.0
        p = self._table(symbol, phase)
        c = _interp(p.xs, p.ys, x)
        sg = _side_sign(side)
        if sg != 0 and p.rx:
            c *= _interp(p.rx, p.rb if sg > 0 else p.rs, x)
        return c

    def half_spread_bp(self, symbol: str, phase: str) -> float:
        """The table value at the smallest ``x`` (r_sim ``hs``): the S3 exit band in bp."""
        return self._table(symbol, phase).hs

    def depth(self, symbol: str, phase: str) -> float:
        """Median inner depth within 3% of mid, in units of L (``inner_depth_within_3pct_over_L_median``)."""
        return self._table(symbol, phase).depth

    def rho(self, symbol: str) -> float:
        """Share of the depth within 50 bp refilled within 2 s in the storm (``replenish_storm.depth50_100``)."""
        return self._rho[symbol]

    def entry_cost_bp(self, symbol: str, phase: str, clip_x: float) -> float:
        """S3 entry-cost term in bp: ``thr_mult * (walk(clip_x) + fo_allow)`` (mean table, no side)."""
        return self.thr_mult * (self.walk_bp(symbol, phase, clip_x) + self.fo_allow[self.kind(symbol)])

    # ------------------------------------------------------------------ startup check
    def check_against_config(self) -> list[str]:
        """Readable differences between the model and the config tables beyond ``cost.check_tolerance_bp``.

        Entry costs use an S3 clip of ``1 / sizing.clip_div.s3`` (L/12) and the exit bands the half-spread,
        exactly as ``writer/w05_params.py`` derived the config tables. Configured cost phases that the model does
        not have are listed too, because lookups for them fall back to the storm table.
        """
        out: list[str] = []
        try:
            div = int(self.cfg.get("sizing.clip_div.s3", 12))
        except (TypeError, ValueError):
            div = 12
        clip_x = 1.0 / max(1, div)
        tol = self.tolerance_bp
        for s in SYMBOLS:
            try:
                sc = self.cfg.sym(s)
            except (KeyError, AttributeError, TypeError):
                out.append(f"{s}: no symbol section in the config")
                continue
            for ph, v in sorted((sc.get("entry_cost_bp") or {}).items()):
                model = self.entry_cost_bp(s, ph, clip_x)
                if not isinstance(v, (int, float)) or abs(model - float(v)) > tol:
                    out.append(f"{s} entry_cost_bp.{ph}: config {v} vs model {model:.1f}")
            for ph, v in sorted((sc.get("exit_band_bp") or {}).items()):
                model = self.half_spread_bp(s, ph)
                if not isinstance(v, (int, float)) or abs(model - float(v)) > tol:
                    out.append(f"{s} exit_band_bp.{ph}: config {v} vs model {model:.1f}")
        for entry in self.cfg.get("cost.phases", []) or []:
            name = entry[0] if isinstance(entry, (list, tuple)) and entry else None
            if isinstance(name, str) and name not in MODEL_PHASES:
                out.append(f"cost.phases: phase {name!r} is not in the cost model (falls back to {FALLBACK_PHASE})")
        for ph, n in sorted(self.unknown_phase_hits.items()):
            out.append(f"lookups for unknown phase {ph!r}: {n} (fell back to {FALLBACK_PHASE})")
        return out

    def describe(self) -> dict[str, Any]:
        """Small summary for logs: path, thresholds and the per-symbol limits."""
        return {"path": self.path, "thr_mult": self.thr_mult, "fo_allow_bp": dict(self.fo_allow),
                "L": dict(self._L)}
