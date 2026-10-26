"""Worked examples for the report: (a) FERRY 300-322 s of s225 r1 (repeated small buys walk the quote);
(b) HAVEN formation 58-82 s of s225 r1 (fast vs slow layer, stale asks).
usage: python3 -I b07b_example.py recording.jsonl history_dir"""
import sys, os, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl
D = bl.load(sys.argv[1], sys.argv[2])
d = "1802-05-10"
for sym, a, b in (("FERRY", 300.0, 322.0),):
    fr = [f for f in D["books"][sym] if f.date == d]; ras = [f.ra for f in fr]
    o = D["rounds"][d]["open"]
    f0 = fr[bisect.bisect_right(ras, o + a) - 1]; f1 = fr[bisect.bisect_right(ras, o + b) - 1]
    buys = sells = nb = ns = 0
    for tr in D["trades"][sym]:
        if tr.date == d and a <= tr.t <= b:
            j = bisect.bisect_left(ras, tr.ra) - 1
            pre = fr[j]
            if tr.price >= pre.asks[0][0] - 1e-9: buys += tr.qty; nb += 1
            elif tr.price <= pre.bids[0][0] + 1e-9: sells += tr.qty; ns += 1
    c0 = bl.consensus_at(D["consensus"][d][sym], a); c1 = bl.consensus_at(D["consensus"][d][sym], b)
    L = bl.LIMITS[sym]
    print(f"{sym} {d} {a:.0f}-{b:.0f} s: {nb} buy trades ({buys} sh), {ns} sell trades ({sells} sh); net {(buys-sells)/L:+.2f} L")
    print(f"  bid {f0.bids[0][0]} -> {f1.bids[0][0]} ({1e4*(f1.bids[0][0]/f0.bids[0][0]-1):+.0f} bp), ask {f0.asks[0][0]} -> {f1.asks[0][0]} "
          f"({1e4*(f1.asks[0][0]/f0.asks[0][0]-1):+.0f} bp); consensus {c0} -> {c1} ({1e4*(c1/c0-1):+.0f} bp); book mid vs consensus at {b:.0f} s {1e4*(f1.mid/c1-1):+.0f} bp")
sym = "HAVEN"
fr = [f for f in D["books"][sym] if f.date == d]
print(f"\n{sym} {d} formation: book frames 62-75 s (bid levels | ask levels) and consensus rows 30-37")
for f in fr:
    if 62 <= f.t <= 75:
        print(f"  {f.t:6.2f}  " + " ".join(f"{p}x{q}" for p, q in f.bids) + " | " + " ".join(f"{p}x{q}" for p, q in f.asks))
print("  consensus rows 30-37 (real 60-74 s):", D["consensus"][d][sym][30:38])
