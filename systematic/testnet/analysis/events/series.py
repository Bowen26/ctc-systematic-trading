"""Write 2-s book series aligned to the market clock, next to the research consensus, one CSV per round:
series_s<season>r<round>.csv with real_s, row, and per symbol bid, ask, mid (touch, last frame at or before the
grid time; empty inside a disconnect gap) and cons (market.csv row real_s/2).

usage: python3 -I series.py recording.jsonl season224_dir season225_dir out_dir
"""
import sys, os, csv, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec

R = rec.Recording(sys.argv[1], sys.argv[2:4])
out = sys.argv[4]
os.makedirs(out, exist_ok=True)
for key in [(224, 3), (225, 1), (225, 2), (225, 3)]:
    ri = R.rounds[key]
    path = os.path.join(out, f"series_s{key[0]}r{key[1]}.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["real_s", "row"] + [f"{s}_{c}" for s in rec.SYMS for c in ("bid", "ask", "mid", "cons")])
        for row in range(0, 391):
            s = 2 * row
            t = R.wall(key, s)
            vals = []
            for sym in rec.SYMS:
                i = bisect.bisect_right(R.book_rx[sym], t) - 1
                ok = i >= 0 and not R.in_gap(t) and not (key == (224, 3) and s < 222)
                b = R.books[sym][i] if ok else None
                bid = b[2][0][0] if b and b[2] else ""
                ask = b[3][0][0] if b and b[3] else ""
                m = round(0.5 * (bid + ask), 4) if (bid != "" and ask != "") else ""
                c = ri.cons(sym, s)
                vals += [bid, ask, m, c if c is not None else ""]
            w.writerow([s, row] + vals)
    print("wrote", path)
