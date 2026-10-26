"""Group C: ``ctcbot.models.laws.LawsCrossCheck`` and ``load_laws_module`` (DESIGN.md section 9.1 C, laws)."""
from __future__ import annotations

import json
import os
import random
import sys
import types
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from test_c_support import FakeConfig  # noqa: E402
from ctcbot.models.coverage import CLOSURE_SYMBOLS, StormCoverage  # noqa: E402
from ctcbot.models.laws import DL_SYMBOLS, LawsCrossCheck, load_laws_module, merged_path  # noqa: E402
from ctcbot.types import SYMBOLS, ListLog  # noqa: E402


def season_feed(n: int) -> list[dict]:
    path = testpaths.require(os.path.join(testpaths.HISTORY, f"season-{n:03d}", "feed.jsonl"))
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def settlement(n: int) -> dict:
    with open(os.path.join(testpaths.HISTORY, f"season-{n:03d}", "settlement.json")) as f:
        return json.load(f)


class LawsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.laws = load_laws_module(testpaths.require(testpaths.LAWS))
        cls.cfg = FakeConfig()

    def build(self, mode: str = "inline", laws=None):
        log = ListLog()
        cov = StormCoverage(self.cfg, log)
        lc = LawsCrossCheck(self.cfg, log, laws or self.laws, cov, mode=mode)
        return cov, lc

    def feed(self, cov, lc, msgs):
        for m in msgs:
            cov.on_news(m)
            lc.on_news(m)

    def check_season(self, n: int, drop_frac: float = 0.0, seed: int = 0) -> None:
        msgs = season_feed(n)
        if drop_frac:
            rng = random.Random(seed + n)
            msgs = [m for m in msgs if not (m["type"] == "storm" and rng.random() < drop_frac)]
        cov, lc = self.build()
        final3 = next(i for i, m in enumerate(msgs) if m["type"] == "damage_report" and m["round"] == 3
                      and m["stage"] == "final")
        self.feed(cov, lc, msgs[:final3])
        self.assertIsNone(lc.values(), "values before the round-3 final report")
        self.feed(cov, lc, msgs[final3:])
        v = lc.values()
        self.assertIsNotNone(v)
        S = settlement(n)
        for s in SYMBOLS:
            tol = self.cfg.sym(s)["laws_tol"]
            self.assertIsNotNone(v[s], f"season {n} {s}")
            self.assertLessEqual(abs(v[s] - S[s]), tol, f"season {n} drop {drop_frac} {s}: {v[s]:.4f} vs {S[s]}")
        cmp = lc.compare(S)
        self.assertTrue(all(ok for _, ok in cmp.values()), cmp)

    def test_research_seasons_401_405(self):
        for n in range(401, 406):
            with self.subTest(season=n):
                self.check_season(n)

    def test_research_seasons_with_dropped_obs(self):
        for n in range(401, 406):
            with self.subTest(season=n):
                self.check_season(n, drop_frac=0.10, seed=7)

    def test_values_never_integrate(self):
        real = self.laws
        calls = {"n": 0}

        class Counting(real.ClosureIntegrator):
            def __init__(self, *a, **k):
                calls["n"] += 1
                super().__init__(*a, **k)

        fake = types.SimpleNamespace(values=real.values, ClosureIntegrator=Counting)
        cov, lc = self.build(laws=fake)
        msgs = season_feed(401)
        ends = 0
        for m in msgs:
            before = calls["n"]
            cov.on_news(m)
            lc.on_news(m)
            if calls["n"] != before:
                self.assertIn(m["type"], ("storm_dissipated", "damage_report"), m["type"])
                ends += calls["n"] - before
            lc.values()
        self.assertEqual(calls["n"], 3)
        self.assertEqual(ends, 3)
        for _ in range(100):
            lc.values()
            lc.compare({s: 1.0 for s in SYMBOLS})
        self.assertEqual(calls["n"], 3)
        # a late sample for a finished round schedules exactly one recomputation
        lc.on_snapshot({"type": "snapshot", "round": 1, "phase": "closed",
                        "obs": {"age": 481, "x": 0, "y": 0, "radius": 80, "intensity": 0.1}, "damage": []})
        self.assertEqual(calls["n"], 4)

    def test_closure_symbols_none_while_a_round_is_missing(self):
        msgs = season_feed(402)
        msgs = [m for m in msgs if m["round"] != 1 or m["type"] == "damage_report"]
        cov, lc = self.build()
        self.feed(cov, lc, msgs)
        self.assertEqual(cov.coverage().get(1), "missing")
        v = lc.values()
        self.assertIsNotNone(v)
        S = settlement(402)
        for s in CLOSURE_SYMBOLS:
            self.assertIsNone(v[s], s)
        for s in DL_SYMBOLS:
            self.assertIsNotNone(v[s], s)
            self.assertLessEqual(abs(v[s] - S[s]), self.cfg.sym(s)["laws_tol"])
        self.assertEqual(lc.compare(S)["PORT"], (None, None))

    def test_finals_from_snapshot_damage_list(self):
        msgs = season_feed(403)
        finals = [m for m in msgs if m["type"] == "damage_report"]
        rest = [m for m in msgs if m["type"] != "damage_report"]
        cov, lc = self.build()
        self.feed(cov, lc, rest)
        self.assertIsNone(lc.values())
        snap = {"type": "snapshot", "round": 3, "phase": "aftermath", "obs": None, "track": [],
                "damage": [{k: v for k, v in m.items() if k not in ("type", "seq", "headline")} for m in finals]}
        cov.on_snapshot(snap)
        lc.on_snapshot(snap)
        v = lc.values()
        S = settlement(403)
        self.assertIsNotNone(v)
        for s in SYMBOLS:
            self.assertLessEqual(abs(v[s] - S[s]), self.cfg.sym(s)["laws_tol"], s)

    def test_thread_mode_matches_inline(self):
        msgs = season_feed(404)
        cov_i, lc_i = self.build("inline")
        self.feed(cov_i, lc_i, msgs)
        cov_t, lc_t = self.build("thread")
        try:
            self.feed(cov_t, lc_t, msgs)
            self.assertTrue(lc_t.flush(10.0))
            self.assertEqual(lc_t.values(), lc_i.values())
        finally:
            lc_t.close()

    def test_merged_path_interpolates_gap_intensity(self):
        obs = {0: (0.0, 0.0, 80.0, 1.0), 10: (10.0, 0.0, 80.0, 2.0)}
        track = {0: (0.0, 0.0, 80.0), 4: (4.0, 1.0, 80.0), 12: (12.0, 0.0, 80.0)}
        p = merged_path(obs, track)
        self.assertEqual([a for a, *_ in p], [0, 4, 10, 12])
        self.assertAlmostEqual(p[1][4], 1.4)       # linear between the bracketing observations
        self.assertAlmostEqual(p[3][4], 2.0)       # held after the last observation
        self.assertEqual(merged_path({}, track), [])

    def test_reset_and_rebuild(self):
        msgs = season_feed(405)
        cov, lc = self.build()
        self.feed(cov, lc, msgs)
        v1 = lc.values()
        lc.reset()
        self.assertIsNone(lc.values())
        cov.rebuild(msgs)
        lc.rebuild(msgs)
        self.assertEqual(lc.values(), v1)

    def test_load_laws_module_errors(self):
        with self.assertRaises(ImportError):
            load_laws_module(os.path.join(testpaths.tmpdir("laws"), "missing.py"))
        d = testpaths.tmpdir("laws")
        p = os.path.join(d, "empty_laws.py")
        with open(p, "w") as f:
            f.write("X = 1\n")
        with self.assertRaises(ImportError):
            load_laws_module(p)
        with self.assertRaises(ValueError):
            LawsCrossCheck(self.cfg, ListLog(), self.laws, StormCoverage(self.cfg, ListLog()), mode="bogus")


if __name__ == "__main__":
    unittest.main()
