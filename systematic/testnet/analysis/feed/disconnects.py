"""Task 1: disconnects of the testnet recording - market-clock position, what was lost, snapshot recovery,
drop pattern, and a two-connection simulation.

    python3 -I disconnects.py <recording.jsonl> <research history dir> <out.json>

Reads only; writes <out.json> (and prints a report).
"""
from __future__ import annotations

import json
import math
import os
import random
import statistics as stx
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import feedlib as FL  # noqa: E402

REC, HIST, OUT = sys.argv[1:4]
raw = open(REC).read().splitlines()
frames = [json.loads(l) for l in raw]
sizes = [len(l) for l in raw]
conns = FL.connections(frames)
out = {}

# ------------------------------------------------------------------ 0. market clock per round
print("== 0. market clock: round opens fitted from storm frames (open = received_at - real s since 09:30)")
fits = {}
for d in sorted(set(m["time"][:10] for _, _, m in FL.news_frames(frames) if m.get("type") == "storm")):
    pts = [(FL.mkt_seconds(m["time"]), w) for _, w, m in FL.news_frames(frames)
           if m.get("type") == "storm" and m["time"][:10] == d]
    offs = sorted(w - s for s, w in pts)
    med = stx.median(offs)
    # least-squares slope of wall vs market-implied real seconds (clock rate check; 1.0 = exactly 30x)
    xs = [s for s, _ in pts]; ys = [w for _, w in pts]
    mx, my = stx.fmean(xs), stx.fmean(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    res = [o - med for o in offs]
    lin = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    lin_sd = stx.pstdev(lin)
    lin_rng = (min(lin), max(lin))
    print(f"  {d}: residual after a linear (rate-free) fit: sd {lin_sd:.3f} s, min {lin_rng[0]:+.3f}, max {lin_rng[1]:+.3f}")
    fits[d] = {"lin_resid_sd": lin_sd, "lin_resid_min": lin_rng[0], "lin_resid_max": lin_rng[1]}
    fits[d] = {**fits[d],"season_round": FL.SEASON_DATES[d], "n_storm_frames": len(pts), "open_wall_median": med,
               "open_hms": FL.hms(med), "open_min_minus_median": offs[0] - med,
               "resid_sd": stx.pstdev(res), "resid_p05": res[int(0.05 * (len(res) - 1))],
               "resid_p95": res[int(0.95 * (len(res) - 1))], "resid_max": res[-1], "slope": slope,
               "span_s": max(xs) - min(xs)}
    f = fits[d]
    print(f"  {d} {f['season_round']}: open {f['open_hms']} (n={len(pts)}), residual sd {f['resid_sd']:.3f} s, "
          f"min {f['open_min_minus_median']:+.3f}, p95 {f['resid_p95']:+.3f}, max {f['resid_max']:+.3f}; "
          f"slope {slope:.6f} over {f['span_s']:.0f} s")
# per-type residuals against the storm fit
types = {}
for _, w, m in FL.news_frames(frames):
    t = m.get("type")
    d = (m.get("time") or "")[:10]
    if t == "snapshot" or d not in fits or "seq" not in m:
        continue
    types.setdefault(t, []).append(w - FL.mkt_seconds(m["time"]) - fits[d]["open_wall_median"])
print("  residual by message type (s, relative to the storm-frame fit): ")
out["clock_resid_by_type"] = {}
for t, xs in sorted(types.items()):
    xs.sort()
    out["clock_resid_by_type"][t] = {"n": len(xs), "median": stx.median(xs), "max": xs[-1], "min": xs[0]}
    print(f"    {t:16s} n={len(xs):4d} median {stx.median(xs):+.3f} min {xs[0]:+.3f} max {xs[-1]:+.3f}")
opens = sorted((v["open_wall_median"], d) for d, v in fits.items())
print("  gaps between round opens (s):", [round(b[0] - a[0], 2) for a, b in zip(opens, opens[1:])],
      "-> pause after close =", [round(b[0] - a[0] - FL.ROUND_LEN, 2) for a, b in zip(opens, opens[1:])])
out["clock"] = fits
clock = FL.Clock({d: v["open_wall_median"] for d, v in fits.items()})
# season-226 round-1 open is not fit from storms (recording ends pre-storm): from snapshots' 09:30->09:45 switch
snaps = [(w, m) for _, w, m in FL.news_frames(frames) if m.get("type") == "snapshot"]
s226 = [(w, m["time"]) for w, m in snaps if m["time"].startswith("1801-05-11")]
print("  season 226 snapshots:", [(FL.hms(w), t[11:]) for w, t in s226])


def wall_of(date, t):
    return fits[date]["open_wall_median"] + FL.mkt_seconds(t)


# ------------------------------------------------------------------ 1. connection table
print("\n== 1. connections and drops")
BACKOFF = [1, 2, 4, 8, 16, 30, 30]  # record_all.py: 1 s after a drop, doubling after each failed attempt
rows = []
for c in conns:
    sub = range(c["i0"], c["i1"])
    nb = sum(sizes[i] for i in sub)
    row = {"conn": c["k"], "ack": FL.hms(c["start"]), "last_frame": FL.hms(c["last"]),
           "lifetime_s": c["last"] - c["start"], "frames": c["i1"] - c["i0"], "bytes": nb}
    if c["next_ack"] is not None:
        gap = c["next_ack"] - c["last"]
        # infer number of failed reconnect attempts from the backoff ladder
        nfail, cum = 0, BACKOFF[0]
        while cum + BACKOFF[nfail + 1] <= gap and nfail < 5:
            nfail += 1
            cum += BACKOFF[nfail]
        row.update({"gap_to_next_ack_s": gap, "inferred_failed_attempts": nfail, "sum_backoff_s": cum,
                    "drop_window": [FL.hms(c["last"]), FL.hms(c["next_ack"] - cum)],
                    "drop_window_len_s": max(0.0, c["next_ack"] - cum - c["last"])})
        d, s, ph = clock.locate(c["last"])
        d2, s2, _ = clock.locate(c["next_ack"])
        row.update({"date": d, "season_round": FL.SEASON_DATES.get(d), "s_at_drop": s, "mkt_at_drop": FL.mkt_label(s),
                    "phase": ph, "s_at_reconnect": s2 if d2 == d else None})
    rows.append(row)
    print("  " + json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}))
out["connections"] = rows

# ------------------------------------------------------------------ 2. what was lost
print("\n== 2. lost news per disconnect (research seq between the last seq before and the first seq after)")
research = {224: FL.research_feed(HIST, 224), 225: FL.research_feed(HIST, 225)}
rby = {s: {m["seq"]: m for m in feed} for s, feed in research.items()}
got = {224: set(), 225: set()}
for _, _, m in FL.news_frames(frames):
    s = FL.season_of_msg(m)
    if s in got and "seq" in m:
        got[s].add(m["seq"])
losses = []
for c in conns[:-1]:
    before = [m for i in range(c["i0"], c["i1"]) if frames[i]["type"] == "news"
              for m in [frames[i]["message"]] if "seq" in m]
    nxt = conns[c["k"]]
    after = [m for i in range(nxt["i0"], nxt["i1"]) if frames[i]["type"] == "news"
             for m in [frames[i]["message"]] if "seq" in m]
    lb, fa = before[-1], after[0]
    sb, sa = FL.season_of_msg(lb), FL.season_of_msg(fa)
    lost = []
    if sb == sa:
        lost = [rby[sb][q] for q in range(lb["seq"] + 1, fa["seq"]) if q not in got[sb]]
    else:  # season boundary inside the gap
        lost = [rby[sb][q] for q in range(lb["seq"] + 1, max(rby[sb]) + 1) if q not in got[sb]]
        lost += [rby[sa][q] for q in range(0, fa["seq"]) if q not in got[sa]]
    kinds = {}
    for m in lost:
        k = m["type"] + ("/" + m["source"] if m["type"] == "advisory" else "")
        kinds[k] = kinds.get(k, 0) + 1
    ent = {"drop": c["k"], "season": sb, "last_seq_before": lb["seq"], "first_seq_after": fa["seq"],
           "lost_seqs": [m["seq"] for m in lost], "lost_types": kinds,
           "lost_mkt_times": [lost[0]["time"], lost[-1]["time"]] if lost else None,
           "lost_detail": [(m["seq"], m["type"], m.get("source"), m["time"][11:], m.get("age"), m.get("number")) for m in lost
                           if m["type"] != "storm"]}
    losses.append(ent)
    print(f"  drop {c['k']}: season {sb}, last seq {lb['seq']} -> first seq {fa['seq']}; lost {len(lost)}: {kinds}")
    for x in ent["lost_detail"]:
        print("      ", x)
out["losses"] = losses
n225 = sum(len(e["lost_seqs"]) for e in losses if e["season"] == 225)
print(f"  total lost season 225: {n225}; season 224: {sum(len(e['lost_seqs']) for e in losses if e['season'] == 224)}")

# ------------------------------------------------------------------ 3. snapshot recovery
print("\n== 3. snapshots: cadence, fields, and recovery of the lost messages")
sw = [w for w, _ in snaps]
dts = [b - a for a, b in zip(sw, sw[1:])]
reg = [x for x in dts if x < 45]
print(f"  {len(snaps)} snapshots; interval median {stx.median(reg):.3f} s, mean {stx.fmean(reg):.3f}, "
      f"min {min(reg):.2f}, max (excluding drops) {max(reg):.2f}; intervals > 45 s: {[round(x, 1) for x in dts if x >= 45]}")
keys = sorted(set().union(*[set(m) for _, m in snaps]))
print("  snapshot fields:", keys)
print("  phase values:", sorted(set(m.get("phase") for _, m in snaps)))
print("  headlines per snapshot:", sorted(set(len(m.get("headlines") or []) for _, m in snaps)))
# track thinning
spac = {}
for _, m in snaps:
    tr = m.get("track") or []
    if len(tr) > 2:
        ages = [p[0] for p in tr]
        dd = sorted(set(b - a for a, b in zip(ages[:-1], ages[1:-1])))
        spac.setdefault(tuple(dd), []).append(ages[-1])
print("  snapshot track spacing (s of storm age) -> latest ages seen:",
      {str(k): (min(v), max(v)) for k, v in spac.items()})
# global cadence: phase of the snapshot within its 30-s grid relative to the round clock
ph = [(clock.locate(w)[1]) % 30.0 for w in sw]
print("  snapshot phase within 30 s of round clock: first", round(ph[0], 2), "last", round(ph[-1], 2),
      "(drifts; snapshots are on a server timer, not per connection)")
# first snapshot after each reconnect and recovery of each lost message
OUTLOOK_TEXT = {"turn erratic": "erratic", "stall over": "stall", "recurve": "recurve", "hop along": "chain",
                "direct run": "direct", "eyewall": "eyewall"}
recov_rows = []
for e, c in zip(losses, conns[:-1]):
    nxt = conns[c["k"]]
    first = [(w, m) for w, m in snaps if w >= nxt["start"]]
    if not e["lost_seqs"]:
        print(f"  drop {e['drop']}: nothing lost")
        continue
    s = e["season"]
    lost = [rby[s][q] for q in e["lost_seqs"]]
    lost_round = lost[0]["round"]
    rsn = [(w, m) for w, m in first if m.get("round") == lost_round and FL.season_of_msg(m) == s]
    w0, m0 = rsn[0]
    reveals_gap = m0.get("last_seq", -1) > e["last_seq_before"]
    print(f"  drop {e['drop']}: first snapshot {w0 - nxt['start']:.1f} s after reconnect "
          f"({w0 - (c['last']):.1f} s after the drop), last_seq {m0['last_seq']} (gap visible: {reveals_gap}), "
          f"advisory #{(m0.get('advisory') or {}).get('number')}, {len(m0.get('track') or [])} track pts, "
          f"{len(m0.get('headlines') or [])} headlines {[h['seq'] for h in m0.get('headlines') or []]}")
    hl_seqs = {h["seq"]: h for h in m0.get("headlines") or []}
    track_ages = {p[0] for p in m0.get("track") or []}
    cnt = {}
    for m in lost:
        t = m["type"]
        if t == "storm":
            if (m0.get("obs") or {}).get("age") == m["age"]:
                st = "full (snapshot.obs)"
            elif m["age"] in track_ages:
                st = "position+radius only (snapshot.track; no intensity/velocity)"
            else:
                st = "interpolated only (between snapshot.track points)"
        elif t == "track":
            st = "superseded (snapshot.track, thinned)"
        elif t == "advisory" and m["source"] == "official":
            adv = m0.get("advisory") or {}
            if adv.get("number") == m["number"]:
                st = "full (snapshot.advisory, testnet version)"
            elif m["seq"] in hl_seqs:
                st = "headline text only (outlook + rounded hurricane conditions, no cone)"
            else:
                st = "lost"
        elif t == "advisory":
            h = hl_seqs.get(m["seq"])
            if h is not None:
                guess = next((v for k, v in OUTLOOK_TEXT.items() if k in h["headline"]), None)
                st = f"headline text only; outlook parsable from text -> {guess} (true {m['outlook']})"
            else:
                st = "lost"
        elif t in ("damage_report",):
            st = "full (snapshot.damage)" if any(d.get("stage") == m["stage"] and d.get("round") == m["round"]
                                                 for d in m0.get("damage") or []) else "lost"
        elif t == "impact":
            st = "full (snapshot.impacts)" if any(d.get("island") == m["island"] for d in m0.get("impacts") or []) else "lost"
        else:
            st = "headline only" if m["seq"] in hl_seqs else "lost"
        cnt[st] = cnt.get(st, 0) + 1
        recov_rows.append({"drop": e["drop"], "seq": m["seq"], "type": t, "source": m.get("source"), "recovery": st})
    for k, v in cnt.items():
        print(f"      {v:2d} x {k}")
    # does the snapshot advisory equal the live testnet advisory of the same number (consistency)?
out["recovery"] = recov_rows
# consistency of snapshot.advisory with the live testnet advisory of the same number
live_off = {}
for _, w, m in FL.news_frames(frames):
    if m.get("type") == "advisory" and m.get("source") == "official":
        live_off[(m["time"][:10], m["number"])] = m
same = diff = 0
for _, m in snaps:
    a = m.get("advisory")
    if not a:
        continue
    lv = live_off.get((a["time"][:10], a["number"]))
    if lv is None:
        continue
    keyf = ("hurricane_conditions", "cone", "outlook", "headline")
    if all(lv[k] == a[k] for k in keyf):
        same += 1
    else:
        diff += 1
print(f"  snapshot.advisory vs live testnet advisory of the same number: identical {same}, different {diff}")
out["snapshot_adv_consistency"] = {"identical": same, "different": diff}

# ------------------------------------------------------------------ 4. drop pattern
print("\n== 4. drop pattern")
lifetimes = [r["lifetime_s"] for r in rows[:-1]]
connected = sum(r["lifetime_s"] for r in rows)
mtbf = connected / len(lifetimes)
cv = stx.pstdev(lifetimes) / stx.fmean(lifetimes)
print(f"  lifetimes (s) of dropped connections: {[round(x, 1) for x in lifetimes]}; censored last: {rows[-1]['lifetime_s']:.1f}")
print(f"  connected time {connected:.1f} s, 5 drops -> MTBF {mtbf:.0f} s; CV of lifetimes {cv:.2f} (exponential = 1, fixed lifetime = 0)")
# Poisson chance of >=1 drop during one 780 s round and during a 3-round season
for T in (480, 780, 3 * 780):
    print(f"    P(>=1 drop in {T} s) = {1 - math.exp(-T / mtbf):.2f}; expected drops {T / mtbf:.2f}")
# frame/byte rate in the 10 s before each drop vs all 10 s windows of that connection while the market is open
for c in conns[:-1]:
    end = c["last"]
    ws = []
    i = c["i0"]
    t = frames[c["i0"]]["received_at"] + 5
    while t + 10 <= end:
        lo, hi = t, t + 10
        n = sum(1 for j in range(c["i0"], c["i1"]) if lo <= frames[j]["received_at"] < hi)
        ws.append(n)
        t += 10
    last = sum(1 for j in range(c["i0"], c["i1"]) if end - 10 <= frames[j]["received_at"] <= end)
    pct = sum(1 for x in ws if x <= last) / max(1, len(ws))
    # latency (received_at - server timestamp) in the last 10 s vs the whole connection
    lat_all = [frames[j]["received_at"] - FL.iso_to_unix(frames[j]["timestamp"]) for j in range(c["i0"], c["i1"])
               if frames[j]["type"] in ("orderbook", "quote")]
    lat_end = [frames[j]["received_at"] - FL.iso_to_unix(frames[j]["timestamp"]) for j in range(c["i0"], c["i1"])
               if frames[j]["type"] in ("orderbook", "quote") and frames[j]["received_at"] >= end - 10]
    lastnews = [frames[j]["message"] for j in range(c["i0"], c["i1"]) if frames[j]["type"] == "news"][-1]
    lastnews_w = [frames[j]["received_at"] for j in range(c["i0"], c["i1"]) if frames[j]["type"] == "news"][-1]
    d, s, phs = clock.locate(end)
    print(f"  drop {c['k']}: last-10-s frames {last} (percentile {pct:.2f} of {len(ws)} windows, median {stx.median(ws)}); "
          f"latency p50 last 10 s {stx.median(lat_end) * 1000:.0f} ms vs conn {stx.median(lat_all) * 1000:.0f} ms, max last 10 s "
          f"{max(lat_end) * 1000:.0f} ms; last news {lastnews.get('type')}/{lastnews.get('source', '')} {end - lastnews_w:.2f} s before; "
          f"s mod 2 at last frame {s % 2:.2f}; age of conn mod 20 (client ping) {(end - c['start']) % 20:.1f}, mod 30 (heartbeat) "
          f"{(end - c['start']) % 30:.1f}")
out["mtbf_s"] = mtbf
out["lifetime_cv"] = cv
# drops per phase vs connected time per phase (Poisson expectation under a constant hazard)
opens_all = FL.all_opens(frames)
clk2 = FL.Clock(opens_all)
ptime = {}
for c in conns:
    t = c["start"]
    while t < c["last"]:
        ph_ = clk2.locate(t)[2]
        ptime[ph_] = ptime.get(ph_, 0.0) + min(0.1, c["last"] - t)
        t += 0.1
pdrop = {}
for r in rows[:-1]:
    ph_ = clk2.locate(conns[r["conn"] - 1]["last"])[2]
    pdrop[ph_] = pdrop.get(ph_, 0) + 1
tot_t = sum(ptime.values())
print("  drops by phase (observed / expected under a constant hazard, connected s):",
      {ph_: (pdrop.get(ph_, 0), round(5 * v / tot_t, 2), round(v)) for ph_, v in ptime.items()})
quiet = sum(v for ph_, v in ptime.items() if ph_ in ("aftermath", "pause"))
lam_q = 5 * quiet / tot_t
print(f"  P(0 drops in aftermath + pause | constant hazard) = exp(-{lam_q:.2f}) = {math.exp(-lam_q):.3f}")
out["drops_by_phase"] = {"drops": pdrop, "connected_s": ptime}
# proximity of each storm-phase drop to a tick that published a track (age 0, 20, 40, ... -> s = 60 + 20k) or an
# official advisory (age 30, 90, ... -> s = 90 + 60k).  Drop window = [last frame, next ack - backoff].
ev = sorted([60 + 20 * k for k in range(25)] + [90 + 60 * k for k in range(8)])
print("  drop window relative to the nearest track/official-advisory tick (s):")
prox = []
for r in rows[:-1]:
    if r["phase"] != "storm":
        continue
    lo = r["s_at_drop"]
    hi = lo + r["drop_window_len_s"]
    near = min(ev, key=lambda e: min(abs(lo - e), abs(hi - e)) if not (lo <= e <= hi) else 0)
    kind = "advisory" if (near - 90) % 60 == 0 and near >= 90 and (near - 60) % 20 != 0 else "track"
    prox.append((lo - near, hi - near))
    print(f"    conn {r['conn']}: [{lo:.2f}, {hi:.2f}] -> {kind} tick at s={near}: [{lo - near:+.2f}, {hi - near:+.2f}]")
wmin = min(p[0] for p in prox)
wmax = max(p[1] for p in prox)
cover = sum(1 for t10 in range(600, 5400) if any(wmin <= t10 / 10 - e <= wmax for e in ev)) / 4800
print(f"    all {len(prox)} storm drops fall in [{wmin:+.2f}, {wmax:+.2f}] s around such a tick; that band covers {cover:.3f} of storm time,"
      f" so P(all {len(prox)} by chance) = {cover ** len(prox):.1e} (post hoc; suggestive only)")
out["tick_proximity"] = {"band": [wmin, wmax], "coverage": cover, "p_chance": cover ** len(prox)}

# ------------------------------------------------------------------ 5. two-connection simulation
print("\n== 5. robust feed simulation: a second connection B with independent drops")
# publish wall time of each lost season-225 message (and 224) from the fitted round clock
lost_t = []
for e in losses:
    for q in e["lost_seqs"]:
        m = rby[e["season"]][q]
        lost_t.append((e["season"], q, wall_of(m["time"][:10], m["time"]) + 0.02))
span0, span1 = frames[0]["received_at"], frames[-1]["received_at"]
gaps = [r["gap_to_next_ack_s"] for r in rows[:-1]]
print("  observed outages (last frame -> next ack, s):", [round(g, 2) for g in gaps])
# improved reconnect policy: retry after 0.25 s, then every 0.5 s (no exponential backoff); the quick outages shrink by
# (1 s backoff - 0.25 s); the long one is bounded by the 4 failed attempts: endpoint unreachable for 15-31 s
quick = [g - 1.0 + 0.25 for g in gaps if g < 5]


def outage(policy, rng):
    if policy == "recorded":
        return rng.choice(gaps)
    k = rng.randrange(len(gaps))
    if gaps[k] < 5:
        return gaps[k] - 1.0 + 0.25
    return rng.uniform(15.0, 31.0) + 0.25


def sim_B(policy, rng, t0, t1):
    """Alternating renewal: up ~ Exp(MTBF), down ~ outage(policy).  Start in the stationary state."""
    downs = []
    mean_down = stx.fmean([outage(policy, rng) for _ in range(200)])
    t = t0 - rng.uniform(0, mtbf + mean_down)  # random phase
    while t < t1:
        t += rng.expovariate(1.0 / mtbf)
        d = outage(policy, rng)
        downs.append((t, t + d))
        t += d
    return downs


def lost_by(downs, t):
    return any(a <= t <= b for a, b in downs)


rng = random.Random(20261023)
NS = 20000
res = {}
for policy in ("recorded", "fast-retry"):
    tot = []
    for _ in range(NS):
        downs = sim_B(policy, rng, span0, span1)
        n = sum(1 for (s, q, t) in lost_t if s == 225 and lost_by(downs, t))
        tot.append(n)
    tot.sort()
    res[policy] = {"mean_still_lost_of_22": stx.fmean(tot), "p_zero": sum(1 for x in tot if x == 0) / NS,
                   "p99": tot[int(0.99 * NS)], "max": tot[-1]}
    print(f"  B with {policy} reconnect: of the 22 season-225 losses, still lost (both down) mean {res[policy]['mean_still_lost_of_22']:.3f}, "
          f"P(none lost) {res[policy]['p_zero']:.4f}, p99 {res[policy]['p99']}, max {res[policy]['max']}")
# general rate: messages lost per season for 1 vs 2 connections (both with the same process), 866 msgs over 3 x 780 s.
# publish times: research season-225 schedule laid on a continuous timeline of 3 rounds + 2 pauses of 180 s
sched = [((m["round"] - 1) * 960.0 + FL.mkt_seconds(m["time"])) for m in research[225]]
kind = [m["type"] + ("/" + m["source"] if m["type"] == "advisory" else "") for m in research[225]]
crit = [i for i, k in enumerate(kind) if k in ("advisory/official", "damage_report")]
T1 = 2 * 960.0 + 780.0
for policy in ("recorded", "fast-retry"):
    one, two, c1, c2, o1, d1 = [], [], [], [], [], []
    for _ in range(4000):
        A = sim_B(policy, rng, 0, T1)
        B = sim_B(policy, rng, 0, T1)
        la = [lost_by(A, t) for t in sched]
        lb = [lost_by(B, t) for t in sched]
        one.append(sum(la))
        two.append(sum(1 for x, y in zip(la, lb) if x and y))
        c1.append(any(la[i] for i in crit))
        c2.append(any(la[i] and lb[i] for i in crit))
        o1.append(sum(1 for i in crit if la[i] and kind[i] == "advisory/official"))
        d1.append(sum(1 for i in crit if la[i] and kind[i] == "damage_report"))
    print(f"  per season, {policy}: P(lose >= 1 official advisory or damage report) 1 conn {stx.fmean(c1):.3f}, 2 conns {stx.fmean(c2):.4f}; "
          f"1 conn mean lost official advisories {stx.fmean(o1):.3f}, damage reports {stx.fmean(d1):.4f}")
    res[policy + "_per_season"] = {"one_conn_mean": stx.fmean(one), "two_conn_mean": stx.fmean(two),
                                   "one_conn_p_any": sum(1 for x in one if x) / len(one),
                                   "two_conn_p_any": sum(1 for x in two if x) / len(two)}
    r = res[policy + "_per_season"]
    print(f"  per season ({len(sched)} msgs), {policy}: 1 conn loses mean {r['one_conn_mean']:.1f} (P>0 {r['one_conn_p_any']:.2f}); "
          f"2 conns mean {r['two_conn_mean']:.3f} (P>0 {r['two_conn_p_any']:.3f})")
# single connection with fast retry (0.25 s, then every 0.5 s), on the actual drops: reconnect time upper bound =
# last frame + (gap - 1 s backoff) + 0.25 s for quick drops; for the long one the endpoint was unreachable until at
# least drop + 15 s (the 4th attempt failed) and at most drop + 31 s.
saved_lo = saved_hi = 0
for e, r in zip(losses, rows[:-1]):
    if e["season"] != 225 or not e["lost_seqs"]:
        continue
    pub = [FL.mkt_seconds(rby[225][q]["time"]) for q in e["lost_seqs"]]
    if r["inferred_failed_attempts"] == 0:
        t_re = r["s_at_drop"] + (r["gap_to_next_ack_s"] - 1.0) + 0.25
        n = sum(1 for p in pub if p > t_re)
        saved_lo += n; saved_hi += n
        print(f"  fast retry alone, conn {r['conn']}: reconnect by s={t_re:.2f}; saves {n} of {len(pub)}")
    else:
        t_early = r["s_at_drop"] + 15 + 0.25
        t_late = r["s_at_drop"] + r["gap_to_next_ack_s"]
        n_hi = sum(1 for p in pub if p > t_early)
        n_lo = sum(1 for p in pub if p > t_late)
        saved_lo += n_lo; saved_hi += n_hi
        print(f"  fast retry alone, conn {r['conn']}: reconnect between s={t_early:.2f} and {t_late:.2f}; saves {n_lo}-{n_hi} of {len(pub)}")
print(f"  => fast retry on one connection saves {saved_lo}-{saved_hi} of the {n225} season-225 losses")
out["fast_retry_single_saved"] = [saved_lo, saved_hi]
# correlated-outage caveat: if the long outage was server-side, B is down too
long_lost = sum(len(e["lost_seqs"]) for e in losses if e["season"] == 225 and len(e["lost_seqs"]) > 5)
print(f"  if the long outage was server-side (common to both connections), B saves only the {n225 - long_lost} short-drop losses;"
      f" a fast-retry policy would then still lose the messages published while the endpoint was unreachable (>= 15 s)")
out["sim"] = res
json.dump(out, open(OUT, "w"), indent=1, default=str)
print("\nwrote", OUT)
