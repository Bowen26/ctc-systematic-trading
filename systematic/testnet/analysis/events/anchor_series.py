"""Per-layer mids (A, C, E) vs consensus and cumulative signed team flow for one symbol/round.
usage: python3 -I anchor_series.py recording.jsonl s224 s225 SEASON ROUND SYM STEP"""
import sys, os, math, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec
R = rec.Recording(sys.argv[1], sys.argv[2:4])
key = (int(sys.argv[4]), int(sys.argv[5])); sym = sys.argv[6]; step = float(sys.argv[7])
ri = R.rounds[key]; L = rec.LIMITS[sym]
SZ = {"A": round(.1 * L), "C": round(.3 * L), "E": 3 * L}
if sym == "ARCH": SZ = {"A": 20, "C": 60, "E": 150}
if sym == "REBLD": SZ = {"A": 40, "C": 120, "E": 300}
inv = {v: k for k, v in SZ.items()}
# signed flow
flow = []
for (rx, ts, p, qn, tid) in R.trades[sym]:
    i = bisect.bisect_left(R.book_rx[sym], rx - 0.0005) - 1
    m = rec.mid(R.books[sym][i]) if i >= 0 else None
    sg = 0 if m is None else (1 if p > m else -1 if p < m else 0)
    flow.append((rx, sg * qn))
frx = [f[0] for f in flow]
lastm = {}
print(" real    cons |  touch mid   devbp |  A mid   C mid    E mid  (E dev bp) | cum flow %L since t=0")
s = 0.0
while s <= 780:
    t = R.wall(key, s)
    i = bisect.bisect_right(R.book_rx[sym], t) - 1
    b = R.books[sym][i] if i >= 0 else None
    # scan back for last full-size layer quotes
    j = i
    found = {}
    while j >= 0 and len(found) < 3 and R.real(key, R.books[sym][j][0]) > s - 60:
        bk = R.books[sym][j]
        bp = {inv[qn]: p for p, qn in bk[2] if qn in inv}; ap = {inv[qn]: p for p, qn in bk[3] if qn in inv}
        for lay in SZ:
            if lay not in found and lay in bp and lay in ap: found[lay] = 0.5 * (bp[lay] + ap[lay])
        j -= 1
    c = ri.cons(sym, s); m = rec.mid(b)
    k = bisect.bisect_right(frx, t)
    k0 = bisect.bisect_right(frx, R.wall(key, 0))
    cf = sum(f[1] for f in flow[k0:k])
    dev = 1e4 * math.log(m / c) if (m and c) else float("nan")
    ed = 1e4 * math.log(found["E"] / c) if ("E" in found and c) else float("nan")
    print(f"{s:5.0f} {c if c else float('nan'):7.2f} | {m if m else float('nan'):8.2f} {dev:+7.0f} | " + " ".join(f"{found.get(l, float('nan')):7.2f}" for l in "ACE") + f" ({ed:+5.0f}) | {100*cf/L:+7.1f}")
    s += step
