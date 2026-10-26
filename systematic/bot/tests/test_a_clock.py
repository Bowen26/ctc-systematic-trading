"""Group A tests: ctcbot.clock.ClockModel (DESIGN.md §9.1 A clock)."""
import json
import os
import random
import statistics
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from ctcbot.case import CaseInfo, Schedule  # noqa: E402
from ctcbot.clock import ClockModel  # noqa: E402
from ctcbot.config import load_config  # noqa: E402
from ctcbot.normalize import FeedNormalizer  # noqa: E402
from ctcbot.sources import iter_recording  # noqa: E402
from ctcbot.types import (ListLog, NewsEvent, Phase, SeasonResetEvent, ServerTime, SnapshotEvent,  # noqa: E402
                          SourceFrame)

RESEARCH_TIMING = {"pre_storm": 60, "storm": 480, "aftermath": 240, "obs_every": 2, "track_every": 20,
                   "advisory_first": 30, "advisory_every": 60, "prelim_report": 30, "final_report": 40}
DATE = "1802-05-10"


def mtime(t, date=DATE):
    """Market-time string for real second t of the round (1/30-s resolution kept as fractions)."""
    secs = 34200.0 + 30.0 * t
    h, rem = divmod(secs, 3600.0)
    m, s = divmod(rem, 60.0)
    whole = int(s)
    frac = s - whole
    out = f"{date}T{int(h):02d}:{int(m):02d}:{whole:02d}"
    if frac > 1e-9:
        out += f".{int(round(frac * 1e6)):06d}"
    return out


class ClockBase(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.sch = Schedule(RESEARCH_TIMING, self.cfg.get("cost.phases"))
        self.log = ListLog()
        self.clk = ClockModel(self.sch, self.cfg, self.log)


class TestSynthetic(ClockBase):
    def test_storm_obs_with_delivery_delay(self):
        rng = random.Random(7)
        open_mono = 1000.0
        errs = []
        obs = []
        for k in range(240):                          # storm obs every 2 s, t = 60..538
            t = 60.0 + 2.0 * k
            obs.append((open_mono + t + rng.uniform(0.0, 0.3), t))
        obs.sort()
        i = 0
        now = obs[0][0]
        while now < open_mono + 540.0:
            while i < len(obs) and obs[i][0] <= now:
                self.clk.observe_feed(mtime(obs[i][1]), 1, obs[i][0], "storm", 0)
                i += 1
            r = self.clk.read(now)
            if i >= 2:
                self.assertTrue(r.running)
                self.assertTrue(r.known)
                self.assertEqual(r.quality, "feed")
                errs.append(abs(r.t - (now - open_mono)))
            now += rng.uniform(0.05, 0.4)
        self.assertLessEqual(max(errs), 0.35)
        self.assertEqual(self.clk.round_events(), [(0, 1)])
        self.assertEqual(self.clk.read(now).phase, Phase.AFTERMATH)

    def _slow_tick(self, use_server):
        slope = 1.0005
        open_mono = 50.0
        rng = random.Random(3)
        errs = []
        events = []
        for k in range(780):
            if use_server:
                sent = open_mono + k + rng.uniform(0, 0.01)
                rtt = rng.uniform(0.02, 0.25)
                tm = (sent + rtt / 2 - open_mono) / slope
                ft = mtime(round(tm * 30.0) / 30.0)   # one market second resolution
                events.append((sent + rtt, "server", (ServerTime("c1", "running", ft, tm), sent, sent + rtt)))
        for k in range(240):
            tm = 60.0 + 2.0 * k
            events.append((open_mono + tm * slope + rng.uniform(0.0, 0.3), "storm", tm))
        events.append((open_mono + 0.2, "snap", 0.0))
        events.sort(key=lambda e: e[0])
        for when, kind, payload in events:
            if kind == "server":
                self.clk.observe_server(*payload)
            elif kind == "storm":
                self.clk.observe_feed(mtime(payload), 1, when, "storm", 0)
            else:
                self.clk.observe_feed(mtime(payload), 1, when, "snapshot", 0, phase="pre_storm")
            r = self.clk.read(when)
            truth = (when - open_mono) / slope
            if r.running and r.t is not None and truth > 62.0:
                errs.append(abs(r.t - truth))
        self.assertGreater(len(errs), 200)
        return max(errs)

    def test_server_slope(self):
        self.assertLessEqual(self._slow_tick(True), 0.3)
        self.assertEqual(self.clk.read(50.0 + 300.0).quality, "server")

    def test_feed_slope(self):
        self.assertLessEqual(self._slow_tick(False), 0.3)

    def test_server_state_precedence(self):
        self.clk.observe_feed(mtime(100.0), 1, 200.0, "storm", 0)
        self.clk.observe_feed(mtime(102.0), 1, 202.0, "storm", 0)
        self.assertTrue(self.clk.read(202.1).running)
        self.clk.observe_server(ServerTime("c1", "paused", mtime(102.0), 0.0), 202.2, 202.3)
        r = self.clk.read(202.4)
        self.assertFalse(r.running)
        self.assertAlmostEqual(r.t, 102.0, places=6)
        self.assertEqual(r.comp_id, "c1")
        self.clk.observe_feed(mtime(104.0), 1, 204.0, "storm", 0)
        self.assertFalse(self.clk.read(204.1).running)             # the server is still fresh
        self.assertTrue(self.clk.read(206.0).running)              # server stale: back to the feed rule

    def test_pause_detection_and_reset(self):
        open_mono = 500.0
        self.clk.on_season_reset(1, "comp-a")
        self.assertEqual(self.clk.read(400.0).running, False)
        for k in range(5):                                         # five 09:30:00 snapshots 30 s apart
            at = open_mono - 120.0 + 30.0 * k + 0.36
            self.clk.observe_feed(mtime(0.0), 1, at, "snapshot", 1, phase="pre_storm")
            r = self.clk.read(at + 0.01)
            self.assertFalse(r.running, k)
            self.assertEqual(r.t, 0.0)
            self.assertEqual(r.round, 1)
        self.assertEqual(self.clk.round_events(), [(1, 1)])
        self.assertTrue(any(k == "clock_stopped" for k, _ in self.log.events))
        self.clk.observe_feed(mtime(30.0), 1, open_mono + 30.48, "snapshot", 1, phase="pre_storm")
        r = self.clk.read(open_mono + 31.0)
        self.assertTrue(r.running)
        self.assertEqual(r.quality, "feed")
        self.assertLess(abs(r.t - 31.0), 0.5)                     # the pause samples were not used
        self.assertEqual(r.phase, Phase.PRE_STORM)
        self.clk.observe_feed(mtime(60.0), 1, open_mono + 60.1, "storm", 1)
        self.clk.observe_feed(mtime(62.0), 1, open_mono + 62.05, "storm", 1)
        r = self.clk.read(open_mono + 70.0)
        self.assertLess(abs(r.t - 70.0), 0.1)
        # season reset: not running until time advances again
        self.clk.on_season_reset(2, "comp-b")
        r = self.clk.read(open_mono + 71.0)
        self.assertFalse(r.running)
        self.assertIsNone(r.round)
        self.assertEqual(r.season_epoch, 2)
        self.clk.observe_feed(mtime(0.0, "1801-05-11"), 1, open_mono + 80.0, "snapshot", 2, phase="pre_storm")
        self.assertFalse(self.clk.read(open_mono + 81.0).running)
        self.clk.observe_feed(mtime(30.0, "1801-05-11"), 1, open_mono + 110.2, "snapshot", 2, phase="pre_storm")
        self.assertTrue(self.clk.read(open_mono + 111.0).running)
        self.assertEqual(self.clk.round_events(), [(2, 1)])

    def test_round_back_date_mismatch_closed(self):
        self.clk.observe_feed(mtime(300.0, "1802-05-11"), 2, 1300.0, "storm", 0)
        self.clk.observe_feed(mtime(302.0, "1802-05-11"), 2, 1302.0, "storm", 0)
        self.clk.observe_feed(mtime(740.0, "1802-05-10"), 1, 1302.5, "news", 0)       # late round-1 report
        self.clk.observe_feed(mtime(303.0, "1802-05-12"), 2, 1303.0, "news", 0)       # wrong date for round 2
        self.clk.observe_feed(mtime(800.0, "1802-05-11"), 2, 1303.1, "snapshot", 0, phase="closed")
        r = self.clk.read(1303.2)
        self.assertEqual((r.round, r.date), (2, "1802-05-11"))
        self.assertLess(abs(r.t - 303.2), 0.01)
        self.assertEqual(self.clk.round_events(), [(0, 2)])
        kinds = {k for k, _ in self.log.events}
        self.assertIn("clock_round_back", kinds)
        self.assertIn("clock_date_mismatch", kinds)
        self.assertIn("clock_phase_mismatch", kinds)               # a 'closed' snapshot at t = 303: logged only

    def test_extrapolate_then_unknown_and_t_to_mono(self):
        self.clk.observe_feed(mtime(200.0), 1, 1200.0, "storm", 0)
        self.clk.observe_feed(mtime(202.0), 1, 1202.0, "storm", 0)
        self.assertAlmostEqual(self.clk.t_to_mono(740.0, 1203.0), 1740.0, places=6)
        r = self.clk.read(1211.0)
        self.assertEqual(r.quality, "feed")                        # storm stale after 10 s, but snapshot/news none
        r = self.clk.read(1212.5)
        self.assertEqual(r.quality, "extrapolated")
        self.assertAlmostEqual(r.t, 212.5, places=6)
        r = self.clk.read(1243.0)
        self.assertEqual(r.quality, "unknown")
        self.assertFalse(r.known)
        self.assertEqual(r.phase, Phase.UNKNOWN)
        self.assertIsNone(self.clk.t_to_mono(740.0, 1243.0))
        kinds = [f["quality"] for k, f in self.log.events if k == "clock"]
        self.assertEqual(kinds[-3:], ["feed", "extrapolated", "unknown"])

    def test_snapshot_and_news_anchor(self):
        self.clk.observe_feed(mtime(30.0), 1, 1030.4, "snapshot", 0, phase="pre_storm")
        self.clk.observe_feed(mtime(31.0), 1, 1031.05, "news", 0)
        r = self.clk.read(1040.0)
        self.assertTrue(r.running)
        self.assertEqual(r.quality, "feed")
        self.assertAlmostEqual(r.t, 40.0 - 0.05, places=6)        # minimum offset among the samples


class TestRecording(unittest.TestCase):
    """Feed-only replay of the testnet recording, using received_at as recv_mono."""

    OPENS = {(0, 3): 1792766628.455, (1, 1): 1792767650.037, (1, 2): 1792768610.536, (1, 3): 1792769571.126}

    @classmethod
    def setUpClass(cls):
        testpaths.require(testpaths.RECORDING)
        testpaths.require(testpaths.RESEARCH_CASE)
        cfg = load_config()
        case = CaseInfo.from_file(testpaths.RESEARCH_CASE)
        sch = Schedule(case.timing, cfg.get("cost.phases"))
        log = ListLog()
        norm = FeedNormalizer(cfg, log)
        clk = ClockModel(sch, cfg, log)
        cls.events, cls.est, cls.closed_checks = [], {}, []
        for ra, fr in iter_recording(testpaths.RECORDING):
            for e in norm.poll(ra) + norm.ingest(SourceFrame(fr, ra, ra, "rec#1")):
                if isinstance(e, SeasonResetEvent) and not e.provisional:
                    clk.on_season_reset(e.new_epoch, None)
                elif isinstance(e, NewsEvent) and e.market_time:
                    clk.observe_feed(e.market_time, e.round, e.recv_mono, "storm" if e.mtype == "storm" else "news",
                                     e.season_epoch)
                elif isinstance(e, SnapshotEvent) and e.stage == "arrived" and e.market_time:
                    clk.observe_feed(e.market_time, e.round, e.recv_mono, "snapshot", e.season_epoch, phase=e.phase)
                    if e.market_time in ("1802-05-10T16:52:30", "1802-05-11T01:55:00"):
                        new = clk.round_events()
                        cls.events += new
                        cls.closed_checks.append((clk.read(ra), new))
            cls.events += clk.round_events()
            m = fr.get("message") if fr.get("type") == "news" else None
            if isinstance(m, dict) and m.get("type") == "storm":
                r = clk.read(ra)
                if r.running and r.t is not None:
                    cls.est.setdefault((r.season_epoch, r.round), []).append(ra - r.t)

    def test_round_events(self):
        self.assertEqual(self.events, [(0, 3), (1, 1), (1, 2), (1, 3), (2, 1)])

    def test_round_opens(self):
        self.assertEqual(set(self.est), set(self.OPENS))
        for key, fit in self.OPENS.items():
            got = statistics.median(self.est[key])
            self.assertLess(abs(got - fit), 0.5, (key, got, fit))
            self.assertLess(max(abs(x - fit) for x in self.est[key]), 0.5, key)

    def test_closed_snapshots_after_225_r1(self):
        self.assertEqual(len(self.closed_checks), 2)
        for r, new in self.closed_checks:
            self.assertEqual((r.season_epoch, r.round, r.date), (1, 1, "1802-05-10"))
            self.assertEqual(new, [])
            self.assertFalse(r.running)
            self.assertEqual(r.phase, Phase.CLOSED)


if __name__ == "__main__":
    unittest.main()
