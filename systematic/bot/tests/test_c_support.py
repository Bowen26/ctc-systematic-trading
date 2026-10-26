"""Shared fakes for the group C unit tests (DESIGN.md section 9.1 C). Defines no TestCase.

Group C's tests must not depend on other groups' behaviour, so the collaborators from A and B are replaced here:
``FakeConfig`` (the default configuration of DESIGN.md section 8: ``config/default.json`` when present, otherwise
the JSON block of DESIGN.md itself), ``FakeSchedule`` (the research timing), ``FakeCase`` and builders for the
``ctcbot.types`` records that make up a ``StrategyContext``. ``FakeCost`` stands in for the cost model where a test
does not exercise it.
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT not in sys.path:
    sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402,F401  (path setup and data constants)
from ctcbot.types import (SYMBOLS, BookLevel, BookView, ClockReading, Command, EngineHealth,  # noqa: E402
                          FairValueSnapshot, FeedHealth, Fill, ListLog, NewsEvent, Phase, PriceQuality, RiskMode,
                          SeasonInfo, Side, StrategyContext, Trigger, WorkingSummary)

_MISSING = object()

RESEARCH_LIMITS = {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500, "MAT": 700,
                   "GEN": 600, "HAVEN": 250, "REIN": 200, "ARCH": 50, "REBLD": 100}
BASKETS = {"ARCH": {"PORT": 1, "ISLE": 1, "AQUA": 2, "GRID": 1, "FERRY": 2, "HAVEN": 1},
           "REBLD": {"RECON": 2, "MAT": 2, "GEN": 1}}
TIMING = {"pre_storm": 60, "storm": 480, "aftermath": 240, "prelim_report": 30, "final_report": 40}
MIDS = {"PORT": 45.0, "ISLE": 55.0, "AQUA": 27.0, "GRID": 36.0, "FERRY": 19.0, "RECON": 52.0, "MAT": 36.0,
        "GEN": 40.0, "HAVEN": 40.0, "REIN": 89.0, "ARCH": 290.0, "REBLD": 215.0}


def _design_default() -> dict:
    with open(os.path.join(BOT, "DESIGN.md")) as f:
        text = f.read()
    sec = text.index("## 8. `config/default.json`")
    a = text.index("```json", sec) + len("```json")
    b = text.index("```", a)
    return json.loads(text[a:b])


def default_dict() -> dict:
    """The section 8 configuration (``config/default.json``, else DESIGN.md's own block)."""
    p = os.path.join(BOT, "config", "default.json")
    try:
        with open(p) as f:
            d = json.load(f)
        if isinstance(d, dict) and "strategies" in d:
            return d
    except (OSError, ValueError):
        pass
    return _design_default()


_DEFAULT = default_dict()


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class FakeConfig:
    """Config stand-in: ``d``, ``get(dotted, default)``, ``path``, ``sym`` and a test-only ``set``."""

    def __init__(self, overrides: dict | None = None, **sets: object) -> None:
        self.d = deep_merge(_DEFAULT, overrides or {})
        self.d.pop("live_allowed", None)
        self.root = BOT
        self.hash = "fakecfg00000"
        self.sources = ("fake",)
        self.live_allowed = True
        for k, v in sets.items():
            self.set(k.replace("__", "."), v)

    def get(self, dotted: str, default: object = _MISSING) -> object:
        cur: object = self.d
        for k in dotted.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                if default is _MISSING:
                    raise KeyError(dotted)
                return default
        return cur

    def set(self, dotted: str, value: object) -> "FakeConfig":
        keys = dotted.split(".")
        cur = self.d
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
        cur[keys[-1]] = value
        return self

    def path(self, p: str | None) -> str | None:
        if p is None:
            return None
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.root, p))

    def sym(self, symbol: str) -> dict:
        return self.d["symbols"][symbol]


class FakeSchedule:
    """The research timing (open 0, storm 60-540, prelim 570, final 740, close 780) with the config cost phases."""

    def __init__(self, cfg: FakeConfig | None = None) -> None:
        self.open, self.storm_start, self.storm_end = 0.0, 60.0, 540.0
        self.prelim, self.final, self.close = 570.0, 740.0, 780.0
        self.round_len, self.speed = 780.0, 30.0
        cfg = cfg or FakeConfig()
        self.cost_phases = [(n, self.at(a), self.at(b)) for n, a, b in cfg.get("cost.phases")]

    def at(self, spec) -> float:
        if not isinstance(spec, (list, tuple)) or len(spec) != 2:
            raise ValueError(spec)
        return float(getattr(self, spec[0])) + float(spec[1])

    def phase(self, t):
        if t is None:
            return Phase.UNKNOWN
        if t < 0 or t >= self.close:
            return Phase.CLOSED
        if t < self.storm_start:
            return Phase.PRE_STORM
        if t < self.storm_end:
            return Phase.STORM
        return Phase.AFTERMATH

    def cost_phase(self, t: float) -> str:
        if not self.cost_phases or t < self.cost_phases[0][1]:
            return "open"
        for name, a, b in self.cost_phases:
            if a <= t < b:
                return name
        return "after"

    def advisory_times(self) -> list[float]:
        return [90.0 + 60.0 * k for k in range(8)]


class FakeCase:
    """CaseInfo stand-in with the research limits and baskets."""

    def __init__(self, limits: dict | None = None) -> None:
        self.raw: dict = {}
        self.symbols = tuple(SYMBOLS)
        self.limits = dict(limits or RESEARCH_LIMITS)
        self.baskets = copy.deepcopy(BASKETS)
        self.kinds = {s: ("etf" if s in BASKETS else "physical") for s in SYMBOLS}
        self.timing = dict(TIMING)


class FakeCost:
    """Cost model stand-in: a flat 20-bp walk and 15-bp half-spread."""

    def __init__(self, walk: float = 20.0, hs: float = 15.0) -> None:
        self.walk, self.hs = walk, hs
        self.calls: list[tuple] = []

    def walk_bp(self, symbol, phase, x, side=None):
        self.calls.append((symbol, phase, x, side))
        return self.walk if x > 0 else 0.0

    def half_spread_bp(self, symbol, phase):
        return self.hs

    def depth(self, symbol, phase):
        return 0.6

    def rho(self, symbol):
        return 0.5

    def entry_cost_bp(self, symbol, phase, clip_x):
        return 2.0 * (self.walk + (17.28 if symbol in BASKETS else 36.0))

    def L(self, symbol):
        return RESEARCH_LIMITS[symbol]


class FakeLawCheck:
    def __init__(self, passed: bool = True) -> None:
        self.ok = passed
        self.cleared: list[str] = []
        self.failed: set[str] = set()

    def passed(self, sym):
        return self.ok and sym not in self.failed

    def clear(self, sym):
        self.cleared.append(sym)
        if sym == "all":
            self.ok = True
            self.failed.clear()
        else:
            self.failed.discard(sym)


class FakeLaws:
    """LawsCrossCheck stand-in: ``values()`` from a dict (or None) and ``compare`` against per-symbol tolerances."""

    def __init__(self, values: dict | None, tol: float | dict = 0.02) -> None:
        self.vals = values
        self.tol = tol

    def values(self):
        return None if self.vals is None else dict(self.vals)

    def compare(self, S):
        out = {}
        for s in SYMBOLS:
            law = None if self.vals is None else self.vals.get(s)
            if law is None or s not in S:
                out[s] = (None, None)
                continue
            tol = self.tol.get(s, 0.02) if isinstance(self.tol, dict) else self.tol
            d = S[s] - law
            out[s] = (d, abs(d) <= tol + 1e-12)
        return out


def make_deps(cfg: FakeConfig | None = None, *, laws=None, law_check=None, cost=None, log=None, case=None):
    from ctcbot.strategies.base import StrategyDeps
    cfg = cfg or FakeConfig()
    return StrategyDeps(cfg=cfg, case=case or FakeCase(), schedule=FakeSchedule(cfg), cost=cost or FakeCost(),
                        laws=laws, law_check=law_check or FakeLawCheck(), log=log or ListLog())


# ------------------------------------------------------------------------------ record builders
def book(sym: str, mid: float | None, hs_bp: float = 15.0, *, bid: float | None = None, ask: float | None = None,
         wall: bool = False, qty: int = 50) -> BookView:
    if mid is not None and bid is None and ask is None:       # a one-cent tick grid, like the exchange
        bid = math.floor(mid * (1 - hs_bp / 1e4) * 100 + 1e-6) / 100
        ask = math.ceil(mid * (1 + hs_bp / 1e4) * 100 - 1e-6) / 100
    m = (bid + ask) / 2 if bid is not None and ask is not None else None
    hs = (ask - bid) / 2 / m * 1e4 if m else None
    bids = (BookLevel(bid, qty),) if bid is not None else ()
    asks = (BookLevel(ask, qty),) if ask is not None else ()
    return BookView(symbol=sym, bid=bid, ask=ask, bid_size=qty if bid is not None else None,
                    ask_size=qty if ask is not None else None, bids=bids, asks=asks, mid=m, half_spread_bp=hs,
                    age_s=0.1, empty=bid is None and ask is None,
                    wall_only=wall or (hs is not None and hs > 300.0), server_ts="2026-10-23T10:00:00")


def books(mids: dict | None = None, hs_bp: float = 15.0, **over: BookView) -> dict:
    out = {s: book(s, m, hs_bp) for s, m in (mids or MIDS).items()}
    out.update(over)
    return out


def fv(values: dict | None = None, *, round_: int | None = 1, seq_hi: int | None = 10, epoch: int = 0,
       ok: bool = True, diag: dict | None = None, computed: float = 0.0, version: int = 1,
       sd: float = 1.0) -> FairValueSnapshot:
    vals = {}
    for s in SYMBOLS:
        v = (values or {}).get(s, MIDS[s])
        vals[s] = v if isinstance(v, tuple) else (float(v), sd)
    return FairValueSnapshot(version=version, values=vals, season_epoch=epoch, round=round_, seq_hi=seq_hi,
                             msg_time=None, included_mono=computed, computed_mono=computed, compute_ms=10.0, ok=ok,
                             diag=diag or {}, engine_version=version)


def clock(t: float | None, round_: int | None = 1, *, epoch: int = 0, running: bool = True,
          quality: str = "feed", known: bool = True) -> ClockReading:
    sch = FakeSchedule()
    return ClockReading(known=known, season_epoch=epoch, comp_id="c1", round=round_, date="1802-05-10", t=t,
                        phase=sch.phase(t) if known else Phase.UNKNOWN, running=running, quality=quality,
                        anchor_age_s=0.5)


def health(oldest: float | None = None, alive: bool = True) -> EngineHealth:
    return EngineHealth(alive=alive, pending=0 if oldest is None else 1, oldest_pending_age_s=oldest,
                        last_compute_ms=10.0, errors=0, consecutive_errors=0, snapshot_fix=True)


def feed_health(obs_age: float | None = 0.5) -> FeedHealth:
    return FeedHealth(up=True, connections_up=2, connections=2, last_frame_age_s=0.1, last_news_age_s=0.5,
                      last_storm_obs_age_s=obs_age, round_max_age_gap_s=2.0, down_since_mono=None, dup_news=0,
                      seq_gaps=0, wm=100, max_seq=100)


def season(*, final: dict | None = None, final_seq: dict | None = None, loss_known: float = 0.0,
           case_diff: frozenset = frozenset(), coverage: dict | None = None, epoch: int = 0) -> SeasonInfo:
    return SeasonInfo(season_epoch=epoch, comp_id="c1", prelim={}, final=final or {}, final_seq=final_seq or {},
                      loss_known=loss_known, announcements=(), case_diff=case_diff, timing_diff=False,
                      coverage=coverage if coverage is not None else {1: "full", 2: "full", 3: "full"},
                      reset_pending=None)


def working(sym: str, buy: int = 0, sell: int = 0) -> WorkingSummary:
    return WorkingSummary(symbol=sym, buy_qty=buy, sell_qty=sell, clip_buy_qty=buy, clip_sell_qty=sell,
                          n_orders=1 if buy or sell else 0, clip_working=bool(buy or sell), keys=frozenset())


def news(mtype: str, msg: dict | None = None, *, seq: int | None = None, round_: int | None = None,
         now: float = 0.0) -> NewsEvent:
    m = dict(msg or {})
    m.setdefault("type", mtype)
    if seq is not None:
        m.setdefault("seq", seq)
    if round_ is not None:
        m.setdefault("round", round_)
    return NewsEvent(msg=m, mtype=mtype, seq=seq, round=round_, market_time=m.get("time"), season_epoch=0,
                     recv_mono=now)


def fill(sym: str, side: Side, qty: int, price: float, *, strategy: str = "h1", ref_mid: float | None = None,
         expected: float | None = 20.0, quality: PriceQuality = PriceQuality.EXACT, mono: float = 0.0,
         local_id: str | None = None) -> Fill:
    return Fill(local_id=local_id or f"x{sym}{mono}{qty}{price}", order_id=None, strategy=strategy, key=None,
                symbol=sym, side=side, qty=qty, price=price, quality=quality, mono=mono, ref_mid=ref_mid,
                expected_cost_bp=expected, source="ack")


def make_ctx(t: float | None, round_: int | None = 1, *, now: float | None = None, bk: dict | None = None,
             positions: dict | None = None, work: dict | None = None, fvs: FairValueSnapshot | None = None,
             engine: EngineHealth | None = None, feed: FeedHealth | None = None, seasoninfo: SeasonInfo | None = None,
             trigger: Trigger = Trigger.TICK, news_: tuple = (), fills: tuple = (), frozen=frozenset(),
             disabled=frozenset(), leadlag=frozenset(), limits: dict | None = None, cfg: FakeConfig | None = None,
             command: Command | None = None, **clk) -> StrategyContext:
    cfg = cfg or FakeConfig()
    lim = limits if limits is not None else {s: int(math.floor(RESEARCH_LIMITS[s] * float(cfg.get("sizing.scale"))
                                                              + 1e-9)) for s in SYMBOLS}
    return StrategyContext(now=now if now is not None else (t if t is not None else 0.0), trigger=trigger,
                           clock=clock(t, round_, **clk), schedule=FakeSchedule(cfg), limits=lim,
                           books=bk if bk is not None else books(), positions=positions or {},
                           working=work or {}, fv=fvs, engine=engine or health(), feed=feed or feed_health(),
                           season=seasoninfo or season(), risk_mode=RiskMode.NORMAL, disabled=frozenset(disabled),
                           frozen=frozenset(frozen), leadlag=frozenset(leadlag), news=tuple(news_), snapshots=(),
                           fills=tuple(fills), command=command, budget_take=100)


def targets(intents) -> dict:
    from ctcbot.types import TargetIntent
    return {i.symbol: i for i in intents if isinstance(i, TargetIntent)}
