"""Where do empty / one-sided / single-level book frames occur (real s in round)?
usage: python3 -I b02c_empty.py recording.jsonl history_dir"""
import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl
D = bl.load(sys.argv[1], sys.argv[2])
c = collections.Counter()
ex = collections.defaultdict(list)
for s in bl.SYMBOLS:
    for f in D["books"][s]:
        kind = "empty" if not f.bids and not f.asks else "one-sided" if not f.bids or not f.asks else \
               "1 level/side" if len(f.bids) == 1 and len(f.asks) == 1 else None
        if kind:
            where = "pre-open/pause (t<0 or t>=780)" if not (0 <= f.t < 780) else "open 0-3 s" if f.t < 3 else \
                    "close 776-780 s" if f.t >= 776 else "in session"
            c[(kind, where)] += 1
            if where == "in session":
                ex[kind].append((s, f.date, round(f.t, 2)))
for k, v in sorted(c.items()):
    print(f"  {k[0]:13s} {k[1]:32s} {v}")
for k, v in ex.items():
    print(f"  in-session {k}: {v[:12]}")
