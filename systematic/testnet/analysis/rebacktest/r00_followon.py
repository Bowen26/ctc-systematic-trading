"""Follow-on penalty as a function of the PRIOR same-side order size and of OUR clip size (calibrates the
clip-size choice in the re-backtest).  Extends books/b06b_reclip.py: same event definition (another taker's
one-side bundle in 120-770 s, side from the last book before the trade), but every bundle >= 0.05 L is kept and
binned by size x1 = q/L, and the follow-on clip is walked at size L/8 and L/4.

penalty(x1, x2, dt) = mean cost of an x2*L clip walked at trade+dt, vs the PRE-trade mid
                      minus the mean cost of the same clip walked just before the trade (-0.25 s);
reported raw and net of the consensus move over dt (the house's own information), in bp.

usage: python3 -I r00_followon.py <books_dir> <recording.jsonl> <history_dir> <out.json>
"""
import sys, os, json, bisect, statistics

books_dir, rec, hist, out = sys.argv[1:5]
sys.path.insert(0, books_dir)
import books_lib as bl  # noqa: E402

D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
BINS = [(0.05, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, 0.40), (0.40, 9.0)]
CLIPS = {"L/8": 0.125, "L/4": 0.25}
DTS = [2.0, 4.0, 8.0]
ETF = ("ARCH", "REBLD")
acc = {}  # (group, bin, clip, dt) -> lists


def add(key, raw, net):
    a = acc.setdefault(key, {"raw": [], "net": [], "x1": []})
    a["raw"].append(raw); a["net"].append(net)


for sym in bl.SYMBOLS:
    grp = "etf" if sym in ETF else "stock"
    L = bl.LIMITS[sym]
    fr = D["books"][sym]; ras = [f.ra for f in fr]
    orders = []
    for tr in D["trades"][sym]:
        if tr.date not in rounds or not (120 <= tr.t < 770) or tr.ra - bl.iso_to_unix_local(tr.server_ts) > 1:
            continue
        j = bisect.bisect_left(ras, tr.ra) - 1
        if j < 0 or fr[j].seg != tr.seg or not fr[j].mid:
            continue
        pre = fr[j]
        s = 1 if (pre.asks and tr.price >= pre.asks[0][0] - 1e-9) else -1 if (pre.bids and tr.price <= pre.bids[0][0] + 1e-9) else 0
        if not s:
            continue
        if orders and abs(orders[-1]["ra"] - tr.ra) < 1e-3 and orders[-1]["s"] == s:
            orders[-1]["q"] += tr.qty
        else:
            orders.append({"ra": tr.ra, "t": tr.t, "d": tr.date, "s": s, "q": tr.qty, "m0": pre.mid, "seg": tr.seg, "jpre": j})
    for o in orders:
        x1 = o["q"] / L
        b = next((k for k, (lo, hi) in enumerate(BINS) if lo <= x1 < hi), None)
        if b is None:
            continue
        cons = D["consensus"][o["d"]][sym]
        c0 = bl.consensus_at(cons, o["t"])
        for cname, x2 in CLIPS.items():
            q2 = max(1, round(x2 * L))
            f0 = fr[o["jpre"]]
            lv0 = f0.asks if o["s"] > 0 else f0.bids
            n0, v0, _ = bl.walk(lv0, q2)
            if n0 < q2:
                continue
            before = o["s"] * (v0 - o["m0"]) / o["m0"] * 1e4
            for dt in DTS:
                j = bisect.bisect_right(ras, o["ra"] + dt) - 1
                if j < 0 or fr[j].seg != o["seg"] or fr[j].date != o["d"]:
                    continue
                f = fr[j]
                lv = f.asks if o["s"] > 0 else f.bids
                n1, v1, _ = bl.walk(lv, q2)
                if n1 < q2:
                    continue
                c = o["s"] * (v1 - o["m0"]) / o["m0"] * 1e4
                c1 = bl.consensus_at(cons, o["t"] + dt)
                net = c - (o["s"] * (c1 - c0) / c0 * 1e4 if (c0 and c1) else 0.0)
                add((grp, b, cname, dt), c - before, net - before)
                acc[(grp, b, cname, dt)]["x1"].append(x1)

res = {}
print("follow-on penalty (bp) = cost of our clip at trade+dt minus the same clip just before the trade; raw / net of consensus")
for grp in ("stock", "etf"):
    for cname in CLIPS:
        for dt in DTS:
            line = []
            for b, (lo, hi) in enumerate(BINS):
                a = acc.get((grp, b, cname, dt))
                if not a or len(a["raw"]) < 3:
                    line.append(f"[{lo:.2f},{hi if hi < 9 else 'inf'}) n<3")
                    continue
                n = len(a["raw"]); mr = statistics.fmean(a["raw"]); mn = statistics.fmean(a["net"])
                se = statistics.pstdev(a["net"]) / max(1, n - 1) ** 0.5
                xm = statistics.fmean(a["x1"])
                res[f"{grp}|{cname}|{dt}|{lo}-{hi}"] = {"n": n, "x1_mean": xm, "raw": mr, "net": mn, "net_se": se}
                line.append(f"[{lo:.2f},{hi if hi < 9 else 'inf'}) x1={xm:.2f} n={n:4d} {mr:6.1f}/{mn:6.1f}+-{se:4.1f}")
            print(f"{grp:5s} clip {cname} +{dt:.0f}s: " + " | ".join(line))
# simple slope fit of net penalty on x1 (through the origin), stocks, per clip, dt=2
for grp in ("stock", "etf"):
    for cname in CLIPS:
        xs, ys = [], []
        for b in range(len(BINS)):
            a = acc.get((grp, b, cname, 2.0))
            if a:
                xs += a["x1"]; ys += a["net"]
        if xs:
            sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            sl = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
            print(f"{grp:5s} clip {cname} +2s: n={len(xs)} net penalty through origin = {sxy / sxx:.1f} bp per unit x1; "
                  f"OLS intercept {my - sl * mx:.1f} + slope {sl:.1f}*x1")
            res[f"{grp}|{cname}|fit2s"] = {"n": len(xs), "slope0": sxy / sxx, "ols_a": my - sl * mx, "ols_b": sl}
json.dump(res, open(out, "w"), indent=1)
