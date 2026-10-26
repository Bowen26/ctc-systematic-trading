"""Group C: ``ctcbot.models.coverage`` (DESIGN.md section 9.1 C, coverage)."""
from __future__ import annotations

import json
import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from test_c_support import FakeConfig  # noqa: E402
from ctcbot.models.coverage import StormCoverage, market_seconds  # noqa: E402
from ctcbot.types import ListLog  # noqa: E402


def tm(age: float, day: str = "1802-05-10") -> str:
    """Market time of a storm age (storm forms at 10:00, 30 market s per real s)."""
    s = 36000 + int(round(age * 30))
    return f"{day}T{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def obs(r: int, age: int, seq: int = 0) -> dict:
    return {"type": "storm", "round": r, "storm": "X", "time": tm(age), "age": age, "x": 500.0, "y": 700 - age,
            "vx": 0.0, "vy": -1.0, "radius": 80.0, "intensity": 1.0, "seq": seq}


def track(r: int, upto: int, step: int = 4) -> dict:
    return {"type": "track", "round": r, "storm": "X", "time": tm(upto), "age": upto,
            "points": [[a, 500.0, 700 - a, 80.0] for a in range(0, upto + 1, step)]}


def forward_recording(path: str):
    """Yield (season, kind, message): news by unseen seq per season, snapshots above the high-water mark."""
    season, seen, wm = 224, set(), -1
    with open(path) as f:
        for line in f:
            fr = json.loads(line)
            if fr.get("type") != "news" or not isinstance(fr.get("message"), dict):
                continue
            m = fr["message"]
            if m.get("type") == "snapshot":
                ls = m.get("last_seq")
                if ls == -1 and (seen or wm >= 0):
                    yield season, "reset", m
                    season, seen, wm = season + 1, set(), -1
                    continue
                if isinstance(ls, int) and ls > wm:
                    wm = ls
                    while wm + 1 in seen:
                        wm += 1
                    yield season, "snapshot", m
                continue
            s = m.get("seq")
            if s in seen:
                continue
            if s is not None:
                seen.add(s)
                while wm + 1 in seen:
                    wm += 1
            yield season, "news", m


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.cov = StormCoverage(FakeConfig(), ListLog())

    def feed_round(self, r: int, skip_obs=range(0), with_track=True, dissipate=True, upto=480):
        for a in range(0, upto + 1, 2):
            if a not in skip_obs:
                self.cov.on_news(obs(r, a))
            if with_track and a % 20 == 0 and a > 0:
                self.cov.on_news(track(r, a))
        if dissipate:
            self.cov.on_news({"type": "storm_dissipated", "round": r, "storm": "X", "time": tm(upto), "seq": 999})

    def test_full_round(self):
        self.feed_round(1)
        self.assertEqual(self.cov.coverage(), {1: "full"})

    def test_gapped_round_with_track_points(self):
        self.feed_round(1, skip_obs=range(100, 112, 2))      # 12 s without observations, track every 4 s
        self.assertEqual(self.cov.coverage(), {1: "gapped"})

    def test_missing_round_without_any_sample(self):
        self.feed_round(1, skip_obs=range(100, 112, 2), with_track=False)
        self.assertEqual(self.cov.coverage(), {1: "missing"})

    def test_ten_second_gap_is_not_a_long_gap(self):
        self.feed_round(1, skip_obs=range(102, 110, 2), with_track=False)   # 100 -> 110: 10 s, not > 10
        self.assertEqual(self.cov.coverage(), {1: "full"})

    def test_no_data_and_rounds_below_first_seen(self):
        self.assertEqual(self.cov.coverage(), {})
        self.feed_round(3)
        self.assertEqual(self.cov.coverage(), {1: "missing", 2: "missing", 3: "full"})

    def test_mid_storm_join_with_snapshot_track_is_gapped(self):
        snap = {"type": "snapshot", "round": 2, "storm": "X", "time": tm(180), "phase": "storm", "last_seq": 50,
                "obs": {"time": tm(180), "age": 180, "x": 1, "y": 2, "radius": 80, "intensity": 1.0},
                "track": [[a, 1.0, 2.0, 80.0] for a in range(0, 180, 8)] + [[180, 1, 2, 80]], "damage": []}
        self.cov.on_snapshot(snap)
        for a in range(180, 300, 2):
            self.cov.on_news(obs(2, a))
        self.assertEqual(self.cov.coverage(), {1: "missing", 2: "gapped"})

    def test_join_without_track_is_missing(self):
        for a in range(160, 300, 2):
            self.cov.on_news(obs(1, a))
        self.assertEqual(self.cov.coverage(), {1: "missing"})

    def test_end_of_span(self):
        # the last 20 s of observations lost: dissipated at 480 -> gapped (track covers positions)
        self.feed_round(1, skip_obs=range(462, 481, 2))
        self.assertEqual(self.cov.coverage(), {1: "gapped"})
        # without storm_dissipated but past the storm (damage report): the span ends at the last observation
        self.cov.reset()
        self.feed_round(1, skip_obs=range(462, 481, 2), dissipate=False)
        self.cov.on_news({"type": "damage_report", "round": 1, "stage": "preliminary", "time": tm(510),
                          "damage_pct": {}, "industry_loss_musd": 1.0, "seq": 1000})
        self.assertEqual(self.cov.coverage(), {1: "full"})

    def test_rebuild_and_frame_unwrap(self):
        msgs = [{"type": "news", "message": obs(1, a)} for a in range(0, 482, 2)]
        msgs.append({"type": "storm_dissipated", "round": "1", "time": tm(480), "seq": 1})
        self.cov.rebuild(msgs)
        self.assertEqual(self.cov.coverage(), {1: "full"})
        self.cov.rebuild([])
        self.assertEqual(self.cov.coverage(), {})

    def test_defensive_inputs(self):
        for bad in (None, 3, "x", {}, {"type": "storm"}, {"type": "storm", "round": "a"},
                    {"type": "track", "round": 1, "points": [None, [1], ["a", 1, 2, 3]]},
                    {"type": "snapshot", "obs": "bad", "track": 7, "damage": [None, {"round": "x"}]}):
            self.cov.on_news(bad)  # type: ignore[arg-type]
            self.cov.on_snapshot(bad)  # type: ignore[arg-type]
        self.assertIsInstance(self.cov.coverage(), dict)
        self.assertEqual(market_seconds("1802-05-10T10:15:00"), 36900.0)
        self.assertEqual(market_seconds("1802-05-10T10:15:00.5Z"), 36900.5)
        self.assertIsNone(market_seconds("garbage"))

    def test_recording(self):
        testpaths.require(testpaths.RECORDING)
        out = {}
        cur = None
        for season, kind, m in forward_recording(testpaths.RECORDING):
            if kind == "reset":
                out[season] = self.cov.coverage()
                self.cov.reset()
                continue
            cur = season
            if kind == "snapshot":
                self.cov.on_snapshot(m)
            else:
                self.cov.on_news(m)
        self.assertEqual(out[224], {1: "missing", 2: "missing", 3: "gapped"})
        self.assertEqual(out[225], {1: "full", 2: "gapped", 3: "full"})
        self.assertEqual(sorted(out), [224, 225])   # two season resets in the recording


if __name__ == "__main__":
    unittest.main()
