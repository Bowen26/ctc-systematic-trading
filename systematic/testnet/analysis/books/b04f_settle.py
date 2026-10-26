"""End-of-season check: book level in the last seconds of round 3 (765-779 s) vs research settlement.json and
the consensus at row 389. usage: python3 -I b04f_settle.py recording.jsonl history_dir"""
import sys, os, bisect, statistics, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl
D = bl.load(sys.argv[1], sys.argv[2])
for season, d in ((224, "1803-05-11"), (225, "1802-05-12")):
    st = json.load(open(os.path.join(sys.argv[2], f"season-{season:03d}", "settlement.json")))
    o = D["rounds"][d]["open"]
    print(f"season {season} round 3 ({d}): book level 765-779 s vs settlement and consensus row 389")
    for s in bl.SYMBOLS:
        fr = [f for f in D["books"][s] if f.date == d]; ras = [f.ra for f in fr]
        xs, bid, ask = [], [], []
        t = 765.0
        while t < 779.5:
            j = bisect.bisect_right(ras, o + t) - 1
            if j >= 0 and fr[j].mid and bl.seg_at(D["segs"], o + t) == fr[j].seg:
                xs.append(fr[j].mid); bid.append(fr[j].bids[0][0]); ask.append(fr[j].asks[0][0])
            t += 0.25
        if not xs:
            print(f"  {s:6s} no book"); continue
        m = statistics.median(xs); c = D["consensus"][d][s][389]
        print(f"  {s:6s} book {m:8.2f} (touch {statistics.median(bid):.2f}/{statistics.median(ask):.2f})  settle {st[s]:8.2f} ({1e4*(m/st[s]-1):+5.0f} bp)"
              f"  cons389 {c:8.2f} ({1e4*(c/st[s]-1):+5.0f} bp)  settle inside touch: {statistics.median(bid) <= st[s] <= statistics.median(ask)}")
