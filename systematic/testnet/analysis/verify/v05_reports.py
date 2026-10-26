"""(d) Report reprice timing on the books vs consensus, and the round-3 final-report (S1-r3) opportunity.
te = received_at of the damage_report frame (real s in round). Book mid path at te+k; consensus path by row.
S1-r3: settlement S from settlement.json; stale value = sum over levels priced through S -+ eps of |S-p|*q,
capped at L per symbol (flat start, one direction).
usage: python3 -I v05_reports.py <recording> <history_dir>
"""
import sys, os, bisect, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from vlib import *

rec, hist = sys.argv[1], sys.argv[2]
F = load_rec(rec)
op = round_opens(F)
B = books_by_round(F, op)
T = trades_by_round(F, op)
MK = {224: load_market(hist, 224), 225: load_market(hist, 225)}
SET = {224: load_settle(hist, 224), 225: load_settle(hist, 225)}
EPS = dict(PORT=0.07, ISLE=0.02, AQUA=0.02, GRID=0.06, FERRY=0.09, RECON=0.02, MAT=0.02, GEN=0.11,
           HAVEN=0.02, REIN=0.02, ARCH=0.17, REBLD=0.12)
STK = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN"]

reps = []
for w, m in news_msgs(F):
    if m.get("type") == "damage_report":
        k = DATE_ROUND.get(m["time"][:10])
        if k in op:
            reps.append((k, m["stage"], w - op[k]["open"]))
print("damage reports received (round, stage, real s):", [(k, s, round(t, 3)) for k, s, t in reps])

KS = [0, 0.5, 1, 2, 3, 4, 6, 8, 10, 12, 16, 20]
print("\n== book reprice: share of the te-0.2 -> te+20 s book-mid move done at te+k (symbols with |move| >= 1%) ==")
print("%-10s %-6s %7s %7s | %s | consensus share at rows +1..+5 (real +2..+10 from report row)" % (
    "event", "sym", "move%", "cmove%", " ".join("%5s" % k for k in KS)))
agg = {k: [] for k in KS}
aggc = {}
for key, stage, te in reps:
    rows = MK[key[0]][key[1]]
    r0 = int(round(te)) // 2  # report row (570 -> 285, 740 -> 370)
    for s in STK + ["ARCH", "REBLD"]:
        ser = B[key][s]
        m0 = mid(book_at(ser, te - 0.2))
        m20 = mid(book_at(ser, te + 20))
        if m0 is None or m20 is None:
            continue
        mv = m20 - m0
        c0, c10 = rows[r0][s], rows[r0 + 10][s]
        cm = (c10 / c0 - 1) * 100
        if abs(mv / m0) < 0.01:
            continue
        sh = [(mid(book_at(ser, te + k)) - m0) / mv for k in KS]
        csh = [(rows[r0 + j][s] - c0) / (c10 - c0) if c10 != c0 else float("nan") for j in range(1, 6)]
        print("%s %-5s %-6s %+7.2f %+7.2f | %s | %s" % ("%dr%d" % key, stage[:5], s, mv / m0 * 100, cm,
              " ".join("%5.2f" % x for x in sh), " ".join("%5.2f" % x for x in csh)))
        if s in STK:
            for k, x in zip(KS, sh):
                agg[k].append(x)
            for j, x in enumerate(csh):
                aggc.setdefault(j, []).append(x)
print("stocks, median share done: " + " ".join("+%gs %.2f" % (k, st.median(agg[k])) for k in KS if agg[k]))
print("stocks consensus median share at rows +1..+5: " + " ".join("%.2f" % st.median(v) for j, v in sorted(aggc.items())))

print("\n== S1-r3: stale value vs settlement on the recorded books after the round-3 final report ==")
for key, stage, te in reps:
    if key[1] != 3 or stage != "final":
        continue
    S = SET[key[0]]
    print("-- %s te=%.3f --" % (key, te))
    # (1) single-frame value at te+k, per symbol, capped at L
    for k in (0.1, 0.3, 0.5, 1, 2, 4, 6, 8, 10, 15):
        tot, per = 0.0, {}
        for s in SYMS:
            b = book_at(B[key][s], te + k)
            if b is None:
                continue
            L, Sv, e = LIM[s], S[s], EPS[s]
            vb = 0.0; left = L
            for p, q in b[2]:  # asks below S-eps -> buy
                if p < Sv - e and left > 0:
                    x = min(q, left); vb += x * (Sv - p); left -= x
            vs = 0.0; left = L
            for p, q in b[1]:
                if p > Sv + e and left > 0:
                    x = min(q, left); vs += x * (p - Sv); left -= x
            v = max(vb, vs)
            if v > 0:
                per[s] = round(v)
            tot += v
        print("  single frame te+%-4g: $%6.0f  %s" % (k, tot, per))
    # (2) sweeper: start at te+d, take every stale level (limit S-+eps) in every new frame until 760 s, L cap per symbol
    for d in (0.1, 0.3, 1.0, 2.0, 4.0, 6.0):
        tot, per = 0.0, {}
        for s in SYMS:
            ser = B[key][s]
            L, Sv, e = LIM[s], S[s], EPS[s]
            used, val = 0, 0.0
            start = te + d
            frames = [f for f in ser if start <= f[0] <= 760.0]
            b0 = book_at(ser, start)
            if b0 is not None and (not frames or frames[0][0] > start):
                frames = [b0] + frames
            seen = {}
            for f in frames:
                # assume we took everything stale in the previous frame; a level re-appearing at same price is new size
                for side, lv in ((1, f[2]), (-1, f[1])):
                    for p, q in lv:
                        good = (side > 0 and p < Sv - e) or (side < 0 and p > Sv + e)
                        if not good or used >= L:
                            continue
                        prev = seen.get((side, p), 0)
                        newq = q if prev == 0 else max(0, q - prev)
                        x = min(newq, L - used)
                        if x > 0:
                            used += x; val += x * abs(Sv - p)
                        seen[(side, p)] = q
                # levels no longer present are reset
                present = set((1, p) for p, _ in f[2]) | set((-1, p) for p, _ in f[1])
                for kk in list(seen):
                    if kk not in present:
                        seen.pop(kk)
            if val > 0:
                per[s] = round(val)
            tot += val
        print("  sweeper from te+%-4g to 760 s: $%6.0f  %s" % (d, tot, per))
    # (3) others' trades at stale prices after te (value captured by others)
    cap = 0.0; capd = {}
    for s in SYMS:
        Sv, e = S[s], EPS[s]
        for t, p, q in T[key][s]:
            if te <= t <= 760 and abs(p - Sv) > e:
                cap += q * abs(Sv - p) if ((p < Sv) or (p > Sv)) else 0
                capd[s] = capd.get(s, 0) + round(q * abs(Sv - p))
    print("  others' trades te..760 s priced beyond S+-eps: |S-p|*q summed $%.0f %s" % (cap, capd))
    # (4) sanity-check rule: |mid - S| > max(3 eps, 0.5%) at t
    for t in (742, 744, 746, 748, 750, 752, 756, 760):
        bad = []
        for s in SYMS:
            m = mid(book_at(B[key][s], t))
            if m is None:
                continue
            thr = max(3 * EPS[s], 0.005 * S[s])
            if abs(m - S[s]) > thr:
                bad.append("%s(%.2f%%)" % (s, (m / S[s] - 1) * 100))
        print("  sanity check at %d s would cancel: %s" % (t, bad))
    # consensus: playbook oracle on market.csv rows 371/372 at 25 bp, size L
    rows = MK[key[0]][key[1]]
    for rr in (371, 372, 373, 375):
        v = 0.0
        for s in SYMS:
            p = rows[rr][s]
            v += max(0.0, abs(S[s] - p) - 0.0025 * p) * LIM[s]
        print("  consensus oracle filled at row %d (%d s), 25 bp: $%.0f" % (rr, 2 * rr, v))
