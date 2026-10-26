"""Season-open check: testnet book at the season-225 open vs the packet's live starting prices and vs market.csv row 0.
usage: python3 -I b04e_open_vs_packet.py recording.jsonl history_dir
book level = median touch mid over real 3-10 s of round 1 (1802-05-10); consensus = mean of rows 1-5."""
import sys, os, bisect, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl
D = bl.load(sys.argv[1], sys.argv[2])
d = "1802-05-10"; o = D["rounds"][d]["open"]
PACKET = {"PORT": 72.48, "ISLE": 50.26, "AQUA": 27.33, "GRID": 36.41, "FERRY": 17.91, "RECON": 52.38, "MAT": 35.81,
          "GEN": 40.30, "HAVEN": 39.55, "REIN": 89.36, "ARCH": 289.20, "REBLD": 216.68}
print("symbol  book(3-10s)  packet  book-packet(bp)  cons(rows1-5)  book-cons(bp)  first trade px (0-10 s)  trades in 0-10 s")
for s in bl.SYMBOLS:
    fr = [f for f in D["books"][s] if f.date == d]; ras = [f.ra for f in fr]
    xs = []
    t = 3.0
    while t <= 10:
        j = bisect.bisect_right(ras, o + t) - 1
        if j >= 0 and fr[j].mid and bl.seg_at(D["segs"], o + t) == fr[j].seg:
            xs.append(fr[j].mid)
        t += 0.25
    b = statistics.median(xs)
    c = statistics.fmean(D["consensus"][d][s][1:6])
    tr = [x for x in D["trades"][s] if x.date == d and 0 <= x.t < 10]
    print(f"{s:6s} {b:10.2f} {PACKET[s]:8.2f} {1e4*(b/PACKET[s]-1):+10.0f} {c:12.2f} {1e4*(b/c-1):+12.0f}   "
          f"{tr[0].price if tr else float('nan'):10.2f}   n_trades<10s={len(tr)}")

print("\nwall fills at the open: trades in real 0-3 s of every round priced > 3% away from the book level at 3-10 s")
for dd, v in sorted(D["rounds"].items(), key=lambda kv: kv[1].get("open", 0)):
    if not v.get("season") or "open" not in v:
        continue
    oo = v["open"]
    for s in bl.SYMBOLS:
        fr = [f for f in D["books"][s] if f.date == dd]; ras = [f.ra for f in fr]
        xs = []
        t = 3.0
        while t <= 10:
            j = bisect.bisect_right(ras, oo + t) - 1
            if j >= 0 and fr[j].mid and bl.seg_at(D["segs"], oo + t) == fr[j].seg:
                xs.append(fr[j].mid)
            t += 0.25
        if not xs:
            continue
        b = statistics.median(xs)
        for x in D["trades"][s]:
            if x.date == dd and 0 <= x.t < 3 and abs(x.price / b - 1) > 0.03:
                print(f"  {dd} {s:6s} t={x.t:5.2f}s px {x.price:8.2f} x{x.qty:<5d} vs level {b:8.2f} ({1e4*(x.price/b-1):+.0f} bp, cost ${abs(x.price-b)*x.qty:.0f})")
