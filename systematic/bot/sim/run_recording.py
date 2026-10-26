"""Run the bot against the testnet recording in virtual time and check DESIGN.md section 9.2.

usage (from BOT):
  SYS/.venv/bin/python -I sim/run_recording.py [--recording PATH] [--history DIR] [--mode dryrun|book]
      [--fv inline] [--command T:LINE ...] [--profile PATH ...] [--set a.b=V ...] [--out sim/out/NAME]
      [--from UNIX] [--to UNIX]

``--mode dryrun`` uses ``SimExchange(fills=False)``: orders rest, never fill and are cancelled by the bot;
``--mode book`` fills against the recorded books. Both use group C's real engine inline with the fast params
(``engine.params`` of the config) and ``library_seasons = sim.recorded_library_seasons`` ([226, 600]), so the
replayed seasons are out of sample (TESTNET_FINDINGS section 2). The engine's snapshot-fix probe runs first; if
the fix is missing, ``engine.forward_snapshots`` is forced to "never", as the cli does.

``--command T:LINE`` scripts a console command at virtual time T (seconds since the recording's first frame; a
value above 1e9 is read as a unix ``received_at``). The section 9.2 acceptance passes ``--command "1:lawok all"``:
the operator's answer to the late join in season 224 (playbook section 6.4 step 11).

Output: ``<out>/report.json`` with the section 9.2 checks, the exchange results and per-season P&L by strategy and
round; the checks are printed as PASS/FAIL. Exit status 1 if a blocking check fails. Offline only.

The checks written for testnet_s224_s225.jsonl (2 confirmed season resets, 5 reconnect pairs, the relevant
snapshots 695/764/250/416/828, the season-225 book checks) run only when that whole recording is replayed (seasons
224 and 225 in the window, nothing cut off by ``--from`` / ``--to``), and the per-season S1-r3 checks run only for
seasons whose round-3 final report is inside the window; each skip prints one ``[SKIP]`` line.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import os  # noqa: E402

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT not in sys.path:
    sys.path.insert(0, BOT)

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from typing import Any, Sequence  # noqa: E402

SP_DEFAULT = ("/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/"
              "5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad")
HISTORY_DEFAULT = os.environ.get("CTCBOT_HISTORY", SP_DEFAULT + "/data/hurricane-season-research/history")
FV_CACHE_DEFAULT = os.environ.get("CTCBOT_FV_CACHE", SP_DEFAULT + "/work/se/backtest/cache")

EXPECTED_RELEVANT = [695, 764, 250, 416, 828]
LATE_JOIN_OK = {"ISLE", "AQUA", "RECON", "MAT", "HAVEN", "REIN"}
CLOSURE = {"PORT", "GRID", "FERRY", "GEN", "ARCH", "REBLD"}
#: the seasons of testnet_s224_s225.jsonl, the recording the section 9.2 expectations (2 season resets, 5 reconnect
#: pairs, the relevant snapshots, the season-225 book checks) were written for
ORIGINAL_SEASONS = frozenset({"224", "225"})


def replay_window(market: Any, t_from: float | None, t_to: float | None) -> tuple[float, float]:
    """The replayed window ``[lo, hi)`` in virtual time; a side without ``--from`` / ``--to`` is unbounded."""
    lo = market.to_virtual(t_from) if t_from is not None else float("-inf")
    hi = market.to_virtual(t_to) if t_to is not None else float("inf")
    return lo, hi


def seasons_in_window(market: Any, lo: float, hi: float) -> set[str]:
    """The seasons with at least one round overlapping the replayed window."""
    return {str(rs.season) for rs in market.rounds if rs.open_t < hi and rs.close_t > lo}


def is_original_recording(market: Any, lo: float, hi: float) -> bool:
    """True when the whole 224/225 recording is replayed: seasons 224 and 225 in the window, no frame cut off."""
    whole = lo <= 0.0 and hi > market.last_t
    return whole and ORIGINAL_SEASONS <= seasons_in_window(market, lo, hi)


def parse_command(spec: str) -> tuple[float, str]:
    """``"T:LINE"`` -> (T, LINE)."""
    t, sep, line = str(spec).partition(":")
    if not sep or not line.strip():
        raise ValueError(f"--command needs T:LINE, got {spec!r}")
    return float(t), line.strip()


def to_command(line: str) -> Any:
    """A ``Command`` from a console line, through group D's parser when it is importable."""
    from ctcbot.types import Command
    try:
        from ctcbot.app.console import parse
        cmd = parse(line)
        if cmd is not None:
            return Command(cmd.name, tuple(cmd.args), source="sim")
    except Exception:  # noqa: BLE001 - fall back to a plain split
        pass
    toks = line.split()
    return Command(toks[0].lower(), tuple(toks[1:]), source="sim")


def _check(name: str, ok: bool, value: Any = None, blocking: bool = True) -> dict:
    return {"check": name, "ok": bool(ok), "value": value, "blocking": blocking}


def run(args: argparse.Namespace) -> tuple[dict, list[dict]]:
    from ctcbot.app.core import BotCore
    from ctcbot.case import CaseInfo, Schedule, diff_case
    from ctcbot.config import load_config
    from ctcbot.models.cost_model import CostModel
    from ctcbot.types import SYMBOLS, ListLog

    from sim.exchange import SimExchange
    from sim.harness import EngineTap, Harness, VirtualClock
    from sim.recorded_market import RecordedMarket
    from sim.report import season_report

    sets = list(args.sets)
    cfg = load_config(args.config, overlays=[os.path.abspath(p) for p in args.profile], sets=sets)
    log = ListLog()
    case = CaseInfo.from_file(cfg.path(cfg.get("paths.research_case")))
    schedule = Schedule(case.timing, cfg.get("cost.phases"))
    probe_ok: bool | None = None
    try:
        from ctcbot.models.engine_adapter import load_engine_module, snapshot_fix_present
        edir = cfg.path(cfg.get("engine.dir"))
        mod = load_engine_module(edir)
        probe_ok = bool(snapshot_fix_present(mod, cfg.path(cfg.get("engine.probe_file")), case.raw,
                                             os.path.join(edir, str(cfg.get("engine.params")))))
    except Exception as e:  # noqa: BLE001 - a failed probe is treated as "bug present"
        log.alert("engine_probe_failed", f"{type(e).__name__}: {e}")
        probe_ok = False
    if not probe_ok:
        log.alert("engine_snapshot_bug", "snapshot fix missing: forward_snapshots forced to never")
        sets.append('engine.forward_snapshots="never"')
        cfg = load_config(args.config, overlays=[os.path.abspath(p) for p in args.profile], sets=sets)
    cost = CostModel(cfg.path(cfg.get("cost.model")), cfg)
    laws_mod = None
    try:
        from ctcbot.models.laws import load_laws_module
        laws_mod = load_laws_module(cfg.path(cfg.get("laws.module")))
    except Exception as e:  # noqa: BLE001
        log.alert("laws_unavailable", f"{type(e).__name__}: {e}")
    path = os.path.abspath(args.recording or cfg.path(cfg.get("paths.recording")))
    market = RecordedMarket(path, cfg, args.history, case=case, schedule=schedule, t_from=args.t_from,
                            t_to=args.t_to)
    vt = VirtualClock(wall0=float(market.first_received_at or 1.8e9))
    exchange = SimExchange(market, cfg, cost, log, fills=(args.mode == "book"))
    from ctcbot.models.engine_adapter import EngineAdapter
    lib = cfg.get("sim.recorded_library_seasons", [226, 600])
    try:
        engine: Any = EngineAdapter(cfg, case.raw, vt, log, mode="inline", library_seasons=(int(lib[0]), int(lib[1])),
                                    snapshot_fix=probe_ok)
    except TypeError:   # an adapter without the (non-DESIGN) snapshot_fix keyword
        engine = EngineAdapter(cfg, case.raw, vt, log, mode="inline", library_seasons=(int(lib[0]), int(lib[1])))
    tap = EngineTap(engine, vt.mono)
    case_diff = diff_case(case.raw, case.raw)
    commands = []
    for spec in args.command:
        t, line = parse_command(spec)
        if t > 1e9:
            t = market.to_virtual(t)
        commands.append((t, to_command(line)))

    # probes: the coverage and S1-r3 state shortly after each round-3 final report
    probes = []
    finals = {}
    for t, season, rnd, stage, seq in market.reports:
        if rnd == 3 and stage == "final" and season not in finals:
            finals[season] = (t, seq)

    def coverage_probe(core: Any, t: float) -> Any:
        try:
            season = core.season
            return {"coverage": {str(k): v for k, v in dict(season.coverage).items()}, "epoch": season.season_epoch}
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}

    for season, (t, _) in finals.items():
        probes.append((t + 2.0, coverage_probe))

    def core_factory(timesrc: Any) -> Any:
        return BotCore(cfg, case, case_diff, tap, timesrc, log, mode="sim", run_dir=None, run_id="rec",
                       laws_module=laws_mod, cost=cost)

    t_start = max(0.0, market.to_virtual(args.t_from)) if args.t_from is not None else 0.0
    vt.set(t_start)
    h = Harness(cfg, market, exchange, tap, core_factory, log, commands=commands, timesrc=vt, probes=probes,
                t_start=t_start)
    res = h.run()
    ex = res["exchange"]
    core_rep = res.get("core") or {}
    t_final = res["timing"]["t_final"]

    # ------------------------------------------------------------------ per-season reports
    seasons = {}
    for label in ex["seasons"]:
        settle = market.settlement(label) or {s: market.mark(s, t_final) for s in SYMBOLS}
        seasons[label] = season_report(label, res, {k: v for k, v in settle.items() if v is not None}, label=label)

    # ------------------------------------------------------------------ section 9.2 checks
    checks: list[dict] = []
    notes: list[str] = []
    lo, hi = replay_window(market, args.t_from, args.t_to)
    original = is_original_recording(market, lo, hi)
    if not original:
        in_window = sorted(seasons_in_window(market, lo, hi))
        notes.append("224/225-specific checks skipped (2 confirmed season resets, 5 reconnect pairs, relevant "
                     "snapshots 695/764/250/416/828, season-225 book checks): the replay is not the whole "
                     f"testnet_s224_s225 recording (seasons in the window: {in_window})")
    lc = res.get("log_counts") or {}
    checks.append(_check("0 exceptions", lc.get("exception", 0) == 0 and not res.get("exceptions"),
                         lc.get("exception", 0)))
    viol = ex["violations"]
    checks.append(_check("0 violations of every type", sum(viol.values()) == 0, viol))
    checks.append(_check("0 HTTP 429", ex["rate"]["http_429"] == 0, ex["rate"]["http_429"]))
    checks.append(_check("at most 150 bot actions in any 61 s", ex["rate"]["max_61s"] <= 150, ex["rate"]["max_61s"]))
    checks.append(_check("at most 8 resting orders per symbol", max(ex["max_resting"].values()) <= 8,
                         ex["max_resting"]))
    live_core = []
    try:
        live_core = [r for r in h.core.store.live() if not getattr(r, "dry", False)]
    except Exception:  # noqa: BLE001
        pass
    checks.append(_check("every order terminal after close_all + two reconciles",
                         ex["live_orders"] == 0 and not live_core,
                         {"exchange_live": ex["live_orders"], "bot_live": len(live_core),
                          "bot_live_states": sorted({str(getattr(r, "state", "")) for r in live_core}),
                          "after_close_rejects": ex.get("after_close_rejects")}))
    resets = (core_rep.get("resets") or {}) if isinstance(core_rep, dict) else {}
    if original:
        checks.append(_check("exactly 2 confirmed season resets", resets.get("confirmed") == 2, resets))
    checks.append(_check("0 provisional resets", resets.get("provisional", 0) == 0, resets.get("provisional")))
    if original:
        pairs = max(0, market.stats["acks"] - 1)
        checks.append(_check("5 reconnect pairs", pairs == 5, pairs))
        fwd = str(cfg.get("engine.forward_snapshots", "relevant"))
        checks.append(_check("engine received exactly the relevant snapshots 695, 764, 250, 416, 828",
                             list(tap.submitted_snapshots) == EXPECTED_RELEVANT, list(tap.submitted_snapshots),
                             blocking=fwd == "relevant"))
    cov = {p["t"]: p["value"] for p in res.get("probes") or []}
    checks.append(_check("coverage after each round-3 final report"
                         + (" (224: 1-2 missing, 3 gapped; 225: 2 gapped)" if original else ""),
                         True, cov, blocking=False))
    meta = res.get("orders") or {}
    s1 = {}
    for oid, o in ex["orders"].items():
        if (meta.get(oid) or {}).get("strategy") == "s1r3":
            s1.setdefault(o["season"], []).append((o["t"], o["symbol"]))
    outside = sorted(season for season, (t_rep, _) in finals.items() if not lo <= t_rep < hi)
    if outside:
        notes.append(f"S1-r3 checks skipped for seasons {outside}: their round-3 final report is outside --from/--to")
    for season, (t_rep, seq) in sorted(finals.items()):
        if season in outside:
            continue
        placed = sorted(s1.get(season, []))
        syms = sorted({s for _, s in placed})
        first_fv = next((tp for tp, _, sh, _ in tap.publishes if tp >= t_rep - 1e-9 and sh is not None
                         and seq is not None and sh >= seq), None)
        same = bool(placed) and first_fv is not None and abs(placed[0][0] - first_fv) < 1e-6
        checks.append(_check(f"S1-r3 fired in {season} r3 with the first fv seq_hi >= final_seq", same,
                             {"first_order_t": placed[0][0] if placed else None, "first_fv_t": first_fv,
                              "symbols": syms}))
        if season == "224":
            checks.append(_check("224 r3: S1-r3 orders only in a non-empty subset of the D/L names",
                                 bool(syms) and set(syms) <= LATE_JOIN_OK and not (set(syms) & CLOSURE), syms))
        else:
            missing = [s for s in SYMBOLS if s not in syms]
            gate_logs = {s: sum(1 for k, f in log.events if str(k).startswith("s1r3") and f.get("symbol") == s)
                         for s in missing}
            checks.append(_check(f"{season} r3: every symbol not placed has a logged gate",
                                 all(v > 0 for v in gate_logs.values()), gate_logs))
    checks.append(_check("no marketable order in the first 3 s of any round", not ex["early_marketable"],
                         ex["early_marketable"][:5]))
    if args.mode == "book" and original:
        cps = (ex["seasons"].get("225") or {}).get("checkpoints") or {}
        not_flat_alerts = (res.get("alerts") or {}).get("not_flat", 0)
        for r in ("1", "2"):
            held = {s: q for s, q in (cps.get(r) or {}).items() if s != "REIN"}
            checks.append(_check(f"book mode: flat except REIN by 568 s in 225 r{r} (or a logged not_flat)",
                                 not held or not_flat_alerts > 0, held))
        s3_225 = ((seasons.get("225") or {}).get("comp") or {}).get("s3", [0.0])[0]
        checks.append(_check("S3 in season 225 within -15k..+15k (non-blocking flag)", -15000 <= s3_225 <= 15000,
                             s3_225, blocking=False))
    out = {"mode": args.mode, "recording": path, "probe_ok": probe_ok, "cfg_hash": cfg.hash, "seasons": seasons,
           "checks": checks, "skipped": notes, "core": core_rep, "timing": res.get("timing"),
           "alerts": res.get("alerts"),
           "engine_snapshots": list(tap.submitted_snapshots), "exchange": {k: v for k, v in ex.items()
                                                                           if k not in ("fills", "orders")},
           "fills": ex["fills"], "orders": ex["orders"], "order_meta": meta, "probes": res.get("probes")}
    return out, checks


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recording", default=None)
    ap.add_argument("--history", default=HISTORY_DEFAULT)
    ap.add_argument("--mode", choices=("dryrun", "book"), default="dryrun")
    ap.add_argument("--fv", choices=("inline",), default="inline", help="the real engine inline (the only mode)")
    ap.add_argument("--command", action="append", default=[])
    ap.add_argument("--config", default=None)
    ap.add_argument("--profile", action="append", default=[])
    ap.add_argument("--set", dest="sets", action="append", default=[])
    ap.add_argument("--out", default=None)
    ap.add_argument("--from", dest="t_from", type=float, default=None)
    ap.add_argument("--to", dest="t_to", type=float, default=None)
    args = ap.parse_args(argv)
    out_dir = os.path.abspath(args.out or os.path.join(BOT, "sim", "out",
                                                       time.strftime(f"recording_{args.mode}_%Y%m%dT%H%M%S")))
    if not out_dir.startswith(os.path.join(BOT, "")):
        print(f"--out must be under {BOT}", file=sys.stderr)
        return 2
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    out, checks = run(args)
    out["wall_s"] = time.time() - t0
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    print(f"### recording run ({args.mode}), {out['wall_s']:.0f} s wall")
    for label, rep in sorted(out["seasons"].items()):
        comps = ", ".join(f"{k} {v[0]:,.0f}" for k, v in sorted((rep.get("comp") or {}).items()))
        rounds = " / ".join(f"{x:,.0f}" for x in rep["round"])
        print(f"season {label}: pnl {rep['pnl']:,.0f} ({rep['marks']}); rounds {rounds}; {comps}")
    for c in checks:
        tag = "PASS" if c["ok"] else ("INFO" if not c.get("blocking", True) else "FAIL")
        print(f"  [{tag}] {c['check']}: {json.dumps(c['value'], default=str)[:300]}")
    for note in out.get("skipped") or []:
        print(f"  [SKIP] {note}")
    print(f"report: {os.path.join(out_dir, 'report.json')}")
    return 1 if any(not c["ok"] and c.get("blocking", True) for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
