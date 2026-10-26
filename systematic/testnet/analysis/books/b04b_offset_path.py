"""Path of book touch-mid vs consensus through each round (diagnostic for task 4).
usage: python3 -I b04b_offset_path.py recording.jsonl history_dir"""
import sys, os, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl
D = bl.load(sys.argv[1], sys.argv[2])
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") and "open" in v}
order = sorted(rounds, key=lambda d: rounds[d]["open"])
TS = [1, 10, 30, 56, 62, 66, 70, 80, 100, 150, 220, 300, 400, 500, 538, 566, 576, 600, 700, 736, 746, 760, 778]
PACKET = {"PORT": 72.48, "ISLE": 50.26, "AQUA": 27.33, "GRID": 36.41, "FERRY": 17.91, "RECON": 52.38, "MAT": 35.81,
          "GEN": 40.30, "HAVEN": 39.55, "REIN": 89.36, "ARCH": 289.20, "REBLD": 216.68}
for d in order:
    v = rounds[d]
    print(f"\n=== {d} season {v['season']} round {v['round']}: cells = book touch mid / consensus row floor(t/2), (bps) ===")
    print("t:      " + " ".join(f"{t:>8d}" for t in TS))
    for sym in bl.SYMBOLS:
        fr = [f for f in D["books"][sym] if f.date == d]
        ras = [f.ra for f in fr]
        cells = []
        for t in TS:
            x = v["open"] + t
            j = bisect.bisect_right(ras, x) - 1
            c = D["consensus"][d][sym][int(t // 2)]
            if j < 0 or bl.seg_at(D["segs"], x) != fr[j].seg or fr[j].mid is None:
                cells.append(f"{'--':>8s}"); continue
            m = fr[j].mid
            cells.append(f"{(m - c) / c * 1e4:+8.0f}")
        print(f"{sym:6s}  " + " ".join(cells) + f"   cons0 {D['consensus'][d][sym][0]:.2f} packet {PACKET[sym]:.2f} ({(PACKET[sym]-D['consensus'][d][sym][0])/D['consensus'][d][sym][0]*1e4:+.0f} bp)")
