"""Maker structure and stale quotes around formation (60 s) and the reports (570 s, 740 s).
usage: python3 -I b08_stale.py recording.jsonl history_dir out.json

1) Ladder signature: modal quantity at each visible level as a fraction of L (fresh, unfilled quotes).
2) Two maker layers: 'fast' = levels with qty <= 0.1*L_eff, 'slow' = 0.2*L_eff <= qty < 1*L_eff (L_eff = L for stocks,
   4L for ETFs, as the ladder signature shows); centre = mean of best such bid and ask. Lag of the slow centre behind
   the fast centre at formation: first time each centre covers 50% of its te-4 -> te+24 move.
3) Stale quotes: V = book level (median touch mid) at te+24 s. For an up-move, stale = ask quantity priced <= V*(1-50bp)
   (for a down-move, bid quantity >= V*(1+50bp)). On a 0.25-s grid from te to te+20 s: when it was available,
   the largest stale quantity (in L), size-weighted edge vs V (bps), and the edge in $ at the best moment
   (upper bound: assumes we take all of it at that instant, before anyone else).
"""
import sys, os, json, bisect, statistics, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
order = sorted(rounds, key=lambda d: rounds[d]["open"])
FR = {(d, s): [f for f in D["books"][s] if f.date == d] for d in order for s in bl.SYMBOLS}
RA = {k: [f.ra for f in v] for k, v in FR.items()}
LEFF = {s: (4 * bl.LIMITS[s] if s in ("ARCH", "REBLD") else bl.LIMITS[s]) for s in bl.SYMBOLS}
res = {"ladder": {}, "layers": [], "stale": []}


def frame_at(d, s, t):
    fr, ras = FR[(d, s)], RA[(d, s)]
    x = rounds[d]["open"] + t
    j = bisect.bisect_right(ras, x) - 1
    if j < 0 or bl.seg_at(D["segs"], x) != fr[j].seg:
        return None
    return fr[j]


def level(d, s, t):
    v = [frame_at(d, s, t + k * 0.25) for k in range(-4, 5)]
    v = [f.mid for f in v if f is not None and f.mid]
    return statistics.median(v) if v else None


print("== 1) ladder signature: modal level quantities (all frames, both sides), as multiples of L ==")
for s in bl.SYMBOLS:
    L = bl.LIMITS[s]
    c = collections.Counter()
    for f in D["books"][s]:
        for p, n in f.bids + f.asks:
            c[n] += 1
    top = [n for n, _ in c.most_common(6)]
    res["ladder"][s] = {"modal_qty": top, "as_L": [round(n / L, 3) for n in top]}
    print(f"  {s:6s} L={L:4d}: modal qtys {top} = {[round(n / L, 3) for n in top]} x L")


def centre(f, s, kind):
    Le = LEFF[s]
    if kind == "fast":
        ok = lambda n: n <= 0.1 * Le + 1e-9
    else:
        ok = lambda n: 0.2 * Le <= n < 1.0 * Le
    b = [p for p, n in f.bids if ok(n)]
    a = [p for p, n in f.asks if ok(n)]
    if not b or not a:
        return None
    return 0.5 * (b[0] + a[0])


print("\n== 2) fast vs slow maker layer at formation: t50 (s after 60 s) of each layer's centre; move in bps ==")
lags = []
for d in order:
    for s in bl.SYMBOLS:
        te = 60.0
        vals = {}
        for kind in ("fast", "slow"):
            def cen(t, kind=kind):
                xs = [centre(f, s, kind) for f in (frame_at(d, s, t + k * 0.25) for k in range(-2, 3)) if f]
                xs = [x for x in xs if x]
                return statistics.median(xs) if xs else None
            a, b = cen(te - 4), cen(te + 24)
            if a is None or b is None:
                continue
            mv = (b - a) / a * 1e4
            t50 = None
            k = 0
            while te - 4 + 0.5 * k <= te + 24:
                t = te - 4 + 0.5 * k
                v = cen(t)
                if v is not None and b != a and (v - a) / (b - a) >= 0.5:
                    t50 = t - te
                    break
                k += 1
            vals[kind] = (mv, t50)
        if "fast" in vals and "slow" in vals and abs(vals["fast"][0]) >= 50 and vals["fast"][1] is not None and vals["slow"][1] is not None:
            lags.append(vals["slow"][1] - vals["fast"][1])
            res["layers"].append({"date": d, "sym": s, "fast": vals["fast"], "slow": vals["slow"]})
            print(f"  {d} {s:6s} fast {vals['fast'][0]:+6.0f} bp t50 {vals['fast'][1]:+5.1f}s | slow {vals['slow'][0]:+6.0f} bp t50 {vals['slow'][1]:+5.1f}s")
if lags:
    print(f"  slow-minus-fast t50 lag: median {statistics.median(lags):+.1f} s, p25 {sorted(lags)[len(lags)//4]:+.1f}, p75 {sorted(lags)[3*len(lags)//4]:+.1f} (n={len(lags)})")
    res["layer_lag_median_s"] = statistics.median(lags)

print("\n== 3) stale quotes after events (edge >= 50 bp vs V = book level at te+24 s) ==")
tot = collections.defaultdict(float)
for name, te in (("formation", 60.0), ("prelim", 570.0), ("final", 740.0)):
    for d in order:
        for s in bl.SYMBOLS:
            L = bl.LIMITS[s]
            a, V = level(d, s, te - 4), level(d, s, te + 24)
            if a is None or V is None or abs(V - a) / a < 0.005:
                continue
            up = V > a
            avail = []
            best = (0.0, 0.0, None, 0.0)
            for k in range(0, 81):
                t = te + 0.25 * k
                f = frame_at(d, s, t)
                if f is None:
                    continue
                if up:
                    lv = [(p, n) for p, n in f.asks if p <= V * (1 - 0.005)]
                    edge_d = sum((V - p) * n for p, n in lv)
                else:
                    lv = [(p, n) for p, n in f.bids if p >= V * (1 + 0.005)]
                    edge_d = sum((p - V) * n for p, n in lv)
                qn = sum(n for _, n in lv)
                if qn > 0:
                    avail.append(t - te)
                    if edge_d > best[0]:
                        best = (edge_d, qn / L, t - te, edge_d / (qn * V) * 1e4)
            if avail:
                row = {"event": name, "date": d, "sym": s, "move_bps": (V - a) / a * 1e4, "first_s": min(avail), "last_s": max(avail),
                       "n_grid": len(avail), "best_usd": best[0], "best_qty_over_L": best[1], "best_at_s": best[2],
                       "best_edge_bps": best[3]}
                res["stale"].append(row)
                tot[name] += best[0]
                print(f"  {name:9s} {d} {s:6s} move {row['move_bps']:+6.0f} bp: stale from +{row['first_s']:.2f} to +{row['last_s']:.2f} s "
                      f"({len(avail)*0.25:.1f} s on grid); best at +{best[2]:.2f}s: {best[1]:.2f} L at {best[3]:.0f} bp edge = ${best[0]:.0f}")
for k, v in tot.items():
    print(f"  total best-moment stale edge, {k}: ${v:.0f} over {len(order)} rounds")
res["stale_total_usd"] = dict(tot)
json.dump(res, open(out, "w"), indent=1)

print("\n== 4) stale quantity profile after formation: qty (in L) priced >= 50 bp better than V, and its mean edge (bp) ==")
OFFS = [0.5, 1, 2, 3, 3.5, 4, 5, 6, 8, 10, 12, 15]
prof = {}
for d in order:
    for s in bl.SYMBOLS:
        L = bl.LIMITS[s]
        te = 60.0
        a, V = level(d, s, te - 4), level(d, s, te + 24)
        if a is None or V is None or abs(V - a) / a < 0.01:
            continue
        up = V > a
        cells = []
        for o in OFFS:
            f = frame_at(d, s, te + o)
            if f is None:
                cells.append("   --    "); continue
            lv = [(p, n) for p, n in (f.asks if up else f.bids) if (p <= V * 0.995 if up else p >= V * 1.005)]
            # inner only: exclude the 3L wall
            lv = [(p, n) for p, n in lv if n < 2 * L]
            qn = sum(n for _, n in lv)
            e = (sum(((V - p) if up else (p - V)) * n for p, n in lv) / (qn * V) * 1e4) if qn else 0
            cells.append(f"{qn / L:4.2f}@{e:3.0f}")
            prof.setdefault(f"{d}|{s}", []).append((o, qn / L, e))
        print(f"  {d} {s:6s} move {(V - a) / a * 1e4:+6.0f}: " + " ".join(cells))
print("  offsets (s after 60): " + " ".join(f"{o:>8}" for o in OFFS))
res["formation_profile_excl_wall"] = prof
json.dump(res, open(out, "w"), indent=1)

print("\n== 5) first repricing after formation: first frame after 60 s whose best quote on the move side has moved >= 25% "
      "of the 60->84 s move; and when the last stale inner level (>= 50 bp edge vs V, qty < 2L) disappears ==")
fr_t, last_t = [], []
rows5 = []
for d in order:
    for s in bl.SYMBOLS:
        L = bl.LIMITS[s]
        a, V = level(d, s, 56), level(d, s, 84)
        if a is None or V is None or abs(V - a) / a < 0.01:
            continue
        up = V > a
        first = None; last = None
        for f in FR[(d, s)]:
            if not (60 <= f.t <= 90) or not f.bids or not f.asks:
                continue
            px = f.asks[0][0] if up else f.bids[0][0]
            # for an up-move the ask side matters to a buyer; detect when ANY fast quote has moved: use the bid side too
            moved = max((f.bids[0][0] - a) / (V - a), (f.asks[0][0] - a) / (V - a)) if up else \
                    max((a - f.asks[0][0]) / (a - V), (a - f.bids[0][0]) / (a - V))
            if first is None and moved >= 0.25:
                first = f.t
            lv = [(p, n) for p, n in (f.asks if up else f.bids) if (p <= V * 0.995 if up else p >= V * 1.005) and n < 2 * L]
            if lv:
                last = f.t
        rows5.append({"date": d, "sym": s, "first_reprice_s": first, "last_stale_frame_s": last})
        if first is not None:
            fr_t.append(first)
        if last is not None:
            last_t.append(last)
        print(f"  {d} {s:6s} move {(V-a)/a*1e4:+6.0f} bp: first reprice {first if first is None else round(first,2)} s; last frame with stale inner level {last if last is None else round(last,2)} s")
if fr_t:
    print(f"  first reprice: median {statistics.median(fr_t):.2f} s, min {min(fr_t):.2f}, max {max(fr_t):.2f} (n={len(fr_t)}); "
          f"last stale inner level: median {statistics.median(last_t):.2f} s, min {min(last_t):.2f}, max {max(last_t):.2f} (n={len(last_t)})")
res["formation_reprice"] = rows5
json.dump(res, open(out, "w"), indent=1)
