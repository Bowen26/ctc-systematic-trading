"""Is the testnet book a time-shifted copy of market.csv? RMS of log(book(t)/consensus(t+k)) over t = 100..500 s
(2-s grid) for shifts k; and the book/consensus path of HAVEN, PORT, FERRY around storm end.
usage: python3 -I v10_gap_path.py <recording> <history_dir>
"""
import sys, os, math, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from vlib import *

rec, hist = sys.argv[1], sys.argv[2]
F = load_rec(rec)
op = round_opens(F)
B = books_by_round(F, op)
KS = [-30, -10, -4, 0, 4, 10, 20, 40, 80, 160]
print("RMS log(book(t)/cons(t+k)) in bp, t=100..500 s, by round; min marked *")
for key in [(225, 1), (225, 2), (225, 3), (224, 3)]:
    mk = load_market(hist, key[0])[key[1]]
    t0 = 230 if key == (224, 3) else 100
    for s in ("PORT", "ISLE", "FERRY", "HAVEN", "GEN", "ARCH"):
        row = []
        for k in KS:
            v = []
            for t in range(t0, 501, 2):
                b = mid(book_at(B[key][s], float(t)))
                j = (t + k) // 2
                if b is None or not (0 <= j < 390):
                    continue
                v.append(math.log(b / mk[j][s]) ** 2)
            row.append(math.sqrt(st.mean(v)) * 1e4)
        mn = min(row)
        print("%s %-6s " % ("%dr%d" % key, s) + " ".join(("k=%+d:%4.0f%s" % (k, x, "*" if x == mn else " ")) for k, x in zip(KS, row)))
print("\nbook mid / consensus around storm end and reports (round, sym: t -> book, cons)")
for key in [(225, 1), (225, 2), (225, 3)]:
    mk = load_market(hist, key[0])[key[1]]
    for s in ("HAVEN", "PORT", "FERRY", "ISLE"):
        cells = []
        for t in (300, 450, 500, 530, 540, 546, 552, 560, 574, 600, 700, 744, 760):
            b = mid(book_at(B[key][s], float(t)))
            cells.append("%d:%.2f/%.2f" % (t, b if b else float("nan"), mk[min(t // 2, 389)][s]))
        print("%dr%d %-6s %s" % (key[0], key[1], s, " ".join(cells)))
S = load_settle(hist, 225)
print("season 225 settlement:", S)
