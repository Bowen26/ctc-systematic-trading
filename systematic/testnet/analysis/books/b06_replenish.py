"""Task 3: touch replenishment after taker orders, and maker requote cadence.
usage: python3 -I b06_replenish.py recording.jsonl history_dir out.json

Taker order = trades of one side in one received_at bundle (b05). For each order that consumed >= 50% of the touch
on its side (a 'depletion'), with pre-trade touch price P0 and size Q0, follow the book until 30 s later
(censored at a disconnect or the round end) and record the first time that
  same_px : quantity at P0 or better (<= P0 for asks, >= P0 for bids) is back to >= 50% / 100% of Q0
  touch   : best-level size on that side is back to >= 50% / 100% of Q0 at any price; plus the price
            of that level vs P0 (bps, positive = away from the taker, i.e. worse for a repeat taker)
  depth50 : depth within 50 bp of the pre-trade mid on that side is back to >= 50% / 100% of its pre-trade value
Also: requote cadence = time between consecutive book frames whose touch or second-level price changed with no
trade in the same bundle (a maker repricing), by symbol and phase.
"""
import sys, os, json, bisect, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
HORIZON = 30.0


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))] if xs else None


def km_quantile(times, censored, p):
    """Kaplan-Meier quantile of time-to-refill with right censoring (times censored at their value)."""
    ev = sorted(zip(times, censored))
    n = len(ev)
    s = 1.0
    at_risk = n
    i = 0
    while i < n:
        t = ev[i][0]
        d = c = 0
        while i < n and ev[i][0] == t:
            if ev[i][1]:
                c += 1
            else:
                d += 1
            i += 1
        if at_risk > 0 and d:
            s *= (1 - d / at_risk)
        at_risk -= d + c
        if 1 - s >= p:
            return t
    return None


res = {}
for sym in bl.SYMBOLS:
    L = bl.LIMITS[sym]
    fr = D["books"][sym]
    ras = [f.ra for f in fr]
    trades = [t for t in D["trades"][sym] if t.date in rounds and 0 <= t.t < 780 and t.ra - bl.iso_to_unix_local(t.server_ts) < 1]
    # group into orders
    orders = []
    for tr in trades:
        j = bisect.bisect_left(ras, tr.ra) - 1
        if j < 0 or fr[j].seg != tr.seg:
            continue
        pre = fr[j]
        side = "B" if (pre.asks and tr.price >= pre.asks[0][0] - 1e-9) else "S" if (pre.bids and tr.price <= pre.bids[0][0] + 1e-9) else None
        if side is None:
            continue
        if orders and abs(orders[-1]["ra"] - tr.ra) < 1e-3 and orders[-1]["side"] == side:
            orders[-1]["qty"] += tr.qty
        else:
            orders.append({"ra": tr.ra, "t": tr.t, "date": tr.date, "seg": tr.seg, "side": side, "qty": tr.qty, "pre": pre, "j": j})
    ev = []
    for o in orders:
        pre = o["pre"]
        lv = pre.asks if o["side"] == "B" else pre.bids
        if not lv or not (pre.bids and pre.asks):
            continue
        P0, Q0 = lv[0]
        if o["qty"] < 0.5 * Q0:
            continue
        m0 = pre.mid
        sgn = 1 if o["side"] == "B" else -1

        def same_px(f):
            levels = f.asks if o["side"] == "B" else f.bids
            return sum(n for p, n in levels if (p <= P0 + 1e-9 if sgn > 0 else p >= P0 - 1e-9))

        def depth50(f):
            levels = f.asks if o["side"] == "B" else f.bids
            return sum(n for p, n in levels if abs(p - m0) / m0 <= 0.005)
        d50_0 = depth50(pre)
        end_open = rounds[o["date"]]["open"] + 780
        seg_end = D["segs"][o["seg"]][1]
        limit = min(o["ra"] + HORIZON, end_open, seg_end)
        out_ = {"t": o["t"], "date": o["date"], "side": o["side"], "qty": o["qty"], "Q0": Q0, "q_over_Q0": o["qty"] / Q0,
                "censor_at": limit - o["ra"]}
        found = {}
        k = o["j"] + 1
        while k < len(fr) and fr[k].ra <= limit:
            f = fr[k]
            if f.seg != o["seg"]:
                break
            if f.ra <= o["ra"] + 1e-9 and k == o["j"] + 1 and f.ra < o["ra"]:
                k += 1; continue
            dt = f.ra - o["ra"]
            sp = same_px(f)
            levels = f.asks if o["side"] == "B" else f.bids
            ts = levels[0][1] if levels else 0
            d5 = depth50(f)
            for name, val, ref in (("same_px", sp, Q0), ("touch", ts, Q0), ("depth50", d5, d50_0)):
                for frac in (0.5, 1.0):
                    key = f"{name}_{int(frac*100)}"
                    if key not in found and ref > 0 and val >= frac * ref and dt > 0:
                        found[key] = dt
                        if name == "touch" and frac == 1.0 and levels:
                            out_["touch_refill_px_bps"] = sgn * (levels[0][0] - P0) / P0 * 1e4
            k += 1
        out_.update(found)
        ev.append(out_)
    r = {}
    print(f"\n{sym} (L={L}): depletion events (order >= 50% of touch): {len(ev)}")
    for ph, a, b in [("pre", 0, 60), ("early", 60, 120), ("storm", 120, 540), ("after", 540, 780), ("all", 0, 780)]:
        e = [x for x in ev if a <= x["t"] < b]
        if not e:
            continue
        row = {"n": len(e), "full_wipe_share": sum(1 for x in e if x["q_over_Q0"] >= 1) / len(e)}
        parts = []
        for key in ("same_px_50", "same_px_100", "touch_50", "touch_100", "depth50_50", "depth50_100"):
            times = [x.get(key, x["censor_at"]) for x in e]
            cens = [key not in x for x in e]
            med = km_quantile(times, cens, 0.5); p90 = km_quantile(times, cens, 0.9)
            w1 = sum(1 for x in e if x.get(key, 1e9) <= 1.0) / len(e)
            w2 = sum(1 for x in e if x.get(key, 1e9) <= 2.0) / len(e)
            w5 = sum(1 for x in e if x.get(key, 1e9) <= 5.0) / len(e)
            row[key] = {"km_median_s": med, "km_p90_s": p90, "within_1s": w1, "within_2s": w2, "within_5s": w5,
                        "never_in_30s": sum(cens) / len(e)}
            parts.append(f"{key}: med {med if med is None else round(med,2)}s p90 {p90 if p90 is None else round(p90,1)}s <=2s {100*w2:.0f}%")
        px = [x["touch_refill_px_bps"] for x in e if "touch_refill_px_bps" in x]
        row["touch_refill_px_bps"] = {"median": q(px, .5), "mean": statistics.fmean(px) if px else None, "p10": q(px, .1), "p90": q(px, .9)}
        r[ph] = row
        print(f"  {ph:6s} n={len(e):4d} wipe {100*row['full_wipe_share']:.0f}% | " + " | ".join(parts) +
              f" | touch refill px vs P0: med {q(px,.5) if px else float('nan'):+.1f} bp mean {statistics.fmean(px) if px else float('nan'):+.1f}")
    # requote cadence: frames where L1 or L2 price changed and no trade in the same bundle
    tr_ras = set(round(t.ra, 3) for t in trades)
    rq = {}
    prev = None
    for f in fr:
        if f.date not in rounds or not (0 <= f.t < 780):
            prev = f; continue
        if prev is not None and prev.seg == f.seg and prev.date == f.date:
            ch = (f.bids[:2] and prev.bids[:2] and [p for p, _ in f.bids[:2]] != [p for p, _ in prev.bids[:2]]) or \
                 (f.asks[:2] and prev.asks[:2] and [p for p, _ in f.asks[:2]] != [p for p, _ in prev.asks[:2]])
            if ch and round(f.ra, 3) not in tr_ras:
                ph = [n for n, a, b in bl.PHASES[:4] if a <= f.t < b][0]
                rq.setdefault((f.date, ph), []).append(f.ra)
        prev = f
    cad = {}
    for ph in bl.MAIN_PHASES:
        gaps = []
        for (d, p), xs in rq.items():
            if p == ph:
                gaps += [y - x for x, y in zip(xs, xs[1:]) if y - x < 60]
        if gaps:
            cad[ph] = {"n": len(gaps), "p25": q(gaps, .25), "p50": q(gaps, .5), "p75": q(gaps, .75), "p90": q(gaps, .9)}
    r["requote_gap_s"] = cad
    print("  maker requote gaps (s, no-trade price changes at L1/L2): " + "; ".join(
        f"{ph} p25/50/75/90 {v['p25']:.2f}/{v['p50']:.2f}/{v['p75']:.2f}/{v['p90']:.2f} (n={v['n']})" for ph, v in cad.items()))
    res[sym] = r
json.dump(res, open(out, "w"), indent=1)
