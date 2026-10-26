"""Configuration: load ``config/default.json``, apply overlays and ``--set`` items, validate, hash.

DESIGN.md section 4.A ``config.py`` and section 8. The configuration is plain JSON. Overlays merge into the
base in order (dicts recursively; lists and scalars replace). Each ``--set`` item is ``a.b.c=VALUE`` with
VALUE parsed as JSON, falling back to a string; a numeric path component indexes a list
(``feed.connections.1.local_addr=["192.168.1.20", 0]``). A ``--set`` path must already exist, so a typo on
competition day fails loudly instead of adding an unused key.

``live_allowed`` is a top-level boolean that any file may carry. It is removed from each file before merging
and never merged: ``Config.live_allowed`` is the AND over the loaded files, so one sim-only profile makes a
run ineligible for ``--live`` whatever comes after it.

Validation raises :class:`ConfigError` listing every problem found, not just the first.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ctcbot.types import SYMBOLS

BOT_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH: str = os.path.join(BOT_ROOT, "config", "default.json")

ANCHORS: frozenset[str] = frozenset({"open", "storm_start", "storm_end", "prelim", "final", "close"})
STRATEGIES: tuple[str, ...] = ("h1", "s3", "flatten", "s1r3", "rein_ask", "etf_leadlag", "announce")
FLOOR_NAMES: tuple[str, ...] = ("KILL", "SNIPE", "HEDGE", "TAKE")
CLIP_DIVS: tuple[str, ...] = ("s3", "h1", "h1_exit", "flatten", "flatten_max", "leadlag")

# Every anchor spec of default.json, by dotted path. Validation also scans the strategy, risk and guard
# sections for any other [str, number] pair, so an overlay that adds an anchor key is checked as well.
ANCHOR_PATHS: tuple[str, ...] = (
    "risk.block_marketable_before",
    "guards.opening.until",
    "strategies.h1.ref_mid_at", "strategies.h1.entry_start", "strategies.h1.entry_last",
    "strategies.h1.check_t0", "strategies.h1.check_t1", "strategies.h1.fallback_deadline",
    "strategies.h1.fallback_start", "strategies.h1.fallback_last",
    "strategies.s3.start", "strategies.s3.end",
    "strategies.flatten.start", "strategies.flatten.end", "strategies.flatten.behind_alert_at",
    "strategies.s1r3.fast_until", "strategies.s1r3.sweep_end", "strategies.s1r3.sanity_at",
    "strategies.s1r3.block_from", "strategies.s1r3.prewarm_at", "strategies.s1r3.announce_lock_from",
    "strategies.s1r3.late_trigger_ok_until",
    "strategies.rein_ask.start", "strategies.rein_ask.end_r12", "strategies.rein_ask.end_r3",
    "strategies.etf_leadlag.start", "strategies.etf_leadlag.end",
)

_MISSING: Any = object()

#: Packet section 8: 180 order actions per minute for each team.
TEAM_ACTIONS_MAX: int = 180
#: Hardening bound on the KILL overdraft (DESIGN.md §13).
KILL_OVERDRAFT_MAX: int = 20
#: Live-run bounds (``live_bound_problems``): the safety bands may be tightened freely but loosened only this far.
LIVE_BOUNDS: dict[str, tuple[float, float]] = {
    "risk.max_marketable_from_mid": (0.0, 0.03),
    "risk.fat_finger_frac": (0.0, 0.50),
    "risk.snipe_band_frac": (0.0, 0.80),
    "risk.max_clip_frac": (0.0, 1.0),
    "risk.max_snipe_frac": (0.0, 2.0),
    "risk.max_resting_per_symbol": (1, 16),
    "risk.drawdown_usd": (1.0, 40000.0),
    "risk.fill_sanity_n": (1, 10),
}


class ConfigError(Exception):
    """A configuration file, overlay or ``--set`` item is unusable. The message lists every problem."""

    def __init__(self, problems: str | Sequence[str]) -> None:
        self.problems: list[str] = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems) if len(self.problems) == 1
                         else f"{len(self.problems)} config problems: " + "; ".join(self.problems))


@dataclass(frozen=True)
class Config:
    """The effective configuration of one run.

    ``d`` is the merged dict (treat it as read-only), ``hash`` the first 12 hex digits of the sha256 of its
    canonical JSON, ``root`` the directory relative paths resolve against (BOT), ``sources`` the loaded files
    followed by the ``--set`` items, and ``live_allowed`` the AND of every loaded file's ``live_allowed``.
    """

    d: dict
    hash: str
    root: str
    sources: tuple[str, ...]
    live_allowed: bool

    def __hash__(self) -> int:  # the dict field is unhashable; identity of the effective config is its hash
        return hash((self.hash, self.root, self.sources, self.live_allowed))

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        """Value at ``a.b.c`` (numeric components index lists). KeyError if missing and no default."""
        try:
            return _walk(self.d, dotted)
        except KeyError:
            if default is _MISSING:
                raise KeyError(dotted) from None
            return default

    def path(self, p: str | None) -> str | None:
        """Resolve a configured path: None stays None, absolute paths stay, relative ones join ``root``."""
        if p is None:
            return None
        if not isinstance(p, str) or not p:
            raise ConfigError(f"bad path value {p!r}")
        if os.path.isabs(p):
            return os.path.normpath(p)
        return os.path.normpath(os.path.join(self.root, p))

    def sym(self, symbol: str) -> dict:
        """The per-symbol section ``d["symbols"][symbol]``."""
        return self.d["symbols"][symbol]


# ----------------------------------------------------------------------------------------------- helpers
def _split(dotted: str) -> list[str]:
    if not isinstance(dotted, str) or not dotted or any(p == "" for p in dotted.split(".")):
        raise KeyError(dotted)
    return dotted.split(".")


def _walk(d: Any, dotted: str) -> Any:
    cur = d
    for part in _split(dotted):
        if isinstance(cur, dict):
            if part not in cur:
                raise KeyError(dotted)
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise KeyError(dotted) from None
        else:
            raise KeyError(dotted)
    return cur


def deep_merge(base: dict, over: dict) -> dict:
    """Return a new dict: ``over`` merged into ``base``. Dicts merge recursively; lists and scalars replace.

    Neither input is modified.
    """
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e.strerror or e}") from None
    except ValueError as e:
        raise ConfigError(f"config file {path} is not valid JSON: {e}") from None
    if not isinstance(d, dict):
        raise ConfigError(f"config file {path} must hold a JSON object")
    return d


def _pop_live_allowed(d: dict, path: str) -> bool:
    if "live_allowed" not in d:
        return True
    v = d.pop("live_allowed")
    if not isinstance(v, bool):
        raise ConfigError(f"{path}: live_allowed must be true or false, got {v!r}")
    return v


def _parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _apply_set(d: dict, item: str) -> None:
    if not isinstance(item, str) or "=" not in item:
        raise ConfigError(f"--set item {item!r} is not of the form a.b.c=VALUE")
    key, _, raw = item.partition("=")
    key = key.strip()
    try:
        parts = _split(key)
    except KeyError:
        raise ConfigError(f"--set item {item!r} has an empty key") from None
    if parts[0] == "live_allowed":
        raise ConfigError("live_allowed cannot be changed with --set; use a profile file")
    value = _parse_value(raw)
    cur: Any = d
    for i, part in enumerate(parts[:-1]):
        try:
            cur = cur[part] if isinstance(cur, dict) else cur[int(part)]
        except (KeyError, ValueError, IndexError, TypeError):
            raise ConfigError(f"--set {key}: unknown config key {'.'.join(parts[:i + 1])}") from None
    last = parts[-1]
    if isinstance(cur, dict):
        if last not in cur:
            raise ConfigError(f"--set {key}: unknown config key")
        cur[last] = value
    elif isinstance(cur, list):
        try:
            cur[int(last)] = value
        except (ValueError, IndexError):
            raise ConfigError(f"--set {key}: bad list index {last!r}") from None
    else:
        raise ConfigError(f"--set {key}: parent is not an object or a list")


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _anchor_problem(path: str, v: Any) -> str | None:
    if (not isinstance(v, (list, tuple)) or len(v) != 2 or not isinstance(v[0], str) or v[0] not in ANCHORS
            or not _is_num(v[1])):
        return f"{path}: anchor spec must be [anchor, number] with anchor in {sorted(ANCHORS)}, got {v!r}"
    return None


def _scan_pairs(d: Any, prefix: str) -> Iterable[tuple[str, Any]]:
    """Every [str, number] pair below ``d``: candidate anchor specs."""
    if isinstance(d, dict):
        for k, v in d.items():
            yield from _scan_pairs(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(d, list):
        if len(d) == 2 and isinstance(d[0], str) and _is_num(d[1]):
            yield prefix, d


def _url_problem(name: str, v: Any, scheme: str) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str) or not v.startswith(scheme + "://") or len(v) <= len(scheme) + 3:
        return f"{name} must start with {scheme}:// (got {v!r})"
    return None


def validate(d: dict) -> list[str]:
    """Return every problem of the merged config ``d`` (an empty list means valid)."""
    p: list[str] = []

    def get(dotted: str) -> Any:
        try:
            return _walk(d, dotted)
        except KeyError:
            p.append(f"{dotted}: missing")
            return _MISSING

    # symbols
    syms = d.get("symbols")
    if not isinstance(syms, dict):
        p.append("symbols: missing or not an object")
    else:
        missing = [s for s in SYMBOLS if s not in syms]
        extra = sorted(s for s in syms if s not in SYMBOLS)
        if missing:
            p.append(f"symbols: missing {missing}")
        if extra:
            p.append(f"symbols: unknown {extra}")
        for s in SYMBOLS:
            if s in syms and not isinstance(syms[s], dict):
                p.append(f"symbols.{s}: not an object")
            elif s in syms and syms[s].get("kind") not in ("stock", "etf"):
                p.append(f"symbols.{s}.kind must be 'stock' or 'etf'")

    # strategy sections
    strategies = d.get("strategies")
    if not isinstance(strategies, dict):
        p.append("strategies: missing or not an object")
    else:
        for name in STRATEGIES:
            sec = strategies.get(name)
            if not isinstance(sec, dict):
                p.append(f"strategies.{name}: missing section")
            elif not isinstance(sec.get("enabled"), bool):
                p.append(f"strategies.{name}.enabled must be true or false")

    # anchors: the known paths, then any other [str, number] pair in the sections that hold anchors
    seen: set[str] = set()
    for path in ANCHOR_PATHS:
        try:
            v = _walk(d, path)
        except KeyError:
            p.append(f"{path}: missing anchor spec")
            continue
        seen.add(path)
        prob = _anchor_problem(path, v)
        if prob:
            p.append(prob)
    for section in ("strategies", "risk", "guards"):
        for path, v in _scan_pairs(d.get(section), section):
            if path not in seen:
                prob = _anchor_problem(path, v)
                if prob:
                    p.append(prob)
    phases = get("cost.phases")
    if phases is not _MISSING:
        if not isinstance(phases, list) or not phases:
            p.append("cost.phases must be a non-empty list")
        else:
            for i, ph in enumerate(phases):
                if not isinstance(ph, list) or len(ph) != 3 or not isinstance(ph[0], str):
                    p.append(f"cost.phases.{i}: must be [name, start_spec, end_spec]")
                    continue
                for j in (1, 2):
                    prob = _anchor_problem(f"cost.phases.{i}.{j}", ph[j])
                    if prob:
                        p.append(prob)

    # rate
    floors = get("rate.floors")
    if floors is not _MISSING:
        if not isinstance(floors, dict):
            p.append("rate.floors must be an object")
        else:
            for name in FLOOR_NAMES:
                if not _is_int(floors.get(name)) or floors.get(name) < 0:
                    p.append(f"rate.floors.{name} must be an int >= 0")
            for name in floors:
                if name not in FLOOR_NAMES:
                    p.append(f"rate.floors.{name}: unknown priority")
    cap, team = get("rate.bot_cap_per_min"), get("exchange.team_actions_per_min")
    ko = get("rate.kill_overdraft")
    if cap is not _MISSING and team is not _MISSING:
        if not _is_int(cap) or not _is_int(team) or cap < 1:
            p.append("rate.bot_cap_per_min and exchange.team_actions_per_min must be positive ints")
        elif team > TEAM_ACTIONS_MAX:
            p.append(f"exchange.team_actions_per_min {team} exceeds the packet's team limit {TEAM_ACTIONS_MAX}")
        else:
            od = ko if _is_int(ko) and ko >= 0 else 0
            if cap + od > team - 10:
                p.append(f"rate.bot_cap_per_min {cap} + rate.kill_overdraft {od} exceeds "
                         f"exchange.team_actions_per_min - 10 = {team - 10}")
    for key in ("rate.window_s", "rate.throttle_freeze_s"):
        v = get(key)
        if v is not _MISSING and (not _is_num(v) or v <= 0):
            p.append(f"{key} must be a positive number")
    if ko is not _MISSING and (not _is_int(ko) or not 0 <= ko <= KILL_OVERDRAFT_MAX):
        p.append(f"rate.kill_overdraft must be an int in [0, {KILL_OVERDRAFT_MAX}]")
    rps = get("rate.requests_per_s_cap")
    if rps is not _MISSING and (not _is_int(rps) or rps < 1):
        p.append("rate.requests_per_s_cap must be a positive int")

    # S3 edge reference and its limit pairing
    edge_ref, lcf = get("strategies.s3.edge_ref"), get("strategies.s3.limit_cost_frac")
    if edge_ref is not _MISSING and lcf is not _MISSING:
        if edge_ref not in ("touch", "mid"):
            p.append(f"strategies.s3.edge_ref must be 'touch' or 'mid', got {edge_ref!r}")
        elif not _is_num(lcf):
            p.append("strategies.s3.limit_cost_frac must be a number")
        elif edge_ref == "touch" and lcf != 1.0:
            p.append(f"strategies.s3.limit_cost_frac must be 1.0 with edge_ref 'touch', got {lcf}")
        elif edge_ref == "mid" and not 0.0 <= lcf <= 0.5:
            p.append(f"strategies.s3.limit_cost_frac must be in [0, 0.5] with edge_ref 'mid', got {lcf}")

    # feed connections
    conns = get("feed.connections")
    if conns is not _MISSING:
        if not isinstance(conns, list) or not 1 <= len(conns) <= 4:
            p.append("feed.connections must have 1 to 4 entries")
        else:
            names: list[str] = []
            for i, c in enumerate(conns):
                if not isinstance(c, dict) or set(c) - {"name", "local_addr"} or "name" not in c:
                    p.append(f"feed.connections.{i}: must be {{\"name\": str, \"local_addr\": null | [ip, port]}}")
                    continue
                if not isinstance(c["name"], str) or not c["name"] or "#" in c["name"]:
                    p.append(f"feed.connections.{i}.name must be a non-empty string without '#'")
                else:
                    names.append(c["name"])
                la = c.get("local_addr")
                if la is not None and not (isinstance(la, list) and len(la) == 2 and isinstance(la[0], str)
                                           and _is_int(la[1]) and 0 <= la[1] <= 65535):
                    p.append(f"feed.connections.{i}.local_addr must be null or [ip, port]")
            if len(names) != len(set(names)):
                p.append(f"feed.connections: duplicate names {sorted(n for n in set(names) if names.count(n) > 1)}")
    chans = get("feed.channels")
    if chans is not _MISSING and (not isinstance(chans, list) or not chans
                                  or not all(isinstance(c, str) for c in chans)):
        p.append("feed.channels must be a non-empty list of strings")

    # sizing
    scale = get("sizing.scale")
    if scale is not _MISSING and (not _is_num(scale) or not 0.0 < scale <= 1.0):
        p.append(f"sizing.scale must be in (0, 1], got {scale!r}")
    divs = get("sizing.clip_div")
    if divs is not _MISSING:
        if not isinstance(divs, dict):
            p.append("sizing.clip_div must be an object")
        else:
            for name in CLIP_DIVS:
                v = divs.get(name)
                if not _is_int(v) or v < 1:
                    p.append(f"sizing.clip_div.{name} must be a positive int")

    # exchange
    for name, scheme in (("exchange.rest_base", "https"), ("exchange.case_url", "https"), ("exchange.ws_url", "wss")):
        v = get(name)
        if v is not _MISSING:
            prob = _url_problem(name, v, scheme)
            if prob:
                p.append(prob)
    ua = get("exchange.user_agent")
    if ua is not _MISSING and (not isinstance(ua, str) or not ua.strip() or ua.lower().startswith("python-urllib")
                               or any(ch in ua for ch in "\r\n")):
        p.append("exchange.user_agent must be a custom one-line string (urllib's default is refused)")
    ke = get("exchange.key_env")
    if ke is not _MISSING and (not isinstance(ke, str) or not ke):
        p.append("exchange.key_env must be a non-empty string")
    for key in ("exchange.timeout_order_s", "exchange.timeout_get_s", "exchange.idle_recycle_s"):
        v = get(key)
        if v is not _MISSING and (not _is_num(v) or v <= 0):
            p.append(f"{key} must be a positive number")
    for key in ("exchange.workers", "exchange.price_decimals"):
        v = get(key)
        if v is not _MISSING and (not _is_int(v) or v < 1):
            p.append(f"{key} must be a positive int")
    kw = get("exchange.kill_workers")
    if kw is not _MISSING and (not _is_int(kw) or not 1 <= kw <= 4):
        p.append("exchange.kill_workers must be an int in [1, 4] (the kill needs its own worker)")
    gr = get("exchange.get_retries")
    if gr is not _MISSING and (not _is_int(gr) or gr < 0):
        p.append("exchange.get_retries must be an int >= 0")
    mqa = get("exchange.max_queue_age_s")
    if mqa is not _MISSING and (not _is_num(mqa) or not 0.0 < mqa <= 1.0):
        p.append("exchange.max_queue_age_s must be in (0, 1] (the ledger window has 1 s of slack)")
    amo = get("exchange.allow_market_orders")
    if amo is not _MISSING and not isinstance(amo, bool):
        p.append("exchange.allow_market_orders must be true or false")
    return p


def live_bound_problems(cfg: "Config") -> list[str]:
    """Problems that make a config unfit for ``--live`` although it is valid for offline runs.

    Sim-only profiles (``live_allowed: false``) may loosen the price bands (the parity profile uses 8 % from mid);
    a live run may not loosen them beyond :data:`LIVE_BOUNDS`, nor allow market orders.
    """
    p: list[str] = []
    for key, (lo, hi) in LIVE_BOUNDS.items():
        try:
            v = cfg.get(key)
        except KeyError:
            p.append(f"{key}: missing")
            continue
        if not _is_num(v) or not lo <= float(v) <= hi:
            p.append(f"{key} = {v!r} is outside the live bounds [{lo:g}, {hi:g}]")
    try:
        if cfg.get("exchange.allow_market_orders") is not False:
            p.append("exchange.allow_market_orders must be false for --live")
    except KeyError:
        pass
    return p


def config_hash(d: dict) -> str:
    """First 12 hex digits of the sha256 of the canonical JSON of ``d``."""
    blob = json.dumps(d, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def load_config(path: str | None = None, overlays: Sequence[str] = (), sets: Sequence[str] = ()) -> Config:
    """Load ``path`` (default ``BOT_ROOT/config/default.json``), apply ``overlays`` in order, then ``sets``.

    Raises :class:`ConfigError` on unreadable files, bad ``--set`` items and validation problems.
    """
    if isinstance(overlays, str) or isinstance(sets, str):
        raise ConfigError("overlays and sets must be sequences of strings")
    base_path = os.path.abspath(path or DEFAULT_PATH)
    d = _read_json(base_path)
    live = _pop_live_allowed(d, base_path)
    sources = [base_path]
    for ov in overlays:
        ov_path = os.path.abspath(ov)
        o = _read_json(ov_path)
        live = _pop_live_allowed(o, ov_path) and live
        d = deep_merge(d, o)
        sources.append(ov_path)
    problems: list[str] = []
    for item in sets:
        try:
            _apply_set(d, item)
            sources.append("set:" + item)
        except ConfigError as e:
            problems.extend(e.problems)
    problems.extend(validate(d))
    if problems:
        raise ConfigError(problems)
    return Config(d=d, hash=config_hash(d), root=BOT_ROOT, sources=tuple(sources), live_allowed=live)


def _leaves(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict) and d:
        for k, v in d.items():
            out.update(_leaves(v, f"{prefix}.{k}" if prefix else str(k)))
    else:
        out[prefix] = d
    return out


def _fmt(v: Any) -> str:
    if v is _MISSING:
        return "<absent>"
    s = json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)
    return s if len(s) <= 200 else s[:197] + "..."


def diff_from_default(cfg: Config) -> list[str]:
    """``"a.b.c: <default> -> <effective>"`` for every leaf where ``cfg.d`` differs from default.json, sorted."""
    base = _read_json(DEFAULT_PATH)
    base.pop("live_allowed", None)
    a, b = _leaves(base), _leaves(cfg.d)
    lines = []
    for path in sorted(set(a) | set(b)):
        va, vb = a.get(path, _MISSING), b.get(path, _MISSING)
        if va is _MISSING or vb is _MISSING or json.dumps(va, sort_keys=True) != json.dumps(vb, sort_keys=True):
            lines.append(f"{path}: {_fmt(va)} -> {_fmt(vb)}")
    return lines
