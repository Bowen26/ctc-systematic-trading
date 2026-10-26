"""Task 2: cost of a marketable order that walks the visible book, by symbol, phase and clip size; cost-model fit.
usage: python3 -I b03_costs.py recording.jsonl history_dir cost_model.json costs_detail.json [skew.json]

Book state sampled on a 0.25-s grid (time-weighted). For clip q: buy walks the asks, sell walks the bids.
cost_bps = (vwap - mid)/mid*1e4 for a buy, (mid - vwap)/mid*1e4 for a sell, mid = touch mid, on the filled part.
unfilled = share of q beyond all visible levels. 'cost_pen' charges unfilled shares at the worst visible price
(a lower bound on their true cost). Fits per symbol and phase, on the mean of buy and sell:
  quad:   cost(x) = h + g1*x + g2*x^2,  x = q/L, least squares over x in {10/L, 1/40, 2/40, ..., 40/40}
  quad05: same, fitted on x <= 0.5 only (the clip range the bot uses)
  lin:    cost(x) = h + gamma*x with h fixed at the 10-share cost (RT-fill-model form), fitted on x <= 0.5
plus the full table (piecewise-linear interpolation is the recommended form; the book has a hard 3L wall at ~8%).
"""
import sys, os, json, statistics, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out_model, out_detail = sys.argv[1:5]
skew_path = sys.argv[5] if len(sys.argv) > 5 else None
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
DT = 0.25
GRID = [i / 40 for i in range(1, 41)]


def lsq(xs, ys, cols):
    """least squares with basis functions cols (list of callables)"""
    A = [[f(x) for f in cols] for x in xs]
    p = len(cols)
    M = [[sum(a[i] * a[j] for a in A) for j in range(p)] + [sum(a[i] * y for a, y in zip(A, ys))] for i in range(p)]
    for c in range(p):
        piv = max(range(c, p), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        for r in range(p):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, p + 1):
                    M[r][k] -= f * M[c][k]
    beta = [M[i][p] / M[i][i] for i in range(p)]
    pred = [sum(b * f(x) for b, f in zip(beta, cols)) for x in xs]
    rmse = math.sqrt(statistics.fmean((a - b) ** 2 for a, b in zip(pred, ys)))
    return beta, rmse


def walk_cost(levels, q, mid, sign):
    filled, vwap, worst = bl.walk(levels, q)
    if not filled:
        return None, 1.0, None
    c = sign * (vwap - mid) / mid * 1e4
    un = (q - filled) / q
    pen = c if un == 0 else sign * ((vwap * filled + worst * (q - filled)) / q - mid) / mid * 1e4
    return c, un, pen


model = {"_meta": {"source": "testnet_s224_s225.jsonl, rounds s224r3 (from 218 s), s225 r1-r3; 0.25-s grid, time-weighted",
                   "cost_definition": "bps of touch mid, per share, average of market buy and market sell walking the visible book",
                   "x": "clip size / position limit L", "phases": {n: [a, b] for n, a, b in bl.PHASES},
                   "recommended_form": "piecewise-linear interpolation of 'table' (mean cost at x = k/40); "
                                       "quad05 for x <= 0.5; never walk into the 3L wall (x > table 'wall_x')"}}
detail = {}
COST_PHASES = [("open", 0, 3), ("pre", 3, 60), ("early", 60, 120), ("storm", 120, 540), ("after", 540, 780),
               ("rep_prelim", 568, 600), ("rep_final", 738, 760), ("h1_entry", 50, 58), ("h1_exit", 72, 80), ("s3_start", 80, 120)]
model["_meta"]["phases"] = {n: [a, b] for n, a, b in COST_PHASES}
model["_meta"]["note"] = "pre = 3-60 s: the first ~2.5 s after each open show only the far 3L level (see b02_phases.out)"
print("cost bps of mid per share (mean of buy & sell; time-weighted), and % of q unfilled within visible levels")
for sym in bl.SYMBOLS:
    L = bl.LIMITS[sym]
    qs = {"10sh": 10, "L/8": L / 8, "L/4": L / 4, "L/2": L / 2, "L": L}
    model[sym] = {"L": L}
    detail[sym] = {}
    print(f"\n{sym} (L={L}; clips 10/{L/8:g}/{L/4:g}/{L/2:g}/{L})")
    for name, a, b in COST_PHASES:
        samples = []
        for d, v in rounds.items():
            fr = [f for f in D["books"][sym] if f.date == d]
            for t, f, age in bl.sample(fr, D["segs"], v["open"], a, b, DT):
                if f.bids and f.asks:
                    samples.append((d, t, f))
        if not samples:
            continue
        # requested clips
        rowq = {}
        for lab, qq in qs.items():
            qq = max(1, int(round(qq)))
            cb, cs, ub, us, pb, ps = [], [], [], [], [], []
            for d, t, f in samples:
                m = f.mid
                c, u, p = walk_cost(f.asks, qq, m, 1)
                if c is not None: cb.append(c); pb.append(p)
                ub.append(u)
                c, u, p = walk_cost(f.bids, qq, m, -1)
                if c is not None: cs.append(c); ps.append(p)
                us.append(u)
            allc = cb + cs
            rowq[lab] = {"q": qq, "buy_mean": statistics.fmean(cb), "sell_mean": statistics.fmean(cs),
                         "mean": statistics.fmean(allc), "median": statistics.median(allc),
                         "p90": sorted(allc)[int(0.9 * (len(allc) - 1))],
                         "pen_mean": statistics.fmean(pb + ps),
                         "unfilled_share_mean": statistics.fmean(ub + us),
                         "pct_samples_unfilled": 100 * sum(1 for u in ub + us if u > 0) / len(ub + us)}
        # fine grid table
        xs, ys, us_ = [], [], []
        x10 = 10 / L
        for x in [x10] + GRID:
            qq = max(1, int(round(x * L)))
            cc, uu = [], []
            for d, t, f in samples:
                m = f.mid
                for lv, sg in ((f.asks, 1), (f.bids, -1)):
                    c, u, p = walk_cost(lv, qq, m, sg)
                    cc.append(p if p is not None else float("nan")); uu.append(u)
            cc = [c for c in cc if not math.isnan(c)]
            xs.append(x); ys.append(statistics.fmean(cc)); us_.append(statistics.fmean(uu))
        quad, rq = lsq(xs, ys, [lambda x: 1, lambda x: x, lambda x: x * x])
        sel = [(x, y) for x, y in zip(xs, ys) if x <= 0.5 + 1e-9]
        quad05, rq05 = lsq([x for x, _ in sel], [y for _, y in sel], [lambda x: 1, lambda x: x, lambda x: x * x])
        h0 = ys[0]
        gam, rl = lsq([x for x, _ in sel], [y - h0 for _, y in sel], [lambda x: x])
        # wall: first x where mean cost jumps above 300 bp
        wall = next((x for x, y in zip(xs, ys) if y > 300), None)
        hs = [(f.asks[0][0] - f.bids[0][0]) / 2 / f.mid * 1e4 for d, t, f in samples]
        inner = sorted(0.5 * (sum(n for p, n in f.bids if p >= f.mid * 0.97) + sum(n for p, n in f.asks if p <= f.mid * 1.03)) / L
                       for d, t, f in samples)
        inner_med = inner[len(inner) // 2]
        model[sym][name] = {"n_samples": len(samples), "half_spread_bps_mean": statistics.fmean(hs),
                            "half_spread_bps_median": statistics.median(hs),
                            "quad": {"h": quad[0], "g1": quad[1], "g2": quad[2], "rmse": rq},
                            "quad05": {"h": quad05[0], "g1": quad05[1], "g2": quad05[2], "rmse": rq05},
                            "lin05": {"h": h0, "gamma_bps_per_x": gam[0], "rmse": rl},
                            "wall_x": wall, "inner_depth_within_3pct_over_L_median": inner_med,
                            "table": {f"{x:.4f}": round(y, 2) for x, y in sorted(zip(xs, ys))},
                            "unfilled_share": {f"{x:.4f}": round(u, 4) for x, u in sorted(zip(xs, us_))},
                            "clips": {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in rowq.items()}}
        detail[sym][name] = rowq
        cl = " ".join(f"{k}:{v['mean']:6.1f}({v['unfilled_share_mean']*100:.1f}%)" for k, v in rowq.items())
        print(f"  {name:10s} {cl}  | quad05 h={quad05[0]:.1f} g1={quad05[1]:.0f} g2={quad05[2]:.0f} (rmse {rq05:.1f}); lin05 gamma={gam[0]:.0f} bp/x; inner depth(3%) {inner_med:.2f} L")
        if name in ("h1_entry", "h1_exit", "early"):
            print("             buy/sell: " + " ".join(f"{k}:{v['buy_mean']:.0f}/{v['sell_mean']:.0f}" for k, v in rowq.items()))
if skew_path:
    sk = json.load(open(skew_path))
    for sym in bl.SYMBOLS:
        r = sk.get(sym, {}).get("storm 120-540")
        a = sk.get(sym, {}).get("after 540-780")
        model[sym]["permanent_impact_bps_per_L"] = {
            "storm_10s": r["B"]["10"] if r else None, "storm_10s_se": r["B_se"]["10"] if r else None,
            "storm_30s": r["B"]["30"] if r else None, "after_10s": a["B"]["10"] if a else None,
            "after_30s": a["B"]["30"] if a else None,
            "source": "b07_skew.py distributed-lag regression of 2-s touch-mid changes on signed taker flow/L, controlling for consensus changes"}
    model["_pooled_permanent_impact_bps_per_L"] = sk.get("_pooled")
json.dump(model, open(out_model, "w"), indent=1)
json.dump(detail, open(out_detail, "w"), indent=1)
