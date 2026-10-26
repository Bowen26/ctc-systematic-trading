"""Half-spread and touch-depth time profile around formation (40-160 s), season-225 rounds 1-3 pooled.
usage: python3 -I b02b_formation_spread.py recording.jsonl history_dir
Per 4-s bin: median over (rounds x 0.25-s samples) of half-spread bps; and depth within 50 bp of mid per side / L."""
import sys, os, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl
D = bl.load(sys.argv[1], sys.argv[2])
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
GROUPS = {"islands5": ["PORT", "ISLE", "AQUA", "GRID", "FERRY"], "rebuild3": ["RECON", "MAT", "GEN"], "HAVEN": ["HAVEN"],
          "REIN": ["REIN"], "ARCH": ["ARCH"], "REBLD": ["REBLD"]}
bins = list(range(40, 160, 4))
print("bin(s)  " + "".join(f"{g:>22s}" for g in GROUPS) + "     (median half-spread bp | median depth within 50 bp per side, x L)")
for a in bins:
    cells = []
    for g, syms in GROUPS.items():
        hs, dp = [], []
        for s in syms:
            L = bl.LIMITS[s]
            for d, v in rounds.items():
                fr = [f for f in D["books"][s] if f.date == d]
                for t, f, age in bl.sample(fr, D["segs"], v["open"], a, a + 4, 0.25):
                    if f.bids and f.asks:
                        m = f.mid
                        hs.append((f.asks[0][0] - f.bids[0][0]) / 2 / m * 1e4)
                        dp.append(0.5 * (sum(n for p, n in f.bids if p >= m * 0.995) + sum(n for p, n in f.asks if p <= m * 1.005)) / L)
        cells.append(f"{statistics.median(hs):9.0f} | {statistics.median(dp):5.2f} L" if hs else "  n/a")
    print(f"{a:3d}-{a+4:<3d} " + "".join(f"{c:>22s}" for c in cells))
