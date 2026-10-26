"""Writer's check of the book right after each round open (standard library only).
usage: python3 -I w03_open.py <recording.jsonl> <verify dir>
For each round open in the recording: seconds until each symbol's touch half-spread first drops below 1%,
and taker trades in the first 3 s (size >= 0.25 L), with their cost against the book mid at 20 s.
"""
import sys, statistics as st
sys.path.insert(0, sys.argv[2])
import vlib as V

frames = V.load_rec(sys.argv[1])
opens = V.round_opens(frames)
books = V.books_by_round(frames, opens)
trades = V.trades_by_round(frames, opens)
for k in sorted(opens, key=lambda x: opens[x]["open"]):
    if k == (224, 3):
        continue  # joined mid-round
    tt = []
    for s in V.SYMS:
        first = None
        for (t, b, a) in books[k][s]:
            if t < 0 or not b or not a:
                continue
            m = 0.5 * (b[0][0] + a[0][0])
            if (a[0][0] - b[0][0]) / 2 / m < 0.01:
                first = t
                break
        tt.append(first)
    ok = [x for x in tt if x is not None]
    big, cost = [], 0.0
    for s in V.SYMS:
        m20 = V.mid(V.book_at(books[k][s], 20))
        for (t, p, q) in trades[k][s]:
            if 0 <= t <= 3 and q >= 0.25 * V.LIM[s] and m20:
                side = 1 if p > m20 else -1
                big.append(f"{s} {q}@{p} ({1e4 * side * (p - m20) / m20:+.0f} bp)")
                cost += side * (p - m20) * q
    print(f"{k}: touch inside 1% after median {st.median(ok):.2f} s (range {min(ok):.2f}-{max(ok):.2f}, n={len(ok)}); "
          f"trades >= 0.25 L in 0-3 s: {len(big)}, cost vs 20-s mid ${cost:,.0f}: " + "; ".join(big))
