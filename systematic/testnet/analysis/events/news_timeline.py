"""Print the news/ack/heartbeat timeline of the recording (non-storm messages, plus first/last storm per round).
usage: python3 -I news_timeline.py recording.jsonl"""
import sys, json, datetime as dt
prev = None
with open(sys.argv[1]) as f:
    for line in f:
        fr = json.loads(line); t = fr["type"]; rx = fr["received_at"]
        loc = dt.datetime.fromtimestamp(rx).strftime("%H:%M:%S.%f")[:-3]
        if t == "subscription_ack":
            print(loc, "ACK"); continue
        if t == "heartbeat":
            continue
        if t != "news": continue
        m = fr["message"]; mt = m.get("type")
        if mt == "snapshot":
            keys = list(m.keys())
            print(loc, "snapshot keys", keys[:12], {k: m[k] for k in keys if k in ("time","round","status","phase","season","storm")}); continue
        if mt == "storm":
            key = (m.get("round"), m.get("storm"))
            if prev != key: print(loc, "first storm", m["time"], m["seq"], key)
            prev = key; last_storm = (loc, m["time"], m["seq"]); continue
        if mt in ("track",): continue
        if mt == "advisory": continue
        print(loc, mt, m.get("time"), m.get("seq"), m.get("round"), m.get("stage", ""), m.get("storm", ""))
