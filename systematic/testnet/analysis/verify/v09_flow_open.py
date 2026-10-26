"""Others' signed taker flow by window (aggressor = trade price vs last book mid before it), the link between
cumulative net flow and the book-minus-consensus gap, and the season-225 open vs the packet starting prices.
usage: python3 -I v09_flow_open.py <recording> <history_dir>
"""
import sys, os, bisect, math, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from vlib import *

rec, hist = sys.argv[1], sys.argv[2]
F = load_rec(rec)
op = round_opens(F)
B = books_by_round(F, op)
T = trades_by_round(F, op)
PACKET = dict(PORT=72.48, ISLE=50.26, AQUA=27.33, GRID=36.41, FERRY=17.91, RECON=52.38, MAT=35.81, GEN=40.30,
              HAVEN=39.55, REIN=89.36, ARCH=289.20, REBLD=216.68)


def signed(key, s):
    ser = B[key][s]
    ts = [x[0] for x in ser]
    out = []
    for t, p, q in T[key][s]:
        j = bisect.bisect_left(ts, t) - 1  # last book strictly before the trade
        if j < 0:
            continue
        m = mid(ser[j])
        if m is None or p == m:
            continue
        out.append((t, q if p > m else -q))
    return out


WIN = [(0, 50), (50, 60), (60, 80), (80, 540), (540, 780)]
print("net signed taker flow of other participants, in units of one team's limit L (+ = net buying)")
print("%-8s %-6s " % ("round", "sym") + " ".join("%9s" % ("%d-%d" % w) for w in WIN))
for key in [(225, 1), (225, 2), (225, 3)]:
    for s in SYMS:
        sg = signed(key, s)
        cells = [sum(q for t, q in sg if a <= t < b) / LIM[s] for a, b in WIN]
        print("%-8s %-6s " % ("%dr%d" % key, s) + " ".join("%+9.2f" % c for c in cells))
print()

# cumulative flow vs gap (book - consensus) at 10-s samples 80-530 s, per round pooled over symbols
print("corr over 10-s samples (80-530 s) of [cumulative net flow since 0 s, /L] with [log(book/consensus)], per round:")
for key in [(225, 1), (225, 2), (225, 3)]:
    mk = load_market(hist, 225)[key[1]]
    xs, ys = [], []
    for s in SYMS:
        if s == "REIN":
            continue
        sg = signed(key, s)
        for t in range(80, 531, 10):
            b = mid(book_at(B[key][s], float(t)))
            if b is None:
                continue
            cf = sum(q for tt, q in sg if tt <= t) / LIM[s]
            xs.append(cf); ys.append(math.log(b / mk[t // 2][s]))
    mx, my = st.mean(xs), st.mean(ys)
    c = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    sl = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sum((a - mx) ** 2 for a in xs)
    print("  %s n=%d corr %.2f slope %.0f bp per L" % (key, len(xs), c, sl * 1e4))

print("\nseason 225 open: median book mid over 3-10 s vs packet and vs market.csv rows 2-5 (bp)")
mk = load_market(hist, 225)[1]
w25p = w25c = 0
for s in SYMS:
    ms = [mid(book_at(B[(225, 1)][s], 3 + 0.25 * i)) for i in range(29)]
    ms = [m for m in ms if m]
    bm = st.median(ms)
    cm = st.median([mk[i][s] for i in range(2, 6)])
    dp, dc = (bm / PACKET[s] - 1) * 1e4, (bm / cm - 1) * 1e4
    w25p += abs(dp) <= 25; w25c += abs(dc) <= 25
    print("  %-6s book %.2f  packet %.2f (%+5.0f)  consensus %.2f (%+5.0f)" % (s, bm, PACKET[s], dp, cm, dc))
print("  within 25 bp: packet %d/12, consensus %d/12" % (w25p, w25c))
