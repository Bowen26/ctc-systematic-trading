"""Group C: ``ctcbot.models.laws.LawCheck`` (DESIGN.md section 9.1 C, law_check)."""
from __future__ import annotations

import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

from test_c_support import MIDS, FakeConfig, books, fv  # noqa: E402
from ctcbot.models.laws import LawCheck  # noqa: E402
from ctcbot.types import ListLog  # noqa: E402

CHECKED = ("PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "HAVEN")


class LawCheckTest(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()
        self.log = ListLog()
        self.lc = LawCheck(self.cfg, self.log)
        self.slopes = self.cfg.get("law_check.slopes")

    def run_check(self, round_=1, stage="preliminary", d_mu=None, realised=None, fv_after=True, t0=570.0, seq=295):
        """One scripted report: engine means move by d_mu, mids by realised (default: the slope prediction)."""
        d_mu = d_mu or {s: 2.0 for s in CHECKED}
        before = fv(dict(MIDS), seq_hi=seq - 1, version=1, computed=t0 - 1)
        self.lc.on_report(round_, stage, seq, before, books(), t0)
        if fv_after:
            after = fv({s: MIDS[s] + d_mu.get(s, 0.0) for s in MIDS}, seq_hi=seq, version=2, computed=t0 + 0.05)
            self.lc.on_fv(after, t0 + 0.05)
        key = "prelim" if stage.startswith("prelim") else "final"
        mids = dict(MIDS)
        for s in CHECKED:
            pred = self.slopes[key][s][round_ - 1] * d_mu.get(s, 0.0)
            mids[s] = MIDS[s] + (realised.get(s, pred) if realised is not None else pred)
        self.lc.on_tick(books(mids), t0 + 10.0)
        self.assertEqual(self.lc.pending(), 1)
        self.lc.on_tick(books(mids), t0 + 16.0)
        self.assertEqual(self.lc.pending(), 0)
        return self.log.kinds("law_check")[-1]

    def test_matching_moves_pass(self):
        rec = self.run_check()
        self.assertFalse(rec["void"])
        self.assertFalse(rec["failed"])
        self.assertEqual(rec["checked"], len(CHECKED))
        self.assertEqual(self.lc.checks_done(), 1)
        self.assertTrue(all(self.lc.passed(s) for s in CHECKED))

    def test_two_sign_misses_fail_and_list_symbols(self):
        rec = self.run_check(realised={"PORT": -1.0, "HAVEN": -0.8})
        self.assertTrue(rec["failed"])
        self.assertEqual(sorted(rec["misses"]), ["HAVEN", "PORT"])
        self.assertEqual(self.lc.failures(), frozenset({"PORT", "HAVEN"}))
        self.assertFalse(self.lc.passed("PORT"))
        self.assertTrue(self.lc.passed("ISLE"))
        self.lc.clear("PORT")                  # lawok PORT
        self.assertTrue(self.lc.passed("PORT"))
        self.assertFalse(self.lc.passed("HAVEN"))
        self.assertTrue(self.log.kinds("alert"))

    def test_single_miss_or_size_miss(self):
        rec = self.run_check(realised={"PORT": -1.0})
        self.assertFalse(rec["failed"])
        self.assertEqual(rec["misses"], ["PORT"])
        self.assertTrue(self.lc.passed("PORT"))
        lc2 = LawCheck(self.cfg, ListLog())
        self.lc = lc2
        self.log = lc2.log
        rec = self.run_check(round_=2, stage="final", realised={"GRID": 0.01, "MAT": 0.02})  # far too small
        self.assertTrue(rec["failed"])
        self.assertEqual(sorted(rec["misses"]), ["GRID", "MAT"])

    def test_small_predicted_moves_are_not_checked(self):
        rec = self.run_check(d_mu={s: 0.01 for s in CHECKED}, realised={s: -0.5 for s in CHECKED})
        self.assertEqual(rec["checked"], 0)
        self.assertFalse(rec["failed"])
        self.assertEqual(self.lc.checks_done(), 1)

    def test_no_check_fails_until_lawok_all(self):
        self.assertEqual(self.lc.checks_done(), 0)
        self.assertFalse(self.lc.passed("ISLE"))
        self.lc.clear("ISLE")
        self.assertFalse(self.lc.passed("ISLE"))
        self.lc.clear("all")
        self.assertTrue(self.lc.passed("ISLE"))

    def test_check_without_fv_after_is_void(self):
        rec = self.run_check(fv_after=False)
        self.assertTrue(rec["void"])
        self.assertEqual(self.lc.checks_done(), 0)
        self.assertFalse(self.lc.passed("PORT"))

    def test_fv_before_seq_does_not_count_as_after(self):
        before = fv(dict(MIDS), seq_hi=290, version=1)
        self.lc.on_report(1, "final", 296, before, books(), 740.0)
        self.lc.on_fv(fv({"PORT": 99.0}, seq_hi=295, version=2), 740.1)    # not yet the report
        self.lc.on_tick(books(), 757.0)
        self.assertTrue(self.log.kinds("law_check")[-1]["void"])

    def test_rounds_and_stages_outside_scope_are_ignored(self):
        self.lc.on_report(3, "final", 900, fv(), books(), 740.0)
        self.lc.on_report(1, "weird", 1, fv(), books(), 740.0)
        self.assertEqual(self.lc.pending(), 0)

    def test_state_roundtrip_and_reset(self):
        self.run_check(realised={"PORT": -1.0, "HAVEN": -0.8})
        d = self.lc.state_dict()
        other = LawCheck(self.cfg, ListLog())
        other.load_state(d)
        self.assertEqual(other.checks_done(), 1)
        self.assertEqual(other.failures(), frozenset({"PORT", "HAVEN"}))
        other.load_state({"checks_done": "x", "failures": [1, "GEN"]})
        self.assertEqual(other.failures(), frozenset({"GEN"}))
        self.lc.reset()
        self.assertEqual(self.lc.checks_done(), 0)
        self.assertEqual(self.lc.failures(), frozenset())


if __name__ == "__main__":
    unittest.main()
