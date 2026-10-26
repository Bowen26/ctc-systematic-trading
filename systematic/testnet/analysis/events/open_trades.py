"""Trades and book state in the first 3 s of each round (the house's fast layers are not yet quoting).
usage: python3 -I open_trades.py recording.jsonl s224 s225"""
import sys, os, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec
R = rec.Recording(sys.argv[1], sys.argv[2:4])
for key in [(225, 1), (225, 2), (225, 3)]:
    tot = 0.0
    print(f"-- {key}")
    for sym in rec.SYMS:
        fl = R.books_between(sym, R.wall(key, -5), R.wall(key, 3))
        first = R.books_between(sym, R.wall(key, -5), R.wall(key, 30))
        ab = None
        for b in first:
            if len(b[2]) >= 3 and len(b[3]) >= 3:
                ab = R.real(key, b[0]); break
        trs = R.trades_between(sym, R.wall(key, -5), R.wall(key, 3))
        if not trs: continue
        b30 = R.book_at(sym, R.wall(key, 20)); m20 = rec.mid(b30)
        for (rx, ts, p, qn, tid) in trs:
            cost = qn * abs(p - m20)
            tot += cost
            print(f"   {sym:6s} trade {qn:5d} ({100*qn/rec.LIMITS[sym]:.0f}% L) at {p} real {R.real(key, rx):5.2f} s; book mid at 20 s {m20:.2f} -> {100*(p/m20-1):+.2f}%;"
                  f" cost vs 20-s mid ${cost:.0f}; full ladder first seen at {ab if ab is None else round(ab,2)} s")
    print(f"   total paid vs 20-s mid by these opening prints: ${tot:.0f}")
