"""Does the house formation jump (consensus row 28 -> 37, 56 -> 74 s) depend on Lk, the insured loss already
known before the round (sum of earlier rounds' final industry_loss_musd)?  Lk is public before H1 enters, so an
Lk-conditioned H1 direction is admissible.  Train 1-400 and holdout 401-600 separately; mean jump in bp.
usage: python3 -I r05_jump_lk.py <history_dir>"""
import csv, json, os, sys

hist = sys.argv[1]
SY = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
man = json.load(open(os.path.join(hist, "manifest.json")))
BUCK = [(-1, 1), (1, 400), (400, 800), (800, 1100), (1100, 1300), (1300, 9999)]
acc = {}
for se in man["seasons"]:
    n = se["season"]
    split = "train" if n <= 400 else "hold"
    loss = {st["round"]: st["industry_loss_musd"] for st in se["storms"]}
    rows = {1: [], 2: [], 3: []}
    with open(os.path.join(hist, se["dir"], "market.csv")) as f:
        for r in csv.DictReader(f):
            rows[int(r["round"])].append([float(r[s]) for s in SY])
    for rd in (1, 2, 3):
        lk = sum(loss[q] for q in range(1, rd))
        b = next(k for k, (lo, hi) in enumerate(BUCK) if lo <= lk < hi) if rd > 1 else 0
        p0, p1 = rows[rd][28], rows[rd][37]
        for i, s in enumerate(SY):
            j = (p1[i] / p0[i] - 1) * 1e4
            a = acc.setdefault((split, rd, b, s), [])
            a.append(j)
for split in ("train", "hold"):
    print(f"== {split}: mean formation jump (bp, rows 28->37) by round and Lk bucket ($M); n = storms")
    print("round Lk-bucket      n " + " ".join(f"{s:>6s}" for s in SY))
    for rd in (1, 2, 3):
        for b, (lo, hi) in enumerate(BUCK):
            k0 = (split, rd, b, "PORT")
            if k0 not in acc:
                continue
            nn = len(acc[k0])
            print(f"r{rd}   [{lo:5d},{hi:5d}) {nn:4d} " + " ".join(f"{sum(acc[(split, rd, b, s)]) / nn:6.0f}" for s in SY))
