"""Paired comparison of the bot's research run with r_sim's REC configuration (DESIGN.md sections 4.E and 9.3).

usage (from BOT):
  SYS/.venv/bin/python -I sim/compare_rec.py --seasons 401-440 --report sim/out/NAME/report.json
      [--variant base] [--lib holdout] [--nproc 8] [--sp SP] [--rebacktest DIR] [--cost-model PATH]

It imports ``testnet/analysis/rebacktest/r_sim.py`` read-only (``sys.dont_write_bytecode`` is set before the import,
and nothing is written anywhere), runs REC exactly as ``testnet/analysis/writer/w02_sens.py`` builds it (REOPT with
H1 at full size in L/4 clips at 50-56 s, S3 k 0.1 / 0.2 from 80 s in L/12 clips, the automatic 540-568 s flatten,
measured costs, report-window lag 1 row, REIN excluded, rate 150, threshold multiplier 2.0), and prints the r_sim
table rows of REC and the bot side by side, the per-season paired table and the paired statistics
(Pearson r and median |difference|) that section 9.3 judges.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import os  # noqa: E402

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYS_DIR = os.path.dirname(BOT)
if BOT not in sys.path:
    sys.path.insert(0, BOT)

import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
from typing import Any, Sequence  # noqa: E402

SP_DEFAULT = os.environ.get("CTCBOT_SP", "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/"
                                         "5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad")


def rec_config(R: Any) -> dict:
    """The REC configuration of ``writer/w02_sens.py`` (its first entry), built with the given r_sim module."""
    s3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
    h1 = dict(h1_size=1.0, h1_in=(50, 56), h1_out=None, clip_h1=0.25)
    me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
    return dict(me, name="REC (REOPT, H1 L/4 50-56 s)", windows=R.plan_windows(**h1, **s3))


def _season_no(x: Any) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _latest_report() -> str | None:
    cands = sorted(glob.glob(os.path.join(BOT, "sim", "out", "*", "report.json")), key=os.path.getmtime)
    return cands[-1] if cands else None


def main(argv: Sequence[str] | None = None) -> int:
    from sim import report as REP
    from sim.run_research import parse_seasons

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", default="401-440")
    ap.add_argument("--report", default=None, help="the bot's report.json (default: the newest under sim/out)")
    ap.add_argument("--variant", default="base")
    ap.add_argument("--lib", default="holdout")
    ap.add_argument("--nproc", type=int, default=8)
    ap.add_argument("--sp", default=SP_DEFAULT)
    ap.add_argument("--rebacktest", default=os.path.join(SYS_DIR, "testnet", "analysis", "rebacktest"))
    ap.add_argument("--cost-model", default=os.path.join(SYS_DIR, "testnet", "analysis", "books", "cost_model.json"))
    args = ap.parse_args(argv)
    seasons = parse_seasons(args.seasons)

    path = args.report or _latest_report()
    bot: dict[int, float] = {}
    bot_summary: dict = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            rep = json.load(f)
        var = (rep.get("variants") or {}).get(args.variant) or {}
        reps = [r for r in var.get("seasons") or [] if _season_no(r.get("season")) in seasons and not r.get("error")]
        bot_summary = REP.summarize(reps) if reps else {}
        for k, v in (bot_summary.get("per_season") or {}).items():
            n = _season_no(k)
            if n is not None:
                bot[n] = float(v)
    else:
        print("no bot report found; printing REC only", file=sys.stderr)

    sys.path.insert(0, os.path.abspath(args.rebacktest))
    import r_sim as R  # noqa: E402 - read-only import of the team's re-backtest module

    R.init(args.sp, os.path.abspath(args.cost_model))
    res = R.run(args.sp, args.lib, seasons, [rec_config(R)], nproc=max(1, int(args.nproc)))[0]
    rec = {n: float(r["pnl"]) for n, r in zip(seasons, res)}
    s = R.summarize(res)

    print(f"### REC (r_sim) vs bot, {args.lib} {seasons[0]}-{seasons[-1]}")
    print(R.HDR)
    print(R.row("REC (r_sim)", s))
    if bot_summary.get("n"):
        print(REP.row(f"bot ({args.variant})", bot_summary))
    if bot:
        pr = REP.paired(bot, rec)
        print("\n| season | bot | REC | bot - REC |\n|---|---|---|---|")
        for n in pr["seasons"]:
            print(f"| {n} | {bot[n]:,.0f} | {rec[n]:,.0f} | {bot[n] - rec[n]:+,.0f} |")
        print(f"\npaired ({pr['n']} seasons): Pearson r {pr['r']:.3f}, median |diff| {pr['median_abs_diff']:,.0f}, "
              f"mean diff {pr['mean_diff']:+,.0f} (se {pr['se_diff']:,.0f})")
        ok_r = pr["r"] >= 0.90
        ok_m = pr["median_abs_diff"] <= 2000.0
        print(f"  [{'PASS' if ok_r else 'FAIL'}] r >= 0.90    [{'PASS' if ok_m else 'FAIL'}] median |diff| <= 2,000")
    drift = [n for n in seasons if n in REP.REC_PER_SEASON and abs(rec[n] - REP.REC_PER_SEASON[n]) > 1.0]
    if drift:
        print(f"\nnote: r_sim REC differs from the DESIGN.md baseline list in seasons {drift}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
