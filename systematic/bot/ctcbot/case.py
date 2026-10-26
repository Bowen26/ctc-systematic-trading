"""Case data, case differences, strategy limits and the round schedule (DESIGN.md section 4.A ``case.py``).

The bot never hardcodes a time: every window is an anchor spec such as ``["storm_start", -10]`` resolved by
:meth:`Schedule.at` against the ``timing`` block of the *served* case.json. :func:`diff_case` compares the
served case with the research case the models were fitted on and reports value differences per symbol
apart from timing differences, which the live gate handles separately.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ctcbot.types import ETFS, SYMBOLS, Phase

if TYPE_CHECKING:  # pragma: no cover
    from ctcbot.config import Config

_TRADING_DAY_OPEN_S = 34200.0   # 09:30 in seconds of the market day
_MARKET_DAY_S = 23400.0         # 09:30-16:00


class CaseError(ValueError):
    """The case dict is missing a required part or has the wrong shape."""


@dataclass(frozen=True)
class CaseInfo:
    """The parts of case.json the bot uses. ``raw`` is the dict as served."""

    raw: dict
    symbols: tuple[str, ...]
    limits: dict[str, int]
    baskets: dict[str, dict[str, int]]
    kinds: dict[str, str]     # company kind from case ("physical" | "rebuild" | "insurer"); "etf" for ETFs
    timing: dict

    def __hash__(self) -> int:
        return id(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CaseInfo":
        """Build from a parsed case.json; raises :class:`CaseError` on a malformed case."""
        if not isinstance(d, dict):
            raise CaseError("case must be a JSON object")
        problems: list[str] = []
        companies = d.get("companies")
        etfs = d.get("etfs")
        limits_raw = d.get("limits")
        timing = d.get("timing")
        if not isinstance(companies, dict):
            problems.append("companies missing")
            companies = {}
        if not isinstance(etfs, dict):
            problems.append("etfs missing")
            etfs = {}
        if not isinstance(limits_raw, dict):
            problems.append("limits missing")
            limits_raw = {}
        if not isinstance(timing, dict):
            problems.append("timing missing")
            timing = {}
        for k in ("pre_storm", "storm", "aftermath", "prelim_report", "final_report"):
            v = timing.get(k)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0:
                problems.append(f"timing.{k} missing or not a number >= 0")
        limits: dict[str, int] = {}
        for sym, v in limits_raw.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or v != int(v):
                problems.append(f"limits.{sym} must be a non-negative integer")
            else:
                limits[str(sym)] = int(v)
        kinds: dict[str, str] = {}
        for sym, c in companies.items():
            kinds[str(sym)] = str(c.get("kind")) if isinstance(c, dict) else "unknown"
        baskets: dict[str, dict[str, int]] = {}
        for etf, e in etfs.items():
            kinds[str(etf)] = "etf"
            b = e.get("basket") if isinstance(e, dict) else None
            if not isinstance(b, dict):
                problems.append(f"etfs.{etf}.basket missing")
                continue
            try:
                baskets[str(etf)] = {str(k): int(v) for k, v in b.items()}
            except (TypeError, ValueError):
                problems.append(f"etfs.{etf}.basket weights must be integers")
        if problems:
            raise CaseError("bad case: " + "; ".join(problems))
        symbols = tuple(list(companies) + [e for e in etfs if e not in companies])
        return cls(raw=d, symbols=symbols, limits=limits, baskets=baskets, kinds=kinds, timing=dict(timing))

    @classmethod
    def from_file(cls, path: str) -> "CaseInfo":
        """Load a case.json file."""
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except OSError as e:
            raise CaseError(f"cannot read case file {path}: {e.strerror or e}") from None
        except ValueError as e:
            raise CaseError(f"case file {path} is not valid JSON: {e}") from None
        return cls.from_dict(d)


@dataclass(frozen=True)
class CaseDiff:
    """Differences of the served case against the research case."""

    symbols: frozenset[str]   # symbols whose values may differ (companies, limits, baskets, map)
    timing: bool              # case["timing"] differs
    lines: tuple[str, ...]    # readable differences for the log and the live prompt

    @property
    def clean(self) -> bool:
        return not self.symbols and not self.timing


def _short(v: Any, n: int = 120) -> str:
    s = json.dumps(v, sort_keys=True, default=str) if not isinstance(v, str) else repr(v)
    return s if len(s) <= n else s[: n - 3] + "..."


def _leaf_diffs(a: Any, b: Any, path: str, out: list[str], limit: int = 200) -> None:
    """Append ``path: a -> b`` lines for every differing leaf (dicts by key, lists by index)."""
    if len(out) >= limit:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}.{k}" if path else str(k)
            if k not in a:
                out.append(f"{p}: <absent> -> {_short(b[k])}")
            elif k not in b:
                out.append(f"{p}: {_short(a[k])} -> <absent>")
            else:
                _leaf_diffs(a[k], b[k], p, out, limit)
        return
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _leaf_diffs(x, y, f"{path}[{i}]", out, limit)
        return
    if _canon(a) != _canon(b):
        out.append(f"{path}: {_short(a)} -> {_short(b)}")


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def _etfs_containing(sym: str, *cases: dict) -> set[str]:
    out = set()
    for c in cases:
        etfs = c.get("etfs") if isinstance(c, dict) else None
        if isinstance(etfs, dict):
            for etf, e in etfs.items():
                b = e.get("basket") if isinstance(e, dict) else None
                if isinstance(b, dict) and sym in b:
                    out.add(str(etf))
    return out


def diff_case(served: dict, research: dict) -> CaseDiff:
    """Compare the served case with the research case.

    A per-company difference flags that symbol plus every ETF that contains it (in either case); a limit
    difference flags that symbol; a basket or other ETF difference flags that ETF; a difference in any other
    top-level key except ``timing`` (map, islands, outlooks, storm, ...) flags all 12. A ``timing`` difference
    sets ``timing=True`` and flags no symbol.
    """
    served = served if isinstance(served, dict) else {}
    research = research if isinstance(research, dict) else {}
    flagged: set[str] = set()
    lines: list[str] = []
    all12 = set(SYMBOLS)

    def section(name: str) -> tuple[dict, dict]:
        a, b = research.get(name), served.get(name)
        return (a if isinstance(a, dict) else {}), (b if isinstance(b, dict) else {})

    # companies
    ra, sa = section("companies")
    for sym in sorted(set(ra) | set(sa), key=str):
        if _canon(ra.get(sym)) != _canon(sa.get(sym)):
            lines_before = len(lines)
            _leaf_diffs(ra.get(sym, "<absent>"), sa.get(sym, "<absent>"), f"companies.{sym}", lines)
            if len(lines) == lines_before:
                lines.append(f"companies.{sym}: differs")
            if sym in all12:
                flagged.add(sym)
                flagged |= _etfs_containing(sym, served, research) & all12
            else:
                flagged |= all12
    # limits
    ra, sa = section("limits")
    for sym in sorted(set(ra) | set(sa), key=str):
        if _canon(ra.get(sym)) != _canon(sa.get(sym)):
            lines.append(f"limits.{sym}: {_short(ra.get(sym, '<absent>'))} -> {_short(sa.get(sym, '<absent>'))}")
            flagged |= {sym} & all12 if sym in all12 else all12
    # etfs
    ra, sa = section("etfs")
    for etf in sorted(set(ra) | set(sa), key=str):
        if _canon(ra.get(etf)) != _canon(sa.get(etf)):
            _leaf_diffs(ra.get(etf, "<absent>"), sa.get(etf, "<absent>"), f"etfs.{etf}", lines)
            flagged |= {etf} if etf in all12 else all12
    # timing
    ta, tb = research.get("timing"), served.get("timing")
    timing = _canon(ta) != _canon(tb)
    if timing:
        _leaf_diffs(ta, tb, "timing", lines)
    # everything else
    for key in sorted((set(research) | set(served)) - {"companies", "limits", "etfs", "timing"}, key=str):
        a, b = research.get(key, "<absent>"), served.get(key, "<absent>")
        if _canon(a) != _canon(b):
            n0 = len(lines)
            _leaf_diffs(a, b, str(key), lines)
            if len(lines) == n0:
                lines.append(f"{key}: differs")
            flagged |= all12
    return CaseDiff(symbols=frozenset(flagged), timing=timing, lines=tuple(lines))


def strategy_limits(case: CaseInfo, cfg: "Config") -> dict[str, int]:
    """Strategy limits L_s = int(case limit x sizing.scale) for every symbol (0 for a symbol the case lacks)."""
    scale = float(cfg.get("sizing.scale"))
    out = {sym: int(math.floor(case.limits.get(sym, 0) * scale + 1e-9)) for sym in SYMBOLS}
    for sym, lim in case.limits.items():
        if sym not in out:
            out[sym] = int(math.floor(lim * scale + 1e-9))
    return out


def clip_size(limit_scaled: int, div: int) -> int:
    """``max(1, int(limit_scaled / div))``: the only clip formula."""
    if div <= 0:
        raise ValueError(f"clip divisor must be positive, got {div}")
    return max(1, int(limit_scaled / div))


class Schedule:
    """Round timing in real seconds since the round's 09:30 open, from case.json ``timing``.

    ``open`` 0, ``storm_start`` = pre_storm, ``storm_end`` = pre_storm + storm, ``prelim`` = storm_end +
    prelim_report, ``close`` = ``round_len`` = pre_storm + storm + aftermath, ``final`` = close - final_report
    and ``speed`` = 23400 / round_len market seconds per real second. On the research timing these are
    0 / 60 / 540 / 570 / 740 / 780 and 30.
    """

    ANCHORS: tuple[str, ...] = ("open", "storm_start", "storm_end", "prelim", "final", "close")

    def __init__(self, timing: Mapping[str, float], cost_phases: Sequence[Sequence]) -> None:
        try:
            pre = float(timing["pre_storm"])
            storm = float(timing["storm"])
            after = float(timing["aftermath"])
            prelim_rep = float(timing.get("prelim_report", 30.0))
            final_rep = float(timing.get("final_report", 40.0))
        except (KeyError, TypeError, ValueError) as e:
            raise CaseError(f"bad case timing: {e}") from None
        if pre < 0 or storm <= 0 or after < 0:
            raise CaseError("bad case timing: pre_storm, storm and aftermath must be positive")
        self.timing: dict = dict(timing)
        self.open: float = 0.0
        self.storm_start: float = pre
        self.storm_end: float = pre + storm
        self.prelim: float = self.storm_end + prelim_rep
        self.close: float = pre + storm + after
        self.round_len: float = self.close
        self.final: float = self.close - final_rep
        self.speed: float = _MARKET_DAY_S / self.round_len
        self._obs_every = float(timing.get("obs_every", 2.0) or 2.0)
        self._adv_first = timing.get("advisory_first")
        self._adv_every = timing.get("advisory_every")
        self.cost_phases: list[tuple[str, float, float]] = []
        for ph in cost_phases:
            if not isinstance(ph, (list, tuple)) or len(ph) != 3:
                raise CaseError(f"bad cost phase {ph!r}")
            self.cost_phases.append((str(ph[0]), self.at(ph[1]), self.at(ph[2])))

    def at(self, spec: Sequence) -> float:
        """Resolve ``[anchor, offset]``: ``["storm_start", -10]`` -> 50.0 on the research timing."""
        if not isinstance(spec, (list, tuple)) or len(spec) != 2 or spec[0] not in self.ANCHORS:
            raise ValueError(f"bad anchor spec {spec!r}")
        return float(getattr(self, spec[0])) + float(spec[1])

    def phase(self, t: float | None) -> Phase:
        """pre_storm on [0, storm_start), storm on [storm_start, storm_end), aftermath on [storm_end, close)."""
        if t is None or not math.isfinite(t):
            return Phase.UNKNOWN
        if t < 0.0 or t >= self.close:
            return Phase.CLOSED
        if t < self.storm_start:
            return Phase.PRE_STORM
        if t < self.storm_end:
            return Phase.STORM
        return Phase.AFTERMATH

    def cost_phase(self, t: float) -> str:
        """Cost-table phase at ``t``: ``"open"`` before the first phase, ``"after"`` after the last."""
        ph = self.cost_phases
        if not ph or t < ph[0][1]:
            return "open"
        last_started = ph[0][0]
        for name, start, end in ph:
            if start <= t < end:
                return name
            if start <= t:
                last_started = name
        if t >= ph[-1][2]:
            return "after"
        return last_started

    def advisory_times(self) -> list[float]:
        """Real times of the official advisories: storm_start + advisory_first + k * advisory_every < storm_end."""
        try:
            first = float(self._adv_first)
            every = float(self._adv_every)
        except (TypeError, ValueError):
            return []
        if every <= 0:
            return []
        out, t = [], self.storm_start + first
        while t < self.storm_end - 1e-9:
            out.append(t)
            t += every
        return out

    @staticmethod
    def market_to_t(market_time: str, speed: float) -> tuple[str, float]:
        """``("1802-05-10", real s since 09:30)`` for an ISO market time without zone (any year >= 1).

        A trailing ``Z`` or UTC offset is stripped (the wall-clock fields are kept). Raises ValueError.
        """
        if not isinstance(market_time, str):
            raise ValueError(f"market time must be a string, got {type(market_time).__name__}")
        s = market_time.strip()
        if s.endswith(("Z", "z")):
            s = s[:-1]
        try:
            dt = _dt.datetime.fromisoformat(s)
        except ValueError:
            raise ValueError(f"bad market time {market_time[:40]!r}") from None
        secs = dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1e6
        return dt.date().isoformat(), (secs - _TRADING_DAY_OPEN_S) / speed

    def __repr__(self) -> str:
        return (f"Schedule(storm_start={self.storm_start}, storm_end={self.storm_end}, prelim={self.prelim}, "
                f"final={self.final}, close={self.close}, speed={self.speed})")


def is_etf(symbol: str) -> bool:
    """True for ARCH and REBLD."""
    return symbol in ETFS
