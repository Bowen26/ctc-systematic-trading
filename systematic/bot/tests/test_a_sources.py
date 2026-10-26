"""Group A tests: ctcbot.sources (WsSingleSource, build_sources, RecordingSource, iter_recording) — §9.1 A sources.

The WebSocket tests inject a fake ``connect``; nothing touches the network.
"""
import asyncio
import json
import os
import random
import shutil
import sys
import time
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from ctcbot import sources  # noqa: E402
from ctcbot.config import load_config  # noqa: E402
from ctcbot.sources import RecordingSource, WsSingleSource, build_sources, iter_recording  # noqa: E402
from ctcbot.timesource import RealTime, ScaledTime  # noqa: E402
from ctcbot.types import SYMBOLS, ListLog, SourceFrame  # noqa: E402

KEY = "ws-SECRET-key-0123456789"
WS = "wss://ex.test/ws/v1/market-data"


def cfg_with(*extra):
    return load_config(sets=[f"exchange.ws_url={WS}", "exchange.rest_base=https://ex.test/api/v1"] + list(extra))


class Closed(Exception):
    """Stands in for websockets.ConnectionClosed."""


class FakeWS:
    """Serves scripted frames; then drops (raises), stalls (never returns) or keeps serving heartbeats."""

    def __init__(self, frames, after="drop", gap=0.0):
        self.frames = list(frames)
        self.after = after
        self.gap = gap
        self.sent = []
        self.sent_at = []
        self.closed = False
        self.t_open = time.monotonic()

    async def send(self, text):
        self.sent.append(text)
        self.sent_at.append(time.monotonic())

    async def recv(self):
        if self.frames:
            if self.gap:
                await asyncio.sleep(self.gap)
            return self.frames.pop(0)
        if self.after == "drop":
            raise Closed("no close frame received")
        while not self.closed:
            await asyncio.sleep(0.02)
        raise Closed("closed")

    async def close(self):
        self.closed = True


class FakeConnect:
    """Records every connect call; answers from a list of FakeWS objects or exceptions."""

    def __init__(self, plan):
        self.plan = list(plan)
        self.calls = []
        self.made = []

    async def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs, time.monotonic()))
        item = self.plan.pop(0) if self.plan else FakeWS([], after="stall")
        if isinstance(item, BaseException):
            raise item
        self.made.append(item)
        return item


async def run_for(src, seconds):
    out = []
    task = asyncio.ensure_future(src.run(out.append))
    await asyncio.sleep(seconds)
    src.stop()
    try:
        await asyncio.wait_for(task, 3.0)
    except asyncio.TimeoutError:
        task.cancel()
        raise
    return out


def frames_json(n, start=0):
    return [json.dumps({"type": "heartbeat", "timestamp": f"t{i}"}) for i in range(start, start + n)]


class TestWsSingleSource(unittest.TestCase):
    def make(self, connect, cfg=None, watchdog=10.0, **kw):
        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)
            await asyncio.sleep(0)
        src = WsSingleSource(cfg or cfg_with(), KEY, SYMBOLS, RealTime(), self.log, lambda: watchdog,
                             connect=connect, sleep=fake_sleep, rng=random.Random(5), **kw)
        return src, sleeps

    def setUp(self):
        self.log = ListLog()

    def test_one_subscribe_per_connection_and_frames(self):
        conn = FakeConnect([FakeWS(frames_json(3)), FakeWS(frames_json(2, 3), after="stall")])
        src, sleeps = self.make(conn)
        out = asyncio.run(run_for(src, 0.3))
        self.assertEqual(len(conn.calls), 2)
        url, kw, t_conn = conn.calls[0]
        self.assertEqual(url, WS)
        cfg = cfg_with()
        self.assertEqual(kw, {"additional_headers": {"X-API-Key": KEY}, "user_agent_header": cfg.get("exchange.user_agent"),
                              "open_timeout": 5.0, "ping_interval": 20.0, "ping_timeout": 20.0,
                              "max_size": 8388608, "max_queue": None, "proxy": None})
        for ws in conn.made:
            self.assertEqual(len(ws.sent), 1)
            sub = json.loads(ws.sent[0])
            self.assertEqual(sub, {"action": "subscribe", "symbols": list(SYMBOLS),
                                   "channels": ["news", "quotes", "trades", "orderbook"]})
            self.assertLess(ws.sent_at[0] - ws.t_open, 10.0)
            self.assertLess(ws.sent_at[0] - ws.t_open, 0.1)
        self.assertTrue(all(isinstance(f, SourceFrame) for f in out))
        seq = [(f.conn, f.control, f.frame.get("timestamp")) for f in out]
        self.assertEqual(seq, [("ws0#1", "up", None), ("ws0#1", None, "t0"), ("ws0#1", None, "t1"),
                               ("ws0#1", None, "t2"), ("ws0#1", "down", None), ("ws0#2", "up", None),
                               ("ws0#2", None, "t3"), ("ws0#2", None, "t4"), ("ws0#2", "down", None)])
        self.assertEqual(sleeps, [0.25])                          # a working session restarts the ladder
        self.assertTrue(all(f.recv_wall > 1e9 for f in out))
        self.assertEqual(src.stats().frames, 5)
        self.assertEqual(src.sent_total, 2)

    def test_reconnect_delays(self):
        plan = [OSError("refused")] * 6 + [FakeWS([], after="stall")]
        conn = FakeConnect(plan)
        src, sleeps = self.make(conn)
        asyncio.run(run_for(src, 0.2))
        self.assertEqual(len(conn.calls), 7)
        self.assertEqual(sleeps[0], 0.25)
        self.assertEqual(len(sleeps), 6)
        for d in sleeps[1:]:
            self.assertGreaterEqual(d, 0.4)
            self.assertLessEqual(d, 0.6)
        self.assertLessEqual(max(sleeps), 2.0)
        self.assertEqual(src.stats().errors, 6)
        rng = random.Random(1)
        src2 = WsSingleSource(cfg_with(), KEY, SYMBOLS, RealTime(), self.log, lambda: 3.0, connect=conn, rng=rng)
        delays = []
        for _ in range(200):
            delays.append(src2.reconnect_delay())
            src2._attempt += 1
        self.assertEqual(delays[0], 0.25)
        self.assertTrue(all(0.4 <= d <= 0.6 for d in delays[1:]))
        src3 = WsSingleSource(cfg_with("feed.reconnect_every_s=5.0"), KEY, SYMBOLS, RealTime(), self.log,
                              lambda: 3.0, connect=conn)
        src3._attempt = 3
        self.assertEqual(src3.reconnect_delay() <= 2.0, True)

    def test_watchdog_reconnects(self):
        conn = FakeConnect([FakeWS(frames_json(1), after="stall"), FakeWS([], after="stall")])
        src, sleeps = self.make(conn, watchdog=0.3)
        out = asyncio.run(run_for(src, 1.3))
        self.assertGreaterEqual(src.watchdog_trips, 1)
        self.assertGreaterEqual(len(conn.calls), 2)
        self.assertTrue(conn.made[0].closed)
        self.assertEqual(sleeps[0], 0.25)
        self.assertIn(("ws0#1", "down"), [(f.conn, f.control) for f in out])
        self.assertTrue(self.log.kinds("ws_watchdog"))

    def test_url_checks(self):
        for bad in ("ws://ex.test/ws/v1/market-data", "wss://evil.test/ws/v1/market-data", "https://ex.test/ws"):
            with self.assertRaises(ValueError):
                WsSingleSource(cfg_with(), KEY, SYMBOLS, RealTime(), self.log, lambda: 3.0, connect=FakeConnect([]),
                               url=bad)
        with self.assertRaises(ValueError):
            WsSingleSource(load_config(), KEY, SYMBOLS, RealTime(), self.log, lambda: 3.0, connect=FakeConnect([]))
        WsSingleSource(cfg_with(), KEY, SYMBOLS, RealTime(), self.log, lambda: 3.0, url="wss://EX.test/other")

    def test_network_gate(self):
        self.assertFalse(sources.NETWORK_ENABLED)
        src = WsSingleSource(cfg_with(), KEY, SYMBOLS, RealTime(), self.log, lambda: 3.0)

        async def go():
            await src.run(lambda f: None)
        with self.assertRaises(RuntimeError) as cm:
            asyncio.run(go())
        self.assertEqual(str(cm.exception), "network disabled")

    def test_key_not_logged(self):
        conn = FakeConnect([OSError(f"handshake failed for X-API-Key: {KEY}"), FakeWS(["not json", "[1, 2]"]),
                            FakeWS([], after="stall")])
        src, _ = self.make(conn)
        asyncio.run(run_for(src, 0.2))
        blob = json.dumps(self.log.events, default=str)
        self.assertNotIn(KEY, blob)
        self.assertIn("***", blob)
        self.assertNotIn(KEY, repr(src))
        self.assertTrue(self.log.kinds("ws_bad_frame"))
        self.assertTrue(any(f.get("url") == WS for f in self.log.kinds("ws_up")))

    def test_consumer_exception_keeps_connection(self):
        conn = FakeConnect([FakeWS(frames_json(3), after="stall")])
        src, _ = self.make(conn)
        got = []

        def emit(f):
            if f.frame.get("timestamp") == "t0":
                raise ValueError("consumer bug")
            got.append(f)

        async def go():
            task = asyncio.ensure_future(src.run(emit))
            await asyncio.sleep(0.2)
            src.stop()
            await asyncio.wait_for(task, 3.0)
        asyncio.run(go())
        self.assertEqual(len(conn.calls), 1)
        self.assertEqual([f.frame.get("timestamp") for f in got if f.control is None], ["t1", "t2"])
        self.assertTrue(any(f.get("where") == "ws_emit" for f in self.log.kinds("exception")))

    def test_bad_watchdog_callback(self):
        def boom():
            raise ValueError("no phase")
        src = WsSingleSource(cfg_with(), KEY, SYMBOLS, RealTime(), self.log, boom, connect=FakeConnect([]))
        self.assertEqual(src._watchdog(), 35.0)


class TestBuildSources(unittest.TestCase):
    def test_one_per_connection_with_local_addr(self):
        log = ListLog()
        conn = FakeConnect([FakeWS([], after="stall"), FakeWS([], after="stall")])
        cfg = cfg_with('feed.connections.1.local_addr=["192.168.7.20", 0]')
        srcs = build_sources(cfg, KEY, SYMBOLS, RealTime(), log, lambda: 8.0, connect=conn)
        self.assertEqual([s.name for s in srcs], ["ws0", "ws1"])
        self.assertEqual([s.local_addr for s in srcs], [None, ("192.168.7.20", 0)])

        async def go():
            out = []
            tasks = [asyncio.ensure_future(s.run(out.append)) for s in srcs]
            await asyncio.sleep(0.2)
            for s in srcs:
                s.stop()
            await asyncio.wait_for(asyncio.gather(*tasks), 3.0)
            return out
        out = asyncio.run(go())
        kws = {kw.get("local_addr", "none") for _, kw, _ in conn.calls}
        self.assertEqual(kws, {"none", ("192.168.7.20", 0)})
        self.assertEqual(sorted(f.conn for f in out if f.control == "up"), ["ws0#1", "ws1#1"])
        three = cfg_with('feed.connections=[{"name": "a", "local_addr": null}, {"name": "b", "local_addr": null},'
                         ' {"name": "c", "local_addr": null}]')
        self.assertEqual(len(build_sources(three, KEY, SYMBOLS, RealTime(), log, lambda: 8.0, connect=conn)), 3)


class TestRecording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        testpaths.require(testpaths.RECORDING)

    def test_iter_recording(self):
        n = 0
        prev = None
        for ra, fr in iter_recording(testpaths.RECORDING):
            self.assertNotIn("received_at", fr)
            self.assertIsInstance(ra, float)
            n += 1
            prev = ra
        self.assertEqual(n, 44296)
        self.assertGreater(prev, 1.79e9)
        d = testpaths.tmpdir("rec")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "bad.jsonl")
        with open(path, "w") as f:
            f.write('{"type": "heartbeat", "received_at": 5.5}\n\nnot json\n[1]\n{"type": "x"}\n'
                    '{"type": "y", "received_at": "soon"}\n{"type": "z", "received_at": 7}\n')
        self.assertEqual(list(iter_recording(path)), [(5.5, {"type": "heartbeat"}), (7.0, {"type": "z"})])

    def test_full_replay_unpaced(self):
        out = []
        src = RecordingSource(testpaths.RECORDING, RealTime(), ListLog(), paced=False)
        asyncio.run(src.run(out.append))
        frames = [f for f in out if f.control is None]
        ctrl = [(f.conn, f.control) for f in out if f.control is not None]
        self.assertEqual(len(frames), 44296)
        self.assertEqual(ctrl[0], ("rec#1", "up"))
        self.assertEqual(ctrl[-1], ("rec#6", "down"))
        pairs = ctrl[1:-1]
        self.assertEqual(len(pairs), 10)
        self.assertEqual(pairs, [(f"rec#{k}", "down") if i % 2 == 0 else (f"rec#{k + 1}", "up")
                                 for i, k in enumerate([1, 1, 2, 2, 3, 3, 4, 4, 5, 5])])
        acks = [f for f in out if f.frame.get("type") == "subscription_ack"]
        self.assertEqual([f.conn for f in acks], [f"rec#{k}" for k in range(1, 7)])
        self.assertTrue(all("received_at" not in f.frame for f in frames))
        self.assertEqual(frames[0].recv_wall, 1792766846.800372)
        self.assertEqual(src.stats().reconnects, 5)
        self.assertTrue(src.done)

    def test_window_and_pacing(self):
        firsts = list(iter_recording(testpaths.RECORDING))
        t_from = firsts[100][0]
        t_to = t_from + 3.0
        want = [fr for ra, fr in firsts if t_from <= ra < t_to]
        out = []
        ts = ScaledTime(30.0)
        src = RecordingSource(testpaths.RECORDING, ts, ListLog(), paced=True, t_from=t_from, t_to=t_to)
        t0 = time.monotonic()
        asyncio.run(src.run(out.append))
        wall = time.monotonic() - t0
        frames = [f for f in out if f.control is None]
        self.assertEqual([f.frame for f in frames], want)
        self.assertTrue(all(t_from <= f.recv_wall < t_to for f in frames))
        self.assertLess(wall, 3.0 / 30.0 + 1.5)
        span_mono = frames[-1].recv_mono - frames[0].recv_mono
        span_rec = frames[-1].recv_wall - frames[0].recv_wall
        self.assertLess(abs(span_mono - span_rec), 0.5)          # received_at - first maps onto timesrc time
        self.assertEqual((out[0].control, out[-1].control), ("up", "down"))


if __name__ == "__main__":
    unittest.main()
