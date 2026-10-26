"""Task 2: testnet vs research feed, message by message (seasons 224 and 225).  Official advisories in detail.

    python3 -I advisory_diff.py <recording.jsonl> <research history dir> <out.json>
"""
from __future__ import annotations

import collections
import json
import math
import os
import re
import statistics as stx
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import feedlib as FL  # noqa: E402

REC, HIST, OUT = sys.argv[1:4]
frames = FL.load_frames(REC)
research = {s: FL.research_feed(HIST, s) for s in (224, 225)}
rby = {s: {m["seq"]: m for m in f} for s, f in research.items()}
manifest = json.load(open(os.path.join(HIST, "manifest.json")))
truth = {s["season"]: {st["round"]: st for st in s["storms"]} for s in manifest["seasons"] if s["season"] in (224, 225)}
out = {}

# ------------------------------------------------------------------ 1. exact match by type
print("== 1. exact match of every recorded seq'd news message against research feed.jsonl (by seq)")
live = []
for f in frames:
    if f["type"] == "news" and "seq" in f["message"]:
        m = f["message"]
        s = FL.season_of_msg(m)
        if s in rby:
            live.append((s, m))
tab = collections.defaultdict(lambda: [0, 0])
diffkeys = collections.defaultdict(collections.Counter)
for s, m in live:
    r = rby[s][m["seq"]]
    k = m["type"] + ("/" + m["source"] if m["type"] == "advisory" else "")
    same = (m == r)
    tab[k][0 if same else 1] += 1
    if not same:
        for key in sorted(set(m) | set(r)):
            if m.get(key) != r.get(key):
                diffkeys[k][key] += 1
for k, (a, b) in sorted(tab.items()):
    print(f"  {k:20s} identical {a:4d}  different {b:3d}  differing fields: {dict(diffkeys[k])}")
out["exact"] = {k: {"identical": a, "different": b, "fields": dict(diffkeys[k])} for k, (a, b) in tab.items()}

# ------------------------------------------------------------------ 2. official advisories: paired list
pairs = []  # (season, round, number, testnet, research, origin)
for s, m in live:
    if m["type"] == "advisory" and m["source"] == "official":
        pairs.append((s, m["round"], m["number"], m, rby[s][m["seq"]], "live"))
have = {(p[0], p[1], p[2]) for p in pairs}
for f in frames:  # testnet version of advisories lost in a drop, from snapshots
    if f["type"] == "news" and f["message"].get("type") == "snapshot" and f["message"].get("advisory"):
        a = f["message"]["advisory"]
        s = FL.season_of_msg(a)
        rnd = f["message"]["round"]
        if s in rby and (s, rnd, a["number"]) not in have:
            r = next(x for x in research[s] if x["type"] == "advisory" and x["source"] == "official" and x["round"] == rnd
                     and x["number"] == a["number"])
            pairs.append((s, rnd, a["number"], dict(a, round=rnd, type="advisory"), r, "snapshot"))
            have.add((s, rnd, a["number"]))
pairs.sort(key=lambda p: (p[0] != 224, p[0], p[1], p[2]))
print(f"\n== 2. official advisories compared: {len(pairs)} ({sum(1 for p in pairs if p[5] == 'snapshot')} from snapshots)")
meta = collections.Counter()
for s, rnd, n, t, r, o in pairs:
    for k in ("outlook", "outlook_name", "age", "time", "number", "storm", "source"):
        meta[(k, t.get(k) == r.get(k))] += 1
print("  field equality:", {f"{k}={'same' if v else 'diff'}": c for (k, v), c in sorted(meta.items())})
# outlook vs the true pattern
right = collections.Counter()
for s, rnd, n, t, r, o in pairs:
    tp = truth[s][rnd]["pattern"]
    right[("testnet", t["outlook"] == tp)] += 1
    right[("research", r["outlook"] == tp)] += 1
print("  outlook == true pattern:", dict(right))

# ------------------------------------------------------------------ 3. hurricane_conditions
print("\n== 3. hurricane_conditions (testnet - research), per island")
isl = ["cascadia_prime", "coral_key", "azure_isle", "palm_atoll"]
cells = []
for s, rnd, n, t, r, o in pairs:
    for i in isl:
        cells.append((i, t["hurricane_conditions"][i], r["hurricane_conditions"][i], s, rnd, n))
hc = {}
for i in isl + ["all"]:
    d = [a - b for (j, a, b, *_) in cells if j == i or i == "all"]
    hc[i] = {"n": len(d), "mean": stx.fmean(d), "sd": stx.pstdev(d), "max_abs": max(abs(x) for x in d),
             "n_nonzero": sum(1 for x in d if abs(x) > 1e-9)}
    print(f"  {i:15s} n={len(d):3d} mean {hc[i]['mean']:+.4f} sd {hc[i]['sd']:.4f} max|d| {hc[i]['max_abs']:.2f} "
          f"nonzero {hc[i]['n_nonzero']} (t of mean {hc[i]['mean'] / (hc[i]['sd'] / math.sqrt(len(d)) + 1e-12):+.2f})")
out["hc"] = hc
# by research level
bins = [("R=0", lambda p: p == 0), ("0<R<=0.1", lambda p: 0 < p <= 0.1), ("0.1<R<0.9", lambda p: 0.1 < p < 0.9),
        ("0.9<=R<1", lambda p: 0.9 <= p < 1), ("R=1", lambda p: p == 1)]
print("  |d| by research level:")
for lab, fn in bins:
    d = [a - b for (j, a, b, *_) in cells if fn(b)]
    if d:
        print(f"    {lab:10s} n={len(d):3d} rms {math.sqrt(stx.fmean([x * x for x in d])):.4f} max {max(abs(x) for x in d):.2f}")
# is T a function of R?  same research value -> different testnet values?
grp = collections.defaultdict(set)
for (j, a, b, *_) in cells:
    grp[b].add(a)
multi = {b: sorted(v) for b, v in grp.items() if len(v) > 1}
print(f"  research values that map to more than one testnet value: {len(multi)} of {len(grp)}; e.g.",
      dict(list(sorted(multi.items()))[:4]))
# binomial Monte Carlo model: Var(d) = 2 p (1 - p) / N  (+ rounding 2 * 0.01^2 / 12)
num = den = 0.0
for (j, a, b, *_) in cells:
    p = (a + b) / 2
    if 0 < p < 1:
        num += 2 * p * (1 - p)
        den += (a - b) ** 2 - 2 * 0.01 ** 2 / 12
N_hc = num / den
# standardized residuals under that N
z = [(a - b) / math.sqrt(2 * ((a + b) / 2) * (1 - (a + b) / 2) / N_hc + 2 * 0.01 ** 2 / 12)
     for (j, a, b, *_) in cells if 0 < (a + b) / 2 < 1]
print(f"  binomial-ensemble fit: N = {N_hc:.0f} members; standardized d: sd {stx.pstdev(z):.2f}, max |z| {max(abs(x) for x in z):.2f}, "
      f"share |z|>2 {sum(1 for x in z if abs(x) > 2) / len(z):.3f} (normal 0.046)")
# both endpoints: if R in {0,1} what is T?
ends = collections.Counter((b, a) for (j, a, b, *_) in cells if b in (0.0, 1.0))
print("  research 0/1 -> testnet value counts:", dict(sorted(ends.items())))
# correlation of d across islands within an advisory (sum of probabilities is not constrained)
out["N_hc"] = N_hc

# ------------------------------------------------------------------ 4. cone
print("\n== 4. cone points (testnet - research) by lead")
storm_pos = {}
for s in (224, 225):
    for m in research[s]:
        if m["type"] == "storm":
            storm_pos[(s, m["round"], m["age"])] = (m["x"], m["y"])
cone = collections.defaultdict(list)
for s, rnd, n, t, r, o in pairs:
    assert len(t["cone"]) == len(r["cone"])
    for k, (ct, cr) in enumerate(zip(t["cone"], r["cone"])):
        assert ct["age"] == cr["age"]
        lead = ct["age"] - t["age"]
        truep = storm_pos.get((s, rnd, ct["age"]))
        cone[lead].append({"storm": f"s{s}r{rnd}", "num": n, "dx": ct["x"] - cr["x"], "dy": ct["y"] - cr["y"], "st": ct["spread"], "sr": cr["spread"],
                           "et": math.hypot(ct["x"] - truep[0], ct["y"] - truep[1]) if truep else None,
                           "er": math.hypot(cr["x"] - truep[0], cr["y"] - truep[1]) if truep else None,
                           "age": ct["age"]})
conetab = {}
nn_pos_num = nn_pos_den = 0.0
lr_all = []
for lead in sorted(cone):
    v = cone[lead]
    dd = [math.hypot(x["dx"], x["dy"]) for x in v]
    lr = [math.log(x["st"] / x["sr"]) for x in v]
    lr_all += lr
    sbar = [((x["st"] + x["sr"]) / 2) for x in v]
    # spread = radial RMS of the ensemble -> mean of N members has radial var spread^2/N; difference of two: 2 s^2/N
    Npos = sum(2 * s_ ** 2 for s_ in sbar) / sum(x["dx"] ** 2 + x["dy"] ** 2 for x in v)
    vl = stx.pvariance(lr)
    Nsp = 1 + 1 / vl if vl > 0 else float("inf")
    et = [x["et"] for x in v if x["et"] is not None]
    er = [x["er"] for x in v if x["er"] is not None]
    conetab[lead] = {"n": len(v), "pos_diff_mean": stx.fmean(dd), "pos_diff_sd": stx.pstdev(dd), "pos_diff_max": max(dd),
                     "dx_mean": stx.fmean([x["dx"] for x in v]), "dy_mean": stx.fmean([x["dy"] for x in v]),
                     "spread_research_mean": stx.fmean([x["sr"] for x in v]), "spread_testnet_mean": stx.fmean([x["st"] for x in v]),
                     "log_spread_ratio_mean": stx.fmean(lr), "log_spread_ratio_sd": math.sqrt(vl),
                     "spread_absdiff_mean": stx.fmean([abs(x["st"] - x["sr"]) for x in v]),
                     "N_from_position": Npos, "N_from_spread": Nsp,
                     "err_vs_truth_testnet_median": stx.median(et) if et else None,
                     "err_vs_truth_research_median": stx.median(er) if er else None,
                     "n_truth": len(et)}
    c = conetab[lead]
    sdx = stx.pstdev([x["dx"] for x in v]); sdy = stx.pstdev([x["dy"] for x in v])
    c["t_dx"] = c["dx_mean"] / (sdx / math.sqrt(len(v))); c["t_dy"] = c["dy_mean"] / (sdy / math.sqrt(len(v)))
    c["t_logratio"] = c["log_spread_ratio_mean"] / (c["log_spread_ratio_sd"] / math.sqrt(len(v)))
    print(f"  lead {lead:3d}: t(mean dx) {c['t_dx']:+.2f}, t(mean dy) {c['t_dy']:+.2f}, t(mean log spread ratio) {c['t_logratio']:+.2f}; "
          f"mean |spread diff| {c['spread_absdiff_mean']:.1f}")
    print(f"  lead {lead:3d} s n={len(v):2d}: |dpos| mean {c['pos_diff_mean']:5.1f} sd {c['pos_diff_sd']:5.1f} max {c['pos_diff_max']:5.1f} "
          f"(mean dx {c['dx_mean']:+.1f}, dy {c['dy_mean']:+.1f}); spread R {c['spread_research_mean']:.1f} T {c['spread_testnet_mean']:.1f}, "
          f"log ratio mean {c['log_spread_ratio_mean']:+.3f} sd {c['log_spread_ratio_sd']:.3f}; N(pos) {Npos:.0f}, N(spread) {Nsp:.0f}; "
          f"median error vs realised track T {c['err_vs_truth_testnet_median'] and round(c['err_vs_truth_testnet_median'], 1)} "
          f"R {c['err_vs_truth_research_median'] and round(c['err_vs_truth_research_median'], 1)} (n={len(et)})")
out["cone"] = conetab
for lead in (120, 240):
    v = cone[lead]
    dx = sorted(x["dx"] for x in v)
    neg = sum(1 for x in dx if x < 0)
    trim = dx[1:-1]
    print(f"  lead {lead}: dx median {stx.median(dx):+.1f}, negative {neg} of {len(dx)}; mean without min/max {stx.fmean(trim):+.1f} "
          f"(t {stx.fmean(trim) / (stx.pstdev(trim) / math.sqrt(len(trim))):+.2f}); dx values {[round(x, 1) for x in dx]}")
print("  lead 240 dx by storm:", {k: [round(x["dx"], 1) for x in cone[240] if x["storm"] == k] for k in sorted({x["storm"] for x in cone[240]})})
# signed error vs the realised track (x only) at long leads, both feeds
for lead in (120, 240):
    ext = []
    for s_, rnd_, n_, t_, r_, o_ in pairs:
        for ct, cr in zip(t_["cone"], r_["cone"]):
            if ct["age"] - t_["age"] == lead and (s_, rnd_, ct["age"]) in storm_pos:
                xt = storm_pos[(s_, rnd_, ct["age"])][0]
                ext.append((ct["x"] - xt, cr["x"] - xt))
    print(f"  lead {lead}: mean signed x error vs realised track: testnet {stx.fmean([a for a, b in ext]):+.1f}, research {stx.fmean([b for a, b in ext]):+.1f} (n={len(ext)})")
# pooled error-vs-truth comparison: share of cone points where testnet is closer to the realised position
closer = [x["et"] < x["er"] for v in cone.values() for x in v if x["et"] is not None]
print(f"  testnet cone point closer to the realised storm than research: {sum(closer)} of {len(closer)}")
ratio = [x["et"] / x["er"] for v in cone.values() for x in v if x["et"] is not None and x["er"] > 0]
print(f"  error ratio testnet/research: median {stx.median(ratio):.3f}; mean log ratio {stx.fmean([math.log(r_) for r_ in ratio]):+.3f}")

# ------------------------------------------------------------------ 5. headline
print("\n== 5. headlines")
NAMES = {"cascadia_prime": "Cascadia Prime", "coral_key": "Coral Key", "azure_isle": "Azure Isle", "palm_atoll": "Palm Atoll"}


def headline_from(m):
    items = sorted(((v, NAMES[k]) for k, v in m["hurricane_conditions"].items()), key=lambda kv: -kv[0])
    parts = [f"{nm} {round(v * 100)}%" for v, nm in items if round(v * 100) >= 5]
    return f"Advisory {m['number']}: steering outlook {m['outlook_name']}. Hurricane conditions: " + (", ".join(parts) + "." if parts else "no island above 5%.")


hs = collections.Counter()
for s, rnd, n, t, r, o in pairs:
    hs[("headline same" if t["headline"] == r["headline"] else "headline differs")] += 1
    hs[("testnet headline == template(testnet hc)", headline_from(t) == t["headline"])] += 1
    hs[("research headline == template(research hc)", headline_from(r) == r["headline"])] += 1
    strip = lambda h: re.sub(r"\d+%", "P%", h)
    hs[("same after masking percentages", strip(t["headline"]) == strip(r["headline"]))] += 1
print("  ", dict(hs))
for s, rnd, n, t, r, o in pairs:
    if headline_from(t) != t["headline"] or headline_from(r) != r["headline"]:
        print("    template mismatch:", t["headline"], "|", headline_from(t), "||", r["headline"], "|", headline_from(r))
        break

# ------------------------------------------------------------------ 6. the advisory lost in drop 1 (224 #5)
print("\n== 6. per-advisory listing (|d| max of hc; cone |dpos| by lead)")
rows = []
for s, rnd, n, t, r, o in pairs:
    dh = max(abs(t["hurricane_conditions"][i] - r["hurricane_conditions"][i]) for i in isl)
    dp = [round(math.hypot(a["x"] - b["x"], a["y"] - b["y"]), 1) for a, b in zip(t["cone"], r["cone"])]
    rows.append((s, rnd, n, o, t["outlook"], r["outlook"], dh, dp))
    print(f"  s{s} r{rnd} #{n} ({o}): outlook T {t['outlook']} R {r['outlook']}; max|d hc| {dh:.2f}; |dpos| {dp}")
out["per_advisory"] = rows
json.dump(out, open(OUT, "w"), indent=1, default=str)
print("\nwrote", OUT)
