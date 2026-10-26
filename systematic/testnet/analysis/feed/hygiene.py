"""Task 4: data-hygiene facts about the testnet stream for the bot.

    python3 -I hygiene.py <recording.jsonl> <research history dir> <out.json>
"""
from __future__ import annotations

import collections
import json
import os
import re
import statistics as stx
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import feedlib as FL  # noqa: E402

REC, HIST, OUT = sys.argv[1:4]
raw = open(REC).read().splitlines()
frames = [json.loads(l) for l in raw]
sizes = [len(l) for l in raw]
conns = FL.connections(frames)
conn_of = [0] * len(frames)
for c in conns:
    for i in range(c["i0"], c["i1"]):
        conn_of[i] = c["k"]
opens = FL.all_opens(frames)
clock = FL.Clock(opens)
print("round opens used:", {d: FL.hms(v) for d, v in sorted(opens.items(), key=lambda kv: kv[1])})
out = {}

# ------------------------------------------------------------------ 1. duplicates and ordering of news
print("== 1. news duplicates and seq order")
seen = collections.Counter()
order_viol = []
prev_seq = {}
for i, f in enumerate(frames):
    if f["type"] != "news":
        continue
    m = f["message"]
    if "seq" not in m:
        continue
    key = (FL.season_of_msg(m), m["seq"])
    seen[key] += 1
    k = conn_of[i]
    p = prev_seq.get(k)
    if p is not None and p[0] == key[0] and m["seq"] <= p[1]:
        order_viol.append((k, p, key))
    prev_seq[k] = key
dups = {k: v for k, v in seen.items() if v > 1}
print(f"  news with seq: {sum(seen.values())} frames, {len(seen)} distinct (season, seq); duplicates {len(dups)}; "
      f"non-increasing seq within a connection: {len(order_viol)}")
# seq restarts at 0 each season
starts = [(FL.season_of_msg(f["message"]), f["message"]["seq"], FL.hms(f["received_at"])) for f in frames
          if f["type"] == "news" and f["message"].get("seq") == 0]
print("  seq 0 frames (season restarts):", starts)
# messages per tick and their seq order by type
ticks = collections.defaultdict(list)
for i, f in enumerate(frames):
    if f["type"] == "news" and "seq" in f["message"]:
        m = f["message"]
        ticks[(FL.season_of_msg(m), m["time"])].append((m["seq"], m["type"], m.get("source"), f["received_at"]))
multi = collections.Counter()
for k, v in ticks.items():
    if len(v) > 1:
        multi[" < ".join(t + ("/" + s if s else "") for _, t, s, _ in sorted(v))] += 1
print("  same-market-time groups (seq order):", dict(multi))
same_frame = sum(1 for v in ticks.values() if len(v) > 1 and max(x[3] for x in v) - min(x[3] for x in v) < 0.002)
print(f"  groups arriving within 2 ms of each other (one read): {same_frame} of {sum(1 for v in ticks.values() if len(v) > 1)}")
out["news"] = {"duplicates": len(dups), "order_violations": len(order_viol), "tick_groups": dict(multi)}

# ------------------------------------------------------------------ 2. snapshots
print("\n== 2. snapshots")
snaps = [(i, f["received_at"], f["message"]) for i, f in enumerate(frames)
         if f["type"] == "news" and f["message"].get("type") == "snapshot"]
maxseq = {}
cmp = collections.Counter()
for i, w, m in snaps:
    # max seq received so far on this connection for this season
    s = FL.season_of_msg(m)
    best = None
    for j in range(i - 1, conns[conn_of[i] - 1]["i0"] - 1, -1):
        g = frames[j]
        if g["type"] == "news" and "seq" in g["message"] and FL.season_of_msg(g["message"]) == s:
            best = g["message"]["seq"]
            break
    ls = m.get("last_seq")
    if best is None:
        cmp["no prior news on this connection"] += 1
    elif ls == best:
        cmp["last_seq == max seq received before it"] += 1
    elif ls > best:
        cmp["last_seq > max received (gap revealed)"] += 1
    else:
        cmp["last_seq < max received"] += 1
print("  last_seq vs news already received on the same connection:", dict(cmp))
print("  snapshot has no 'seq'; keys:", sorted(snaps[0][2].keys()))
times = [(m["time"], m.get("phase"), m.get("last_seq")) for _, _, m in snaps]
print("  snapshot 'time' during the between-round pause runs ahead (clock not usable):",
      [t for t, p, _ in times if p == "closed" and not t.endswith("16:00:00")])
print("  snapshots with last_seq = -1 (new season, nothing published yet):", sum(1 for _, _, l in times if l == -1))
snap_sizes = [sizes[i] for i, _, _ in snaps]
print(f"  snapshot frame size: median {stx.median(snap_sizes)} B, max {max(snap_sizes)} B")
# obs inside a snapshot vs the live storm message of the same age
live = {}
for f in frames:
    if f["type"] == "news" and f["message"].get("type") == "storm":
        m = f["message"]
        live[(m["time"][:10], m["age"])] = m
eq = ne = 0
for _, _, m in snaps:
    o = m.get("obs")
    if not o:
        continue
    lv = live.get((o["time"][:10], o["age"]))
    if lv is None:
        continue
    if all(lv[k] == o[k] for k in ("x", "y", "vx", "vy", "radius", "intensity")):
        eq += 1
    else:
        ne += 1
print(f"  snapshot.obs equals the live storm message of the same age: {eq}, differs: {ne}")
out["snapshot"] = {"last_seq_cmp": dict(cmp), "obs_eq": eq, "obs_ne": ne, "size_max": max(snap_sizes)}

# ------------------------------------------------------------------ 3. timestamp formats
print("\n== 3. timestamp formats")
fmts = collections.Counter()
for f in frames:
    if "timestamp" in f:
        ts = f["timestamp"]
        fmts[(f["type"], "timestamp", re.sub(r"\d", "9", ts))] += 1
    if f["type"] == "news":
        m = f["message"]
        fmts[("news:" + m.get("type", "?"), "time", re.sub(r"\d", "9", m.get("time", "")))] += 1
for k, v in sorted(fmts.items()):
    print(f"  {k[0]:22s} {k[1]:9s} {k[2]}  x{v}")
# news market time seconds: which second values occur
secs = collections.Counter(f["message"]["time"][17:19] for f in frames if f["type"] == "news" and "time" in f["message"])
print("  market-time seconds field values in news:", dict(secs))
# trade timestamps older than the frame (reconnect replay of the last trade)
old = [(f["received_at"] - FL.iso_to_unix(f["timestamp"])) for f in frames if f["type"] == "trade"]
print(f"  trade frames: received_at - trade timestamp: median {stx.median(old) * 1000:.0f} ms, max {max(old):.1f} s; "
      f"> 1 s: {sum(1 for x in old if x > 1)}")
# trades replayed on reconnect: duplicate trade_ids
tid = collections.Counter(f["trade_id"] for f in frames if f["type"] == "trade")
dup_t = [t for t, n in tid.items() if n > 1]
print(f"  trade_id duplicates: {len(dup_t)} (of {len(tid)} distinct)")
# where are they?
first_after_ack = collections.Counter()
for c in conns:
    syms = set()
    for i in range(c["i0"] + 1, min(c["i1"], c["i0"] + 40)):
        f = frames[i]
        if f["type"] == "trade" and tid[f["trade_id"]] > 1:
            first_after_ack[c["k"]] += 1
print("  duplicate trade_ids among the first 40 frames after each ack:", dict(first_after_ack))
out["trade_dup_ids"] = len(dup_t)

# ------------------------------------------------------------------ 4. frame rates by phase
print("\n== 4. frame rates by phase (frames per connected second)")
phase_time = collections.Counter()
phase_frames = collections.defaultdict(collections.Counter)
phase_bytes = collections.Counter()
for c in conns:
    t = c["start"]
    while t < c["last"]:
        d, s, ph = clock.locate(t)
        phase_time[ph] += min(0.1, c["last"] - t)
        t += 0.1
for i, f in enumerate(frames):
    d, s, ph = clock.locate(f["received_at"])
    t = f["type"] if f["type"] != "news" else "news:" + f["message"].get("type", "?")
    phase_frames[ph][t] += 1
    phase_bytes[ph] += sizes[i]
types = sorted({t for v in phase_frames.values() for t in v})
rates = {}
for ph in ("pre-storm", "storm", "aftermath", "pause"):
    T = phase_time[ph]
    rates[ph] = {t: phase_frames[ph][t] / T for t in types if phase_frames[ph][t]}
    tot = sum(phase_frames[ph].values()) / T
    print(f"  {ph:10s} connected {T:7.1f} s: total {tot:6.2f} frames/s, {phase_bytes[ph] / T / 1000:6.1f} kB/s; " +
          ", ".join(f"{t} {r:.3f}" for t, r in sorted(rates[ph].items(), key=lambda kv: -kv[1])))
out["rates"] = rates
# per-symbol book update rate during the storm
sym_storm = collections.Counter()
for f in frames:
    if f["type"] == "orderbook":
        d, s, ph = clock.locate(f["received_at"])
        if ph == "storm":
            sym_storm[f["symbol"]] += 1
print("  orderbook frames per second by symbol (storm):",
      {k: round(v / phase_time["storm"], 2) for k, v in sym_storm.most_common()})

# ------------------------------------------------------------------ 5. bursts
print("\n== 5. bursts")
ws = [f["received_at"] for f in frames]
for win in (0.1, 1.0, 2.0):
    best = (0, 0)
    j = 0
    for i in range(len(ws)):
        while ws[i] - ws[j] > win:
            j += 1
        if i - j + 1 > best[0]:
            best = (i - j + 1, i)
    d, s, ph = clock.locate(ws[best[1]])
    print(f"  max frames in {win:.1f} s: {best[0]} at {FL.hms(ws[best[1]])} ({d} s={s:.1f} {ph})")
# identical received_at batches (one TCP read)
bl, cur, start = [], 1, 0
for i in range(1, len(ws)):
    if ws[i] - ws[i - 1] < 0.002:
        cur += 1
    else:
        bl.append((cur, start)); cur, start = 1, i
bl.append((cur, start))
bs = sorted(b for b, _ in bl)
print(f"  frames per read batch (consecutive frames < 2 ms apart): median {stx.median(bs)}, p99 {bs[int(.99 * len(bs))]}, max {bs[-1]}")
bmax = max(bl)
print(f"  largest batch at {FL.hms(ws[bmax[1]])}: {bmax[0]} frames ->",
      dict(collections.Counter(f["type"] for f in frames[bmax[1]:bmax[1] + bmax[0]])))
# frames per 1 s around scheduled events, max over rounds (s relative to the round open)
for lab, lo, hi in (("quiet pre-storm 20-50", 20, 50), ("formation 60-80", 60, 80), ("mid-storm 200-400", 200, 400),
                    ("prelim 570-590", 570, 590), ("final 740-760", 740, 760)):
    best = 0
    for d, v in opens.items():
        sel = [w for w in ws if lo <= w - v < hi]
        j = 0
        for i in range(len(sel)):
            while sel[i] - sel[j] > 1.0:
                j += 1
            best = max(best, i - j + 1)
    print(f"  max frames in any 1 s, {lab} s: {best}")
# news bursts: max news frames within 1 s
nws = [f["received_at"] for f in frames if f["type"] == "news"]
best, j = 0, 0
for i in range(len(nws)):
    while nws[i] - nws[j] > 1.0:
        j += 1
    best = max(best, i - j + 1)
print(f"  max news frames within 1 s: {best}")
# max inter-frame gap while connected and the market open (pre-storm/storm/aftermath)
gaps_open = []
gaps_ph = collections.defaultdict(list)
for c in conns:
    for i in range(c["i0"] + 1, c["i1"]):
        d, s, ph = clock.locate(frames[i]["received_at"])
        g_ = frames[i]["received_at"] - frames[i - 1]["received_at"]
        gaps_ph[ph].append(g_)
        if ph in ("pre-storm", "storm", "aftermath"):
            gaps_open.append((g_, i))
for ph, v in gaps_ph.items():
    v.sort()
    print(f"  inter-frame gap in {ph}: p99 {v[int(.99 * len(v))]:.2f} s, max {v[-1]:.2f} s")
gaps_open.sort()
g, gi = gaps_open[-1]
print(f"  inter-frame gap while connected and the market open: p99 {gaps_open[int(.99 * len(gaps_open))][0]:.2f} s, "
      f"p99.9 {gaps_open[int(.999 * len(gaps_open))][0]:.2f} s, max {g:.2f} s at {FL.hms(frames[gi]['received_at'])}")
# storm obs inter-arrival during storms
st = [(f["message"]["time"][:10], f["message"]["age"], f["received_at"]) for f in frames
      if f["type"] == "news" and f["message"].get("type") == "storm"]
ia = [b[2] - a[2] for a, b in zip(st, st[1:]) if a[0] == b[0] and b[1] - a[1] == 2]
ia.sort()
print(f"  storm-obs inter-arrival (consecutive ages): median {stx.median(ia):.3f} s, min {ia[0]:.3f}, max {ia[-1]:.3f}")
# max gap per symbol book updates in the open market
last = {}
symgap = collections.Counter()
for i, f in enumerate(frames):
    if f["type"] != "orderbook":
        continue
    d, s, ph = clock.locate(f["received_at"])
    if ph not in ("pre-storm", "storm", "aftermath") or not (f["bids"] and f["asks"]):
        last.pop(f["symbol"], None)
        continue
    k = f["symbol"]
    if k in last:
        symgap[f["symbol"]] = max(symgap[f["symbol"]], f["received_at"] - last[k])
    last[k] = f["received_at"]
print("  max gap between book updates per symbol while open (s):", {k: round(v, 1) for k, v in sorted(symgap.items())})

# ------------------------------------------------------------------ 6. book / quote consistency
print("\n== 6. order book and quote consistency")
lastbook = {}
qmatch = qmis = crossed = locked = 0
nlev = collections.Counter()
dec = collections.Counter()
unsorted = 0
empty_q = collections.Counter()
empty_b = collections.Counter()
samets = {(f["symbol"], f["timestamp"]): f for f in frames if f["type"] == "orderbook"}
mis_examples = []
for f in frames:
    if f["type"] == "orderbook":
        b, a = f["bids"], f["asks"]
        nlev[(len(b), len(a))] += 1
        if not b or not a:
            d_, s_, ph_ = clock.locate(f["received_at"])
            empty_b[(len(b), len(a), ph_, round(s_))] += 1
        if b and a:
            if b[0]["price"] > a[0]["price"]:
                crossed += 1
            elif b[0]["price"] == a[0]["price"]:
                locked += 1
        if any(x["price"] < y["price"] for x, y in zip(b, b[1:])) or any(x["price"] > y["price"] for x, y in zip(a, a[1:])):
            unsorted += 1
        for lv in b + a:
            p = repr(lv["price"])
            dec[len(p.split(".")[1]) if "." in p else 0] += 1
        lastbook[f["symbol"]] = f
    elif f["type"] == "quote":
        bk = samets.get((f["symbol"], f["timestamp"])) or lastbook.get(f["symbol"])
        if bk is None or not bk["bids"] or not bk["asks"]:
            empty_q[(f["bid"], f["ask"])] += 1
            continue
        ok = (bk["bids"][0]["price"] == f["bid"] and bk["asks"][0]["price"] == f["ask"] and
              bk["bids"][0]["quantity"] == f["bid_size"] and bk["asks"][0]["quantity"] == f["ask_size"])
        qmatch += ok
        qmis += not ok
        if not ok and len(mis_examples) < 3:
            mis_examples.append((f, bk))
print(f"  book levels (bids, asks): {dict(nlev)}; crossed {crossed}, locked {locked}, mis-sorted {unsorted}")
print(f"  price decimals: {dict(sorted(dec.items()))}")
print(f"  quote == top of the latest book of that symbol: {qmatch}, differs {qmis}; quotes after an empty book: {dict(empty_q)}")
print("  empty/one-sided books (bids, asks, phase, s):", dict(empty_b))
for q, bk in mis_examples:
    print("    mismatch example: quote", {k: q[k] for k in ("symbol", "bid", "ask", "bid_size", "ask_size", "timestamp")},
          "book top", bk["bids"][:2], bk["asks"][:2], bk["timestamp"])
# classify mismatches: same prices but different sizes, or different prices
cls = collections.Counter()
for f in frames:
    if f["type"] != "quote":
        continue
    bk = samets.get((f["symbol"], f["timestamp"]))
    if bk is None:
        cls["no book with the same timestamp"] += 1
        continue
    if not bk["bids"] or not bk["asks"]:
        cls["empty book"] += 1
        continue
    pe = bk["bids"][0]["price"] == f["bid"] and bk["asks"][0]["price"] == f["ask"]
    se = bk["bids"][0]["quantity"] == f["bid_size"] and bk["asks"][0]["quantity"] == f["ask_size"]
    cls[("prices equal" if pe else "prices differ") + ", " + ("sizes equal" if se else "sizes differ")] += 1
print("  quote vs the book with the same server timestamp:", dict(cls))
# quote-only updates: how far is the latest orderbook behind, and does the next book agree?
lastb, pend, lagq = {}, {}, []
agree = disagree = 0
for f in frames:
    if f["type"] == "orderbook":
        if f["symbol"] in pend and f["bids"] and f["asks"]:
            q = pend.pop(f["symbol"])
            if f["bids"][0]["price"] == q["bid"] and f["asks"][0]["price"] == q["ask"]:
                agree += 1
            else:
                disagree += 1
            lagq.append(FL.iso_to_unix(f["timestamp"]) - FL.iso_to_unix(q["timestamp"]))
        lastb[f["symbol"]] = f
    elif f["type"] == "quote" and (f["symbol"], f["timestamp"]) not in samets and f["bid"] is not None and f["ask"] is not None:
        pend[f["symbol"]] = f
lagq.sort()
print(f"  quote-only updates (no book frame with that timestamp): next book of the symbol has the same top {agree}, "
      f"a different top {disagree}; next book arrives after median {stx.median(lagq):.2f} s, p90 {lagq[int(.9 * len(lagq))]:.2f} s")
# typical pattern: trade -> orderbook -> quote with the same server timestamp
pat = collections.Counter()
for i in range(len(frames) - 2):
    f = frames[i]
    if f["type"] == "trade":
        pat[(frames[i + 1]["type"], frames[i + 2]["type"])] += 1
print("  frames following a trade:", dict(pat.most_common(4)))
# quote frames without a preceding book for the same timestamp
out["book"] = {"crossed": crossed, "locked": locked, "quote_match": qmatch, "quote_mismatch": qmis}

# field types
tys = collections.defaultdict(collections.Counter)
for f in frames:
    if f["type"] == "news":
        m = f["message"]
        for k in ("seq", "round", "age", "number", "last_seq"):
            if k in m:
                tys[(m["type"], k)][type(m[k]).__name__] += 1
print("  news field types:", {f"{a}.{b}": dict(c) for (a, b), c in sorted(tys.items())})

# ------------------------------------------------------------------ 7. frame sizes
print("\n== 7. frame sizes (bytes of the recorded JSON line, incl. received_at)")
bysz = collections.defaultdict(list)
for i, f in enumerate(frames):
    t = f["type"] if f["type"] != "news" else "news:" + f["message"].get("type", "?")
    bysz[t].append(sizes[i])
for t, v in sorted(bysz.items()):
    print(f"  {t:22s} n={len(v):6d} median {stx.median(v):6.0f} max {max(v):6d}")

json.dump(out, open(OUT, "w"), indent=1, default=str)
print("\nwrote", OUT)
