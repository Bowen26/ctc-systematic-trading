"""Run the bot against research seasons in virtual time and report in r_sim's format (DESIGN.md sections 4.E, 9.3).

usage (from BOT):
  SYS/.venv/bin/python -I sim/run_research.py --history DIR --fv-cache DIR --seasons 401-440
      [--profile PATH ...] [--set a.b=V ...] [--fv cache|inline] [--workers 8] [--out sim/out/NAME]
      [--ablations] [--h1-off] [--snapshots all|pause|none] [--drop-seq N ...]

Each season gets a fresh ``BotCore`` (mode "sim"), ``SimExchange`` and ``ResearchMarket`` driven by
``sim.harness.Harness``, in a multiprocessing fork pool. ``--fv cache`` (default) replays the engine fair-value
cache ``fv_holdout_NNN.json`` (``fv_loso_NNN.json`` as a fallback); ``--fv inline`` runs group C's
``EngineAdapter(mode="inline", library_seasons=(1, 400))`` with the standard ``engine_params.json``, as the
caches were built (about 40 s per season).

``--ablations`` runs the variant given by ``--profile`` / ``--set`` plus each ``sim/ablations/abl_*.json`` overlay
on top of it and writes the section 9.3 attribution table; ``--h1-off`` adds the variant with
``strategies.h1.enabled=false`` (the parity diagnostic). Output: ``<out>/report.json`` (summaries, per-season
reports, paired comparison with the REC baseline, checks) and the printed ``HDR`` table. The exit status is 1 if
a blocking section 9.3 check fails, else 0.

Offline only: nothing here opens a socket, and ``sys.dont_write_bytecode`` is set before any import so no
``__pycache__`` appears under the read-only engine or laws folders.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import os  # noqa: E402

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT not in sys.path:
    sys.path.insert(0, BOT)

import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from typing import Any, Sequence  # noqa: E402

SP_DEFAULT = ("/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/"
              "5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad")
HISTORY_DEFAULT = os.environ.get("CTCBOT_HISTORY", SP_DEFAULT + "/data/hurricane-season-research/history")
FV_CACHE_DEFAULT = os.environ.get("CTCBOT_FV_CACHE", SP_DEFAULT + "/work/se/backtest/cache")
ABLATIONS_DIR = os.path.join(BOT, "sim", "ablations")


def parse_seasons(spec: str) -> list[int]:
    """``"401-440,445"`` -> [401, ..., 440, 445]."""
    out: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        if hi < lo:
            raise ValueError(f"bad season range {part!r}")
        out.extend(range(lo, hi + 1))
    return out


def run_season(n: int, opts: dict) -> dict:
    """One research season with a fresh core and exchange; returns ``sim.report.season_report`` plus extras."""
    sys.dont_write_bytecode = True
    from ctcbot.app.core import BotCore
    from ctcbot.case import CaseInfo, Schedule, diff_case
    from ctcbot.config import load_config
    from ctcbot.models.cost_model import CostModel
    from ctcbot.types import ListLog

    from sim.exchange import SimExchange
    from sim.fv_cache import CachedFairValues, cache_file
    from sim.harness import EngineTap, Harness, VirtualClock
    from sim.report import season_report
    from sim.research_market import ResearchMarket

    cfg = load_config(opts.get("config"), overlays=opts["profiles"], sets=opts["sets"])
    case = CaseInfo.from_file(cfg.path(cfg.get("paths.research_case")))
    schedule = Schedule(case.timing, cfg.get("cost.phases"))
    cost = CostModel(cfg.path(cfg.get("cost.model")), cfg)
    log = ListLog()
    laws_mod = None
    try:
        from ctcbot.models.laws import load_laws_module
        laws_mod = load_laws_module(cfg.path(cfg.get("laws.module")))
    except Exception as e:  # noqa: BLE001 - the core then runs without laws (S1-r3 fails closed)
        log.alert("laws_unavailable", f"{type(e).__name__}: {e}")
    vt = VirtualClock()
    market = ResearchMarket(opts["history"], n, cfg, cost, case=case, schedule=schedule,
                            drop_seqs=opts.get("drop_seqs") or (), snapshots=opts.get("snapshots") or "all")
    exchange = SimExchange(market, cfg, cost, log, fills=True)
    if opts["fv"] == "inline":
        from ctcbot.models.engine_adapter import EngineAdapter
        engine: Any = EngineAdapter(cfg, case.raw, vt, log, mode="inline", library_seasons=(1, 400))
        if not opts.get("probe_ok", True):
            log.alert("engine_snapshot_bug", "snapshot fix missing: forward_snapshots forced to never")
    else:
        engine = CachedFairValues(cache_file(opts["fv_cache"], n), schedule, baskets=case.baskets, log=log)
    tap = EngineTap(engine, vt.mono)
    case_diff = diff_case(case.raw, case.raw)

    def core_factory(timesrc: Any) -> Any:
        return BotCore(cfg, case, case_diff, tap, timesrc, log, mode="sim", run_dir=None, run_id=f"sim{n:03d}",
                       laws_module=laws_mod, cost=cost)

    h = Harness(cfg, market, exchange, tap, core_factory, log, timesrc=vt)
    res = h.run()
    rep = season_report(n, res, market.settle)
    rep["engine_snapshots"] = list(tap.submitted_snapshots)
    rep["cfg_hash"] = cfg.hash
    rep["log_hash"] = res.get("log_hash")
    core_rep = res.get("core") or {}
    rep["core_exceptions"] = core_rep.get("exceptions") if isinstance(core_rep, dict) else None
    rep["harness_exceptions"] = res.get("exceptions")[:5] if res.get("exceptions") else []
    try:
        engine.close()
    except Exception:  # noqa: BLE001
        pass
    return rep


def engine_probe(cfg: Any) -> bool:
    """Group C's snapshot-fix probe on the configured engine (False when it fails or raises)."""
    try:
        from ctcbot.case import CaseInfo
        from ctcbot.models.engine_adapter import load_engine_module, snapshot_fix_present
        case = CaseInfo.from_file(cfg.path(cfg.get("paths.research_case")))
        edir = cfg.path(cfg.get("engine.dir"))
        return bool(snapshot_fix_present(load_engine_module(edir), cfg.path(cfg.get("engine.probe_file")), case.raw,
                                         os.path.join(edir, str(cfg.get("engine.params")))))
    except Exception:  # noqa: BLE001
        return False


def _work(task: tuple[str, int, dict]) -> tuple[str, int, dict]:
    name, n, opts = task
    try:
        return name, n, run_season(n, opts)
    except Exception as e:  # noqa: BLE001 - one failing season is reported, not fatal
        return name, n, {"season": n, "pnl": float("nan"), "error": f"{type(e).__name__}: {e}",
                         "traceback": traceback.format_exc()}


def run_variants(variants: dict[str, dict], seasons: Sequence[int], workers: int) -> dict[str, list[dict]]:
    """Run every (variant, season) pair; returns variant -> season reports in season order."""
    tasks = [(name, n, opts) for name, opts in variants.items() for n in seasons]
    out: dict[str, dict[int, dict]] = {name: {} for name in variants}
    if workers <= 1 or len(tasks) <= 1:
        for task in tasks:
            name, n, rep = _work(task)
            out[name][n] = rep
            _progress(name, n, rep)
    else:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(workers) as pool:
            for name, n, rep in pool.imap_unordered(_work, tasks, chunksize=1):
                out[name][n] = rep
                _progress(name, n, rep)
    return {name: [d[n] for n in seasons if n in d] for name, d in out.items()}


def _progress(name: str, n: int, rep: dict) -> None:
    if rep.get("error"):
        print(f"  {name} season {n}: ERROR {rep['error']}", file=sys.stderr, flush=True)
    else:
        print(f"  {name} season {n}: pnl {rep.get('pnl', float('nan')):,.0f}  acts {rep.get('act', 0)}  "
              f"viol {rep.get('violations_total', 0)}  exc {rep.get('exceptions', 0)}  "
              f"({rep.get('wall_s', 0.0):.1f}s)", file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", default=HISTORY_DEFAULT)
    ap.add_argument("--fv-cache", default=FV_CACHE_DEFAULT)
    ap.add_argument("--seasons", default="401-440")
    ap.add_argument("--config", default=None, help="base config (default config/default.json)")
    ap.add_argument("--profile", action="append", default=[])
    ap.add_argument("--set", dest="sets", action="append", default=[])
    ap.add_argument("--fv", choices=("cache", "inline"), default="cache")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--ablations", action="store_true")
    ap.add_argument("--h1-off", action="store_true", help="also run strategies.h1.enabled=false (diagnostic)")
    ap.add_argument("--snapshots", choices=("all", "pause", "none"), default="all")
    ap.add_argument("--inject-snapshots", action="store_true", help="alias of --snapshots all")
    ap.add_argument("--drop-seq", type=int, action="append", default=[])
    args = ap.parse_args(argv)

    from ctcbot.config import load_config
    from sim import report as R

    seasons = parse_seasons(args.seasons)
    profiles = [os.path.abspath(p) for p in args.profile]
    sets = list(args.sets)
    if args.fv == "inline":
        sets.append("engine.params=engine_params.json")
    base_cfg = load_config(args.config, overlays=profiles, sets=sets)   # validates before forking
    probe_ok = True
    if args.fv == "inline":
        probe_ok = engine_probe(base_cfg)
        if not probe_ok:        # as the cli does: lose snapshot recovery rather than corrupt the engine
            sets.append('engine.forward_snapshots="never"')
            base_cfg = load_config(args.config, overlays=profiles, sets=sets)
    workers = int(args.workers if args.workers is not None else base_cfg.get("sim.workers", 8))
    common = {"config": args.config, "history": args.history, "fv_cache": args.fv_cache, "fv": args.fv,
              "probe_ok": probe_ok,
              "snapshots": "all" if args.inject_snapshots else args.snapshots, "drop_seqs": list(args.drop_seq)}
    variants: dict[str, dict] = {"base": dict(common, profiles=profiles, sets=sets)}
    abl_files = sorted(glob.glob(os.path.join(ABLATIONS_DIR, "abl_*.json"))) if args.ablations else []
    for p in abl_files:
        name = os.path.splitext(os.path.basename(p))[0]
        variants[name] = dict(common, profiles=profiles + [p], sets=sets)
    if args.h1_off:
        variants["h1_off"] = dict(common, profiles=profiles, sets=sets + ["strategies.h1.enabled=false"])
    for name, v in variants.items():
        load_config(args.config, overlays=v["profiles"], sets=v["sets"])   # fail fast on a bad overlay

    out_dir = args.out or os.path.join(BOT, "sim", "out", time.strftime("research_%Y%m%dT%H%M%S"))
    out_dir = os.path.abspath(out_dir)
    if not out_dir.startswith(os.path.join(BOT, "")):
        print(f"--out must be under {BOT}", file=sys.stderr)
        return 2
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    print(f"research sim: {len(seasons)} seasons x {len(variants)} variants, fv {args.fv}, {workers} workers",
          file=sys.stderr, flush=True)
    results = run_variants(variants, seasons, workers)

    parity = any(os.path.basename(p) == "rec_parity.json" for p in profiles)
    summaries = {name: R.summarize(reps) for name, reps in results.items()}
    report: dict[str, Any] = {"seasons": seasons, "fv": args.fv, "profiles": profiles, "sets": sets,
                              "cfg_hash": base_cfg.hash, "parity": parity, "wall_s": time.time() - t0,
                              "variants": {}}
    rec_seasons = {n: v for n, v in R.REC_PER_SEASON.items() if n in seasons}
    for name, reps in results.items():
        s = summaries[name]
        report["variants"][name] = {"profiles": variants[name]["profiles"], "sets": variants[name]["sets"],
                                    "summary": s, "paired_rec": R.paired(s.get("per_season") or {}, rec_seasons),
                                    "seasons": reps,
                                    "errors": [r for r in reps if r.get("error")]}
    checks = (R.parity_checks(summaries["base"], rec_seasons, summaries.get("h1_off")) if parity
              else R.default_checks(summaries["base"], summaries.get("abl_edge")))
    report["checks"] = checks
    if args.ablations:
        report["attribution"] = R.attribution(summaries["base"],
                                              {k: v for k, v in summaries.items() if k.startswith("abl_")})
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)

    print(f"### research seasons {seasons[0]}-{seasons[-1]} ({len(seasons)}), fv {args.fv}")
    print(R.HDR)
    for name in results:
        print(R.row(name, summaries[name]))
    pr = report["variants"]["base"]["paired_rec"]
    print(f"\npaired vs REC ({pr['n']} seasons): r {pr['r']:.3f}, median |diff| {pr['median_abs_diff']:,.0f}, "
          f"mean diff {pr['mean_diff']:,.0f} (se {pr['se_diff']:,.0f})")
    if args.ablations and "attribution" in report:
        at = report["attribution"]
        print("\n| overlay | mean | delta vs base | paired diff (se) |\n|---|---|---|---|")
        for rw in at["rows"]:
            print(f"| {rw['overlay']} | {rw['mean']:,.0f} | {rw['delta_mean']:+,.0f} | "
                  f"{rw['paired_mean_diff']:+,.0f} ({rw['paired_se']:,.0f}) |")
        print(f"gap to REC {at['gap_to_rec']:+,.0f}; explained {at['explained']:+,.0f}; residual {at['residual']:+,.0f}")
    print("\nchecks (" + ("parity" if parity else "default") + "):")
    print(R.format_checks(checks))
    nerr = sum(len(v["errors"]) for v in report["variants"].values())
    if nerr:
        print(f"\n{nerr} season runs failed; see report.json", file=sys.stderr)
    print(f"\nreport: {os.path.join(out_dir, 'report.json')}")
    blocking_fail = any(not c.get("ok") and c.get("blocking", True) for c in checks)
    return 1 if (blocking_fail or nerr) else 0


if __name__ == "__main__":
    raise SystemExit(main())
