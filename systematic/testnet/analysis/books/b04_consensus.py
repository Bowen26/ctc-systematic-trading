"""Task 4: book mid vs research consensus (market.csv) of the same round.
usage: python3 -I b04_consensus.py recording.jsonl history_dir out.json

Alignment: row i of market.csv is real second 2i of the round; the book is placed on the same clock with the
round open fitted from storm-observation news frames (b01). For a book sampling offset delta (s), compare
book(2i + delta) with consensus row i. Measures:
  - offset (mid - cons)/cons in bps, by phase, at delta = +1 s (book half-way through the row) and at the best delta
  - RMS offset as a function of delta in 0.5-s steps (-8..+8 s): where the book best matches the consensus
  - correlation of 2-s changes, d mid(2i+1) vs d cons(i+k), k = -4..+4 rows
  - quiet-phase noise: in 0-58 s and 600-738 s, consensus 2-s changes are mostly quote noise; if the book
    carries the same noise, d mid correlates with d cons there and mid - cons has small RMS
Mid definitions: 'touch' = (bid1+ask1)/2; 'l2' = (bid2+ask2)/2 (less affected by touch depletion);
'ladder' = midpoint of the 4 inner levels' mean bid and mean ask prices.
"""
import sys, os, json, math, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
order = sorted(rounds, key=lambda d: rounds[d]["open"])


def mids(f):
    if not (f.bids and f.asks):
        return None
    r = {"touch": 0.5 * (f.bids[0][0] + f.asks[0][0])}
    if len(f.bids) >= 2 and len(f.asks) >= 2:
        r["l2"] = 0.5 * (f.bids[1][0] + f.asks[1][0])
    if len(f.bids) >= 4 and len(f.asks) >= 4:
        r["ladder"] = 0.5 * (statistics.fmean(p for p, _ in f.bids[:4]) + statistics.fmean(p for p, _ in f.asks[:4]))
    return r


def book_at(frames, ras, open_, t):
    import bisect
    x = open_ + t
    j = bisect.bisect_right(ras, x) - 1
    if j < 0:
        return None
    f = frames[j]
    s = bl.seg_at(D["segs"], x)
    if s is None or s != f.seg or f.date != frames[j].date:
        return None
    return f


def corr(a, b):
    n = len(a)
    if n < 5:
        return float("nan")
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a)); sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa > 0 and sb > 0 else float("nan")


def phase_rows(name):
    return {"pre": range(0, 30), "early": range(30, 60), "storm": range(60, 270), "after": range(270, 390),
            "quiet_pre": range(0, 29), "quiet_after": range(300, 369), "rep_prelim": range(284, 300),
            "rep_final": range(369, 380), "all": range(0, 390)}[name]


res = {"by_symbol": {}}
DELTAS = [x * 0.5 for x in range(-16, 17)]
print("bps = (book mid - consensus)/consensus * 1e4; book sampled at real 2i + delta vs consensus row i")
for sym in bl.SYMBOLS:
    S = {}
    # per round frames/ras
    fr = {d: [f for f in D["books"][sym] if f.date == d] for d in order}
    ra = {d: [f.ra for f in fr[d]] for d in order}
    cons = {d: D["consensus"][d][sym] for d in order}
    # RMS vs delta, by phase group, touch mid
    rms_delta = {}
    for kind in ("touch", "l2", "ladder"):
        for ph in ("quiet_pre", "storm", "quiet_after", "all"):
            row = []
            for dl in DELTAS:
                e = []
                for d in order:
                    for i in phase_rows(ph):
                        t = 2 * i + dl
                        if t < 0 or t >= 780:
                            continue
                        f = book_at(fr[d], ra[d], rounds[d]["open"], t)
                        if f is None:
                            continue
                        m = mids(f)
                        if not m or kind not in m:
                            continue
                        c = cons[d][i]
                        e.append((m[kind] - c) / c * 1e4)
                row.append((dl, math.sqrt(statistics.fmean(x * x for x in e)) if e else float("nan"),
                            statistics.fmean(e) if e else float("nan"), len(e)))
            rms_delta[f"{kind}|{ph}"] = row
    S["rms_vs_delta"] = rms_delta
    best = {k: min(v, key=lambda r: r[1] if not math.isnan(r[1]) else 1e9) for k, v in rms_delta.items()}
    S["best_delta"] = {k: {"delta": b[0], "rms_bps": b[1], "mean_bps": b[2], "n": b[3]} for k, b in best.items()}
    # offset distribution by phase at delta=+1 (touch & l2)
    dist = {}
    for kind in ("touch", "l2", "ladder"):
        for ph in ("pre", "early", "storm", "after", "rep_prelim", "rep_final"):
            for dl in (1.0, best[f"{kind}|all"][0]):
                e = []
                for d in order:
                    for i in phase_rows(ph):
                        t = 2 * i + dl
                        if not (0 <= t < 780):
                            continue
                        f = book_at(fr[d], ra[d], rounds[d]["open"], t)
                        m = mids(f) if f else None
                        if not m or kind not in m:
                            continue
                        c = cons[d][i]
                        e.append((m[kind] - c) / c * 1e4)
                e.sort()
                if e:
                    dist[f"{kind}|{ph}|{dl}"] = {"n": len(e), "mean": statistics.fmean(e), "sd": statistics.pstdev(e),
                                                 "p05": e[int(.05 * (len(e) - 1))], "p50": e[int(.5 * (len(e) - 1))],
                                                 "p95": e[int(.95 * (len(e) - 1))], "mean_abs": statistics.fmean(abs(x) for x in e)}
    S["offset"] = dist
    # cross-correlation of 2-s changes: book sampled at 2i+1 (mid of row i), changes d_i = m(2i+1)-m(2i-1)
    xc = {}
    for kind in ("touch", "l2", "ladder"):
        for ph in ("quiet_pre", "storm", "quiet_after", "all"):
            res_k = []
            for k in range(-4, 5):
                a, b = [], []
                for d in order:
                    for i in phase_rows(ph):
                        if i < 1 or i + k < 1 or i + k >= 390:
                            continue
                        f1 = book_at(fr[d], ra[d], rounds[d]["open"], 2 * i + 1)
                        f0 = book_at(fr[d], ra[d], rounds[d]["open"], 2 * i - 1)
                        if not f1 or not f0:
                            continue
                        m1, m0 = mids(f1), mids(f0)
                        if not m1 or not m0 or kind not in m1 or kind not in m0:
                            continue
                        c1, c0 = cons[d][i + k], cons[d][i + k - 1]
                        a.append((m1[kind] - m0[kind]) / m0[kind] * 1e4)
                        b.append((c1 - c0) / c0 * 1e4)
                res_k.append((k, corr(a, b), len(a),
                              math.sqrt(statistics.fmean(x * x for x in a)) if a else None,
                              math.sqrt(statistics.fmean(x * x for x in b)) if b else None))
            xc[f"{kind}|{ph}"] = res_k
    S["xcorr"] = xc
    res["by_symbol"][sym] = S
    # print summary
    print(f"\n{sym}")
    for kind in ("touch", "l2", "ladder"):
        line = []
        for ph in ("quiet_pre", "storm", "quiet_after", "all"):
            b = S["best_delta"][f"{kind}|{ph}"]
            r1 = [r for r in rms_delta[f"{kind}|{ph}"] if r[0] == 1.0][0]
            line.append(f"{ph}: best d={b['delta']:+.1f}s rms {b['rms_bps']:.1f} (mean {b['mean_bps']:+.1f}) | d=+1 rms {r1[1]:.1f}")
        print(f"  {kind:6s} " + " ; ".join(line))
    for kind in ("touch", "ladder"):
        for ph in ("quiet_pre", "storm", "quiet_after"):
            r = xc[f"{kind}|{ph}"]
            kb = max(r, key=lambda z: z[1] if not math.isnan(z[1]) else -9)
            print(f"  xcorr {kind:6s} {ph:11s}: " + " ".join(f"k{z[0]:+d}:{z[1]:.2f}" for z in r) +
                  f"  -> peak k={kb[0]:+d}; rms d_book {r[4][3]:.1f} bp, d_cons {r[4][4]:.1f} bp (n={r[4][2]})")
json.dump(res, open(out, "w"), indent=1)
