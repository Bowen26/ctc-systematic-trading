"""Clock profile of signed taker flow (units of L), summed over the analysed rounds, by symbol group and time bin.
usage: python3 -I b05b_flow_profile.py trades.json? (no) -> python3 -I b05b_flow_profile.py recording.jsonl history_dir"""
import sys, os, bisect, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl
D = bl.load(sys.argv[1], sys.argv[2])
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
G = {"islands": ["PORT", "ISLE", "AQUA", "GRID", "FERRY"], "HAVEN": ["HAVEN"], "ARCH": ["ARCH"],
     "rebuild": ["RECON", "MAT", "GEN"], "REBLD": ["REBLD"], "REIN": ["REIN"]}
BINS = [(0, 40), (40, 50), (50, 58), (58, 64), (64, 72), (72, 80), (80, 100), (100, 200), (200, 300), (300, 400), (400, 540),
        (540, 560), (560, 570), (570, 580), (580, 600), (600, 740), (740, 750), (750, 780)]
flow = collections.defaultdict(float); vol = collections.defaultdict(float)
per_round = collections.defaultdict(float)
for sym in bl.SYMBOLS:
    fr = D["books"][sym]; ras = [f.ra for f in fr]; L = bl.LIMITS[sym]
    g = [k for k, v in G.items() if sym in v][0]
    for tr in D["trades"][sym]:
        if tr.date not in rounds or not (0 <= tr.t < 780) or tr.ra - bl.iso_to_unix_local(tr.server_ts) > 1:
            continue
        j = bisect.bisect_left(ras, tr.ra) - 1
        if j < 0 or fr[j].seg != tr.seg:
            continue
        b = fr[j]
        s = 1 if (b.asks and tr.price >= b.asks[0][0] - 1e-9) else -1 if (b.bids and tr.price <= b.bids[0][0] + 1e-9) else 0
        for a, c in BINS:
            if a <= tr.t < c:
                flow[(g, a)] += s * tr.qty / L / len(G[g]); vol[(g, a)] += tr.qty / L / len(G[g])
                per_round[(g, a, tr.date)] += s * tr.qty / L / len(G[g])
print("net signed taker flow per symbol (buy-sell, units of L, averaged over the group's symbols, summed over 4 rounds) | buy share of volume")
print(f"{'bin (real s)':14s}" + "".join(f"{g:>18s}" for g in G))
for a, c in BINS:
    print(f"{a:4d}-{c:<4d}       " + "".join(f"{flow[(g,a)]:+9.2f} ({100*(0.5+0.5*flow[(g,a)]/vol[(g,a)]) if vol[(g,a)] else float('nan'):3.0f}%)" for g in G))
print("\nper round, islands group:")
for d in sorted(rounds, key=lambda d: rounds[d]["open"]):
    print(f"  {d}: " + " ".join(f"{a}-{c}:{per_round[('islands', a, d)]:+.2f}" for a, c in BINS))
print("per round, rebuild group:")
for d in sorted(rounds, key=lambda d: rounds[d]["open"]):
    print(f"  {d}: " + " ".join(f"{a}-{c}:{per_round[('rebuild', a, d)]:+.2f}" for a, c in BINS))
