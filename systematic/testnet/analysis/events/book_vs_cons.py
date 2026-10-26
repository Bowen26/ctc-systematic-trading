"""How does the recorded book relate to the research consensus?  For each round and symbol:
RMS (bp) of log(book mid at real s) - log(consensus at real s - L) for lags L, on a 1-s grid, plus spread stats.
usage: python3 -I book_vs_cons.py recording.jsonl season224_dir season225_dir"""
import sys, os, math, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec
R = rec.Recording(sys.argv[1], sys.argv[2:])
LAGS = list(range(-4, 15))
def q(xs, p): xs = sorted(xs); return xs[min(len(xs) - 1, int(p * len(xs)))]
summary = {}
for key in [(224, 3), (225, 1), (225, 2), (225, 3)]:
    ri = R.rounds[key]
    s_from = 0 if key != (224, 3) else 220
    print(f"== season {key[0]} round {key[1]}: real s {s_from}-780")
    print("sym    best_lag  rms@best  rms@0  rms@2  rms@4  rms@6  rms@8 | mean(book-cons@best) bp | half-spread bp p50 (p10-p90) | touch size bid/ask p50")
    for sym in rec.SYMS:
        res = {L: [] for L in LAGS}
        hs, bsz, asz = [], [], []
        for s in range(s_from, 781):
            b = R.book_at(sym, R.wall(key, s))
            m = rec.mid(b)
            if m is None: continue
            if R.real(key, b[0]) < s_from - 1: continue
            hs.append(1e4 * (b[3][0][0] - b[2][0][0]) / 2 / m); bsz.append(b[2][0][1]); asz.append(b[3][0][1])
            for L in LAGS:
                c = ri.cons(sym, s - L)
                if c: res[L].append(1e4 * math.log(m / c))
        rms = {L: math.sqrt(st.mean(x * x for x in v)) for L, v in res.items() if v}
        best = min(rms, key=rms.get)
        summary[(key, sym)] = best
        print(f"{sym:6s} {best:5d}   {rms[best]:7.1f}  " + "  ".join(f"{rms[L]:5.1f}" for L in (0, 2, 4, 6, 8)) +
              f" | {st.mean(res[best]):+6.1f} | {q(hs,.5):5.1f} ({q(hs,.1):.1f}-{q(hs,.9):.1f}) | {q(bsz,.5)}/{q(asz,.5)}")
