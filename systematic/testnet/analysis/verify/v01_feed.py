"""(a) Clock fit, disconnects, lost messages and advisory differences.
usage: python3 -I v01_feed.py <recording> <history_dir>
"""
import sys, os, json, statistics as st, math
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from vlib import *

rec, hist = sys.argv[1], sys.argv[2]
F = load_rec(rec)
print("frames", len(F))
types = {}
for fr in F:
    types[fr["type"]] = types.get(fr["type"], 0) + 1
print("frame types", types)

# ---- clock
op = round_opens(F)
print("\n== round opens from storm frames (median of received_at - real_s) ==")
for k in sorted(op, key=lambda k: op[k]["open"]):
    o = op[k]
    print("%s open %s.%03d n=%d sd=%.3f min=%+.3f max=%+.3f slope=%.6f lin_sd=%.3f" % (
        k, datetime.fromtimestamp(o["open"]).strftime("%H:%M:%S"), int((o["open"] % 1) * 1000),
        o["n"], o["sd"], o["mn"], o["mx"], o["slope"], o["lin_sd"]))
# residuals of other news types vs the storm median fit
print("\n-- median residual of other news types vs storm-median open (s) --")
res = {}
for w, m in news_msgs(F):
    if "time" not in m or m.get("type") in ("snapshot", None):
        continue
    key = DATE_ROUND.get(m["time"][:10])
    if key not in op:
        continue
    r = w - op[key]["open"] - real_of(m["time"])
    res.setdefault(m["type"], []).append(r)
for t, v in sorted(res.items()):
    print("%-16s n=%4d median %+.3f  p5 %+.3f p95 %+.3f" % (t, len(v), st.median(v), pct(v, .05), pct(v, .95)))
ks = sorted(op, key=lambda k: op[k]["open"])
for a, b in zip(ks, ks[1:]):
    print("open spacing %s -> %s : %.2f s" % (a, b, op[b]["open"] - op[a]["open"]))

# ---- connections and drops
segs = connections(F)
print("\n== connections ==")
for i, (s, e) in enumerate(segs):
    nxt = segs[i + 1][0] if i + 1 < len(segs) else None
    # locate in round
    loc = ""
    for k in ks:
        o = op[k]["open"]
        if o <= e < o + 780:
            loc = "%s real %.1f" % (k, e - o)
    print("conn %d start %s end %s life %.1f s gap_to_next %s  last frame at %s" % (
        i + 1, datetime.fromtimestamp(s).strftime("%H:%M:%S.%f")[:-3],
        datetime.fromtimestamp(e).strftime("%H:%M:%S.%f")[:-3], e - s,
        "%.2f" % (nxt - e) if nxt else "-", loc))

# ---- losses vs research
print("\n== lost / different messages vs research feed ==")
rec_by = {}
for w, m in news_msgs(F):
    if "seq" not in m:
        continue
    key = DATE_ROUND.get(m["time"][:10])
    if key is None:
        continue
    rec_by.setdefault(key[0], {})
    rec_by[key[0]].setdefault(m["seq"], (w, m))
for season in (224, 225):
    feed = load_feed(hist, season)
    want = {m["seq"]: m for m in feed}
    got = rec_by[season]
    first = min(got)
    missing = sorted(s for s in want if s >= first and s not in got)
    diff = sorted(s for s in got if s in want and got[s][1] != want[s])
    tm = {}
    for s in missing:
        t = want[s]["type"] + ("/" + want[s].get("source", "") if want[s]["type"] == "advisory" else "")
        tm[t] = tm.get(t, 0) + 1
    td = {}
    for s in diff:
        t = want[s]["type"] + ("/" + want[s].get("source", "") if want[s]["type"] == "advisory" else "")
        td[t] = td.get(t, 0) + 1
    print("season %d: first recorded seq %d, missing %d %s, different %d %s" % (season, first, len(missing), tm, len(diff), td))
    print("  missing seqs:", missing)
    for s in missing:
        m = want[s]
        if m["type"] != "storm":
            print("   lost seq %d %s %s %s age %s" % (s, m["type"], m.get("source", ""), m.get("number", ""), m.get("age")))
    # group missing by drop: real time of each missing message
    for s in missing:
        m = want[s]
        key = (season, m["round"])
    # which fields differ
    fields = {}
    for s in diff:
        a, b = got[s][1], want[s]
        for f in set(a) | set(b):
            if a.get(f) != b.get(f):
                fields[f] = fields.get(f, 0) + 1
    print("  fields that differ (count of messages):", fields)

    # advisory characterisation
    if season == 225 or season == 224:
        dhc, outl, num = [], 0, 0
        cone_d = {}
        for s in diff:
            a, b = got[s][1], want[s]
            if b["type"] != "advisory":
                continue
            num += 1
            outl += a["outlook"] == b["outlook"] and a.get("number") == b.get("number") and a["age"] == b["age"] and a["time"] == b["time"]
            for isl in b["hurricane_conditions"]:
                dhc.append((a["hurricane_conditions"][isl] - b["hurricane_conditions"][isl], b["hurricane_conditions"][isl]))
            for ca, cb in zip(a["cone"], b["cone"]):
                lead = cb["age"] - b["age"]
                cone_d.setdefault(lead, []).append((math.hypot(ca["x"] - cb["x"], ca["y"] - cb["y"]), ca["spread"], cb["spread"]))
        if num:
            d = [x for x, _ in dhc]
            nz = sum(1 for x in d if abs(x) > 1e-9)
            print("  advisories differing: %d; outlook+number+age+time equal in %d" % (num, outl))
            print("  hurricane_conditions diff: n=%d mean %+.4f sd %.4f max|d| %.3f nonzero %d; at research=0: %d nonzero of %d; at research=1: %d nonzero of %d" % (
                len(d), st.mean(d), st.pstdev(d), max(abs(x) for x in d), nz,
                sum(1 for x, r in dhc if r == 0 and abs(x) > 1e-9), sum(1 for x, r in dhc if r == 0),
                sum(1 for x, r in dhc if r == 1 and abs(x) > 1e-9), sum(1 for x, r in dhc if r == 1)))
            # binomial check: z = d / sqrt(p(1-p)/N) for 0<p<1; fit N
            mids = [(x, r) for x, r in dhc if 0 < r < 1]
            for N in (25, 47, 50, 100):
                z = [x / math.sqrt(max(r * (1 - r), 1e-9) * 2 / N) for x, r in mids]  # diff of two draws: var 2p(1-p)/N
                print("    N=%d: sd of z (diff of two independent N-member estimates) = %.2f, n=%d" % (N, st.pstdev(z), len(z)))
            for lead in sorted(cone_d):
                v = cone_d[lead]
                print("    cone lead %3d: n=%d mean pos diff %.1f max %.1f mean spread testnet %.1f research %.1f" % (
                    lead, len(v), st.mean(x for x, _, _ in v), max(x for x, _, _ in v),
                    st.mean(a for _, a, _ in v), st.mean(b for _, _, b in v)))
    # also: all official advisories received: how many identical
    offi = [s for s in got if s in want and want[s]["type"] == "advisory" and want[s]["source"] == "official"]
    print("  official advisories received %d, identical %d" % (len(offi), sum(1 for s in offi if got[s][1] == want[s])))

# ---- map lost messages to drops: wall time they would have arrived
print("\n== lost messages vs connection gaps (expected arrival = open + real_s) ==")
for season in (224, 225):
    feed = load_feed(hist, season)
    want = {m["seq"]: m for m in feed}
    got = rec_by[season]
    first = min(got)
    missing = sorted(s for s in want if s >= first and s not in got)
    for s in missing:
        m = want[s]
        key = (season, m["round"])
        exp = op[key]["open"] + real_of(m["time"])
        gap = None
        for i in range(len(segs) - 1):
            if segs[i][1] - 0.5 <= exp <= segs[i + 1][0] + 0.5:
                gap = i + 1
        print("  s%d seq %d %-9s r%d real %.1f -> inside gap after conn %s" % (season, s, m["type"], m["round"], real_of(m["time"]), gap))

# ---- snapshots: recovery of lost advisory (s224 #5)
print("\n== snapshots ==")
snaps = [(w, m) for w, m in news_msgs(F) if m.get("type") == "snapshot"]
gaps = [snaps[i + 1][0] - snaps[i][0] for i in range(len(snaps) - 1)]
print("snapshots %d, median spacing %.2f s" % (len(snaps), st.median(gaps)))
adv_keys = set()
for w, m in snaps:
    if m.get("advisory"):
        adv_keys |= set(m["advisory"].keys())
print("snapshot.advisory keys:", sorted(adv_keys))
print("snapshot top-level keys:", sorted(set().union(*[set(m.keys()) for _, m in snaps])))
