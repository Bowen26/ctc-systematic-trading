"""Dump book mid / touch vs consensus every N real seconds for one round.
usage: python3 -I ts_dump.py recording.jsonl s224 s225 SEASON ROUND STEP [SYMS,...] [from] [to]"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec
R = rec.Recording(sys.argv[1], sys.argv[2:4])
key = (int(sys.argv[4]), int(sys.argv[5])); step = float(sys.argv[6])
syms = sys.argv[7].split(",") if len(sys.argv) > 7 else rec.SYMS
a = float(sys.argv[8]) if len(sys.argv) > 8 else 0; z = float(sys.argv[9]) if len(sys.argv) > 9 else 780
ri = R.rounds[key]
print("real_s " + " ".join(f"{s:>22s}" for s in syms))
s = a
while s <= z:
    out = []
    for sym in syms:
        b = R.book_at(sym, R.wall(key, s))
        c = ri.cons(sym, s)
        if b is None or c is None: out.append(f"{'-':>22s}"); continue
        bb = f"{b[2][0][0]:7.2f}" if b[2] else "   none"; aa = f"{b[3][0][0]:<7.2f}" if b[3] else "none   "
        out.append(f"{bb}/{aa} c{c:7.2f}")
    print(f"{s:6.1f} " + " ".join(out))
    s += step
