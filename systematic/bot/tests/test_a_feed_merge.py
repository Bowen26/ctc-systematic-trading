"""Group A tests: two simulated connections merged by one FeedNormalizer (DESIGN.md §9.1 A feed_merge).

The playbook's "two connections, merge and dedup". ``drop_windows``, ``ServerIndex``, ``make_copy``, the season
labelling of ``load`` and the arrival-order merge are copied from ``bot_skeleton/test_feed_merge.py`` (allowed by
DESIGN.md §0). The recording is the server stream; each run makes two copies with independent (or half
shared) drops, reconnect resend bursts, latency jitter and stalls, and feeds them as ``ws0`` / ``ws1``
SourceFrames through one normalizer in arrival order.

Checks per run: N1 every news (season, seq) either copy received is forwarded exactly once with its content;
N2 the season resets happen at 224->225 and 225->226 and nowhere else; S1 every forwarded relevant snapshot
covers a seq neither forwarded nor covered before, and none is forwarded twice; S2 every seq neither copy
delivered is covered by a later relevant snapshot whenever one exists; T1 every trade_id of either copy is
forwarded once; B1 forwarded book and quote timestamps strictly increase per symbol and type, and the last
forwarded frame is the newest either copy had; G1 the largest storm-age jump of each round on the merged
stream exceeds 10 s exactly when both copies were dark that long (always in 225 r2: the recorded 31.7-s
outage).
"""
import bisect
import collections
import datetime as dt
import heapq
import os
import random
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from ctcbot.config import load_config  # noqa: E402
from ctcbot.normalize import FeedNormalizer  # noqa: E402
from ctcbot.sources import iter_recording  # noqa: E402
from ctcbot.types import (BookEvent, ListLog, NewsEvent, QuoteEvent, SeasonResetEvent, SnapshotEvent,  # noqa: E402
                          SourceFrame, TradeEvent)

SEASON_OF_DATE = {"1803-05-09": 224, "1803-05-10": 224, "1803-05-11": 224, "1803-05-12": 224,
                  "1802-05-10": 225, "1802-05-11": 225, "1802-05-12": 225, "1802-05-13": 225,
                  "1801-05-11": 226, "1801-05-12": 226}
FIRST_SEASON = 224
N_SEEDS = 20 if testpaths.SLOW else 5


def load(path):
    frames = []
    for ra, fr in iter_recording(path):
        fr = dict(fr, received_at=ra)
        frames.append(fr)
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


# ---- simulated connections (copied from bot_skeleton/test_feed_merge.py) --------------------------------
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


def merge_run(copies, cfg):
    """Feed the copies through one FeedNormalizer in arrival order (core order: ingest, then poll)."""
    norm = FeedNormalizer(cfg, ListLog())
    heap = [(c[0][0], k, 0) for k, c in enumerate(copies) if c]
    heapq.heapify(heap)
    events = []
    while heap:
        t, k, i = heapq.heappop(heap)
        _, f, _ = copies[k][i]
        frame = {kk: v for kk, v in f.items() if kk != "received_at"}
        sf = SourceFrame(frame=frame, recv_wall=t, recv_mono=t, conn=f"ws{k}#1")
        events += [(t, e) for e in norm.ingest(sf)]
        events += [(t, e) for e in norm.poll(t)]
        if i + 1 < len(copies[k]):
            heapq.heappush(heap, (copies[k][i + 1][0], k, i + 1))
    events += [(float("inf"), e) for e in norm.poll(float("inf"))]
    return events, norm


def _ts(s):
    return dt.datetime.fromisoformat(s).timestamp()


def _max_jump(ages):
    """Largest forward jump between consecutive observations (running maximum; late older ones ignored)."""
    best, hi = 0.0, None
    for a in ages:
        if hi is None:
            hi = a
        elif a > hi:
            best = max(best, a - hi)
            hi = a
    return best


class TestFeedMerge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        testpaths.require(testpaths.RECORDING)
        cls.cfg = load_config()
        cls.server, cls.seasons = load(testpaths.RECORDING)
        cls.truth = {}
        for f, s in zip(cls.server, cls.seasons):
            k = news_key(f, s)
            if k:
                cls.truth[k] = f["message"]
        cls.rec_lost = []
        for s in (224, 225):
            q = sorted(k[1] for k in cls.truth if k[0] == s)
            cls.rec_lost += [(s, x) for x in range(q[0], q[-1] + 1) if (s, x) not in cls.truth]
        cls.ix = ServerIndex(cls.server)

    def _run(self, rng, shared_frac, label):
        server, seasons, ix = self.server, self.seasons, self.ix
        t0, t1 = server[0]["received_at"], server[-1]["received_at"]
        dA = drop_windows(rng, t0, t1)
        dB = [w for w in dA if rng.random() < shared_frac] if shared_frac else []
        dB = sorted(dB + drop_windows(rng, t0, t1) if not shared_frac else
                    dB + [w for w in drop_windows(rng, t0, t1) if rng.random() < 1 - shared_frac])
        A = make_copy(server, seasons, rng, dA, ix)
        B = make_copy(server, seasons, rng, dB, ix)
        events, norm = merge_run([A, B], self.cfg)

        def have(copy):
            return {news_key(f, s): f["message"] for _, f, s in copy if news_key(f, s)}
        hA, hB = have(A), have(B)
        union = set(hA) | set(hB)
        news = [e for _, e in events if isinstance(e, NewsEvent) and e.seq is not None]
        fwd = [(FIRST_SEASON + e.season_epoch, e.seq) for e in news]
        # N1
        self.assertEqual(len(fwd), len(set(fwd)), f"{label}: N1 duplicate news forwarded")
        self.assertEqual(set(fwd), union, f"{label}: N1 forwarded != A u B")
        for e in news:
            self.assertEqual(e.msg, self.truth[(FIRST_SEASON + e.season_epoch, e.seq)], f"{label}: N1 content")
        # N2
        resets = [e for _, e in events if isinstance(e, SeasonResetEvent)]
        self.assertEqual([(r.new_epoch, r.provisional) for r in resets], [(1, False), (2, False)], label)
        self.assertEqual(norm.stats()["provisional_resets"], 0, label)
        order = [s for s, _ in fwd]
        self.assertEqual(order, sorted(order), f"{label}: N2 news of different seasons interleaved")
        # S1
        live, covered, snap_fwd = collections.defaultdict(set), collections.defaultdict(lambda: -1), []
        for _, e in events:
            if isinstance(e, NewsEvent) and e.seq is not None:
                live[FIRST_SEASON + e.season_epoch].add(e.seq)
            elif isinstance(e, SnapshotEvent) and e.stage == "released" and e.engine_relevant:
                s = FIRST_SEASON + e.season_epoch
                holes = [q for q in range(covered[s] + 1, e.last_seq + 1) if q not in live[s]]
                self.assertTrue(holes, f"{label}: S1 snapshot {e.last_seq} covers no hole")
                covered[s] = max(covered[s], e.last_seq)
                snap_fwd.append((s, e.last_seq))
        self.assertEqual(len(snap_fwd), len(set(snap_fwd)), f"{label}: S1 a snapshot forwarded twice")
        # S2
        avail = collections.defaultdict(list)
        for _, f, s in A + B:
            if f["type"] == "news" and f["message"].get("type") == "snapshot":
                avail[s].append(f["message"].get("last_seq", -1))
        never = sorted((set(self.truth) | set(self.rec_lost)) - set(fwd))
        coverable = [k for k in never if any(L >= k[1] for L in avail[k[0]])]
        bad = [k for k in coverable if not any(s == k[0] and L >= k[1] for s, L in snap_fwd)]
        self.assertEqual(bad, [], f"{label}: S2 lost seqs never covered")
        # T1
        tids = {f["trade_id"] for _, f, _ in A + B if f["type"] == "trade"}
        tf = [e.trade_id for _, e in events if isinstance(e, TradeEvent)]
        self.assertEqual(len(tf), len(set(tf)), f"{label}: T1 duplicate trade")
        self.assertEqual(set(tf), tids, f"{label}: T1 trades")
        # B1
        last = {}
        for _, e in events:
            if isinstance(e, (BookEvent, QuoteEvent)):
                key = (type(e).__name__, e.symbol)
                ts = _ts(e.server_ts)
                if key in last:
                    self.assertGreater(ts, last[key], f"{label}: B1 not increasing {key}")
                last[key] = ts
        newest = {}
        for _, f, _ in A + B:
            if f["type"] in ("orderbook", "quote"):
                key = ("BookEvent" if f["type"] == "orderbook" else "QuoteEvent", f["symbol"])
                newest[key] = max(newest.get(key, -1e18), _ts(f["timestamp"]))
        self.assertEqual(last, newest, f"{label}: B1 final book is not the newest")
        # G1
        merged = collections.defaultdict(list)
        for _, e in events:
            if isinstance(e, NewsEvent) and e.mtype == "storm" and isinstance(e.msg.get("age"), (int, float)):
                merged[(e.season_epoch, e.round)].append(e.msg["age"])
        both = collections.defaultdict(set)
        for (s, seq) in union:
            m = self.truth[(s, seq)]
            if m.get("type") == "storm":
                both[(s - FIRST_SEASON, int(m["round"]))].add(m["age"])
        for key, ages in merged.items():
            g_merged = _max_jump(ages)
            g_union = _max_jump(sorted(both[key]))
            self.assertEqual(g_merged > 10.0, g_union > 10.0, f"{label}: G1 {key} {g_merged} vs {g_union}")
        self.assertGreater(_max_jump(merged[(1, 2)]), 10.0, f"{label}: G1 225 r2 outage not seen")
        return {"lost_merged": len(never), "snap_fwd": len(snap_fwd), "dup_news": norm.stats()["dup_news"]}

    def test_independent(self):
        for seed in range(N_SEEDS):
            with self.subTest(seed=seed):
                r = self._run(random.Random(seed), 0.0, f"independent seed {seed}")
                self.assertEqual(r["lost_merged"] >= len(self.rec_lost), True)

    def test_half_shared(self):
        for seed in range(N_SEEDS):
            with self.subTest(seed=seed):
                self._run(random.Random(1000 + seed), 0.5, f"half shared seed {seed}")


if __name__ == "__main__":
    unittest.main()
