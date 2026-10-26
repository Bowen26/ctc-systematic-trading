"""Group A tests: ctcbot.case (CaseInfo, diff_case, strategy_limits, clip_size, Schedule) — DESIGN.md §9.1 A."""
import copy
import json
import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from ctcbot.case import CaseError, CaseInfo, Schedule, clip_size, diff_case, strategy_limits  # noqa: E402
from ctcbot.config import load_config  # noqa: E402
from ctcbot.types import SYMBOLS, Phase  # noqa: E402


def phase_of(t):
    """Inline copy of testnet/analysis/rebacktest/r_sim.py phase_of (read-only reference)."""
    if t < 3:
        return "open"
    if t < 50:
        return "pre"
    if t < 58:
        return "h1_entry"
    if t < 60:
        return "pre"
    if t < 72:
        return "early"
    if t < 80:
        return "h1_exit"
    if t < 120:
        return "s3_start"
    if t < 540:
        return "storm"
    if t < 568:
        return "after"
    if t < 600:
        return "rep_prelim"
    if t < 738:
        return "after"
    if t < 760:
        return "rep_final"
    return "after"


class CaseTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        testpaths.require(testpaths.RESEARCH_CASE)
        with open(testpaths.RESEARCH_CASE, encoding="utf-8") as f:
            cls.research = json.load(f)
        cls.cfg = load_config()
        cls.case = CaseInfo.from_dict(cls.research)
        cls.sch = Schedule(cls.case.timing, cls.cfg.get("cost.phases"))


class TestSchedule(CaseTestBase):
    def test_research_timing(self):
        s = self.sch
        self.assertEqual((s.open, s.storm_start, s.storm_end, s.prelim, s.final, s.close),
                         (0.0, 60.0, 540.0, 570.0, 740.0, 780.0))
        self.assertEqual(s.round_len, 780.0)
        self.assertEqual(s.speed, 30.0)

    def test_at(self):
        self.assertEqual(self.sch.at(["storm_start", -10]), 50.0)
        self.assertEqual(self.sch.at(("final", 16)), 756.0)
        self.assertEqual(self.sch.at(["close", -2]), 778.0)
        with self.assertRaises(ValueError):
            self.sch.at(["midnight", 0])

    def test_phase(self):
        p = self.sch.phase
        self.assertEqual(p(None), Phase.UNKNOWN)
        self.assertEqual(p(-0.01), Phase.CLOSED)
        self.assertEqual(p(0.0), Phase.PRE_STORM)
        self.assertEqual(p(59.999), Phase.PRE_STORM)
        self.assertEqual(p(60.0), Phase.STORM)
        self.assertEqual(p(539.9), Phase.STORM)
        self.assertEqual(p(540.0), Phase.AFTERMATH)
        self.assertEqual(p(779.99), Phase.AFTERMATH)
        self.assertEqual(p(780.0), Phase.CLOSED)
        self.assertEqual(p(float("nan")), Phase.UNKNOWN)

    def test_cost_phase_equals_r_sim(self):
        t = 0.0
        while t <= 780.0:
            self.assertEqual(self.sch.cost_phase(t), phase_of(t), t)
            t += 0.25
        for t in (-5.0, -0.25, 780.25, 900.0):
            self.assertEqual(self.sch.cost_phase(t), phase_of(t), t)

    def test_advisory_times(self):
        self.assertEqual(self.sch.advisory_times(), [90.0 + 60.0 * k for k in range(8)])

    def test_market_to_t(self):
        mt = Schedule.market_to_t
        self.assertEqual(mt("1802-05-10T10:15:00", 30.0), ("1802-05-10", 90.0))
        self.assertEqual(mt("1802-05-10T09:30:00", 30.0), ("1802-05-10", 0.0))
        self.assertEqual(mt("1802-05-10T16:00:00Z", 30.0), ("1802-05-10", 780.0))
        self.assertEqual(mt("1802-05-10T16:00:00+00:00", 30.0), ("1802-05-10", 780.0))
        self.assertEqual(mt("2025-08-04T11:31:30-04:00", 30.0), ("2025-08-04", 243.0))
        self.assertAlmostEqual(mt("1802-05-10T10:15:15.5", 30.0)[1], 90.0 + 15.5 / 30.0)
        self.assertEqual(mt("1802-05-11T01:55:00", 30.0)[1], (1 * 3600 + 55 * 60 - 34200) / 30.0)
        for bad in ("", "10:15", "1802-13-01T10:00:00", None, 5):
            with self.assertRaises(ValueError):
                mt(bad, 30.0)

    def test_other_timing(self):
        timing = dict(self.case.timing, pre_storm=30, storm=240, aftermath=120)
        s = Schedule(timing, self.cfg.get("cost.phases"))
        self.assertEqual((s.storm_start, s.storm_end, s.prelim, s.final, s.close, s.speed),
                         (30.0, 270.0, 300.0, 350.0, 390.0, 60.0))
        self.assertEqual(s.at(["storm_start", -10]), 20.0)


class TestCaseInfo(CaseTestBase):
    def test_from_dict(self):
        c = self.case
        self.assertEqual(set(c.symbols), set(SYMBOLS))
        self.assertEqual(c.limits["PORT"], 200)
        self.assertEqual(c.limits["ARCH"], 50)
        self.assertEqual(c.baskets["ARCH"], {"PORT": 1, "ISLE": 1, "AQUA": 2, "GRID": 1, "FERRY": 2, "HAVEN": 1})
        self.assertEqual(c.kinds["PORT"], "physical")
        self.assertEqual(c.kinds["RECON"], "rebuild")
        self.assertEqual(c.kinds["HAVEN"], "insurer")
        self.assertEqual(c.kinds["REBLD"], "etf")
        self.assertEqual(c.timing["storm"], 480)
        self.assertEqual(CaseInfo.from_file(testpaths.RESEARCH_CASE).limits, c.limits)

    def test_bad_case(self):
        for bad in ({}, {"companies": {}, "etfs": {}, "limits": {"PORT": -1}, "timing": self.research["timing"]},
                    dict(self.research, timing={"pre_storm": 60}), "x"):
            with self.assertRaises(CaseError):
                CaseInfo.from_dict(bad)

    def test_strategy_limits_and_clips(self):
        cfg = load_config(sets=["sizing.scale=0.25"])
        lim = strategy_limits(self.case, cfg)
        self.assertEqual(set(lim), set(SYMBOLS))
        self.assertEqual(lim["PORT"], 50)
        self.assertEqual(lim["ARCH"], 12)
        self.assertEqual(lim["FERRY"], 200)
        self.assertEqual(strategy_limits(self.case, self.cfg)["MAT"], 700)
        odd = load_config(sets=["sizing.scale=0.29"])
        self.assertEqual(strategy_limits(self.case, odd)["REBLD"], 29)     # 100 * 0.29 = 28.999999999999996
        self.assertEqual(clip_size(12, 12), 1)
        self.assertEqual(clip_size(200, 12), 16)
        self.assertEqual(clip_size(250, 4), 62)
        self.assertEqual(clip_size(0, 4), 1)
        with self.assertRaises(ValueError):
            clip_size(10, 0)


class TestDiffCase(CaseTestBase):
    def test_clean(self):
        d = diff_case(copy.deepcopy(self.research), self.research)
        self.assertTrue(d.clean)
        self.assertEqual(d.lines, ())

    def test_grid_site_weight(self):
        served = copy.deepcopy(self.research)
        served["companies"]["GRID"]["sites"][0]["weight"] = 0.31
        d = diff_case(served, self.research)
        self.assertEqual(d.symbols, frozenset({"GRID", "ARCH"}))
        self.assertFalse(d.timing)
        self.assertFalse(d.clean)
        self.assertTrue(any("companies.GRID.sites[0].weight" in ln and "0.31" in ln for ln in d.lines), d.lines)

    def test_rebuilder_and_about_text(self):
        served = copy.deepcopy(self.research)
        served["companies"]["RECON"]["about"] = "changed"
        self.assertEqual(diff_case(served, self.research).symbols, frozenset({"RECON", "REBLD"}))
        served = copy.deepcopy(self.research)
        served["companies"]["REIN"]["kind"] = "physical"
        self.assertEqual(diff_case(served, self.research).symbols, frozenset({"REIN"}))

    def test_timing_change(self):
        served = copy.deepcopy(self.research)
        served["timing"]["storm"] = 420
        d = diff_case(served, self.research)
        self.assertTrue(d.timing)
        self.assertEqual(d.symbols, frozenset())
        self.assertTrue(any(ln.startswith("timing.storm") for ln in d.lines))

    def test_map_islands_outlooks_flag_all(self):
        for key, change in (("map", {"width": 1001, "height": 700}), ("outlooks", {"direct": "x"}),
                            ("storm", {"dissipate": 61, "start": [500, 740], "start_jitter": 120})):
            served = copy.deepcopy(self.research)
            served[key] = change
            self.assertEqual(diff_case(served, self.research).symbols, frozenset(SYMBOLS), key)
        served = copy.deepcopy(self.research)
        served["islands"]["coral_key"]["polygon"][0] = [701, 240]
        self.assertEqual(diff_case(served, self.research).symbols, frozenset(SYMBOLS))
        served = copy.deepcopy(self.research)
        served["new_rule"] = True
        self.assertEqual(diff_case(served, self.research).symbols, frozenset(SYMBOLS))

    def test_limit_and_basket(self):
        served = copy.deepcopy(self.research)
        served["limits"]["HAVEN"] = 300
        self.assertEqual(diff_case(served, self.research).symbols, frozenset({"HAVEN"}))
        served = copy.deepcopy(self.research)
        served["etfs"]["REBLD"]["basket"]["GEN"] = 2
        self.assertEqual(diff_case(served, self.research).symbols, frozenset({"REBLD"}))

    def test_combined(self):
        served = copy.deepcopy(self.research)
        served["limits"]["PORT"] = 100
        served["timing"]["aftermath"] = 200
        d = diff_case(served, self.research)
        self.assertEqual(d.symbols, frozenset({"PORT"}))
        self.assertTrue(d.timing)


if __name__ == "__main__":
    unittest.main()
