"""Task 4 detail: book-minus-consensus offset by round and phase, and noise of book vs consensus in quiet phases.
usage: python3 -I b04d_offset_rounds.py recording.jsonl history_dir out.json
offset_i = (book level at real 2i+1 - consensus row i)/consensus row i (bps); book level = median touch mid over +-1 s.
Quiet windows: 4-58 s and 600-738 s. Change RMS over h = 2, 10, 20, 40 s for book level and consensus.
"""
import sys, os, json, bisect, statistics, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
order = sorted(rounds, key=lambda d: rounds[d]["open"])
FR = {(d, s): [f for f in D["books"][s] if f.date == d] for d in order for s in bl.SYMBOLS}
RA = {k: [f.ra for f in v] for k, v in FR.items()}


def lvl(d, s, t):
    xs = []
    for k in range(-4, 5):
        x = rounds[d]["open"] + t + 0.25 * k
        j = bisect.bisect_right(RA[(d, s)], x) - 1
        if j >= 0 and bl.seg_at(D["segs"], x) == FR[(d, s)][j].seg and FR[(d, s)][j].mid:
            xs.append(FR[(d, s)][j].mid)
    return statistics.median(xs) if xs else None


PH = [("pre", 2, 30), ("early", 30, 60), ("storm", 60, 270), ("after", 270, 390)]
res = {"offset": {}, "noise": {}}
print("offset = book - consensus (bps): mean / sd [p5, p95] by round and phase")
for s in bl.SYMBOLS:
    res["offset"][s] = {}
    line = []
    for d in order:
        cells = []
        for name, a, b in PH:
            e = []
            for i in range(a, b):
                v = lvl(d, s, 2 * i + 1)
                c = D["consensus"][d][s][i]
                if v:
                    e.append((v - c) / c * 1e4)
            if len(e) > 3:
                e.sort()
                r = {"n": len(e), "mean": statistics.fmean(e), "sd": statistics.pstdev(e), "p05": e[int(.05 * (len(e) - 1))], "p95": e[int(.95 * (len(e) - 1))]}
                res["offset"][s][f"{d}|{name}"] = r
                cells.append(f"{name} {r['mean']:+5.0f}/{r['sd']:3.0f}")
            else:
                cells.append(f"{name}   n/a   ")
        line.append(f"{d}: " + " ".join(cells))
    print(f"{s:6s} " + "\n       ".join(line))

print("\nquiet-phase change RMS (bps) over h seconds: book level vs consensus (4-58 s and 600-738 s, 4 rounds pooled)")
for s in bl.SYMBOLS:
    row = {}
    for h in (2, 10, 20, 40):
        bk, cn = [], []
        for d in order:
            for a, b in ((4, 58), (600, 738)):
                t = a
                while t + h <= b:
                    v0, v1 = lvl(d, s, t), lvl(d, s, t + h)
                    c0, c1 = D["consensus"][d][s][int(t // 2)], D["consensus"][d][s][int((t + h) // 2)]
                    if v0 and v1:
                        bk.append(1e4 * math.log(v1 / v0)); cn.append(1e4 * math.log(c1 / c0))
                    t += 2
        row[h] = (math.sqrt(statistics.fmean(x * x for x in bk)), math.sqrt(statistics.fmean(x * x for x in cn)), len(bk),
                  statistics.median(abs(x) for x in bk), statistics.median(abs(x) for x in cn))
    res["noise"][s] = {str(h): {"book_rms": v[0], "cons_rms": v[1], "n": v[2], "book_mad": v[3], "cons_mad": v[4]} for h, v in row.items()}
    print(f"  {s:6s} " + "  ".join(f"h={h:2d}s book {v[0]:5.1f} (med|.| {v[3]:4.1f}) cons {v[1]:5.1f} (med|.| {v[4]:4.1f})" for h, v in row.items()))
json.dump(res, open(out, "w"), indent=1)
