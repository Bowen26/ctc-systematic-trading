"""Survey trades: size distribution, aggressor side (vs prevailing book), per-round signed flow.
usage: python3 -I trade_survey.py recording.jsonl s224 s225"""
import sys, os, collections, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec
R = rec.Recording(sys.argv[1], sys.argv[2:])
sizes = collections.Counter(); side_cnt = collections.Counter()
for sym in rec.SYMS:
    for (rx, ts, p, qn, tid) in R.trades[sym]:
        sizes[qn] += 1
print("trade sizes (top 30):", sizes.most_common(30))
tot = sum(sizes.values()); print("total trades", tot, "total qty", sum(k * v for k, v in sizes.items()))
# trade_id duplicates?
ids = collections.Counter(t[4] for s in rec.SYMS for t in R.trades[s])
print("duplicate trade ids:", sum(1 for v in ids.values() if v > 1))
# aggressor vs book just before the trade's server timestamp
def book_before(sym, t):
    import bisect
    i = bisect.bisect_left(R.book_rx[sym], t) - 1
    return R.books[sym][i] if i >= 0 else None
for key in [(224, 3), (225, 1), (225, 2), (225, 3)]:
    ri = R.rounds[key]
    print(f"== {key}: per symbol: n trades, qty, buy qty, sell qty, inside qty, net (buy-sell) as % of limit; by phase net%L [0-50, 50-60, 60-80, 80-540, 540-570, 570-740, 740-780]")
    for sym in rec.SYMS:
        t1, t2 = R.wall(key, -5), R.wall(key, 785)
        tr = R.trades_between(sym, t1, t2)
        b = s_ = ins = 0; ph = [0] * 7; edges = [0, 50, 60, 80, 540, 570, 740, 790]
        for (rx, ts, p, qn, tid) in tr:
            bk = book_before(sym, rx - 0.0005)
            m = rec.mid(bk)
            if m is None: ins += qn; continue
            sgn = 1 if p > m + 1e-9 else -1 if p < m - 1e-9 else 0
            if sgn > 0: b += qn
            elif sgn < 0: s_ += qn
            else: ins += qn
            rs = R.real(key, rx)
            for k in range(7):
                if edges[k] <= rs < edges[k + 1]: ph[k] += sgn * qn
        L = rec.LIMITS[sym]
        print(f"  {sym:6s} n={len(tr):4d} qty={sum(x[3] for x in tr):6d} buy={b:5d} sell={s_:5d} mid={ins:4d} net={100*(b-s_)/L:+7.1f}%L | " + " ".join(f"{100*x/L:+6.1f}" for x in ph))
