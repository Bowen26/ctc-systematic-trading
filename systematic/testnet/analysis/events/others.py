"""Things the consensus could not show: other participants' orders and trades, and quote behaviour around events.

A. Foreign orders: between consecutive frames of a symbol, a price level that is new and lies inside the previously
   visible 5 levels (so it is not just a deeper level scrolling into view) with a size that is not one of the five
   house layer sizes, or an existing level whose size grows by a non-layer amount. Counted by phase, with size
   (% of limit) and price position (inside the house spread / at the touch / behind).
B. Trades: per round and phase, number of trades and volume as multiples of one team's limit; aggressor side split;
   the largest single prints (block takes of the backstop layer E at the round open).
C. Touch half-spread and touch size around events (median over frames in each window), stocks vs ETFs.

usage: python3 -I others.py recording.jsonl season224_dir season225_dir
"""
import sys, os, bisect, statistics as st, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec

R = rec.Recording(sys.argv[1], sys.argv[2:4])
KEYS = [(224, 3), (225, 1), (225, 2), (225, 3)]
LAY = {}
for s, L in rec.LIMITS.items():
    LAY[s] = {round(0.10 * L), round(0.07 * L), round(0.30 * L), round(0.24 * L), 3 * L}
LAY["HAVEN"].add(18)
LAY["ARCH"] = {20, 14, 60, 48, 150}
LAY["REBLD"] = {40, 28, 120, 96, 300}


def ph(s):
    if s < 60: return "pre 0-60"
    if s < 540: return "storm 60-540"
    return "after 540-780"


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")


print("A. Foreign (non-house) order arrivals")
cnt = collections.Counter(); sizes = collections.defaultdict(list); pos = collections.Counter(); examples = []
for key in KEYS:
    for sym in rec.SYMS:
        bl = R.books_between(sym, R.wall(key, 0), R.wall(key, 780))
        L = rec.LIMITS[sym]
        for i in range(1, len(bl)):
            a, b = bl[i - 1], bl[i]
            if b[0] - a[0] > 5:
                continue
            s = R.real(key, b[0])
            if key == (224, 3) and s < 222:
                continue
            for side, idx in (("bid", 2), ("ask", 3)):
                prev = dict(a[idx]); cur = dict(b[idx])
                if not a[idx]:
                    continue
                worst = a[idx][-1][0]
                for p, qq in cur.items():
                    inside_vis = (p >= worst) if side == "bid" else (p <= worst)
                    new = None
                    if p not in prev and inside_vis and qq not in LAY[sym]:
                        new = qq
                    elif p in prev and qq > prev[p] and (qq - prev[p]) not in LAY[sym]:
                        new = qq - prev[p]
                    if new is None:
                        continue
                    # position vs the house touch of the previous frame
                    bb = a[2][0][0] if a[2] else None; aa = a[3][0][0] if a[3] else None
                    if bb is None or aa is None:
                        where = "?"
                    elif bb < p < aa:
                        where = "inside spread"
                    elif (side == "bid" and abs(p - bb) < 1e-9) or (side == "ask" and abs(p - aa) < 1e-9):
                        where = "at touch"
                    else:
                        where = "behind touch"
                    cnt[ph(s)] += 1; sizes[ph(s)].append(new / L); pos[(ph(s), where)] += 1
                    if len(examples) < 12 and where == "inside spread":
                        examples.append(f"{key} {sym} {s:.1f}s {side} {p} x{new}")
for k in ("pre 0-60", "storm 60-540", "after 540-780"):
    print(f"   {k:14s}: {cnt[k]:5d} arrivals; size % of limit p50 {100*q(sizes[k],.5):.1f} p90 {100*q(sizes[k],.9):.1f};"
          f" inside spread {pos[(k,'inside spread')]}, at touch {pos[(k,'at touch')]}, behind {pos[(k,'behind touch')]}")
print("   examples inside the spread:", "; ".join(examples[:8]))

print()
print("B. Trades by round and phase: count, volume in team-limits (sum over symbols of qty/limit), buy share")
for key in KEYS:
    row = []
    for phs, (p0, p1) in (("0-60", (0, 60)), ("60-540", (60, 540)), ("540-740", (540, 740)), ("740-780", (740, 781))):
        n = 0; v = 0.0; buys = 0.0
        for sym in rec.SYMS:
            for (rx, ts, p, qn, tid) in R.trades_between(sym, R.wall(key, p0), R.wall(key, p1)):
                j = bisect.bisect_left(R.book_rx[sym], rx - 0.0005) - 1
                m = rec.mid(R.books[sym][j]) if j >= 0 else None
                n += 1; v += qn / rec.LIMITS[sym]
                if m and p > m: buys += qn / rec.LIMITS[sym]
        row.append(f"{phs}: {n:4d} tr, {v:5.1f} L, buy {100*buys/max(v,1e-9):3.0f}%")
    print(f"   {key}: " + " | ".join(row))
print("   largest single prints (qty as % of limit):")
big = []
for sym in rec.SYMS:
    for (rx, ts, p, qn, tid) in R.trades[sym]:
        big.append((qn / rec.LIMITS[sym], sym, rx, p, qn))
for f, sym, rx, p, qn in sorted(big, reverse=True)[:10]:
    key = None
    for k in KEYS:
        if R.rounds[k].t0 and -60 < R.real(k, rx) < 800: key = k
    j = bisect.bisect_left(R.book_rx[sym], rx - 0.0005) - 1
    m = rec.mid(R.books[sym][j]) if j >= 0 else None
    print(f"     {sym:6s} {qn:5d} ({100*f:.0f}% L) at {p} (pre-trade mid {m}) round {key} real {R.real(key, rx) if key else float('nan'):.1f} s")

print()
print("C. Touch half-spread (bp) and touch size (% of limit, min of bid/ask) around events, median over 0.5-s samples")
for name, te in (("formation", 60), ("prelim", 570), ("final", 740)):
    for grp, syms in (("stocks", rec.STOCKS), ("ETFs", ["ARCH", "REBLD"])):
        out = []
        for w0, w1 in ((-10, 0), (0, 3), (3, 6), (6, 10), (10, 20), (20, 40)):
            hs = []; sz = []
            for key in KEYS:
                if name == "formation" and key == (224, 3):
                    continue
                for sym in syms:
                    x = w0
                    while x < w1:
                        t = R.wall(key, te + x)
                        if not R.in_gap(t):
                            i = bisect.bisect_right(R.book_rx[sym], t) - 1
                            b = R.books[sym][i]
                            if b[2] and b[3]:
                                m = rec.mid(b)
                                hs.append(1e4 * (b[3][0][0] - b[2][0][0]) / 2 / m)
                                sz.append(min(b[2][0][1], b[3][0][1]) / rec.LIMITS[sym])
                        x += 0.5
            out.append(f"[{w0:+d},{w1:+d}) {q(hs,.5):5.1f}bp {100*q(sz,.5):4.0f}%")
        print(f"   {name:9s} {grp:6s} " + "  ".join(out))
