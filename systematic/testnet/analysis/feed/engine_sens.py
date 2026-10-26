"""Task 3: sensitivity of the team's fair-value engine to the testnet feed (season 225).

    python3 -I engine_sens.py <recording.jsonl> <research history dir> <engine dir> <params name> <lib s0> <lib s1> <out.json> [engine file, default fairvalue.py]

Streams fed to a fresh SeasonState each (arrival order):
  R  research feed.jsonl (arrival = fitted round open + market-implied real second)
  T  the recorded testnet news of season 225, including snapshots, at received_at (what the bot saw)
  C  research feed with the 24 official advisories replaced by their testnet versions (advisory noise only)
  D  research feed minus the 22 messages lost in the disconnects, plus the recorded snapshots (gap only)
Fair values are taken at s = 60, 70, ..., 540 of each round, after all messages that arrived by open + s + 1.5 s.
S3 decisions use the house consensus of market.csv (row s/2) with half-spread h, gamma, k as in the playbook.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import statistics as stx
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import feedlib as FL  # noqa: E402

REC, HIST, ENG, PNAME, LIB0, LIB1, OUT = sys.argv[1:8]
EFILE = sys.argv[8] if len(sys.argv) > 8 else "fairvalue.py"
spec = importlib.util.spec_from_file_location("se_engine", os.path.join(ENG, EFILE))
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)
SYM = E.SYMBOLS
H, GAMMA = 0.0025, 0.005
KR = {1: 0.1, 2: 0.1, 3: 0.2}
SEASON = 225
DATES = {1: "1802-05-10", 2: "1802-05-11", 3: "1802-05-12"}

frames = FL.load_frames(REC)
opens = {d: r["open_med"] for d, r in FL.fit_round_opens(frames).items()}
research = FL.research_feed(HIST, SEASON)
rby = {m["seq"]: m for m in research}
rec_news = [(f["received_at"], f["message"]) for f in frames
            if f["type"] == "news" and FL.season_of_msg(f["message"]) == SEASON]
got = {m["seq"] for _, m in rec_news if "seq" in m}
lost = sorted(set(rby) - got)
snaps = [(w, m) for w, m in rec_news if m.get("type") == "snapshot"]
tn_off = {m["seq"]: m for _, m in rec_news if m.get("type") == "advisory" and m.get("source") == "official"}
print(f"season {SEASON}: research {len(research)} msgs, recorded {len(got)} seq'd + {len(snaps)} snapshots; lost {len(lost)}; "
      f"testnet official advisories {len(tn_off)}")


def arr(m):
    return opens[m["time"][:10]] + FL.mkt_seconds(m["time"])


streams = {
    "R": sorted(((arr(m), m) for m in research), key=lambda x: (x[0], x[1]["seq"])),
    "T": sorted(rec_news, key=lambda x: x[0]),
    "C": sorted(((arr(m), tn_off.get(m["seq"], m)) for m in research), key=lambda x: (x[0], x[1]["seq"])),
    "D": sorted([(arr(m), m) for m in research if m["seq"] not in set(lost)] + snaps, key=lambda x: x[0]),
}
# house consensus
rows = list(csv.DictReader(open(os.path.join(HIST, f"season-{SEASON:03d}", "market.csv"))))
px = {r: [] for r in (1, 2, 3)}
for row in rows:
    px[int(row["round"])].append({s: float(row[s]) for s in SYM})
settle = json.load(open(os.path.join(HIST, f"season-{SEASON:03d}", "settlement.json")))
limits = json.load(open(os.path.join(ENG, "..", "data", "case.json")))["limits"]
evals = [(r, s) for r in (1, 2, 3) for s in range(60, 541, 10)]
post = [(r, s) for r in (1, 2, 3) for s in (560, 600, 760)]  # after storm end, after the prelim, after the final

params = os.path.join(ENG, PNAME)
res = {}
timing = {}
for name, stream in streams.items():
    t0 = time.perf_counter()
    st = E.SeasonState(params=params, library_seasons=(int(LIB0), int(LIB1)))
    i = 0
    vals = {}
    ms = []
    for r, s in sorted(evals + post):
        thr = opens[DATES[r]] + s + 1.5
        while i < len(stream) and stream[i][0] <= thr:
            st.on_message(stream[i][1])
            i += 1
        t1 = time.perf_counter()
        fv = E.fair_values(st)
        ms.append((time.perf_counter() - t1) * 1000)
        pp = st.pattern_posterior()
        vals[(r, s)] = ({k: tuple(v) for k, v in fv.items()}, (pp or {}).get("p"), st.phase())
    res[name] = vals
    ms.sort()
    timing[name] = {"total_s": time.perf_counter() - t0, "call_ms_median": stx.median(ms), "call_ms_p95": ms[int(.95 * len(ms))]}
    print(f"  stream {name}: {i} msgs fed, {len(evals)} evaluations, {timing[name]['total_s']:.1f} s "
          f"(call median {timing[name]['call_ms_median']:.0f} ms, p95 {timing[name]['call_ms_p95']:.0f} ms)", flush=True)

out = {"params": PNAME, "library": [int(LIB0), int(LIB1)], "timing": timing, "lost": lost}


# evaluation points inside a disconnect window (last frame -> next ack + 2 s), where the recorded feed is stale
stale = set()
for c in FL.connections(frames)[:-1]:
    for r, d in DATES.items():
        a0, a1 = c["last"] - opens[d], c["next_ack"] + 2.0 - opens[d]
        for (rr, s) in evals:
            if rr == r and a0 <= s + 1.5 <= a1 + 10:
                stale.add((rr, s))
print("evaluation points inside or just after a disconnect (excluded in the 'fresh' rows):", sorted(stale))


def compare(a, b, label, skip=()):
    ev = [k for k in evals if k not in skip]
    print(f"\n== {label}: |mu_{a} - mu_{b}| over {len(ev)} storm evaluations (every 10 s, s = 60..540, 3 rounds)")
    print(f"  {'sym':6s} {'max':>7s} {'mean':>7s} {'max/sd':>7s} {'mean/sd':>7s} {'max/thr':>7s} {'mean/thr':>8s} {'max/entry':>9s}  worst at")
    tab = {}
    for sym in SYM:
        d, dsd, dth, den = [], [], [], []
        worst = (0, None)
        for (r, s) in ev:
            ma, sa = res[a][(r, s)][0][sym]
            mb, sb = res[b][(r, s)][0][sym]
            P = px[r][s // 2][sym]
            sd = (sa + sb) / 2
            k = KR[r]
            thr = k * sd + H * P
            ent = k * sd + (H + GAMMA) * P
            x = abs(ma - mb)
            d.append(x); dsd.append(x / sd if sd > 0 else 0); dth.append(x / thr); den.append(x / ent)
            if x > worst[0]:
                worst = (x, (r, s))
        tab[sym] = {"max": max(d), "mean": stx.fmean(d), "max_over_sd": max(dsd), "mean_over_sd": stx.fmean(dsd),
                    "max_over_thr": max(dth), "mean_over_thr": stx.fmean(dth), "max_over_entry": max(den),
                    "worst_at": worst[1]}
        t = tab[sym]
        print(f"  {sym:6s} {t['max']:7.3f} {t['mean']:7.3f} {t['max_over_sd']:7.3f} {t['mean_over_sd']:7.3f} "
              f"{t['max_over_thr']:7.3f} {t['mean_over_thr']:8.3f} {t['max_over_entry']:9.3f}  r{worst[1][0]} s={worst[1][1]}")
    # pattern posterior difference
    dp = []
    for (r, s) in ev:
        pa, pb = res[a][(r, s)][1], res[b][(r, s)][1]
        if pa and pb:
            dp.append((max(abs(pa[c] - pb[c]) for c in pa), r, s))
    dp.sort(reverse=True)
    print(f"  pattern posterior max |dp| over classes: max {dp[0][0]:.3f} at r{dp[0][1]} s={dp[0][2]}, "
          f"mean {stx.fmean([x for x, _, _ in dp]):.4f}, share of evaluations > 0.05: {sum(1 for x, _, _ in dp if x > 0.05) / len(dp):.3f}")
    return tab


def s3(name):
    """Simplified S3 on 10-s steps: full-limit targets, decision on consensus row s/2, fill at row s/2+1 +- h."""
    pos = {s_: 0 for s_ in SYM if s_ != "REIN"}
    pnl = 0.0
    trades = 0
    path = {}
    for r in (1, 2, 3):
        for s_ in pos:
            pos[s_] = 0
        for s in range(80, 541, 10):
            fv = res[name][(r, s)][0]
            for sym in pos:
                mu, sd = fv[sym]
                P = px[r][s // 2][sym]
                ask, bid = P * (1 + H), P * (1 - H)
                thr = KR[r] * sd + GAMMA * P
                tgt = pos[sym]
                if mu - ask > thr:
                    tgt = limits[sym]
                elif bid - mu > thr:
                    tgt = -limits[sym]
                elif pos[sym] > 0 and mu < bid:
                    tgt = 0
                elif pos[sym] < 0 and mu > ask:
                    tgt = 0
                if tgt != pos[sym]:
                    q = tgt - pos[sym]
                    F = px[r][s // 2 + 1][sym] * (1 + H if q > 0 else 1 - H)
                    pnl -= q * F
                    pos[sym] = tgt
                    trades += 1
                path[(r, s, sym)] = pos[sym]
        for sym in pos:  # rounds 1-2: flatten at 566 s; round 3: settle
            q = -pos[sym]
            if q == 0:
                continue
            if r < 3:
                F = px[r][283][sym] * (1 + H if q > 0 else 1 - H)
                pnl -= q * F
                trades += 1
            else:
                pnl -= q * settle[sym]
            pos[sym] = 0
    return pnl, trades, path


tabs = {"T_vs_R": compare("T", "R", "testnet feed (T) vs research feed (R)"),
        "C_vs_R": compare("C", "R", "advisory noise only (C) vs research (R)"),
        "D_vs_R": compare("D", "R", "disconnect gaps + snapshots only (D) vs research (R)"),
        "T_vs_R_fresh": compare("T", "R", "testnet feed (T) vs research (R), excluding stale points", skip=stale)}
out["tables"] = tabs
print("\n== S3 decisions (simplified, 10-s steps, h = 0.25%, gamma = 0.5%, k = 0.1/0.1/0.2, full-limit targets, no REIN)")
paths = {}
for name in ("R", "T", "C", "D"):
    pnl, ntr, path = s3(name)
    paths[name] = path
    out.setdefault("s3", {})[name] = {"pnl": pnl, "trades": ntr}
    print(f"  {name}: P&L ${pnl:,.0f}, {ntr} target changes")
for a in ("T", "C", "D"):
    diff = [(k, paths[a][k], paths["R"][k]) for k in paths["R"] if paths[a][k] != paths["R"][k]]
    print(f"  target position differs from R in {len(diff)} of {len(paths['R'])} (round, s, symbol) cells"
          + (f"; e.g. {diff[:6]}" if diff else ""))
    out["s3"][a]["cells_differ"] = len(diff)
print("\n== after the storm (s = 560 storm over, 600 after the preliminary report, 760 after the final report)")
for a in ("T", "C", "D"):
    worst = {}
    for (r, s) in post:
        for sym in SYM:
            x = abs(res[a][(r, s)][0][sym][0] - res["R"][(r, s)][0][sym][0])
            worst[(r, s)] = max(worst.get((r, s), (0.0, "")), (x, sym))
    print(f"  {a} vs R: max |dmu| (symbol) by (round, s):", {f"r{r}s{s}": (round(v[0], 4), v[1]) for (r, s), v in sorted(worst.items())})
    out.setdefault("post", {})[a] = {f"r{r}s{s}": v for (r, s), v in worst.items()}
fin = {name: res[name][(3, 760)][0] for name in res}
print("  round 3 after the final report: engine mean - settlement.json, per stream (PORT, GRID, FERRY, GEN, ARCH, REBLD):")
for name in ("R", "T", "C", "D"):
    print(f"    {name}: " + ", ".join(f"{sym} {fin[name][sym][0] - settle[sym]:+.3f}" for sym in ("PORT", "GRID", "FERRY", "GEN", "ARCH", "REBLD")))
# residual difference once the round-2 gap is closed by the snapshot at s ~ 271
for a in ("T", "D"):
    late = [(abs(res[a][(2, s)][0][sym][0] - res["R"][(2, s)][0][sym][0]), sym, s) for s in range(280, 541, 10) for sym in SYM]
    late.sort(reverse=True)
    print(f"  {a} vs R, round 2 after the resync snapshot (s >= 280): max |dmu| {late[0][0]:.3f} ({late[0][1]} s={late[0][2]})")
json.dump(out, open(OUT, "w"), indent=1, default=str)
print("\nwrote", OUT)
