"""Task 3 (feasibility of worked clips): after another taker's order of >= 0.2 L, what would the next L/4 clip on the
same side cost, measured against the pre-trade mid, at +0.5/+1/+2/+4/+8 s? Compared with an L/4 clip just before.
usage: python3 -I b06b_reclip.py recording.jsonl history_dir out.json
Cost = side * (vwap(L/4 walking the visible book at t) - mid_pre) / mid_pre * 1e4  (includes any drift of the book).
Also the same quantity net of the consensus move over the interval (removes the house's own information)."""
import sys, os, json, bisect, statistics, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
OFF = [-0.25, 0.5, 1, 2, 4, 8]
res = {}
allrows = {o: [] for o in OFF}
allnet = {o: [] for o in OFF}
for sym in bl.SYMBOLS:
    L = bl.LIMITS[sym]; q = max(1, round(L / 4))
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
            orders.append({"ra": tr.ra, "t": tr.t, "d": tr.date, "s": s, "q": tr.qty, "m0": pre.mid, "seg": tr.seg})
    big = [o for o in orders if o["q"] >= 0.2 * L]
    rows = {o: [] for o in OFF}
    net = {o: [] for o in OFF}
    for o in big:
        for off in OFF:
            x = o["ra"] + off
            j = bisect.bisect_right(ras, x) - 1
            if off < 0:
                j = bisect.bisect_left(ras, o["ra"]) - 1
            if j < 0 or fr[j].seg != o["seg"] or fr[j].date != o["d"]:
                continue
            f = fr[j]
            lv = f.asks if o["s"] > 0 else f.bids
            filled, vwap, worst = bl.walk(lv, q)
            if filled < q:
                continue
            c = o["s"] * (vwap - o["m0"]) / o["m0"] * 1e4
            cons = D["consensus"][o["d"]][sym]
            c0 = bl.consensus_at(cons, o["t"]); c1 = bl.consensus_at(cons, o["t"] + max(off, 0))
            rows[off].append(c)
            allrows[off].append(c)
            if c0 and c1:
                n = c - o["s"] * (c1 - c0) / c0 * 1e4
                net[off].append(n); allnet[off].append(n)
    res[sym] = {str(off): {"n": len(v), "mean": statistics.fmean(v) if v else None, "median": statistics.median(v) if v else None,
                           "net_mean": statistics.fmean(net[off]) if net[off] else None} for off, v in rows.items()}
    print(f"{sym:6s} n={len(big):3d} L/4={q}: " + "  ".join(
        f"{off:+.2f}s {statistics.fmean(v):5.1f}" + (f"/{statistics.fmean(net[off]):5.1f}" if net[off] else "") for off, v in rows.items() if v))
print("pooled (mean bps, raw/net of consensus): " + "  ".join(
    f"{off:+.2f}s {statistics.fmean(v):5.1f}/{statistics.fmean(allnet[off]):5.1f} (n={len(v)})" for off, v in allrows.items() if v))
res["_pooled"] = {str(off): {"n": len(v), "mean": statistics.fmean(v), "net_mean": statistics.fmean(allnet[off])} for off, v in allrows.items() if v}
json.dump(res, open(out, "w"), indent=1)
