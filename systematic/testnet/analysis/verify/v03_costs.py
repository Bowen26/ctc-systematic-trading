"""(b) Half-spread, touch depth and book-walk cost curve by phase (time-weighted 0.25-s grid).
Only book frames received on the connection that is up at grid time t are used (stale books across a
disconnect are skipped). Cost = mean of a market buy and sell walking the visible book, bp of touch mid.
usage: python3 -I v03_costs.py <recording> [symbols...]
"""
import sys, os, bisect, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from vlib import *

rec = sys.argv[1]
syms = sys.argv[2:] or SYMS
F = load_rec(rec)
op = round_opens(F)
segs = connections(F)
ack_t = [s for s, _ in segs]
seg_end = [e for _, e in segs]
B = books_by_round(F, op)
PH = [("pre", 3, 60), ("h1_entry", 50, 58), ("early", 60, 120), ("h1_exit", 72, 80), ("s3_start", 80, 120),
      ("storm", 120, 540), ("after", 540, 776)]
XS = [None, 1 / 8, 1 / 4, 1 / 2, 1.0]  # None = 10 shares


def connected_frame(series, o, t):
    """latest frame at real t that came on the connection alive at wall o+t, else None"""
    w = o + t
    k = bisect.bisect_right(ack_t, w) - 1
    if k < 0 or w > seg_end[k] + 0.0:
        return None
    b = book_at(series, t)
    if b is None or b[0] + o < ack_t[k]:
        return None
    return b


res = {}
for key in sorted(op, key=lambda k: op[k]["open"]):
    if key[0] not in (224, 225):
        continue
    o = op[key]["open"]
    for s in syms:
        ser = B[key][s]
        L = LIM[s]
        for name, a, b in PH:
            t = a
            while t < b:
                fr = connected_frame(ser, o, t)
                t += 0.25
                if fr is None or not fr[1] or not fr[2]:
                    continue
                bb, ba = fr[1][0][0], fr[2][0][0]
                m = 0.5 * (bb + ba)
                hs = (ba - bb) / 2 / m * 1e4
                touch = 0.5 * (fr[1][0][1] + fr[2][0][1])
                cs = []
                for x in XS:
                    q = 10 if x is None else max(1, int(round(x * L)))
                    pa = walk(fr[2], q)
                    pb = walk(fr[1], q)
                    if pa is None or pb is None:
                        cs.append(None)
                    else:
                        cs.append(0.5 * ((pa - m) / m + (m - pb) / m) * 1e4)
                d = res.setdefault((s, name), {"hs": [], "touch": [], "c": [[] for _ in XS], "rounds": {}})
                d["hs"].append(hs); d["touch"].append(touch)
                for i, c in enumerate(cs):
                    if c is not None:
                        d["c"][i].append(c)
                d["rounds"].setdefault(key, [[] for _ in XS])
                for i, c in enumerate(cs):
                    if c is not None:
                        d["rounds"][key][i].append(c)

print("cost = mean(buy,sell) walk of visible book, bp of touch mid; median over 0.25-s grid; mean in ()")
print("%-6s %-9s %6s %6s %6s | %12s %12s %12s %12s %12s | L/4 by round (mean)" % (
    "sym", "phase", "n", "hs50", "hs90", "10sh", "L/8", "L/4", "L/2", "L"))
for s in syms:
    for name, a, b in PH:
        d = res.get((s, name))
        if not d:
            continue
        cells = []
        for v in d["c"]:
            cells.append("%5.1f (%5.1f)" % (st.median(v), st.mean(v)) if v else "   -        ")
        byr = " ".join("%s:%.0f" % ("%dr%d" % k, st.mean(v[2])) for k, v in sorted(d["rounds"].items()) if v[2])
        print("%-6s %-9s %6d %6.1f %6.1f | %s | %s  touch50=%g" % (s, name, len(d["hs"]), st.median(d["hs"]),
              pct(d["hs"], .9), " ".join(cells), byr, st.median(d["touch"])))
    print()

# RT-form fit on storm phase: cost(x) = h + g*x on x in {10/L, 1/8, 1/4, 1/2} using medians
print("RT form fit cost(x)=h+g*x on medians of x<=1/2 (storm 120-540 and s3_start 80-120); RT model h=25, g=50, L/4 37.5")
for s in syms:
    for ph in ("storm", "s3_start", "after"):
        d = res.get((s, ph))
        if not d:
            continue
        L = LIM[s]
        xs = [10 / L, 1 / 8, 1 / 4, 1 / 2]
        ys = [st.median(d["c"][i]) for i in range(4)]
        mx, my = st.mean(xs), st.mean(ys)
        g = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        h = my - g * mx
        print("%-6s %-8s h=%5.1f g=%6.1f   (medians: %s)" % (s, ph, h, g, " ".join("%.1f" % y for y in ys)))
