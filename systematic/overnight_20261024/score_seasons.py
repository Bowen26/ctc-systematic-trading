"""Score every complete testnet season in a recording on the recorded books.

Variants: "final" = the bot's default config (S3 from 120 s since 24 Oct), "old_s3_80s" = S3 from 80 s.
A season counts once the recording covers it from before round 1's open to after round 3's close.
Each variant is run with ``sim/run_recording.py --mode book`` on just that season (--from/--to), with the
engine's storm library limited to seasons 251-600 so the replayed seasons are out of sample.

usage: python3 -I score_seasons.py BOT_DIR HISTORY RECORDING OUT_DIR
Writes OUT_DIR/summary.json (one entry per scored season) and OUT_DIR/summary.md. Safe to re-run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

BOT, HIST, REC, OUT = sys.argv[1:5]
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True
from ctcbot.config import load_config  # noqa: E402
from sim.recorded_market import RecordedMarket  # noqa: E402

PY = os.path.join(os.path.dirname(BOT), ".venv", "bin", "python")
VARIANTS = {"final": [], "old_s3_80s": ["--set", 'strategies.s3.start=["storm_start",20]']}
LIB = 'sim.recorded_library_seasons=[251,600]'

mkt = RecordedMarket(REC, load_config(None, overlays=[], sets=[]), HIST)
first = mkt.first_received_at
spath = os.path.join(OUT, "summary.json")
summary = json.load(open(spath)) if os.path.exists(spath) else {}

rounds_by_season: dict[str, dict[int, object]] = {}
for rs in mkt.rounds:
    rounds_by_season.setdefault(rs.season, {})[rs.round] = rs

for season, rr in sorted(rounds_by_season.items()):
    if season in summary or set(rr) != {1, 2, 3}:
        continue
    if rr[1].open_t < 0.0 or rr[3].close_t + 5.0 > mkt.last_t:      # started late, or not finished yet
        continue
    t_from, t_to = first + rr[1].open_t - 10.0, first + rr[3].close_t + 10.0
    entry = {"season": season}
    for name, extra in VARIANTS.items():
        # run_recording.py only writes under the bot folder
        out_dir = os.path.join(BOT, "sim", "out", "overnight", os.path.basename(os.path.normpath(OUT)),
                               f"s{season}_{name}")
        cmd = [PY, "-I", "sim/run_recording.py", "--recording", REC, "--history", HIST, "--mode", "book",
               "--set", LIB, *extra, "--from", f"{t_from:.3f}", "--to", f"{t_to:.3f}", "--out", out_dir]
        subprocess.run(cmd, cwd=BOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        try:
            rep = json.load(open(os.path.join(out_dir, "report.json")))
            x = rep["seasons"][season]
            entry[name] = {"pnl": round(x["pnl"], 1), "rounds": [round(v, 1) for v in x["round"]],
                           "cost": round(x["cost"], 1), "violations": x["violations_total"],
                           "exceptions": x["exceptions"]}
        except (OSError, KeyError, ValueError) as e:
            entry[name] = {"error": f"{type(e).__name__}: {e}"}
    summary[season] = entry
    json.dump(summary, open(spath, "w"), indent=1)

lines = ["# Overnight testnet seasons, scored on the recorded books", "",
         "Simulated fills against the real recorded order books; the bot's own code. `final` = default config "
         "(S3 from 120 s); `old` = S3 from 80 s. Engine storm library = seasons 251-600 (out of sample).", "",
         "| Season | final | old (S3 80 s) | final - old | final by round | violations | exceptions |",
         "|---|---|---|---|---|---|---|"]
tot_f = tot_o = 0.0
for season in sorted(summary):
    e = summary[season]
    f, o = e.get("final", {}), e.get("old_s3_80s", {})
    if "pnl" not in f or "pnl" not in o:
        lines.append(f"| {season} | {f.get('error', '?')} | {o.get('error', '?')} | | | | |")
        continue
    tot_f += f["pnl"]; tot_o += o["pnl"]
    lines.append(f"| {season} | {f['pnl']:,.0f} | {o['pnl']:,.0f} | {f['pnl'] - o['pnl']:+,.0f} | "
                 f"{' / '.join(f'{v:,.0f}' for v in f['rounds'])} | {f['violations'] + o['violations']} | "
                 f"{f['exceptions'] + o['exceptions']} |")
n = sum(1 for e in summary.values() if "pnl" in e.get("final", {}))
if n:
    lines += ["", f"**Total over {n} seasons:** final {tot_f:,.0f} (avg {tot_f / n:,.0f}), "
                  f"old {tot_o:,.0f} (avg {tot_o / n:,.0f}), difference {tot_f - tot_o:+,.0f}."]
open(os.path.join(OUT, "summary.md"), "w").write("\n".join(lines) + "\n")
print(f"scored {n} seasons")
