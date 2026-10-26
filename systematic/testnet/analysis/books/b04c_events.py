"""Task 4 (events): how the book and the consensus reprice at formation (60 s), the preliminary (570 s) and final
(740 s) reports, and around official advisories; and whether book-consensus divergence accrues at advisories.
usage: python3 -I b04c_events.py recording.jsonl history_dir out.json

Book level at time t = median touch mid over [t-1, t+1] (0.25-s grid). Consensus level at real t = row floor(t/2).
Move = level(te+24) - level(te-4) in bps of level(te-4). t50/t90 = first time after te-4 the level has covered
50%/90% of the move (seconds relative to te).
"""
import sys, os, json, bisect, statistics, math, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
order = sorted(rounds, key=lambda d: rounds[d]["open"])
FR = {(d, s): [f for f in D["books"][s] if f.date == d] for d in order for s in bl.SYMBOLS}
RA = {k: [f.ra for f in v] for k, v in FR.items()}


def mid_at(d, s, t):
    fr, ras = FR[(d, s)], RA[(d, s)]
    x = rounds[d]["open"] + t
    j = bisect.bisect_right(ras, x) - 1
    if j < 0 or bl.seg_at(D["segs"], x) != fr[j].seg:
        return None
    return fr[j].mid


def level(d, s, t):
    v = [mid_at(d, s, t + k * 0.25) for k in range(-4, 5)]
    v = [x for x in v if x is not None]
    return statistics.median(v) if v else None


def cons(d, s, t):
    i = int(t // 2)
    c = D["consensus"][d][s]
    return c[i] if 0 <= i < len(c) else None


def timing(fn, te, pre_dt=-4, post_dt=24, step=0.5):
    a, b = fn(te + pre_dt), fn(te + post_dt)
    if a is None or b is None:
        return None
    mv = (b - a) / a * 1e4
    t50 = t90 = None
    k = 0
    while te + pre_dt + k * step <= te + post_dt:
        t = te + pre_dt + k * step
        v = fn(t)
        if v is not None and abs(b - a) > 0:
            fr_ = (v - a) / (b - a)
            if t50 is None and fr_ >= 0.5:
                t50 = t - te
            if t90 is None and fr_ >= 0.9:
                t90 = t - te
        k += 1
    return {"move_bps": mv, "t50": t50, "t90": t90}


res = {"events": {}, "advisory_divergence": {}}
EV = [("formation", 60.0), ("prelim", 570.0), ("final", 740.0)]
for name, te in EV:
    print(f"\n== {name} (real {te:.0f} s): book vs consensus; move bps (t50/t90 s after event); only |cons move| >= 30 bp ==")
    rows = []
    for d in order:
        for s in bl.SYMBOLS:
            bk = timing(lambda t: level(d, s, t), te)
            cn = timing(lambda t: cons(d, s, t), te, step=2.0)
            if bk is None or cn is None:
                continue
            rows.append((d, s, bk, cn))
            res["events"].setdefault(name, []).append({"date": d, "sym": s, "book": bk, "cons": cn})
    for d, s, bk, cn in rows:
        if abs(cn["move_bps"]) >= 30 or abs(bk["move_bps"]) >= 30:
            print(f"  {d} {s:6s} book {bk['move_bps']:+7.0f} ({bk['t50']},{bk['t90']})   cons {cn['move_bps']:+7.0f} ({cn['t50']},{cn['t90']})")
    big = [(bk, cn) for d, s, bk, cn in rows if abs(cn["move_bps"]) >= 30 and bk["t50"] is not None and cn["t50"] is not None]
    if big:
        lag = [bk["t50"] - cn["t50"] for bk, cn in big]
        ratio = [bk["move_bps"] / cn["move_bps"] for bk, cn in big]
        print(f"  summary (n={len(big)}): book t50 median {statistics.median(bk['t50'] for bk, cn in big):+.1f} s, "
              f"cons t50 median {statistics.median(cn['t50'] for bk, cn in big):+.1f} s, book-minus-cons t50 median {statistics.median(lag):+.1f} s; "
              f"book/cons move ratio median {statistics.median(ratio):.2f} [p25 {sorted(ratio)[len(ratio)//4]:.2f}, p75 {sorted(ratio)[3*len(ratio)//4]:.2f}]; "
              f"sign agreement {sum(1 for bk, cn in big if bk['move_bps']*cn['move_bps']>0)}/{len(big)}")
        res["events"][name + "_summary"] = {"n": len(big), "book_t50_med": statistics.median(bk['t50'] for bk, cn in big),
                                            "cons_t50_med": statistics.median(cn['t50'] for bk, cn in big),
                                            "lag_med": statistics.median(lag), "ratio_med": statistics.median(ratio)}

# formation path detail at 1-s resolution for the ETFs and two stocks
print("\n== formation path: book level and consensus, % vs t=56 s ==")
TT = [56, 58, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 72, 74, 76, 78, 80, 84, 90]
for d in order:
    for s in ("PORT", "HAVEN", "ISLE", "ARCH", "REBLD", "RECON"):
        b0, c0 = level(d, s, 56), cons(d, s, 56)
        if b0 is None:
            continue
        bl_ = [level(d, s, t) for t in TT]
        cl_ = [cons(d, s, t) for t in TT]
        print(f"  {d} {s:6s} book " + " ".join(f"{(x-b0)/b0*100:+5.1f}" if x else "  -- " for x in bl_))
        print(f"  {d} {s:6s} cons " + " ".join(f"{(x-c0)/c0*100:+5.1f}" if x else "  -- " for x in cl_))
print("  t:          " + " ".join(f"{t:5d}" for t in TT))

# divergence accrual at official advisories (testnet advisories differ from the research ones)
adv = {}
for ra, m, sg in D["news"]:
    if m.get("type") == "advisory" and m.get("source") == "official" and m.get("time", "")[:10] in rounds:
        d = m["time"][:10]
        adv.setdefault(d, []).append(ra - rounds[d]["open"])
print("\n== divergence (book level - consensus, bps) change over [te-2, te+10] at official advisories vs all 12-s windows 70-538 s ==")
for s in bl.SYMBOLS:
    def off(d, t):
        b, c = level(d, s, t), cons(d, s, t)
        return None if b is None or c is None else (b - c) / c * 1e4
    at, base = [], []
    for d in order:
        for te in adv.get(d, []):
            if 70 <= te <= 538:
                a, b = off(d, te - 2), off(d, te + 10)
                if a is not None and b is not None:
                    at.append(b - a)
        t = 70.0
        while t + 12 <= 538:
            a, b = off(d, t), off(d, t + 12)
            if a is not None and b is not None:
                base.append(b - a)
            t += 12
    if at and base:
        ra_ = math.sqrt(statistics.fmean(x * x for x in at)); rb = math.sqrt(statistics.fmean(x * x for x in base))
        res["advisory_divergence"][s] = {"n_adv": len(at), "rms_adv": ra_, "n_base": len(base), "rms_base": rb}
        print(f"  {s:6s} rms d(offset) at advisories {ra_:6.1f} bp (n={len(at)}) vs all windows {rb:6.1f} bp (n={len(base)}); ratio {ra_/rb:.2f}")
json.dump(res, open(out, "w"), indent=1)
