"""Offline test of the two-connection feed layer (feed.FeedMerger, feed.RobustFeed). Never touches the network.

    python3 -I test_feed_merge.py <testnet_s224_s225.jsonl> [n_seeds]

Part 1, merge. The recording is the server stream. Each run makes two simulated connections A and B from it:
  * independent drops (one every 6 min on average per connection, about twice the 23 Oct rate): every frame in the
    window is lost on that connection; 85% of drops reconnect in 0.3-0.85 s, 15% are 3-35 s outages. After each
    reconnect the server's resend burst is added (subscription_ack, a fresh book and quote per symbol, and the
    symbol's last trade again with the same trade_id, as on 23 Oct);
  * latency 2 ms + exp(8 ms) per frame on each connection, plus 4 stalls per connection that hold frames back
    0.2-1.5 s and then release them in a burst (so the two copies overtake each other).
Scenario "independent": the two connections drop independently. Scenario "half shared": half of B's drops are
copies of A's (server-side drops hit both), so some messages are lost on both.
Both copies are merged in arrival order through one FeedMerger (poll() before every frame). Checks, every run:
  N1  every news (season, seq) that A or B received is forwarded exactly once, with the recorded content;
      missing = only what neither copy had (the recording's own 24 losses plus joint drops)
  N2  the season changes 224 -> 225 -> 226 reset the seen-set (season 225 restarts at seq 0, so without the reset
      its seqs 0-876 would collide with season 224's)
  S1  a snapshot is forwarded only when it covers a seq that was neither forwarded live nor covered by an earlier
      forwarded snapshot, and never twice
  S2  every seq neither copy delivered is covered by a later forwarded snapshot whenever one exists
  T1  every trade_id of A or B forwarded exactly once (the recording itself re-sends 37 after its reconnects)
  B1  books and quotes: per symbol the forwarded server timestamps strictly increase; a copy is dropped only when a
      frame at least as new was already forwarded; the final book per symbol is the newest of A and B
  O1  every silence of the merged stream longer than 10 s that starts inside a storm phase (60-540 s of a round,
      5 s margins) raises feed_outage and then feed_restored with that gap; no alarm outside 55-575 s (the merger
      runs on its own phase estimate here). The recorded 31.7-s outage (225 r2, both copies dark) is one of them,
      unless a simulated reconnect burst or stall release lands inside it and splits it
Part 2, RobustFeed with fake sockets, consumed through its events() iterator and an on_event callback: an
accelerated slice of the recording; connection 0 drops after 1 s, connection 1 stalls after 1.5 s. Checks that the
iterator and the callback see the same events in order, that the iterator ends after stop(), that every event has
its category, raw dict, received_at and (news only) seq, the 0.25-s first reconnect, the 3-s storm watchdog, one subscription per
connection session, that the merged news is exactly what the connections received, and that together they
received every news seq the fake server streamed.
Part 3: the reconnect ladder and the watchdog table.
Part 4: mutations that must be caught: no seen-set reset on a season change; the literal snapshot rule (forward only
if last_seq is above the highest seq forwarded, which never recovers a lost message on one connection because
the live stream up to last_seq always arrives first); no 1-s snapshot hold.
"""
import asyncio
import bisect
import collections
import datetime as dt
import heapq
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python3 -I does not add it
import feed  # noqa: E402

SEASON_OF_DATE = {"1803-05-09": 224, "1803-05-10": 224, "1803-05-11": 224, "1803-05-12": 224,
                  "1802-05-10": 225, "1802-05-11": 225, "1802-05-12": 225, "1802-05-13": 225,
                  "1801-05-11": 226, "1801-05-12": 226}
FAILS = []


def check(cond, label):
    if not cond:
        FAILS.append(label)
    return cond


def load(path):
    with open(path) as f:
        frames = [json.loads(line) for line in f]
    server = [f for f in frames if f["type"] != "subscription_ack"]
    season, seasons = None, []
    for f in server:        # season label of each frame, from the market date (closed-phase snapshots run late)
        m = f.get("message") if f["type"] == "news" else None
        if isinstance(m, dict) and isinstance(m.get("time"), str):
            season = SEASON_OF_DATE.get(m["time"][:10], season)
        seasons.append(season)
    return server, seasons


def news_key(f, season):
    m = f.get("message")
    if f["type"] == "news" and isinstance(m, dict) and m.get("type") != "snapshot" and m.get("seq") is not None:
        return (season, m["seq"])
    return None


# ---- simulated connections ----------------------------------------------------------------------
def drop_windows(rng, t0, t1, mean_gap=360.0):
    out, t = [], t0 + rng.expovariate(1.0 / mean_gap)
    while t < t1:
        d = (0.25 + rng.uniform(0.05, 0.6)) if rng.random() < 0.85 else rng.uniform(3.0, 35.0)
        out.append((t, t + d))
        t += d + rng.expovariate(1.0 / mean_gap)
    return out


class ServerIndex:
    """Arrival times, running server clock and per-(type, symbol) frame positions, for the resend bursts."""

    def __init__(self, server):
        self.recv = [f["received_at"] for f in server]
        self.clock, cur = [], None
        for f in server:
            ts = f.get("timestamp")
            if ts and (cur is None or ts > cur):
                cur = ts
            self.clock.append(cur)
        self.pos = collections.defaultdict(list)
        for i, f in enumerate(server):
            if f["type"] in ("orderbook", "quote", "trade"):
                self.pos[(f["type"], f["symbol"])].append(i)


def make_copy(server, seasons, rng, drops, ix, n_stalls=4):
    """[(arrival, frame, season)] of one simulated connection."""
    recv = ix.recv
    t0, t1 = recv[0], recv[-1]
    stalls = []
    for _ in range(n_stalls):
        a = rng.uniform(t0, t1)
        stalls.append((a, a + rng.uniform(0.2, 1.5)))
    dropped = [False] * len(server)
    bursts = []
    for a, b in drops:
        i0, i1 = bisect.bisect_right(recv, a), bisect.bisect_right(recv, b)
        for i in range(i0, i1):
            dropped[i] = True
        # resend burst at reconnect: newest book and quote per symbol (stamped with the server clock), last trade
        last = {}
        for key, idx in ix.pos.items():
            j = bisect.bisect_left(idx, i1) - 1
            if j >= 0:
                last[key] = server[idx[j]]
        now_ts = ix.clock[i1 - 1] if i1 else None
        burst = [{"type": "subscription_ack", "timestamp": now_ts}]
        for sym in sorted({k[1] for k in last}):
            for typ in ("orderbook", "quote"):
                if (typ, sym) in last:
                    burst.append(dict(last[(typ, sym)], timestamp=now_ts))
            if ("trade", sym) in last:
                burst.append(dict(last[("trade", sym)]))
        season = seasons[i1 - 1] if i1 else seasons[0]
        bursts.append((b, burst, season))
    out = []
    for i, f in enumerate(server):
        if not dropped[i]:
            out.append((recv[i] + 0.002 + rng.expovariate(1 / 0.008), f, seasons[i]))
    for b, burst, season in bursts:
        for f in burst:
            out.append((b + 0.002 + rng.expovariate(1 / 0.008), f, season))
    out.sort(key=lambda x: x[0])
    res, prev = [], -1e18
    for t, f, s in out:
        for a, b in stalls:
            if a <= t < b:
                t = b
        t = max(t, prev + 1e-6)          # one connection delivers in order
        res.append((t, f, s))
        prev = t
    return res


def merge_run(copies, hold=feed.SNAPSHOT_HOLD_S, cls=feed.FeedMerger):
    """Feed the copies through one FeedMerger in arrival order. Returns (events [(t, Event, season)], merger,
    book_violations)."""
    mg = cls(snapshot_hold_s=hold)
    heap = [(c[0][0], k, 0) for k, c in enumerate(copies) if c]
    heapq.heapify(heap)
    events = []
    fwd_ts = {}
    viol = 0
    season = None
    arrivals = []
    while heap:
        t, k, i = heapq.heappop(heap)
        _, f, s = copies[k][i]
        arrivals.append(t)
        season = s if s is not None else season
        for e in mg.poll(t):
            events.append((t, e, e_season(e, season)))
        out = mg.ingest(k, f, t)
        for e in out:
            events.append((t, e, e_season(e, season)))
        if f["type"] in ("orderbook", "quote"):
            key = (f["type"], f["symbol"])
            ts = feed._server_ts(f["timestamp"])
            if any(e.kind.startswith("md_") for e in out):
                fwd_ts[key] = ts
            elif not (key in fwd_ts and fwd_ts[key] >= ts):
                viol += 1                    # dropped although nothing as new was forwarded
        if i + 1 < len(copies[k]):
            heapq.heappush(heap, (copies[k][i + 1][0], k, i + 1))
    for e in mg.poll(float("inf")):
        events.append((float("inf"), e, e_season(e, season)))
    merge_run.arrivals = arrivals
    return events, mg, viol


def e_season(e, fallback):
    m = e.msg if isinstance(e.msg, dict) else {}
    if isinstance(m.get("time"), str) and m.get("type") != "snapshot":
        return SEASON_OF_DATE.get(m["time"][:10], fallback)
    return fallback


def round_opens(server):
    """Wall time of each round's open (09:30 market), median over the storm observations of that market date."""
    by = collections.defaultdict(list)
    for f in server:
        m = f.get("message") if f["type"] == "news" else None
        if isinstance(m, dict) and m.get("type") == "storm":
            hh, mm, ss = m["time"][11:19].split(":")
            by[m["time"][:10]].append(f["received_at"] - (int(hh) * 60 + int(mm) + int(ss) / 60 - 570) * 2)
    return sorted(sorted(v)[len(v) // 2] for v in by.values())


def run_merge_case(server, seasons, ix, opens, truth, rec_lost, rng, shared_frac, label, cls=feed.FeedMerger):
    t0, t1 = server[0]["received_at"], server[-1]["received_at"]
    dA = drop_windows(rng, t0, t1)
    dB = [w for w in dA if rng.random() < shared_frac] if shared_frac else []
    dB = sorted(dB + drop_windows(rng, t0, t1) if not shared_frac else
                dB + [w for w in drop_windows(rng, t0, t1) if rng.random() < 1 - shared_frac])
    A = make_copy(server, seasons, rng, dA, ix)
    B = make_copy(server, seasons, rng, dB, ix)
    allkeys = set(truth) | set(rec_lost)
    events, mg, book_viol = merge_run([A, B], cls=cls)
    arrivals = merge_run.arrivals

    def have(copy):
        return {news_key(f, s): f["message"] for _, f, s in copy if news_key(f, s)}
    hA, hB = have(A), have(B)
    union = set(hA) | set(hB)
    news = [(s, e) for _, e, s in events if e.seq is not None]
    fwd = [(s, e.seq) for s, e in news]
    ok = check(len(fwd) == len(set(fwd)), f"{label}: N1 duplicate news forwarded")
    ok &= check(set(fwd) == union, f"{label}: N1 forwarded != A u B ({len(union - set(fwd))} missing, "
                                   f"{len(set(fwd) - union)} extra)")
    ok &= check(all(e.msg == truth[(s, e.seq)] for s, e in news), f"{label}: N1 content differs from the recording")
    resets = [(t, e) for t, e, _ in events if e.kind == "season_reset"]
    order = [s for s, _ in news]
    ok &= check(len(resets) == 2, f"{label}: N2 {len(resets)} season resets, expected 2")
    ok &= check(order == sorted(order), f"{label}: N2 news of different seasons interleaved")
    # snapshots
    live, covered, snap_fwd, s1_bad = collections.defaultdict(set), collections.defaultdict(lambda: -1), [], 0
    season_now = None
    for t, e, s in events:
        if e.kind == "season_reset":
            season_now = None
        if e.seq is not None:
            live[s].add(e.seq)
            season_now = s
        if e.kind == "snapshot":
            s = season_now if season_now is not None else s
            L = e.msg.get("last_seq", -1)
            holes = [q for q in range(covered[s] + 1, L + 1) if q not in live[s]]
            s1_bad += not holes
            covered[s] = max(covered[s], L)
            snap_fwd.append((s, L))
    ok &= check(s1_bad == 0, f"{label}: S1 {s1_bad} snapshots forwarded without covering a hole")
    ok &= check(len(snap_fwd) == len(set(snap_fwd)), f"{label}: S1 a snapshot forwarded twice")
    snaps_avail = collections.defaultdict(list)
    for _, f, s in A + B:
        if f["type"] == "news" and f["message"].get("type") == "snapshot":
            snaps_avail[s].append(f["message"].get("last_seq", -1))
    never = sorted(allkeys - set(fwd))
    uncoverable = [k for k in never if not any(L >= k[1] for L in snaps_avail[k[0]])]
    s2_bad = [k for k in never if k not in uncoverable and not any(s == k[0] and L >= k[1] for s, L in snap_fwd)]
    ok &= check(not s2_bad, f"{label}: S2 {len(s2_bad)} lost seqs never covered by a snapshot, e.g. {s2_bad[:3]}")
    # trades
    tA = {f["trade_id"] for _, f, _ in A if f["type"] == "trade"}
    tB = {f["trade_id"] for _, f, _ in B if f["type"] == "trade"}
    tf = [e.msg["trade_id"] for _, e, _ in events if e.kind == "md_trade"]
    ok &= check(len(tf) == len(set(tf)) and set(tf) == tA | tB, f"{label}: T1 trades")
    # books and quotes
    ok &= check(book_viol == 0, f"{label}: B1 {book_viol} book/quote copies dropped without a newer one forwarded")
    mono_bad, last_fwd = 0, {}
    for _, e, _ in events:
        if e.kind in ("md_orderbook", "md_quote"):
            key = (e.kind, e.msg["symbol"])
            ts = feed._server_ts(e.msg["timestamp"])
            mono_bad += key in last_fwd and ts <= last_fwd[key][0]
            last_fwd[key] = (ts, e.msg)
    newest = {}
    for _, f, _ in A + B:
        if f["type"] in ("orderbook", "quote"):
            key = ("md_" + f["type"], f["symbol"])
            ts = feed._server_ts(f["timestamp"])
            if key not in newest or ts > newest[key][0]:
                newest[key] = (ts, f)
    final_ok = all(last_fwd.get(k, (None,))[0] == v[0] for k, v in newest.items())
    ok &= check(mono_bad == 0 and final_ok, f"{label}: B1 books/quotes not monotone ({mono_bad}) or final book stale")
    # outages
    outages = [e.msg for _, e, _ in events if e.kind == "feed_restored"]
    n_alarm = sum(e.kind == "feed_outage" for _, e, _ in events)
    silences = [(a, b) for a, b in zip(arrivals, arrivals[1:]) if b - a > feed.OUTAGE_ALERT_S]
    expect = [(a, b) for a, b in silences if any(o + 65 <= a <= o + 535 for o in opens)]
    raised = {o["since"]: o["gap_s"] for o in outages}
    missed = [(a, b) for a, b in expect if a not in raised or abs(raised[a] - (b - a)) > 1e-6]
    wrong = [x for x in raised if not any(o + 55 <= x <= o + 575 for o in opens)]
    ok &= check(not missed and not wrong and n_alarm == len(outages),
                f"{label}: O1 storm silences without alarm {missed[:2]}, alarms outside storm {wrong[:2]}")
    nA, nB = len(allkeys - set(hA)), len(allkeys - set(hB))
    return {"label": label, "ok": ok, "drops": (len(dA), len(dB)), "lost_A": nA, "lost_B": nB,
            "lost_merged": len(never), "rec_lost": len(rec_lost), "joint_drop": len(never) - len(rec_lost),
            "snap_in": mg.stats["snapshots_in"], "snap_fwd": len(snap_fwd), "snap_red": mg.stats["snapshots_redundant"],
            "uncoverable": len(uncoverable), "dup_news": mg.stats["dup_news"], "dup_trades": mg.stats["dup_md_trade"],
            "stale_md": mg.stats["stale_md_orderbook"] + mg.stats["stale_md_quote"],
            "outages": [round(o["gap_s"], 1) for o in outages], "first_copy": dict(mg.first_copy)}


def race_case(server, seasons, hold=feed.SNAPSHOT_HOLD_S):
    """23 Oct: the snapshot after 225 r3 advisory #1 came 19 ms after it. If connection 0 missed the advisory and
    its snapshot beats connection 1's live copy, the 1-s hold must let the live copy win (snapshot not forwarded)."""
    idx = next(i for i, f in enumerate(server) if f["type"] == "news" and seasons[i] == 225
               and f["message"].get("type") == "advisory" and f["message"].get("source") == "official"
               and f["message"].get("round") == 3 and f["message"].get("number") == 1)
    adv = server[idx]["message"]
    j = next(i for i in range(idx, len(server)) if server[i]["type"] == "news"
             and server[i]["message"].get("type") == "snapshot")
    snap = server[j]
    i225 = seasons.index(225)        # both connections clean from the season start up to the advisory
    pre = [(server[i]["received_at"], server[i], seasons[i]) for i in range(i225, idx)]
    t_adv = server[idx]["received_at"]
    news_between = [server[i] for i in range(idx, j) if server[i]["type"] == "news"]
    c0 = pre + [(t_adv + 0.020, snap, 225)]                   # connection 0 lost the advisory and the obs
    c1 = pre + [(t_adv + 0.039 + 0.001 * n, f, 225) for n, f in enumerate(news_between)]   # 19 ms behind
    events, mg, _ = merge_run([c0, c1], hold=hold)
    got = [e for _, e, _ in events if e.seq == adv["seq"]]
    snaps = [e for _, e, _ in events if e.kind == "snapshot" and e.msg is snap["message"]]
    ok = check(len(got) == 1 and not snaps, "race: snapshot forwarded although the live copy arrived inside the hold")
    # without the other connection the same snapshot must be forwarded (it is the only source of the advisory)
    events1, _, _ = merge_run([c0], hold=hold)
    ok &= check(any(e.kind == "snapshot" and e.msg is snap["message"] for _, e, _ in events1),
                "race: snapshot not forwarded when no connection had the advisory")
    return ok, snap["message"]["last_seq"], adv["seq"], round(snap["received_at"] - t_adv, 3)


# ---- Part 2: RobustFeed with fake sockets -----------------------------------------------------------
class FakeServer:
    def __init__(self, frames, dt_s):
        self.frames, self.dt = frames, dt_s
        self.t0 = time.monotonic()
        self.sent = collections.defaultdict(list)       # (conn, session) -> news seqs delivered

    def pos(self):
        return int((time.monotonic() - self.t0) / self.dt)


class FakeWS(feed.WSAdapter):
    """Streams the server's frames from the connect moment on. script: (mode, after_s): 'drop' returns None,
    'stall' never returns again."""

    def __init__(self, server, conn, session, script):
        self.server, self.conn, self.session, self.script = server, conn, session, script
        self.sends, self.closed = [], False

    async def connect(self, url, headers):
        await asyncio.sleep(0.01)
        self.i0 = self.i = self.server.pos()
        self.t_conn = time.monotonic()

    async def send(self, text):
        self.sends.append(text)

    async def recv(self):
        mode, after = self.script or (None, None)
        if mode and time.monotonic() - self.t_conn > after:
            if mode == "drop":
                return None
            while not self.closed:
                await asyncio.sleep(0.05)
            return None
        if self.closed:
            return None
        if self.i >= len(self.server.frames):
            await asyncio.sleep(3600)
        due = self.server.t0 + self.i * self.server.dt
        await asyncio.sleep(max(0.0, due - time.monotonic()))
        f = self.server.frames[self.i]
        self.i += 1
        if f["type"] == "news" and f["message"].get("seq") is not None:
            self.server.sent[(self.conn, self.session)].append(f["message"]["seq"])
        return json.dumps({k: v for k, v in f.items() if k != "received_at"})

    async def close(self):
        self.closed = True


async def robust_case(server, seasons):
    i0 = next(i for i, f in enumerate(server) if f["type"] == "news" and seasons[i] == 225
              and f["message"].get("type") == "storm_formed")
    frames = server[i0:i0 + 1800]
    srv = FakeServer(frames, 0.004)                   # 1,800 frames in 7.2 s (about 20x real time)
    sessions = collections.Counter()
    made = []
    scripts = {(0, 0): ("drop", 1.0), (1, 0): ("stall", 1.5)}

    def factory(k):
        def make():
            s = sessions[k]
            sessions[k] += 1
            ws = FakeWS(srv, k, s, scripts.get((k, s)))
            made.append(ws)
            return ws
        return make

    cb = []
    rf = feed.RobustFeed("wss://fake", {}, ["PORT"], ["news"], adapter_factory=[factory(0), factory(1)],
                         phase_fn=lambda: "storm", rng=random.Random(7), log=lambda *a: None, on_event=cb.append)
    evs = []

    async def consume():                              # the consumer API: async iterator (plus the callback above)
        async for ev in rf.events():
            evs.append(ev)

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(len(frames) * srv.dt + 0.5)
    rf.stop()
    for ws in made:
        await ws.close()
    try:
        await asyncio.wait_for(task, 5.0)
        ended = True
    except asyncio.TimeoutError:
        ended = False
    cats = collections.Counter(e.category for e in evs)
    shape_ok = all(isinstance(e.msg, dict) and isinstance(e.received_at, float) and e.received_at == e.recv_ts
                   and (e.seq is not None) == (e.category == "news") for e in evs
                   if e.category in ("news", "snapshot", "orderbook", "quote", "trade"))
    seqs = [e.seq for e in evs if e.seq is not None]
    delivered = set()
    for v in srv.sent.values():
        delivered.update(v)
    res = {
        "sessions": dict(sessions), "delays": [[round(d, 3) for d in x] for x in rf.delays],
        "watchdog_trips": rf.watchdog_trips, "news_fwd": len(seqs), "news_delivered": len(delivered),
        "subscriptions_per_session": sorted({len(ws.sends) for ws in made if hasattr(ws, "t_conn")}),
        "connected": sum(e.kind == "ws_connected" for e in evs), "disconnected": sum(e.kind == "ws_disconnected" for e in evs),
        "categories": dict(cats), "iterator_ended_after_stop": ended, "callback_events_match": cb == evs,
    }
    live = [ws for ws in made if hasattr(ws, "i0")]
    streamed = {f["message"]["seq"] for f in frames[min(ws.i0 for ws in live):max(ws.i for ws in live)]
                if f["type"] == "news" and f["message"].get("seq") is not None}
    res["news_streamed"] = len(streamed)
    ok = check(len(seqs) == len(set(seqs)) and set(seqs) == delivered, "robust: merged news != union of connections")
    ok &= check(delivered == streamed, "robust: a news seq the server streamed reached neither connection")
    ok &= check(rf.delays[0][:1] == [feed.RECONNECT_FIRST_S], "robust: conn 0 first reconnect not 0.25 s")
    ok &= check(rf.watchdog_trips[1] == 1 and rf.watchdog_trips[0] == 0, "robust: watchdog trips wrong")
    ok &= check(rf.delays[1][:1] == [feed.RECONNECT_FIRST_S], "robust: conn 1 first reconnect after watchdog not 0.25 s")
    ok &= check(res["subscriptions_per_session"] == [1], "robust: not exactly one subscription per session")
    ok &= check(ended and cb == evs and shape_ok and {"news", "orderbook", "quote", "trade", "status"} <= set(cats),
                "robust: consumer API (events() iterator / on_event callback / Event fields)")
    return ok, res


def ladder_case():
    rng = random.Random(1)
    seqs = [[feed.reconnect_delay(a, rng) for a in range(8)] for _ in range(2000)]
    first = {s[0] for s in seqs}
    later = [d for s in seqs for d in s[1:]]
    a1 = [s[1] for s in seqs]
    ok = check(first == {0.25}, "ladder: first retry not 0.25 s")
    ok &= check(max(later) <= 2.0 and min(later) >= 0.375, "ladder: delay outside 0.375-2.0 s")
    ok &= check(0.37 < min(a1) and max(a1) < 0.63, "ladder: second retry not 0.5 s +-25%")
    ok &= check(feed.watchdog_s("storm") == 3 and feed.watchdog_s("pre_storm") == 8 and feed.watchdog_s("aftermath") == 8
                and feed.watchdog_s("closed") == 35 and feed.watchdog_s(None) == 35, "watchdog table")
    return ok, (min(a1), max(a1)), (min(later), max(later)), [round(sum(s[a] for s in seqs) / len(seqs), 3) for a in range(8)]


class NoSeasonReset(feed.FeedMerger):
    def _new_season(self, m, recv_ts, why):
        return [feed.Event("season_reset", m, recv_ts)]


class LiteralLastSeq(feed.FeedMerger):
    def poll(self, now, phase=None):
        self.wm = max(self.wm, self.max_seq)
        return super().poll(now, phase)


def mutation_case(server, seasons, ix, opens, truth, rec_lost):
    global FAILS
    saved, out = FAILS, []
    for name, run in (
            ("no seen-set reset", lambda: run_merge_case(server, seasons, ix, opens, truth, rec_lost, random.Random(0),
                                                         0.0, "mut", cls=NoSeasonReset)),
            ("literal last_seq rule", lambda: run_merge_case(server, seasons, ix, opens, truth, rec_lost,
                                                             random.Random(0), 0.0, "mut", cls=LiteralLastSeq)),
            ("no snapshot hold", lambda: race_case(server, seasons, hold=0.0))):
        FAILS = []
        run()
        out.append((name, sorted({"race" if x.startswith("race") else x.split(": ", 1)[1].split(" ")[0] for x in FAILS})))
    FAILS = saved
    for name, caught in out:
        check(bool(caught), f"mutation '{name}' not caught")
    return out


def main():
    path = sys.argv[1]
    n_seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    t_start = time.perf_counter()
    server, seasons = load(path)
    truth = {}
    for f, s in zip(server, seasons):
        k = news_key(f, s)
        if k:
            truth[k] = f["message"]
    rec_lost = []
    for s in (224, 225):
        q = sorted(k[1] for k in truth if k[0] == s)
        rec_lost += [(s, x) for x in range(q[0], q[-1] + 1) if (s, x) not in truth]
    ix = ServerIndex(server)
    opens = round_opens(server)
    n_snap = sum(1 for f in server if f["type"] == "news" and f["message"].get("type") == "snapshot")
    print(f"recording: {len(server)} server frames, {len(truth)} seq'd news (224: "
          f"{sum(k[0] == 224 for k in truth)}, 225: {sum(k[0] == 225 for k in truth)}), {n_snap} snapshots; "
          f"lost on 23 Oct (both copies lack them): {len(rec_lost)}")

    print("\nPart 1: merge of two simulated connections (lost = seq'd news the stream never forwarded)")
    print(f"{'run':22s} {'drops A/B':>9s} {'lost A':>6s} {'lost B':>6s} {'merged':>6s} {'= rec':>5s} {'+joint':>6s} "
          f"{'snap in/fwd/red':>15s} {'dup news':>8s} {'dup trd':>7s} {'stale md':>8s}  outages (s)   result")
    rows = []
    for scen, frac in (("independent", 0.0), ("half shared", 0.5)):
        for seed in range(n_seeds):
            r = run_merge_case(server, seasons, ix, opens, truth, rec_lost, random.Random(1000 * (frac > 0) + seed), frac,
                               f"{scen} seed {seed}")
            rows.append((scen, r))
            print(f"{r['label']:22s} {r['drops'][0]:4d}/{r['drops'][1]:<4d} {r['lost_A']:6d} {r['lost_B']:6d} "
                  f"{r['lost_merged']:6d} {r['rec_lost']:5d} {r['joint_drop']:6d} "
                  f"{r['snap_in']:5d}/{r['snap_fwd']:3d}/{r['snap_red']:<5d} {r['dup_news']:8d} {r['dup_trades']:7d} "
                  f"{r['stale_md']:8d}  {','.join(str(x) for x in r['outages']):12s}  {'PASS' if r['ok'] else 'FAIL'}",
                  flush=True)
    for scen in ("independent", "half shared"):
        rs = [r for s, r in rows if s == scen]
        print(f"  {scen}: {sum(r['ok'] for r in rs)} of {len(rs)} runs pass; news lost per run: one connection "
              f"{sum(r['lost_A'] + r['lost_B'] for r in rs) / (2 * len(rs)):.1f} (mean of A and B), merged "
              f"{sum(r['lost_merged'] for r in rs) / len(rs):.1f}, of which {len(rec_lost)} were lost on 23 Oct before "
              f"the split and {sum(r['joint_drop'] for r in rs) / len(rs):.1f} were dropped by both copies")

    ok, L, aseq, lag = race_case(server, seasons)
    print(f"\nrace (225 r3 advisory #1, seq {aseq}; its snapshot, last_seq {L}, came {lag * 1000:.0f} ms later): "
          f"the live copy 19 ms behind the snapshot wins inside the 1-s hold, the snapshot is dropped; with one "
          f"connection the snapshot is forwarded -> {'PASS' if ok else 'FAIL'}")

    ok, res = asyncio.run(robust_case(server, seasons))
    print(f"\nPart 2: RobustFeed with fake sockets: {res} -> {'PASS' if ok else 'FAIL'}")

    ok, a1, later, means = ladder_case()
    print(f"\nPart 3: reconnect ladder: first retry 0.25 s; second {a1[0]:.3f}-{a1[1]:.3f} s; all later "
          f"{later[0]:.3f}-{later[1]:.3f} s; mean by attempt {means}; watchdogs {feed.WATCHDOG_S} -> "
          f"{'PASS' if ok else 'FAIL'}")

    muts = mutation_case(server, seasons, ix, opens, truth, rec_lost)
    print("\nPart 4: mutations -> checks that fail: " + "; ".join(f"{n}: {', '.join(c) or 'NONE'}" for n, c in muts)
          + (" -> PASS" if all(c for _, c in muts) else " -> FAIL"))

    print(f"\n{time.perf_counter() - t_start:.1f} s")
    if FAILS:
        print(f"FAILED ({len(FAILS)}):")
        for x in FAILS[:30]:
            print("  " + x)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
