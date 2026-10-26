"""Target for rounds 1-2 should be the post-storm level (S3 flattens at 540-568 s): also reported vs the 600-s level.
Who is closer to settlement mid-storm: the testnet book, the research consensus, or the engine?
And does the S3 signal (engine - price) still predict (settlement - price) when price = the book?
Samples every 10 s over 100-530 s (storm) of season-225 rounds (and 224 r3 from 230 s); connected books only.
Engine = team fair-value cache fv_loso_<season>.json (leave-one-season-out), value after messages <= t.
usage: python3 -I v08_book_vs_truth.py <recording> <history_dir> <fv_cache_dir>
"""
import sys, os, json, bisect, math, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from vlib import *

rec, hist, cdir = sys.argv[1], sys.argv[2], sys.argv[3]
F = load_rec(rec)
op = round_opens(F)
segs = connections(F)
ack_t = [s for s, _ in segs]; seg_end = [e for _, e in segs]
B = books_by_round(F, op)
NS = len(SYMS)


def fv_load(season):
    ent = json.load(open(os.path.join(cdir, "fv_loso_%03d.json" % season)))["entries"]
    out = {1: [], 2: [], 3: []}
    for e in ent:
        out[int(e[0])].append((float(e[1]), e[2:2 + NS], e[2 + NS:2 + 2 * NS]))
    return out


def cmid(series, o, t):
    w = o + t
    k = bisect.bisect_right(ack_t, w) - 1
    if k < 0 or w > seg_end[k]:
        return None
    b = book_at(series, t)
    if b is None or b[0] + o < ack_t[k]:
        return None
    return mid(b)


def corr(x, y):
    mx, my = st.mean(x), st.mean(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x); syy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else float("nan"), sxy / sxx if sxx > 0 else float("nan")


TR = [s for s in SYMS if s != "REIN"]
allrows = []
for key in [(224, 3), (225, 1), (225, 2), (225, 3)]:
    season, r = key
    mk = load_market(hist, season)[r]
    S = load_settle(hist, season)
    fv = fv_load(season)[r]
    fvt = [x[0] for x in fv]
    o = op[key]["open"]
    t0 = 230 if key == (224, 3) else 100
    rows = []
    for t in range(t0, 531, 10):
        j = bisect.bisect_right(fvt, float(t)) - 1
        if j < 0:
            continue
        mu = fv[j][1]
        for i, s in enumerate(SYMS):
            if s not in TR:
                continue
            bm = cmid(B[key][s], o, float(t))
            if bm is None:
                continue
            c = mk[t // 2][s]
            b600 = cmid(B[key][s], o, 600.0)
            rows.append(dict(key=key, t=t, s=s, book=math.log(bm / c), eng=math.log(mu[i] / c),
                             S=math.log(S[s] / c), C6=math.log(mk[300][s] / c),
                             B6=math.log(b600 / c) if b600 else math.log(mk[300][s] / c), w=1.0))
    allrows += rows
    rm = lambda f: math.sqrt(st.mean([f(x) ** 2 for x in rows])) * 1e4
    print("%s n=%d  RMSE vs settlement (bp, log): consensus %.0f  book %.0f  engine %.0f" % (
        key, len(rows), rm(lambda x: x["S"]), rm(lambda x: x["S"] - x["book"]), rm(lambda x: x["S"] - x["eng"])))
    cb = corr([x["book"] for x in rows], [x["eng"] for x in rows])
    cs = corr([x["book"] for x in rows], [x["S"] for x in rows])
    ce = corr([x["eng"] - x["book"] for x in rows], [x["S"] - x["book"] for x in rows])
    cc = corr([x["eng"] for x in rows], [x["S"] for x in rows])
    rm6 = lambda f: math.sqrt(st.mean([f(x) ** 2 for x in rows])) * 1e4
    print("    vs post-storm level at 600 s (consensus row 300): consensus %.0f book %.0f engine %.0f; vs book mid at 600 s: consensus %.0f book %.0f engine %.0f" % (
        rm6(lambda x: x["C6"]), rm6(lambda x: x["C6"] - x["book"]), rm6(lambda x: x["C6"] - x["eng"]),
        rm6(lambda x: x["B6"]), rm6(lambda x: x["B6"] - x["book"]), rm6(lambda x: x["B6"] - x["eng"])))
    c6 = corr([x["eng"] - x["book"] for x in rows], [x["B6"] - x["book"] for x in rows])
    c6c = corr([x["eng"] for x in rows], [x["C6"] for x in rows])
    print("    S3 signal vs 600-s level: on consensus corr(eng-cons, C600-cons) %.2f; on book corr(eng-book, B600-book) %.2f" % (c6c[0], c6[0]))
    print("    corr(book-cons, engine-cons) %.2f; corr(book-cons, S-cons) %.2f slope %.2f; "
          "S3 signal on consensus corr(eng-cons, S-cons) %.2f slope %.2f; on book corr(eng-book, S-book) %.2f slope %.2f" % (
              cb[0], cs[0], cs[1], cc[0], cc[1], ce[0], ce[1]))

print("\nper symbol, pooled 4 rounds: RMSE vs S (bp) consensus / book / engine; corr(eng-book, S-book)")
for s in TR:
    rows = [x for x in allrows if x["s"] == s]
    rm = lambda f: math.sqrt(st.mean([f(x) ** 2 for x in rows])) * 1e4
    ce = corr([x["eng"] - x["book"] for x in rows], [x["S"] - x["book"] for x in rows])
    print("%-6s n=%3d  %5.0f / %5.0f / %5.0f   corr %.2f" % (s, len(rows), rm(lambda x: x["S"]), rm(lambda x: x["S"] - x["book"]),
                                                     rm(lambda x: x["S"] - x["eng"]), ce[0]))
# sign-rule edge proxy: mean of sign(eng - P) * (S - P) in bp, P = cons or book, only where |eng-P| > 75 bp
for lab, f in (("consensus", lambda x: 0.0), ("book", lambda x: x["book"])):
    v = [math.copysign(1, x["eng"] - f(x)) * (x["S"] - f(x)) * 1e4 for x in allrows if abs(x["eng"] - f(x)) > 0.0075]
    v3 = [math.copysign(1, x["eng"] - f(x)) * (x["S"] - f(x)) * 1e4 for x in allrows if abs(x["eng"] - f(x)) > 0.0075 and x["key"][1] == 3]
    print("sign rule |eng-P|>75bp, P=%-9s: n=%d mean %+.0f bp (round 3 only: n=%d mean %+.0f bp)" % (
        lab, len(v), st.mean(v), len(v3), st.mean(v3) if v3 else float("nan")))
