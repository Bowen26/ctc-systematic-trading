"""Task 1: book statistics by symbol and phase, time-weighted (book state sampled on a 0.25-s grid).
usage: python3 -I b02_phases.py recording.jsonl history_dir out.json"""
import sys, os, json, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
DT = 0.25
PCTS = [0.0025, 0.005, 0.01, 0.02]
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}


def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    return xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))]


def collect(sym, phase_name, a, b, only=None):
    rows = []
    frames_in = 0
    for d, v in rounds.items():
        if only and d not in only:
            continue
        fr = [f for f in D["books"][sym] if f.date == d]
        frames_in += sum(1 for f in fr if a <= f.t < b)
        for t, f, age in bl.sample(fr, D["segs"], v["open"], a, b, DT):
            rows.append((d, t, f, age))
    return rows, frames_in


res = {}
print(f"time-weighted on a {DT}-s grid; pooled over rounds {sorted(rounds, key=lambda d: rounds[d]['open'])}")
print("hs = half-spread bps of mid (median [p10,p90]); touch = median bid/ask touch qty; dep = median per-side depth within"
      " 0.25/0.5/1/2% of mid (shares, mean of the two sides); upd = book frames per covered second;"
      " 1s/0s = % of time one-sided / empty")
for sym in bl.SYMBOLS:
    L = bl.LIMITS[sym]
    res[sym] = {}
    print(f"\n{sym} (L={L})")
    for name, a, b in bl.PHASES:
        rows, nfr = collect(sym, name, a, b)
        cov = len(rows) * DT
        if not rows:
            print(f"  {name:10s} no data")
            continue
        two = [r for r in rows if r[2].bids and r[2].asks]
        one = [r for r in rows if bool(r[2].bids) != bool(r[2].asks)]
        empty = [r for r in rows if not r[2].bids and not r[2].asks]
        hs, tb, ta, dep, nlev = [], [], [], {p: [] for p in PCTS}, []
        lvl = {"bid": [[] for _ in range(6)], "ask": [[] for _ in range(6)]}
        age = [r[3] for r in rows]
        for d, t, f, ag in two:
            m = f.mid
            hs.append((f.asks[0][0] - f.bids[0][0]) / 2 / m * 1e4)
            tb.append(f.bids[0][1]); ta.append(f.asks[0][1])
            for p in PCTS:
                db = sum(n for pr, n in f.bids if pr >= m * (1 - p))
                da = sum(n for pr, n in f.asks if pr <= m * (1 + p))
                dep[p].append(0.5 * (db + da))
            nlev.append(0.5 * (len(f.bids) + len(f.asks)))
            for k, (pr, n) in enumerate(f.bids[:6]):
                lvl["bid"][k].append(((m - pr) / m * 1e4, n))
            for k, (pr, n) in enumerate(f.asks[:6]):
                lvl["ask"][k].append(((pr - m) / m * 1e4, n))
        r = {"covered_s": cov, "frames": nfr, "updates_per_s": nfr / cov if cov else None,
             "pct_two_sided": 100 * len(two) / len(rows), "pct_one_sided": 100 * len(one) / len(rows),
             "pct_empty": 100 * len(empty) / len(rows),
             "half_spread_bps": {"median": q(hs, .5), "mean": statistics.fmean(hs) if hs else None,
                                 "p10": q(hs, .1), "p90": q(hs, .9)},
             "touch_bid": {"median": q(tb, .5), "mean": statistics.fmean(tb) if tb else None},
             "touch_ask": {"median": q(ta, .5), "mean": statistics.fmean(ta) if ta else None},
             "depth_within": {f"{p*100:g}%": {"median": q(dep[p], .5), "mean": statistics.fmean(dep[p]) if dep[p] else None,
                                              "p10": q(dep[p], .1), "median_over_L": q(dep[p], .5) / L} for p in PCTS},
             "levels_mean": statistics.fmean(nlev) if nlev else None,
             "book_age_s_median": q(age, .5),
             "shape": {side: [{"level": k + 1, "share_present": len(v) / len(two) if two else 0,
                               "dist_bps_median": q([x for x, _ in v], .5), "qty_median": q([n for _, n in v], .5)}
                              for k, v in enumerate(lvl[side]) if v] for side in lvl}}
        res[sym][name] = r
        dp = "/".join(f"{q(dep[p], .5):.0f}" for p in PCTS)
        print(f"  {name:10s} cov {cov:6.0f}s upd {r['updates_per_s']:.2f}/s  hs {q(hs,.5):6.1f} [{q(hs,.1):5.1f},{q(hs,.9):6.1f}]"
              f"  touch {q(tb,.5):.0f}/{q(ta,.5):.0f}  dep {dp}  (L/4={L/4:.0f})  lev {r['levels_mean']:.2f}"
              f"  1s {r['pct_one_sided']:.1f}% 0s {r['pct_empty']:.1f}%")
    # shape, storm phase pooled
    sh = res[sym].get("storm", {}).get("shape")
    if sh:
        for side in ("bid", "ask"):
            print(f"  shape storm {side}: " + "  ".join(
                f"L{x['level']}: {x['dist_bps_median']:.0f}bp x{x['qty_median']} ({100*x['share_present']:.0f}%)" for x in sh[side]))
    sh = res[sym].get("pre", {}).get("shape")
    if sh:
        for side in ("bid", "ask"):
            print(f"  shape pre   {side}: " + "  ".join(
                f"L{x['level']}: {x['dist_bps_median']:.0f}bp x{x['qty_median']} ({100*x['share_present']:.0f}%)" for x in sh[side]))

# first seconds after the open: how long is the book one-sided/empty/far-only
print("\n== first 10 s of each round: share of time with at most one level per side (open book state) ==")
for sym in bl.SYMBOLS:
    parts = []
    for d, v in sorted(rounds.items(), key=lambda kv: kv[1]["open"]):
        fr = [f for f in D["books"][sym] if f.date == d]
        sm = list(bl.sample(fr, D["segs"], v["open"], 0, 10, DT))
        if not sm:
            parts.append(f"{d}: n/a"); continue
        thin = [t for t, f, a in sm if len(f.bids) <= 1 and len(f.asks) <= 1]
        last_thin = max(thin) if thin else None
        parts.append(f"{d}: {100*len(thin)/len(sm):.0f}% (last thin t={last_thin})")
    print(f"  {sym}: " + "; ".join(parts))
json.dump(res, open(out, "w"), indent=1)
