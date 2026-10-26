"""Survey the recording: frame counts, cadence of book frames, timestamp vs received_at.
usage: python3 -I survey.py recording.jsonl"""
import sys, json, collections, datetime as dt
path = sys.argv[1]
cnt = collections.Counter(); per_sym = collections.Counter()
last = {}; gaps = collections.defaultdict(list)
lag = []
news_types = collections.Counter()
acks = []
first_rx = None; last_rx = None
with open(path) as f:
    for line in f:
        fr = json.loads(line); t = fr["type"]; cnt[t] += 1
        rx = fr["received_at"]; first_rx = first_rx or rx; last_rx = rx
        if t == "subscription_ack": acks.append((fr["timestamp"], rx))
        if t in ("orderbook", "quote", "trade"):
            per_sym[(t, fr["symbol"])] += 1
            if t == "orderbook":
                s = fr["symbol"]
                if s in last: gaps[s].append(rx - last[s])
                last[s] = rx
            ts = dt.datetime.fromisoformat(fr["timestamp"]).timestamp()
            lag.append((t, rx - ts))
        if t == "news":
            m = fr["message"]; news_types[m.get("type")] += 1
print("frames", sum(cnt.values()), dict(cnt))
print("news types", dict(news_types))
print("acks", acks)
print("span", first_rx, last_rx, (last_rx - first_rx))
for s in sorted(last):
    g = sorted(gaps[s]); n = len(g)
    print(s, "books", per_sym[("orderbook", s)], "quotes", per_sym[("quote", s)], "trades", per_sym[("trade", s)],
          "gap p10/p50/p90/max %.2f %.2f %.2f %.1f" % (g[n//10], g[n//2], g[9*n//10], g[-1]))
for t in ("orderbook", "quote", "trade"):
    l = sorted(x for k, x in lag if k == t); n = len(l)
    print(t, "rx - timestamp p1/p50/p99 %.3f %.3f %.3f" % (l[n//100], l[n//2], l[99*n//100]))
