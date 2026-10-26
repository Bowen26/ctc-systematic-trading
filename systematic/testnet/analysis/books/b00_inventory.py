"""Inventory of the testnet recording: frame types, symbols, time ranges, news kinds.
usage: python3 -I b00_inventory.py recording.jsonl"""
import json, sys, collections, datetime
path = sys.argv[1]
types = collections.Counter(); sym = collections.Counter(); kinds = collections.Counter()
first = {}; last = {}
acks = []
news_dates = collections.Counter()
keys = collections.defaultdict(set)
n = 0
prev_ra = None; maxgap = []
with open(path) as f:
    for line in f:
        n += 1
        fr = json.loads(line)
        t = fr.get("type"); types[t] += 1
        keys[t] |= set(fr.keys())
        ra = fr["received_at"]
        if prev_ra is not None and ra - prev_ra > 3: maxgap.append((prev_ra, ra, ra-prev_ra))
        prev_ra = ra
        first.setdefault(t, ra); last[t] = ra
        if t == "subscription_ack": acks.append((fr["timestamp"], ra))
        if "symbol" in fr: sym[(t, fr["symbol"])] += 1
        if t == "news":
            m = fr["message"]; kinds[m.get("type")] += 1
            if "time" in m: news_dates[m["time"][:10]] += 1
            keys["news.message"] |= set(m.keys())
print("lines", n)
for t, c in types.items():
    print(t, c, datetime.datetime.fromtimestamp(first[t]).isoformat(), datetime.datetime.fromtimestamp(last[t]).isoformat())
print("keys", {k: sorted(v) for k, v in keys.items()})
print("acks", acks)
print("news kinds", dict(kinds))
print("news dates", dict(news_dates))
for t in ("orderbook", "quote", "trade"):
    print(t, {s: c for (tt, s), c in sorted(sym.items()) if tt == t})
print("gaps >3s in received_at:")
for a, b, g in maxgap: print("  ", datetime.datetime.fromtimestamp(a).isoformat(), datetime.datetime.fromtimestamp(b).isoformat(), round(g, 2))

# book frame structure: level counts, crossed books, per-symbol inter-arrival within a connection
lv = collections.Counter(); crossed = 0; nb = 0
ia = collections.defaultdict(list); last = {}
seg = -1
with open(path) as f:
    for line in f:
        fr = json.loads(line)
        if fr["type"] == "subscription_ack":
            seg += 1; last = {}
        if fr["type"] != "orderbook":
            continue
        nb += 1
        b, a = fr["bids"], fr["asks"]
        lv[(len(b), len(a))] += 1
        if b and a and b[0]["price"] >= a[0]["price"]:
            crossed += 1
        s = fr["symbol"]
        if s in last:
            ia[s].append(fr["received_at"] - last[s])
        last[s] = fr["received_at"]
print("book frames", nb, "crossed/locked", crossed, "level counts (bids,asks) top:", lv.most_common(6),
      "share with 5x5:", round(lv[(5, 5)] / nb, 3), "share with any side > 5:", round(sum(v for k, v in lv.items() if max(k) > 5) / nb, 3))
for s in sorted(ia):
    x = sorted(ia[s]); q = lambda p: x[int(p * (len(x) - 1))]
    print(f"  {s:6s} book inter-arrival s: p10 {q(.1):.2f} p50 {q(.5):.2f} p90 {q(.9):.2f} p99 {q(.99):.1f}")
