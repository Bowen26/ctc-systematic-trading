"""Task 3b: trade-flow characterization. Aggressor side from the last book frame before the trade
(price >= best ask -> buy, <= best bid -> sell, else inside/unknown). Sizes, rates, sides, by symbol and phase;
clustering of trades into one aggressive order (same received_at and side = one taker order walking levels);
regularity (inter-arrival of taker orders) and net signed flow per round.
usage: python3 -I b05_trades.py recording.jsonl history_dir out.json"""
import sys, os, json, bisect, statistics, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
order = sorted(rounds, key=lambda d: rounds[d]["open"])


def classify(sym):
    fr = D["books"][sym]
    ras = [f.ra for f in fr]
    outl = []
    for tr in D["trades"][sym]:
        if tr.date not in rounds or not (0 <= tr.t < 780):
            continue
        j = bisect.bisect_left(ras, tr.ra) - 1   # last book strictly before the trade frame arrived
        # trade frames precede the book frame of the same bundle; same received_at bundle -> use earlier frame
        while j >= 0 and fr[j].ra >= tr.ra:
            j -= 1
        side = "?"
        if j >= 0 and fr[j].seg == tr.seg:
            b = fr[j]
            if b.asks and tr.price >= b.asks[0][0] - 1e-9:
                side = "B"
            elif b.bids and tr.price <= b.bids[0][0] + 1e-9:
                side = "S"
            else:
                side = "I"
        lag = tr.ra - bl.iso_to_unix_local(tr.server_ts)
        outl.append({"t": tr.t, "date": tr.date, "price": tr.price, "qty": tr.qty, "side": side, "ra": tr.ra,
                     "server_lag": lag, "seg": tr.seg})
    return outl


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))] if xs else float("nan")


res = {}
print("trades within [0,780) of the 4 analysed rounds; replayed-on-subscribe trades (server lag > 1 s) dropped")
print("orders = trades grouped by (received_at bundle, side); rates per covered second of book data")
cover = {}
for d in order:
    v = rounds[d]
    for name, a, b in bl.PHASES:
        # covered seconds = 0.25-s grid samples with any live connection
        n = 0
        k = a
        while k < b:
            if bl.seg_at(D["segs"], v["open"] + k) is not None:
                n += 1
            k += 0.25
        cover[(d, name)] = n * 0.25
for sym in bl.SYMBOLS:
    L = bl.LIMITS[sym]
    T = [x for x in classify(sym) if x["server_lag"] < 1.0]
    # group into orders
    orders = []
    for x in T:
        if orders and abs(orders[-1]["ra"] - x["ra"]) < 1e-3 and orders[-1]["side"] == x["side"]:
            orders[-1]["qty"] += x["qty"]; orders[-1]["n"] += 1
        else:
            orders.append({"ra": x["ra"], "t": x["t"], "date": x["date"], "side": x["side"], "qty": x["qty"], "n": 1})
    r = {}
    print(f"\n{sym} (L={L}): {len(T)} trades, {len(orders)} taker orders")
    for name, a, b in bl.PHASES:
        tt = [x for x in T if a <= x["t"] < b]
        oo = [o for o in orders if a <= o["t"] < b]
        cov = sum(cover[(d, name)] for d in order)
        if not tt:
            continue
        sides = collections.Counter(x["side"] for x in tt)
        vol = sum(x["qty"] for x in tt)
        bvol = sum(x["qty"] for x in tt if x["side"] == "B"); svol = sum(x["qty"] for x in tt if x["side"] == "S")
        osz = [o["qty"] for o in oo]
        multi = sum(1 for o in oo if o["n"] > 1)
        ia = []
        for d in order:
            ts = sorted(o["t"] for o in oo if o["date"] == d)
            ia += [y - x for x, y in zip(ts, ts[1:])]
        r[name] = {"trades": len(tt), "orders": len(oo), "orders_per_s": len(oo) / cov if cov else None,
                   "shares_per_s": vol / cov if cov else None, "share_buy_vol": bvol / vol if vol else None,
                   "sides": dict(sides), "order_size": {"p10": q(osz, .1), "p50": q(osz, .5), "p90": q(osz, .9),
                                                        "max": max(osz), "mean": statistics.fmean(osz)},
                   "order_size_over_L_p50": q(osz, .5) / L, "multi_level_orders": multi,
                   "interarrival_s": {"p10": q(ia, .1), "p50": q(ia, .5), "p90": q(ia, .9)} if ia else None}
        print(f"  {name:10s} orders {len(oo):4d} ({r[name]['orders_per_s']:.2f}/s), {vol/cov:5.1f} sh/s, size p10/50/90/max "
              f"{q(osz,.1):.0f}/{q(osz,.5):.0f}/{q(osz,.9):.0f}/{max(osz)} (p50 {q(osz,.5)/L*100:.1f}% L), "
              f"buy share of volume {100*bvol/vol:.0f}%, sides {dict(sides)}, multi-level {multi}, ia p50 {q(ia,.5) if ia else float('nan'):.2f}s")
    # net signed flow per round (units of L), storm and whole round
    nets = {}
    for d in order:
        for name, a, b in (("storm", 60, 540), ("round", 0, 780)):
            nb = sum(x["qty"] for x in T if x["date"] == d and a <= x["t"] < b and x["side"] == "B")
            ns = sum(x["qty"] for x in T if x["date"] == d and a <= x["t"] < b and x["side"] == "S")
            nets[f"{d}|{name}"] = {"buy": nb, "sell": ns, "net_over_L": (nb - ns) / L}
    r["net_flow"] = nets
    print("  net signed taker flow (buy - sell)/L, 60-540 s: " + ", ".join(
        f"{d}: {nets[d + '|storm']['net_over_L']:+.2f}" for d in order))
    # size modes
    cnt = collections.Counter(x["qty"] for x in T)
    r["size_modes"] = cnt.most_common(8)
    print(f"  most common trade sizes: {cnt.most_common(8)}")
    res[sym] = r
json.dump(res, open(out, "w"), indent=1)
