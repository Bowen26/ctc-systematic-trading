"""Exploration: server->client latency (received_at - server timestamp) per connection and before each drop;
frame/byte rates before each drop vs baseline.

    python3 -I explore3.py <recording.jsonl>
"""
import json
import os
import statistics as stx
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import feedlib as FL  # noqa: E402

path = sys.argv[1]
raw = open(path).read().splitlines()
frames = [json.loads(l) for l in raw]
sizes = [len(l) for l in raw]
conns = FL.connections(frames)
lat = []
for i, f in enumerate(frames):
    if f["type"] in ("orderbook", "quote", "heartbeat", "subscription_ack"):
        lat.append((i, f["received_at"], f["received_at"] - FL.iso_to_unix(f["timestamp"])))
for c in conns:
    xs = [l for (i, w, l) in lat if c["i0"] <= i < c["i1"]]
    xs.sort()
    print("conn", c["k"], "n", len(xs), "lat min %.3f p50 %.3f p95 %.3f p99 %.3f max %.3f" % (
        xs[0], xs[len(xs) // 2], xs[int(.95 * len(xs))], xs[int(.99 * len(xs))], xs[-1]))
    end = c["last"]
    for win in (10, 5, 2):
        ys = [l for (i, w, l) in lat if c["i0"] <= i < c["i1"] and end - win <= w <= end]
        if ys:
            print("    last %ds: n %d lat p50 %.3f max %.3f" % (win, len(ys), stx.median(ys), max(ys)))
    # frames and bytes in last 10 s
    idx = [i for i in range(c["i0"], c["i1"]) if frames[i]["received_at"] >= end - 10]
    nb = sum(sizes[i] for i in idx)
    # baseline: all 10 s windows in this connection
    print("    last 10 s: %d frames, %d bytes" % (len(idx), nb))
    print("    last 12 frames:")
    for i in range(max(c["i0"], c["i1"] - 12), c["i1"]):
        f = frames[i]
        m = f.get("message") or {}
        print("      %s %-9s %-6s %-9s seq=%s size=%d srv=%s" % (FL.hms(f["received_at"]), f["type"], f.get("symbol") or "",
              m.get("type") or "", m.get("seq"), sizes[i], f.get("timestamp", "")[11:23]))
