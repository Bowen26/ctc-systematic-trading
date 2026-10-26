"""Independent, simplified S3 replay for season 225 (cross-check of rebacktest r11 'REOPT without H1').
Rule (REOPT-like): every 2 s over 80-538 s, mu/sd = engine cache (fv_loso_225, value after messages <= t);
 target +L if mu - ask > k*sd + M*(c12 + FO)*P, -L if bid - mu > same; close a long if mu < bid, a short if mu > ask;
 move toward target by L/12 per 2 s. k = 0.1 (r1-2) / 0.2 (r3), M = 2, FO = 36 bp stocks / 17 bp ETFs,
 c12 = cost of an L/12 clip (book: walked on the live book; consensus: RT model h + gamma/12).
 Rounds 1-2 flattened over 540-568 s in L/12 clips (rest at 570 s); round 3 held to settlement.json.
Venues: BOOK = decide on the book (ask/bid = touch) and fill by walking the recorded book at t+0.3 s (own fills
 deplete the frame until the next frame); CONS = decide on consensus row floor(t/2) and fill at row floor(t/2)+1
 x (1 +- (0.25% + 0.5%/12)).  No follow-on, no impact: optimistic for both.
usage: python3 -I v12_s3_book.py <recording> <history_dir> <fv_cache_dir>
"""
import sys, os, json, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from vlib import *

rec, hist, cdir = sys.argv[1:4]
F = load_rec(rec)
op = round_opens(F)
B = books_by_round(F, op)
MK = load_market(hist, 225)
S = load_settle(hist, 225)
ent = json.load(open(os.path.join(cdir, "fv_loso_225.json")))["entries"]
FV = {1: [], 2: [], 3: []}
for e in ent:
    FV[int(e[0])].append((float(e[1]), e[2:14], e[14:26]))
TR = [s for s in SYMS if s != "REIN"]


def run(venue, r, M=2.0, clipf=1 / 12, flat0=540.0, fclip=None):
    key = (225, r)
    k = 0.1 if r < 3 else 0.2
    fv = FV[r]; ts = [x[0] for x in fv]
    pnl = {}
    for s in TR:
        i = SYMS.index(s); L = LIM[s]; clip = max(1, int(round(L * clipf)))
        FO = 17e-4 if s in ("ARCH", "REBLD") else 36e-4
        ser = B[key][s]; bts = [x[0] for x in ser]
        pos, cash, tgt = 0, 0.0, 0
        dep = {}

        def fill(t, side, q):
            nonlocal pos, cash
            if q <= 0:
                return
            if venue == "CONS":
                j = int(t // 2) + 1
                p = MK[r][j][s] * (1 + side * (0.0025 + 0.005 * q / L))
                pos += side * q; cash -= side * q * p
                return
            jj = bisect.bisect_right(bts, t + 0.3) - 1
            if jj < 0:
                return
            lv = ser[jj][2] if side > 0 else ser[jj][1]
            d = dep.setdefault(jj, {})
            left = q
            for p, n in lv:
                a = n - d.get(p, 0)
                if a <= 0:
                    continue
                x = min(a, left); d[p] = d.get(p, 0) + x
                pos += side * x; cash -= side * x * p; left -= x
                if left == 0:
                    break

        t = 80.0
        while t <= 538.0:
            j = bisect.bisect_right(ts, t) - 1
            mu, sd = fv[j][1][i], fv[j][2][i]
            if venue == "CONS":
                c = MK[r][int(t // 2)][s]
                bid = ask = c
                c12 = 0.0025 + 0.005 * clipf
                P = c
            else:
                b = book_at(ser, t + 0.3)
                if b is None or not b[1] or not b[2]:
                    t += 2; continue
                bid, ask = b[1][0][0], b[2][0][0]
                P = 0.5 * (bid + ask)
                pa, pb = walk(b[2], clip), walk(b[1], clip)
                c12 = 0.5 * ((pa - P) + (P - pb)) / P if pa and pb else 0.01
            thr = k * sd + M * (c12 + FO) * P
            if mu - ask > thr:
                tgt = L
            elif bid - mu > thr:
                tgt = -L
            elif tgt > 0 and mu < bid:
                tgt = 0
            elif tgt < 0 and mu > ask:
                tgt = 0
            if tgt != pos:
                side = 1 if tgt > pos else -1
                fill(t, side, min(clip, abs(tgt - pos)))
            t += 2.0
        if r < 3:
            t = flat0
            fc = clip if fclip is None else max(1, int(round(L * fclip)))
            while t <= 568.0 and pos != 0:
                side = -1 if pos > 0 else 1
                fill(t, side, min(fc, abs(pos)))
                t += 2.0
            tt = 570.0
            while pos != 0 and tt < 600:
                side = -1 if pos > 0 else 1
                fill(tt, side, abs(pos)); tt += 2
            pnl[s] = cash + pos * S[s]
        else:
            pnl[s] = cash + pos * S[s]
    return pnl


for M in (2.0, 3.0):
    print("== threshold multiplier M=%.0f ==" % M)
    for venue in ("CONS", "BOOK"):
        tot = []
        for r in (1, 2, 3):
            p = run(venue, r, M=M)
            tot.append(sum(p.values()))
            print("%s r%d %+8.0f  %s" % (venue, r, tot[-1], " ".join("%s %+.0f" % (s, v) for s, v in p.items())))
        print("%s season 225 total %+.0f" % (venue, sum(tot)))

print("== variant: rounds 1-2 flatten over 552-568 s in L/8 clips (after the book's storm-end snap) ==")
for venue in ("CONS", "BOOK"):
    tot = [sum(run(venue, r, flat0=552.0, fclip=1 / 8).values()) for r in (1, 2)]
    print("%s r1 %+.0f r2 %+.0f" % (venue, tot[0], tot[1]))
