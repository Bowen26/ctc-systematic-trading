"""Task 5: does the book skew after one-sided taker flow? (price impact that persists = inventory skew)
usage: python3 -I b07_skew.py recording.jsonl history_dir out.json

2-s intervals i of each round (real 2i..2i+2). y_i = 1e4*log(mid(2i+2)/mid(2i)) (touch mid);
f_i = signed taker volume in the interval / L (buy +); c_i = 1e4*log(cons_{i+1}/cons_i).
OLS: y_i = a + sum_{k=0..K} b_k f_{i-k} + sum_{k=-2..K} g_k c_{i-k}.
Cumulative impulse response to one L of net buying after h intervals: B_h = sum_{k<=h} b_k (bps per L).
A market maker that skews quotes on inventory gives B_h > 0 that does not decay; pure touch depletion that the
makers refill at the old price gives B_0 > 0 and B_h -> 0. Also reports the event study: mean signed mid move
after taker orders >= 0.25 L, net of the consensus move, at +2/+10/+30 s.
"""
import sys, os, json, math, bisect, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
order = sorted(rounds, key=lambda d: rounds[d]["open"])
K = 15


def solve(A, b):
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            raise ValueError("singular")
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def inv(A):
    n = len(A)
    cols = []
    for j in range(n):
        e = [1.0 if i == j else 0.0 for i in range(n)]
        cols.append(solve(A, e))
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def ols(X, y):
    p = len(X[0])
    XtX = [[sum(x[i] * x[j] for x in X) for j in range(p)] for i in range(p)]
    Xty = [sum(x[i] * yy for x, yy in zip(X, y)) for i in range(p)]
    beta = solve(XtX, Xty)
    res = [yy - sum(bb * xx for bb, xx in zip(beta, x)) for x, yy in zip(X, y)]
    s2 = sum(r * r for r in res) / max(1, len(y) - p)
    Vi = inv(XtX)
    return beta, Vi, s2, res


def series(sym):
    """per round: lists y, f, c indexed by interval i (None where book missing)."""
    fr = D["books"][sym]; ras = [f.ra for f in fr]; L = bl.LIMITS[sym]
    out_ = {}
    trades = [t for t in D["trades"][sym] if t.ra - bl.iso_to_unix_local(t.server_ts) < 1]
    tra = [t.ra for t in trades]
    for d in order:
        o = rounds[d]["open"]
        cons = D["consensus"][d][sym]

        def mid(t):
            x = o + t
            j = bisect.bisect_right(ras, x) - 1
            if j < 0 or bl.seg_at(D["segs"], x) != fr[j].seg or fr[j].date != d:
                return None
            return fr[j].mid
        ys, fs, cs = [], [], []
        for i in range(390):
            m0, m1 = mid(2 * i), mid(2 * i + 2)
            ys.append(1e4 * math.log(m1 / m0) if m0 and m1 else None)
            cs.append(1e4 * math.log(cons[i + 1] / cons[i]) if i + 1 < len(cons) else None)
            # signed flow
            a_, b_ = bisect.bisect_right(tra, o + 2 * i), bisect.bisect_right(tra, o + 2 * i + 2)
            s = 0.0
            for tr in trades[a_:b_]:
                j = bisect.bisect_left(ras, tr.ra) - 1
                if j < 0 or fr[j].seg != tr.seg:
                    continue
                pre = fr[j]
                if pre.asks and tr.price >= pre.asks[0][0] - 1e-9:
                    s += tr.qty
                elif pre.bids and tr.price <= pre.bids[0][0] + 1e-9:
                    s -= tr.qty
            fs.append(s / L)
        out_[d] = (ys, fs, cs)
    return out_


def design(S, lo, hi):
    X, y = [], []
    for d, (ys, fs, cs) in S.items():
        for i in range(max(lo, K), min(hi, 389 - 2)):
            if ys[i] is None:
                continue
            row = [1.0] + [fs[i - k] for k in range(K + 1)]
            cc = [cs[i - k] for k in range(-2, K + 1)]
            if any(v is None for v in cc):
                continue
            X.append(row + cc); y.append(ys[i])
    return X, y


res = {}
PH = {"pre+storm+after (excl. 56-120 s)": None, "storm 120-540": (60, 270), "after 540-780": (270, 389)}
print(f"impulse response of touch mid (bps) to 1 L of net taker buying; distributed lag K={K} intervals of 2 s")
pooled = {name: ([], []) for name in PH}
for sym in bl.SYMBOLS:
    S = series(sym)
    res[sym] = {}
    for name, rng in PH.items():
        if rng is None:
            X1, y1 = design(S, 0, 28); X2, y2 = design(S, 60, 389)
            X, y = X1 + X2, y1 + y2
        else:
            X, y = design(S, *rng)
        pooled[name][0].extend(X); pooled[name][1].extend(y)
        if len(y) < 60:
            continue
        beta, Vi, s2, _ = ols(X, y)
        b = beta[1:K + 2]
        cum = [sum(b[:h + 1]) for h in range(K + 1)]
        # se of cumulative sums
        se = []
        for h in range(K + 1):
            idx = list(range(1, h + 2))
            v = sum(Vi[i][j] for i in idx for j in idx) * s2
            se.append(math.sqrt(max(v, 0)))
        g = beta[K + 2:]
        res[sym][name] = {"n": len(y), "B": {str(2 * h): cum[h] for h in range(K + 1)},
                          "B_se": {str(2 * h): se[h] for h in range(K + 1)},
                          "cons_coef_sum_lag0..K": sum(g[2:]), "cons_coef_leads": g[:2]}
    r = res[sym].get("storm 120-540")
    a = res[sym].get("after 540-780")
    if r and a:
        print(f"  {sym:6s} storm: B0 {r['B']['0']:+6.1f}±{r['B_se']['0']:.1f}  B10s {r['B']['10']:+6.1f}±{r['B_se']['10']:.1f}  B30s {r['B']['30']:+6.1f}±{r['B_se']['30']:.1f}"
              f"  (cons pass-through {r['cons_coef_sum_lag0..K']:.2f}) | after: B0 {a['B']['0']:+6.1f}  B10s {a['B']['10']:+6.1f}±{a['B_se']['10']:.1f}  B30s {a['B']['30']:+6.1f}±{a['B_se']['30']:.1f}")
print("\npooled across the 12 symbols (flow in units of each symbol's L):")
res["_pooled"] = {}
for name, (X, y) in pooled.items():
    beta, Vi, s2, _ = ols(X, y)
    b = beta[1:K + 2]
    cum = [sum(b[:h + 1]) for h in range(K + 1)]
    se = [math.sqrt(max(sum(Vi[i][j] for i in range(1, h + 2) for j in range(1, h + 2)) * s2, 0)) for h in range(K + 1)]
    res["_pooled"][name] = {"n": len(y), "B": {str(2 * h): cum[h] for h in range(K + 1)}, "B_se": {str(2 * h): se[h] for h in range(K + 1)}}
    print(f"  {name:34s} n={len(y):5d}: " + " ".join(f"{2*h}s:{cum[h]:+.1f}±{se[h]:.1f}" for h in (0, 1, 2, 5, 10, 15)))

# event study: orders >= 0.25 L
print("\nevent study: taker orders (one bundle, one side) of >= 0.15 L; signed touch-mid move net of consensus move (bps)")
ev_all = {h: [] for h in (2, 10, 30)}
ev_info = []
for sym in bl.SYMBOLS:
    fr = D["books"][sym]; ras = [f.ra for f in fr]; L = bl.LIMITS[sym]
    trades = [t for t in D["trades"][sym] if t.date in rounds and 6 <= t.t < 740 and t.ra - bl.iso_to_unix_local(t.server_ts) < 1]
    groups = []
    for tr in trades:
        j = bisect.bisect_left(ras, tr.ra) - 1
        if j < 0 or fr[j].seg != tr.seg:
            continue
        pre = fr[j]
        s = 1 if (pre.asks and tr.price >= pre.asks[0][0] - 1e-9) else -1 if (pre.bids and tr.price <= pre.bids[0][0] + 1e-9) else 0
        if not s or not pre.mid:
            continue
        if groups and abs(groups[-1]["ra"] - tr.ra) < 1e-3 and groups[-1]["s"] == s:
            groups[-1]["q"] += tr.qty
        else:
            groups.append({"ra": tr.ra, "t": tr.t, "d": tr.date, "s": s, "q": tr.qty, "m0": pre.mid, "seg": tr.seg})
    for g in groups:
        if g["q"] < 0.15 * L or 56 <= g["t"] < 120:
            continue
        row = {"sym": sym, "t": g["t"], "q_over_L": g["q"] / L}
        for h in (2, 10, 30):
            x = g["ra"] + h
            j = bisect.bisect_right(ras, x) - 1
            if j < 0 or fr[j].seg != g["seg"] or fr[j].date != g["d"] or not fr[j].mid or fr[j].t >= 780:
                continue
            c0 = bl.consensus_at(D["consensus"][g["d"]][sym], g["t"]); c1 = bl.consensus_at(D["consensus"][g["d"]][sym], g["t"] + h)
            if not c0 or not c1:
                continue
            mv = g["s"] * (1e4 * math.log(fr[j].mid / g["m0"]) - 1e4 * math.log(c1 / c0))
            row[str(h)] = mv
            ev_all[h].append(mv)
        ev_info.append(row)
for h, v in ev_all.items():
    if v:
        se = statistics.pstdev(v) / math.sqrt(len(v))
        print(f"  +{h:2d}s: n={len(v)} mean {statistics.fmean(v):+.1f} bp (se {se:.1f}), median {statistics.median(v):+.1f}")
res["_event_study"] = {str(h): {"n": len(v), "mean": statistics.fmean(v) if v else None,
                                "se": statistics.pstdev(v) / math.sqrt(len(v)) if v else None,
                                "median": statistics.median(v) if v else None} for h, v in ev_all.items()}
json.dump(res, open(out, "w"), indent=1)
