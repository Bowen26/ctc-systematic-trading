"""Book-minus-consensus gap (bp, log) at fixed times of each round: does the book's mid-storm departure from the
research consensus close at storm end (540 s) and after the reports?  Also the PnL per share of fading the gap
(sell the book when it is above the consensus, buy when below) at 300 s and closing at 560 s, at the touch.
usage: python3 -I gap_convergence.py recording.jsonl s224 s225"""
import sys, os, math, bisect, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec
R = rec.Recording(sys.argv[1], sys.argv[2:4])
TS = [30, 100, 200, 300, 400, 530, 560, 600, 700, 770]
def bk(sym, key, s):
    i = bisect.bisect_right(R.book_rx[sym], R.wall(key, s)) - 1
    return R.books[sym][i]
for key in [(225, 1), (225, 2), (225, 3), (224, 3)]:
    ri = R.rounds[key]
    print(f"-- {key}: gap bp at " + " ".join(f"{t:>6d}" for t in TS) + " | fade 300->560 s at touch, % of price")
    fades = []
    for sym in rec.SYMS:
        g = []
        for t in TS:
            if key == (224, 3) and t < 222: g.append("     -"); continue
            m = rec.mid(bk(sym, key, t)); c = ri.cons(sym, t)
            g.append(f"{1e4*math.log(m/c):+6.0f}")
        b0 = bk(sym, key, 300); b1 = bk(sym, key, 560)
        m0 = rec.mid(b0); c0 = ri.cons(sym, 300)
        if m0 > c0:   # sell at bid, buy back at ask
            f = (b0[2][0][0] - b1[3][0][0]) / m0
        else:
            f = (b1[2][0][0] - b0[3][0][0]) / m0
        fades.append(f)
        print(f"   {sym:6s} " + " ".join(g) + f" | {100*f:+6.2f}%")
    print(f"   mean fade {100*st.mean(fades):+.2f}%, positive {sum(1 for f in fades if f > 0)}/12")
