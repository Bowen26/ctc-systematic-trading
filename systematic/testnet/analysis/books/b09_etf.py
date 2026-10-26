"""ETF book vs basket of component books (book-based lead-lag; the playbook's ETF book-lag check).
usage: python3 -I b09_etf.py recording.jsonl history_dir out.json
NAV_book(t) = sum w_i * touch mid_i(t) over components (ARCH = 1 PORT + 1 ISLE + 2 AQUA + 1 GRID + 2 FERRY + 1 HAVEN;
REBLD = 2 RECON + 2 MAT + 1 GEN). Gap(t, lag) = ETF mid(t) / NAV(t - lag) - 1 (bps), sampled every 0.5 s.
Reports the RMS gap by lag (best lag = how far the ETF book trails the component books), the gap distribution,
and how often |gap| exceeds the ETF half-spread + 25 bp (a marketable arbitrage on the visible touch).
"""
import sys, os, json, bisect, statistics, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
order = sorted(rounds, key=lambda d: rounds[d]["open"])
BASK = {"ARCH": {"PORT": 1, "ISLE": 1, "AQUA": 2, "GRID": 1, "FERRY": 2, "HAVEN": 1},
        "REBLD": {"RECON": 2, "MAT": 2, "GEN": 1}}
FR = {(d, s): [f for f in D["books"][s] if f.date == d] for d in order for s in bl.SYMBOLS}
RA = {k: [f.ra for f in v] for k, v in FR.items()}


def frame_at(d, s, t):
    fr, ras = FR[(d, s)], RA[(d, s)]
    x = rounds[d]["open"] + t
    j = bisect.bisect_right(ras, x) - 1
    if j < 0 or bl.seg_at(D["segs"], x) != fr[j].seg:
        return None
    return fr[j]


def nav(d, etf, t):
    tot = 0.0
    for s, w in BASK[etf].items():
        f = frame_at(d, s, t)
        if f is None or not f.mid:
            return None
        tot += w * f.mid
    return tot


res = {}
LAGS = [x * 0.5 for x in range(0, 41)]
for etf in BASK:
    res[etf] = {}
    for ph, a, b in (("pre", 5, 58), ("storm", 60, 540), ("after", 540, 780), ("all", 5, 780)):
        rows = []
        for lag in LAGS:
            g = []
            for d in order:
                t = a + lag
                while t < b:
                    f = frame_at(d, etf, t); n = nav(d, etf, t - lag)
                    if f is not None and f.mid and n:
                        g.append((f.mid / n - 1) * 1e4)
                    t += 0.5
            if g:
                mu = statistics.fmean(g)
                rows.append((lag, math.sqrt(statistics.fmean((x - mu) ** 2 for x in g)), mu, len(g)))
        best = min(rows, key=lambda r: r[1])
        res[etf][ph] = {"rms_by_lag": rows, "best_lag_s": best[0], "best_rms_bps": best[1], "rms_lag0": rows[0][1],
                        "mean_gap_lag0": rows[0][2]}
        print(f"{etf} {ph:5s}: RMS(gap - mean) at lag 0 {rows[0][1]:.1f} bp (mean {rows[0][2]:+.1f}); best lag {best[0]:.1f} s -> {best[1]:.1f} bp; "
              f"lags 2/4/6/8/10 s: " + " ".join(f"{r[1]:.0f}" for r in rows if r[0] in (2, 4, 6, 8, 10)))
    # tradable gap: ETF touch vs NAV (same time), how often the ETF ask < NAV*(1-x) or bid > NAV*(1+x)
    for x in (0.0025, 0.005, 0.01):
        n = hit = 0
        dur = []
        for d in order:
            t = 60.0
            run = 0
            while t < 780:
                f = frame_at(d, etf, t); nv = nav(d, etf, t)
                if f is not None and f.bids and f.asks and nv:
                    n += 1
                    # centre the NAV on the round's median premium to remove a static level offset
                    if f.asks[0][0] < nv * (1 - x) or f.bids[0][0] > nv * (1 + x):
                        hit += 1; run += 0.5
                    elif run:
                        dur.append(run); run = 0
                t += 0.5
        res[etf][f"touch_cross_nav_{x*1e4:.0f}bp"] = {"share": hit / n if n else None, "episodes": len(dur),
                                                     "median_dur_s": statistics.median(dur) if dur else None}
        print(f"  {etf}: share of 60-780 s with ETF touch through NAV_book by >{x*1e4:.0f} bp: {100*hit/n:.1f}% "
              f"({len(dur)} episodes, median {statistics.median(dur) if dur else 0:.1f} s)")
json.dump(res, open(out, "w"), indent=1)
