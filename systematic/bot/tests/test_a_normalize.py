"""Group A tests: ctcbot.normalize.FeedNormalizer (DESIGN.md §9.1 A normalize)."""
import importlib.util
import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from ctcbot.config import load_config  # noqa: E402
from ctcbot.normalize import FeedNormalizer  # noqa: E402
from ctcbot.sources import iter_recording  # noqa: E402
from ctcbot.types import (BookEvent, ConnEvent, HeartbeatEvent, ListLog, NewsEvent, QuoteEvent,  # noqa: E402
                          SeasonResetEvent, SnapshotEvent, SourceFrame, TradeEvent)

RELEVANT = [695, 764, 250, 416, 828]


class LiteralRule(FeedNormalizer):
    """The mutation the design warns about: relevant iff last_seq > the highest seq forwarded."""

    def _relevant(self, last_seq):
        return last_seq > self.max_seq


def replay(norm, path):
    """Feed a recording through ``norm`` as one connection, in the core's order (ingest, then poll at the
    same instant: DESIGN.md §4.D ``on_frames`` with one frame per batch); returns every event."""
    out = []
    for ra, fr in iter_recording(path):
        out += norm.ingest(SourceFrame(fr, ra, ra, "rec#1"))
        out += norm.poll(ra)
    out += norm.poll(float("inf"))
    return out


class TestRecording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        testpaths.require(testpaths.RECORDING)
        cls.cfg = load_config()
        cls.log = ListLog()
        cls.norm = FeedNormalizer(cls.cfg, cls.log)
        cls.events = replay(cls.norm, testpaths.RECORDING)

    def test_relevant_snapshots(self):
        rel = [e.last_seq for e in self.events
               if isinstance(e, SnapshotEvent) and e.stage == "released" and e.engine_relevant]
        self.assertEqual(rel, RELEVANT)
        arrived = sum(1 for e in self.events if isinstance(e, SnapshotEvent) and e.stage == "arrived")
        released = sum(1 for e in self.events if isinstance(e, SnapshotEvent) and e.stage == "released")
        self.assertEqual(arrived, 123)
        self.assertEqual(released, 103)          # 20 repeats in the pauses are held only once
        st = self.norm.stats()
        self.assertEqual(st["snapshots_relevant"], 5)
        self.assertEqual(st["snapshots_in"], 123)

    def test_skeleton_merger_agrees(self):
        testpaths.require(testpaths.SKELETON_FEED)
        spec = importlib.util.spec_from_file_location("skeleton_feed_ref", testpaths.SKELETON_FEED)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mg = mod.FeedMerger()
        rel = []
        for ra, fr in iter_recording(testpaths.RECORDING):
            for ev in mg.poll(ra) + mg.ingest(0, fr, ra):
                if ev.kind == "snapshot":
                    rel.append(ev.msg.get("last_seq"))
        rel += [ev.msg.get("last_seq") for ev in mg.poll(float("inf")) if ev.kind == "snapshot"]
        self.assertEqual(rel, RELEVANT)

    def test_trades_news_resets(self):
        st = self.norm.stats()
        self.assertGreaterEqual(st["dup_trades"], 37)
        ids = [e.trade_id for e in self.events if isinstance(e, TradeEvent)]
        self.assertEqual(len(ids), len(set(ids)))
        news = [(e.season_epoch, e.seq) for e in self.events if isinstance(e, NewsEvent)]
        self.assertEqual(len(news), len(set(news)))
        resets = [e for e in self.events if isinstance(e, SeasonResetEvent)]
        self.assertEqual([(e.reason, e.new_epoch, e.provisional) for e in resets],
                         [("snapshot_last_seq", 1, False), ("snapshot_last_seq", 2, False)])
        self.assertEqual(st["provisional_resets"], 0)
        self.assertEqual(st["dup_news"], 0)
        self.assertEqual(st["seq_conflict"], 0)
        self.assertEqual(self.norm.epoch, 2)
        self.assertEqual({e.season_epoch for e in self.events if isinstance(e, NewsEvent)}, {0, 1})

    def test_empty_books_and_none_quotes(self):
        empties = [e for e in self.events if isinstance(e, BookEvent) and not e.bids and not e.asks]
        nones = [e for e in self.events if isinstance(e, QuoteEvent) and e.bid is None and e.ask is None]
        self.assertGreater(len(empties), 0)
        self.assertGreater(len(nones), 0)

    def test_literal_rule_mutation_is_caught(self):
        events = replay(LiteralRule(self.cfg, ListLog()), testpaths.RECORDING)
        rel = [e.last_seq for e in events if isinstance(e, SnapshotEvent) and e.stage == "released"
               and e.engine_relevant]
        self.assertFalse(set(rel) & set(RELEVANT), rel)


class Feed:
    """Helper: feed synthetic frames through one normalizer with explicit times."""

    def __init__(self, test, **sets):
        items = [f"feed.{k}={v}" for k, v in sets.items()]
        self.log = ListLog()
        self.n = FeedNormalizer(load_config(sets=items), self.log)
        self.events = []
        self.t = 0.0
        self.test = test

    def news(self, m, dt=0.1, conn="ws0#1"):
        self.t += dt
        ev = self.n.poll(self.t) + self.n.ingest(SourceFrame({"type": "news", "message": m}, self.t, self.t, conn))
        self.events += ev
        return ev

    def frame(self, f, dt=0.1, conn="ws0#1", control=None):
        self.t += dt
        ev = self.n.poll(self.t) + self.n.ingest(SourceFrame(f, self.t, self.t, conn, control))
        self.events += ev
        return ev

    def advance(self, dt):
        self.t += dt
        ev = self.n.poll(self.t)
        self.events += ev
        return ev

    def season_storm(self, rnd=1, storm="Pia", date="1802-05-10", n=800, start=0):
        """A season's worth of storm messages: seqs start..n-1 in round rnd."""
        self.news({"type": "storm_formed", "round": rnd, "storm": storm, "time": f"{date}T10:00:00", "seq": start})
        for s in range(start + 1, n):
            self.news({"type": "storm", "round": rnd, "storm": storm, "time": f"{date}T10:{(s // 30) % 60:02d}:00",
                       "age": s, "seq": s}, dt=0.01)


def snap(last_seq, rnd=1, time="1802-05-10T10:30:00", phase="storm"):
    return {"type": "snapshot", "round": rnd, "storm": "Pia", "time": time, "phase": phase, "last_seq": last_seq}


def news_of(events):
    return [e for e in events if isinstance(e, NewsEvent)]


def resets_of(events):
    return [e for e in events if isinstance(e, SeasonResetEvent)]


class TestSynthetic(unittest.TestCase):
    def test_announcement_seq0_after_storm_formed(self):
        f = Feed(self)
        f.news({"type": "storm_formed", "round": 1, "storm": "Pia", "time": "1802-05-10T10:00:00", "seq": 0})
        ev = f.news({"type": "announcement", "round": "1", "storm": "Pia", "time": "1802-05-10T10:05:00",
                     "headline": "Port closes", "symbols": ["PORT"], "seq": 0})
        self.assertEqual(len(news_of(ev)), 1)
        self.assertEqual(news_of(ev)[0].round, 1)              # "1" -> 1
        self.assertEqual(resets_of(f.events), [])
        ev2 = f.news({"type": "announcement", "round": "1", "storm": "Pia", "time": "1802-05-10T10:06:00",
                      "headline": "Grid repaired", "symbols": ["GRID"], "seq": 0})
        self.assertEqual(len(news_of(ev2)), 1)
        again = f.news({"type": "announcement", "round": "1", "storm": "Pia", "time": "1802-05-10T10:06:00",
                        "headline": "Grid repaired", "symbols": ["GRID"], "seq": 0})
        self.assertEqual(news_of(again), [])
        self.assertEqual(f.n.stats()["dup_news"], 1)

    def test_announcement_never_triggers(self):
        f = Feed(self)
        f.season_storm(n=200)
        ev = f.news({"type": "announcement", "round": 1, "storm": "X", "time": "1802-06-01T10:00:00",
                     "headline": "h", "symbols": [], "seq": 0})
        self.assertEqual(resets_of(ev), [])
        self.assertEqual(len(news_of(ev)), 1)

    def test_duplicate_storm_formed_late(self):
        f = Feed(self)
        sf = {"type": "storm_formed", "round": 1, "storm": "Pia", "time": "1802-05-10T10:00:00", "seq": 0}
        f.news(sf)
        for s in range(1, 900):
            r = 1 if s < 300 else (2 if s < 600 else 3)
            date = {1: "1802-05-10", 2: "1802-05-11", 3: "1802-05-12"}[r]
            f.news({"type": "storm", "round": r, "storm": "S", "time": f"{date}T11:00:00", "seq": s}, dt=0.01)
        ev = f.news(dict(sf))
        self.assertEqual(ev, [])
        self.assertEqual(resets_of(f.events), [])
        self.assertEqual(f.n.stats()["dup_news"], 1)

    def test_late_round1_report_in_round2(self):
        f = Feed(self)
        for s in range(0, 300):
            f.news({"type": "storm", "round": 1, "storm": "Pia", "time": "1802-05-10T11:00:00", "seq": s}, dt=0.01)
        for s in range(301, 400):
            f.news({"type": "storm", "round": 2, "storm": "Rex", "time": "1802-05-11T11:00:00", "seq": s}, dt=0.01)
        ev = f.news({"type": "damage_report", "round": 1, "storm": "Pia", "time": "1802-05-10T15:40:00",
                     "stage": "final", "seq": 300})
        self.assertEqual(len(news_of(ev)), 1)
        self.assertEqual(resets_of(f.events), [])
        self.assertEqual(f.n.wm, 399)

    def test_conflict_without_trigger_dropped(self):
        f = Feed(self)
        for s in range(0, 100):
            f.news({"type": "storm", "round": 2, "storm": "Rex", "time": "1802-05-11T11:00:00", "seq": s}, dt=0.01)
        for k in range(3):
            ev = f.news({"type": "track", "round": 2, "storm": "Rex", "time": f"1802-05-11T11:0{k + 1}:00",
                         "seq": 50 + k})
            self.assertEqual(ev, [])
        st = f.n.stats()
        self.assertEqual(st["seq_conflict"], 3)
        self.assertEqual(resets_of(f.events), [])
        self.assertTrue(any(k == "alert" and fl.get("alert") == "seq_conflict" for k, fl in f.log.events))

    def _provisional(self):
        f = Feed(self)
        f.season_storm(n=801)
        self.assertEqual(f.n.max_seq, 800)
        ev = f.news({"type": "storm_formed", "round": 1, "storm": "Zed", "time": "1801-05-11T10:00:00", "seq": 0})
        rs = resets_of(ev)
        self.assertEqual([(e.reason, e.new_epoch, e.provisional) for e in rs], [("storm_formed_r1", 1, True)])
        self.assertEqual(len(news_of(ev)), 1)                   # forwarded into the cleared state
        self.assertEqual(f.n.epoch, 0)
        self.assertEqual(f.n.pending_reset, "storm_formed_r1")
        self.assertEqual(news_of(ev)[0].season_epoch, 0)
        f.news({"type": "storm", "round": 1, "storm": "Zed", "time": "1801-05-11T10:00:00", "seq": 1})
        return f

    def test_provisional_confirmed_by_minus1_snapshot(self):
        f = self._provisional()
        ev = f.news(snap(-1, time="1801-05-11T09:30:00", phase="pre_storm"))
        rs = resets_of(ev)
        self.assertEqual([(e.reason, e.new_epoch, e.provisional) for e in rs], [("storm_formed_r1", 1, False)])
        self.assertEqual(f.n.epoch, 1)
        self.assertIsNone(f.n.pending_reset)
        self.assertEqual(f.n.max_seq, 1)                        # the new season's messages were kept
        arrived = [e for e in ev if isinstance(e, SnapshotEvent)]
        self.assertEqual(arrived[0].season_epoch, 1)
        self.assertEqual(f.n.stats()["provisional_resets"], 1)

    def test_provisional_confirmed_by_consistent_snapshot(self):
        f = self._provisional()
        ev = f.news(snap(30, time="1801-05-11T10:30:00"))
        self.assertEqual([(e.reason, e.provisional) for e in resets_of(ev)], [("storm_formed_r1", False)])
        self.assertEqual(f.n.epoch, 1)
        # a snapshot of another date does not confirm
        f2 = self._provisional()
        self.assertEqual(resets_of(f2.news(snap(30, time="1801-05-12T10:30:00", rnd=2))), [])
        self.assertEqual(f2.n.pending_reset, "storm_formed_r1")

    def test_provisional_confirmed_by_core(self):
        f = self._provisional()
        ev = f.n.confirm_season_reset("comp_id", f.t)
        self.assertEqual((ev.reason, ev.new_epoch, ev.provisional), ("storm_formed_r1", 1, False))
        self.assertIsNone(f.n.confirm_season_reset("comp_id", f.t))
        self.assertTrue(any(k == "season_reset" and fl.get("by") == "comp_id" for k, fl in f.log.events))

    def test_provisional_rejected_restores_dedup_and_wm(self):
        f = self._provisional()
        self.assertEqual((f.n.wm, f.n.max_seq), (1, 1))         # cleared while pending: only the new seqs 0-1
        f.n.reject_season_reset(f.t)
        self.assertIsNone(f.n.pending_reset)
        self.assertEqual(f.n.epoch, 0)
        self.assertEqual(f.n.wm, 800)
        self.assertEqual(f.n.max_seq, 800)
        ev = f.news({"type": "storm", "round": 1, "storm": "Pia", "time": "1802-05-10T10:23:00", "age": 700,
                     "seq": 700})
        self.assertEqual(ev, [])
        self.assertEqual(f.n.stats()["rejected_resets"], 1)
        self.assertEqual(f.n.epoch, 0)

    def test_second_trigger_while_pending_ignored(self):
        f = self._provisional()
        for s in range(2, 120):
            f.news({"type": "storm", "round": 1, "storm": "Zed", "time": "1801-05-11T10:01:00", "seq": s}, dt=0.01)
        ev = f.news({"type": "storm_formed", "round": 1, "storm": "Yan", "time": "1800-05-11T10:00:00", "seq": 3})
        self.assertEqual(resets_of(ev), [])
        self.assertEqual(f.n.stats()["provisional_ignored"], 1)

    def test_confirmed_mode(self):
        f = Feed(self, provisional_feed_resets="false")
        f.season_storm(n=801)
        ev = f.news({"type": "storm_formed", "round": 1, "storm": "Zed", "time": "1801-05-11T10:00:00", "seq": 0})
        self.assertEqual([(e.reason, e.new_epoch, e.provisional) for e in resets_of(ev)],
                         [("storm_formed_r1", 1, False)])
        self.assertEqual(news_of(ev)[0].season_epoch, 1)

    def test_round_decrease_and_date_change(self):
        f = Feed(self)
        for s in range(0, 60):
            f.news({"type": "storm", "round": 2, "storm": "Rex", "time": "1802-05-11T11:00:00", "seq": s}, dt=0.01)
        ev = f.news(snap(10, rnd=1, time="1801-05-11T10:15:00"))
        self.assertEqual([(e.reason, e.provisional) for e in resets_of(ev)], [("round_decrease", True)])
        g = Feed(self)
        for s in range(0, 60):
            g.news({"type": "storm", "round": 2, "storm": "Rex", "time": "1802-05-11T11:00:00", "seq": s}, dt=0.01)
        ev = g.news({"type": "storm", "round": 2, "storm": "Q", "time": "1803-05-11T11:00:00", "seq": 60})
        self.assertEqual([(e.reason, e.provisional) for e in resets_of(ev)], [("date_change", True)])
        self.assertEqual(len(news_of(ev)), 1)

    def test_minus1_snapshot_repeated_resets_once(self):
        f = Feed(self)
        f.season_storm(n=100)
        for k in range(5):
            f.news(snap(-1, time="1801-05-11T09:30:00", phase="pre_storm"), dt=30.0)
        rs = resets_of(f.events)
        self.assertEqual([(e.reason, e.new_epoch, e.provisional) for e in rs], [("snapshot_last_seq", 1, False)])
        self.assertEqual(sum(1 for e in f.events if isinstance(e, SnapshotEvent) and e.stage == "arrived"), 5)

    def test_force_then_minus1_resets_once(self):
        f = Feed(self)
        f.season_storm(n=100)
        ev = f.n.force_season_reset("comp_id", f.t)
        self.assertEqual((ev.reason, ev.new_epoch, ev.provisional), ("comp_id", 1, False))
        f.news(snap(-1, time="1801-05-11T09:30:00", phase="pre_storm"))
        self.assertEqual(resets_of(f.events), [])
        self.assertEqual(f.n.epoch, 1)

    def test_snapshot_hold_and_duplicates(self):
        f = Feed(self)
        for s in range(0, 10):
            f.news({"type": "storm", "round": 1, "storm": "Pia", "time": "1802-05-10T10:00:00", "seq": s}, dt=0.01)
        ev = f.news(snap(12))
        self.assertEqual([e.stage for e in ev if isinstance(e, SnapshotEvent)], ["arrived"])
        f.news(snap(12), dt=0.2, conn="ws1#1")                  # the other connection's copy
        self.assertEqual([e for e in f.advance(0.5) if isinstance(e, SnapshotEvent)], [])
        rel = [e for e in f.advance(0.31) if isinstance(e, SnapshotEvent)]
        self.assertEqual([(e.stage, e.engine_relevant, e.last_seq) for e in rel], [("released", True, 12)])
        self.assertEqual(f.n.wm, 12)
        self.assertEqual(f.advance(5.0), [])
        self.assertEqual(f.n.stats()["snapshots_dup"], 1)
        late = f.news({"type": "storm", "round": 1, "storm": "Pia", "time": "1802-05-10T10:00:10", "seq": 11})
        self.assertEqual(len(news_of(late)), 1)                 # an unseen seq below wm is still forwarded

    def test_live_copy_wins_inside_hold(self):
        f = Feed(self)
        for s in range(0, 10):
            f.news({"type": "storm", "round": 1, "storm": "Pia", "time": "1802-05-10T10:00:00", "seq": s}, dt=0.01)
        f.news(snap(11), conn="ws0#1")
        f.news({"type": "storm", "round": 1, "storm": "Pia", "time": "1802-05-10T10:00:20", "seq": 10}, dt=0.02,
               conn="ws1#1")
        f.news({"type": "storm", "round": 1, "storm": "Pia", "time": "1802-05-10T10:00:22", "seq": 11}, dt=0.02,
               conn="ws1#1")
        rel = [e for e in f.advance(1.0) if isinstance(e, SnapshotEvent)]
        self.assertEqual([(e.stage, e.engine_relevant) for e in rel], [("released", False)])

    def test_frames_of_every_type(self):
        f = Feed(self)
        self.assertIsInstance(f.frame({}, control="up")[0], ConnEvent)
        self.assertFalse(f.frame({}, control="down")[0].up)
        self.assertIsInstance(f.frame({"type": "heartbeat", "timestamp": "x"})[0], HeartbeatEvent)
        self.assertEqual(f.frame({"type": "subscription_ack", "symbols": ["PORT"], "channels": ["news"]}), [])
        self.assertTrue(any(k == "subscription_ack" for k, _ in f.log.events))
        self.assertEqual(f.frame({"type": "mystery"}), [])
        self.assertEqual(f.frame({"type": "news", "message": "not a dict"}), [])
        self.assertEqual(f.frame(["not", "a", "dict"]), [])
        bk = f.frame({"type": "orderbook", "symbol": "PORT", "timestamp": "2026-10-23T10:00:00.1",
                      "bids": [{"price": 44.85, "quantity": 18}, None, {"price": None, "quantity": 3}],
                      "asks": []})[0]
        self.assertEqual((bk.bids[0].price, bk.bids[0].qty, len(bk.bids), bk.asks), (44.85, 18, 1, ()))
        self.assertEqual(f.frame({"type": "orderbook", "symbol": "PORT", "timestamp": "2026-10-23T10:00:00.1",
                                  "bids": [], "asks": []}), [])        # not newer: stale
        q = f.frame({"type": "quote", "symbol": "PORT", "bid": None, "ask": None, "bid_size": None,
                     "ask_size": None, "timestamp": "2026-10-23T10:00:00.2"})[0]
        self.assertEqual((q.bid, q.ask, q.bid_size), (None, None, None))
        t1 = f.frame({"type": "trade", "symbol": "PORT", "price": 45.0, "quantity": 5,
                      "timestamp": "2026-10-23T10:00:00.3"})
        self.assertEqual(len(t1), 1)
        self.assertEqual(f.frame({"type": "trade", "symbol": "PORT", "price": 45.0, "quantity": 5,
                                  "timestamp": "2026-10-23T10:00:00.3"}), [])   # no id: tuple dedup
        bare = f.frame({"seq": 3, "round": 1, "type": "storm", "storm": "Pia", "time": "1802-05-10T10:01:00",
                        "received_at": 5.0})
        self.assertEqual(news_of(bare)[0].seq, 3)
        self.assertNotIn("received_at", news_of(bare)[0].msg)
        st = f.n.stats()
        self.assertEqual(st["stale_books"], 1)
        self.assertEqual(st["dup_trades"], 1)
        self.assertEqual(st["unknown_frames"], 1)
        self.assertGreaterEqual(st["bad_frames"], 2)

    def test_gaps_phase_hint_seq_gaps(self):
        f = Feed(self)
        self.assertIsNone(f.n.phase_hint())
        for s in (0, 1, 2, 5, 6, 9):
            f.news({"type": "storm", "round": 1, "storm": "Pia", "time": "1802-05-10T10:01:00", "seq": s})
        self.assertEqual(f.n.gaps(), [(3, 4), (7, 8)])
        self.assertEqual(f.n.stats()["seq_gaps"], 2)
        self.assertEqual(f.n.wm, 2)
        self.assertEqual(f.n.phase_hint(), "storm")
        f.news({"type": "damage_report", "round": 1, "storm": "Pia", "time": "1802-05-10T14:15:00", "seq": 10})
        self.assertEqual(f.n.phase_hint(), "aftermath")
        f.news(snap(10, time="1802-05-10T16:00:00", phase="closed"))
        self.assertEqual(f.n.phase_hint(), "closed")

    def test_trade_cache_bounded(self):
        f = Feed(self, trade_id_cache="3")
        for i in range(5):
            f.frame({"type": "trade", "symbol": "PORT", "price": 1.0, "quantity": 1, "trade_id": f"t{i}"})
        self.assertEqual(f.n.stats()["trade_ids"], 3)
        self.assertEqual(len(f.frame({"type": "trade", "symbol": "PORT", "price": 1.0, "quantity": 1,
                                      "trade_id": "t0"})), 1)  # evicted: forwarded again


if __name__ == "__main__":
    unittest.main()
