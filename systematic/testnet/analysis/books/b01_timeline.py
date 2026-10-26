"""Round-open fit (wall clock -> market clock), connection segments, phase coverage and server-vs-local clock.
usage: python3 -I b01_timeline.py recording.jsonl history_dir out.json"""
import sys, os, json, statistics, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist, out = sys.argv[1:4]
D = bl.load(rec, hist)
fmt = lambda x: datetime.datetime.fromtimestamp(x).strftime("%H:%M:%S.%f")[:-3]
print("== round open fit (open = received_at - market_seconds/30, storm observations) ==")
res = {"rounds": {}, "segments": []}
for d, v in sorted(D["rounds"].items(), key=lambda kv: kv[1].get("open", 0)):
    if "open" not in v:
        continue
    f = v["fit"]
    print(f"{d} season {v['season']} round {v['round']}: open {fmt(v['open'])} local; storm obs n={f['n_storm_obs']} "
          f"resid sd {f['resid_sd']*1000:.0f} ms, p5 {f['resid_p05']*1000:.0f} p95 {f['resid_p95']*1000:.0f} "
          f"min {f['resid_min']*1000:.0f} max {f['resid_max']*1000:.0f} ms; median-min {1000*(f['open_median']-f['open_min']):.0f} ms; "
          f"news t range {f['first_news_t']:.1f}..{f['last_news_t']:.1f} s")
    print("    resid by type (median ms, n):", {k: (round(1000*x), f['resid_by_type_n'][k]) for k, x in f['resid_by_type_median'].items()})
    res["rounds"][d] = {"season": v["season"], "round": v["round"], "open": v["open"], "fit": f}
opens = sorted((v["open"], d) for d, v in D["rounds"].items() if "open" in v)
print("== gaps between consecutive round opens (s) ==")
for (o1, d1), (o2, d2) in zip(opens, opens[1:]):
    print(f"  {d1} -> {d2}: {o2-o1:.1f} s (pause after close {o2-o1-780:.1f} s)")
print("== connection segments ==")
for i, (a, b) in enumerate(D["segs"]):
    # map to rounds
    def where(x):
        best = None
        for o, d in opens:
            if o - 5 <= x: best = (o, d)
        return f"{best[1]} t={x-best[0]:.1f}" if best else "?"
    print(f"  seg {i}: {fmt(a)} -> {fmt(b)} ({b-a:.1f} s)  [{where(a)} -> {where(b)}]")
    res["segments"].append({"seg": i, "start": a, "end": b, "start_at": where(a), "end_at": where(b)})
print("== book-frame coverage per round and phase (seconds of real time covered by >=1 book frame per 2-s bin, any symbol) ==")
cov = {}
for d, v in D["rounds"].items():
    if "open" not in v: continue
    bins = set()
    for s in bl.SYMBOLS:
        for b in D["books"][s]:
            if b.date == d and 0 <= b.t < 780:
                bins.add(int(b.t // 2))
    row = {}
    for name, a, b in bl.PHASES:
        nb = sum(1 for i in range(int(a//2), int((b+1)//2)) if i in bins)
        row[name] = (2*nb, b-a)
    cov[d] = row
    print(f"  {d} (s{v['season']} r{v['round']}):", {k: f"{x}/{y}" for k, (x, y) in row.items()})
res["coverage"] = cov
print("== frames outside [0,780) of a round (pauses) ==")
for s in bl.SYMBOLS[:1]:
    pass
outside = {}
for s in bl.SYMBOLS:
    for b in D["books"][s]:
        if not (0 <= b.t < 780):
            outside.setdefault(b.date, []).append(b.t)
for d, ts in outside.items():
    print(f"  {d}: {len(ts)} book frames with t in [{min(ts):.1f}, {max(ts):.1f}]")
print("== server timestamp vs received_at (orderbook frames): received_at - server_ts (ms) ==")
lat = []
for s in bl.SYMBOLS:
    for b in D["books"][s]:
        lat.append(b.ra - bl.iso_to_unix_local(b.server_ts))
lat.sort()
q = lambda p: lat[int(p*(len(lat)-1))]*1000
print(f"  n={len(lat)} p1 {q(.01):.1f} p50 {q(.5):.1f} p90 {q(.9):.1f} p99 {q(.99):.1f} max {lat[-1]*1000:.1f} ms")
tl = []
for s in bl.SYMBOLS:
    for tr in D["trades"][s]:
        tl.append(tr.ra - bl.iso_to_unix_local(tr.server_ts))
tl.sort()
q = lambda p: tl[int(p*(len(tl)-1))]*1000
print(f"  trades: n={len(tl)} p1 {q(.01):.1f} p50 {q(.5):.1f} p99 {q(.99):.1f} max {tl[-1]*1000:.1f} ms (large values = replayed last trade on (re)subscribe)")
json.dump(res, open(out, "w"), indent=1, default=str)
