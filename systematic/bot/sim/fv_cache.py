"""``CachedFairValues``: a ``FairValueProvider`` over the engine fair-value caches (DESIGN.md section 4.E).

The caches (``SP/work/se/backtest/cache/fv_<lib>_NNN.json``, built by the team backtest's ``fvcache.py``) hold one
engine evaluation after every distinct message time: ``{"season", "lib", "symbols", "entries": [[round, t,
mu x 12, sd x 12], ...]}``. Like ``backtest.Season.fair_at``:

* per round, entries are ordered by t, and every round after the first carries the previous round's last value in
  at ``t = -1``;
* ``submit`` tracks the newest (round, t) of the submitted messages (``t`` = real seconds since that round's 09:30,
  from the message's market time) and publishes the entry with the largest ``t_e <= t`` of that round.

A snapshot is published whenever the selected entry, the highest submitted seq (``seq_hi``) or an adjustment
changes, so S1-r3 sees an fv with ``seq_hi >= final_seq`` right after the final report. ``diag`` is empty, the
listener is called synchronously inside ``submit``, and ``health`` is always fresh.

``set_adjustment`` applies the engine's rule ``v -> mult * v + add`` to the mean (sd scales by ``|mult|``); with
``baskets`` given, an ETF also moves by its basket's adjusted components, as the engine rebuilds ETFs from the
adjusted stocks. ``override`` is recorded and logged but cannot change cached values.
"""
from __future__ import annotations

import bisect
import json
import math
import os
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from ctcbot.types import SYMBOLS, EngineHealth, EventLog, FairValueSnapshot

if TYPE_CHECKING:  # pragma: no cover
    from ctcbot.case import Schedule

__all__ = ["CachedFairValues", "cache_file"]

TRADING_PHASES = ("pre_storm", "storm", "aftermath")


def cache_file(cache_dir: str, season: int, libs: Sequence[str] = ("holdout", "loso")) -> str:
    """Path of the cache for ``season`` in ``cache_dir`` (``fv_holdout_NNN.json`` first, then ``fv_loso_NNN.json``)."""
    for lib in libs:
        p = os.path.join(cache_dir, f"fv_{lib}_{int(season):03d}.json")
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"no fair-value cache for season {season} in {cache_dir}")


class CachedFairValues:
    """Fair values replayed from an engine cache (see the module docstring).

    ``CachedFairValues(cache_path, schedule, season=None, baskets=None, log=None)``; ``cache_path`` may be a cache
    directory, in which case ``season`` selects the file.
    """

    def __init__(self, cache_path: str, schedule: "Schedule", *, season: int | None = None,
                 baskets: Mapping[str, Mapping[str, int]] | None = None, log: EventLog | None = None) -> None:
        if os.path.isdir(cache_path):
            if season is None:
                raise ValueError("a cache directory needs a season")
            cache_path = cache_file(cache_path, season)
        self.path = cache_path
        self.schedule = schedule
        self.speed = float(schedule.speed)
        self.log = log
        self.baskets = {e: dict(b) for e, b in (baskets or {}).items()}
        with open(cache_path, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("entries"), list):
            raise ValueError(f"{cache_path}: not a fair-value cache")
        syms = list(d.get("symbols") or SYMBOLS)
        if sorted(syms) != sorted(SYMBOLS):
            raise ValueError(f"{cache_path}: symbols {syms} differ from the 12 SYMBOLS")
        idx = [syms.index(s) for s in SYMBOLS]
        n = len(syms)
        self.season = d.get("season")
        self.fv: dict[int, list[tuple[float, tuple[float, ...], tuple[float, ...]]]] = {1: [], 2: [], 3: []}
        last = None
        for e in d["entries"]:
            if not isinstance(e, list) or len(e) < 2 + 2 * n:
                continue
            r, t = int(e[0]), float(e[1])
            if r not in self.fv:
                continue
            mu = tuple(float(e[2 + k]) for k in idx)
            sd = tuple(float(e[2 + n + k]) for k in idx)
            if r > 1 and not self.fv[r] and last is not None:
                self.fv[r].append((-1.0, last[0], last[1]))
            self.fv[r].append((t, mu, sd))
            last = (mu, sd)
        if not self.fv[1]:
            raise ValueError(f"{cache_path}: no round-1 entries")
        for r in (2, 3):
            if not self.fv[r]:
                self.fv[r].append((-1.0, last[0], last[1]))    # type: ignore[index]
        self.fvt = {r: [x[0] for x in self.fv[r]] for r in (1, 2, 3)}
        self._listener: Callable[[], None] | None = None
        self._version = 0
        self._engine_version = 0
        self._epoch = 0
        self._reset_state()
        self._adjust: dict[str, tuple[float, float]] = {}
        self.overrides: dict[str, Any] = {}
        self.submitted = 0

    def _reset_state(self) -> None:
        self._latest: FairValueSnapshot | None = None
        self._newest: tuple[int, float] | None = None
        self._newest_time: str | None = None
        self._included_mono = -math.inf
        self._seq_hi: int | None = None
        self._key: tuple | None = None

    # -- FairValueProvider
    def set_listener(self, cb: Callable[[], None]) -> None:
        self._listener = cb

    def fair_at(self, r: int, t: float) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """(mu, sd) of round r at round second t, exactly as ``backtest.Season.fair_at``."""
        i = bisect.bisect_right(self.fvt[r], t + 1e-9) - 1
        x = self.fv[r][max(i, 0)]
        return x[1], x[2]

    def submit(self, msgs: Sequence[dict], recv_mono: float) -> None:
        got = False
        for m in msgs:
            if not isinstance(m, dict):
                continue
            typ = m.get("type")
            seq = m.get("last_seq") if typ == "snapshot" else m.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0:
                self._seq_hi = seq if self._seq_hi is None else max(self._seq_hi, seq)
            if typ == "snapshot" and m.get("phase") not in TRADING_PHASES:
                continue
            try:
                r = int(m.get("round"))
                _, t = self.schedule.market_to_t(str(m.get("time")), self.speed)
            except (TypeError, ValueError):
                continue
            if r not in self.fv:
                continue
            got = True
            self.submitted += 1
            key = (r, t)
            if self._newest is None or key >= self._newest:
                self._newest = key
                self._newest_time = str(m.get("time"))
            self._included_mono = float(recv_mono)
        if got:
            self._maybe_publish(float(recv_mono))

    def _values(self) -> dict[str, tuple[float, float]]:
        assert self._newest is not None
        mu, sd = self.fair_at(*self._newest)
        vals = {s: (mu[k], sd[k]) for k, s in enumerate(SYMBOLS)}
        if not self._adjust:
            return vals
        out = dict(vals)
        for s, (mult, add) in self._adjust.items():
            if s in out and s not in self.baskets:
                m0, s0 = vals[s]
                out[s] = (mult * m0 + add, abs(mult) * s0)
        for e, basket in self.baskets.items():
            if e not in out:
                continue
            m0, s0 = vals[e]
            m1 = m0 + sum(w * (out[c][0] - vals[c][0]) for c, w in basket.items() if c in vals)
            if e in self._adjust:
                mult, add = self._adjust[e]
                m1, s0 = mult * m1 + add, abs(mult) * s0
            out[e] = (m1, s0)
        return out

    def _maybe_publish(self, now: float, force: bool = False) -> None:
        if self._newest is None:
            return
        r, t = self._newest
        i = max(0, bisect.bisect_right(self.fvt[r], t + 1e-9) - 1)
        entry = (r, i)
        key = (entry, self._seq_hi, tuple(sorted(self._adjust.items())))
        if not force and key == self._key:
            return
        if self._key is None or self._key[0] != entry:
            self._engine_version += 1
        self._key = key
        self._version += 1
        self._latest = FairValueSnapshot(
            version=self._version, values=self._values(), season_epoch=self._epoch, round=r, seq_hi=self._seq_hi,
            msg_time=self._newest_time, included_mono=self._included_mono, computed_mono=now, compute_ms=0.0,
            ok=True, error=None, diag={}, engine_version=self._engine_version)
        if self._listener is not None:
            self._listener()

    def latest(self) -> FairValueSnapshot | None:
        return self._latest

    def health(self, now: float) -> EngineHealth:
        return EngineHealth(alive=True, pending=0, oldest_pending_age_s=None, last_compute_ms=0.0, errors=0,
                            consecutive_errors=0, snapshot_fix=True)

    def set_adjustment(self, symbol: str, mult: float = 1.0, add: float = 0.0) -> None:
        if symbol not in SYMBOLS:
            return
        if float(mult) == 1.0 and float(add) == 0.0:
            self._adjust.pop(symbol, None)
        else:
            self._adjust[symbol] = (float(mult), float(add))
        if self.log is not None:
            self.log.event("fv_cache_adjust", symbol=symbol, mult=float(mult), add=float(add))
        self._maybe_publish(self._latest.computed_mono if self._latest is not None else 0.0)

    def override(self, dotted: str, value: Any) -> None:
        self.overrides[str(dotted)] = value
        if self.log is not None:
            self.log.event("fv_cache_override_ignored", key=str(dotted))

    def reset_season(self, season_epoch: int, persist_path: str | None) -> None:
        self._epoch = int(season_epoch)
        self._engine_version = 0
        self._adjust.clear()
        self.overrides.clear()
        self._reset_state()

    def close(self) -> None:
        self._listener = None
