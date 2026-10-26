"""Print every book frame (all levels) and trade for one symbol in a real-second window of a round.
usage: python3 -I book_frames.py recording.jsonl s224 s225 SEASON ROUND SYM FROM TO"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec
R = rec.Recording(sys.argv[1], sys.argv[2:4])
key = (int(sys.argv[4]), int(sys.argv[5])); sym = sys.argv[6]; a, z = float(sys.argv[7]), float(sys.argv[8])
ri = R.rounds[key]
ev = [(b[0], "B", b) for b in R.books_between(sym, R.wall(key, a), R.wall(key, z))]
ev += [(t[0], "T", t) for t in R.trades_between(sym, R.wall(key, a), R.wall(key, z))]
ev += [(rx, "N", m) for rx, m in R.news if R.wall(key, a) <= rx <= R.wall(key, z) and m.get("type") not in ("storm", "track", "snapshot")]
for rx, k, x in sorted(ev, key=lambda e: e[0]):
    s = R.real(key, rx)
    c = ri.cons(sym, s)
    if k == "B":
        print(f"{s:7.2f} c={c:8.2f} BID " + " ".join(f"{p:.2f}x{q}" for p, q in x[2]) + "  | ASK " + " ".join(f"{p:.2f}x{q}" for p, q in x[3]))
    elif k == "T":
        print(f"{s:7.2f}            TRADE {x[2]:.2f} x{x[3]}  (server ts lag {1000*(x[0]-x[1]):.0f} ms)")
    else:
        print(f"{s:7.2f}            NEWS {x.get('type')} {x.get('stage','')} {x.get('source','')} {x.get('time')}")
