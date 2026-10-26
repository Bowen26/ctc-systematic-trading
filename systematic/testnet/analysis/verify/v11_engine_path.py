"""Engine mu (fv_loso cache) vs testnet book mid vs consensus at selected times, season 225.
usage: python3 -I v11_engine_path.py <recording> <history_dir> <fv_cache_dir>"""
import sys, os, json, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from vlib import *
rec, hist, cdir = sys.argv[1:4]
F = load_rec(rec); op = round_opens(F); B = books_by_round(F, op)
ent = json.load(open(os.path.join(cdir, "fv_loso_225.json")))["entries"]
fv = {1: [], 2: [], 3: []}
for e in ent:
    fv[int(e[0])].append((float(e[1]), e[2:14]))
for r in (1, 2, 3):
    mk = load_market(hist, 225)[r]; ts = [x[0] for x in fv[r]]
    for s in ("HAVEN", "PORT", "ISLE", "FERRY", "ARCH"):
        i = SYMS.index(s); cells = []
        for t in (100, 200, 300, 400, 500, 538, 560):
            j = bisect.bisect_right(ts, float(t)) - 1
            cells.append("%d: eng %.2f book %.2f cons %.2f" % (t, fv[r][j][1][i], mid(book_at(B[(225, r)][s], float(t))), mk[t // 2][s]))
        print("r%d %-6s | %s" % (r, s, " | ".join(cells)))
