"""Decompose the book into the house's five quoting layers by their fixed order size
(A = 10% of limit, B = 7%, C = 30%, D = 24%, E = 300%; ETFs use PORT-like / GRID-like sizes),
and measure each layer's half-width, its mid vs the research consensus, and how often it requotes.
usage: python3 -I layers.py recording.jsonl s224 s225"""
import sys, os, math, statistics as st, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec
R = rec.Recording(sys.argv[1], sys.argv[2:])
SIZES = {}
for s, L in rec.LIMITS.items():
    SIZES[s] = {"A": round(0.10 * L), "B": round(0.07 * L), "C": round(0.30 * L), "D": round(0.24 * L), "E": 3 * L}
SIZES["HAVEN"]["B"] = 18
SIZES["ARCH"] = {"A": 20, "B": 14, "C": 60, "D": 48, "E": 150}
SIZES["REBLD"] = {"A": 40, "B": 28, "C": 120, "D": 96, "E": 300}
def q(xs, p): xs = sorted(xs); return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")
out = collections.defaultdict(lambda: collections.defaultdict(list))
for key in [(224, 3), (225, 1), (225, 2), (225, 3)]:
    ri = R.rounds[key]
    for sym in rec.SYMS:
        sz = SIZES[sym]; inv = {v: k for k, v in sz.items()}
        last = {}
        for b in R.books_between(sym, R.wall(key, 0), R.wall(key, 780)):
            s = R.real(key, b[0])
            if key == (224, 3) and s < 222: continue
            bp = {inv[qn]: p for p, qn in b[2] if qn in inv}
            ap = {inv[qn]: p for p, qn in b[3] if qn in inv}
            c = ri.cons(sym, s)
            for lay in "ABCDE":
                if lay in bp and lay in ap and ap[lay] > bp[lay]:
                    m = 0.5 * (bp[lay] + ap[lay])
                    phase = "pre" if s < 60 else "storm" if s < 540 else "after"
                    out[(sym, lay)]["hw_" + phase].append(1e4 * (ap[lay] - bp[lay]) / 2 / m)
                    out[(sym, lay)]["dev"].append(1e4 * math.log(m / c))
                    if lay in last and abs(last[lay][1] - m) > 1e-9:
                        out[(sym, lay)]["upd"].append(s - last[lay][0])
                    if lay not in last or abs(last[lay][1] - m) > 1e-9:
                        last[lay] = (s, m)
print("layer half-width bp (median pre/storm/after), mid - consensus bp (median, p10, p90), median requote interval s; all four rounds pooled")
for sym in rec.SYMS:
    row = []
    for lay in "ABCDE":
        d = out[(sym, lay)]
        row.append(f"{lay}:{q(d['hw_pre'],.5):4.0f}/{q(d['hw_storm'],.5):4.0f}/{q(d['hw_after'],.5):4.0f} dev {q(d['dev'],.5):+5.0f}({q(d['dev'],.1):+4.0f},{q(d['dev'],.9):+4.0f}) upd {q(d['upd'],.5):4.1f}")
    print(f"{sym:6s} size {SIZES[sym]}")
    for r_ in row: print("      ", r_)
