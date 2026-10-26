"""Fit the round-open wall time t0 per round: offset = received_at - (market seconds since 09:30)/30.
usage: python3 -I clock_fit.py recording.jsonl season224_dir season225_dir"""
import sys, os, datetime as dt, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec
R = rec.Recording(sys.argv[1], sys.argv[2:])
def q(xs, p): xs = sorted(xs); return xs[min(len(xs) - 1, int(p * len(xs)))]
for key in sorted(R.rounds, key=lambda k: (k[0], k[1])):
    ri = R.rounds[key]
    if not ri.offsets: continue
    offs = [o[1] for o in ri.offsets]
    xs = [o[0] for o in ri.offsets]
    n = len(offs); mx = st.mean(xs); my = st.mean(offs)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, offs)) / sxx if sxx else 0
    t0 = ri.t0
    res = [y - t0 for y in offs]
    print(f"season {key[0]} round {key[1]} ({ri.date}): n={n} t0={dt.datetime.fromtimestamp(t0).strftime('%H:%M:%S.%f')[:-3]}"
          f" real-s span {min(xs):.0f}-{max(xs):.0f}; residual ms p1 {1000*q(res,.01):.0f} p5 {1000*q(res,.05):.0f} p50 {1000*q(res,.5):.0f}"
          f" p95 {1000*q(res,.95):.0f} p99 {1000*q(res,.99):.0f} min {1000*min(res):.0f} max {1000*max(res):.0f} sd {1000*st.pstdev(res):.0f};"
          f" drift {1000*slope:.3f} ms per real s")
    bytype = {}
    for (x, y, t) in ri.offsets: bytype.setdefault(t, []).append(y - t0)
    print("    by type:", {t: (len(v), round(1000 * st.median(v))) for t, v in bytype.items()})
    # 'late' outliers
    late = [(x, round(1000*(y - t0)), t) for (x, y, t) in ri.offsets if y - t0 > 0.3]
    if late: print("    >300 ms late:", late[:12], "..." if len(late) > 12 else "")
# snapshot market time vs fitted clock
print("snapshot time minus fitted clock (real s):")
for rx, m in R.news:
    if m.get("type") != "snapshot": continue
    key = R.date2round.get(m["time"][:10])
    if key is None or R.rounds[key].t0 is None: continue
    real = rec.mkt_seconds(m["time"]) / 30
    if real > 790: continue
    print(f"  {key} phase {m['phase']:9s} snap real {real:6.1f}  fitted {rx - R.rounds[key].t0:7.2f}  diff {rx - R.rounds[key].t0 - real:+.2f}")
print("gaps (disconnects):", [(dt.datetime.fromtimestamp(a).strftime('%H:%M:%S.%f')[:-3], round(b - a, 2)) for a, b in R.gaps])
for key, ri in sorted(R.rounds.items()):
    if ri.t0 is None: continue
    print(key, "gaps in round real s:", [(round(a - ri.t0, 1), round(b - ri.t0, 1)) for a, b in R.gaps if -60 < a - ri.t0 < 800])
print("linear fit offset = a + b*real_s (storm obs):")
for key, ri in sorted(R.rounds.items()):
    if ri.t0 is None: continue
    res = [y - (ri.a + ri.b * x) for x, y, t in ri.offsets]
    print(f"  {key}: a={dt.datetime.fromtimestamp(ri.a).strftime('%H:%M:%S.%f')[:-3]} b={ri.b*1000:.3f} ms/s;"
          f" residual sd {1000*st.pstdev(res):.0f} ms, p5 {1000*q(res,.05):.0f} p95 {1000*q(res,.95):.0f}, max |res| {1000*max(abs(r) for r in res):.0f} ms")
