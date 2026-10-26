"""Per-season and summary reports in r_sim's table format, plus the section 9.3 acceptance checks.

* ``season_report(season, run, settle)`` condenses one ``Harness.run()`` result: P&L, per-round P&L, P&L to
  settlement per strategy (``sum q * (settle - px)`` over the exchange's exact fills, joined to the bot's
  ``OrderRecord.strategy`` by order_id), cost paid (``sum |q| * ref_px * cost``), order actions, max actions in
  60 s / 61 s, replenishment hits, violations, HTTP 429s, exceptions and alerts.
* ``summarize(reports)`` mirrors r_sim's ``summarize``; ``row(name, summary)`` prints one line under ``HDR``.
* ``paired(bot, ref)`` gives the paired statistics against the REC per-season list (``REC_PER_SEASON``).
* ``parity_checks``, ``default_checks`` and ``attribution`` implement the pass criteria of DESIGN.md section 9.3.

Everything here is pure: inputs are plain dicts, outputs are plain dicts and strings.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from ctcbot.types import SYMBOLS

__all__ = ["HDR", "REC", "REC_PER_SEASON", "season_report", "summarize", "row", "paired", "parity_checks",
           "default_checks", "attribution", "pct", "format_checks", "COMPONENT_GROUP"]

HDR = ("| variant | mean | sd | se | p5 | hit | R1 | R2 | R3 | cost paid | acts | max60 | repl hits | "
       "components (PnL to settle) |\n"
       "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")

# DESIGN.md section 9.3 baseline (REC under the measured-cost fill model, holdout cache, seasons 401-440)
REC: dict[str, Any] = {
    "mean": 19660.0, "sd": 12924.0, "se": 2043.0, "p5": 2072.0, "hit": 0.97, "round": [6376.0, 6524.0, 6760.0],
    "cost": 10627.0, "acts": 1227.0, "max60": 150, "components": {"H1": -5723.0, "S1": -9.0, "S3": 27626.0,
                                                                   "S3x": -2233.0},
    "h1_s3_flat": 19670.0, "no_h1_mean": 14017.0, "h1_half_mean": 17644.0,
}
_REC_LIST = [39726, 10763, 16896, 17789, 10488, 14979, 2100, 8724, 19606, 9497, 7562, 13957, 34683, 6837, 11038,
             14500, 12384, 24360, 16981, 7937, 27665, 24751, 14242, 7971, 20350, -800, 25529, 34043, 40862, 58202,
             19675, 8238, 35068, 16984, 35795, 1537, 40414, 14642, 30649, 29787]
REC_PER_SEASON: dict[int, float] = {401 + k: float(v) for k, v in enumerate(_REC_LIST)}

# strategies whose sum is compared with REC's H1 + S3 + S3x
COMPONENT_GROUP = ("h1", "s3", "flatten")


def pct(v: Sequence[float], q: float) -> float:
    """r_sim's linear-interpolation percentile."""
    v = sorted(v)
    if not v:
        return float("nan")
    x = q * (len(v) - 1)
    lo = int(math.floor(x))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (x - lo)


def _num(x: Any, default: float = 0.0) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def season_report(season: Any, run: Mapping[str, Any], settle: Mapping[str, float] | None,
                  label: str | None = None) -> dict:
    """Condense one harness run (one research season, or one season of a recording) into a report dict."""
    ex = run.get("exchange") or {}
    label = str(label if label is not None else season)
    ss = (ex.get("seasons") or {}).get(label) or {}
    meta = run.get("orders") or {}
    comp: dict[str, list[float]] = {}
    comp_round: dict[str, dict[str, float]] = {}
    sym: dict[str, float] = {}
    cost_paid = 0.0
    n_fills = 0
    for f in ex.get("fills") or []:
        if str(f.get("season")) != label:
            continue
        n_fills += 1
        q = int(f.get("qty") or 0)
        px = _num(f.get("price"))
        sgn = 1 if f.get("side") == "buy" else -1
        ref = f.get("ref_px")
        cost = abs(q) * _num(ref) * _num(f.get("cost_frac")) if ref else 0.0
        cost_paid += cost
        strat = (meta.get(str(f.get("order_id"))) or {}).get("strategy") or (
            "etf" if f.get("kind") == "etf" else "unknown")
        s = str(f.get("symbol"))
        pnl = sgn * q * (_num(settle.get(s)) - px) if settle and s in settle else 0.0
        a = comp.setdefault(str(strat), [0.0, 0.0, 0.0])
        a[0] += pnl
        a[1] += cost
        a[2] += abs(q)
        rr = comp_round.setdefault(str(strat), {})
        rk = str(f.get("round"))
        rr[rk] = rr.get(rk, 0.0) + pnl
        sym[s] = sym.get(s, 0.0) + pnl
    rate = ex.get("rate") or {}
    viol = ex.get("violations") or {}
    log_counts = run.get("log_counts") or {}
    rounds = ss.get("round_pnl") or {}
    return {
        "season": season, "label": label,
        "pnl": _num(ss.get("pnl"), float("nan")), "marks": ss.get("marks"),
        "round": [_num(rounds.get(str(r))) for r in (1, 2, 3)],
        "comp": comp, "comp_round": comp_round, "sym": sym,
        "group": sum(comp.get(k, [0.0])[0] for k in COMPONENT_GROUP),
        "cost": cost_paid, "fills": n_fills,
        "act": int(rate.get("actions") or 0), "act_by_kind": rate.get("by_kind") or {},
        "max60": int(rate.get("max_60s") or 0), "max61": int(rate.get("max_61s") or 0),
        "http_429": int(rate.get("http_429") or 0),
        "repl_hits": int(ex.get("repl_hits") or 0),
        "violations": {k: int(v) for k, v in viol.items()},
        "violations_total": int(sum(int(v) for v in viol.values())),
        "exceptions": int(log_counts.get("exception", 0)),
        "alerts": dict(run.get("alerts") or {}),
        "live_orders_end": int(ex.get("live_orders") or 0),
        "early_marketable": len(ex.get("early_marketable") or []),
        "max_resting": max((int(v) for v in (ex.get("max_resting") or {}).values()), default=0),
        "positions_end": ss.get("positions") or {},
        "wall_s": _num((run.get("timing") or {}).get("wall_s")),
    }


def summarize(reports: Sequence[Mapping[str, Any]]) -> dict:
    """r_sim-style summary over season reports (n, mean, sd, se, p5, p50, hit, round means, comp means, ...)."""
    ok = [r for r in reports if math.isfinite(_num(r.get("pnl"), float("nan")))]
    n = len(ok)
    if n == 0:
        return {"n": 0}
    p = [float(r["pnl"]) for r in ok]
    m = sum(p) / n
    sd = math.sqrt(sum((q - m) ** 2 for q in p) / max(1, n - 1))
    comp: dict[str, list[float]] = {}
    for r in ok:
        for k, v in (r.get("comp") or {}).items():
            a = comp.setdefault(k, [0.0, 0.0, 0.0])
            for z in range(3):
                a[z] += float(v[z]) / n
    syms = {s: sum(_num((r.get("sym") or {}).get(s)) for r in ok) / n for s in SYMBOLS}
    viol: dict[str, int] = {}
    for r in ok:
        for k, v in (r.get("violations") or {}).items():
            viol[k] = viol.get(k, 0) + int(v)
    return {
        "n": n, "mean": m, "sd": sd, "se": sd / math.sqrt(n), "p5": pct(p, 0.05), "p50": pct(p, 0.5),
        "hit": sum(q > 0 for q in p) / n,
        "round": [sum(_num(r["round"][k]) for r in ok) / n for k in range(3)],
        "act": sum(r.get("act", 0) for r in ok) / n,
        "max60": max(int(r.get("max60", 0)) for r in ok), "max61": max(int(r.get("max61", 0)) for r in ok),
        "repl_hits": sum(r.get("repl_hits", 0) for r in ok) / n,
        "cost": sum(_num(r.get("cost")) for r in ok) / n,
        "comp": comp, "group": sum(_num(r.get("group")) for r in ok) / n, "sym": syms,
        "violations": viol, "violations_total": sum(viol.values()),
        "http_429": sum(int(r.get("http_429", 0)) for r in ok),
        "exceptions": sum(int(r.get("exceptions", 0)) for r in ok),
        "per_season": {str(r.get("season")): float(r["pnl"]) for r in ok},
    }


def row(name: str, s: Mapping[str, Any]) -> str:
    """One line in r_sim's ``HDR`` format."""
    if not s or not s.get("n"):
        return f"| {name} | (no seasons) |||||||||||||"
    comp = s.get("comp") or {}
    cs = ", ".join(f"{a} {b[0]:,.0f}" for a, b in sorted(comp.items()))
    return (f"| {name} | {s['mean']:,.0f} | {s['sd']:,.0f} | {s['se']:,.0f} | {s['p5']:,.0f} | {s['hit']:.2f} | "
            f"{s['round'][0]:,.0f} | {s['round'][1]:,.0f} | {s['round'][2]:,.0f} | {s.get('cost', 0.0):,.0f} | "
            f"{s['act']:.0f} | {s['max60']} | {s['repl_hits']:.1f} | {cs} |")


def paired(bot: Mapping[Any, float], ref: Mapping[Any, float]) -> dict:
    """Paired comparison on the seasons both have: n, Pearson r, median |difference|, mean difference and its se."""
    def norm(d: Mapping[Any, float]) -> dict[int, float]:
        out = {}
        for k, v in d.items():
            try:
                out[int(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out

    b, rf = norm(bot), norm(ref)
    keys = sorted(set(b) & set(rf))
    xs = [b[k] for k in keys]
    ys = [rf[k] for k in keys]
    n = len(keys)
    if n < 2:
        return {"n": n, "r": float("nan"), "median_abs_diff": float("nan"), "mean_diff": float("nan"),
                "se_diff": float("nan"), "seasons": keys}
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    r = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else float("nan")
    d = [x - y for x, y in zip(xs, ys)]
    md = sum(d) / n
    sdd = math.sqrt(sum((v - md) ** 2 for v in d) / max(1, n - 1))
    return {"n": n, "r": r, "median_abs_diff": pct([abs(v) for v in d], 0.5), "mean_diff": md,
            "se_diff": sdd / math.sqrt(n), "seasons": keys, "diffs": dict(zip(keys, d))}


def _within(x: float, ref: float, frac: float) -> bool:
    return ref * (1 - frac) <= x <= ref * (1 + frac)


def parity_checks(summary: Mapping[str, Any], ref: Mapping[Any, float] | None = None,
                  h1_off: Mapping[str, Any] | None = None) -> list[dict]:
    """The section 9.3 pass criteria of the parity run (``config/rec_parity.json``, ``--fv cache``)."""
    ref = REC_PER_SEASON if ref is None else ref
    pr = paired(summary.get("per_season") or {}, ref)
    m = _num(summary.get("mean"), float("nan"))
    out = [
        {"check": "mean within +-10% of 19,660", "ok": _within(m, REC["mean"], 0.10), "value": m},
        {"check": "paired r >= 0.90", "ok": pr["n"] >= 2 and pr["r"] >= 0.90, "value": pr["r"], "n": pr["n"]},
        {"check": "median |diff| <= 2,000", "ok": pr["n"] >= 2 and pr["median_abs_diff"] <= 2000.0,
         "value": pr["median_abs_diff"]},
        {"check": "H1 + S3 + flatten within +-12% of 19,670",
         "ok": _within(_num(summary.get("group"), float("nan")), REC["h1_s3_flat"], 0.12),
         "value": summary.get("group")},
        {"check": "actions per season within +-20% of 1,227",
         "ok": _within(_num(summary.get("act"), float("nan")), REC["acts"], 0.20), "value": summary.get("act")},
        {"check": "max actions in any 60 s <= 150", "ok": int(summary.get("max60", 10 ** 9)) <= 150,
         "value": summary.get("max60")},
        {"check": "0 violations", "ok": int(summary.get("violations_total", 1)) == 0,
         "value": summary.get("violations")},
    ]
    if h1_off is not None:
        hm = _num(h1_off.get("mean"), float("nan"))
        out.append({"check": "diagnostic: h1 off within +-10% of 14,017", "ok": _within(hm, REC["no_h1_mean"], 0.10),
                    "value": hm, "blocking": False})
    return out


def default_checks(summary: Mapping[str, Any], abl_edge: Mapping[str, Any] | None = None) -> list[dict]:
    """The section 9.3 blocking checks of the default-config run (and the abl_edge band when given)."""
    m = _num(summary.get("mean"), float("nan"))
    out = [
        {"check": "p5 >= -6,000", "ok": _num(summary.get("p5"), -math.inf) >= -6000.0, "value": summary.get("p5")},
        {"check": "hit >= 0.85", "ok": _num(summary.get("hit"), 0.0) >= 0.85, "value": summary.get("hit")},
        {"check": "0 violations", "ok": int(summary.get("violations_total", 1)) == 0,
         "value": summary.get("violations")},
        {"check": "no 61-s window over 150", "ok": int(summary.get("max61", 10 ** 9)) <= 150,
         "value": summary.get("max61")},
        {"check": "mean >= 0.60 x REC (11,796)", "ok": m >= 0.60 * REC["mean"], "value": m},
    ]
    if abl_edge is not None:
        em = _num(abl_edge.get("mean"), float("nan"))
        out.append({"check": "abl_edge within 0.70-1.10 x REC (13,762 to 21,626)",
                    "ok": 0.70 * REC["mean"] <= em <= 1.10 * REC["mean"], "value": em})
    return out


def attribution(default: Mapping[str, Any], overlays: Mapping[str, Mapping[str, Any]]) -> dict:
    """Mean, paired difference against the default and the unexplained residual toward REC per overlay."""
    base = default.get("per_season") or {}
    rows = []
    explained = 0.0
    for name, s in sorted(overlays.items()):
        pr = paired(s.get("per_season") or {}, base)
        delta = _num(s.get("mean"), 0.0) - _num(default.get("mean"), 0.0)
        explained += delta
        rows.append({"overlay": name, "mean": s.get("mean"), "delta_mean": delta, "paired_mean_diff": pr["mean_diff"],
                     "paired_se": pr["se_diff"], "n": pr["n"]})
    gap = REC["mean"] - _num(default.get("mean"), 0.0)
    return {"rows": rows, "gap_to_rec": gap, "explained": explained, "residual": gap - explained}


def format_checks(checks: Iterable[Mapping[str, Any]]) -> str:
    """Human-readable PASS/FAIL lines."""
    lines = []
    for c in checks:
        tag = "PASS" if c.get("ok") else ("INFO" if c.get("blocking") is False else "FAIL")
        v = c.get("value")
        vs = f"{v:,.3f}" if isinstance(v, float) else str(v)
        lines.append(f"  [{tag}] {c.get('check')}: {vs}")
    return "\n".join(lines)
